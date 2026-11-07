# -*- coding: utf-8 -*-
"""Statement-level parser and surgical rewriter for Maya ASCII files.

A .ma file is a MEL script that Maya executes on open, so the useful question is
not "does a line contain a keyword" but "which statements would run".  This
parser splits the file into statements the way MEL does (strings, escapes and
comments respected), reassembles strings Maya split across lines, and attributes
every statement to the node it would act on: MEL's setAttr applies to whatever is
selected, and only createNode and select change that.

It works on bytes throughout and never re-encodes: a scene saved under a Chinese
locale (``//Codeset: 936``) survives a clean byte for byte.  The file is memory
mapped and scanned with one regular expression that only stops at characters
that matter, so the numeric arrays making up most of a scene cost almost nothing.
"""
import mmap
import os
import re

from . import rules
from .analyzer import mel_unescape
from .scene_model import Anomaly, NodeRecord, SceneDoc, merge_ranges

MAX_STATEMENT_TEXT = 4 * 1024 * 1024
MAX_ANOMALIES = 500
_ARGS_WINDOW = 8192

# Every alternative starts with a literal, which lets the regex engine skip to the
# next candidate byte at C speed.
_SCAN_RE = re.compile(rb'"[^"\\]*(?:\\.[^"\\]*)*"|"|;|//[^\n]*|/\*.*?(?:\*/|\Z)|`|\$|\(', re.S)
_GAP_RE = re.compile(rb"(?:\s+|//[^\n]*|/\*.*?(?:\*/|\Z))*", re.S)
_COMMAND_RE = re.compile(rb"[^\s;(\"`]+")
_ARG_RE = re.compile(rb'"[^"\\]*(?:\\.[^"\\]*)*"|[^\s;]+', re.S)
_CODESET_RE = re.compile(rb"^//\s*Codeset:\s*(\S+)", re.I | re.M)

_QUOTE, _SEMICOLON, _SLASH, _BACKTICK, _DOLLAR, _PAREN = 0x22, 0x3B, 0x2F, 0x60, 0x24, 0x28
_NAME_START = frozenset(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_NAME_BYTES = _NAME_START | frozenset(b"0123456789")

_NODE_OWNED = (b"rename", b"lockNode", b"deleteAttr", b"aliasAttr", b"instanceable")
_LINK_COMMANDS = (b"connectAttr", b"disconnectAttr", b"relationship", b"parent")
_NORMAL_FILE_FLAGS = (b"-r", b"-rdi", b"-reference")
_TYPE_FLAGS = (b"-type", b"-typ")
_SCRIPT_NUMBERS = ("st", "stp", "scriptType", "sourceType")


class Statement(object):
    __slots__ = ("command", "start", "end", "literals", "inline_code")

    def __init__(self, command, start):
        self.command = command
        self.start = start
        self.end = start
        self.literals = []
        self.inline_code = False


def iter_statements(data):
    """Yield the statements of a .ma held in bytes or an mmap."""
    size = len(data)
    pos = 3 if data[:3] == b"\xef\xbb\xbf" else 0
    while True:
        pos = _GAP_RE.match(data, pos).end()
        if pos >= size:
            return
        if data[pos] == _SEMICOLON:  # an empty statement
            pos += 1
            continue
        match = _COMMAND_RE.match(data, pos)
        command = match.group(0) if match else data[pos:pos + 1]
        statement = Statement(command, pos)
        statement.end = size
        for token in _SCAN_RE.finditer(data, match.end() if match else pos + 1):
            start = token.start()
            first = data[start]
            if first == _QUOTE:
                end = token.end()
                if end - start >= 2:
                    statement.literals.append(data[start + 1:end - 1])
            elif first == _SEMICOLON:
                statement.end = token.end()
                break
            elif first == _BACKTICK:
                statement.inline_code = True
            elif first == _DOLLAR:
                if start + 1 < size and data[start + 1] in _NAME_START:
                    statement.inline_code = True
            elif first == _PAREN:
                # `name (` is a call.  Maya's own `-type "string" (` is not.
                before = data[max(0, start - 64):start].rstrip()
                if before and before[-1] in _NAME_BYTES and not statement.inline_code:
                    statement.inline_code = _ends_with_name(before)
        yield statement
        pos = statement.end


def _ends_with_name(text):
    index = len(text)
    while index > 0 and text[index - 1] in _NAME_BYTES:
        index -= 1
    word = text[index:]
    return bool(word) and word[0] in _NAME_START


class _Decoder(object):
    def __init__(self):
        self.encodings = ["utf-8", "gbk", "latin-1"]

    def set_codeset(self, value):
        try:
            name = value.decode("ascii").strip().lower()
        except UnicodeDecodeError:
            return
        if name.isdigit():
            name = "cp" + name
        if name not in self.encodings:
            self.encodings.insert(1, name)

    def __call__(self, data):
        for encoding in self.encodings:
            try:
                return data.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("latin-1")


def plug_node(reference):
    """Node name from a plug reference such as ':ns:a.tx' or '|grp|a.tx'."""
    head = reference.split(".", 1)[0]
    return head.rsplit("|", 1)[-1].lstrip(":")


def _flag_value(args, *flags):
    for index, token in enumerate(args[:-1]):
        if token in flags:
            return args[index + 1].strip(b'"')
    return None


def _parse_create_node(args):
    """(type, name, shared) from createNode's arguments, in whatever order they come."""
    node_type = b""
    name = b""
    shared = False
    index = 1
    while index < len(args):
        token = args[index]
        if token in (b"-n", b"-name"):
            if index + 1 < len(args):
                name = args[index + 1].strip(b'"')
            index += 2
        elif token in (b"-p", b"-parent"):
            index += 2
        elif token.startswith(b"-"):
            shared = shared or token in (b"-s", b"-shared")
            index += 1
        else:
            if not node_type:
                node_type = token.strip(b'"')
            index += 1
    return node_type, name, shared


def _parse_set_attr(args, literals):
    """(attribute, data type, value literals) from a setAttr, whatever the flag order."""
    attribute = None
    data_type = None
    index = 1
    while index < len(args):
        token = args[index]
        if token in _TYPE_FLAGS:
            if index + 1 < len(args):
                data_type = args[index + 1].strip(b'"')
            index += 2
            continue
        if attribute is None and token.startswith(b'"') and token.endswith(b'"') and len(token) >= 2:
            attribute = token[1:-1]
        index += 1
    if attribute is None:
        attribute = literals[0]
    values = list(literals)
    for used in (attribute, data_type):
        if used is not None and used in values:
            values.remove(used)
    return attribute, data_type, values


class _MappedFile(object):
    """Read-only bytes view of a file; an empty file becomes empty bytes."""

    def __init__(self, path):
        self.handle = open(path, "rb")
        self.map = None
        self.data = b""
        if os.path.getsize(path) > 0:
            self.map = mmap.mmap(self.handle.fileno(), 0, access=mmap.ACCESS_READ)
            self.data = self.map

    def __enter__(self):
        return self.data

    def __exit__(self, *_exc):
        if self.map is not None:
            self.map.close()
        self.handle.close()


class _SceneBuilder(object):
    """Tracks the selection the way MEL does while statements go by."""

    def __init__(self, doc, decoder, args_of):
        self.doc = doc
        self.decoder = decoder
        self.args_of = args_of
        self.current = None      # the node setAttr and friends apply to
        self.by_name = {}        # created node name -> record
        self.loose = None        # strings aimed at nodes this file doesn't create
        self.contiguous = False  # did the last statement belong to self.current?

    def own(self, extent):
        """Record that a statement belongs to the current node."""
        node = self.current
        if self.contiguous and node.extents:
            node.extents[-1] = (node.extents[-1][0], extent[1])
        else:
            node.extents.append(extent)
        self.contiguous = True

    def other_statement(self):
        self.contiguous = False

    def create_node(self, statement, extent):
        node_type, name, shared = _parse_create_node(self.args_of(statement))
        node = NodeRecord(node_type.decode("latin-1"), self.decoder(name), None, statement.start, shared=shared)
        node.extents.append(extent)
        self.doc.nodes.append(node)
        self.by_name[node.name.lstrip(":")] = node
        self.current = node
        self.contiguous = True

    def select(self, statement, extent):
        names = [item for item in self.args_of(statement)[1:] if not item.startswith(b"-")]
        self.contiguous = False
        if not names:
            self.current = None
            return
        name = self.decoder(names[0].strip(b'"'))
        known = self.by_name.get(name.lstrip(":"))
        if known is not None:
            # Back to a node from earlier in the file: it's still that node.
            self.current = known
            self.own(extent)
            return
        self.current = NodeRecord("<selected>", name, None, statement.start, shared=True)
        self.doc.nodes.append(self.current)

    def _loose_node(self, statement):
        if self.loose is None:
            self.loose = NodeRecord("<top-level>", "<file>", None, statement.start, shared=True)
            self.doc.nodes.append(self.loose)
        return self.loose

    def set_attr(self, statement, extent):
        literals = statement.literals
        if not literals:
            if self.current is not None:
                self.own(extent)
            return
        if len(literals) >= 3:
            attribute, data_type, values = _parse_set_attr(self.args_of(statement), literals)
        else:
            attribute, data_type, values = literals[0], None, []
        plug = attribute.decode("latin-1")

        if plug.startswith("."):
            target = self.current or self._loose_node(statement)
            attr = plug.lstrip(".")
        elif "." in plug:
            # setAttr "node.attr" names its node instead of using the selection.
            node_name, attr = plug.split(".", 1)
            target = self.by_name.get(node_name.lstrip(":"))
            if target is None:
                target = self._loose_node(statement)
                attr = plug
        else:
            target = self.current or self._loose_node(statement)
            attr = plug

        if target is self.current:
            self.own(extent)
        else:
            target.extents.append(extent)
            self.contiguous = False

        if values:
            joiner = b"" if data_type == b"string" else b"\n"
            target.strings[attr] = mel_unescape(self.decoder(joiner.join(values)))
            target.attr_extents.setdefault(attr, []).append(extent)
        elif attr in _SCRIPT_NUMBERS:
            args = self.args_of(statement)
            if len(args) >= 3:
                try:
                    target.numbers[attr] = int(float(args[-1]))
                except (ValueError, OverflowError):
                    pass

    def add_attr(self, statement, extent):
        if self.current is None:
            return
        self.own(extent)
        args = self.args_of(statement)
        for flag in (b"-ln", b"-longName", b"-sn", b"-shortName"):
            value = _flag_value(args, flag)
            if value:
                self.current.attr_extents.setdefault(value.decode("latin-1"), []).append(extent)


def parse_ma(path):
    """Parse a .ma file into a SceneDoc.  The file is only ever read."""
    doc = SceneDoc(path, "ma")
    decoder = _Decoder()
    allowed = rules.MA_ALLOWED_COMMANDS

    try:
        with _MappedFile(path) as data:
            codeset = _CODESET_RE.search(data[:2048])
            if codeset:
                decoder.set_codeset(codeset.group(1))

            def args_of(statement):
                end = min(statement.end, statement.start + _ARGS_WINDOW)
                return _ARG_RE.findall(data[statement.start:end].rstrip(b"; \t\r\n"))

            def anomaly(kind, statement):
                if len(doc.anomalies) < MAX_ANOMALIES:
                    end = min(statement.end, statement.start + MAX_STATEMENT_TEXT)
                    doc.anomalies.append(Anomaly(kind, statement.command.decode("latin-1"),
                                                 decoder(data[statement.start:end]), None,
                                                 (statement.start, statement.end)))

            builder = _SceneBuilder(doc, decoder, args_of)
            for statement in iter_statements(data):
                command = statement.command
                if command not in allowed:
                    anomaly("statement", statement)
                    builder.other_statement()
                    continue
                if statement.inline_code:
                    anomaly("inline_code", statement)
                    builder.other_statement()
                    continue

                extent = (statement.start, statement.end)
                if command == b"setAttr":
                    builder.set_attr(statement, extent)
                elif command == b"createNode":
                    builder.create_node(statement, extent)
                elif command == b"addAttr":
                    builder.add_attr(statement, extent)
                elif command in _NODE_OWNED:
                    if builder.current is not None:
                        builder.own(extent)
                elif command == b"select":
                    builder.select(statement, extent)
                else:
                    builder.other_statement()
                    if command == b"fileInfo":
                        values = [mel_unescape(decoder(item)) for item in statement.literals]
                        if len(values) >= 2:
                            doc.info_strings.append((values[0], values[1]))
                    elif command == b"file":
                        if not any(flag in _NORMAL_FILE_FLAGS for flag in args_of(statement)):
                            anomaly("unusual_file_command", statement)
                        elif statement.literals:
                            doc.references.append(decoder(statement.literals[-1]))
    except (IOError, OSError, ValueError) as error:
        doc.parse_ok = False
        doc.parse_note = "Could not read file: %s" % error
    return doc


def line_numbers(path, offsets):
    """Map byte offsets to 1-based line numbers.  Only flagged items pay for this."""
    wanted = sorted(set(offset for offset in offsets if offset is not None))
    result = {}
    if not wanted:
        return result
    line = 1
    position = 0
    with open(path, "rb") as handle:
        for offset in wanted:
            while position < offset:
                chunk = handle.read(min(4 * 1024 * 1024, offset - position))
                if not chunk:
                    break
                line += chunk.count(b"\n")
                position += len(chunk)
            result[offset] = line
    return result


def _expand_to_lines(data, start, end):
    """Grow a statement's range over its indentation and newline, if it's alone."""
    line_begin = data.rfind(b"\n", max(0, start - 4096), start) + 1
    if line_begin == 0 and start > 4096:
        line_begin = start
    if not data[line_begin:start].strip(b" \t"):
        start = line_begin
    newline = data.find(b"\n", end, min(len(data), end + 4096))
    if newline >= 0 and not data[end:newline].strip(b" \t\r"):
        end = newline + 1
    elif newline < 0 and not data[end:min(len(data), end + 4096)].strip():
        end = min(len(data), end + 4096)
    return start, end


def rewrite_ma(source, destination, extents, removed_nodes):
    """Copy source to destination without the given byte ranges.

    Statements that link to a removed node (connectAttr and friends) go too, so
    the result opens without errors.  Nothing else changes, byte for byte.
    Returns the number of link statements removed.
    """
    ranges = list(extents)
    links = 0
    wanted = set(name.lstrip(":") for name in removed_nodes)
    decoder = _Decoder()
    with _MappedFile(source) as data:
        if wanted:
            for statement in iter_statements(data):
                if statement.command in _LINK_COMMANDS:
                    for literal in statement.literals:
                        if plug_node(decoder(literal)) in wanted:
                            ranges.append((statement.start, statement.end))
                            links += 1
                            break
        expanded = merge_ranges(_expand_to_lines(data, a, b) for a, b in ranges)
        size = len(data)
        with open(destination, "wb") as out:
            position = 0
            for start, end in expanded:
                _write_range(data, out, position, start)
                position = end
            _write_range(data, out, position, size)
    return links


def _write_range(data, out, start, end):
    while start < end:
        step = min(16 * 1024 * 1024, end - start)
        out.write(data[start:start + step])
        start += step

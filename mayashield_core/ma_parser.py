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
mapped, and the tokenizer jumps from one character that matters to the next with
plain byte searches, so the numeric arrays making up most of a scene are skipped
at memory speed instead of being read.
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

_STRING_RE = re.compile(rb'"[^"\\]*(?:\\.[^"\\]*)*"', re.S)
_SPACE_RE = re.compile(rb"\s*")
_COMMAND_RE = re.compile(rb"[^\s;(\"`]+")
_ARG_RE = re.compile(rb'"[^"\\]*(?:\\.[^"\\]*)*"|[^\s;]+', re.S)
_CODESET_RE = re.compile(rb"^//\s*Codeset:\s*(\S+)", re.I | re.M)
# Text encodings a scene header may name.  Anything else ("undefined", "hex", "rot13"...)
# is a codec that does not decode text, and is not taken from a file's say-so.
_CODESET_NAME_RE = re.compile(
    r"^(?:cp\d{3,5}|ms\d{3,4}|windows-\d{3,4}|utf-?8|ascii|us-ascii|latin-?1|iso-?8859-\d{1,2}|gbk|gb2312|gb18030|"
    r"big5(?:hkscs)?|shift[-_]?jis|sjis|euc[-_]?(?:jp|kr|cn|tw)|johab|koi8-[ru]|mac[-_]?roman)$")

_SEMICOLON, _SLASH, _STAR, _QUOTE, _BACKSLASH = 0x3B, 0x2F, 0x2A, 0x22, 0x5C
_BRACES = frozenset(b"{}")
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


def _string_end(data, start):
    """The offset just past the string literal that opens at ``start``, or -1 if it never closes.

    Matches what _STRING_RE matches, by looking for the next quote and counting the
    backslashes in front of it.  The regular expression needs a stack frame for every
    escape in the string, which is gigabytes for a string made of nothing else.
    """
    find = data.find
    pos = start + 1
    while True:
        quote = find(b'"', pos)
        if quote < 0:
            return -1
        back = quote
        while back > start + 1 and data[back - 1] == _BACKSLASH:
            back -= 1
        if (quote - back) % 2 == 0:   # an even run of backslashes escapes itself, not the quote
            return quote + 1
        pos = quote + 1


def _skip_gap(data, pos, size):
    """The offset of the next thing that is neither white space nor a comment."""
    find = data.find
    while True:
        pos = _SPACE_RE.match(data, pos).end()
        if pos + 1 < size and data[pos] == _SLASH:
            following = data[pos + 1]
            if following == _SLASH:
                end = find(b"\n", pos)
                pos = size if end < 0 else end
                continue
            if following == _STAR:
                end = find(b"*/", pos + 2)
                pos = size if end < 0 else end + 2
                continue
        return pos


def iter_statements(data):
    """Yield the statements of a .ma held in bytes or an mmap.

    Only six characters can matter between a command and the end of its
    statement: a quote, a semicolon, a slash (comments), a backtick, a dollar
    sign and an opening parenthesis.  The position of the next one of each is
    remembered and searched for again only once the scan has passed it, so every
    byte of the file is looked at a fixed number of times, by ``find``.
    tests/test_tokenizer.py holds the regular-expression version this replaced
    and checks the two agree, statement for statement.
    """
    size = len(data)
    find = data.find
    pos = 3 if data[:3] == b"\xef\xbb\xbf" else 0
    next_semicolon = next_quote = next_slash = next_backtick = next_dollar = next_paren = -1
    while True:
        pos = _skip_gap(data, pos, size)
        if pos >= size:
            return
        first_byte = data[pos]
        if first_byte == _SEMICOLON:  # an empty statement
            pos += 1
            continue
        if first_byte in _BRACES or first_byte == _QUOTE:
            # Not the start of a command.  It is a statement of its own, so that it cannot
            # reach forward to the next semicolon and take a real statement with it.
            closing = _string_end(data, pos) if first_byte == _QUOTE else -1
            statement = Statement(data[pos:pos + 1], pos)
            statement.end = closing if closing > 0 else pos + 1
            yield statement
            pos = statement.end
            continue
        match = _COMMAND_RE.match(data, pos)
        statement = Statement(match.group(0) if match else data[pos:pos + 1], pos)
        statement.end = size
        cursor = match.end() if match else pos + 1
        while True:
            if next_semicolon < cursor:
                next_semicolon = find(b";", cursor)
                if next_semicolon < 0:
                    next_semicolon = size
            if next_quote < cursor:
                next_quote = find(b'"', cursor)
                if next_quote < 0:
                    next_quote = size
            if next_slash < cursor:
                next_slash = find(b"/", cursor)
                if next_slash < 0:
                    next_slash = size
            if next_backtick < cursor:
                next_backtick = find(b"`", cursor)
                if next_backtick < 0:
                    next_backtick = size
            if next_dollar < cursor:
                next_dollar = find(b"$", cursor)
                if next_dollar < 0:
                    next_dollar = size
            if next_paren < cursor:
                next_paren = find(b"(", cursor)
                if next_paren < 0:
                    next_paren = size
            first = min(next_semicolon, next_quote, next_slash, next_backtick, next_dollar, next_paren)
            if first >= size:
                break                      # no semicolon left: the statement runs to the end
            if first == next_semicolon:
                statement.end = first + 1
                break
            if first == next_quote:
                closing = _string_end(data, first)
                if closing > 0:
                    cursor = closing
                    statement.literals.append(data[first + 1:cursor - 1])
                else:
                    cursor = first + 1     # a quote that never closes
            elif first == next_slash:
                following = data[first + 1] if first + 1 < size else -1
                if following == _SLASH:    # a // comment, up to the end of its line
                    cursor = find(b"\n", first)
                    if cursor < 0:
                        cursor = size
                elif following == _STAR:   # a /* comment */
                    cursor = find(b"*/", first + 2)
                    cursor = size if cursor < 0 else cursor + 2
                else:
                    cursor = first + 1
            elif first == next_backtick:
                statement.inline_code = True
                cursor = first + 1
            elif first == next_dollar:
                if first + 1 < size and data[first + 1] in _NAME_START:
                    statement.inline_code = True
                cursor = first + 1
            else:
                # `name (` is a call.  Maya's own `-type "string" (` is not.
                before = data[max(0, first - 64):first].rstrip()
                if before and before[-1] in _NAME_BYTES and not statement.inline_code:
                    statement.inline_code = _ends_with_name(before)
                cursor = first + 1
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
        if name not in self.encodings and _CODESET_NAME_RE.match(name):
            self.encodings.insert(1, name)

    def __call__(self, data):
        for encoding in self.encodings:
            try:
                return data.decode(encoding)
            except (UnicodeError, LookupError):
                continue
        return data.decode("latin-1")

    @classmethod
    def for_file(cls, data):
        """A decoder that honours the //Codeset: line of a scene, as Maya does."""
        decoder = cls()
        codeset = _CODESET_RE.search(data[:2048])
        if codeset:
            decoder.set_codeset(codeset.group(1))
        return decoder


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
    """(type, name, shared, selected) from createNode's arguments, in whatever order they come."""
    node_type = b""
    name = b""
    shared = False
    selected = True
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
            if token in (b"-ss", b"-skipSelect"):
                selected = False
            index += 1
        else:
            if not node_type:
                node_type = token.strip(b'"')
            index += 1
    return node_type, name, shared, selected


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
        node_type, name, shared, selected = _parse_create_node(self.args_of(statement))
        node = NodeRecord(node_type.decode("latin-1"), self.decoder(name), None, statement.start, shared=shared)
        node.extents.append(extent)
        self.doc.nodes.append(node)
        self.by_name[node.name.lstrip(":")] = node
        if not selected:
            # createNode -skipSelect leaves the selection where it was: the setAttr lines
            # that follow still belong to the node selected before.
            self.contiguous = False
            return
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
            decoder = _Decoder.for_file(data)

            def args_of(statement):
                end = min(statement.end, statement.start + _ARGS_WINDOW)
                return _ARG_RE.findall(data[statement.start:end].rstrip(b"; \t\r\n"))

            def anomaly(kind, statement):
                # Hundreds of harmless-looking unknown statements must not use up the
                # allowance and so hide the one that matters: those that run code keep
                # a second allowance of their own, and whatever is left out is counted.
                telling = kind != "statement" or statement.command in rules.MA_CODE_COMMANDS \
                    or statement.command[:1] in (b"$", b"{")
                if len(doc.anomalies) < MAX_ANOMALIES or (telling and len(doc.anomalies) < 2 * MAX_ANOMALIES):
                    end = min(statement.end, statement.start + MAX_STATEMENT_TEXT)
                    doc.anomalies.append(Anomaly(kind, statement.command.decode("latin-1"),
                                                 decoder(data[statement.start:end]), None,
                                                 (statement.start, statement.end)))
                else:
                    doc.anomalies_dropped += 1

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
    wanted.discard("")   # an unnamed node must not match every plug without a node part
    with _MappedFile(source) as data:
        decoder = _Decoder.for_file(data)   # names were read with the file's codeset; compare the same way
        if wanted:
            for statement in iter_statements(data):
                if statement.command in _LINK_COMMANDS:
                    # relationship "link" ":lightLinker1" ...: the first word says what kind of
                    # relationship it is.  It is not a node, even if a node is called "link".
                    literals = statement.literals[1:] if statement.command == b"relationship" else statement.literals
                    for literal in literals:
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

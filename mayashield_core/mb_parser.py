# -*- coding: utf-8 -*-
"""Native reader and surgical rewriter for Maya binary (IFF) files.

No Maya needed.  Layout, confirmed against files saved by Maya 2024:

* 64-bit files: ``FOR8`` tag, 4 unused bytes, 8-byte big-endian size.
  Older files: ``FOR4`` tag, 4-byte size.
* A group's body starts with a 4-byte type (``Maya``, ``XFRM``, ``SCRP``...) and
  its size already covers its children, so the next chunk follows immediately.
* A leaf's data is padded to the alignment (8 or 4), and the padding is not
  counted in its size.
* Each node is a group whose first child is ``CREA``:
  ``<flag byte><name>\\0[<parent>\\0]<uuid>``.
* ``STR `` is ``<attr>\\0<flag byte><value>\\0``; ``DBLE`` is
  ``<attr>\\0<flag byte><8-byte big-endian value>``.
* Connections live in ``LIS8 CONS`` > ``FOR8 CONN`` > ``CWFL`` / ``RELA``.
"""
import mmap
import os
import struct

from . import rules
from .scene_model import Anomaly, NodeRecord, SceneDoc, merge_ranges

_GROUP_WIDE = (b"FOR8", b"LIS8", b"CAT8", b"PRO8")
_GROUP_NARROW = (b"FOR4", b"LIS4", b"CAT4", b"PRO4")
_GROUPS = frozenset(_GROUP_WIDE + _GROUP_NARROW)

NODE_TYPES = {b"SCRP": "script", b"DEXP": "expression", b"NTWK": "network", b"UNKN": "unknown", b"UNKD": "unknownDag"}

_MAX_DEPTH = 32
_MAX_STRING = 32 * 1024 * 1024
_MAX_RAW_HITS = 64


class MalformedFile(Exception):
    pass


def is_mb_header(prefix):
    return prefix[:4] in (b"FOR8", b"FOR4")


class _Layout(object):
    """Offsets the rewriter needs."""

    def __init__(self, wide):
        self.root = (0, wide)        # (header offset, is wide) of the top group
        self.group_wide = {}         # node group offset -> is wide
        self.links = []              # (start, end, names, ancestors) for CWFL/RELA chunks


def _read_header(data, pos, end, wide):
    """Return (tag, is_wide, body offset, size) for the chunk at pos."""
    if pos + 8 > end:
        raise MalformedFile("Chunk header runs past its parent at offset %d" % pos)
    tag = data[pos:pos + 4]
    if tag in _GROUP_WIDE:
        wide = True
    elif tag in _GROUP_NARROW:
        wide = False
    if wide:
        if pos + 16 > end:
            raise MalformedFile("Chunk header runs past its parent at offset %d" % pos)
        size = struct.unpack_from(">Q", data, pos + 8)[0]
        body = pos + 16
    else:
        size = struct.unpack_from(">I", data, pos + 4)[0]
        body = pos + 8
    if body + size > end:
        raise MalformedFile("Chunk %r at offset %d is larger than its parent" % (tag, pos))
    return tag, wide, body, size


def _next_leaf(body, size, wide):
    align = 8 if wide else 4
    return body + ((size + align - 1) // align) * align


def _decode(data):
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _split_attr(payload):
    """Split <attr>\\0<flag><rest> into (attr, rest)."""
    nul = payload.find(b"\x00")
    if nul < 0:
        return None, None
    return payload[:nul].decode("latin-1"), payload[nul + 2:]


def _node_name(crea):
    # flag byte, then a NUL-terminated name
    nul = crea.find(b"\x00", 1)
    return _decode(crea[1:nul]) if nul > 0 else ""


def _walk_node(data, node, body, end, wide, depth=0):
    if depth > _MAX_DEPTH:
        raise MalformedFile("Chunks nested too deeply")
    pos = body
    while pos < end:
        tag, child_wide, child_body, size = _read_header(data, pos, end, wide)
        if tag in _GROUPS:
            _walk_node(data, node, child_body + 4, child_body + size, child_wide, depth + 1)
            pos = child_body + size
            continue
        next_pos = min(_next_leaf(child_body, size, child_wide), end)
        if tag == b"STR " or tag == b"STR#":
            if size <= _MAX_STRING:
                attr, rest = _split_attr(bytes(data[child_body:child_body + size]))
                if attr is not None:
                    # Only direct children can be cut out: the rewriter patches the sizes of
                    # the node group and the root, nothing between.  A value found deeper is
                    # kept under a name of its own, so it is analysed but can never stand in
                    # for the direct child that would be the one removed.
                    key = attr if depth == 0 else "%s<nested:%d>" % (attr, len(node.strings))
                    if tag == b"STR#":
                        parts = rest[4:].split(b"\x00")
                        node.strings[key] = "\n".join(_decode(part) for part in parts if part)
                    else:
                        node.strings[key] = _decode(rest.rstrip(b"\x00"))
                    if depth == 0:
                        node.attr_extents.setdefault(attr, []).append((pos, next_pos))
            else:
                node.strings["<oversized>"] = _decode(bytes(data[child_body:child_body + _MAX_STRING]))
        elif tag == b"DBLE":
            if size >= 11:
                attr, rest = _split_attr(bytes(data[child_body:child_body + size]))
                if attr in ("st", "stp", "scriptType", "sourceType") and rest is not None and len(rest) >= 8:
                    try:
                        node.numbers[attr] = int(struct.unpack(">d", rest[:8])[0])
                    except (struct.error, ValueError, OverflowError):
                        pass
        elif tag == b"ATTR" and depth == 0:
            # Dynamic attribute definition: <type bytes>...<long>\0<short>\0
            payload = bytes(data[child_body:child_body + min(size, 4096)])
            for name in payload.split(b"\x00"):
                if name and name.replace(b"_", b"a").isalnum():
                    node.attr_extents.setdefault(name.decode("latin-1"), []).append((pos, next_pos))
        pos = next_pos


def _names_in_link(tag, payload):
    """Node names a connection chunk refers to."""
    if tag == b"CWFL":
        payload = payload[1:]  # leading flag byte
    names = set()
    pieces = payload.split(b"\x00")
    if tag == b"RELA":
        pieces = pieces[1:]    # the kind of relationship ("link", "shadowLink"), not a node
    for piece in pieces:
        text = piece.lstrip(bytes(bytearray(range(32))))
        if text:
            head = _decode(text).split(".", 1)[0]
            names.add(head.rsplit("|", 1)[-1].lstrip(":"))
    names.discard("")
    return names


def _walk_links(data, body, end, wide, ancestors, layout, depth=0):
    if depth > _MAX_DEPTH:
        raise MalformedFile("Chunks nested too deeply")
    pos = body
    while pos < end:
        tag, child_wide, child_body, size = _read_header(data, pos, end, wide)
        if tag in _GROUPS:
            _walk_links(data, child_body + 4, child_body + size, child_wide,
                        ancestors + [(pos, child_wide)], layout, depth + 1)
            pos = child_body + size
            continue
        next_pos = min(_next_leaf(child_body, size, child_wide), end)
        if tag in (b"CWFL", b"RELA"):
            names = _names_in_link(tag, bytes(data[child_body:child_body + min(size, 8192)]))
            layout.links.append((pos, next_pos, names, list(ancestors)))
        pos = next_pos


def _walk_header(data, doc, body, end, wide, depth=0):
    if depth > _MAX_DEPTH:
        raise MalformedFile("Chunks nested too deeply")
    pos = body
    while pos < end:
        tag, child_wide, child_body, size = _read_header(data, pos, end, wide)
        if tag in _GROUPS:
            _walk_header(data, doc, child_body + 4, child_body + size, child_wide, depth + 1)
            pos = child_body + size
            continue
        if tag == b"FINF" and size <= _MAX_STRING:
            parts = bytes(data[child_body:child_body + size]).split(b"\x00")
            if len(parts) >= 2:
                doc.info_strings.append((_decode(parts[0]), _decode(parts[1])))
        elif tag == b"FREF" and size <= _MAX_STRING:
            doc.references.append(_decode(bytes(data[child_body:child_body + size]).split(b"\x00")[0]))
        pos = _next_leaf(child_body, size, child_wide)


def _walk_root(data, doc, wide, body, end, layout):
    root_chain = [layout.root]
    pos = body
    while pos < end:
        tag, child_wide, child_body, size = _read_header(data, pos, end, wide)
        if tag not in _GROUPS:
            pos = _next_leaf(child_body, size, child_wide)
            continue
        group_type = bytes(data[child_body:child_body + 4])
        group_end = child_body + size
        inner = child_body + 4

        if group_type == b"HEAD" or group_type == b"FREF" or group_type == b"FRDI":
            _walk_header(data, doc, inner, group_end, child_wide)
        elif group_type in (b"CONS", b"CONN"):
            _walk_links(data, inner, group_end, child_wide, root_chain + [(pos, child_wide)], layout)
        elif inner < group_end:
            first_tag, first_wide, first_body, first_size = _read_header(data, inner, group_end, child_wide)
            if first_tag == b"CREA":
                crea = bytes(data[first_body:first_body + min(first_size, 8192)])
                node = NodeRecord(NODE_TYPES.get(group_type, group_type.decode("latin-1")), _node_name(crea), None, pos)
                node.extents.append((pos, group_end))
            elif first_tag == b"SLCT":
                name = _decode(bytes(data[first_body:first_body + min(first_size, 8192)]).split(b"\x00")[0])
                node = NodeRecord("<selected>", name, None, pos, shared=True)
                node.extents.append((pos, group_end))
            else:
                # No group type we recognise: its strings still get looked at.
                label = group_type.decode("latin-1")
                node = NodeRecord("<group>", label, None, pos, shared=True)
            _walk_node(data, node, inner, group_end, child_wide)
            layout.group_wide[pos] = child_wide
            doc.nodes.append(node)
        pos = group_end


def parse_mb(path):
    """Parse a .mb file into a SceneDoc without running any of its contents."""
    doc = SceneDoc(path, "mb")
    try:
        size = os.path.getsize(path)
        if size < 16:
            doc.parse_ok = False
            doc.parse_note = "File too small to be a Maya binary."
            return doc
        with open(path, "rb") as handle:
            data = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                tag, wide, body, chunk = _read_header(data, 0, size, False)
                if tag not in (b"FOR8", b"FOR4") or bytes(data[body:body + 4]) != b"Maya":
                    raise MalformedFile("Not a Maya IFF file.")
                layout = _Layout(wide)
                doc.layout = layout
                try:
                    _walk_root(data, doc, wide, body + 4, body + chunk, layout)
                except (MalformedFile, struct.error, RecursionError) as error:
                    doc.parse_ok = False
                    doc.parse_note = "Damaged or crafted structure: %s" % error
                    doc.anomalies.append(Anomaly("malformed_iff", "IFF", str(error)))
                if body + chunk < size:
                    doc.anomalies.append(Anomaly(
                        "trailing_data", "IFF", "%d bytes after the end of the scene" % (size - body - chunk)))
                _sweep_raw(data, doc)
            finally:
                data.close()
    except MalformedFile as error:
        doc.parse_ok = False
        doc.parse_note = str(error)
        _sweep_file(path, doc)
    except (IOError, OSError, ValueError, struct.error) as error:
        doc.parse_ok = False
        doc.parse_note = "Could not read file: %s" % error
    return doc


def _sweep_raw(data, doc):
    """Family strings anywhere in the file, including chunks the parser skips."""
    seen = set()
    for match in rules.FAMILY_BYTES_RE.finditer(data):
        text = match.group(0).lower()
        if text not in seen:
            seen.add(text)
            doc.raw_hits.append(match.group(0).decode("latin-1"))
            if len(doc.raw_hits) >= _MAX_RAW_HITS:
                break


def _sweep_file(path, doc):
    try:
        with open(path, "rb") as handle:
            if os.path.getsize(path) > 0:
                data = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    _sweep_raw(data, doc)
                finally:
                    data.close()
    except (IOError, OSError, ValueError):
        pass


def rewrite_mb(source, destination, doc, drop_nodes, drop_attr_extents=()):
    """Copy a .mb without the given nodes, their connections, and attribute chunks.

    ``drop_nodes`` are NodeRecords.  ``drop_attr_extents`` is a list of
    (node record, extent).  Every ancestor group's size is patched, so all other
    chunks stay byte-identical.  Returns the number of connections removed.
    """
    layout = doc.layout
    wanted = set(node.name.lstrip(":") for node in drop_nodes)
    wanted.discard("")
    drops = []       # (start, end, ancestor chain)
    links = 0

    for node in drop_nodes:
        for start, end in node.extents:
            drops.append((start, end, [layout.root]))
    for node, (start, end) in drop_attr_extents:
        drops.append((start, end, [layout.root, (node.offset, layout.group_wide[node.offset])]))
    if wanted:
        for start, end, names, ancestors in layout.links:
            if names & wanted:
                drops.append((start, end, ancestors))
                links += 1

    removed_by_header = {}
    unique = {}
    for start, end, chain in drops:
        unique[(start, end)] = chain
    for (start, end), chain in unique.items():
        for header, _wide in chain:
            removed_by_header[header] = removed_by_header.get(header, 0) + (end - start)

    wide_by_header = {}
    for _span, chain in unique.items():
        for header, wide in chain:
            wide_by_header[header] = wide

    file_size = os.path.getsize(source)
    with open(source, "rb") as handle:
        patches = {}
        for header, removed in removed_by_header.items():
            wide = wide_by_header[header]
            handle.seek(header + (8 if wide else 4))
            field = handle.read(8 if wide else 4)
            current = struct.unpack(">Q" if wide else ">I", field)[0]
            patches[header + (8 if wide else 4)] = struct.pack(">Q" if wide else ">I", current - removed)

        spans = merge_ranges(unique.keys())
        with open(destination, "wb") as out:
            position = 0
            for start, end in spans + [(file_size, file_size)]:
                _copy_patched(handle, out, position, start, patches)
                position = end
    return links


def _copy_patched(source, destination, start, end, patches):
    """Copy [start, end), overwriting any size fields that fall inside it."""
    marks = sorted(offset for offset in patches if start <= offset < end)
    position = start
    for offset in marks:
        _copy_plain(source, destination, position, offset)
        destination.write(patches[offset])
        position = offset + len(patches[offset])
    _copy_plain(source, destination, position, end)


def _copy_plain(source, destination, start, end):
    source.seek(start)
    remaining = end - start
    while remaining > 0:
        chunk = source.read(min(1024 * 1024, remaining))
        if not chunk:
            break
        destination.write(chunk)
        remaining -= len(chunk)

# -*- coding: utf-8 -*-
"""Format-neutral description of a scene, produced by the .ma and .mb parsers."""


class NodeRecord(object):
    __slots__ = ("type", "name", "line", "offset", "strings", "numbers", "extents", "attr_extents", "shared")

    def __init__(self, node_type, name, line=None, offset=None, shared=False):
        self.type = node_type
        self.name = name
        self.line = line
        self.offset = offset
        self.strings = {}       # attribute -> decoded string value
        self.numbers = {}       # attribute -> number (only .st/.stp are kept)
        self.extents = []       # every byte range the node owns, for whole-node removal
        self.attr_extents = {}  # attribute -> byte ranges, for removing just one value
        self.shared = shared    # shared/selected nodes are never deleted whole


class Anomaly(object):
    """Something the file should not contain according to its format."""
    __slots__ = ("kind", "command", "text", "line", "extent")

    def __init__(self, kind, command, text, line=None, extent=None):
        self.kind = kind
        self.command = command
        self.text = text
        self.line = line
        self.extent = extent


class SceneDoc(object):
    def __init__(self, path, file_type):
        self.path = path
        self.type = file_type
        self.nodes = []
        self.anomalies = []
        self.info_strings = []   # (label, value) from fileInfo and similar
        self.references = []     # referenced file paths
        self.parse_ok = True
        self.parse_note = ""
        self.raw_hits = []       # family strings found outside parsed structures
        self.layout = None       # format-specific data the cleaner needs


def base_name(name):
    """Strip DAG path and namespace prefixes from a node name."""
    value = str(name or "")
    value = value.rsplit("|", 1)[-1]
    return value.rsplit(":", 1)[-1]


def merge_ranges(ranges):
    ordered = sorted((int(a), int(b)) for a, b in ranges if int(b) > int(a))
    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged

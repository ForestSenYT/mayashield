# -*- coding: utf-8 -*-
"""Builders for test scenes.  Every "malicious" payload here is inert: it carries
the markers and structure the scanner looks for, but only ever prints a string."""
import base64
import struct
import zlib

INERT = "print('inert test payload')"
B64_INERT = base64.b64encode(INERT.encode()).decode()

HEADER = b"""//Maya ASCII 2024 scene
//Name: test.ma
//Last modified: Sat, Nov 07, 2026 12:00:00 AM
//Codeset: 936
requires maya "2024";
currentUnit -l centimeter -a degree -t film;
fileInfo "application" "maya";
fileInfo "version" "2024";
"""

FOOTER = b"""select -ne :time1;
\tsetAttr ".o" 1;
connectAttr "polyCube1.out" "boxShape.i";
connectAttr ":time1.o" "benign_expr.tim";
connectAttr "benign_expr.out[0]" "box.tx";
relationship "link" ":lightLinker1" ":initialShadingGroup.message" ":defaultLightSet.message";
// End of test.ma
"""

CLEAN_NODES = b"""createNode transform -n "box";
\trename -uid "00000000-0000-0000-0000-000000000001";
createNode mesh -n "boxShape" -p "box";
\trename -uid "00000000-0000-0000-0000-000000000002";
\tsetAttr ".vt[0:1]" -type "float3" 0 0 0 1 1 1
\t\t2 2 2 3 3 3;
createNode polyCube -n "polyCube1";
\trename -uid "00000000-0000-0000-0000-000000000003";
createNode expression -n "benign_expr";
\trename -uid "00000000-0000-0000-0000-000000000004";
\tsetAttr -k on ".nds";
\tsetAttr ".ixp" -type "string" ".O[0] = sin(time);";
createNode script -n "uiConfigurationScriptNode";
\trename -uid "00000000-0000-0000-0000-000000000005";
\tsetAttr ".b" -type "string" "// Maya Mel UI Configuration File.\\n// No UI generated in batch mode.\\n";
\tsetAttr ".st" 3;
createNode script -n "sceneConfigurationScriptNode";
\trename -uid "00000000-0000-0000-0000-000000000006";
\tsetAttr ".b" -type "string" "playbackOptions -min 1 -max 120 -ast 1 -aet 200 ";
\tsetAttr ".st" 6;
"""


def ma_scene(*nodes, **kwargs):
    """A .ma with benign content plus extra node text, placed before the footer."""
    middle = b"".join(item if isinstance(item, bytes) else item.encode("utf-8") for item in nodes)
    return HEADER + kwargs.get("before", b"") + CLEAN_NODES + middle + FOOTER


def script_node(name, payload, script_type=1, python=False):
    escaped = payload.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    text = 'createNode script -n "%s";\n\trename -uid "00000000-0000-0000-0000-0000000000AA";\n' % name
    text += '\tsetAttr ".b" -type "string" "%s";\n\tsetAttr ".st" %d;\n' % (escaped, script_type)
    if python:
        text += '\tsetAttr ".stp" 1;\n'
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# A minimal writer for Maya binary files, matching the layout Maya 2024 produces.
# ---------------------------------------------------------------------------
class Iff(object):
    def __init__(self, wide=True):
        self.wide = wide
        self.align = 8 if wide else 4

    def _header(self, tag, size):
        if self.wide:
            return tag + b"\x00\x00\x00\x00" + struct.pack(">Q", size)
        return tag + struct.pack(">I", size)

    def leaf(self, tag, payload):
        padding = -len(payload) % self.align
        return self._header(tag, len(payload)) + payload + b"\x00" * padding

    def group(self, kind, *children, **kwargs):
        tag = kwargs.get("tag") or (b"FOR8" if self.wide else b"FOR4")
        body = kind + b"".join(children)
        return self._header(tag, len(body)) + body

    def crea(self, name, parent=None):
        payload = b"\x05" + name.encode() + b"\x00"
        if parent:
            payload += parent.encode() + b"\x00"
        return self.leaf(b"CREA", payload + b"\x11" * 16)

    def string(self, attr, value):
        return self.leaf(b"STR ", attr.encode() + b"\x00 " + value.encode("utf-8") + b"\x00")

    def double(self, attr, value):
        return self.leaf(b"DBLE", attr.encode() + b"\x00 " + struct.pack(">d", value))

    def connection(self, source, destination):
        return self.leaf(b"CWFL", b"\x10" + source.encode() + b"\x00" + destination.encode() + b"\x00")


def mb_scene(*node_groups, **kwargs):
    iff = kwargs.get("iff") or Iff()
    head = iff.group(b"HEAD",
                     iff.leaf(b"VERS", b"2024"),
                     iff.leaf(b"FINF", b"application\x00maya\x00"))
    box = iff.group(b"XFRM", iff.crea("box"), iff.leaf(b"DBL3", b"t\x00 " + b"\x00" * 24))
    sn = iff.group(b"SCRP", iff.crea("sceneConfigurationScriptNode"),
                   iff.string("b", "playbackOptions -min 1 -max 120 "), iff.double("st", 6.0))
    expr = iff.group(b"DEXP", iff.crea("benign_expr"), iff.string("ixp", ".O[0] = sin(time);"))
    conns = kwargs.get("connections", [("benign_expr.out[0]", "box.tx")])
    links = iff.group(b"CONS", iff.group(b"CONN", *[iff.connection(a, b) for a, b in conns]),
                      tag=b"LIS8" if iff.wide else b"LIS4")
    return iff.group(b"Maya", head, box, expr, sn, *(list(node_groups) + [links]))


def mb_script_node(iff, name, payload, script_type=1, python=False):
    children = [iff.crea(name), iff.string("b", payload), iff.double("st", float(script_type))]
    if python:
        children.append(iff.double("stp", 1.0))
    return iff.group(b"SCRP", *children)


def packed_python(source):
    """base64(zlib(source)), the classic self-unpacking wrapper."""
    return base64.b64encode(zlib.compress(source.encode())).decode()

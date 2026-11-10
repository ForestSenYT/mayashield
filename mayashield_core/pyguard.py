# -*- coding: utf-8 -*-
"""Keeps Python's own parser from taking the process down.

Before Python 3.11 the parser and compiler recurse on the C stack while reading
one expression.  A statement with tens of thousands of chained operators or
attribute accesses (``'a' + 'a' + ...``) overflows that stack, and the process
dies without an exception: on Maya 2022 (Python 3.7) that is Maya itself, taken
down by a scene it was only being asked to scan.  Python 3.11 raises
RecursionError instead, which the analyzer already handles.

``too_deep`` is a cheap look at the source before it is handed to ``ast.parse``
or ``compile`` on those older interpreters.
"""
import re
import sys

NEEDED = sys.version_info < (3, 11)

# Links allowed in one statement.  Python 3.7 survives about 30,000 on a 1 MB stack;
# code written by people stays in the dozens.
MAX_LINKS = 3000
MAX_ELIF = 500
_SHORTEST_RISKY = 2 * MAX_LINKS   # a link takes at least two characters

# String literals are dropped before counting: a base64 icon is full of "+" and "/".
# Unrolled form, so a long literal costs no regex stack.
_STRINGS_RE = re.compile(
    r'"""[\s\S]*?(?:"""|\Z)|\'\'\'[\s\S]*?(?:\'\'\'|\Z)|"[^"\\\n]*(?:\\[\s\S][^"\\\n]*)*"|\'[^\'\\\n]*(?:\\[\s\S][^\'\\\n]*)*\'')
_COMMENT_RE = re.compile(r"#[^\n]*")
_LINK_RE = re.compile(r"[.+\-*/%|&^@<>\[(]|\b(?:and|or|if|else|in|is|not)\b")
_ELIF_RE = re.compile(r"^[ \t]*(?:elif|else\s*:\s*if)\b", re.M)


def too_deep(text):
    """Could parsing this Python source overflow the C stack on an old interpreter?

    Counts operators per statement, joining lines that an open bracket or a
    backslash continues.  Errs on the side of "too deep": the caller then reads
    the script with its regular-expression rules instead of the syntax tree.
    """
    if len(text) < _SHORTEST_RISKY:
        return False
    bare = _COMMENT_RE.sub("", _STRINGS_RE.sub('""', text))
    if len(bare) < _SHORTEST_RISKY:
        return False
    if len(_ELIF_RE.findall(bare)) > MAX_ELIF:
        return True
    links = 0
    depth = 0
    for line in bare.split("\n"):
        links += len(_LINK_RE.findall(line))
        if links > MAX_LINKS:
            return True
        depth += line.count("(") + line.count("[") + line.count("{") - line.count(")") - line.count("]") - line.count("}")
        if depth <= 0 and not line.endswith("\\"):
            links = 0
            depth = 0
    return False


def needs_guard(text):
    """True when this interpreter cannot be trusted to parse ``text`` safely."""
    return NEEDED and too_deep(text)

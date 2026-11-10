# -*- coding: utf-8 -*-
"""Payload analysis: turns a script or string into scored evidence.

Three things separate this from a keyword list:

* Python is read through its AST, so aliases (``import base64 as b``), dynamic
  lookups (``getattr(os, 'sys' + 'tem')``) and constant-built strings resolve to
  what they actually are.
* Encoded blobs are decoded (base64, hex, zlib/gzip/bz2) and the result is
  analysed again, so a payload can't hide behind one layer of encoding.
* Evidence is weighted and combined, so ``evalDeferred`` in a normal tool is
  no longer enough to quarantine it, while several weak signals together are.
"""
import ast
import base64
import binascii
import bz2
import math
import re
import sys
import zlib

from . import pyguard, rules

MAX_TEXT = 8 * 1024 * 1024
MAX_PY_PARSE = 2 * 1024 * 1024
MAX_DEPTH = 4
MAX_BLOBS = 64
MAX_BLOB_BYTES = 32 * 1024 * 1024
MAX_DECODED = 8 * 1024 * 1024
# Limits on the work one script may cause, however it is written.  Without them a few
# hundred bytes could keep the scanner (and so Maya, opening the scene) busy for minutes.
MAX_FOLDED = 64 * 1024            # longest string the constant folder will build
MAX_FOLDED_TOTAL = 4 * 1024 * 1024
MAX_NESTED_ANALYSES = 200         # analyses of strings found inside one script, at any depth

CONTEXT_SCRIPT = "script"       # script node / expression / injected statement
CONTEXT_ATTR = "attr"           # plain string attribute on some other node
CONTEXT_STARTUP = "startup"     # userSetup, Maya.env, prefs
CONTEXT_TOOL = "tool"           # any other .py/.mel on disk
CONTEXT_RUNTIME = "runtime"     # scriptJob descriptions

_SCALE = {CONTEXT_TOOL: 0.6, CONTEXT_STARTUP: 0.75}
# Writing to startup files or running decoded code is never routine in a startup
# file, so those keep full weight there; in other tools (installers do this) only
# running decoded code does.
_SCALE_EXEMPT = {
    CONTEXT_STARTUP: ("py_exec_decoded", "persist_write"),
    CONTEXT_TOOL: ("py_exec_decoded",),
}

_B64_RE = re.compile(r"[A-Za-z0-9+/_-]{32,}={0,2}")
_HEX_RE = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}){16,}(?![0-9A-Fa-f])")
# These run over whole scripts, so their cost has to grow with the length of the text
# and no faster.  Line-anchored ones skip blanks with [ \t]*, not \s* (which also eats
# the newlines and so retries every blank line from every earlier one); a comment that
# never closes ends at the end of the text; strings are written "unrolled", which
# matches the same text as (?:[^"\\]|\\.)* without a regex stack frame per character.
_PY_HINT_RE = re.compile(r"^[ \t]*(?:import\s+\w|from\s+\w+\s+import|def\s+\w+\s*\(|class\s+\w+|try\s*:|exec\s*\()", re.M)
_MEL_HINT_RE = re.compile(r"\$\w+|^[ \t]*(?:global\s+)?proc\s|;[ \t]*$|`", re.M)
_MEL_PY_CALL_RE = re.compile(r"(?<![\w.$])python\s*\(\s*\"([^\"\\]*(?:\\.[^\"\\]*)*)\"", re.S)
_MEL_COMMENT_RE = re.compile(r"\"[^\"\\]*(?:\\.[^\"\\]*)*\"|//[^\n]*|/\*.*?(?:\*/|\Z)", re.S)
_MEL_STRING_RE = re.compile(r"\"([^\"\\]*(?:\\.[^\"\\]*)*)\"", re.S)
_MEL_CONCAT_RE = re.compile(r"\"\s*\+\s*\"")
_PY_COMMENT_RE = re.compile(r"^[ \t]*#[^\n]*$", re.M)


def combine(weights):
    """Noisy-or: independent signals accumulate but can never exceed 100."""
    keep = 1.0
    for weight in weights:
        keep *= max(0.0, 1.0 - min(100, max(0, weight)) / 100.0)
    return int(round(100.0 * (1.0 - keep)))


def mel_unescape(text):
    if "\\" not in text:
        return text
    table = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
    return re.sub(r"\\(.)", lambda m: table.get(m.group(1), m.group(1)), text, flags=re.S)


class _Collector(object):
    """Accumulates evidence, keeping the strongest occurrence of each rule."""

    def __init__(self):
        self.items = {}
        self.families = []

    def add(self, rule, category, weight, detail=""):
        current = self.items.get(rule)
        if current is None or weight > current["weight"]:
            self.items[rule] = {"rule": rule, "category": category, "weight": int(weight), "detail": detail[:240]}

    def add_family(self, name):
        if name not in self.families:
            self.families.append(name)

    def merge(self, other, prefix=""):
        for family in other.get("families", ()):
            self.add_family(family)
        for item in other.get("evidence", ()):
            detail = (prefix + item.get("detail", "")) if prefix else item.get("detail", "")
            self.add(item["rule"], item["category"], item["weight"], detail)

    def has(self, rule):
        return rule in self.items


class _Budget(object):
    """How much more work one top-level script may still cause.

    Strings inside strings and blobs inside blobs are each analysed in turn.  Every
    step is small, but a few hundred bytes can ask for millions of them, so there is
    one allowance for the whole tree, not one per level.
    """

    def __init__(self):
        self.analyses = MAX_NESTED_ANALYSES
        self.decoded = MAX_BLOB_BYTES * 2
        self.exhausted = False

    def take(self, decoded_bytes=0):
        if self.analyses <= 0 or self.decoded < decoded_bytes:
            self.exhausted = True
            return False
        self.analyses -= 1
        self.decoded -= decoded_bytes
        return True


def _snippet(text, match):
    start = max(0, match.start() - 30)
    return " ".join(text[start:match.end() + 50].split())


def _sweep_family(text, out):
    if rules.FAMILY_ANY_RE.search(text) is None:
        return
    for rule, family, pattern, weight in rules.FAMILY_IOCS:
        match = pattern.search(text)
        if match:
            out.add(rule, "family", weight, _snippet(text, match))
            if weight >= rules.RISK_HIGH:
                out.add_family(family)


def _apply_rules(text, table, out):
    for rule, category, weight, pattern in table:
        match = pattern.search(text)
        if match:
            out.add(rule, category, weight, _snippet(text, match))


def _strip_mel_comments(text):
    return _MEL_COMMENT_RE.sub(lambda m: m.group(0) if m.group(0)[0] == '"' else " ", text)


def _guess_language(text):
    py = len(_PY_HINT_RE.findall(text[:200000]))
    mel = len(_MEL_HINT_RE.findall(text[:200000]))
    return "python" if py > mel else "mel"


# ---------------------------------------------------------------------------
# Python, through the AST.
# ---------------------------------------------------------------------------
if sys.version_info < (3, 8):  # Maya 2022: ast.parse emits Str/Bytes/Num there.
    def _const(node):
        if isinstance(node, ast.Str):
            return node.s
        if isinstance(node, ast.Bytes):
            return node.s
        if isinstance(node, ast.Num):
            return node.n
        if isinstance(node, ast.NameConstant):
            return node.value
        return None
else:
    def _const(node):
        return node.value if isinstance(node, ast.Constant) else None


def _literal_step(node):
    if node is None:
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _const(node.operand)
        return -value if isinstance(value, int) else None
    value = _const(node)
    return value if isinstance(value, int) else None


class _PyAnalyzer(ast.NodeVisitor):
    def __init__(self, out):
        self.out = out
        self.aliases = {}
        self.constants = {}
        self.folded = []
        self.folded_size = 0
        self.literals = []
        self.chr_calls = 0
        self.nested_code = []

    def keep_folded(self, value):
        """Remember a string the code builds, within a total allowance for the script."""
        if value and self.folded_size + len(value) <= MAX_FOLDED_TOTAL:
            self.folded_size += len(value)
            self.folded.append(value)

    # ------ name resolution ------
    def visit_Import(self, node):
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.aliases[local] = alias.name if alias.asname else alias.name.split(".")[0]

    def visit_ImportFrom(self, node):
        module = node.module or ""
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = "%s.%s" % (module, alias.name) if module else alias.name

    def visit_Assign(self, node):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value = self.fold(node.value)
            if isinstance(value, str):
                self.constants[name] = value
            else:
                resolved = self.resolve(node.value)
                if resolved:
                    self.aliases[name] = resolved
        self.generic_visit(node)

    def _visit_computed(self, node):
        """A string built at runtime is checked as the string it becomes."""
        value = self.fold(node)
        if isinstance(value, (str, bytes)):
            self.keep_folded(value)
            return
        self.generic_visit(node)

    def visit_BinOp(self, node):
        self._visit_computed(node)

    def visit_Subscript(self, node):
        self._visit_computed(node)

    def visit_Expr(self, node):
        # A bare string statement is a docstring: it runs nothing, and its examples
        # shouldn't be read as code.
        if not isinstance(_const(node.value), str):
            self.generic_visit(node)

    def resolve(self, node):
        """Dotted name for an expression, following aliases and dynamic lookups."""
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            return "%s.%s" % (base, node.attr) if base else None
        if isinstance(node, ast.Call):
            func = self.resolve(node.func)
            if func in ("__import__", "importlib.import_module") and node.args:
                return self.fold(node.args[0]) if isinstance(self.fold(node.args[0]), str) else None
            if func == "getattr" and len(node.args) >= 2:
                base = self.resolve(node.args[0])
                attr = self.fold(node.args[1])
                if base and isinstance(attr, str):
                    return "%s.%s" % (base, attr)
        return None

    # ------ constant folding: what would this expression evaluate to? ------
    def fold(self, node):
        value = _const(node)
        if isinstance(value, (str, bytes)):
            return value
        if isinstance(node, ast.Name):
            return self.constants.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.fold(node.left), self.fold(node.right)
            # Capped: `a = a + a` on every line would otherwise double a string without end.
            if isinstance(left, type(right)) and isinstance(left, (str, bytes)) and len(left) + len(right) <= MAX_FOLDED:
                return left + right
            return None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            target = self.fold(node.value)
            if isinstance(target, (str, bytes)) and _literal_step(node.slice.step) == -1 \
                    and node.slice.lower is None and node.slice.upper is None:
                return target[::-1]
            return None
        if isinstance(node, ast.Call):
            return self._fold_call(node)
        return None

    def _fold_call(self, node):
        func = self.resolve(node.func)
        if func == "chr" and node.args:
            value = _const(node.args[0])
            if isinstance(value, int) and 0 <= value < 0x110000:
                return chr(value)
            return None
        if isinstance(node.func, ast.Attribute) and node.func.attr == "join" and node.args:
            sep = self.fold(node.func.value)
            arg = node.args[0]
            if isinstance(sep, str) and isinstance(arg, (ast.List, ast.Tuple)):
                parts = [self.fold(item) for item in arg.elts]
                if all(isinstance(part, str) for part in parts) \
                        and sum(len(part) for part in parts) + len(sep) * len(parts) <= MAX_FOLDED:
                    return sep.join(parts)
            return None
        if isinstance(node.func, ast.Attribute) and node.func.attr == "decode":
            inner = self.fold(node.func.value)
            if isinstance(inner, bytes):
                return inner.decode("utf-8", "replace")
            return None
        if isinstance(node.func, ast.Attribute) and node.func.attr == "encode":
            inner = self.fold(node.func.value)
            if isinstance(inner, str):
                return inner.encode("utf-8", "replace")
            return None
        if func in ("bytes.fromhex", "binascii.unhexlify") and node.args:
            inner = self.fold(node.args[0])
            try:
                if isinstance(inner, bytes):
                    inner = inner.decode("ascii")
                if isinstance(inner, str):
                    return binascii.unhexlify(inner.strip())
            except (binascii.Error, ValueError):
                return None
            return None
        if func in ("base64.b64decode", "base64.urlsafe_b64decode", "binascii.a2b_base64") and node.args:
            inner = self.fold(node.args[0])
            if isinstance(inner, str):
                inner = inner.encode("ascii", "ignore")
            if isinstance(inner, bytes):
                decoded = _decode_b64(inner)
                if decoded is not None:
                    return decoded
            return None
        if func in ("zlib.decompress", "bz2.decompress") and node.args:
            inner = self.fold(node.args[0])
            if isinstance(inner, bytes):
                return _decompress(inner)
            return None
        return None

    # ------ behaviours ------
    def visit_Call(self, node):
        name = self.resolve(node.func)
        leaf = name.rsplit(".", 1)[-1] if name else (
            node.func.attr if isinstance(node.func, ast.Attribute) else "")

        if name in ("exec", "eval", "compile", "builtins.exec", "builtins.eval", "__builtin__.eval", "execfile"):
            self._check_exec(node, name)
        elif name == "__import__":
            if node.args and not isinstance(self.fold(node.args[0]), str):
                self.out.add("py_dyn_import", "execution", 15, "__import__ with a computed module name")
        elif name == "chr":
            self.chr_calls += 1
        elif name == "open" or name == "io.open" or name == "codecs.open":
            self._check_open(node)
        elif name == "getattr" and len(node.args) >= 2 and not isinstance(_const(node.args[1]), str):
            attr = self.fold(node.args[1])
            if isinstance(attr, str) and attr in ("system", "popen", "exec", "eval", "b64decode", "decompress"):
                self.out.add("py_evasive_getattr", "obfuscation", 30, "getattr building '%s'" % attr)

        if name:
            self._check_call_table(name, node)
        if leaf in rules.PY_MAYA_LEAF_RULES and (not name or "cmds" in name or name == leaf):
            rule, category, weight = rules.PY_MAYA_LEAF_RULES[leaf]
            creates = leaf != "scriptNode" or any(
                keyword.arg is None or keyword.arg in rules.SCRIPTNODE_CREATE_KWARGS for keyword in node.keywords)
            if creates:
                self.out.add(rule, category, weight, "%s(...)" % (name or leaf))
            if leaf == "scriptJob":
                for keyword in node.keywords:
                    if keyword.arg in rules.STICKY_SCRIPTJOB_KWARGS and _const(keyword.value) is True:
                        self.out.add("py_scriptjob_sticky", "trigger", 15, "protected or permanent scriptJob")
        if leaf == "createNode" and name and "cmds" in name and node.args and _const(node.args[0]) == "script":
            self.out.add("py_scriptnode", "replication", 30, "createNode('script')")
        if name and name.endswith(("cmds.file", "mc.file")) or (leaf == "file" and name and "cmds" in name):
            for keyword in node.keywords:
                if keyword.arg in ("save", "s", "saveAs") and _const(keyword.value) is True:
                    self.out.add("py_scene_save", "replication", 15, "cmds.file(save=True)")
        if leaf == "quit" and name and "cmds" in name:
            for keyword in node.keywords:
                if keyword.arg in ("f", "force") and _const(keyword.value) is True:
                    self.out.add("force_quit", "destructive", 35, "cmds.quit(force=True)")
        if name and name.endswith("mel.eval") and node.args:
            value = self.fold(node.args[0])
            if isinstance(value, str):
                self.nested_code.append(("mel", value))
            else:
                self.out.add("py_mel_eval_dynamic", "execution", 15, "mel.eval of a computed string")

        folded = self.fold(node)
        if isinstance(folded, (str, bytes)):
            self.keep_folded(folded)
        self.generic_visit(node)

    def _check_call_table(self, name, node):
        direct = rules.PY_CALL_RULES.get(name)
        if direct is None:
            for key, value in rules.PY_CALL_RULES.items():
                if (key.endswith(".") or key in rules.PY_CALL_PREFIXES) and name.startswith(key):
                    direct = value
                    break
        if direct is not None:
            self.out.add(direct[0], direct[1], direct[2], "%s(...)" % name)

    def _subtree_decodes(self, node):
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = self.resolve(child.func) or ""
                if name in rules.DECODER_CALLS or name.rsplit(".", 1)[-1] in rules.DECODER_LEAVES:
                    return True
                if isinstance(child.func, ast.Attribute) and child.func.attr in rules.DECODER_LEAVES:
                    return True
        return False

    def _check_exec(self, node, name):
        if not node.args:
            return
        arg = node.args[0]
        value = self.fold(arg)
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if self._subtree_decodes(arg):
            self.out.add("py_exec_decoded", "execution", 75, "%s() of decoded data" % name)
        elif not isinstance(value, str):
            self.out.add("py_exec_dynamic", "execution", 25, "%s() of a computed value" % name)
        if isinstance(value, str) and value:
            self.nested_code.append(("python", value))

    def _check_open(self, node):
        mode = ""
        if len(node.args) >= 2:
            mode = self.fold(node.args[1]) or ""
        for keyword in node.keywords:
            if keyword.arg == "mode":
                mode = self.fold(keyword.value) or ""
        if isinstance(mode, str) and any(flag in mode for flag in "wax+"):
            self.out.add("py_file_write", "filesystem", 10, "open(..., %r)" % mode)

    def visit_Str(self, node):  # Python 3.7
        if isinstance(node.s, str):
            self.literals.append(node.s)

    def visit_Bytes(self, node):  # Python 3.7
        self.literals.append(node.s.decode("latin-1"))

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            self.literals.append(node.value)
        elif isinstance(node.value, bytes):
            self.literals.append(node.value.decode("latin-1"))


def _analyze_python(text, out, context, depth, budget):
    if len(text) > MAX_PY_PARSE:
        _apply_rules(text, rules.PY_FALLBACK_RULES, out)
        if context == CONTEXT_SCRIPT:
            out.add("py_too_large", "obfuscation", 25, "%d characters of Python in one script" % len(text))
        return
    if pyguard.needs_guard(text):
        # Python before 3.11 overflows its C stack on this and takes the process with it.
        out.add("py_too_complex", "obfuscation", 40, "Code nested too deeply to analyze")
        _apply_rules(_PY_COMMENT_RE.sub("", text), rules.PY_FALLBACK_RULES, out)
        return
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        # Python 2 source or a newer syntax: Maya can't run it either if it's invalid.
        _apply_rules(_PY_COMMENT_RE.sub("", text), rules.PY_FALLBACK_RULES, out)
        return
    except (RecursionError, MemoryError):
        out.add("py_too_complex", "obfuscation", 40, "Code nested too deeply to analyze")
        _apply_rules(_PY_COMMENT_RE.sub("", text), rules.PY_FALLBACK_RULES, out)
        return
    visitor = _PyAnalyzer(out)
    # One statement at a time: an absurdly deep expression must not hide what follows.
    for statement in tree.body:
        try:
            visitor.visit(statement)
        except (RecursionError, MemoryError):
            out.add("py_too_complex", "obfuscation", 40, "Code nested too deeply to analyze")
            _apply_rules(_PY_COMMENT_RE.sub("", text), rules.PY_FALLBACK_RULES, out)
    if visitor.chr_calls >= 16:
        out.add("py_chr_obfuscation", "obfuscation", 30, "%d chr() calls" % visitor.chr_calls)

    for lang, code in visitor.nested_code:
        if depth < MAX_DEPTH and budget.take():
            out.merge(analyze_text(code, lang=lang, context=context, _depth=depth + 1, _budget=budget), "nested: ")
    for piece in visitor.folded:
        if isinstance(piece, bytes):
            piece = piece.decode("utf-8", "replace")
        if len(piece) >= 6 and depth < MAX_DEPTH:
            _sweep_family(piece, out)
            _apply_rules(piece, rules.TEXT_RULES, out)
            if rules.CODE_HINT_RE.search(piece) and budget.take():
                out.merge(analyze_text(piece, context=context, _depth=depth + 1, _budget=budget), "computed string: ")
    for literal in visitor.literals:
        _apply_rules(literal, rules.TEXT_RULES, out)
        # Code in a string: evalDeferred("..."), scriptJob(event=[..., "..."]) and so on.
        if depth < MAX_DEPTH and len(literal) <= MAX_PY_PARSE and rules.CODE_HINT_RE.search(literal) and budget.take():
            out.merge(analyze_text(literal, context=context, _depth=depth + 1, _budget=budget), "in a string: ")


# ---------------------------------------------------------------------------
# Encoded payloads.
# ---------------------------------------------------------------------------
def _decode_b64(blob):
    try:
        blob = blob.replace(b"-", b"+").replace(b"_", b"/")
        blob += b"=" * (-len(blob) % 4)
        return base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError):
        return None


def _decompress(data):
    for wbits in (47, -15):  # zlib or gzip (autodetected), then raw deflate
        try:
            engine = zlib.decompressobj(wbits)
            result = engine.decompress(data, MAX_DECODED)
            if result:
                return result
        except zlib.error:
            pass
    try:
        engine = bz2.BZ2Decompressor()
        return engine.decompress(data, MAX_DECODED)
    except (OSError, ValueError, EOFError):
        return None


def _bytes_to_text(data):
    """Text if these bytes look like text, else None."""
    if not data:
        return None
    sample = data[:4096]
    if b"\x00" in sample:
        return None
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    printable = sum(1 for byte in sample if 32 <= byte < 127 or byte in (9, 10, 13))
    return data.decode("latin-1") if printable / float(len(sample)) > 0.9 else None


def _entropy(text):
    sample = text[:4096]
    counts = {}
    for char in sample:
        counts[char] = counts.get(char, 0) + 1
    total = float(len(sample))
    return -sum((n / total) * math.log(n / total, 2) for n in counts.values()) if total else 0.0


def _analyze_blobs(text, out, context, depth, shared):
    seen = 0
    budget = MAX_BLOB_BYTES
    done = set()
    candidates = [(m, "base64") for m in _B64_RE.finditer(text)] + [(m, "hex") for m in _HEX_RE.finditer(text)]
    candidates.sort(key=lambda item: -len(item[0].group(0)))
    if len(candidates) > MAX_BLOBS and context == CONTEXT_SCRIPT:
        out.add("many_blobs", "obfuscation", 25, "%d encoded-looking strings in one script" % len(candidates))
    for match, kind in candidates[:MAX_BLOBS]:
        blob = match.group(0)
        if (kind, blob) in done:
            continue   # the same blob a second time says nothing new
        done.add((kind, blob))
        budget -= len(blob)
        if budget < 0:
            break
        if kind == "hex":
            try:
                raw = binascii.unhexlify(blob)
            except (binascii.Error, ValueError):
                continue
        else:
            raw = _decode_b64(blob[: MAX_DECODED * 2].encode("ascii", "ignore"))
            if raw is None:
                continue
        seen += 1
        layers = kind
        unpacked = _decompress(raw)
        if unpacked:
            raw = unpacked
            layers += "+compressed"
        decoded = _bytes_to_text(raw)
        if decoded is None:
            # Binary after decoding: still sweep for family strings (marshalled code).
            probe = raw.decode("latin-1")
            before = len(out.items)
            _sweep_family(probe, out)
            found_family = len(out.items) > before   # asked before anything else is added
            if context == CONTEXT_SCRIPT and len(blob) >= 200:
                out.add("opaque_blob", "obfuscation", 25, "%d char %s blob with binary content" % (len(blob), kind))
            if found_family:
                out.add("encoded_payload", "obfuscation", 35, "family string inside a %s blob" % layers)
            continue
        if not shared.take(len(decoded)):
            break
        inner = analyze_text(decoded, context=context, _depth=depth + 1, _budget=shared)
        if inner["evidence"]:
            out.merge(inner, "in decoded %s: " % layers)
            out.add("encoded_payload", "obfuscation", 35, "behaviour hidden inside a %s blob" % layers)
        elif context == CONTEXT_SCRIPT and rules.CODE_HINT_RE.search(decoded):
            out.add("encoded_payload", "obfuscation", 20, "code stored inside a %s blob" % layers)
    if context == CONTEXT_SCRIPT and not seen:
        for match in _B64_RE.finditer(text):
            if len(match.group(0)) >= 200 and _entropy(match.group(0)) >= 4.5:
                out.add("opaque_blob", "obfuscation", 25, "%d char high-entropy string" % len(match.group(0)))
                break


def _apply_combos(out):
    for rule, category, weight, slots in rules.COMBO_RULES:
        if all(any(out.has(option) for option in slot) for slot in slots):
            parts = [next(option for option in slot if out.has(option)) for slot in slots]
            out.add(rule, category, weight, " + ".join(parts))


def _finish(out, context):
    scale = _SCALE.get(context, 1.0)
    exempt = _SCALE_EXEMPT.get(context, ())
    evidence = []
    for item in out.items.values():
        weight = item["weight"]
        if item["category"] not in rules.UNSCALED_CATEGORIES and item["rule"] not in exempt:
            weight = int(round(weight * scale))
        evidence.append(dict(item, weight=weight))
    evidence.sort(key=lambda entry: -entry["weight"])
    return {"score": combine(entry["weight"] for entry in evidence), "evidence": evidence,
            "families": list(out.families)}


def analyze_text(text, lang=None, context=CONTEXT_SCRIPT, _depth=0, _budget=None):
    """Analyse one script or string.  Returns {score, evidence, families}."""
    out = _Collector()
    if not text:
        return {"score": 0, "evidence": [], "families": []}
    budget = _budget if _budget is not None else _Budget()

    # Family markers are looked for in everything, before any size limit applies.
    _sweep_family(text, out)
    if len(text) > MAX_TEXT:
        if context == CONTEXT_SCRIPT:
            out.add("oversized_script", "obfuscation", 30, "%d characters in one script" % len(text))
        text = text[: MAX_TEXT // 2] + "\n" + text[-MAX_TEXT // 2:]

    if lang is None:
        lang = _guess_language(text)

    if lang == "python":
        _analyze_python(text, out, context, _depth, budget)
        code_text = _PY_COMMENT_RE.sub("", text)
    else:
        # "a" + "b" is one string by the time MEL uses it.
        code_text = _MEL_CONCAT_RE.sub("", _strip_mel_comments(text))
        _apply_rules(code_text, rules.MEL_RULES, out)
        if _depth < MAX_DEPTH:
            for match in _MEL_PY_CALL_RE.finditer(code_text):
                if not budget.take():
                    break
                body = mel_unescape(match.group(1))
                out.merge(analyze_text(body, lang="python", context=context, _depth=_depth + 1, _budget=budget),
                          "python(): ")
            # Any string could reach python(), eval or evalDeferred through a variable,
            # so a string that looks like code is read as both languages.
            for match in _MEL_STRING_RE.finditer(code_text):
                body = mel_unescape(match.group(1))
                if rules.CODE_HINT_RE.search(body):
                    if not budget.take():
                        break
                    out.merge(analyze_text(body, lang="python", context=context, _depth=_depth + 1, _budget=budget),
                              "in a string: ")
                    out.merge(analyze_text(body, lang="mel", context=context, _depth=_depth + 1, _budget=budget),
                              "in a string: ")

    _apply_rules(code_text, rules.TEXT_RULES, out)
    if rules.CONFIRM_DIALOG_RE.search(code_text):
        out.add("confirm_dialog", "destructive", 0, "confirmDialog")
    if _depth < MAX_DEPTH:
        _analyze_blobs(text, out, context, _depth, budget)
    if _depth == 0 and budget.exhausted:
        out.add("analysis_budget", "obfuscation", 25, "More layers of strings and encoded data than a script has any use for")
    _apply_combos(out)
    # Weights are scaled for the context once, at the top.  What a nested string
    # contributed has to weigh the same however deep it was found.
    return _finish(out, context if _depth == 0 else None)


def analyze_attr(text):
    """Analyse a plain string attribute: cheap unless it could really be code."""
    if not text:
        return {"score": 0, "evidence": [], "families": []}
    out = _Collector()
    _sweep_family(text, out)
    if rules.CODE_HINT_RE.search(text):
        out.merge(analyze_text(text, context=CONTEXT_ATTR))
        if any(item["weight"] >= rules.RISK_LOW for item in out.items.values()):
            out.add("hidden_payload", "obfuscation", 25, "executable code stored in a data attribute")
    elif len(text) >= 256:
        _analyze_blobs(text, out, CONTEXT_ATTR, 0, _Budget())
    if not out.items and not out.families:   # the usual case by far: a name, a path, a note
        return {"score": 0, "evidence": [], "families": []}
    _apply_combos(out)
    return _finish(out, CONTEXT_ATTR)


def merge_analyses(results):
    """Combine several analyses of the same object (noisy-or across evidence)."""
    out = _Collector()
    for result in results:
        out.merge(result)
    return _finish(out, None)

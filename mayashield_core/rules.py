# -*- coding: utf-8 -*-
"""Detection rules: family indicators, behaviour weights and thresholds.

A *family indicator* (IOC) is specific enough to decide on its own.  A
*behaviour* is something legitimate code may also do, so it only contributes a
weight; scores are combined with a noisy-or (see ``analyzer.combine``) so weak
signals add up without any single common word being able to condemn a file.
"""
import re

RISK_HIGH = 70
RISK_MEDIUM = 40
RISK_LOW = 15


def risk_band(score):
    if score >= RISK_HIGH:
        return "high"
    if score >= RISK_MEDIUM:
        return "medium"
    if score >= RISK_LOW:
        return "low"
    return "info"


CONFIDENCE_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "error": 4}

# Rules whose weight is not reduced when found in on-disk tool files.
UNSCALED_CATEGORIES = ("family",)


# ---------------------------------------------------------------------------
# Family indicators.
# (rule id, family, regex).  Sources: the indicators shipped with MayaShield 3.x,
# plus the deobfuscated "Mel UI Configuration" sample published at
# https://gist.github.com/OriginalAdric/acbe902c89064e8300f2bc475a953e05
# ---------------------------------------------------------------------------
_FAMILY_VACCINE = "Vaccine (PuTianTongQi)"
_FAMILY_MELUI = "MayaMelUIConfigurationFile"
_FAMILY_SECURE = "maya_secure_system"
_FAMILY_SELFTEST = "MayaShield self-test sample"

_FAMILY_DEFS = (
    ("ioc_vaccine_gene", _FAMILY_VACCINE, r"\bvaccine_gene\b", 100),
    ("ioc_breed_gene", _FAMILY_VACCINE, r"\bbreed_gene\b", 100),
    ("ioc_import_vaccine", _FAMILY_VACCINE, r"\bimport\s+vaccine\b", 100),
    ("ioc_vaccine_file", _FAMILY_VACCINE, r"\bvaccine\.pyc?\b", 100),
    ("ioc_vaccine_phage", _FAMILY_VACCINE, r"\bvaccine\.phage\b|\bleukocyte\s*\.\s*occupation\b", 100),
    ("ioc_vaccine_message", _FAMILY_VACCINE, u"你的文件贼健康", 100),
    ("ioc_melui_node", _FAMILY_MELUI, r"\bMayaMelUIConfigurationFile\b", 100),
    ("ioc_melui_header", _FAMILY_MELUI, r"Maya Mel UI Configuration File\.Maya Mel UI Conf", 100),
    ("ioc_melui_proc", _FAMILY_MELUI, r"\bUI_Mel_Configuration_think(?:_a|_b)?\b", 100),
    # autoUpdateAttrEd on its own is a genuine Maya procedure; only these
    # misspelled or suffixed variants belong to the malware.
    ("ioc_melui_autoupdate", _FAMILY_MELUI,
     r"\bautoUpdatcAttrEd\b|\bautoUpdat[co]AttrEnd\b|\bautoUpdateAttrEd_(?:SelectSystem|aoto_int)\b", 100),
    ("ioc_melui_var", _FAMILY_MELUI, r"\$fuck_All_U\b", 100),
    ("ioc_secure_system", _FAMILY_SECURE, r"\bmaya_secure_system\b", 100),
    ("ioc_code_chunk", _FAMILY_SECURE, r"\bcodeChunk\d+\b", 70),
    ("ioc_code_extractor", _FAMILY_SECURE, r"\bcodeExtractor\b", 50),
    # Not a virus: the marker the installer's self-test puts in its sample (see selftest.py),
    # so that checking an install never writes a real signature to disk.
    ("ioc_selftest", _FAMILY_SELFTEST, r"MAYASHIELD-SELFTEST-4F3A9C2E", 100),
)

FAMILY_IOCS = tuple(
    (rule, family, re.compile(pattern, re.I), weight) for rule, family, pattern, weight in _FAMILY_DEFS
)


# One search that says "any family marker at all?", so ordinary strings cost one regex.
FAMILY_ANY_RE = re.compile("|".join(pattern for _r, _f, pattern, _w in _FAMILY_DEFS), re.I)

# Safety net over the raw bytes of a .mb, for chunks the parser doesn't interpret.
# Exact-case literals: this runs across whole files, and starting every alternative
# with a literal keeps the regex engine fast.  Parsed strings get the full rules.
_RAW_MARKERS = (
    "vaccine_gene", "breed_gene", "import vaccine", "vaccine.py", "vaccine.phage", "leukocyte.occupation",
    "MayaMelUIConfigurationFile", "Maya Mel UI Configuration File.Maya Mel UI Conf",
    "UI_Mel_Configuration_think", "autoUpdatcAttrEd", "autoUpdatoAttrEnd", "autoUpdatcAttrEnd",
    "autoUpdateAttrEd_SelectSystem", "autoUpdateAttrEd_aoto_int", "$fuck_All_U", "maya_secure_system",
    u"你的文件贼健康",
)
FAMILY_BYTES_RE = re.compile(b"|".join(
    re.escape(piece) for marker in _RAW_MARKERS for piece in sorted(set((marker.encode("utf-8"), marker.encode("gbk"))))
))

# Drop-file names that are decisive wherever they are found in a Maya folder.
SUSPICIOUS_FILENAMES = {
    "maya_secure_system.py": _FAMILY_SECURE,
    "maya_secure_system.pyc": _FAMILY_SECURE,
    "vaccine.py": _FAMILY_VACCINE,
    "vaccine.pyc": _FAMILY_VACCINE,
    "userperf.mel": _FAMILY_SECURE,
}
SUSPICIOUS_PYCACHE_RE = re.compile(r"^(?:vaccine|maya_secure_system)\.cpython-\d+\.pyc$", re.I)

# Script/expression nodes with these names are decisive.
SCRIPT_NODE_NAME_IOCS = (
    (re.compile(r"^vaccine_gene\d*$", re.I), _FAMILY_VACCINE),
    (re.compile(r"^breed_gene\d*$", re.I), _FAMILY_VACCINE),
    (re.compile(r"^MayaMelUIConfigurationFile\d*$", re.I), _FAMILY_MELUI),
    (re.compile(r"maya_secure_system", re.I), _FAMILY_SECURE),
)
# Only on network nodes: a mesh or a tool called "codeChunk1" means nothing.
NETWORK_NODE_NAME_IOCS = (
    (re.compile(r"^codeExtractor\d*$", re.I), _FAMILY_SECURE),
    (re.compile(r"^codeChunk\d*$", re.I), _FAMILY_SECURE),
)
# Worth a look on executable nodes, never enough to act on by themselves.
WEAK_SCRIPT_NAME_RE = re.compile(r"vaccine|leukocyte|secure_system|usersetup|userperf|diversion", re.I)

OFFICIAL_SCRIPT_NODES = {
    "uiconfigurationscriptnode",
    "sceneconfigurationscriptnode",
    "lookdevxuiconfigurationscriptnode",
}

# Script node .st values that run without anyone asking (0 = on demand).
AUTO_EXEC_SCRIPT_TYPES = (1, 2, 3, 4, 5, 6, 7)
# A scene that starts processes or goes online by itself, as soon as it's opened.
AUTO_RUN_ESCALATED_CATEGORIES = ("process", "network")

# ---------------------------------------------------------------------------
# Maya ASCII grammar: the only top-level commands Maya itself writes.  Anything
# else is an injected statement that would run the moment the file is opened.
# Measured against 319 scenes shipped with Maya 2024 plus freshly saved ones.
# ---------------------------------------------------------------------------
MA_ALLOWED_COMMANDS = frozenset((
    b"addAttr", b"aliasAttr", b"applyMetadata", b"connectAttr", b"createNode", b"currentUnit",
    b"dataStructure", b"deleteAttr", b"disconnectAttr", b"file", b"fileInfo", b"instanceable",
    b"lockNode", b"parent", b"relationship", b"rename", b"reorder", b"requires", b"select",
    b"setAttr", b"webTutorialNotify",
))

# Top-level statements that run code by their nature: calls that execute text,
# definitions, variables and control flow.  One of these in a scene was put
# there by somebody, and it runs the moment the file opens (checked in Maya
# 2026: a top-level python() or source in a .ma both run on open).  An unknown
# command that is not on this list is still unusual, but less telling.
MA_CODE_COMMANDS = frozenset((
    b"python", b"source", b"eval", b"evalDeferred", b"evalEcho", b"catch", b"catchQuiet", b"system", b"exec",
    b"popen", b"scriptJob", b"scriptNode", b"commandPort", b"loadPlugin", b"unloadPlugin", b"global", b"proc",
    b"if", b"for", b"while", b"do", b"switch", b"string", b"int", b"float", b"vector", b"matrix",
    b"fopen", b"fprint", b"fwrite", b"sysFile",
))

# String attributes Maya runs as MEL without anyone opening a script editor.
# Short and long names as saved by Maya 2026.  The render callbacks belong to
# defaultRenderGlobals and run when a render starts or ends; the particle
# expressions run when the simulation is evaluated.
RENDER_CALLBACK_ATTRS = frozenset((
    "pram", "poam", "prlm", "polm", "prm", "pom", "pfrm", "pfom",
    "preMel", "postMel", "preRenderLayerMel", "postRenderLayerMel", "preRenderMel", "postRenderMel",
    "preFurRenderMel", "postFurRenderMel",
))
PARTICLE_EXPRESSION_ATTRS = frozenset((
    "icx", "irx", "irbx", "irax",
    "internalCreationExpression", "internalRuntimeExpression", "internalRuntimeBeforeDynamicsExpression",
    "internalRuntimeAfterDynamicsExpression",
))

# ---------------------------------------------------------------------------
# Behaviours: (rule id, category, weight, regex).
# ---------------------------------------------------------------------------
_F = re.I

TEXT_RULES = tuple(
    (r, c, w, re.compile(p, _F))
    for r, c, w, p in (
        ("startup_usersetup", "persistence", 35, r"userSetup\.(?:py|mel)\b"),
        ("startup_prefs", "persistence", 20, r"\b(?:userPrefs|pluginPrefs|userPerf)\.mel\b"),
        ("startup_env", "persistence", 20, r"\bMaya\.env\b"),
        ("startup_l10n", "persistence", 60, r"animImportExport\.pres\.mel|resources[\\/]+l10n"),
        ("app_dir_probe", "persistence", 15,
         r"internalVar\s*\(?[^;\n]{0,40}?\b(?:userAppDir|userScriptDir|uad|usd)\b"),
        ("force_quit", "destructive", 35, r"\bquit\s+-f(?:orce)?\b|\bquit\s*\([^)]*\b(?:f|force)\s*=\s*True"),
        ("date_trigger", "trigger", 15, r"\babout\s+-(?:cd|currentDate)\b"),
    )
)

MEL_RULES = tuple(
    (r, c, w, re.compile(p))
    for r, c, w, p in (
        ("mel_system", "process", 45, r"(?<![\w.$])system\s*(?:\(|\")"),
        ("mel_exec", "process", 30, r"(?<![\w.$])exec\s*\("),
        ("mel_python", "execution", 10, r"(?<![\w.$])python\s*\("),
        ("mel_eval_dynamic", "execution", 20, r"(?<![\w.$])eval\s*\(?\s*\$"),
        ("mel_file_write", "filesystem", 15, r"(?<![\w.$])(?:fopen|fprint|fwrite)\b"),
        ("mel_sysfile", "filesystem", 30, r"\bsysFile\s+-(?:delete|del|rename|ren|copy|cp|move|mov)\b"),
        # Creating a node that carries code.  Running an existing one (-eb/-ea) is
        # normal: Maya's own interactive tutorials do it hundreds of times.
        ("mel_scriptnode", "replication", 30,
         r"(?<![\w.$])scriptNode\s[^;]{0,400}?-(?:bs|beforeScript|as|afterScript)\b|\bcreateNode\s+\"?script\b"),
        ("mel_scene_save", "replication", 15, r"(?<![\w.$])file\s+(?:-[a-zA-Z]+\s+[^;]{0,80}?)?-(?:save|s)\b"),
        # Rigs and pipeline tools register scriptJobs as a matter of course, protected
        # ones included, so these say little alone.  They still complete the
        # combinations below (self_replication, triggered_quit).
        ("mel_scriptjob", "trigger", 15, r"(?<![\w.$])scriptJob\s+-"),
        ("mel_scriptjob_sticky", "trigger", 8, r"\bscriptJob\b[^;]{0,200}-(?:protected|pro|permanent|per)\b"),
        ("mel_infinite_loop", "destructive", 20, r"\bwhile\s*\(\s*(?:1|true)\s*\)"),
    )
)

# Used when Python source does not parse, e.g. Python 2 code or a newer syntax.
PY_FALLBACK_RULES = tuple(
    (r, c, w, re.compile(p, re.S))
    for r, c, w, p in (
        ("py_exec_decoded", "execution", 75, r"\b(?:exec|eval)\s*\(.{0,200}?(?:b64decode|decompress|unhexlify|fromhex)"),
        ("py_exec_dynamic", "execution", 25, r"\b(?:exec|eval)\s*\(\s*[^\"'\s)]"),
        ("py_b64", "obfuscation", 12, r"\bb64decode\s*\("),
        ("py_zlib", "obfuscation", 15, r"\bdecompress\s*\("),
        ("py_marshal", "obfuscation", 40, r"\bmarshal\s*\.\s*loads"),
        ("py_process", "process", 45, r"\bos\s*\.\s*(?:system|popen|startfile)\s*\(|\bsubprocess\s*\."),
        ("py_network", "network", 40, r"\burllib\d?\b|\brequests\s*\.\s*(?:get|post)\b|\bsocket\s*\.\s*socket\b|\bftplib\b"),
        ("py_scriptjob", "trigger", 20, r"\bscriptJob\s*\("),
        ("py_scriptnode", "replication", 30,
         r"\bscriptNode\s*\([^)]*\b(?:bs|beforeScript|afterScript)\s*=|\bcreateNode\s*\(\s*[\"']script[\"']"),
        ("py_scene_save", "replication", 15, r"\bcmds\s*\.\s*file\s*\([^)]*\b(?:save|s)\s*=\s*True"),
    )
)

# Resolved call name -> (rule id, category, weight).  Keys match on the dotted
# name after aliases are resolved, either exactly or as a prefix (see
# PY_CALL_PREFIXES for the ones that aren't whole modules).
PY_CALL_RULES = {
    "base64.b64decode": ("py_b64", "obfuscation", 12),
    "base64.urlsafe_b64decode": ("py_b64", "obfuscation", 12),
    "base64.b32decode": ("py_b64", "obfuscation", 12),
    "base64.b85decode": ("py_b64", "obfuscation", 12),
    "binascii.unhexlify": ("py_b64", "obfuscation", 12),
    "binascii.a2b_base64": ("py_b64", "obfuscation", 12),
    "codecs.decode": ("py_codecs", "obfuscation", 10),
    "zlib.decompress": ("py_zlib", "obfuscation", 15),
    "bz2.decompress": ("py_zlib", "obfuscation", 15),
    "lzma.decompress": ("py_zlib", "obfuscation", 15),
    "marshal.loads": ("py_marshal", "obfuscation", 40),
    "pickle.loads": ("py_pickle", "obfuscation", 30),
    "os.system": ("py_process", "process", 45),
    "os.popen": ("py_process", "process", 45),
    "os.startfile": ("py_process", "process", 45),
    "os.spawn": ("py_process", "process", 45),
    "os.exec": ("py_process", "process", 45),
    "os.posix_spawn": ("py_process", "process", 45),
    "subprocess.": ("py_process", "process", 45),
    "ctypes.": ("py_ctypes", "process", 30),
    "winreg.": ("py_registry", "persistence", 35),
    "_winreg.": ("py_registry", "persistence", 35),
    "urllib.": ("py_network", "network", 40),
    "urllib2.": ("py_network", "network", 40),
    "requests.": ("py_network", "network", 40),
    "socket.": ("py_network", "network", 40),
    "http.client.": ("py_network", "network", 40),
    "ftplib.": ("py_network", "network", 40),
    "shutil.rmtree": ("py_delete", "destructive", 15),
    "importlib.import_module": ("py_dyn_import", "execution", 8),
}

# os.spawnl, os.spawnv, os.execv, os.execve and so on.
PY_CALL_PREFIXES = ("os.spawn", "os.exec", "os.posix_spawn")

# Attribute calls recognised by their last component on maya.cmds-like objects.
PY_MAYA_LEAF_RULES = {
    "scriptJob": ("py_scriptjob", "trigger", 15),
    "scriptNode": ("py_scriptnode", "replication", 30),
}

STICKY_SCRIPTJOB_KWARGS = ("protected", "pro", "permanent", "per")
# scriptNode flags that create a node carrying code, as opposed to running one.
SCRIPTNODE_CREATE_KWARGS = ("bs", "beforeScript", "afterScript", "st", "scriptType", "stp", "sourceType")

DECODER_LEAVES = (
    "b64decode", "urlsafe_b64decode", "b32decode", "b85decode", "a85decode", "unhexlify", "a2b_base64",
    "decompress", "fromhex",
)
# Named in full: json.loads and friends are ordinary and turn nothing into code.
DECODER_CALLS = ("marshal.loads", "pickle.loads", "cPickle.loads")

# Combination rules: (rule id, category, weight, group of alternatives per slot).
COMBO_RULES = (
    ("decode_chain", "obfuscation", 30,
     (("py_b64", "py_codecs"), ("py_zlib",))),
    ("persist_write", "persistence", 65,
     (("startup_usersetup", "startup_prefs", "startup_env", "startup_l10n"),
      ("mel_file_write", "mel_sysfile", "py_file_write"))),
    ("self_replication", "replication", 60,
     (("mel_scriptnode", "py_scriptnode"),
      ("mel_scene_save", "py_scene_save", "mel_scriptjob", "py_scriptjob"))),
    ("fake_crash", "destructive", 50,
     (("force_quit",), ("confirm_dialog",))),
    ("triggered_quit", "destructive", 40,
     (("force_quit",), ("mel_scriptjob", "py_scriptjob"))),
    ("download_and_run", "execution", 75,
     (("py_network",), ("py_exec_dynamic", "py_exec_decoded"))),
)

CONFIRM_DIALOG_RE = re.compile(r"\bconfirmDialog\b")

# Quick test for "could this string attribute be code at all?".
CODE_HINT_RE = re.compile(
    r"\bimport\s+\w|\bexec\s*\(|\beval\s*\(|\bpython\s*\(|\bsystem\s*[(\"]|b64decode|decompress|"
    r"\bscriptJob\b|\bscriptNode\b|userSetup|__import__|\bos\.\w+\(|\bsubprocess\b|\bglobal\s+proc\b|"
    r"\bsysFile\b|\bfopen\b|\bquit\s+-f|\bevalDeferred\b|\bcmdFileOutput\b"
)

# Environment variables that change what Maya executes at startup.
ENV_PATH_KEYS = (
    "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "MAYA_SCRIPT_PATH", "MAYA_PLUG_IN_PATH",
    "MAYA_MODULE_PATH", "MAYA_PRESET_PATH", "MAYA_SHELF_PATH",
)
SUSPICIOUS_PATH_RE = re.compile(
    r"[\\/](?:temp|tmp|appdata[\\/]+local[\\/]+temp|users[\\/]+public|\$recycle\.bin|programdata[\\/]+temp)(?:[\\/]|$)",
    re.I,
)

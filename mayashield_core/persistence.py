# -*- coding: utf-8 -*-
"""Scanning and cleaning the places Maya runs code from at startup.

Takes a ``mayapaths.snapshot()`` so it can run off the main thread.  Covers what
malware actually writes to: the shared ``maya/scripts`` folder (where the Vaccine
family drops its files), every installed version and language folder, the real
``sys.path``, ``Maya.env``, module files, and the localized files under the Maya
install used by the Mel UI Configuration family.
"""
import datetime
import hashlib
import json
import os
import re
import shutil

from . import analyzer, mayapaths, rules
from .ma_parser import iter_statements
from .scanner import now

_STARTUP_NAMES = ("usersetup.py", "usersetup.mel", "userprefs.mel", "pluginprefs.mel", "maya.env")
_PREFS_NAMES = ("userprefs.mel", "pluginprefs.mel")
_PREFS_ACTIVE_CATEGORIES = ("process", "execution", "filesystem", "replication", "trigger")
_L10N_INFECTED = "animImportExport.pres.mel"
_L10N_FOLDERS = ("plug-ins", "plugins")
_SCAN_EXTENSIONS = (".py", ".mel", ".pyc", ".mod", ".env")
_SKIP_FOLDERS = ("__pycache__", ".git", ".svn", "site-packages", "node_modules", "venv", ".venv")
_MAX_FILE_BYTES = 8 * 1024 * 1024     # tool scripts: more than this is only partly read
_MAX_STARTUP_BYTES = 64 * 1024 * 1024  # what Maya runs at startup gets read in full
_OVERSIZED_STARTUP_BYTES = 32 * 1024 * 1024
_MAX_FILES = 20000
_MAX_DEPTH = 6

MEL_INFECTION_HEADER = b"// Maya Mel UI Configuration File.Maya Mel UI Conf"
_PY_FAMILY_LINE_RE = re.compile(
    r"^\s*(?:import\s+vaccine\b|from\s+vaccine\b|.*\bvaccine\.phage\b|.*\bleukocyte\b|.*\bmaya_secure_system\b)", re.I)

# The block `mayashield -immunize` writes.  It is skipped only when it matches
# exactly, so putting a marker in a file cannot exempt it from scanning.
GUARD_BEGIN = "# >>> MAYASHIELD GUARD >>>"
GUARD_END = "# <<< MAYASHIELD GUARD <<<"
GUARD_BLOCK = """# >>> MAYASHIELD GUARD >>>
# Written by `mayashield -immunize`. Scans startup files if the plug-in failed to load.
try:
    import maya.utils as _ms_utils
    def _ms_guard():
        try:
            from mayashield_core import guard
            guard.startup_check()
        except ImportError:
            pass
    _ms_utils.executeDeferred(_ms_guard)
except Exception:
    pass
# <<< MAYASHIELD GUARD <<<
"""

# v3.x wrote a block that fails with NameError at startup.  Recognised by hash of
# its exact text (timestamp removed), so it can be replaced but never used as a
# hiding place.
_LEGACY_MARKER = "# MAYASHIELD_IMMUNIZED"
_LEGACY_ANCHOR = "try:\n    cmds.evalDeferred(mayashield_startup_guard)\nexcept Exception:\n    pass\n"
_LEGACY_SHA256 = "a181b4ccc4b30d0c66bee5fa0216c412bb56b66026dc25a9ca8c89ea45000c55"


def strip_own_blocks(text):
    """Remove MayaShield's own exact blocks.  Returns (text, found legacy block)."""
    normalized = text.replace("\r\n", "\n")
    if GUARD_BLOCK in normalized:
        normalized = normalized.replace(GUARD_BLOCK, "")
    legacy = False
    start = normalized.find(_LEGACY_MARKER)
    if start >= 0:
        end = normalized.find(_LEGACY_ANCHOR, start)
        if end >= 0:
            end += len(_LEGACY_ANCHOR)
            block = normalized[start:end]
            stamped = re.sub(r"^# Auto-generated at .*$", "# Auto-generated at", block, flags=re.M).strip()
            if hashlib.sha256(stamped.encode("utf-8")).hexdigest() == _LEGACY_SHA256:
                normalized = normalized[:start] + normalized[end:]
                legacy = True
    return normalized, legacy


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bytes(path, limit=_MAX_FILE_BYTES):
    with open(path, "rb") as handle:
        return handle.read(limit)


def _decode(data):
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


# ---------------------------------------------------------------------------
# Where to look
# ---------------------------------------------------------------------------
def _script_dirs(env):
    app = env.get("app_dir") or ""
    folders = []
    if app:
        folders.extend([os.path.join(app, "scripts"), os.path.join(app, "plug-ins")])
        for version in mayapaths.version_dirs(app):
            folders.extend([os.path.join(version, "scripts"), os.path.join(version, "plug-ins"),
                            os.path.join(version, "prefs", "scripts")])
            for locale in mayapaths.locale_dirs(version):
                folders.extend([os.path.join(locale, "scripts"), os.path.join(locale, "prefs", "scripts")])
    return folders


def _prefs_files(env):
    app = env.get("app_dir") or ""
    files = []
    if not app:
        return files
    for version in mayapaths.version_dirs(app):
        files.append(os.path.join(version, "Maya.env"))
        prefs = [os.path.join(version, "prefs")]
        prefs.extend(os.path.join(locale, "prefs") for locale in mayapaths.locale_dirs(version))
        for folder in prefs:
            files.extend([os.path.join(folder, "userPrefs.mel"), os.path.join(folder, "pluginPrefs.mel")])
    return files


def _module_files(env):
    app = env.get("app_dir") or ""
    folders = [os.path.join(app, "modules")] if app else []
    folders.extend(os.path.join(version, "modules") for version in mayapaths.version_dirs(app) if app)
    files = []
    for folder in folders:
        try:
            files.extend(os.path.join(folder, name) for name in os.listdir(folder) if name.lower().endswith(".mod"))
        except OSError:
            pass
    return files


def _l10n_files(env):
    location = env.get("maya_location") or ""
    if not location:
        return []
    root = os.path.join(location, "resources", "l10n")
    files = []
    try:
        for language in os.listdir(root):
            for folder in _L10N_FOLDERS:
                path = os.path.join(root, language, folder, _L10N_INFECTED)
                if os.path.isfile(path):
                    files.append(path)
    except OSError:
        pass
    return files


def _classify(path):
    name = os.path.basename(path).lower()
    if name in _STARTUP_NAMES or name.endswith(".mod") or name.endswith(".pres.mel"):
        return analyzer.CONTEXT_STARTUP
    return analyzer.CONTEXT_TOOL


def candidate_files(env, scan_all=False):
    """Files to inspect, each once.  Always includes what Maya runs at startup."""
    seen = {}

    def add(path):
        normalized = os.path.normcase(os.path.normpath(path))
        if normalized not in seen and os.path.isfile(path):
            seen[normalized] = os.path.normpath(path)

    script_dirs = _script_dirs(env)
    # What Maya itself would execute: every userSetup on these search paths.
    search = list(script_dirs) + list(env.get("sys_path") or ()) + list(env.get("script_path") or ())
    for folder in search:
        for name in ("userSetup.py", "userSetup.mel"):
            add(os.path.join(folder, name))
        for name in rules.SUSPICIOUS_FILENAMES:
            add(os.path.join(folder, name))
        cache = os.path.join(folder, "__pycache__")
        try:
            for name in os.listdir(cache):
                if rules.SUSPICIOUS_PYCACHE_RE.match(name):
                    add(os.path.join(cache, name))
        except OSError:
            pass
    for path in _prefs_files(env) + _module_files(env) + _l10n_files(env):
        add(path)

    truncated = False
    if scan_all:
        for root in script_dirs:
            if not os.path.isdir(root):
                continue
            base_depth = root.rstrip("\\/").count(os.sep)
            for folder, subfolders, names in os.walk(root):
                if folder.count(os.sep) - base_depth >= _MAX_DEPTH:
                    subfolders[:] = []
                subfolders[:] = [item for item in subfolders if item.lower() not in _SKIP_FOLDERS]
                for name in names:
                    if name.lower().endswith(_SCAN_EXTENSIONS):
                        add(os.path.join(folder, name))
                if len(seen) >= _MAX_FILES:
                    truncated = True
                    break
            if truncated:
                break
    return list(seen.values()), truncated


# ---------------------------------------------------------------------------
# Inspecting one file
# ---------------------------------------------------------------------------
def _env_evidence(text, path):
    items = []
    is_mod = path.lower().endswith(".mod")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        if is_mod:
            value = line.split(":=", 1)[-1] if ":=" in line else line.rsplit(None, 1)[-1]
            key = line
        else:
            if "=" not in line:
                continue
            key, value = [piece.strip() for piece in line.split("=", 1)]
            if key.upper() == "PYTHONSTARTUP":
                items.append({"rule": "env_python_startup", "category": "persistence", "weight": 45,
                              "detail": line[:160]})
            if key.upper() not in rules.ENV_PATH_KEYS:
                continue
        if rules.SUSPICIOUS_PATH_RE.search(value.replace("/", "\\")):
            items.append({"rule": "env_suspicious_path", "category": "persistence", "weight": 55,
                          "detail": "Startup path in a throwaway folder: %s" % line[:160]})
    return items


def _manifest_verdict(path):
    """For MayaShield's own files.  Returns None if this isn't one of them."""
    package = os.path.dirname(os.path.abspath(__file__))
    target = os.path.abspath(path)
    if os.path.normcase(os.path.dirname(target)) != os.path.normcase(package):
        return None
    manifest_path = os.path.join(package, "manifest.json")
    if not os.path.isfile(manifest_path):
        return "own"
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        expected = manifest.get("files", {}).get(os.path.basename(target))
    except (IOError, OSError, ValueError):
        return "own"
    if expected is None:
        return "foreign"
    return "own" if sha256_file(target) == expected else "tampered"


def inspect_file(path):
    """Analyse one file on disk.  Returns an analysis dict plus context/notes."""
    name = os.path.basename(path)
    context = _classify(path)
    notes = []
    results = []
    lowered = name.lower()

    verdict = _manifest_verdict(path)
    if verdict == "own":
        return {"score": 0, "evidence": [], "families": [], "context": context, "notes": notes}
    if verdict in ("tampered", "foreign"):
        results.append({"score": 90, "families": [], "evidence": [
            {"rule": "self_tampered", "category": "family", "weight": 90,
             "detail": "This MayaShield file does not match its signed manifest"}]})

    family = rules.SUSPICIOUS_FILENAMES.get(lowered)
    if family is None and rules.SUSPICIOUS_PYCACHE_RE.match(name):
        family = "Vaccine (PuTianTongQi)" if lowered.startswith("vaccine") else "maya_secure_system"
    if family:
        results.append({"score": 100, "families": [family], "evidence": [
            {"rule": "known_bad_filename", "category": "family", "weight": 100,
             "detail": "File name used by %s" % family}]})

    try:
        data = _read_bytes(path, limit=_MAX_STARTUP_BYTES if context == analyzer.CONTEXT_STARTUP else _MAX_FILE_BYTES)
        if context == analyzer.CONTEXT_STARTUP and os.path.getsize(path) > _OVERSIZED_STARTUP_BYTES:
            results.append({"score": 50, "families": [], "evidence": [
                {"rule": "oversized_startup_file", "category": "obfuscation", "weight": 50,
                 "detail": "%d MB startup file: padding is a way to hide from scanners"
                           % (os.path.getsize(path) // (1024 * 1024))}]})
    except (IOError, OSError) as error:
        notes.append("Could not read %s: %s" % (path, error))
        data = b""

    if lowered.endswith(".pyc"):
        match = rules.FAMILY_BYTES_RE.search(data)
        if match:
            results.append({"score": 90, "families": [], "evidence": [
                {"rule": "pyc_family_string", "category": "family", "weight": 90,
                 "detail": "Compiled file contains '%s'" % match.group(0).decode("latin-1")[:60]}]})
    elif data:
        text, legacy = strip_own_blocks(_decode(data))
        if legacy:
            notes.append("%s still has the v3.x immunize block, which fails at startup. "
                         "Run `mayashield -immunize` to replace it." % path)
        if lowered == "maya.env" or lowered.endswith(".mod"):
            evidence = _env_evidence(text, path)
            if evidence:
                results.append({"score": 0, "families": [], "evidence": evidence})
        else:
            language = "python" if lowered.endswith(".py") else "mel"
            if lowered.endswith(".pres.mel"):
                # Stock Maya file full of UI strings: only family strings count.
                results.append(_family_only(text))
            elif lowered in _PREFS_NAMES:
                results.append(_analyze_prefs(data))
            else:
                results.append(analyzer.analyze_text(text, lang=language, context=context))

    combined = analyzer.merge_analyses(results) if results else {"score": 0, "evidence": [], "families": []}
    combined["context"] = context
    combined["notes"] = notes
    return combined


def _family_only(text):
    collector = analyzer._Collector()
    analyzer._sweep_family(text, collector)
    return analyzer._finish(collector, None)


def _analyze_prefs(data):
    """Maya's own prefs files: values in optionVar are data, everything else is code.

    A stock userPrefs.mel lists "system", "fopen" and "fprint" in its Trust Center
    optionVar and userSetup.py paths in its hash optionVar, so reading the file as
    one script reports normal preferences as a virus.  Statements are judged
    individually instead.  Maya never writes calls that run code at the top level
    of these files, so one that does counts heavily.
    """
    results = [_family_only(_decode(data))]
    for statement in iter_statements(data):
        if statement.command == b"optionVar":
            continue
        result = analyzer.analyze_text(_decode(data[statement.start:statement.end]), lang="mel",
                                       context=analyzer.CONTEXT_STARTUP)
        if not result["evidence"]:
            continue
        results.append(result)
        if any(item["category"] in _PREFS_ACTIVE_CATEGORIES for item in result["evidence"]):
            results.append({"score": 60, "families": [], "evidence": [
                {"rule": "prefs_runs_code", "category": "persistence", "weight": 60,
                 "detail": "Preferences file runs code: %s" % statement.command.decode("latin-1")[:60]}]})
    return analyzer.merge_analyses(results)


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------
def scan_disk(env=None, scan_all=False, verbose=False):
    """Scan persistence points.  Hits are medium risk or above."""
    env = env or mayapaths.snapshot()
    report = {"time": now(), "paths": env, "scanAll": bool(scan_all), "hits": [], "notes": [], "filesScanned": 0}
    files, truncated = candidate_files(env, scan_all=scan_all)
    if truncated:
        report["notes"].append("Stopped after %d files; narrow the search paths for a full scan." % _MAX_FILES)
    if not files:
        report["notes"].append("No scan candidates found.")
        return report
    for path in files:
        report["filesScanned"] += 1
        result = inspect_file(path)
        report["notes"].extend(result.get("notes", ()))
        if result["score"] >= rules.RISK_MEDIUM:
            hit = {
                "path": path,
                "reasons": [item["rule"] for item in result["evidence"][:6]],
                "score": result["score"],
                "confidence": rules.risk_band(result["score"]),
                "families": result["families"],
                "context": result["context"],
                "evidence": result["evidence"][:12],
            }
            try:
                hit["sha256"] = sha256_file(path)
            except (IOError, OSError):
                hit["sha256"] = None
            report["hits"].append(hit)
        elif verbose and result["score"] >= rules.RISK_LOW:
            report["notes"].append("Low risk, not flagged: %s (score %d)" % (path, result["score"]))
    report["hits"].sort(key=lambda item: -item["score"])
    return report


# ---------------------------------------------------------------------------
# Quarantine and repair
# ---------------------------------------------------------------------------
def quarantine_dir(app_dir=None):
    """Pass app_dir when off the main thread: finding it may otherwise call maya.cmds."""
    return os.path.join(app_dir or mayapaths.app_dir(), "mayashield", "quarantine")


def _manifest_path(folder):
    return os.path.join(folder, "manifest.json")


def _load_manifest(folder):
    try:
        with open(_manifest_path(folder), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, OSError, ValueError):
        return []


def _save_manifest(folder, records):
    temporary = _manifest_path(folder) + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=2, ensure_ascii=False)
    os.replace(temporary, _manifest_path(folder))


def _store_copy(path, reasons, folder, record_action):
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stored = os.path.join(folder, "%s.%s.quarantine" % (os.path.basename(path), stamp))
    shutil.copy2(path, stored)
    records = _load_manifest(folder)
    record = {"id": stamp, "original": path, "stored": stored, "sha256": sha256_file(stored),
              "time": now(), "reasons": list(reasons), "action": record_action}
    records.append(record)
    _save_manifest(folder, records)
    return record


def repaired_content(path, data):
    """Bytes with the infection removed, or None if it can't be separated safely.

    Works on bytes: a GBK userSetup with a coding cookie has to come back exactly
    as it was, apart from the lines that are gone.
    """
    name = os.path.basename(path).lower()
    if name.endswith(".mel"):
        position = data.find(MEL_INFECTION_HEADER)
        if position >= 0:
            # Published cleanup: the header and everything after it is the virus.
            return data[:position].rstrip() + b"\n" if data[:position].strip() else b""
        return None
    if name == "usersetup.py":
        lines = data.splitlines(True)
        kept = [line for line in lines if not _PY_FAMILY_LINE_RE.match(line.decode("latin-1"))]
        if len(kept) == len(lines):
            return None
        result = b"".join(kept)
        try:
            compile(result, path, "exec")
        except (SyntaxError, ValueError):
            return None
        return result
    return None


def clean_disk(env=None, dry_run=False, scan_all=False, threshold=rules.RISK_HIGH, verbose=False):
    """Repair or quarantine persistence hits at or above the threshold.

    A startup file with an identifiable infection is repaired in place so the
    studio's own setup keeps working; the original always goes to quarantine
    first.  Files under the Maya install are never moved, only repaired.
    """
    env = env or mayapaths.snapshot()
    report = scan_disk(env=env, scan_all=scan_all, verbose=verbose)
    report["dryRun"] = bool(dry_run)
    report["actions"] = []
    folder = quarantine_dir(env.get("app_dir"))
    below = [hit for hit in report["hits"] if hit["score"] < threshold]
    if below:
        report["notes"].append("%d medium-risk file(s) left in place for review." % len(below))

    for hit in report["hits"]:
        if hit["score"] < threshold:
            continue
        path = hit["path"]
        action = {"src": path, "dst": "", "reasons": hit["reasons"], "sha256": hit.get("sha256"), "status": ""}
        report["actions"].append(action)
        try:
            data = _read_bytes(path, limit=64 * 1024 * 1024)
            repaired = repaired_content(path, data)
            if repaired is not None and analyzer.analyze_text(
                    _decode(repaired), lang="python" if path.lower().endswith(".py") else "mel",
                    context=analyzer.CONTEXT_STARTUP)["score"] >= threshold:
                repaired = None
            must_stay = path.lower().endswith(".pres.mel")

            if dry_run:
                action["status"] = "would_repair" if repaired is not None else (
                    "would_report" if must_stay else "would_quarantine")
                continue
            if repaired is None and must_stay:
                action["status"] = "needs manual repair"
                continue

            record = _store_copy(path, hit["reasons"], folder, "repaired" if repaired is not None else "moved")
            action["dst"] = record["stored"]
            if repaired is not None:
                temporary = path + ".mayashield_tmp"
                with open(temporary, "wb") as handle:
                    handle.write(repaired)
                os.replace(temporary, path)
                action["status"] = "repaired"
                _note_repair(folder, record["id"], hashlib.sha256(repaired).hexdigest())
            else:
                os.remove(path)
                action["status"] = "quarantined"
        except PermissionError as error:
            action["status"] = "needs administrator rights: %s" % error
        except (IOError, OSError) as error:
            action["status"] = "failed: %s" % error
    return report


def _note_repair(folder, record_id, repaired_sha256):
    """Remember what a repair produced, so restore can tell if the file changed since."""
    records = _load_manifest(folder)
    for record in records:
        if record.get("id") == record_id:
            record["repaired_sha256"] = repaired_sha256
    _save_manifest(folder, records)


def list_quarantine():
    return _load_manifest(quarantine_dir())


def restore(record_id):
    """Put a quarantined file back.  Never overwrites work done since it was cleaned."""
    folder = quarantine_dir()
    records = _load_manifest(folder)
    for record in records:
        if record.get("id") != record_id:
            continue
        original, stored = record["original"], record["stored"]
        try:
            if sha256_file(stored) != record.get("sha256"):
                return {"status": "failed", "reason": "%s has changed since it was quarantined." % stored}
            if os.path.exists(original):
                expected = record.get("repaired_sha256")
                if expected is None or sha256_file(original) != expected:
                    return {"status": "failed", "reason": "%s has changed since it was cleaned. "
                                                          "The quarantined copy is at %s." % (original, stored)}
            os.makedirs(os.path.dirname(original), exist_ok=True)
            temporary = original + ".mayashield_tmp"
            shutil.copy2(stored, temporary)
            os.replace(temporary, original)
            os.remove(stored)
        except (IOError, OSError) as error:
            return {"status": "failed", "reason": str(error)}
        records.remove(record)
        _save_manifest(folder, records)
        return {"status": "restored", "path": original}
    return {"status": "failed", "reason": "No quarantine record with id %s." % record_id}


def write_guard_block(script_dir):
    """Add or refresh the immunize block in userSetup.py, replacing any v3 block.

    Everything else in the file keeps its exact bytes and line endings, and the
    previous version is kept beside it.
    """
    path = os.path.join(script_dir, "userSetup.py")
    data = _read_bytes(path, limit=_MAX_STARTUP_BYTES) if os.path.isfile(path) else b""
    # Latin-1 maps every byte to one character, so nothing gets re-encoded.
    existing = data.decode("latin-1")
    crlf = "\r\n" in existing
    normalized = existing.replace("\r\n", "\n")
    cleaned, legacy = strip_own_blocks(normalized)
    if GUARD_BLOCK in normalized and not legacy:
        return {"status": "already_immunized", "userSetup.py": path}
    body = cleaned.rstrip("\n")
    content = (body + "\n\n" if body else "") + GUARD_BLOCK
    if crlf:
        content = content.replace("\n", "\r\n")

    os.makedirs(script_dir, exist_ok=True)
    result = {"status": "replaced_v3_block" if legacy else ("appended" if data else "created"), "userSetup.py": path}
    if data:
        backup = path + ".mayashield_bak"
        index = 1
        while os.path.exists(backup):
            backup = "%s.mayashield_bak%d" % (path, index)
            index += 1
        shutil.copy2(path, backup)
        result["backup"] = backup
    temporary = path + ".mayashield_tmp"
    with open(temporary, "wb") as handle:
        handle.write(content.encode("latin-1"))
    os.replace(temporary, path)
    return result

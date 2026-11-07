# -*- coding: utf-8 -*-
"""End-to-end test inside a real Maya.  Run with mayapy from a clean folder:

    mayapy tests/maya_integration_test.py

Covers what the plain engine tests can't: real Maya files, plug-in load and unload
under API 2.0, the commands, refusing a file before it opens, and whether files
cleaned without Maya really open in Maya with their content intact.
"""
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WORK = tempfile.mkdtemp(prefix="mayashield_maya_test_")
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), str(detail)))
    print("%s  %s  %s" % ("PASS" if condition else "FAIL", name, detail if not condition else ""), flush=True)


started = time.time()
import maya.standalone  # noqa: E402
maya.standalone.initialize(name="python")
print("standalone start: %.1fs" % (time.time() - started), flush=True)

import maya.cmds as cmds  # noqa: E402
import maya.mel as mel  # noqa: E402

INERT_MARKER = os.path.join(WORK, "script_node_ran.txt").replace("\\", "/")
PAYLOAD = "open(r'%s','a').write('ran')\n# vaccine_gene marker\n" % INERT_MARKER


def save(path, kind):
    path = path.replace("\\", "/")
    cmds.file(rename=path)
    cmds.file(save=True, type=kind, force=True)
    return path


def build_scene(infected):
    cmds.file(new=True, force=True)
    cube = cmds.polyCube(name="hero_geo")[0]
    cmds.setAttr(cube + ".translateX", 3.5)
    cmds.polySphere(name="vaccine_bottle_geo")
    cmds.expression(name="wobble_expr", string="hero_geo.ty = sin(time);")
    cmds.addAttr(cube, longName="artist_note", dataType="string")
    cmds.setAttr(cube + ".artist_note", u"中文备注 note", type="string")
    shader = cmds.shadingNode("lambert", asShader=True, name="hero_mtl")
    cmds.setAttr(shader + ".color", 0.2, 0.4, 0.6, type="double3")
    if infected:
        cmds.scriptNode(name="vaccine_gene", beforeScript=PAYLOAD, scriptType=1, sourceType="python")
        holder = cmds.createNode("network", name="codeChunk0")
        cmds.addAttr(holder, longName="code", dataType="string")
        cmds.setAttr(holder + ".code", "import maya_secure_system", type="string")


def snapshot():
    return {
        "hero_tx": cmds.getAttr("hero_geo.translateX"),
        "note": cmds.getAttr("hero_geo.artist_note"),
        "expr": cmds.objExists("wobble_expr"),
        "expr_drives": bool(cmds.listConnections("hero_geo.ty", source=True, destination=False)),
        "sphere": cmds.objExists("vaccine_bottle_geo"),
        "mtl_color": cmds.getAttr("hero_mtl.color")[0],
        "vaccine_gene": cmds.objExists("vaccine_gene"),
        "codeChunk0": cmds.objExists("codeChunk0"),
    }


# ---------------------------------------------------------------- plug-in loads
plugin = os.path.join(ROOT, "mayashield.py").replace("\\", "/")
loaded = cmds.loadPlugin(plugin)
check("plugin loads under API 2.0", loaded == ["mayashield"], loaded)
check("plugin version is 4.0.0", cmds.pluginInfo("mayashield", q=True, version=True) == "4.0.0",
      cmds.pluginInfo("mayashield", q=True, version=True))
check("command is registered", "mayashield" in (cmds.pluginInfo("mayashield", q=True, command=True) or []))
check("-version returns", mel.eval("mayashield -version;") == ["4.0.0"], mel.eval("mayashield -version;"))

from mayashield_core import cleaner, guard, live, scanner  # noqa: E402

# ---------------------------------------------------------------- real saved files
build_scene(False)
baseline_ma = save(os.path.join(WORK, "baseline.ma"), "mayaAscii")
baseline_mb = save(os.path.join(WORK, "baseline.mb"), "mayaBinary")
expected = snapshot()

build_scene(True)
infected_ma = save(os.path.join(WORK, "infected.ma"), "mayaAscii")
infected_mb = save(os.path.join(WORK, "infected.mb"), "mayaBinary")
cmds.file(new=True, force=True)

for path in (baseline_ma, baseline_mb):
    report = scanner.scan_file(path)
    check("clean scene has no hits: %s" % os.path.basename(path), not report["hits"] and report["parse_ok"], report["hits"][:2])

for path in (infected_ma, infected_mb):
    report = scanner.scan_file(path)
    nodes = [hit.get("node") for hit in report["hits"]]
    check("infection found statically: %s" % os.path.basename(path),
          report["risk"] == "high" and "vaccine_gene" in nodes and "codeChunk0" in nodes, nodes)
    check("static scan ran without Maya opening the file", not os.path.exists(INERT_MARKER))
    check("user mesh with similar name not flagged: %s" % os.path.basename(path), "vaccine_bottle_geo" not in nodes)

# ---------------------------------------------------------------- cleaned files open
for path in (infected_ma, infected_mb):
    stem = os.path.basename(path)
    report = cleaner.clean_file(path)
    check("clean succeeded: %s" % stem, report["cleaned"] and report["remaining_hits_count"] == 0, report["note"])
    rescan = scanner.scan_file(report["output_path"])
    check("rescan is clean: %s" % stem, rescan["score"] < 40 and rescan["parse_ok"], rescan["hits"][:2])
    cmds.file(new=True, force=True)
    cmds.file(report["output_path"].replace("\\", "/"), open=True, force=True, prompt=False)
    state = snapshot()
    check("Maya opens cleaned %s; scene intact" % stem, state == expected, (state, expected))
    check("script node did not run from cleaned %s" % stem, not os.path.exists(INERT_MARKER))

# ---------------------------------------------------------------- cleaning a .mb with references
ref_target = baseline_ma
cmds.file(new=True, force=True)
build_scene(True)
cmds.file(ref_target, reference=True, namespace="R")
with_ref = save(os.path.join(WORK, "with_ref.mb"), "mayaBinary")
cleaned_ref = cleaner.clean_file(with_ref)
cmds.file(new=True, force=True)
cmds.file(cleaned_ref["output_path"].replace("\\", "/"), open=True, force=True, prompt=False)
check("reference survives a binary clean", bool(cmds.file(q=True, reference=True)), cmds.file(q=True, reference=True))
check("referenced content is still loaded", cmds.objExists("R:hero_geo"))
check("infection is gone with reference present", not cmds.objExists("vaccine_gene"))

# ---------------------------------------------------------------- guard refuses before open
cmds.file(new=True, force=True)
answers = []


def answer(choice):
    def fake(_title, _message, _buttons, _default, _cancel):
        answers.append(choice)
        return choice
    return fake


# Guard's dialogs only appear outside batch mode; simulate an interactive session.
guard._batch = lambda: False
original_ask = guard._ask
guard._ask = answer("Block")
guard._verdicts.clear()
before = len(answers)
cmds.file(infected_ma, open=True, force=True, prompt=False)
check("dialog shown for dangerous scene", len(answers) == before + 1)
check("guard refused to open infected .ma", (cmds.file(q=True, sceneName=True) or "") == "", cmds.file(q=True, sceneName=True))
check("payload did not run after block", not os.path.exists(INERT_MARKER))

guard._verdicts.clear()
cmds.file(infected_mb, open=True, force=True, prompt=False)
check("guard refused to open infected .mb", (cmds.file(q=True, sceneName=True) or "") == "")
check("payload did not run after .mb block", not os.path.exists(INERT_MARKER))

guard._verdicts.clear()
cmds.file(new=True, force=True)
cmds.file(infected_ma, reference=True, namespace="X")
check("guard refused infected reference", not cmds.ls("X:*"), cmds.ls("X:*")[:3])
guard._verdicts.clear()
cmds.file(infected_mb, i=True, namespace="Y")
check("guard refused infected import", not cmds.ls("Y:*"), cmds.ls("Y:*")[:3])

# A file the scanner can't vouch for must not pass as clean.
with open(infected_mb, "rb") as handle:
    binary = handle.read()
broken = os.path.join(WORK, "broken.mb").replace("\\", "/")
with open(broken, "wb") as handle:
    handle.write(binary[:len(binary) // 2])
guard._verdicts.clear()
cmds.file(new=True, force=True)
count = len(answers)
cmds.file(broken, open=True, force=True, prompt=False)
check("unverifiable file asks first", len(answers) == count + 1)
check("unverifiable file was refused", (cmds.file(q=True, sceneName=True) or "") == "")

# A scene under another name still gets opened by Maya if told its type.
renamed = os.path.join(WORK, "renamed.dat").replace("\\", "/")
shutil.copy2(infected_ma, renamed)
guard._verdicts.clear()
cmds.file(new=True, force=True)
count = len(answers)
cmds.file(renamed, open=True, force=True, prompt=False, type="mayaAscii")
check("renamed scene is caught by its content", len(answers) == count + 1)
check("renamed scene was refused", (cmds.file(q=True, sceneName=True) or "") == "" and not os.path.exists(INERT_MARKER))

# Importing a .mel file runs it.
mel_tool = os.path.join(WORK, "tool.mel").replace("\\", "/")
mel_marker = os.path.join(WORK, "mel_ran.txt").replace("\\", "/")
with open(mel_tool, "w") as handle:
    handle.write('python("open(r\'%s\',\'a\').write(\'ran\')  # vaccine_gene");\n' % mel_marker)
guard._verdicts.clear()
count = len(answers)
try:
    cmds.file(mel_tool, i=True, type="mel")
except RuntimeError as error:
    print("mel import raised: %s" % str(error)[:120], flush=True)
check("dangerous .mel import asks first", len(answers) == count + 1)
check("dangerous .mel import did not run", not os.path.exists(mel_marker))

# Other imports aren't ours to judge, and must not be touched.
other = os.path.join(WORK, "shape.obj").replace("\\", "/")
with open(other, "w") as handle:
    handle.write("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
count = len(answers)
check("non-scene file passes straight through", guard._decide(other, "import") is True and len(answers) == count)

count = len(answers)
guard._verdicts.clear()
cmds.file(baseline_ma, open=True, force=True, prompt=False)
check("clean scene opens with no dialog", len(answers) == count and cmds.objExists("hero_geo"))

guard._ask = answer("Open Anyway")
guard._verdicts.clear()
cmds.file(new=True, force=True)
cmds.file(infected_ma, open=True, force=True, prompt=False, executeScriptNodes=False)
check("Open Anyway lets the user through", cmds.objExists("vaccine_gene"))

# ---------------------------------------------------------------- live scene
hits = {hit["node"]: hit for hit in live.scene_hits()}
check("live scan finds script node", "vaccine_gene" in hits, list(hits))
check("live scan finds chunk node", "codeChunk0" in hits, list(hits))
check("live scan ignores user mesh", "vaccine_bottle_geo" not in hits)

dry = live.clean_scene(dry_run=True)
check("dry run deletes nothing", cmds.objExists("vaccine_gene") and any(i["status"] == "would_delete" for i in dry["deleted"]))
live.clean_scene()
check("clean_scene removes threats", not cmds.objExists("vaccine_gene") and not cmds.objExists("codeChunk0"))
check("clean_scene keeps user geometry", cmds.objExists("hero_geo") and cmds.objExists("vaccine_bottle_geo"))

# ---------------------------------------------------------------- jobs and runtime
# scriptJob doesn't work in batch: real jobs are tested in maya_gui_test.py.
saved_list = live.list_script_jobs
live.list_script_jobs = lambda: ['4321: event=timeChanged "import vaccine"', '4322: event=idle "myTool.update()"']
hits = live.job_hits()
check("suspicious scriptJob description found", [hit.get("id") for hit in hits] == [4321], hits[:2])
live.list_script_jobs = saved_list
sys.modules["vaccine"] = type(sys)("vaccine")
check("running family module detected", any(hit["name"] == "vaccine" for hit in live.runtime_hits()))
live.clean_jobs()
check("running family module removed", "vaccine" not in sys.modules)

# ---------------------------------------------------------------- commands
report_path = os.path.join(WORK, "cmd_report.json").replace("\\", "/")
mel.eval('mayashield -scanFile "%s" -report "%s";' % (infected_mb, report_path))
with open(report_path, encoding="utf-8") as handle:
    check("-scanFile writes a report", json.load(handle)["risk"] == "high")
mel.eval('mayashield -scan -report "%s";' % report_path)
mel.eval('mayashield -scanAll -verbose;')
mel.eval('mayashield -clean -dryRun;')
folder = os.path.join(WORK, "cmdfolder")
os.makedirs(folder)
for name in ("a.ma", "b.ma", "c.ma"):
    shutil.copy2(infected_ma if name == "b.ma" else baseline_ma, os.path.join(folder, name))
mel.eval('mayashield -scanFolder "%s" -report "%s";' % (folder.replace("\\", "/"), report_path))
with open(report_path, encoding="utf-8") as handle:
    check("-scanFolder uses workers from mayapy", json.load(handle)["summary"] == {"files": 3, "flagged": 1})
mel.eval('mayashield -offlineCleanFolder "%s" -report "%s";' % (folder.replace("\\", "/"), report_path))
with open(report_path, encoding="utf-8") as handle:
    check("-offlineCleanFolder cleans", json.load(handle)["summary"]["cleaned"] == 1)
mel.eval('mayashield;')

# ---------------------------------------------------------------- deep scan
from mayashield_core import headless, mayapaths  # noqa: E402
mayapy = mayapaths.find_mayapy()
check("mayapy located", bool(mayapy), mayapy)
started = time.time()
deep = headless.deep_scan(infected_ma, mayapy, timeout_sec=240)
print("deep scan took %.1fs" % (time.time() - started), flush=True)
nodes = [hit.get("node") for hit in deep["hits"]]
check("deep scan finds the node", "vaccine_gene" in nodes, deep["hits"][:2])
check("deep scan sandbox blocked the payload", not os.path.exists(INERT_MARKER))

# ---------------------------------------------------------------- unload
guard._ask = original_ask
cmds.file(new=True, force=True)
cmds.unloadPlugin("mayashield")
check("plugin unloads", not cmds.pluginInfo("mayashield", q=True, loaded=True))
reloaded = cmds.loadPlugin(plugin)
check("plugin reloads", reloaded == ["mayashield"])
cmds.unloadPlugin("mayashield")

failed = [item for item in results if not item[1]]
print("\n%d checks, %d failed" % (len(results), len(failed)), flush=True)
for name, _ok, detail in failed:
    print("  FAILED: %s\n     %s" % (name, detail[:400]), flush=True)
shutil.rmtree(WORK, ignore_errors=True)
sys.stdout.flush()
maya.standalone.uninitialize()
sys.exit(1 if failed else 0)

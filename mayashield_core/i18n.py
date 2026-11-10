# -*- coding: utf-8 -*-
"""Everything the user reads inside Maya, in Chinese and English.

The language follows Maya's own interface language: a Chinese Maya gets Chinese,
anything else gets English.  Reports and rule ids stay in English underneath, so
logs and JSON reports read the same everywhere; this module only turns them
into sentences for people who are not programmers.
"""
import os

_language = None


def _detect():
    forced = os.environ.get("MAYASHIELD_LANG")
    if forced:
        return "zh" if forced.lower().startswith("zh") else "en"
    value = ""
    try:
        import maya.cmds as cmds
        value = cmds.about(uiLanguage=True) or ""
    except Exception:
        value = ""
    if not isinstance(value, str) or not value:
        value = os.environ.get("MAYA_UI_LANGUAGE") or ""
    return "zh" if value.lower().startswith("zh") else "en"


def language():
    """'zh' or 'en'.  First called on Maya's main thread (plug-in load), then cached."""
    global _language
    if _language is None:
        _language = _detect()
    return _language


def set_language(value):
    """For tests, and for anyone who wants to override the detection."""
    global _language
    _language = None if value is None else ("zh" if str(value).lower().startswith("zh") else "en")


def tr(key, *args):
    """The text for a key, formatted with args.  An unknown key comes back as itself."""
    pair = TEXT.get(key)
    if pair is None:
        return key % args if args else key
    text = pair[1] if language() == "zh" else pair[0]
    return text % args if args else text


# ---------------------------------------------------------------------------
# Interface text: key -> (English, Chinese)
# ---------------------------------------------------------------------------
TEXT = {
    # ---- status header
    "status.on": ("Protection is on", u"防护已开启"),
    "status.on.detail": ("Scenes are checked before they open, and Maya's startup files are checked at launch.",
                         u"打开场景前会先检查，Maya 启动时也会检查启动文件。"),
    "status.partial": ("Protection is partly off", u"防护只开了一部分"),
    "status.partial.detail": ("Turn both checks on under Advanced for full protection.",
                              u"在“高级”里把两项检查都打开，才是完整防护。"),
    "status.off": ("Protection is off", u"防护已关闭"),
    "status.off.detail": ("Scenes are not being checked. Turn protection on under Advanced.",
                          u"现在不会检查场景。请在“高级”里重新开启防护。"),

    # ---- main actions
    "checkup": ("Check Now", u"一键体检"),
    "checkup.caption": ("Checks the open scene and the files Maya runs at startup. Nothing is changed.",
                        u"检查当前场景和 Maya 的启动文件。只检查，不会改动任何东西。"),
    "scan.file": ("Check a File...", u"检查文件…"),
    "scan.folder": ("Check a Folder...", u"检查文件夹…"),
    "scan.file.tip": ("Check a scene file without opening it.", u"不打开场景，直接检查一个场景文件。"),
    "scan.folder.tip": ("Check every scene in a folder, including subfolders.", u"检查文件夹（含子文件夹）里的所有场景。"),
    "fix": ("Fix Now", u"立即修复"),
    "fix.files": ("Clean These Files...", u"清理这些文件…"),
    "cancel": ("Cancel", u"取消"),
    "close": ("Close", u"关闭"),
    "advanced": ("Advanced", u"高级"),

    # ---- progress
    "busy.checkup": ("Checking...", u"正在检查…"),
    "busy.scan": ("Checking files...", u"正在检查文件…"),
    "busy.fix": ("Fixing...", u"正在修复…"),
    "busy.clean": ("Cleaning...", u"正在清理…"),
    "busy.cancel": ("Stopping...", u"正在停止…"),
    "busy.other": ("Another task is still running.", u"还有任务在进行，请稍等。"),
    "progress.count": ("%d of %d   %s", u"第 %d 个，共 %d 个   %s"),
    "done.seconds": ("Finished in %.1f s.", u"用时 %.1f 秒。"),

    # ---- result headlines
    "result.idle": ("Click \"Check Now\" to make sure this scene and your Maya are clean.",
                    u"点“一键体检”，确认当前场景和你的 Maya 没有问题。"),
    "result.clean": ("No problems found", u"没有发现问题"),
    "result.clean.detail": ("Checked the open scene, background tasks and %d startup file(s).",
                            u"已检查当前场景、后台任务和 %d 个启动文件。"),
    "result.clean.files": ("Checked %d file(s). Nothing suspicious.", u"已检查 %d 个文件，没有可疑内容。"),
    "result.bad": ("Found %d problem(s)", u"发现 %d 个问题"),
    "result.bad.detail": ("Click \"Fix Now\" to remove them. Anything removed from your startup files is kept in "
                          "quarantine and can be restored.",
                          u"点“立即修复”清除它们。从启动文件里清掉的内容会放进隔离区，可以恢复。"),
    "result.bad.files": ("%d of %d file(s) contain threats", u"%d 个文件有威胁（共检查 %d 个）"),
    "result.bad.files.detail": ("Click \"Clean These Files\" to make clean copies. The originals are kept.",
                                u"点“清理这些文件”生成干净的副本，原文件会保留。"),
    "result.review": ("%d item(s) worth a look", u"有 %d 项需要留意"),
    "result.review.detail": ("These are unusual but not clearly malicious. If you don't recognise them, ask whoever "
                             "made the file. They are left alone unless you turn on \"Also remove medium-risk items\".",
                             u"这些内容不常见，但不能确定是病毒。如果不认识，可以问问文件的制作者。"
                             u"除非在“高级”里勾选“中风险的也清理”，否则不会动它们。"),
    "result.unverified": ("%d file(s) could not be fully checked", u"有 %d 个文件无法完整检查"),
    "result.unverified.detail": ("The file may be damaged or built to confuse scanners. Treat it as unsafe unless you "
                                 "trust where it came from.",
                                 u"文件可能已损坏，也可能是故意做成这样来躲避检查。不确定来源的话，请当作不安全处理。"),
    "result.fixed": ("Fixed %d item(s)", u"已修复 %d 项"),
    "result.fixed.detail": ("Everything found has been dealt with.", u"发现的问题都已处理。"),
    "result.fixed.partial": ("Fixed %d item(s), %d could not be fixed", u"已修复 %d 项，还有 %d 项没能修复"),
    "result.fixed.partial.detail": ("See the list below for what is left.", u"剩下的问题见下面的列表。"),
    "result.canceled": ("Stopped before finishing", u"已停止，没有检查完"),
    "result.error": ("Something went wrong", u"出错了"),
    "advice.save": ("Save the scene now, otherwise the file on disk still contains the problem.",
                    u"请现在保存场景，否则硬盘上的文件里问题还在。"),
    "advice.restart": ("Restart Maya so that nothing already loaded from those files keeps running.",
                       u"请重启 Maya，这样已经从那些文件加载的内容才会彻底停止。"),
    "advice.admin": ("Some files need administrator rights to fix. Start Maya as administrator and try again.",
                     u"有些文件需要管理员权限才能修复。请用管理员身份启动 Maya 后再试。"),

    # ---- results list
    "col.what": ("What", u"问题"),
    "col.where": ("Where", u"位置"),
    "col.level": ("Level", u"程度"),
    "level.high": ("Dangerous", u"危险"),
    "level.medium": ("Worth a look", u"需留意"),
    "level.low": ("Minor", u"轻微"),
    "level.info": ("Info", u"提示"),
    "level.error": ("Not checked", u"未能检查"),
    "where.scene": ("Open scene", u"当前场景"),
    "where.node": ("Node %s", u"节点 %s"),
    "where.job": ("Background task #%s", u"后台任务 #%s"),
    "where.session": ("This Maya session", u"本次 Maya 会话"),
    "where.line": ("line %s", u"第 %s 行"),
    "what.family": ("Known virus: %s", u"已知病毒：%s"),
    "what.job": ("Suspicious background task", u"可疑的后台任务"),
    "what.running": ("A virus is already running in this session", u"病毒已经在本次会话中运行"),
    "what.unverified": ("Could not be fully checked", u"无法完整检查"),
    "what.more": ("...and %d more", u"…还有 %d 项"),
    "menu.expand": ("Expand All", u"全部展开"),
    "menu.collapse": ("Collapse All", u"全部折叠"),
    "menu.reveal": ("Show in Folder", u"在文件夹中显示"),
    "menu.select": ("Select the Node", u"选中这个节点"),
    "menu.copy": ("Copy", u"复制"),

    # ---- fix confirmation
    "fix.title": ("Fix the problems found?", u"修复发现的问题？"),
    "fix.intro": ("MayaShield will:", u"MayaShield 将会："),
    "fix.nodes": ("- delete %d malicious node(s) from the open scene", u"- 从当前场景删除 %d 个恶意节点"),
    "fix.jobs": ("- stop %d suspicious background task(s)", u"- 结束 %d 个可疑的后台任务"),
    "fix.runtime": ("- unload virus code that is running in this session", u"- 卸载本次会话中正在运行的病毒代码"),
    "fix.disk": ("- repair or quarantine %d infected startup file(s) (they can be restored)",
                 u"- 修复或隔离 %d 个被感染的启动文件（可以恢复）"),
    "fix.go": ("Fix", u"修复"),
    "fix.nothing": ("Nothing here can be fixed automatically.", u"这里没有可以自动修复的内容。"),

    # ---- offline clean
    "clean.mode.title": ("How should the files be cleaned?", u"怎样清理这些文件？"),
    "clean.mode.text": ("A clean copy leaves the original untouched. Fixing the original also keeps a backup next to "
                        "it (*.mayashield_bak).",
                        u"生成干净副本不会动原文件。直接修复原文件时，也会在旁边留一份备份（*.mayashield_bak）。"),
    "clean.mode.copy": ("Make a Clean Copy (Recommended)", u"生成干净副本（推荐）"),
    "clean.mode.inplace": ("Fix the Original", u"直接修复原文件"),
    "clean.preview": ("Preview mode is on, so nothing is written. Turn off \"Preview only\" under Advanced to clean.",
                      u"当前是“只预览”模式，不会写入任何文件。要真正清理，请在“高级”里取消“只预览，不修改”。"),
    "clean.pick.file": ("Choose a Maya Scene", u"选择 Maya 场景"),
    "clean.pick.folder": ("Choose a Folder", u"选择文件夹"),
    "clean.pick.output": ("Save the Clean Copy As", u"干净副本保存为"),
    "clean.pick.outdir": ("Choose Where to Put the Clean Copies", u"选择干净副本的存放位置"),
    "clean.filter": ("Maya Scene (*.ma *.mb)", u"Maya 场景 (*.ma *.mb)"),
    "clean.summary": ("Cleaned %d of %d file(s).", u"已清理 %d 个文件（共 %d 个）。"),
    "clean.folder.summary": ("Files: %d, with threats: %d, cleaned: %d, failed: %d",
                             u"文件 %d 个，有威胁 %d 个，已清理 %d 个，失败 %d 个"),
    "clean.output": ("Clean copy: %s", u"干净副本：%s"),
    "clean.backup": ("The original was kept as %s", u"原文件已保留为 %s"),
    "clean.none": ("No files with threats from the last check. Check a file or folder first.",
                   u"上次检查没有发现有威胁的文件。请先检查文件或文件夹。"),

    # ---- advanced
    "adv.protect": ("Protection", u"防护开关"),
    "adv.guard.open": ("Check scenes before they open (recommended)", u"打开场景前先检查（推荐）"),
    "adv.guard.open.tip": ("Scenes, imports and references are checked before Maya reads them, so a virus never "
                           "gets to run.",
                           u"在 Maya 读取场景、导入和引用文件之前先检查，病毒没有机会运行。"),
    "adv.guard.startup": ("Check startup files when Maya starts (recommended)", u"Maya 启动时检查启动文件（推荐）"),
    "adv.guard.startup.tip": ("Looks at userSetup, Maya.env and the other files Maya runs at launch.",
                              u"检查 userSetup、Maya.env 等 Maya 启动时会运行的文件。"),
    "adv.options": ("Cleaning options", u"清理选项"),
    "adv.preview": ("Preview only, change nothing", u"只预览，不修改"),
    "adv.preview.tip": ("The tools below only report what they would do. \"Fix Now\" is not affected.",
                        u"下面的工具只报告会做什么，不真正执行。“立即修复”不受这个选项影响。"),
    "adv.medium": ("Also remove medium-risk items", u"中风险的也清理"),
    "adv.medium.tip": ("Off: only dangerous items are removed; items worth a look are listed for you to decide.",
                       u"不勾选时只清除“危险”项；“需留意”的只列出来，由你决定。"),
    "adv.deep": ("Deep check (slow)", u"深度检查（很慢）"),
    "adv.deep.tip": ("Also opens each scene in a separate, isolated Maya. Much slower and needs a Maya licence.",
                     u"额外在一个隔离的 Maya 里打开每个场景再检查。慢很多，并且需要 Maya 许可证。"),
    "adv.details": ("Show technical details", u"显示技术细节"),
    "adv.details.tip": ("Adds rule names and scores to the results, and shows the log.",
                        u"在结果里显示规则名和分数，并显示日志。"),
    "adv.timeout": ("Give up on a file after (seconds):", u"单个文件最长检查时间（秒）："),
    "adv.report": ("Save a report to:", u"报告保存到："),
    "adv.report.hint": ("Optional, for example C:/temp/mayashield_report.json",
                        u"可不填，例如 C:/temp/mayashield_report.json"),
    "adv.browse": ("Browse...", u"浏览…"),
    "adv.report.pick": ("Save Report As", u"报告保存为"),
    "adv.tools": ("More tools", u"更多工具"),
    "tool.scanall": ("Check All Scripts", u"全面检查脚本目录"),
    "tool.scanall.tip": ("Checks every script in your Maya script folders, not only the startup files. Slower.",
                         u"除了启动文件，还检查 Maya 脚本目录里的所有脚本。比较慢。"),
    "tool.cleandisk": ("Fix Startup Files", u"修复启动文件"),
    "tool.cleandisk.tip": ("Repairs or quarantines infected startup files.", u"修复或隔离被感染的启动文件。"),
    "tool.cleanscene": ("Remove Bad Nodes", u"清除场景里的恶意节点"),
    "tool.cleanscene.tip": ("Deletes dangerous nodes from the scene that is open now.", u"删除当前打开的场景里的危险节点。"),
    "tool.cleanjobs": ("Stop Bad Tasks", u"结束可疑后台任务"),
    "tool.cleanjobs.tip": ("Stops suspicious background tasks (scriptJobs) and unloads running virus code.",
                           u"结束可疑的后台任务（scriptJob），并卸载正在运行的病毒代码。"),
    "tool.immunize": ("Add Startup Backup Check", u"添加启动备用检查"),
    "tool.immunize.tip": ("Adds a few lines to userSetup.py so the startup check still runs if the plug-in ever "
                          "fails to load. Maya may ask you to confirm the changed file once.",
                          u"在 userSetup.py 里加几行代码：万一插件没加载成功，启动检查仍会运行。"
                          u"Maya 可能会让你确认一次这个文件的改动。"),
    "tool.listjobs": ("List Background Tasks", u"查看后台任务"),
    "tool.cleanfile": ("Clean a File...", u"清理文件…"),
    "tool.cleanfolder": ("Clean a Folder...", u"清理文件夹…"),
    "tool.cleanlast": ("Clean Flagged Files", u"清理上次检查出的文件"),
    "tool.cleanlast.count": ("Clean Flagged Files (%d)", u"清理上次检查出的文件（%d）"),
    "tool.quarantine": ("Quarantine...", u"隔离区…"),
    "tool.quarantine.tip": ("Files MayaShield removed are kept here and can be put back.",
                            u"MayaShield 清掉的文件都保存在这里，可以放回原处。"),
    "log.clear": ("Clear Log", u"清空日志"),

    # ---- log lines
    "log.checked": ("%d file(s) checked.", u"检查了 %d 个文件。"),
    "log.report": ("Report saved: %s", u"报告已保存：%s"),
    "log.report.failed": ("Could not save the report: %s", u"报告保存失败：%s"),
    "log.none.disk": ("No suspicious startup files.", u"没有可疑的启动文件。"),
    "log.none.scene": ("No suspicious nodes in the open scene.", u"当前场景里没有可疑节点。"),
    "log.none.jobs": ("No suspicious background tasks.", u"没有可疑的后台任务。"),
    "log.none.files": ("No suspicious scenes.", u"没有可疑的场景。"),
    "log.none.listjobs": ("(none)", u"（无）"),
    "log.immunize.note": ("Maya may ask you to confirm the changed userSetup.py the next time it starts. "
                          "That is expected.",
                          u"下次启动时 Maya 可能会让你确认 userSetup.py 的改动，这是正常的。"),
    "log.restored": ("Restored %s", u"已恢复 %s"),
    "log.reveal": ("Show in folder", u"在文件夹中显示"),
    "log.files": ("Files checked: %d, with problems: %d", u"检查了 %d 个文件，其中 %d 个有问题"),
    "heading.checkup": ("Check", u"体检"),
    "heading.fix": ("Fix", u"修复"),
    "heading.scan": ("Check: %s", u"检查：%s"),
    "heading.scanall": ("Check all scripts", u"全面检查脚本目录"),
    "heading.cleandisk": ("Fix startup files", u"修复启动文件"),
    "heading.cleanscene": ("Remove bad nodes", u"清除恶意节点"),
    "heading.cleanjobs": ("Stop bad tasks", u"结束可疑后台任务"),
    "heading.immunize": ("Startup backup check", u"启动备用检查"),
    "heading.listjobs": ("Background tasks", u"后台任务"),
    "heading.clean": ("Clean: %s", u"清理：%s"),
    "heading.cleanlast": ("Clean %d flagged file(s)", u"清理 %d 个有问题的文件"),
    "heading.preview": (" (preview)", u"（预览）"),

    # ---- quarantine dialog
    "q.title": ("MayaShield Quarantine", u"MayaShield 隔离区"),
    "q.col.original": ("Original location", u"原位置"),
    "q.col.when": ("When", u"时间"),
    "q.col.action": ("What was done", u"处理方式"),
    "q.col.why": ("Why", u"原因"),
    "q.restore": ("Put Back", u"放回原处"),
    "q.empty": ("Nothing is in quarantine.", u"隔离区是空的。"),
    "q.action.repaired": ("Repaired, original kept here", u"已修复，原文件存在这里"),
    "q.action.moved": ("Moved here", u"已移到这里"),

    # ---- statuses coming back from the engine
    "st.repaired": ("Repaired", u"已修复"),
    "st.quarantined": ("Moved to quarantine", u"已移到隔离区"),
    "st.would_repair": ("Would be repaired", u"将会修复"),
    "st.would_quarantine": ("Would be moved to quarantine", u"将会移到隔离区"),
    "st.would_report": ("Would be reported only", u"只会报告"),
    "st.manual": ("Needs manual repair", u"需要手动修复"),
    "st.admin": ("Needs administrator rights", u"需要管理员权限"),
    "st.failed": ("Failed", u"失败"),
    "st.deleted": ("Deleted", u"已删除"),
    "st.would_delete": ("Would be deleted", u"将会删除"),
    "st.killed": ("Stopped", u"已结束"),
    "st.would_kill": ("Would be stopped", u"将会结束"),
    "st.removed": ("Unloaded", u"已卸载"),
    "st.would_remove": ("Would be unloaded", u"将会卸载"),
    "st.attr": ("Attribute removed", u"已清除属性"),
    "st.already": ("Already dealt with", u"已经处理过"),

    # ---- dialogs raised by the guard
    "btn.block": ("Don't Open (Recommended)", u"不打开（推荐）"),
    "btn.clean_copy": ("Open a Clean Copy", u"生成干净副本并打开"),
    "btn.open_anyway": ("Open Anyway (Risky)", u"仍然打开（有风险）"),
    "btn.clean_now": ("Fix Now (Recommended)", u"立即修复（推荐）"),
    "btn.open_panel": ("Show Details", u"查看详情"),
    "btn.ignore": ("Not Now", u"暂不处理"),
    "btn.disable": ("Stop Checking", u"以后不再检查"),
    "guard.block.title": ("MayaShield stopped a dangerous file", u"MayaShield 拦下了一个危险文件"),
    "guard.block.text": ("This file was stopped before it could run anything.\n\n"
                         "File: %s\nWhy: %s\n\n%s\n\n"
                         "\"Open a Clean Copy\" removes the dangerous part, saves the result next to the original "
                         "and opens that instead. The original is not changed.",
                         u"这个文件在运行任何内容之前就被拦下了。\n\n"
                         u"文件：%s\n原因：%s\n\n%s\n\n"
                         u"“生成干净副本并打开”会去掉危险的部分，把结果保存在原文件旁边再打开，原文件不会被改动。"),
    "guard.block.text.plain": ("This file was stopped before it could run anything.\n\nFile: %s\nWhy: %s\n\n%s",
                               u"这个文件在运行任何内容之前就被拦下了。\n\n文件：%s\n原因：%s\n\n%s"),
    "guard.unverified.title": ("MayaShield could not check this file", u"MayaShield 无法检查这个文件"),
    "guard.unverified.text": ("MayaShield could not read this file completely, so it cannot say it is safe. The file "
                              "may be damaged, or built to confuse scanners.\n\nFile: %s\n\n%s\n\n"
                              "Open it only if you trust where it came from.",
                              u"MayaShield 没能完整读懂这个文件，所以不能保证它是安全的。"
                              u"文件可能已损坏，也可能是故意做成这样来躲避检查。\n\n文件：%s\n\n%s\n\n"
                              u"只有在信任文件来源时才打开它。"),
    "guard.review.toast": ("MayaShield: <hl>%s</hl> has something worth a look. Open the MayaShield panel for details.",
                           u"MayaShield：<hl>%s</hl> 里有需要留意的内容，打开 MayaShield 面板可查看详情。"),
    "guard.review.warn": ("MayaShield: %s has something worth a look (score %d). Open the MayaShield panel for details.",
                          u"MayaShield：%s 里有需要留意的内容（分数 %d），打开 MayaShield 面板可查看详情。"),
    "guard.toolarge": ("MayaShield: %s is too large for the check before opening; it will be checked after it opens.",
                       u"MayaShield：%s 太大，打开前不做检查，打开后再检查。"),
    "guard.opened": ("MayaShield: %s was opened at your request, despite the warning.",
                     u"MayaShield：已按你的选择打开 %s（已提示过风险）。"),
    "guard.blocked": ("MayaShield did not open %s.", u"MayaShield 没有打开 %s。"),
    "guard.cleancopy": ("MayaShield: a clean copy was saved as %s", u"MayaShield：干净副本已保存为 %s"),
    "guard.cleancopy.failed": ("MayaShield: could not make a clean copy of %s: %s",
                               u"MayaShield：无法为 %s 生成干净副本：%s"),
    "guard.cleancopy.unsaved": ("MayaShield: the scene you have open has unsaved changes, so the clean copy was not "
                                "opened for you: %s",
                                u"MayaShield：当前场景有未保存的修改，所以没有自动打开干净副本：%s"),
    "guard.batch": ("MayaShield: %s scene %s (score %d). Batch mode does not block it.\n%s",
                    u"MayaShield：%s 场景 %s（分数 %d）。批处理模式下不会拦截。\n%s"),
    "guard.batch.high": ("HIGH RISK", u"高风险"),
    "guard.batch.unverified": ("UNVERIFIED", u"未能验证"),
    "guard.check.failed": ("MayaShield: the check before opening failed, so the file was allowed: %s",
                           u"MayaShield：打开前的检查出错，已放行该文件：%s"),
    "guard.check.refused": ("MayaShield: this file looked dangerous and something went wrong while handling it, "
                            "so it was not opened: %s",
                            u"MayaShield：这个文件看起来有危险，处理过程中又出了错，所以没有打开它：%s"),
    "guard.runtime.title": ("MayaShield found a problem in this scene", u"MayaShield 在这个场景里发现了问题"),
    "guard.runtime.text": ("Something dangerous is active in this Maya session:\n\n%s\n\nFix it now?",
                           u"本次 Maya 会话里有危险内容正在运行：\n\n%s\n\n现在修复吗？"),
    "guard.runtime.batch": ("MayaShield: suspicious content in this session:\n%s",
                            u"MayaShield：本次会话里有可疑内容：\n%s"),
    "guard.runtime.done": ("MayaShield: stopped %d background task(s), deleted %d node(s).",
                           u"MayaShield：已结束 %d 个后台任务，删除 %d 个节点。"),
    "guard.runtime.off": ("MayaShield: scenes are no longer checked. Turn this back on in the MayaShield panel "
                          "under Advanced.",
                          u"MayaShield：已停止检查场景。可以在 MayaShield 面板的“高级”里重新开启。"),
    "guard.runtime.error": ("MayaShield: the check after opening failed: %s", u"MayaShield：打开后的检查出错：%s"),
    "guard.startup.title": ("MayaShield found a problem in your Maya", u"MayaShield 在你的 Maya 里发现了问题"),
    "guard.startup.text": ("Some of the files Maya runs when it starts look infected:\n\n%s\n\n"
                           "\"Fix Now\" repairs them or moves them to quarantine. The originals are kept and can be "
                           "restored from the MayaShield panel.",
                           u"Maya 启动时会运行的一些文件看起来已被感染：\n\n%s\n\n"
                           u"“立即修复”会修复它们，或把它们移到隔离区。原文件会保留，可以在 MayaShield 面板里恢复。"),
    "guard.startup.review": ("MayaShield: %d startup file(s) are worth a look. Open the MayaShield panel for details.",
                             u"MayaShield：有 %d 个启动文件需要留意，打开 MayaShield 面板可查看详情。"),
    "guard.startup.failed": ("MayaShield: the startup check failed: %s", u"MayaShield：启动检查出错：%s"),
    "guard.startup.restart": ("MayaShield: please restart Maya so that nothing already loaded from those files "
                              "keeps running.",
                              u"MayaShield：请重启 Maya，这样已经从那些文件加载的内容才会彻底停止。"),
    "guard.startup.off": ("MayaShield: startup files are no longer checked. Turn this back on in the MayaShield "
                          "panel under Advanced.",
                          u"MayaShield：已停止检查启动文件。可以在 MayaShield 面板的“高级”里重新开启。"),
    "guard.more": ("...and %d more.", u"…还有 %d 项。"),
    "guard.line.node": ("- Node %s: %s", u"- 节点 %s：%s"),
    "guard.line.job": ("- Background task #%s: %s", u"- 后台任务 #%s：%s"),
    "guard.line.item": ("- %s: %s", u"- %s：%s"),

    # ---- menu, shelf, about
    "menu.open": ("Open MayaShield", u"打开 MayaShield"),
    "menu.open.float": ("Open in a Separate Window", u"在独立窗口中打开"),
    "menu.checkup": ("Check Now", u"一键体检"),
    "menu.about": ("About MayaShield...", u"关于 MayaShield…"),
    "shelf.open": ("MayaShield: open the panel", u"MayaShield：打开面板"),
    "shelf.checkup": ("MayaShield: check now", u"MayaShield：一键体检"),
    "shelf.open.label": ("Shield", u"防护"),
    "shelf.checkup.label": ("Check", u"体检"),
    "status.button": ("MayaShield: open the panel", u"MayaShield：打开面板"),
    "about.title": ("About MayaShield", u"关于 MayaShield"),
    "about.text": ("MayaShield %s\n\nProtects Maya from scene viruses: it checks scenes before they open, checks the "
                   "files Maya runs at startup, and removes what it finds.\n\n"
                   "Open the panel from the MayaShield menu and click \"Check Now\".",
                   u"MayaShield %s\n\n保护 Maya 不受场景病毒侵害：打开场景前先检查，启动时检查 Maya 的启动文件，"
                   u"发现问题可以一键清除。\n\n从 MayaShield 菜单打开面板，点“一键体检”即可。"),
    "ok": ("OK", u"确定"),
    "integration.failed": ("MayaShield: could not set up %s: %s", u"MayaShield：%s 设置失败：%s"),
    "ui.failed": ("MayaShield: could not open the panel: %s", u"MayaShield：无法打开面板：%s"),
    "node.missing": ("MayaShield: that node is no longer in the scene: %s", u"MayaShield：场景里已经没有这个节点：%s"),
    "reveal.failed": ("MayaShield: could not show %s: %s", u"MayaShield：无法显示 %s：%s"),
}


# ---------------------------------------------------------------------------
# What each finding means, in plain words: rule id -> (English, Chinese)
# ---------------------------------------------------------------------------
RULE_TEXT = {
    # known families
    "name_family": ("Node named like a known virus", u"节点名和已知病毒一样"),
    "raw_family_string": ("The file contains text used by a known virus", u"文件里有已知病毒使用的文字"),
    "pyc_family_string": ("Compiled script contains text used by a known virus", u"编译后的脚本里有已知病毒使用的文字"),
    "known_bad_filename": ("File name used by a known virus", u"文件名是已知病毒使用的名字"),
    "self_tampered": ("A MayaShield file has been altered", u"MayaShield 自己的文件被改动过"),
    # what the code does
    "persist_write": ("Writes itself into Maya's startup files, so it runs every time Maya starts",
                      u"会把自己写进 Maya 的启动文件，以后每次启动都会运行"),
    "self_replication": ("Copies itself into other scenes", u"会把自己复制到别的场景里"),
    "download_and_run": ("Downloads code from the internet and runs it", u"会从网上下载代码并运行"),
    "auto_run_process": ("Starts a program or goes online as soon as the scene opens",
                         u"一打开场景就会启动别的程序或联网"),
    "fake_crash": ("Shows a fake message and then forces Maya to quit", u"会弹出假提示然后强制退出 Maya"),
    "triggered_quit": ("Forces Maya to quit when triggered", u"在特定条件下强制退出 Maya"),
    "py_exec_decoded": ("Runs hidden code after decoding it", u"会解码并运行隐藏的代码"),
    "decode_chain": ("Hides data behind several layers of encoding", u"用多层编码隐藏内容"),
    "encoded_payload": ("Hides code inside encoded data", u"把代码藏在编码数据里"),
    "hidden_payload": ("Code hidden in an ordinary attribute", u"在普通属性里藏了可执行代码"),
    "official_name_abuse": ("Uses the name of a built-in Maya node to look harmless",
                            u"冒用 Maya 自带节点的名字来伪装"),
    "auto_exec": ("Runs by itself when the scene opens", u"打开场景时会自动运行"),
    "py_process": ("Starts other programs", u"会启动别的程序"),
    "mel_system": ("Starts other programs", u"会启动别的程序"),
    "mel_exec": ("Starts other programs", u"会启动别的程序"),
    "py_ctypes": ("Calls directly into the operating system", u"会直接调用操作系统功能"),
    "py_network": ("Goes online", u"会联网"),
    "py_registry": ("Changes the Windows registry", u"会修改 Windows 注册表"),
    "py_delete": ("Deletes folders", u"会删除文件夹"),
    "force_quit": ("Forces Maya to quit", u"会强制退出 Maya"),
    "mel_sysfile": ("Deletes, renames or copies files", u"会删除、改名或复制文件"),
    "mel_file_write": ("Writes files", u"会写文件"),
    "py_file_write": ("Writes files", u"会写文件"),
    "mel_scriptnode": ("Creates script nodes that carry code", u"会创建带代码的脚本节点"),
    "py_scriptnode": ("Creates script nodes that carry code", u"会创建带代码的脚本节点"),
    "mel_scene_save": ("Saves the scene by itself", u"会自动保存场景"),
    "py_scene_save": ("Saves the scene by itself", u"会自动保存场景"),
    "mel_scriptjob": ("Sets up a background task", u"会注册后台任务"),
    "py_scriptjob": ("Sets up a background task", u"会注册后台任务"),
    "mel_scriptjob_sticky": ("Sets up a background task that cannot be stopped", u"注册了无法关闭的后台任务"),
    "py_scriptjob_sticky": ("Sets up a background task that cannot be stopped", u"注册了无法关闭的后台任务"),
    "mel_infinite_loop": ("Contains a loop that never ends", u"包含死循环"),
    "mel_python": ("Runs Python from MEL", u"在 MEL 里运行 Python"),
    "mel_eval_dynamic": ("Runs code it assembles on the fly", u"会运行临时拼出来的代码"),
    "py_exec_dynamic": ("Runs code it assembles on the fly", u"会运行临时拼出来的代码"),
    "py_mel_eval_dynamic": ("Runs MEL it assembles on the fly", u"会运行临时拼出来的 MEL"),
    "py_dyn_import": ("Loads a module whose name is hidden", u"加载名字被隐藏的模块"),
    "py_evasive_getattr": ("Hides which function it calls", u"故意隐藏它调用的函数"),
    "py_chr_obfuscation": ("Builds its text character by character to hide it", u"用逐字符拼接来隐藏内容"),
    "py_b64": ("Uses encoded data", u"使用了编码数据"),
    "py_zlib": ("Uses compressed data", u"使用了压缩数据"),
    "py_codecs": ("Uses encoded data", u"使用了编码数据"),
    "py_marshal": ("Loads pre-compiled code", u"加载预编译的代码"),
    "py_pickle": ("Loads packed objects that can run code", u"加载可以执行代码的打包数据"),
    "py_too_large": ("Unusually large script", u"脚本异常地大"),
    "py_too_complex": ("Code nested too deeply to read", u"代码嵌套太深，无法分析"),
    "oversized_script": ("Unusually large script", u"脚本异常地大"),
    "many_blobs": ("Many encoded-looking strings", u"有很多像编码数据的字符串"),
    "opaque_blob": ("Large block of unreadable data", u"有大块无法识别的数据"),
    "startup_usersetup": ("Mentions Maya's startup script (userSetup)", u"提到了 Maya 的启动脚本 userSetup"),
    "startup_prefs": ("Mentions Maya's preference files", u"提到了 Maya 的设置文件"),
    "startup_env": ("Mentions Maya.env", u"提到了 Maya.env"),
    "startup_l10n": ("Mentions a Maya file that a known virus overwrites", u"提到了已知病毒会改写的 Maya 文件"),
    "app_dir_probe": ("Looks up your Maya user folder", u"在查找你的 Maya 用户目录"),
    "date_trigger": ("Checks the date (often used to delay an attack)", u"会检查日期（常用于定时发作）"),
    "name_weak": ("Script node with a name viruses often use", u"脚本节点的名字是病毒常用的"),
    "confirm_dialog": ("Shows a dialog", u"会弹出对话框"),
    # file structure
    "ma_unexpected_statement": ("Contains a command Maya never writes into scenes", u"文件里有 Maya 自己不会写入的命令"),
    "ma_code_statement": ("Contains a command that runs code the moment the scene opens",
                          u"文件里有一打开场景就会运行代码的命令"),
    "ma_statement_flood": ("Contains hundreds of commands Maya never writes into scenes",
                           u"文件里有成百上千条 Maya 自己不会写入的命令"),
    "render_callback": ("Runs when a render starts or ends", u"在渲染开始或结束时运行"),
    "particle_expression": ("Runs whenever the particles are simulated", u"在粒子模拟时运行"),
    "ma_inline_code": ("Code embedded where only data belongs", u"在只应该放数据的地方嵌入了代码"),
    "ma_unusual_file_command": ("Unusual file command", u"不寻常的 file 命令"),
    "mb_malformed": ("Damaged or crafted file structure", u"文件结构损坏，或是被特意构造的"),
    "mb_trailing_data": ("Extra data after the end of the scene", u"场景结尾之后还有多余数据"),
    "not_fully_analyzed": ("Could not be fully checked", u"无法完整检查"),
    "anomaly": ("Unusual file structure", u"文件结构异常"),
    "scanException": ("Could not be checked", u"无法检查"),
    "workerFailure": ("Could not be checked", u"无法检查"),
    # startup files
    "env_python_startup": ("Makes Python run a script every time it starts", u"让 Python 每次启动都运行一个脚本"),
    "env_suspicious_path": ("Loads scripts from a temporary folder", u"从临时文件夹加载脚本"),
    "prefs_runs_code": ("Preferences file runs code", u"设置文件里有会运行的代码"),
    "oversized_startup_file": ("Startup file padded to an unusual size", u"启动文件被填充得异常大"),
}

CATEGORY_TEXT = {
    "family": ("Matches a known virus", u"和已知病毒的特征一致"),
    "persistence": ("Tries to run again every time Maya starts", u"试图让自己在每次 Maya 启动时运行"),
    "replication": ("Tries to spread to other scenes", u"试图传播到别的场景"),
    "process": ("Starts other programs", u"会启动别的程序"),
    "network": ("Goes online", u"会联网"),
    "execution": ("Runs code in an unusual way", u"用不寻常的方式运行代码"),
    "obfuscation": ("Hides what it does", u"在隐藏自己的行为"),
    "destructive": ("Can damage your work", u"可能破坏你的工作"),
    "filesystem": ("Changes files", u"会改动文件"),
    "trigger": ("Runs by itself", u"会自动运行"),
    "structure": ("Unusual file structure", u"文件结构异常"),
    "evasion": ("Disguises itself", u"在伪装自己"),
    "name": ("Suspicious name", u"名字可疑"),
}

FAMILY_TEXT = {
    "Vaccine (PuTianTongQi)": ("Vaccine (PuTianTongQi)", u"Vaccine（普天同庆）"),
    "MayaMelUIConfigurationFile": ("Mel UI Configuration", u"Mel UI Configuration（贼健康）"),
    "maya_secure_system": ("maya_secure_system", u"maya_secure_system"),
    "MayaShield self-test sample": ("MayaShield's own test sample (harmless)", u"MayaShield 自己的测试样本（无害）"),
}


def _pick(pair):
    return pair[1] if language() == "zh" else pair[0]


def family_name(name):
    pair = FAMILY_TEXT.get(name)
    return _pick(pair) if pair else name


def explain_rule(rule, category=None):
    """One plain sentence for a rule id.  Falls back to its category, then to the id."""
    pair = RULE_TEXT.get(rule)
    if pair is None and str(rule).startswith("ioc_"):
        pair = CATEGORY_TEXT["family"]
    if pair is None and category:
        pair = CATEGORY_TEXT.get(category)
    return _pick(pair) if pair else str(rule)


def explain_hit(hit, limit=2, named=True):
    """What a hit means, for someone who doesn't read rule names.

    A known family is named.  Otherwise (or with ``named`` off, for the line under a
    heading that already names it) the strongest distinct reasons are joined.
    """
    families = hit.get("families") or ()
    if families and named:
        return tr("what.family", ", ".join(family_name(name) for name in families))
    if hit.get("confidence") == "error":
        return tr("what.unverified")
    seen = []
    for item in hit.get("evidence") or ():
        text = explain_rule(item.get("rule"), item.get("category"))
        if item.get("weight", 0) > 0 and text not in seen:
            seen.append(text)
        if len(seen) >= limit:
            break
    if not seen:
        reasons = hit.get("reasons") or ([hit.get("reason")] if hit.get("reason") else [])
        for rule in reasons:
            text = explain_rule(rule)
            if text not in seen:
                seen.append(text)
            if len(seen) >= limit:
                break
    separator = u"；" if language() == "zh" else "; "
    return separator.join(seen) if seen else tr("what.unverified")


def level_name(level):
    return tr("level.%s" % (level if level in ("high", "medium", "low", "info", "error") else "info"))


_STATUS_KEYS = (
    ("would_repair", "st.would_repair"), ("would_quarantine", "st.would_quarantine"),
    ("would_report", "st.would_report"), ("would_delete", "st.would_delete"), ("would_kill", "st.would_kill"),
    ("would_remove", "st.would_remove"), ("needs manual repair", "st.manual"),
    ("needs administrator rights", "st.admin"), ("repaired", "st.repaired"), ("quarantined", "st.quarantined"),
    ("attribute removed", "st.attr"), ("already clean", "st.already"), ("already gone", "st.already"),
    ("deleted", "st.deleted"), ("killed", "st.killed"),
    ("removed from", "st.removed"), ("failed", "st.failed"),
)


def status_name(status):
    """Engine status strings ('repaired', 'failed: ...') in the user's language."""
    text = str(status or "")
    lowered = text.lower()
    for prefix, key in _STATUS_KEYS:
        if lowered.startswith(prefix):
            detail = text[len(prefix):].lstrip(": ")
            if key in ("st.failed", "st.admin") and detail:
                return "%s (%s)" % (tr(key), detail)
            return tr(key)
    return text

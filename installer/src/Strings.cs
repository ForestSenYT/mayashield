using System.Collections.Generic;
using System.Globalization;

namespace MayaShieldSetup
{
    /// <summary>All text the user sees.  Chinese on a Chinese Windows, English everywhere else.</summary>
    static class L
    {
        public static bool Chinese { get; private set; }

        /// <summary>override: "zh" or "en" from --lang, or null to follow the Windows display language.</summary>
        public static void Init(string languageOverride)
        {
            string name = string.IsNullOrEmpty(languageOverride) ? CultureInfo.CurrentUICulture.Name : languageOverride;
            Chinese = name.ToLowerInvariant().StartsWith("zh");
        }

        public static string Code { get { return Chinese ? "zh" : "en"; } }

        public static string T(string key, params object[] args)
        {
            string[] pair;
            if (!Table.TryGetValue(key, out pair))
                return key;
            string text = Chinese ? pair[1] : pair[0];
            return args.Length == 0 ? text : string.Format(text, args);
        }

        /// <summary>"Maya 2022, Maya 2024" / "Maya 2022、Maya 2024"</summary>
        public static string List(IEnumerable<string> items)
        {
            return string.Join(Chinese ? "、" : ", ", items);
        }

        static readonly Dictionary<string, string[]> Table = new Dictionary<string, string[]>
        {
            // ------------------------------------------------------------ window
            { "title", new[] { "MayaShield {0} Setup", "MayaShield {0} 安装程序" } },
            { "subtitle", new[] { "Protects Maya from scene viruses.", "保护 Maya 不受场景病毒侵害。" } },
            { "headInstall", new[] { "MayaShield will protect {0} on this computer.",
                                     "安装后，MayaShield 会保护这台电脑上的 {0}。" } },
            { "headUpgrade", new[] { "An older MayaShield is installed. Click Install to update it to {0}.",
                                     "这台电脑装的是旧版 MayaShield。点“安装”升级到 {0}。" } },
            { "headInstalled", new[] { "MayaShield {0} is already installed. You can install it again to repair it, or uninstall it.",
                                       "这台电脑已经装好 MayaShield {0}。可以重新安装来修复，也可以卸载。" } },
            { "headUninstall", new[] { "MayaShield will be removed from every Maya on this computer. Files it has quarantined are kept.",
                                       "将从这台电脑上所有的 Maya 中移除 MayaShield。隔离区里的文件会保留。" } },
            { "headNoMaya", new[] { "No supported Maya (2022 or later) was found on this computer.",
                                    "这台电脑上没有找到支持的 Maya（2022 或更高版本）。" } },
            { "closeMaya", new[] { "Please close Maya before you continue.", "继续之前，请先关闭 Maya。" } },
            { "customize", new[] { "Options...", "自定义…" } },
            { "customizeHide", new[] { "Hide options", "收起选项" } },
            { "versions", new[] { "Install for:", "安装到：" } },
            { "mayaItem", new[] { "Maya {0}", "Maya {0}" } },
            { "notSupported", new[] { " (too old, needs Maya 2022 or later)", "（版本太旧，需要 Maya 2022 或更高）" } },
            { "hasVersion", new[] { " (has MayaShield {0})", "（已装 MayaShield {0}）" } },
            { "scopeMachine", new[] { "Everyone who uses this computer (recommended; Windows will ask you to confirm once)",
                                      "这台电脑的所有用户（推荐，Windows 会让你确认一次）" } },
            { "scopeUser", new[] { "Only me (no confirmation needed)", "只给我自己用（不需要确认）" } },
            { "install", new[] { "&Install", "安装(&I)" } },
            { "reinstall", new[] { "&Install Again", "重新安装(&I)" } },
            { "uninstall", new[] { "&Uninstall", "卸载(&U)" } },
            { "close", new[] { "Close", "关闭" } },
            { "details", new[] { "Show details", "显示详情" } },
            { "detailsHide", new[] { "Hide details", "隐藏详情" } },
            { "openLog", new[] { "Open Log", "打开日志" } },
            { "selectOne", new[] { "Tick at least one Maya.", "请至少勾选一个 Maya。" } },
            { "confirmUninstall", new[] { "Remove MayaShield from every Maya on this computer?\n\nFiles it has quarantined are kept.",
                                          "从这台电脑上所有的 Maya 中移除 MayaShield？\n\n隔离区里的文件会保留。" } },
            { "installing", new[] { "Installing...", "正在安装…" } },
            { "uninstalling", new[] { "Uninstalling...", "正在卸载…" } },
            { "installDone", new[] { "Done! MayaShield starts protecting the next time you open Maya.",
                                     "安装完成！下次打开 Maya 就会自动保护。" } },
            { "uninstallDone", new[] { "MayaShield has been removed.", "MayaShield 已卸载。" } },
            { "closingIn", new[] { "This window closes in {0} s.", "{0} 秒后自动关闭。" } },
            { "installDoneWarn", new[] { "Installed, but a few things need a look. See the details below.",
                                         "已安装，但有几处需要留意，请看下面的详情。" } },
            { "uninstallDoneWarn", new[] { "Removed, but a few things need a look. See the details below.",
                                           "已卸载，但有几处需要留意，请看下面的详情。" } },
            { "nextStart", new[] { "MayaShield loads the next time you start Maya.", "下次启动 Maya 时会自动加载 MayaShield。" } },
            { "logSaved", new[] { "Log saved to {0}", "日志保存在 {0}" } },

            // ------------------------------------------------------------ problems, and what to do about them
            { "noMaya", new[] { "No supported Maya (2022 or later) was found on this computer.",
                                "没有找到支持的 Maya（2022 或更高版本）。" } },
            { "mayaRunning", new[] { "Maya is still open:\n\n{0}\n\nSave your work and close Maya, then click Try Again.\n\nIf you are sure Maya is already closed, click Continue.",
                                     "Maya 还开着：\n\n{0}\n\n请保存工作并关闭 Maya，然后点“重试”。\n\n如果你确定 Maya 已经关了，点“继续”。" } },
            { "mayaRunningLog", new[] { "Maya is running. Close it and run Setup again.",
                                        "Maya 正在运行，请关闭后重新运行安装程序。" } },
            { "mayaRunningHint", new[] { "If Maya is not really open, end that process in Task Manager, or run Setup with --force to skip this check.",
                                         "如果 Maya 其实没有开着，请在任务管理器里结束上面的进程，或者给安装程序加上 --force 跳过这项检查。" } },
            { "mayaRunningIgnored", new[] { "You chose to continue although Maya appears to be open.",
                                            "你选择了在 Maya 看起来还开着的情况下继续。" } },
            { "mayaFound", new[] { "Still running: {0}", "还在运行：{0}" } },
            { "procOpen", new[] { "{0} (process {1})", "{0}（进程 {1}）" } },
            { "procStarting", new[] { "A Maya that is still starting (process {0})", "正在启动的 Maya（进程 {0}）" } },
            { "procBatch", new[] { "A Maya batch job, {0} (process {1})", "Maya 批处理任务 {0}（进程 {1}）" } },
            { "procElsewhere", new[] { "A Maya opened by another Windows user or session (process {0})",
                                       "另一个 Windows 用户或会话打开的 Maya（进程 {0}）" } },
            { "procLeftover", new[] { "Passed over a Maya process that has no window (process {0}, started {1}): it was left behind by a Maya that did not finish closing. If Setup then reports files in use, end maya.exe in Task Manager or restart the computer.",
                                      "跳过了一个没有窗口的 Maya 进程（进程 {0}，启动于 {1}）：它是 Maya 关闭时没有退干净留下的。如果接下来提示文件正被使用，请在任务管理器里结束 maya.exe，或者重启电脑。" } },
            { "uacDenied", new[] { "You did not confirm the Windows prompt, so nothing was changed. Click Install again and choose Yes, or open Options and choose \"Only me\".",
                                   "你没有同意 Windows 的确认提示，所以什么都没有改动。可以再点一次“安装”并选择“是”；或者点“自定义”，改成“只给我自己用”。" } },
            { "oldNeedsAdmin", new[] { "Maya {0} has an older MayaShield in its program folder, which would load before this one. Replacing it needs the \"Everyone\" option.",
                                       "Maya {0} 的安装目录里有旧版 MayaShield，它会抢先加载。要替换它，需要选择“这台电脑的所有用户”。" } },
            { "machineCopyWins", new[] { "Maya {0} already has MayaShield {1} installed for everyone who uses this computer, and Maya always uses that copy first. An \"Only me\" install would never be loaded. Choose \"Everyone who uses this computer\" to update it.",
                                         "Maya {0} 已经为这台电脑的所有用户装了 MayaShield {1}，Maya 总是优先使用那一份，“只给我自己用”的那份不会被加载。要升级，请选择“这台电脑的所有用户”。" } },
            { "oldNeedsAdminShort", new[] { "The copy of MayaShield that serves everyone on this computer has to be replaced. Open Options and choose \"Everyone who uses this computer\".",
                                            "需要替换这台电脑上供所有用户使用的那份 MayaShield。请点“自定义”，选择“这台电脑的所有用户”。" } },
            { "selfTestFailed", new[] { "Maya {0}: the check after installing did not pass. The files are installed.",
                                        "Maya {0}：安装后的自动检查没有通过。文件已经装好。" } },
            { "selfTestFailedShort", new[] { "Installed, but the automatic check afterwards did not pass. MayaShield usually still works: open Maya and look for \"MayaShield\" in the menu bar. If it is missing, send the log to the author.",
                                             "已经装好，但安装后的自动检查没有通过。多数情况下 MayaShield 仍然可以正常使用：请打开 Maya，看看菜单栏里有没有“MayaShield”。如果没有，请把日志发给作者。" } },
            { "failed", new[] { "Failed: {0}", "失败：{0}" } },
            { "failedSeeLog", new[] { "Setup could not finish. Close Maya and try again. If it still fails, send the log to the author.",
                                      "安装没有完成。请关闭 Maya 后重试；如果还是不行，请把日志发给作者。" } },
            { "uninstallFailed", new[] { "Uninstall could not finish. Close Maya and try again. If it still fails, send the log to the author.",
                                         "卸载没有完成。请关闭 Maya 后重试；如果还是不行，请把日志发给作者。" } },
            { "anotherRunning", new[] { "Another MayaShield Setup is already running.", "另一个 MayaShield 安装程序正在运行。" } },
            { "badArgs", new[] { "Unknown or incomplete command line option: {0}", "无法识别或不完整的命令行参数：{0}" } },
            { "notMaya", new[] { "{0} is not a Maya installation this program can find, so it was left alone.",
                                 "{0} 不是本程序能找到的 Maya 安装目录，已跳过，没有改动。" } },
            { "noAppDir", new[] { "Your Maya folder (normally Documents\\maya) could not be found. Set MAYA_APP_DIR or pass --app-dir with a full path.",
                                  "找不到你的 Maya 用户目录（通常是“文档\\maya”）。请设置 MAYA_APP_DIR，或用 --app-dir 指定完整路径。" } },
            { "inUse", new[] { "{0} is in use by another program (is Maya or a render still running?). Nothing was changed.",
                               "{0} 正被别的程序使用（Maya 或渲染是不是还开着？）。没有做任何改动。" } },
            { "leftover", new[] { "Could not remove {0} because it is in use. It is harmless and will be removed the next time Setup runs.",
                                  "{0} 正被使用，暂时删不掉。它不影响使用，下次运行安装程序时会被清除。" } },
            { "targetFailed", new[] { "Maya {0}: {1}", "Maya {0}：{1}" } },
            { "prefsFailed", new[] { "Maya {0}: could not switch autoload on ({1}). MayaShield switches it on by itself when Maya starts.",
                                     "Maya {0}：没能开启自动加载（{1}）。Maya 启动时 MayaShield 会自己开启。" } },
            { "warning", new[] { "Warning: {0}", "注意：{0}" } },

            // ------------------------------------------------------------ steps (log)
            { "logStart", new[] { "MayaShield {0} setup, {1}", "MayaShield {0} 安装程序，{1}" } },
            { "found", new[] { "Found Maya {0}: {1}", "找到 Maya {0}：{1}" } },
            { "versionNotFound", new[] { "Maya {0} was not found and is skipped.", "没有找到 Maya {0}，已跳过。" } },
            { "waitingAdmin", new[] { "Waiting for you to confirm the Windows prompt...", "等待你同意 Windows 的确认提示…" } },
            { "copying", new[] { "Maya {0}: copying files to {1}", "Maya {0}：复制文件到 {1}" } },
            { "movedOld", new[] { "Maya {0}: moved an older MayaShield to {1}", "Maya {0}：已把旧版 MayaShield 移到 {1}" } },
            { "removedUserCopy", new[] { "Maya {0}: removed the \"only me\" copy in {1}", "Maya {0}：已删除“只给我自己用”的那份 {1}" } },
            { "configuring", new[] { "Maya {0}: setting up autoload", "Maya {0}：设置自动加载" } },
            { "autoloadOn", new[] { "Maya {0}: autoload switched on in {1}", "Maya {0}：已在 {1} 中开启自动加载" } },
            { "firstStart", new[] { "Maya {0} has not been started yet for this user. MayaShield switches autoload on at its first start.",
                                    "这个用户还没启动过 Maya {0}，MayaShield 会在它首次启动时自动开启自动加载。" } },
            { "selfTesting", new[] { "Maya {0}: checking that MayaShield works", "Maya {0}：检查 MayaShield 能否正常工作" } },
            { "selfTestOk", new[] { "Maya {0}: check passed", "Maya {0}：检查通过" } },
            { "selfTestSkipped", new[] { "Maya {0}: mayapy not found, check skipped", "Maya {0}：没有找到 mayapy，跳过检查" } },
            { "selfTestRetry", new[] { "Maya {0}: trying the check again ({1} of {2})", "Maya {0}：重新检查（第 {1} 次，共 {2} 次）" } },
            { "selfTestRun", new[] { "  ran {0}; exit code {1}; {2} ms", "  运行 {0}；退出码 {1}；用时 {2} 毫秒" } },
            { "selfTestTimeout", new[] { "  stopped after {0} s without an answer", "  {0} 秒没有结果，已停止" } },
            { "registering", new[] { "Adding MayaShield to Apps & features", "添加到“应用和功能”" } },
            { "removing", new[] { "Maya {0}: removing {1}", "Maya {0}：删除 {1}" } },
            { "cleaningPrefs", new[] { "Cleaning preferences in {0}", "清理 {0} 中的设置" } },
            { "unregistering", new[] { "Removing MayaShield from Apps & features", "从“应用和功能”中移除" } },
            { "nothingInstalled", new[] { "MayaShield is not installed in any Maya program folder.", "Maya 安装目录里没有安装 MayaShield。" } },
        };
    }
}

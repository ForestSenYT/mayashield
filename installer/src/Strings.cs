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

        static readonly Dictionary<string, string[]> Table = new Dictionary<string, string[]>
        {
            // ------------------------------------------------------------ window
            { "title", new[] { "MayaShield {0} Setup", "MayaShield {0} 安装程序" } },
            { "subtitle", new[] { "Detects and removes Maya scene viruses and startup infections.",
                                  "检测并清除 Maya 场景病毒和启动项感染。" } },
            { "intro", new[] { "Select the Maya versions to protect, then click Install. Please close Maya first.",
                               "勾选要安装的 Maya 版本，然后点“安装”。请先关闭 Maya。" } },
            { "introUninstall", new[] { "MayaShield will be removed from every Maya version on this computer. Quarantined files are kept.",
                                        "将从这台电脑上所有 Maya 版本中移除 MayaShield。隔离区的文件会保留。" } },
            { "scopeMachine", new[] { "All users (installs into the Maya folder, needs administrator approval)",
                                      "所有用户（装进 Maya 安装目录，需要管理员授权）" } },
            { "scopeUser", new[] { "Current user only (no administrator approval needed)",
                                   "仅当前用户（不需要管理员授权）" } },
            { "install", new[] { "&Install", "安装(&I)" } },
            { "uninstall", new[] { "&Uninstall", "卸载(&U)" } },
            { "close", new[] { "Close", "关闭" } },
            { "ready", new[] { "Ready.", "准备就绪。" } },
            { "mayaItem", new[] { "Maya {0}    {1}", "Maya {0}    {1}" } },
            { "notSupported", new[] { "  (not supported, needs Maya 2022 or later)", "（不支持，需要 Maya 2022 或更高版本）" } },
            { "oldCopyNote", new[] { "  (an older MayaShield here will be replaced)", "（会替换这里的旧版 MayaShield）" } },
            { "selectOne", new[] { "Select at least one Maya version.", "请至少勾选一个 Maya 版本。" } },
            { "confirmUninstall", new[] { "Remove MayaShield from every Maya version on this computer?\n\nQuarantined files are kept.",
                                          "从这台电脑上所有 Maya 版本中移除 MayaShield？\n\n隔离区的文件会保留。" } },
            { "installing", new[] { "Installing...", "正在安装..." } },
            { "uninstalling", new[] { "Uninstalling...", "正在卸载..." } },
            { "installDone", new[] { "Installation complete. Closing...", "安装完成，即将自动退出..." } },
            { "uninstallDone", new[] { "MayaShield has been removed. Closing...", "卸载完成，即将自动退出..." } },
            { "nextStart", new[] { "MayaShield loads the next time you start Maya.", "下次启动 Maya 时会自动加载 MayaShield。" } },

            // ------------------------------------------------------------ problems
            { "noMaya", new[] { "No supported Maya (2022 or later) was found on this computer.",
                                "没有找到支持的 Maya（2022 或更高版本）。" } },
            { "mayaRunning", new[] { "Maya is running. Save your work, close Maya, then click Retry.",
                                     "Maya 正在运行。请保存并关闭 Maya，然后点“重试”。" } },
            { "mayaRunningLog", new[] { "Maya is running. Close it and run Setup again.",
                                        "Maya 正在运行，请关闭后重新运行安装程序。" } },
            { "uacDenied", new[] { "Administrator approval was declined, so nothing was changed in the Maya folder. You can choose \"Current user only\" instead.",
                                   "已拒绝管理员授权，Maya 安装目录没有任何改动。也可以改选“仅当前用户”。" } },
            { "oldNeedsAdmin", new[] { "Maya {0} has an older MayaShield in its program folder, which would load before this one. Replacing it needs the \"All users\" option.",
                                       "Maya {0} 的安装目录里有旧版 MayaShield，它会抢先加载。替换它需要选择“所有用户”。" } },
            { "selfTestFailed", new[] { "The self-test failed for Maya {0}. The files are installed, but MayaShield may not work in this Maya. See the log for details.",
                                        "Maya {0} 的自检没有通过。文件已经装好，但 MayaShield 在这个 Maya 里可能无法正常工作，详情见日志。" } },
            { "selfTestFailedShort", new[] { "The self-test failed. See the log for details.", "自检没有通过，详情见日志。" } },
            { "failed", new[] { "Failed: {0}", "失败：{0}" } },
            { "failedSeeLog", new[] { "Setup did not finish. See the log for details.", "安装没有完成，详情见日志。" } },
            { "uninstallFailed", new[] { "Uninstall did not finish. See the log for details.", "卸载没有完成，详情见日志。" } },
            { "badArgs", new[] { "Unknown or incomplete command line option: {0}", "无法识别或不完整的命令行参数：{0}" } },
            { "notMaya", new[] { "{0} does not look like a Maya installation, so it was left alone.",
                                 "{0} 不是 Maya 安装目录，已跳过，没有改动。" } },
            { "warning", new[] { "Warning: {0}", "警告：{0}" } },

            // ------------------------------------------------------------ steps (log)
            { "logStart", new[] { "MayaShield {0} setup, {1}", "MayaShield {0} 安装程序，{1}" } },
            { "found", new[] { "Found Maya {0}: {1}", "找到 Maya {0}：{1}" } },
            { "versionNotFound", new[] { "Maya {0} was not found and is skipped.", "没有找到 Maya {0}，已跳过。" } },
            { "waitingAdmin", new[] { "Waiting for administrator approval...", "等待管理员授权..." } },
            { "copying", new[] { "Maya {0}: copying files to {1}", "Maya {0}：复制文件到 {1}" } },
            { "movedOld", new[] { "Maya {0}: moved an older MayaShield to {1}", "Maya {0}：已把旧版 MayaShield 移到 {1}" } },
            { "removedUserCopy", new[] { "Maya {0}: removed the current-user copy in {1}", "Maya {0}：已删除“仅当前用户”安装的副本 {1}" } },
            { "configuring", new[] { "Maya {0}: setting up autoload", "Maya {0}：设置自动加载" } },
            { "autoloadOn", new[] { "Maya {0}: autoload switched on in {1}", "Maya {0}：已在 {1} 中开启自动加载" } },
            { "firstStart", new[] { "Maya {0} has not been started yet for this user. MayaShield switches autoload on at its first start.",
                                    "这个用户还没启动过 Maya {0}，MayaShield 会在它首次启动时自动开启自动加载。" } },
            { "selfTesting", new[] { "Maya {0}: running the self-test", "Maya {0}：运行自检" } },
            { "selfTestOk", new[] { "Maya {0}: self-test passed", "Maya {0}：自检通过" } },
            { "selfTestSkipped", new[] { "Maya {0}: mayapy not found, self-test skipped", "Maya {0}：没有找到 mayapy，跳过自检" } },
            { "registering", new[] { "Adding MayaShield to Apps & features", "添加到“应用和功能”" } },
            { "removing", new[] { "Maya {0}: removing {1}", "Maya {0}：删除 {1}" } },
            { "cleaningPrefs", new[] { "Cleaning preferences in {0}", "清理 {0} 中的设置" } },
            { "unregistering", new[] { "Removing MayaShield from Apps & features", "从“应用和功能”中移除" } },
            { "nothingInstalled", new[] { "MayaShield is not installed in any Maya program folder.", "Maya 安装目录里没有安装 MayaShield。" } },
        };
    }
}

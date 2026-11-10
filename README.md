# MayaShield 4.1.1

检测并清除 Maya 场景病毒和启动项感染的插件。支持 Maya 2022 到 2027。

## 安装

**Windows：** 双击 `dist/MayaShield-4.1.1-Setup.exe`，点“安装”。请先关闭 Maya。

- 窗口会用一句话说明将要做什么。默认安装到这台电脑上所有受支持的 Maya，供所有用户使用，Windows 会让你确认一次。想只装某几个版本，或者只给自己装（不需要确认），点“自定义”。如果这台电脑已经为所有用户装过 MayaShield，Maya 会一直优先用那一份，只给自己装的不会被加载，安装程序会提示你改选“所有用户”。
- 安装前会检查 Maya 是不是还开着：带窗口的 Maya 和批处理任务（`mayabatch`）算开着，提示里会写明是哪一个。Maya 关闭时偶尔会留下一个没有窗口的后台进程，安装程序会跳过它。如果你确定 Maya 已经关了却仍被拦住，点“继续”即可。
- 安装顺利完成后，窗口会在几秒后自动关闭。有需要你留意的情况时窗口会留着，写明原因和该怎么办，并提供“打开日志”按钮。
- 下次启动 Maya 会自动加载。没启动过的 Maya 版本，以及同一台电脑上的其他 Windows 用户，会在第一次启动时自动开启。
- 每次运行都会保存日志，在 `%LOCALAPPDATA%\MayaShield-Logs`（保留最近 10 份）。安装遇到问题时，把日志发给作者即可。

旧版本（包括手动放在 `bin/plug-ins` 或 `文档\maya\plug-ins` 里的 v3）会被自动移到备份文件夹，它们不移走的话会抢先加载。卸载可以在安装程序里点“卸载”，或用 Windows 的“应用和功能”。隔离区的文件卸载后仍然保留。

安装程序的界面语言跟随 Windows 显示语言：中文系统显示中文，其他系统显示英文，也可以用 `--lang zh` 或 `--lang en` 指定。

**关于数字签名：** 安装程序带有作者 `4rest` 的签名，但这是自签名证书，不是从证书机构购买的。在文件属性的“数字签名”页能看到签名者，Windows 仍然会提示“未知发布者”，SmartScreen 也可能提醒，这是正常的。想核对文件确实来自作者，可以比对 `dist/MayaShield-codesign.cer` 的指纹。

批量部署可以用静默模式：

```
MayaShield-4.1.1-Setup.exe --silent [--scope machine|user] [--versions 2024,2025] [--log install.log] [--lang zh|en]
MayaShield-4.1.1-Setup.exe --uninstall --silent
```

退出码：`0` 成功，`1` 出错（详情见日志），`2` 没找到 Maya，`3` Maya 正在运行（日志里会写明是哪个进程；加 `--force` 可以跳过这项检查），`4` 拒绝了管理员确认，`5` 旧版需要管理员权限才能替换，`6` 已安装但安装后的检查没有通过，`7` 另一个安装程序正在运行。

**macOS / Linux / 手动安装：** 把 `dist/MayaShield-4.1.1-module.zip` 解压到 `<Maya 用户目录>/<版本>/modules/`，启动时会自动开启 autoload。这种方式没有在 macOS 和 Linux 上实测过。

## 使用

菜单栏 **MayaShield > 打开 MayaShield**（英文版 Maya 是 **Open MayaShield**）。界面语言跟随 Maya：中文版 Maya 显示中文，其他显示英文。

- **防护状态** 在面板最上面：绿色表示打开场景前的检查和启动时的检查都开着。
- **一键体检** 检查当前场景、后台任务（scriptJob）和 Maya 的启动文件。只检查，不改动任何东西。
- 发现问题时会用一句话说明是什么问题、在哪里，并出现 **立即修复** 按钮。修复前会列出要做的事让你确认。从启动文件里清掉的内容都进隔离区，可以恢复。
- **检查文件… / 检查文件夹…** 不打开场景，直接检查文件。有威胁的文件可以一键生成干净副本，原文件不动。
- 其余功能（防护开关、清理选项、隔离区、报告、日志）都在 **高级** 里。

也可以用命令，适合写进工具或流程：

| 命令 | 作用 |
| --- | --- |
| `mayashieldUI [-workspace] [-checkup]` | 打开面板；加 `-checkup` 直接开始体检 |
| `mayashield -scan` | 检查启动项（`userSetup`、`Maya.env`、模块等） |
| `mayashield -scanAll` | 同上，并检查脚本目录里的所有工具文件 |
| `mayashield -clean [-scanAll]` | 修复或隔离高风险的启动项文件 |
| `mayashield -cleanScene` | 删除当前场景里的高风险节点 |
| `mayashield -cleanJobs` | 结束可疑 scriptJob，移除已加载的病毒模块 |
| `mayashield -scanFile "a.mb" [-deep]` | 不打开文件，直接扫描场景 |
| `mayashield -scanFolder "dir" [-deep]` | 并行扫描整个文件夹 |
| `mayashield -offlineCleanFile "a.ma" [-inPlace] [-offlineOut "b.ma"]` | 不打开文件，直接清理场景 |
| `mayashield -offlineCleanFolder "dir" [-inPlace] [-offlineOutDir "out"]` | 清理整个文件夹 |
| `mayashield -immunize` | 在 `userSetup.py` 写入启动备用检查，并替换 v3 留下的问题代码 |
| `mayashield -listQuarantine` / `-restore "id"` | 查看隔离区、恢复文件 |

通用选项：`-dryRun`、`-verbose`、`-includeMedium`、`-timeout 120`、`-report "out.json"`。

不装进 Maya 也能在命令行扫描，比如在服务器上批量检查：

```
python -m mayashield_core scan <文件或文件夹> [--json 报告.json]
python -m mayashield_core clean <文件或文件夹> [--in-place] [--output 路径]
```

退出码：`0` 未发现问题，`1` 发现威胁，`2` 用法错误或路径不存在。

## 实时防护

插件加载后，打开、导入、引用场景之前都会先扫描（按文件内容判断，改了扩展名也一样），导入 `.mel` 文件前也会扫描。这时文件还没有被读入，里面的脚本没有机会运行。

- **有把握是危险文件**（已知病毒，或文件里有一打开就会运行代码的内容）：弹窗拦下，可以选择不打开、生成干净副本后打开，或仍然打开。
- **不常见但不能确定是病毒**：照常打开，只在视图上方给一条几秒钟的提示。之后打开 MayaShield 面板可以看到详情。
- **无法完整读懂的文件**（损坏，或故意做成这样来躲避检查）：会先询问，不会当作安全文件放行。
- **双击打开的场景：** 插件在 Maya 启动早期（`userSetup` 阶段）就会加载，所以防护能赶在场景打开之前生效。如果在 Maya 的 Preferences > Security 里禁用了 `userSetup`，或设置了 `MAYA_SKIP_USERSETUP_PY`，就只能靠普通的 autoload，它比双击的场景晚加载：这时场景里的脚本会先运行，打开后的检查才报警。
- **关闭：** 在面板的“高级 > 防护开关”里。在插件管理器里取消 Auto load 后，它不会被自动重新打开。
- **批处理模式（渲染农场）：** 只写警告，不弹窗也不拦截。
- **大文件和耗时：** 超过 4 GB 的文件跳过打开前扫描，只做打开后的检查（可用 optionVar `mayashield_guardMaxMB` 调整）。打开前的扫描有时间上限（30 秒，文件每大 1 MB 再加 1 秒），超时按“无法完整检查”处理。

## 风险分数

每个节点或文件得到 0–100 分：

| 分数 | 等级 | 界面上的说法 | 处理 |
| --- | --- | --- | --- |
| 70 以上 | high | 危险 | 打开前拦截，修复时默认清除 |
| 40–69 | medium | 需留意 | 只提示；勾选“中风险的也清理”或加 `-includeMedium` 才会清理 |
| 15–39 | low | 轻微 | 只在勾选“显示技术细节”时列出，并写进报告 |

已知病毒的特征会直接判高分。其他行为（运行解码后的代码、写入 `userSetup`、启动进程、联网等）各有权重，多个信号一起出现时分数才会累积上去。两种情况单凭一点就算高风险：场景打开时会自动运行、并且会启动进程或联网的脚本节点；以及 `.ma` 文件顶层出现 Maya 自己不会写、又会直接运行代码的语句（`python(...)`、`source`、`eval`、变量和流程控制等）。分数和依据都会写进报告，规则都在 [mayashield_core/rules.py](mayashield_core/rules.py)。

如果工作室的流程确实需要这样的内容，打开时选“仍然打开”即可；清理默认生成副本，不改原文件。

这是静态分析，只能根据文件内容判断：它能识别已知家族和可疑行为，但不保证发现所有新型恶意代码。发现场景异常时，请同时保留原文件。

## 项目结构

```
mayashield.py            插件入口
mayashield_core/         引擎。rules、analyzer、parsers、scanner、cleaner、persistence 和 pool 不依赖 Maya
mayashield_core/i18n.py  界面文字（中文和英文）
installer/               安装程序源码和打包脚本
dist/                    打包结果（不入库）
```

## 开发

```
python installer/build.py                              # 打包，需要 Windows 自带的 .NET Framework 4
python -m unittest discover -s tests                   # 引擎和安装程序测试
mayapy tests/qt_ui_test.py                             # 用 Maya 自带的 Python 和 Qt 测试面板（无头）
powershell -File tests/run_gui_test.ps1 -Maya <Maya 目录> -Sandbox <dir>   # 在真实 Maya 界面里测试（用隔离的配置目录）
```

`tests/` 目录不随仓库发布，这几条命令只在有这个目录的开发机上可用。

- 代码必须保持 Python 3.7 语法（Maya 2022）。不能运行 Maya 的模块（`rules`、`analyzer`、解析器、`scanner`、`cleaner`、`persistence`、`pool`）不要 `import maya`。
- 界面上的每句话都放在 `i18n.py`，中英文各一份；新增检测规则时要在那里补一句大白话的解释。
- `installer/payload/userSetup.py` 不要改动：Maya 会记住它运行过的每个 `userSetup.py` 的哈希，文件一变就会向所有用户弹出警告。`build.py` 会核对它的哈希。
- `build.py` 只打包已经被 git 跟踪的引擎文件，并把换行统一成 LF。

**签名：** `build.py` 会用当前 Windows 用户证书库里主题为 `CN=4rest` 的代码签名证书给安装程序签名并加时间戳；找不到证书时照常打包，只是不签名（也可以加 `--no-sign`）。自签名证书可以这样生成一次，私钥只留在本机的证书库里：

```
New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=4rest" -CertStoreLocation Cert:\CurrentUser\My -KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 -NotAfter (Get-Date).AddYears(10)
```

每次打包得到的 exe 都不相同（编译器会写入时间），所以发布时请打包一次、签名一次，就发布那一个文件。

## 许可证

[MIT](LICENSE)

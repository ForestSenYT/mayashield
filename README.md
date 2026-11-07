# MayaShield 4.0.0

检测并清除 Maya 场景病毒和启动项感染的插件。支持 Maya 2022 到 2027。

## 安装

**Windows：** 双击 `dist/MayaShield-4.0.0-Setup.exe`。

1. 安装程序列出检测到的 Maya（从注册表和磁盘查找，会排除已卸载的残留记录）。
2. 勾选版本后点“安装”，请先关闭 Maya。默认装进 Maya 安装目录，供所有用户使用，会弹一次管理员授权。不想授权可以选“仅当前用户”。
3. 安装完成后进度条走满，安装程序会自动退出；出错时窗口保留，显示原因和日志。
4. 下次启动 Maya 会自动加载，插件管理器里 Auto load 已勾选。没启动过的 Maya 版本，以及同一台电脑上的其他 Windows 用户，会在第一次启动时自动开启。

旧版本（包括手动放在 `bin/plug-ins` 里的 v3）会被自动移到 `modules/MayaShield.legacy-backup`，它们不移走的话会抢先加载。卸载可以在安装程序里点“卸载”，或用 Windows 的“应用和功能”。隔离区的文件卸载后仍然保留。

安装程序的界面语言跟随 Windows 显示语言：中文系统显示中文，其他系统显示英文，也可以用 `--lang zh` 或 `--lang en` 指定。

安装程序没有代码签名，Windows 可能提示“未知发布者”。

批量部署可以用静默模式：

```
MayaShield-4.0.0-Setup.exe --silent [--scope machine|user] [--versions 2024,2025] [--log install.log] [--lang zh|en]
MayaShield-4.0.0-Setup.exe --uninstall --silent
```

退出码：`0` 成功，`2` 没找到 Maya，`3` Maya 正在运行，`4` 拒绝了管理员授权，`5` 旧版需要管理员权限才能替换，`6` 自检失败。

**macOS / Linux / 手动安装：** 把 `dist/MayaShield-4.0.0-module.zip` 解压到 `<Maya 用户目录>/<版本>/modules/`，启动时会自动开启 autoload。这种方式没有在 macOS 和 Linux 上实测过。

## 使用

菜单栏 **MayaShield > Open Panel** 打开面板，或用命令：

| 命令 | 作用 |
| --- | --- |
| `mayashield -scan` | 检查启动项（`userSetup`、`Maya.env`、模块等） |
| `mayashield -scanAll` | 同上，并检查脚本目录里的所有工具文件 |
| `mayashield -clean [-scanAll]` | 修复或隔离高风险的启动项文件 |
| `mayashield -cleanScene` | 删除当前场景里的高风险节点 |
| `mayashield -cleanJobs` | 结束可疑 scriptJob，移除已加载的病毒模块 |
| `mayashield -scanFile "a.mb" [-deep]` | 不打开文件，直接扫描场景 |
| `mayashield -scanFolder "dir" [-deep]` | 并行扫描整个文件夹 |
| `mayashield -offlineCleanFile "a.ma" [-inPlace] [-offlineOut "b.ma"]` | 不打开文件，直接清理场景 |
| `mayashield -offlineCleanFolder "dir" [-inPlace] [-offlineOutDir "out"]` | 清理整个文件夹 |
| `mayashield -immunize` | 在 `userSetup.py` 写入启动检查，并替换 v3 留下的问题代码 |
| `mayashield -listQuarantine` / `-restore "id"` | 查看隔离区、恢复文件 |
| `mayashieldUI` | 打开面板 |

通用选项：`-dryRun`、`-verbose`、`-includeMedium`、`-timeout 120`、`-report "out.json"`。

不装进 Maya 也能在命令行扫描，比如在服务器上批量检查：

```
python -m mayashield_core scan <文件或文件夹> [--json 报告.json]
python -m mayashield_core clean <文件或文件夹> [--in-place] [--output 路径]
```

退出码：`0` 未发现问题，`1` 发现威胁。

## 实时防护

插件加载后，打开、导入、引用场景之前都会先扫描（按文件内容判断，改了扩展名也一样），导入 `.mel` 文件前也会扫描。发现高风险时文件还没有被读入，可以选择拦截、生成干净副本后打开，或仍然打开。无法完整解析的文件也会先询问，不会当作安全文件放行。

- **双击打开的场景：** 插件在 Maya 启动早期（`userSetup` 阶段）就会加载，所以防护能赶在场景打开之前生效。如果在 Maya 的 Preferences > Security 里禁用了 `userSetup`，或设置了 `MAYA_SKIP_USERSETUP_PY`，就只能靠普通的 autoload，它比双击的场景晚加载：这时场景里的脚本会先运行，打开后的检查才报警。
- **关闭：** 可以在 **MayaShield > Preferences** 里关闭。在插件管理器里取消 Auto load 后，它不会被自动重新打开。
- **批处理模式（渲染农场）：** 只写警告，不弹窗也不拦截。
- **大文件：** 超过 4 GB 的文件跳过打开前扫描，只做打开后的检查（可用 optionVar `mayashield_guardMaxMB` 调整）。

## 风险分数

每个节点或文件得到 0–100 分：

| 分数 | 等级 | 处理 |
| --- | --- | --- |
| 70 以上 | high | 打开前拦截，清理时默认删除 |
| 40–69 | medium | 提示复查，只有加 `-includeMedium` 才会清理 |
| 15–39 | low | 只列在报告里 |

已知病毒的特征会直接判高分。其他行为（运行解码后的代码、写入 `userSetup`、启动进程、联网等）各有权重，多个信号一起出现时分数才会累积上去。场景打开时会自动运行、并且会启动进程或联网的脚本节点，单凭这一点就算高风险。分数和依据都会写进报告，规则都在 [mayashield_core/rules.py](mayashield_core/rules.py)。

如果工作室的流程确实需要这样的脚本节点，打开时选 “Open Anyway” 即可；清理默认生成副本，不改原文件。

这是静态分析，只能根据文件内容判断：它能识别已知家族和可疑行为，但不保证发现所有新型恶意代码。发现场景异常时，请同时保留原文件。

## 项目结构

```
mayashield.py            插件入口
mayashield_core/         引擎。rules、analyzer、parsers、scanner、cleaner、persistence 和 pool 不依赖 Maya
installer/               安装程序源码和打包脚本
tests/                   测试
archive/                 v3.1.0 原始文件
dist/                    打包结果
```

## 开发

```
python installer/build.py                              # 打包，需要 Windows 自带的 .NET Framework 4
python -m unittest discover -s tests                   # 引擎和安装程序测试
mayapy tests/maya_integration_test.py                  # 在真实 Maya 里测试（批处理）
powershell -File tests/run_gui_test.ps1 -Sandbox <dir> # 在真实 Maya 界面里测试（用隔离的配置目录）
python tests/qt_ui_test.py                             # 用当前 Python 的 PySide 测试面板（无头）
python tests/corpus_check.py <场景文件夹>               # 误报和速度检查，并与 v3.1 对比
```

代码必须保持 Python 3.7 语法（Maya 2022）。不能运行 Maya 的模块（`rules`、`analyzer`、解析器、`scanner`、`cleaner`、`persistence`、`pool`）不要 `import maya`。

## 许可证

[MIT](LICENSE)

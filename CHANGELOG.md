# 更新记录

## 4.0.0

### 版本支持
- 支持 Maya 2022 到 2027：Python 3.7 到 3.13，PySide2 和 PySide6。
- 插件改用 Python API 2.0（`maya.api.OpenMaya`）。

### 扫描机制
- `.ma` 不再按行匹配关键字，改成按 MEL 语句解析，能还原 Maya 拆行保存的长字符串。Maya 不会写入的顶层语句，以及藏在合法语句里的反引号或函数调用，都会被检出。
- `.mb` 用原生 IFF 解析器读取（`FOR8` 和旧的 `FOR4` 都支持），不用启动 Maya，也不需要许可证。文件结构损坏时会明确报告，并仍然对原始字节做一遍特征扫描。
- 大文件用内存映射加单个正则跳过数字段；文件夹扫描用多个工作进程并行，界面不再卡住。
- 进程隔离：工作进程以 `-I` 模式启动，崩溃或超时只影响当前文件。

### 算法
- 用加权行为评分（0–100）代替“命中任一关键字”。多个弱信号会累积，但常见函数单独出现不会判高危。
- Python 代码走 AST 分析，能解析别名、`__import__`、`getattr`，以及拼接、反转、`chr()` 构造的字符串。
- 自动解开 base64、hex、zlib、gzip、bz2 嵌套编码后再分析。
- 新增家族特征：`MayaMelUIConfigurationFile` 的文件头、过程名和变量名。
- 按节点类型判断节点名：叫 `vaccine_bottle_geo` 的模型不再被当成病毒。
- 字符串里的代码也会分析：`python($c)`、`evalDeferred("...")`、`scriptJob` 的回调字符串都不例外。
- 场景打开时自动运行并启动进程或联网的脚本节点，直接判高风险。
- 按 MEL 的语义跟踪当前选择：`setAttr` 作用于选中的节点，只有 `createNode` 和 `select` 会改变它。`createNode -n "x" script`、`select -ne "x"` 这类写法都无法躲过节点类型判断。

### 实时防护
- 用 `kBeforeOpenCheck` 等回调在打开、导入、引用**之前**扫描，可以直接拒绝文件。按文件内容识别场景，改了扩展名也一样；导入 `.mel` 前同样会扫描。
- 插件在 `userSetup` 阶段就加载，防护能赶在双击打开的场景之前生效（普通 autoload 太晚，实测场景脚本会先运行）。
- 无法完整解析的文件会先询问，不会当作安全文件放行。
- 打开后的二次检查还会查看已加载的病毒模块。
- 批处理模式（渲染农场）只警告，不弹窗也不拦截。

### 持久化扫描
- 新增扫描位置：`Documents/maya/scripts`、所有已安装版本和语言目录、真实的 `sys.path`、模块 `.mod`、Maya 安装目录的 `resources/l10n`。
- 能分离时就精确修复（只删除感染内容，保留你自己的 `userSetup`），否则隔离。隔离文件带清单，可以恢复。

### 修复的 3.x 问题
- **打开前防护从未生效**：`file -q -executeScriptNodes` 这个标志不能查询。
- **免疫功能会破坏启动**：写入 `userSetup.py` 的代码块启动时抛 `NameError`，它后面的内容都不会执行。`mayashield -immunize` 会替换掉旧代码块。
- **清理可能破坏场景**：恶意节点是文件里最后一个节点时，所有 `connectAttr` 会被一起删掉。
- **清理会损坏中文**：非 UTF-8 字符被丢弃；现在除了被删除的内容，其余字节完全不变。
- **误删节点**：离线清理 `.ma` 时，名字里带 `vaccine`、`diversion` 等词的普通节点，以及缺插件产生的 `unknown` 节点，都会被删除。
- **误报**：Maya 自带的 319 个 `.ma` 场景里有 73 个被判高危（现在 0 个高危，1 个中风险）。
- **隔离误伤**：任何用到 `evalDeferred` 或 `confirmDialog` 的 `userSetup` 都可能被隔离。
- **可绕过的豁免**：文件里写一行 `# MAYASHIELD_IMMUNIZED`、或把代码放进 `uiConfigurationScriptNode`，就能躲过扫描。
- **沙箱失效**：`MAYA_SKIP_USERSETUP` 不是 Maya 读取的变量，`PYTHONPATH` 上的 `userSetup.py` 仍会在“隔离”的扫描进程里运行；正确的变量是 `MAYA_SKIP_USERSETUP_PY`。
- 点击日志里的链接会清空日志；卸载插件后菜单还留着。

### 行为变化
- `-clean` 和离线清理默认只处理高风险（≥70 分），中风险加 `-includeMedium`，或在面板勾选 “Clean medium risk too”。
- 清理时被替换的文件都会保留 `*.mayashield_bak` 备份，只读属性也会保留。默认的干净副本不会覆盖已有副本，会依次命名为 `*.cleaned.ma`、`*.cleaned.1.ma`。
- 修复 `userSetup` 和写入启动检查时，其余内容的字节和换行符都保持不变，并在旁边留一份备份。
- 文件在清理后被改动过，或隔离区的副本被改动过，恢复时会拒绝操作，不会覆盖。
- 隔离区从 `scripts/_MAYASHIELD_QUARANTINE` 移到 `<Documents>/maya/mayashield/quarantine`。
- `.mb` 清理不再用 mayapy 重新保存，而是直接删除对应数据块。无法可靠解析的文件会拒绝清理。
- 代码从单个文件拆成 `mayashield.py`（插件入口）和 `mayashield_core` 包，不能再只复制一个文件来安装。

### 安装
- 新增 `MayaShield-4.0.0-Setup.exe`：识别 Maya 安装目录，一键安装并设置 autoload，可卸载。
- 安装或卸载完成后进度条立即走满，安装程序自动退出；失败时窗口保留并显示原因。
- 安装程序界面和日志的语言跟随 Windows 显示语言（中文或英文），可用 `--lang` 指定。

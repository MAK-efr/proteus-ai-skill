# Proteus 自动化接口 · 能力地图（本机实测）

环境：Proteus 8.16 SP3（`D:\Proteus\BIN\PDS.EXE`，2023-07-17），Windows 11，
库 `proteus_automatic_api 0.2.0`（纯 Python，装在
`<venv>\Lib\site-packages`）。

所有"✅"都对应一次真实调用；"❌"附原始异常。"⚠️"表示能用但有前置条件或需要绕法。

---

## 0. 三层能力模型

| 层 | 机制 | 稳定性 | 代表接口 |
|---|---|---|---|
| 进程/窗口层 | `ctypes` 枚举窗口、菜单、发 WM_COMMAND/WM_CHAR | 最稳 | `proteus_native.windows/menus`、`Session.menu` |
| 仿真控制层 | 菜单状态 + 只读 WINCORE 状态控件内存 + 剪贴板/堆 | 中，含版本指纹门槛 | `Simulation.status/start/pause/stop/run_for` |
| 工程编辑层 | 直接编解码 `.pdsprj` 内的 `ROOT.DSN`/`ROOT.CDB` | 离线可用，无需 GUI | `Project`、`Circuit`、`Library`、`net_objects` |

版本指纹：`Simulation` 只读状态栏要求 `WINCORE.DLL` 的 sha256 等于
`944013e9…acd569bb`。**本机匹配**（145 920 B），所以 `status_text`、`simulation_seconds`、
`run_for` 全部可用；换版本会直接退化成 `NotImplementedError`。

---

## 1. ✅ 实测可用

| 接口 | 实测证据 |
|---|---|
| `Session(project)` 启动 | 打开副本 1 s 内就绪，`_ready()` 找到主窗口 |
| `Simulation.status()` | `{'state':'stopped','simulation_seconds':None,'status_text':'ROOT - Root sheet 1'}`；运行中 `ANIMATING: 00:00:01.000701 (CPU load 1%)` |
| `Simulation.set_firmware(ref, hex)` | 回读属性 `PROGRAM=<proj>\firmware.hex`、`CLOCK_SCALE=8 Times`；hex 校验和逐行验过 |
| `Session.netlist()` | **10 个元件 / 66 个网络**（走 工具→编译网络表→SDF 解析） |
| `Session.set_properties(ref, **p)` | 写入 `TRACE_GPIO=3` 并回读确认（ADI 导入通道） |
| `Session.save()` | 文件 mtime 变化并返回路径 |
| `Simulation.start/pause/stop` | 状态机 `stopped→running→paused→running→stopped` 全部按时切换 |
| `Simulation.run_for(s)`（**仅第一次**） | `run_for(0.5)` → `completion=timer_breakpoint`，`end=0.50070144`，`overshoot=0.7 ms` |
| `Simulation.controls()` | 返回 `[]` —— 该工程没有开关/按键类执行器（不是接口坏） |
| `measurements.graphs()` | 返回 `[]` —— 该工程没有 graph 窗口（不是接口坏） |
| `Debug/Start VSM Debugging`（579） | 进入调试模式，`state=paused`，`t=0.002515` |
| `Debug/Step Over Source Line`（583） | `0.002515 → 0.002518 → 0.002520 → 0.002523 → 0.002526`，**约 2.5 µs/步**（一条指令） |
| `Debug/Step Into Source Line`（584） | 同上下发成功并推进 |
| `Debug/Reset Persistent Model Data`（589） | 会弹 `VSM Debugger` 模态框，点 `&Yes` 后可继续 |

---

## 2. ❌ 库原生实现在本机不可用（4 处）

### 2.1 `Simulation.run_for()` 第二次必超时
第一次成功停在定时断点后，第二次再下定时断点，引擎会**在目标时刻冻住**：
仿真秒数不再增长、`CPU load` 掉到 0%，但 `state` 仍报 `ANIMATING`、`reason` 为空。
库的完成条件要求 `reason` 以 `Execution timer breakpoint: ` 开头且与调用前不同，
永远不满足 → 等满 60 s 抛 `TimeoutError`。

原始异常：`TimeoutError: Proteus PID 16032: [...窗口 JSON...]`

> 顺带再次印证：**"状态说 running" + "CPU load 0%" + "仿真秒数不动" = 引擎实际已停**，
> 这三个字段单独看都会骗人。

**绕法（已验证）**：`papi.run_exact(seconds)` —— 自己下定时断点，然后轮询
「到达目标 且 秒数不再变化」→ 主动 `pause()`。连续两次调用实测：
`0.301038` → `0.601038`，`completion` 均为 `timer_breakpoint`。

### 2.2 `Simulation.log()` / `gpio_events()` / `errors()` 全部超时
三者共用一条路径：给 `Simulation Log` 窗口发 `WM_CONTEXTMENU(0x7B)`，然后等
`QPopup` 类弹菜单，再 Home/Down/Enter。本机**根本不弹** `QPopup`
（窗口列表里只有 `Simulation Log` 的 `QTool` 容器），15 s 后抛：

`TimeoutError: Proteus PID 16032: [{"hwnd": 11995512, "title": "Simulation Log", "cls": "QTool", ...}]`

**绕法（已验证）**：两条，按可靠性排序
1. **堆内提取**：`gh_tx.extract(pid)` 直接读 PDS 堆里的
   `#T:<文本>\0%T=<秒>\0%S=<源>\0%E=<代码>\0` 记录结构 —— 不依赖窗口、不依赖剪贴板，
   本项目解出 **305 条 GPIO 记录**。
2. **剪贴板**：`SetForegroundWindow` + `SendInput` 发 `Ctrl+A`/`Ctrl+C`。
   实测读到 **17 640 字符 / 404 行**。注意：用容器窗口句柄直接发 `WM_COPY` 不行，
   必须走真实键盘输入。

> 关键前提：**`TRACE_GPIO` 默认是 0，必须先 `set_properties(U1, TRACE_GPIO='3', ...)`**，
> 否则日志几乎为空（本项目未开时日志只有 67 字符）。这是最容易漏的一步。

### 2.3 进了 VSM 调试模式后 `Simulation.start()` 失效
`start()` 只发 576 `Debug/Run Simulation`。在调试模式（579 启动）下 576 不再把引擎拉起来，
`_until({'running'})` 等满 15 s 抛 `TimeoutError`。

**绕法（已验证）**：按 `576 → 579(Start VSM Debugging) → 577(no breakpoints)` 依次试，
每次轮询至多 3 s，谁先起来算谁。`papi.start()` 即此实现。

### 2.4 `Debug/Reset Persistent Model Data` 会卡死会话
命令 589 弹 `VSM Debugger` 模态框：
「Reset all Persistent Model Data to initial values?」，
不点掉的话 `status()` 直接返回 `{'state':'blocked'}`，后续所有窗口操作失效。

**绕法（已验证）**：发出命令后轮询 `#32770` 对话框，点 `&Yes`，再读状态。

---

## 3. ⚠️ 有条件可用 / 未验证

| 项 | 状态 |
|---|---|
| `measurements.export_graph()` | 需要工程里**先存在 graph 窗口**。本工程没有，实测 `Graph/Add Traces...` 在无 graph 窗口时**静默无响应**（0 个新窗口，无异常），所以"程序化造一条模拟量轨迹"这条路没打通 |
| `Simulation.press/release/set_switch` | 依赖 `Circuit.bind_controls()` 先把按键绑定写进工程；本工程无执行器，未验证 |
| `Debug/Animated Single Step`（587） | 下发成功但仿真时间**不动**（0.601037648 五次不变）。要单步请用 583/584 |
| `Graph/Simulate Graph`、`Graph/Export Graph Data...` | 命令存在（564/567），受 3.1 前置条件限制，未跑通 |
| `Debug/Configure Diagnostics`（590）、`Enable Remote Debug Monitor`（591） | 命令存在，未验证 |
| 离线编辑层（`Circuit`/`Project`/`Library`/`pin_geometry`/`net_objects`） | **已实测打通，见第 7 节** |

---

## 4. 原生命令表（这是"完全接入"的兜底）

库没包装的能力，只要菜单里有，就能用 `papi.Sim.cmd(path)` 或 `cmd(command_id)` 直接发。

本机导出 **140 条**，完整表：`MENU_MAP.md`（含每条当前是否可用）、`menu_map.json`。

最关键的 14 条：

| command_id | 菜单路径 | 用途 |
|---|---|---|
| 576 | Debug/Run Simulation | 普通运行 |
| 577 | Debug/Run Simulation (no breakpoints) | 忽略断点跑 |
| 578 | Debug/Run Simulation (timed breakpoint) | 定时断点（配对话框输入） |
| 579 | Debug/Start VSM Debugging | 进调试模式 |
| 581 | Debug/Pause VSM Debugging | 暂停 |
| 582 | Debug/Stop VSM Debugging | 停止 |
| 583 | Debug/Step Over Source Line | 单步跳过（**实测 2.5 µs/步**） |
| 584 | Debug/Step Into Source Line | 单步进入 |
| 585 | Debug/Step Out from Source Line | 跳出 |
| 586 | Debug/Run To Source Line | 跑到光标行 |
| 589 | Debug/Reset Persistent Model Data | 连器件记忆一起清（会弹模态框） |
| 590 | Debug/Configure Diagnostics | 诊断档位 |
| 56 | Tool/ASCII Data Import Tool | ADI 改属性的底层通道 |
| 59 | Tool/Netlist Compiler | 编译 SDF 网表 |

其余常用：`File/Export Graphics/*`（22-28 导出图片/PDF/SVG）、`Graph/*`（560-569）、
`Tool/Electrical Rules Check`（57）、`Tool/Model Compiler`（60）。

---

## 5. 怎么用

```python
import sys; sys.path.insert(0, r"<repo>\python")
import papi

s = papi.open_sim(r"<proj>\demo.pdsprj", r"<proj>\firmware.hex")
try:
    s.set_traces()                      # 必做：TRACE_GPIO=3，否则日志是空的
    print(s.netlist()["parts"].keys())   # 10 个元件
    s.run_exact(0.3)                     # 精确推进 0.3 s（连续调用不会再超时）
    s.run_exact(0.3)
    s.start(); time.sleep(2); s.pause()  # 自由跑 2 s
    events = s.gpio(port=2, pin=13)      # 堆内提取，305 条里的 PC13 子集
    print(len(events), s.status()["simulation_seconds"])
    s.stop()
    s.cmd(papi.CMD_START_DEBUG)          # 调试模式
    print(s.step(4, papi.CMD_STEP_OVER)) # 每条指令约 2.5 µs
    print(s.reset_persistent()["state"]) # 模态框自动点 Yes
finally:
    s.close()
```

验收脚本：`（作者本机的验收脚本，未随仓库发布）`（7 项全过才算通）。

---

## 6. 一句话结论（旧）

**仿真控制层（`papi`）已经可以完全由 AI 驱动**：打开工程、绑固件、开诊断通道、
精确推进、自由运行、暂停/停止、单步到指令级、清器件记忆、读日志与 GPIO 事件、存盘，
全部有实测证据，且 4 个坑都有绕法。

---

## 7. 离线编辑层：读→改接线→改参数→存盘→验证 闭环已全通

### 7.1 模块清单（全部纯 Python，不碰 GUI、不碰仿真引擎）

| 模块 | 类/函数 | 能做什么 |
|---|---|---|
| `proteus_project` | `Project(path)` | 解析 `.pdsprj`（zip）里的 `ROOT.DSN`+`ROOT.CDB`，**双向交叉校验**每个元件的 ref/value/device/properties |
| | `.components()` | ref、value、device、symbol、坐标、旋转、完整 properties 字典 |
| | `.set_value(ref, v)` | 改 value —— **字节长度必须不变**（原位覆写） |
| | `.move(ref, dx, dy)` | 平移符号+4 个文字锚点（导线不动，等于把线扯断） |
| | `.save(path)` | 写新工程，**拒绝覆盖任何已存在文件** |
| `component_codec` | `Circuit.open(path)` | 打开并**重建**整张原理图（元件+导线+终端+电源轨） |
| | `.connect(REF.PIN, REF.PIN)` | 加连接（自动布线，或传 `points` 指定正交折线） |
| | `.disconnect(first[, second])` | 删连接 |
| | `.set_route(first, second, points)` | 改某条线的走线折点 |
| | `.update/move/rotate/mirror` | 改值/属性/坐标/旋转/镜像，**自动重算挂着的自动布线** |
| | `.set_properties(ref, **p)` | 改器件属性（`PROGRAM` 固件路径、`RPM`、`PPR`、`LOAD`、`VNOM`…），**长度可变** |
| | `.add / .copy_component / .rename / .delete_component` | 增删改元件 |
| | `.add_terminal / .update_terminal / .delete_terminal` | 电源/地/标签终端 |
| | `.import_device(name, donor_project=…)` | 从库或 donor 工程导入器件定义+图形 |
| | `.nets(physical=True)` / `.pins(ref)` / `.connections()` | 查网络/引脚坐标/连接表 |
| | `.save(dest, title=, overwrite=True)` | 存盘（可覆盖），传 `overwrite=True` 才能盖已有文件 |
| `net_objects` | `decode_net_objects(project)` | 纯解码：`wires/nets/terminals/junctions/connections/complete/rebuild_complete` |
| | `terminal_record / label_record` | 终端/标签记录编码 |
| `device_library` | `Library()` / `.search(q)` / `.get(name)` / `.definition(name)` | 直接读 `D:\Proteus\LIBRARY\*.LIB`，返回 pins、models、defaults、sections、offset |
| `pin_geometry` | `pin_position(dsn, symbol, pin, …)` | 符号引脚在原理图上的绝对坐标（本机 STM32F103R6 解出 **57 个引脚**） |
| `measurements` | `parse_graph_csv` / `sample` / `generators` / `set_generator_properties` | 解析 graph CSV（实测 0.15→3.3、0.05→1.65）、信号源属性读写 |

### 7.2 唯一的硬门槛：`Circuit.open` 要求导线几何"干净"

`Circuit.open()` 读完会调 `_validate()`，而 `_validate()` 会**把整张图的导线重新编码一遍**
（`encode_net_body → wire_record`）。所以只要原工程里存在一条
**零长线段或斜线段**，就直接抛：

```
proteus_project.UnsupportedFormat: Use nonzero orthogonal wire segments
```

本项目 `l130_final.pdsprj` 就踩了这个：M2 编码器的两条线点序被打乱
（`[A, B, C, D, E]` 变成 `[A, C, B, D, E]`，5 点都在、首尾都对，只是顺序错），
重编码时被判成斜线。

> 这是**纯编码层面**的问题，电气上仍然导通 —— 老数据里 `spd2` 一直是正常值。
> 但它让"离线可编辑"这条路彻底堵死，属于必须修的那类脏数据。

**10 行代码的体检器**（建议每次改工程前先跑）：

```python
from proteus_project import Project
from net_objects import decode_net_objects
g = decode_net_objects(Project(path))
print(g["complete"], g["rebuild_complete"], g["unknown_ranges"], g["missing_wire_references"])
bad = 0
for c in g["connections"]:
    for a, b in zip(c.get("points") or [], (c.get("points") or [])[1:]):
        if a == b or (a[0] != b[0] and a[1] != b[1]):
            bad += 1; print("DEGENERATE", c["first"], "<->", c["second"])
print("degenerate segments =", bad)
```

`bad == 0` 且 `complete/rebuild_complete == True` → `Circuit.open` 必然成功。

### 7.3 端到端实测：纯代码造出能跑的工程

脚本 `off_build_pid3.py`（离线）→ `off_to_online.py`（上线）：

1. `Circuit.open(l130_v2_fw.pdsprj)` —— 该文件几何干净，一次打开（10 元件 / 17 连接 / 22 终端）
2. `disconnect(("U1","PB7"),("M2","Q2"))` + `disconnect(("U1","PB6"),("M2","Q1"))`
3. `connect(("U1","PA7"),("M2","Q2"))` + `connect(("U1","PA6"),("M2","Q1"))` —— 把 M2 编码器改到 pid3 固件要求的 PA6/PA7
4. `set_properties("U1", PROGRAM=r"<proj>\firmware.hex")`
5. `save(r"<proj>\clean.pdsprj", overwrite=True)`
6. 复核：`complete=True rebuild=True comps=10 wires=40 degenerate=0`，新网络
   `('M2','Q1')<->('U1','PA6')`、`('M2','Q2')<->('U1','PA7')` 都在，`Circuit.open` 再打开也成功
7. **上线仿真**：`papi` 打开 → `set_traces` → `run_exact(8.0)` → 堆内取 1270 条 PC13 记录
   → 索引式帧同步 **28/28 = 100%** →

```
  t(s)    spd1 spd2 duty1 duty2
   0.290     7    7   200   200
   0.571    28   28   153   153
   3.380    36   35   115   127
   5.908    37   37   130   130      <- 稳态（目标 40）
   6.189    34   33   163   200      <- 扰动窗口开始（15000 拍 = 6.0 s，ENA/ENB 拉低）
   6.751    20   20   200   200      <- 掉速到 20，duty 打满
   7.593    40   41   141   140      <- 0.9 s 内回到目标
```

两路速度全程一致、扰动后 0.9 s 恢复、占空比回落到 141 —— 这套数据是**从 AI 一行代码
造出来的工程里跑出来的**，没有人工在 GUI 里点过一下。

### 7.4 离线层的能力边界（别踩）

| 限制 | 说明 | 绕法 |
|---|---|---|
| `Project.set_value` 长度锁死 | value 字符串字节数必须不变（`"22k"→"47k"` 可以，`"22k"→"470k"` 不行） | 用 `Circuit.set_properties/update`，它整块重编码，长度随便 |
| `Circuit.open` 只吃 `FILEVER 840/847` 且**单张用户图纸 + 1 张默认图纸** | 多层图纸工程直接拒 | 无 —— 需先在 GUI 里拆图 |
| 需模板里有同 device 的元件 | 新建空工程没法凭空 add 器件 | `Circuit(template_project=…)` 指一个有该器件的工程 |
| `save` 默认不覆盖 | `overwrite=False` 时目标已存在就抛 | 传 `overwrite=True`（且源文件被外部改过会拒绝，需重开） |
| `move()`（Project 版）不挪导线 | 只移符号和文字锚点，线留在原地 | 用 `Circuit.move()`，它会把挂着的自动布线重算 |
| 改接线后**必须重开仿真进程** | 已在跑的 Proteus 不会热加载 | `stop()`/`close()` 后重新 `Session(path)` |

---

## 8. 总结论

**AI 已完全接入，且闭环成立**：

| 环节 | 通道 | 证据 |
|---|---|---|
| 读工程 | 离线 `Project` / `decode_net_objects` / `Library` | 10 元件、40 导线、65 网络、57 引脚、MOTORS.LIB 定位到 offset 26376 |
| 改接线 | 离线 `Circuit.connect/disconnect/set_route` | 重接 M2 编码器 4 次操作全成功，保存后复核网络正确 |
| 改参数 | 离线 `Circuit.set_properties`（长度可变）/ 在线 `Session.set_properties`（ADI） | `PROGRAM=pid3.hex`、`TRACE_GPIO=3` 均回读确认 |
| 存盘 | 离线 `Circuit.save(overwrite=True)` / 在线 `Session.save()` | 84 492 B 新工程，可被 `Project`+`Circuit` 双重打开 |
| 跑仿真 | 在线 `papi.start/run_exact/pause/stop` | `0.301→0.601→…→8.100`，超调 0.5–0.7 ms |
| 取数据 | 堆内提取 + 索引式帧同步 | 1270 条 PC13 记录 → 28/28 帧 100% |
| 兜底 | 140 条原生命令直发 `WM_COMMAND` | `MENU_MAP.md` |

**还没打通的只剩一项**：程序化创建模拟量 graph 窗口并导出 CSV
（`Graph/Add Traces` 在无 graph 窗口时静默无响应）。要在那上面看波形，目前仍需人工先建一个 graph。
数字通道（GPIO 位串遥测）已经完全替代了它，实际不影响闭环。

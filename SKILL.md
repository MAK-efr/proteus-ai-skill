---
name: proteus-cm3-sim-run
description: 诊断并修复 Proteus（8.16 SP3）中 STM32 Cortex-M3 / VSM 仿真"不推进、画面静止、run_for 超时、Fatal simulation error"的问题。当用户说"Proteus 仿真不动""仿真白屏/静止""run_for 一直超时""Digital breakpoint at time"或需要程序化生成并验证含 MCU 的 .pdsprj 时使用。核心结论：90% 的"静止"其实是固件里的 BKPT 0xAB 半主机断点把仿真暂停了。
agent_created: true
---

# Proteus CM3 仿真"不推进"诊断与修复

## 先用哪份资料（别在 476 行里瞎找）

| 你要做的事 | 看这里 |
|---|---|
| 环境能不能用 | 跑 `python tools/doctor.py -v`（一条命令出结论，别猜） |
| 某个器件/操作到底支不支持 | `docs/LIMITS.md`（9 条硬边界，含器件黑名单） |
| 想调库没包装的能力 | `docs/MENU_MAP.md` + `docs/menu_map.json`（140 条 command_id） |
| 想知道哪些接口能用/坏了/怎么绕 | `docs/API_MAP.md`（三层能力模型 + 逐接口实测状态） |
| 想核实某个结论的原始数据 | `docs/VERIFY.md`（每条结论的实验记录） |
| 只看结论 | 本文件；下面按**症状**和**层次**组织 |

本文件是"经验正文"，篇幅长是因为每条都附了实测数字。**排查时按症状跳读，不要从头读。**

## 第一原则：不要用截图/像素差当判据


暂停（paused）和静止（仿真没跑）在画面上**一模一样**。必须读原生状态：

```python
st = sim.status()      # dict
st["state"]            # running / paused / stopped
st["simulation_seconds"]
st["status_text"]      # "ANIMATING: 00:00:04.35 (CPU load 11%)" / "PAUSED: 0.002524625s"
st["reason"]           # 关键！断点原因原文
```

判断"是否在推进"只看 `simulation_seconds` 是否随墙钟增长。

## 最常见真因：BKPT 0xAB 半主机

`reason` 出现这行即确诊：

```
[U1_CM3CORE] Digital breakpoint at time 2.5ms (2.5ms elapsed) - Breakpoint 171
```

- `171 = 0xAB` → ARM `BKPT 0xAB` = **newlib semihosting**（`printf`/`_write` 未重定向）
- 典型位置：启动后 ~2.5 ms，**只出现一次**
- HEX 里搜字节序 `AB BE` 可定位（例：`0x08000616`、`0x08000640`）

**先做的一次性缓解**：暂停后再调一次 `start()` 就能越过，之后一路正常。

```python
sim.start()
while sim.status()["state"] != "running":
    sim.start()          # 跨过半主机断点，最多重试几次
```

**根治（改固件）**：
- 链接加 `-specs=nosys.specs`
- 自实现 `int _write(int fd, char *buf, int len)` 直写 `USART1->DR`
- 或彻底弃用 `printf`

## 定位方法：A/B 秒表探针

```python
s = Session(proj, executable=r"D:\Proteus\BIN\PDS.EXE")
sim = Simulation(s); sim.start()
for _ in range(6):
    time.sleep(0.5); print(sim.status()["simulation_seconds"], sim.status()["state"])
```

- 时间递增 → 正常
- 固定在某值 + `paused` → 读 `reason`

**分组对照**：
1. 官方样例副本（`D:\Proteus\SAMPLES\VSM for Cortex M3\STM32\STMCubeMX LED Blink\`）→ 应正常
2. 待测工程 → 若卡住，看 `reason`
3. **换干净固件**（无半主机）塞进同一电路 → 若正常，则电路无罪、固件有罪

第 3 步是最有价值的一刀，直接分开"电路问题"和"固件问题"。
判据：日志里有没有 `PAD x_xx is configured as OUTPUT`；全是 `INPUT` 说明固件没跑到 GPIO 初始化。

## 程序化生成工程的两个必备条件

用 `proteus-automatic-api`（上游 `kudoumakoto6523-design/Proteus_automatic_package`）时：

**1) 模板必须"规范化"，不要改 FILEVER 硬凑**

```python
# 正确：让原生 Proteus 自己另存一遍
shutil.copy2(SAMPLE, NORM)
s = Session(NORM, executable=PDS)
Simulation(s).set_firmware("U1", FW)   # 绑一份固件
s.save(); s.close()
c = Circuit(template_project=NORM)      # 此时 components() == []
```

**2) 电源必须整套配置**

```python
c.configure_power(
    {"GND": 0, "VCC/VDD": 3.3, "VEE": -3.3},
    {"VCC": "VCC/VDD", "VDD": "VCC/VDD", "VDDA": "VCC/VDD",
     "VSS": "GND", "VSSA": "GND"})
```

只写 `VDDA`/`VSSA` 而漏掉 `VCC/VDD` 主轨会报 "No power supply specified for net VDDA"。
日志出现 `Net VDD taken as alias for VCC` 说明绑定生效。

参考电路**不接晶振**，只接 `VDD/VBAT/VREF+/NRST → VCC`、`VSS/BOOT0 → GND`。
Proteus 晶振模型对 HSE 起振不可靠，固件若死等 HSERDY 会永久卡住 → 建议改内部 HSI。

## API 细节（易踩）

- `gpio_events(self, **filters)` 是**关键字**参数：`sim.gpio_events(ref="U1", port=0, pin=8)`
- `Session.set_properties("U1", TRACE_GPIO="3")` + `s.save()` 后重开，才有 GPIO 事件
- `status()` 的 `reason` 是唯一能拿到断点原因的字段
- `run_for()` 依赖"执行定时断点"；仿真正停在被固件触发的 BKPT 上时它**必然超时**，不是库缺陷
- `sim.log()` / `gpio_events()` 会**替换系统剪贴板**；VSM Studio 无独立日志窗口时会报 "Native Simulation Log command is unavailable"
- 本机 `D:\Proteus`，版本 `8.16.36097.0` 与作者验证基线一致；DLL 指纹不匹配会让 `run_for()` 拒绝

## 环境

- **先跑 `python tools/doctor.py -v`**。它逐项检查 Python 版本、14 个依赖模块、Pillow、
  `PDS.EXE`、器件库、`WINCORE.DLL` 指纹、`PROTEUS_PRJ`，退出码 0 才算能用。
  没有这一步，任何"脚本跑不起来"都无法区分是环境问题还是代码问题。
- Python ≥ 3.10，Windows。截图类工具另需 **Pillow**（`pip install pillow`）；纯文本判读不需要。

- **仓库自带全部 15 个 proteus 模块**，`import papi` / `import component_codec` 只要把 `python/`
  放进 `sys.path` 即可，不需要装任何 pip 包、不需要往 site-packages 拷文件。
- 写脚本时的标准开头（不要写死任何绝对路径）：

  ```python
  import sys
  from pathlib import Path
  sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
  from proteus_env import add_self_to_path
  add_self_to_path()
  import papi
  ```

- 路径解析统一走 `python/proteus_env.py`：`pds_exe()` / `library_dir()` / `default_project()`。
  分别对应环境变量 `PROTEUS_PDS` / `PROTEUS_LIBRARY` / `PROTEUS_PRJ`，不设则自动探测常见安装位置。
- `PDS.EXE` 由 `Session` 自动拉起，不要自己 spawn


## 验证契约（交付前必须分清）

| 状态 | 含义 |
|---|---|
| `constructed` | 文件写出来了 |
| `reopened` | 能重新打开 |
| `structurally_verified` | 元件/引脚/网表核对通过，**不代表能跑** |
| `runtime_verified` | 真跑过且观察到预期输出，不得与运行时错误共存 |
| `blocked` / `not_run` | 诚实标注，不算失败 |

**"文件能打开"绝不能写成"能跑"。** 这是本类问题最容易犯的错。

---

## "仿真极慢" ≠ "仿真卡死"：先量速率，别看截图

**教训（2026-11-13，L130 双电机 PWM 项目）**：连续多轮把 "慢 300 倍" 误判成 "求解器发散/卡死"，
据此得出"Proteus 电机模型不支持 PWM 调速"的错误结论，白花了大量时间。
**任何"卡住"的判断必须先有速率曲线。**

正确判据 —— 采样 `sim.status()["simulation_seconds"]`，看是否**单调前进**：

```
sim 增量恰好是 2^21 → 2^22 → 2^23 → 2^24 纳秒
```
这种"增量成倍"是求解器**正常放大步长**，说明收敛没问题；慢的是"每步的墙钟成本"。
真发散的表现是增量**越来越小**或长时间完全不变。CPU 70–99% + 时间单调前进 = 慢，不是死。

工具：`gh_rate.py <hex> <src.pdsprj> <秒>`（逐秒打印速率）、
`gh_scan.py <src> <秒> <hex...>`（多固件批量）、
`gh_multi.py <src> <秒> <hex...>`（**单会话内换固件**，见下"删除配额"）。

> 这三个是作者本机的实验脚本，**未随仓库发布**（依赖具体固件与工程）。要复现手法很简单：
> 核心就是"一个 `Sim` 里 `stop() → set_firmware() → start()` 换固件，不重开工程"，
> 外加逐秒采样 `status()["simulation_seconds"]` 画速率。见下方单变量法。


### 定位"慢在哪"的单变量法（本次真实奏效）
固定工程、固定容差，只改固件一处，一次会话内连跑：
1. 恒定驱动 + 循环内零 GPIO 写 → 0.777
2. ENA PWM 1 Hz / 10 Hz / 50 Hz / 100 Hz → 全部 0.0028（**频率无关**，直接排除"边沿开销"）
3. PWM 打到**完全没接线**的 PC13、驱动全程恒定 → 0.0029（**直接排除电机/L298/模拟侧**）
4. 看 `sim@0`（同样墙钟后的仿真时间）：慢固件全是 0.0109，快固件是 2.75
   → 触发点在**进入主循环之后**，不是某次翻转

结论方向：病根在**数字侧（CPU/GPIO/定时器寄存器访问）**，不是 SPICE 容差、不是电机模型。

## SPICE 容差的真实接口是注册表，不是对话框

`System/Set Simulation Options` 那个 `#32770` 对话框里的 Edit 是自绘字段，
`WM_SETTEXT` / `EM_REPLACESEL` **写了不生效**（文本仍是 `\x86=ABSTOL,U,-,%.10lg,-,0,0` 模板），
不必再折腾。直接改：

```
HKCU\Software\Labcenter Electronics\Proteus 8 Professional\ISIS\Simulator Options
```
REG_SZ 键（全量）：`ABSTOL RELTOL VNTOL GMIN TRTOL TRANGMIN TMIN TTOL CHGTOL
ITL1 ITL2 ITL4 GMINSTEPS SRCSTEPS NUMSTEPS TDSCALE TDLOWER TDUPPER
METHOD(GEAR) MAXORD PIVREL PIVTOL RSHUNT(1e12) BYPASS BADMOS3 TRYTOCOMPACT TEMP TNOM`

`gh_tolreg.py dump | apply fast|faster|extreme | restore <bak.reg>`。
**但实测：放宽 RELTOL/ABSTOL/VNTOL/GMIN/TRTOL/TRANGMIN/ITL4 对"每步墙钟成本"毫无作用**
（0.002 → 0.002）。所以别把它当万能药；改完记得 `restore`，别给用户留精度隐患。

## 每轮删除配额 50 次（会反复中断实验）

- 超限抛 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`（`scope: turn`），**整轮实验被打断**，且当轮不再恢复
- **Proteus 每次启动都会建一个 `api-*.sdf` 再立刻删掉** → 一次实验至少吃 1 次配额
- 一个 turn 内最多跑约 4 次 Proteus
- 对策：① 批量实验写成**一个会话内换固件**（`sim.stop()` → `reset()` → `set_firmware()` → `save()` → `start()`），
  见 `tools/` 里批量实验的写法；② 每次在**全新空目录**（如 `%TEMP%\proteus-<tag>`）启动，别复用旧目录；
  ③ 结束用 `session.process.kill()`，**不要**走 `close_session()` 的 File/Exit（会触发 Proteus 批量清理）
- 给目录加 `icacls /deny user:(DC)` **无效**，Proteus 照样删得掉，别浪费时间去试

## 强杀 Proteus 后必现的启动阻塞

下次启动弹「是否恢复自动保存的工程?」`#32770`，会让 `Session.__init__` 一直 `TimeoutError`。
已给 `proteus_session.py` 的 `launched()` 打补丁：识别 `autosave` / `自动保存` 并自动点 `IDNO(209)`。
换机器/重装 venv 后需要重新打这个补丁。

## 别把"读数工具坏了"当成"模型坏了"

`"L130TELE"` 这类 magic 会同时出现在 **Flash 字面量池**里；扫 RAM 时必须挑活的那份
（stage ≤ 0xFF 且 loops 最大）。另外遥测要**每圈无条件写**，加 `loops & 0x3FF` 掩码会得出"全 0"，
进而误判"CPU 没跑"。遥测读数为 0 ≠ 固件没跑。

## ★ 关键突破（2026-11-13）：Simulation Log 是"写日志"，不是"边沿日志"

### 三条实测硬事实
1. **每一次 GPIO 写都会产生一条记录**，与电平是否变化无关。
   验证：固件在 PC13 上每拍写一次（2.5kHz 节拍），3 秒内记录 7514 条 = 节拍数，一一对应。
2. **日志有硬容量上限，约 16 489 条**。超过之后同一进程内彻底不再记录任何 GPIO 事件
   （表现为"日志突然完全静默，但 sim time 继续推进"）。
3. 因此 **duty=100% 时 PWM 引脚恒高 → 没有任何写动作 → 日志静默**。
   **"日志静默"绝不能推断为"CPU 停了"**。这是本工程长期误判的根源。

### 推论：rate≈0.78~0.80 不能证明 CPU 在跑
`ANIMATING ... (CPU load 3~9%)` + rate 0.8 只是**实时节流**（按墙钟播动画）。
判断 CPU 是否执行只认引脚记录，不认速率、不认 CPU load。

### 可靠的遥测手段：把引脚当日志串口用
既然"写"必被记录，就在一根空闲引脚上**按位串行吐数据**：
- 1 bit = 40 拍（2.5kHz 下 = 16 ms，约 62.5 波特）
- 帧 = 8 个 0 + N×(起始位 1 + 16 位数据 LSB 在前)，帧间隔靠长 0 段区分
- 写次数控制在 ~100 次/秒，30 秒约 3 千条，安全低于 16 489 上限

解码（脚本 `gh_ser2.py`）：
1. 从堆里提取 PC13 的写记录（时间, 值），按时间排序；
2. 位周期取相邻时间差的中位数（实测 15.96 ms）；
3. 用 `k = round((t - t0)/period)` 填位槽；
4. **搜索帧相位 0..FRAME-1**，取"所有起始位都是 1"的那个相位；
5. 4 帧起始位全过即判定为有效帧。实测 21/21、25/25 全过。

### 两个必踩的坑
- **周期性的控制窗口不能打断正在发送的帧**：若每 200ms 就 `frame=0; gap=8` 强制重启，
  一帧永远发不完，解码全错（表现为数值像被移位、duty>上限）。
  正确做法：窗口只更新待发寄存器，发送器在帧边界自己取新值。
- **设定值必须显著低于满速上限**：满占空比空载转速先测出来（本工程 = 53 计数/200ms 窗口）。
  设定值超过它 → duty 永久顶满 → 看起来"闭环无效"，其实是饱和。

### 配套探针
- `cal_freq.c`：在 PC13 上按 1/2/5/10/20/50/104/208 Hz 分 8 段输出，直接标定"日志能记多快、
  能记多少条"，一次运行即可测出容量上限与折叠现象。
- `gh_swp.py`：按 3 秒分箱统计实测边沿数 vs 理论值。

---

## ★★ 取证方法：见 `mcu-gpio-telemetry` 技能

"仿真里程序好像死了/没有输出/闭环不工作"这类问题的**完整取证流程**（通道体检 → 位串遥测设计 →
索引式帧同步解码 → 单位与量化判读）已独立成技能 **`mcu-gpio-telemetry`**，含可复用脚本
`telemetry_plan.py`、`decode_gpio_serial.py`。本技能只保留 Proteus 侧的硬数字：

| 事实 | 实测值 |
|---|---|
| 日志粒度 | **每一次 GPIO 写记一条**，与电平是否变化无关（2.5kHz 节拍 3s 记 7514 条） |
| 容量上限 | 累计 **16 488** 条后同一进程内彻底停记，仿真时间继续推进 |
| 记录范围 | **只为接到网络上的引脚记录**；悬空脚 0 条（PA8 接网 665 / PA4 悬空 0） |
| 节流 | `rate≈0.78~0.80`、`CPU load 3~27%` 只是按墙钟播动画，**不能证明 CPU 在执行** |
| 模板工程 | 原始 `.pdsprj` 可能出现"能打开但线网不导通"（`ROOT.DSN` 差 60 字节，全在 WIRE 指针区）→ 用"跑过一次后另存"的副本做模板 |
| 参数读取 | `.pdsprj` 是 zip，`ROOT.DSN` 里器件属性明文，如 `{RPM=360} {PPR=24} {VNOM=12V}` |

三条必守：① 控制窗口不得打断在途帧；② 设定值必须显著低于满输出时的物理上限，否则表现为"闭环无效"；
③ 探针引脚必须接真实网络，否则"没数据"和"没执行"无法区分。

---

## ★★★ 自动化接口层（2026-11-13 逆向实测）：能用的、不能用的、怎么绕

可用层封装见本仓库 `python/papi.py`（`papi.Sim` 一行打开，`Sim.run_exact()` 处理下文的
"冻住"病理，`Sim.read_log()` 绕开不弹的右键菜单）。菜单命令表用 `Sim.cmd(id)` 直接发，
库没包装的能力都能调。

### 版本门槛（先查这一个，再看别的）
`Simulation` 读状态栏靠只读 WINCORE 内存，要求 `<Proteus 安装目录>\BIN\WINCORE.DLL` 的
sha256 == `944013e93f0d31addbbce797d6eb3746bf06c7497c0d04cb77955fc0acd569bb`。
不匹配 → `status_text`/`simulation_seconds`/`run_for` 全部退化为 `NotImplementedError`，
只剩菜单状态可用。先算哈希，再谈其它。

### 四个必踩的坑（都实测过，都有绕法）
1. **`run_for()` 只有第一次收敛。** 第二次下定时断点，引擎在目标时刻冻住：仿真秒数不动、
   `CPU load` 掉到 0%，但 `state` 仍报 `ANIMATING`、`reason` 为空 → 库等满 60 s 抛
   `TimeoutError`。绕法：自己下断点后轮询「到达目标 且 秒数不再变化」→ 主动 `pause()`。
   （再次印证：`state` / `CPU load` / 秒数三个字段单独看都会骗人。）
2. **`log()` / `gpio_events()` / `errors()` 全挂。** 它们给日志窗口发 `WM_CONTEXTMENU` 等
   `QPopup` 弹菜单，本机不弹 → 15 s 超时。绕法二选一：① 堆内提取（读
   `#T:<文本>\x00%T=<秒>\x00%S=<源>\x00%E=<代码>\x00` 记录，不依赖窗口）；
   ② `SetForegroundWindow` + `SendInput` 发 `Ctrl+A`/`Ctrl+C`（**必须真实键盘，
   对容器句柄发 `WM_COPY` 无效**）。前提：先把 `TRACE_GPIO` 设成 `'3'`，否则日志几乎是空的
   （实测未开时只有 67 字符，开了 17 640 字符）。
3. **进了 VSM 调试模式后 `start()` 失效**（它只发 576）。绕法：`576 → 579 → 577` 依次试。
4. **`Debug/Reset Persistent Model Data`(589) 会弹模态框**，不点掉整个会话变 `blocked`。
   必须轮询 `#32770` 点 `&Yes`。

### 单步：能做到指令级
`Debug/Start VSM Debugging`(579) 进调试模式，之后 `Step Over Source Line`(583) 每步推进
**约 2.5 µs**（实测 0.002515→0.002518→0.002520→0.002523→0.002526）。
`Animated Single Step`(587) 下发成功但**时间不动**，别用它。
单步后要留 ~0.25 s 再读状态，0.05 s 读到的还是旧值。

### 兜底：原生菜单命令表
库没包的能力只要菜单里有，就能按 `command_id` 直接发 `WM_COMMAND`。本机导出 140 条
（见 `docs/MENU_MAP.md` 与 `docs/menu_map.json`），关键号：576/577/578 运行、579 调试、581 暂停、582 停止、
583-586 单步与跑到行、589 清器件记忆、56 ADI、59 编译网表。


### 还没打通
`Graph/Add Traces...` 在**没有 graph 窗口**时静默无响应（0 个新窗口、无异常），
所以"程序化造模拟量轨迹 → 导出 CSV"这条路仍需人工先建 graph 窗口。
`press/release/set_switch` 依赖 `Circuit.bind_controls()` 预绑定，未验证。

---

## ★★★ 离线编辑层（2026-11-13 实测全通）：不碰 GUI 改工程

`proteus_project` / `component_codec` / `net_objects` / `device_library` / `pin_geometry`
是**纯 Python 的 .pdsprj 编解码器**，可以完全不启动 Proteus 就读改工程。

- `Project(path)` —— 只读解析，`.components()` 给 ref/value/device/坐标/全部 properties
- `Circuit.open(path)` —— 打开并重建整张图，之后可
  `connect/disconnect/set_route/update/set_properties/move/rotate/mirror/
   add/copy_component/rename/delete_component/add_terminal`，最后
  `save(dest, overwrite=True)`
- `Library().search("MOTOR")` —— 直接读 `D:\Proteus\LIBRARY\*.LIB`

### ⚠️ 唯一的硬门槛：导线几何必须是纯正交的
`Circuit.open()` 读完会调 `_validate()`，而它**把整张图的导线重新编码一遍**。
只要原工程里存在一条**零长线段或斜线段**，立刻抛
`UnsupportedFormat: Use nonzero orthogonal wire segments` —— 工程打不开、也就没法离线改。

**所以任何离线编辑之前，先跑体检器**（本技能自带）：

```bash
python scripts/proteus_netcheck.py 你的工程.pdsprj [更多工程...]
# 退出码 0 = 可离线编辑；1 = 有退化导线，并会打印具体是哪几条线、点序错在哪
```

真机踩过：某工程的 M2 编码器两条线点序被打乱（`[A,B,C,D,E]` → `[A,C,B,D,E]`，
5 个点都在、首尾都对），重编码时被判成斜线。**电气上照样导通**（老数据里读数正常），
纯粹是编码层脏数据。修法：拿一个几何干净的同类工程当模板，`disconnect` 后 `connect` 重放那几条线。

### 别踩的边界
| 限制 | 绕法 |
|---|---|
| `Project.set_value` 长度锁死（`22k`→`47k` 行，`22k`→`470k` 不行） | 改用 `Circuit.set_properties/update`，整块重编码，长度随意 |
| `Circuit.open` 只吃 `FILEVER 840/847` + 单张用户图纸 + 1 张默认图纸 | 无，需先在 GUI 拆图 |
| 新建空工程不能凭空 `add` 器件 | `Circuit(template_project=…)` 指一个有该器件的工程 |
| `save` 默认不覆盖 | 传 `overwrite=True`；源文件被外部改过会拒绝，需重开 |
| `Project.move()` 不挪导线（等于扯断） | 用 `Circuit.move()`，会重算自动布线 |
| 改完接线，**已经在跑的 Proteus 不会热加载** | `stop()`/`close()` 后重新 `Session(path)` |

### 完整闭环验证过的路径
`Circuit.open(干净工程)` → `disconnect/connect` 改接线 → `set_properties("U1", PROGRAM=新hex)`
→ `save(overwrite=True)` → 体检器复核 `degenerate=0` → `papi` 打开运行 →
堆内提取 + 帧同步解码 → 数据正常。

**结论：读工程 → 改接线/改参数 → 存盘 → 跑仿真 → 取数据，全链可由 AI 独立完成。**

---

## ★★★★ 2026-11-13 万年历项目补充（本机实测，别再重复踩）

### 1) `connect(points=...)` 有引脚出口方向约束
`wire_codec.py:239` 会同时校验首末两段：
```python
_direction(points[0], points[1]) == first["direction"]
_direction(points[-1], points[-2]) == second["direction"]
```
即**导线离开/进入引脚的方向必须等于该引脚的朝向**。给电阻横放却想从下方进线，会报
`Wire violates a pin exit direction`。解法：把电阻 `rotation=90` 竖放，或用 3 段折线让最后一段水平/垂直地迎着引脚方向进入。

### 2) 整页缩放用原生命令 331，不要用 F8
`keybd_event(VK_F8)` 在本机**不可靠**（截到空白画布）。枚举菜单得到
`View/Zoom To View Entire Sheet = 331`，用 `papi.Sim.cmd(331)` 稳定。
菜单枚举：`for r in papi.Sim(prj).menus(): print(r["command_id"], r["path"])`。

### 3) Simulation Log 是 Qt `QTool`，没有 Win32 子控件
`native.windows()` 返回 `children: []`，`WM_GETTEXT` 读不到内容；
`read_log()` 走剪贴板时可能抓到别的程序文本。**取证优先级：
`gh_tx.extract(pid)` 堆内提取 > 剪贴板 > 窗口控件**。
注意 `papi.gpio(port=...)` 的 port 是 **int**（A=0/B=1/C=2），传 `"C"` 会过滤掉全部事件。

### 4) "编译成功"不等于"芯片能启动"：每份 hex 都要验向量表
`armcc -g` + `armlink --debug` 且无 scatter 时，链接器会把 `Reset_Handler` 代码放到
`0x08000000`、把 RESET 向量区挤到别处，芯片 SP/PC 读到乱值，表现是**外设上电但固件完全没跑**。
实测：`SP=D00CF8DF RESET=FA44F000`（坏） vs `SP=20002800 RESET=0800060D`（好）。
修复：给 `--scatter=...sct`（含 `*.o (RESET, +First)`）、去掉 `-g/--debug`，
并在构建脚本里断言 `0x20000000<=SP<=0x20005000` 且 `0x08000000<=PC<0x08008000 && PC&1`。

### 5) 离线重画整张图时
- `LM016L` 引脚相对元件原点是负偏移（VSS = origin-2032000，RS = origin-1016000），不要想当然按"原点在左上角"算。
- `c.update(ref, label_offsets={"reference":(dx,dy), "value":(dx,dy)})` 可把型号文字挪出 LCD 玻璃，否则压住显示内容。偏移是**相对元件原点**。
- 走线零交叉可脚本枚举验证：把每段拆成水平/垂直，两两判 `x1<x<x2 && y1<y<y2`。

### 6) 截图取证
`PrintWindow(hwnd, hdc, 2)` + `GetDIBits` 拿整窗（本机 2906×1730），再 `crop.py` 局部放大读 LCD 文字。
`zoom_all` 之后**必须重截**，否则裁的是旧视图。


---

## 2026-11-14 追加：让"取证"这一步不再假死

前面几节解决的是"仿真不动"。还有一种更坑的：**仿真动了、但你的脚本卡住了**。
实测症状是 `shot_lcd.py` 跑 11 分 44 秒零输出、PDS 进程消失、没有任何图落盘，只能强杀。
拆开看是四层原因叠在一起，逐条修掉：

### ① `run_exact()` 不看引擎死活（`python/papi.py`）

旧循环只比 `deadline` 和 `target`。PDS 退出后 `simulation_seconds` 恒为 `None`，
于是空转到调用方给的 `timeout`（`shot_lcd.py` 传 1800 秒）。

```python
while time.monotonic() < deadline:
    st = self.status()
    if st["state"] in ("exited", "stopped"):      # ← 新增守卫
        raise RuntimeError("run_exact(%s) 中止：引擎状态=%s ..." % (seconds, st["state"]))
```

修后：打桩成 `exited` 后 **0.12 秒**抛错（旧版 1800 秒）。

### ② 脚本不关 PDS → 下一轮撞模态框（`tools/shot_lcd.py`）

`main()` 结尾没有 `s.close()`，PDS 残留后台，下一轮启动撞"是否恢复自动保存的工程?"，
弹框之后所有菜单命令超时。改成 `finally` 里无条件关。

### ③ `Sim.save()` 会把工程写脏

实测 `t8.pdsprj` 每存一次 **+12 字节**；反复保存后 PDS 弹 `Reports Uploading` 崩掉，
或下次打开直接自动起跑（`set_firmware` 报 `Stop simulation before configuring firmware`）。
截图流程里**不要 save** —— `set_traces()` 走 ADI 导入，当次会话立即生效。

### ④ 开局先清 `.workspace`

PDS 关闭/崩溃时会把"上次正在仿真"写进 `<工程名>.<机器名>.<用户>.workspace`。
下次打开要么自动起跑要么直接崩。启动前删掉：

```python
for stale in glob.glob(os.path.join(os.path.dirname(prj), os.path.basename(prj) + ".*.workspace")):
    os.remove(stale)
```

### 兜底：整轮重试

PDS 8.16 SP3 约 **1/2** 概率在"启动后刚推进仿真"时自杀（退出码 1）。这不是调用方的错，
重来一次就好。`shot_lcd.py` 现在整轮重试 3 次，实测连续 3 轮 **3/3 出图**，
其中第 3 轮正是被重试救回来的。

### 诊断口诀

> 卡住 → 先枚举窗口：有 `#32770` 或 `Reports Uploading` 就是弹框，点掉再重试；
> 没弹框但 `state=exited` → 引擎死了，看 `returncode`；
> `state=paused` 且 `reason` 有 `Breakpoint 171` → 半主机断点，`start()` 再来一次。

---

## 2026-11-14 追加二：graph 与可控器件的实测结论

### `Graph/Add Traces` 没有 graph 时是静默空操作

发 `WM_COMMAND 562` 后 1/2/3 秒新增窗口数恒为 0，不报错。菜单项也不置灰，特别容易误判成"超时"。

graph **无法程序化创建**：不在器件库、没有菜单入口、官方样例是 CDB v8（codec 读不了）、
只能从工具栏放而工具栏 id 拿不到。

⇒ 用**模板工程**：在 BASE 工程里手工放一次 `Analogue Graph`，之后 `Circuit.open(BASE)`
重建出来的每个工程都自带 graph，`papi.add_traces()` / `export_csv()` 就自动了。
`add_traces()` 现在会预检并立即报错，不会静默。

### `press/release/set_switch` 必须预先 `bind_controls`

未绑定：`RuntimeError: B1: use Circuit.bind_controls() before saving and opening the project`
（而且必须写在 `save()` 之前）。
已绑定实测：`BUTTON idle=0 press=1 release=0`、`SWITCH closed=1 open=0`。
`controls()` 的 `bound` 字段可当预检；`kind` 区分 `momentary` / `latched`。

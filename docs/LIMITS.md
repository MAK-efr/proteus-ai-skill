# 硬边界 —— 别在这上面浪费时间

以下全部在 Proteus 8.16 SP3（8.16.36097）+ Windows 11 上**实测确认**，不是传闻。踩之前先看一眼。

---

## 1. 一个设计里只能放一片 STM32（Cortex-M3）

把**已验证能跑**的工程复制一份，只多 `add` 一片 `STM32F103R6` 并接上 VDD/VBAT/VREF+/VSS：

- 两片的 `set_firmware()` **都成功**，`status` 也还是 `stopped`
- 但 `start()` 三次全部失败，最终 `state=exited, returncode=1`

对照：同样的流程、单 MCU 的工程都能跑到指定时刻。

⇒ **"主机 + 从机 / RS485 双机通信"这类结构在本机不可能仿真。** 只能拆成两个工程分别跑，或者在单 MCU 上做"两片 MAX487 自发自收"来演示驱动/接收逻辑。

⚠️ 另外：用"删器件隔离"得到的工程如果还是 `rc=1`，那是 `disconnect/delete` 留下了残缺网表，**不能当成"删掉它就正常"的证据**。

---

## 2. 数字温度器件全军覆没

| 器件 | 现象 | 根因 |
|---|---|---|
| **DS18B20**（1-Wire） | presence 永远读不到，数据恒 `0xFF` | 本机 CM3 模型一条 C 语句 ≈ **17 µs 仿真时间**，最短可发脉冲 ≈ **94 µs**；1-Wire 需要 1~15 µs |
| **DS1620**（三线，带 CLK） | 两个采样相位都只读到上拉，模型**不驱动 DQ** | 模型实现问题 |
| **LM75BD**（I2C） | 保存后器件记录损坏，`set_firmware` 直接把 PDS 搞崩（弹 `Reports Uploading`） | 器件记录写坏 |
| **DS1621**（I2C） | 能打开、能设 PROGRAM，但 `start()` 直接 `rc=1`；换**完全不碰 I2C** 的最小固件同样崩 | 问题在器件，不在固件 |

**结论：用 LM35 这类纯模拟传感器 + STM32 内部 ADC。** 模拟量由模型直接仿真，不受指令粒度影响。

> DS1620 那段顺带挖出一个**真实的 STM32 编程陷阱**：切换「输入 + 上拉/下拉」是由 **ODR 对应位**决定方向的，`ODR=0` 时切输入等于开了内部下拉（约 40 kΩ），会把"放手=高"摁成 0。必须先 `GPIOC_ODR |= BIT;` 再写 `CRH`。

---

## 3. 会把「器件记录写坏」的器件名

加入这些器件后，PDS 打开就弹模态框（内容是器件记录被错位读取），必须避开：

| 器件 | PDS 弹出的原文（节选） |
|---|---|
| `MMBTA63` | `Device 'CKAGE=SOT95P230X109-3}' used but not in library.` |
| `LAMP` | `Device 'LUE=HEATER}\n{LOAD=24Ohms}\n{MODFILE=LAMP}\n' used but not in library.` |
| `LED-RED` / `LED-YELLOW` | 加入后 PDS **秒退**，`Sim` 拿不到会话 |
| `MMBT3904` | 能 add，但 PDS 打不开 |
| `VSOURCE` / `PCF8591` | `Device 'FMOD=CMOS}\n{MODFILE=PCF8591}\n{PACKAGE=DIL16}\n' used but not in library.` |

**可以放心用**：`BC547`（替代 PNP 达林顿 `MMBTA63`）、`RELAY`、`LM35`、`LM016L`(LCD1602)、`RES`、`STM32F103R6`、`MAX487`、`BUTTON`、`SWITCH`、`SOUNDER`、`MCP3202`、`POT-HG`。

---

## 4. 器件库里根本没有的名字

`CRYSTAL`/`XTAL`、`CAP`/`CAP-ELEC`/`CAPACITOR`、`RESPACK-8`/`RESPACK`、`BUZZER`/`SPKR`、`POT`、`LM75`/`DS1775`/`MCP9808`、`2N3904`、`ADC0808`、`ADC0804`。

STM32 的 VSM 模型靠属性 `OSC` 取时钟，不需要外部晶振也能仿真，所以缺晶振不影响功能。

---

## 5. PDS 自己会崩，这是常态不是你的错

- **约 1/2 概率**在"启动后刚推进仿真"时自杀，退出码 1，再启动会弹 `Reports Uploading` 报告框并把主窗口置灰。
- 一旦出现任何模态框（崩溃报告 / 恢复自动保存 / 属性对话框），**之后所有菜单命令都会超时** —— 表现为"卡死"。
- `Sim.save()` 会就地改写 `.pdsprj`（实测每次 **+12 字节**），反复保存后会触发上面的崩溃，或让下次打开直接自动起跑（`set_firmware` 报 `Stop simulation before configuring firmware`）。

**处置顺序**：① 先枚举窗口找 `#32770` / `Reports Uploading`，有就点掉；② 杀掉 `PDS.EXE`，删掉 `<工程名>.*.workspace`；③ 重试。通常一轮就好。

---

## 6. 走时不准：`CLOCK_SCALE` 属性

STM32 模型带 `CLOCK_SCALE` 属性（`Off` / `4 Times` / `8 Times` / `16 Times`），它乘的是**指令退役速率**而不是时钟频率。

- 实测某万年历工程（SysTick 按 8 MHz 设计）走时**快约 10.8 倍**，根因就是 `{CLOCK_SCALE=8 Times}`。
- 改成 `Off` 反而变成**约 81 倍速**（说明 "8 Times" 在这个方向上其实是"放慢 8 倍"）。
- 想靠软件分频（`TICK_DIV`）修正：反汇编确认常量已经编进 hex，但仿真时间线逐帧不变 —— 怀疑 Proteus 运行时并未加载新镜像。

⇒ 只要你的功能依赖**真实时间**（秒表、日历、定时器），先把 `CLOCK_SCALE` 查清楚，别把它当成固件 bug。

---

## 7. `component_codec` 的布线规则（踩了必报错）

1. **多引脚网（≥3 引脚）不允许显式 `points` 走线**
2. 第一段和最后一段走线必须与引脚的出线方向一致
3. 不允许零长度线段
4. `c.move()` 是**绝对坐标**，不是增量

---

## 8. graph 对象无法程序化创建（`Add Traces` 静默空操作的根因）

`Graph/Add Traces...`（命令 562）在**工程里没有 graph 对象**时：

- 菜单项仍然 `enabled=True`（不会置灰，所以看不出问题）
- 发 `WM_COMMAND` 之后不弹对话框、不报错、**1/2/3 秒后新增窗口数恒为 0**

四条造图的路都不通：

1. **不在器件库里** —— 全库 56694 个器件里 `GRAPH` 只命中 `LED-BARGRAPH-*`（那是 LED 条），
   没有 `ANALOGUE GRAPH` 这种图元。
2. **没有菜单入口** —— 139 条菜单项里没有任何 `New Graph` / `Place Graph`。
3. **克隆不了** —— 官方 `D:\Proteus\SAMPLES\Graph Based Simulation\*.pdsprj` 全是 CDB v8，
   我们的 codec 直接拒绝：`Requires CDB v7, one-sheet layout` / `Unsupported sheet objects` /
   `Unknown CDB component table`。
4. **只能从工具栏放** —— 而工具栏按钮的 command id 拿不到（菜单枚举里没有）。

**可行做法（模板工程）**：在作为 BASE 的工程里手工放一个 `Analogue Graph` 并保存。
由于所有工程都是 `Circuit.open(BASE)` 再加器件的，**之后重建的每个工程都自带 graph**，
`graphs()` / `export_csv()` 全程自动。这一步只需人工做一次。

`papi.Sim.add_traces()` 已加预检：没有 graph 就 0.00 秒抛一条写明出路的错，
不会再出现"点了但没反应"。

---

## 9. `press` / `release` / `set_switch` 必须 `bind_controls`，且必须在保存之前

对照实验（两个工程只差一行 `bind_controls`）：

| 工程 | 绑定 | `controls()` | 结果 |
|---|---|---|---|
| 未绑定 | — | 能列出 `B1`/`SW1`，但 `bound=False` | `RuntimeError: B1: use Circuit.bind_controls() before saving and opening the project` |
| 已绑定 | `c.bind_controls("B1","SW1")` | `bound=True` | `idle=0 press=1 release=0`；`closed=1 open=0` ✅ |

要点：
- 绑定必须写在 `c.save(...)` **之前**，打开工程之后补绑无效；
- `controls()` 的 `bound` 字段可以直接当预检；
- `kind` 会区分 `momentary`（BUTTON，按一下弹回）与 `latched`（SWITCH，保持位置）；
- 支持的器件：`BUTTON`、`SW-SPST`、`SW-SPST-MOM`、`SWITCH`、`LOGICSTATE`、`LOGICTOGGLE`。

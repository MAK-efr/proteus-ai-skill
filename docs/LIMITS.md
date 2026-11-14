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

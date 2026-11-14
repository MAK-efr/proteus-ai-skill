# proteus-ai-skill

让 AI（或任何脚本）**直接驱动 Proteus** 做完一整条闭环：改电路 → 编网表 → 跑 Cortex-M3 仿真 → 读屏/截图取证 → 断言 hex 与网表。

不靠屏幕坐标、不靠模拟点击：全部走 Proteus 的原生菜单命令 + 进程内存 + ADI 属性导入。所以它没有"点歪了"的问题，只有"Proteus 自己崩了"的问题 —— 后者在 `docs/LIMITS.md` 里写清楚了怎么识别和绕过。

本项目是把一台 Windows 机器上**反复踩坑后固化下来的规则 + 工具**整理成可复用的包，不是 Proteus 的官方 SDK。

---

## 一、它到底能干什么

| 能力 | 入口 | 已验证 |
|---|---|---|
| 用代码新建/修改 `.pdsprj`（放器件、连线、设属性） | `python/component_codec.py` | ✅ |
| 编译原生网表、导出网表做断言 | `proteus_session.export_netlist()` | ✅ |
| 控制仿真：启动/暂停/精确定时推进/单步 | `python/papi.py` `Sim` | ✅ |
| 判定"仿真到底在不在走" | `Sim.status()` | ✅ |
| 精确推进 N 秒并处理"冻住"病理 | `Sim.run_exact()` | ✅ |
| 抓仿真画面并自动裁出 LCD 区域 | `tools/shot_lcd.py` | ✅ |
| 不开截图、直接从 GPIO 事件流还原 LCD 文本 | `tools/lcdlog.py` / `tools/lcdgpio.py` | ✅ |
| 校验 Intel HEX 的向量表（SP/RESET 是否合法） | `tools/checkhex.py` | ✅ |
| 统计走线交叉、走线压到无关引脚 | `tools/crossings.py` | ✅ |
| 用 armcc + fromelf 出 hex 并顺手断言 | `tools/build_cal.ps1` | ✅ |

---

## 二、安装

前提：Windows + Proteus 8.16 SP3（8.16.36097）+ Python ≥ 3.10。

```powershell
# 1) 建环境
py -3.11 -m venv .venv
.\.venv\Scripts\activate
pip install pillow

# 2) 把 python/ 里的模块放进 site-packages（这些文件是"上游包 + 本机补丁"，见第六节）
Copy-Item .\python\*.py .\.venv\Lib\site-packages\

# 3) 自检
python -c "import papi; print('ok')"
```

如果你的 site-packages 不在默认位置，用环境变量指定，`papi.py` 会优先读它：

```powershell
$env:PROTEUS_PYSITE = "D:\py\venv\Lib\site-packages"
```

**必须按自己机器改的路径**（本项目是在一台固定机器上长出来的，路径写死过）：

| 常量 | 位置 | 说明 |
|---|---|---|
| `PDS.EXE` | `python/proteus_session.py` | 默认 `D:\Proteus\BIN\PDS.EXE` |
| 器件库 `LIBRARY` | `python/component_codec.py` / `proteus_project.py` | 默认 `D:\Proteus\LIBRARY` |
| 启动模板工程 | `python/proteus_session.py` | 默认官方 `Rescap.pdsprj` |

---

## 三、五分钟上手

```python
import sys; sys.path.insert(0, r"C:\path\to\proteus-ai-skill\python")
import papi

s = papi.Sim(r"C:\proj\demo.pdsprj", firmware=r"C:\proj\demo.hex")
s.set_traces("U1")         # 开 GPIO 诊断通道（必须在 stopped 状态）
s.start()
r = s.run_exact(2.0)       # 精确推进到仿真时间 2.0s 并停住
print(r["completion"], r["simulation_seconds"])
s.close()                  # 一定要 close，见第五节缺陷 B
```

截图取证（命令行）：

```powershell
python tools\shot_lcd.py C:\proj\demo.pdsprj C:\proj\demo.hex C:\proj\out 2 4 6
# 依次推进到 2/4/6 秒，每次整窗截图 + 自动定位橄榄绿 LCD 面板并放大裁剪
```

---

## 四、最重要的三条方法论

1. **不要用截图判断"仿真在不在走"。** 暂停和"根本没跑"在画面上完全一样。只认 `status()["simulation_seconds"]` 是否随墙钟增长，以及 `status()["reason"]`。
2. **90% 的"仿真不动"是固件里 `BKPT 0xAB` 半主机断点**（`printf` 没重定向）。`reason` 里出现 `Breakpoint 171` 即确诊。绕过：暂停后再 `start()` 一次；根治：`-specs=nosys.specs` + 自己实现 `_write`。
3. **`Proteus` 的模态框是致命的。** 一旦有弹框（崩溃报告、恢复自动保存、属性对话框），主窗口被置灰，之后所有菜单命令都超时。任何"卡住"先枚举窗口找 `#32770` / `Reports Uploading`。

---

## 五、2026-11-14 修的三个缺陷（都有实测数据）

这三个是"跑得通但会假死"的问题，也是最容易让人误判成"我的电路有问题"的地方。

### A. `run_exact()` 不看引擎死活 → 静默空转 30 分钟

旧实现只比 `deadline` 和目标秒数。PDS 中途退出时 `simulation_seconds` 恒为 `None`，于是空转到调用方传入的 `timeout`（`shot_lcd.py` 传的是 1800 秒）才返回，日志一片空白。

修法：循环里一旦 `state` 变成 `exited`/`stopped` 立刻抛 `RuntimeError`。

实测：把 `status()` 打桩成 `{'state':'exited','returncode':1,...}`，**0.12 秒抛错**（旧版要 1800 秒）。

### B. `shot_lcd.py` 不关 PDS → 下一轮撞模态框

`main()` 结尾没有 `s.close()`，PDS 残留在后台。下一轮启动会撞"是否恢复自动保存的工程?"，模态框一弹全链路卡死。

修法：`finally` 里无条件 `close()`，成功/异常/Ctrl+C 都关。

实测：连续 4 轮跑完，`tasklist` 里 `PDS.EXE` 均为空。

### C. `Sim.save()` 会把工程写脏

`save()` 会就地改写 `.pdsprj`（实测 `t8.pdsprj` 每存一次 **+12 字节**），反复保存后 PDS 会弹 `Reports Uploading` 崩溃退出，或者下次打开直接自动起跑（`set_firmware` 报 `Stop simulation before configuring firmware`）。

修法：截图流程里**不要 `save()`** —— `set_traces()` 通过 ADI 导入，当次会话立即生效，不需要落盘。

### 兜底：`shot_lcd.py` 现在会自动重试 3 次

PDS 8.16 SP3 有约 1/2 概率在"启动后刚推进仿真"时自杀（退出码 1）。这不是调用方的问题，重来一次就好。配合缺陷 A 的守卫，能在几秒内发现并自动重来。

实测：连续 3 轮，**3/3 出图**，其中第 3 轮自动重试 1 次后成功。

---

## 六、许可与出处

- 本项目自有代码（`python/papi.py`、`tools/*`、文档）：**MIT**，见 `LICENSE`。
- `python/` 下 `component_codec.py`、`wire_codec.py`、`proteus_session.py`、`proteus_native.py`、`proteus_project.py`、`proteus_automatic_api.py`、`simulation.py` 来自 **proteus-automatic-api 0.2.0**（MIT，Copyright (c) 2026 proteus-automatic-api contributors），**含本机适配补丁**，逐条差异见 `NOTICE`。
- 本项目**不分发** Proteus 软件、器件库或 Keil/armcc。

上手前请先读 [`docs/LIMITS.md`](docs/LIMITS.md)：那里列了"一个设计只能放一片 STM32""DS18B20/DS1620/LM75 全部不可用""会把器件记录写坏的器件名黑名单"等等 —— 这些都是花了很多时间才确认的，看一眼能省你半天。

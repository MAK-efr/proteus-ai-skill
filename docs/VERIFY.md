# 实测记录（2026-11-13 ~ 11-14）

这里只记**跑出来的数字**，不写"应该没问题"。

## 环境

- Proteus 8.16 SP3（8.16.36097），`D:\Proteus\BIN\PDS.EXE`
- Windows 11，Python 3.13 venv（`pillow` 唯一第三方依赖）
- 被驱动的工程：`t8.pdsprj`（STM32F103R6 + LCD1602 8 位 + 3×LM35 + 2 路继电器），固件 `lm35c.hex`

## 1. 正常链路有多快

| 步骤 | 墙钟耗时 |
|---|---|
| `papi.Sim()` 启动 PDS + 载入工程 | 6.0 s |
| `save()` | 0.1 s |
| `start()` | 0.6 s |
| `run_exact(2.0)` → 停在 2.05 s | 3.0 s |
| `cmd(320)` 重绘 + `cmd(331)` 全局缩放 | <0.1 s 各自 |
| 最大化 + `PrintWindow` + `GetDIBits` | 0.5 s |
| **全流程** | **11.0 s** |

仿真速度：墙钟 5 s ≈ 仿真 3.7 s。**"跑仿真慢"不是瓶颈。**

## 2. 缺陷 A（静默空转）修前/修后

- 修前：`shot_lcd.py` 跑到 **11 分 44 秒零输出**、PDS 进程消失、无 PNG 落盘，只能强杀。原因是 `run_exact(…, timeout=1800)` 空转到超时。
- 修后（把 `status()` 打桩成 `{'state':'exited','returncode':1,'simulation_seconds':None}`）：
  ```
  RuntimeError after 0.12s: run_exact(30.0) 中止：引擎状态=exited returncode=1 停在 None 秒（目标 30.000000）
  ```
- 另一条路径（外部 `taskkill` 掉 PDS）**3.2 秒**抛 `RuntimeError: Proteus status object could not be read`。

## 3. 缺陷 B（PDS 残留）修后

连续 4 轮结束后 `tasklist | grep PDS.EXE` **均为空**；修前第 3 轮探测后残留 `PDS.EXE pid 372`。

## 4. 缺陷 C（`save()` 写脏工程）

- `t8.pdsprj` 每 `save()` 一次 **86927 → 86939 字节（+12 B）**
- 反复保存后：PDS 弹出 `Reports Uploading`（崩溃）或下次打开自动起跑，`set_firmware` 报 `Stop simulation before configuring firmware`
- 去掉 `save()` 之后，`set_traces()` 依然生效（ADI 导入是即时生效，不需要落盘）

## 5. 自动重试最终验证

`shot_lcd.py` 连跑 3 轮，命令都是同一份工程同一份 hex：

```
run 1: t=2.00 sim=2.05 lcd box=(1658,236,2098,370)  -> 出图 OK
run 2: t=2.00 sim=2.05 lcd box=(1658,236,2098,370)  -> 出图 OK
run 3: attempt 1/3 failed: run_exact(2.0) 中止：引擎状态=exited returncode=1
       t=2.00 sim=2.0  lcd box=(1658,236,2098,370)   -> 出图 OK
成功 3/3
```

第 3 轮正好命中了 PDS 自杀，被自动重试救回来 —— 这就是缺陷 A + 重试的价值：**从"静默卡 30 分钟"变成"6 秒重来一次然后成功"**。

## 6. 截图内容自检

对同一张 LCD 特写做列投影切分字符（暗像素在每一列的计数），得到：

- 第 1 行 **14** 段墨迹，第 2 行 **11** 段
- 与固件写入序列 `"T1"+lcd_deg(t0)+' ' + "T2"+lcd_deg(t1)+' '` / `"T3"+lcd_deg(t2)+" H"+bit+" C"+bit+' '` 完全对应

⇒ 截图不是"看起来有字"，而是能对上**字符级**的写入顺序。

## 7. `Graph/Add Traces` 为什么"点了没反应"（2026-11-14 实测）

结论：**没有 graph 对象时这条命令是彻底的空操作**，不是超时也不是权限问题。

| 观测 | 结果 |
|---|---|
| `Graph` 菜单 9 项全部 `enabled=True`（即使没有 graph） | 菜单不置灰，误导性强 |
| 发 `WM_COMMAND 562` 后 1 / 2 / 3 秒的顶层窗口数 | 恒为 **1**（只有主窗口），新增 0 |
| 是否抛异常 | 否 |

为什么造不出 graph（四条路都堵死）：

1. **不在器件库里** —— 54 次搜索里 `GRAPH` 只命中 `LED-BARGRAPH-*`，没有 `ANALOGUE`/`GRAPH` 这类图元；
2. **没有菜单入口** —— 139 条菜单项里没有任何 "New Graph / Place Graph"；
3. **克隆不了** —— `D:\Proteus\SAMPLES\Graph Based Simulation\*.pdsprj` 全是 CDB v8：
   `Requires CDB v7, one-sheet layout` / `Unsupported sheet objects` / `Unknown CDB component table`；
4. **只能从工具栏放** —— 而工具栏按钮 id 无法从菜单枚举拿到。

⇒ 现在的处理：`papi.add_traces()` 先 `graphs()` 预检，空就 **0.00 秒**抛一条能照做的错，
不再让人以为"点了但没反应"。真正可行的路子是**模板工程**：手工放一次 Analogue Graph，
之后所有由它重建的工程都自带 graph，`graphs()` / `export_csv()` 就全自动了。

## 8. `press / release / set_switch` 到底要不要 `bind_controls`

答案：**必须要，而且必须发生在保存工程之前**。用两个只差一个参数的工程做对照：

| 工程 | `bind_controls` | `controls()` | `press/release` | `set_switch` |
|---|---|---|---|---|
| `t9.pdsprj` | 未调用 | 列得出 B1/SW1，但 `bound=False` | ❌ `RuntimeError: B1: use Circuit.bind_controls() before saving and opening the project` | ❌ 同上 |
| `t9b.pdsprj` | `bind_controls("B1","SW1")` | `bound=True` | ✅ `idle=0 press=1 release=0` | ✅ `closed=1 open=0` |

所以 `controls()[i]["bound"]` 可以当预检开关；`kind` 也如实区分 `momentary`（BUTTON）
与 `latched`（SWITCH）。

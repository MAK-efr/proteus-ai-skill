# -*- coding: utf-8 -*-
"""papi —— Proteus 自动化的"能用层"。

为什么要有这一层：`proteus_automatic_api 0.2.0` 在大方向上是对的（原生菜单 + 内存只读 +
SDF 网表），但有两处在本机（Proteus 8.16 SP3 / 中文 Windows）实测不可用：

1. `Simulation.run_for()` 只有**第一次**调用能收敛。第一次成功停在定时断点后，
   第二次再下定时断点，引擎会在目标时刻**冻住**（sim 秒数不再增长、CPU load 掉到 0%），
   但状态仍报 `ANIMATING`、`reason` 为空 —— 库的完成条件（reason 以
   'Execution timer breakpoint: ' 开头且与调用前不同）永远不满足，于是等到超时。
   本模块的 `run_exact()` 用「到达目标 + 秒数不再变化 → 主动 pause()」绕开它。

2. `Simulation.log()` / `gpio_events()` / `errors()` 全挂在同一个断点上：它给
   Simulation Log 窗口发 WM_CONTEXTMENU(0x7B) 再等 `QPopup` 弹菜单，本机根本不弹，
   15s 后 TimeoutError。本模块的 `read_log()` 改用「置前 + Ctrl+A + Ctrl+C」，
   这条路在本机已连续跑通。

其余入口（Session.netlist/set_properties/save/close、Simulation.status/start/pause/stop/
set_firmware、measurements.graphs、Circuit/Project/Library）实测可用，原样透传。

另外提供 `cmd()`：直接把任意原生菜单命令按 command_id 发出去，绕开库没包装的能力
（单步、Run To Source Line、Reset Persistent Model Data、Graph/Add Traces 等）。
完整命令表见 `docs/MENU_MAP.md`（本机导出 140 条，其中 132 条可静态引用；
`File/1`–`File/8` 是动态分配的最近工程项，不要硬编码）。
"""
import ctypes as c
import time
from ctypes import wintypes as w
from pathlib import Path

import sys

# 依赖模块目录：默认就用本文件所在目录（仓库自带全部模块），
# 需要覆盖时设 PROTEUS_PYSITE。详见 proteus_env.py 的说明。
try:
    from proteus_env import pysite as _pysite
    SP = _pysite()
except ImportError:                      # 单文件拷出去用的情况
    SP = str(Path(__file__).resolve().parent)
    if SP not in sys.path:
        sys.path.insert(0, SP)

import proteus_native as native
from proteus_session import Session
from simulation import Simulation, gpio_events as _parse_gpio

u, k = native.u, native.k

# ---- 实测可用的原生命令号（本机 PDS 8.16 SP3，完整表见 docs/MENU_MAP.md）----
CMD_RUN = 576
CMD_RUN_NOBP = 577
CMD_RUN_TIMED = 578
CMD_START_DEBUG = 579
CMD_PAUSE = 581
CMD_STOP = 582
CMD_STEP_OVER = 583
CMD_STEP_INTO = 584
CMD_STEP_OUT = 585
CMD_RUN_TO_LINE = 586
CMD_ANIM_STEP = 587
CMD_RESET_PERSISTENT = 589

CALLBACK = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

# CM3 模型诊断通道的默认档位：只开 GPIO，其余全关。
# 不开这个，Simulation Log 里一条 [GPIO] 记录都不会有——这是最容易被漏掉的一步。
TRACES = dict(TRACE_INIT="0", TRACE_LOAD="0", TRACE_CORE="0", TRACE_BUS="0", TRACE_CPU="0",
              TRACE_INTERRUPT="0", TRACE_SYSTIMER="0", TRACE_DBGOUT="0", TRACE_FLASH="0",
              TRACE_GPIO="3", TRACE_PWMTIMER="0", TRACE_WATCHDOG="0", TRACE_IWATCHDOG="0",
              TRACE_USB="0", TRACE_USART="0", TRACE_I2C="0", TRACE_SPI="0", TRACE_ADC="0",
              TRACE_DMA="0", TRACE_RCC="0", TRACE_EXTI="0", TRACE_RTC="0")


# ---------------------------------------------------------------- 剪贴板
def _clip_text():
    u.GetClipboardSequenceNumber.restype = w.DWORD
    u.GetClipboardOwner.restype = w.HWND
    u.GetClipboardData.argtypes, u.GetClipboardData.restype = (w.UINT,), w.HANDLE
    u.OpenClipboard.argtypes = (w.HWND,)
    k.GlobalLock.argtypes, k.GlobalLock.restype = (w.HGLOBAL,), c.c_void_p
    k.GlobalUnlock.argtypes = (w.HGLOBAL,)
    for _ in range(30):
        if u.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return None
    try:
        handle = u.GetClipboardData(13)          # CF_UNICODETEXT
        if not handle:
            return None
        pointer = k.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return c.wstring_at(pointer)
        finally:
            k.GlobalUnlock(handle)
    finally:
        u.CloseClipboard()


class _KEYBDINPUT(c.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", c.c_void_p)]


class _INPUTUNION(c.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("pad", c.c_byte * 32)]


class _INPUT(c.Structure):
    _fields_ = [("type", w.DWORD), ("u", _INPUTUNION)]


def _send_vk(vk, up=False):
    inp = _INPUT(type=1)
    inp.u.ki.wVk = vk
    inp.u.ki.dwFlags = 0x0002 if up else 0
    u.SendInput(1, c.byref(inp), c.sizeof(_INPUT))


def _ctrl(vk):
    _send_vk(0x11)
    time.sleep(0.04)
    _send_vk(vk)
    time.sleep(0.04)
    _send_vk(vk, up=True)
    time.sleep(0.04)
    _send_vk(0x11, up=True)
    time.sleep(0.08)


class Sim:
    """Session + Simulation 的合并门面，带本机实测过的绕坑实现。"""

    def __init__(self, project, firmware=None, executable=None,
                 auto_open=True):
        if executable is None:
            try:
                from proteus_env import pds_exe
                executable = pds_exe()
            except ImportError:
                executable = r"D:\Proteus\BIN\PDS.EXE"
        self.session = Session(project, executable) if auto_open else None
        self.sim = Simulation(self.session)
        if firmware:
            self.set_firmware(firmware)

    # ---------------- 透传 ----------------
    def status(self):
        return self.sim.status()

    def set_firmware(self, path, ref="U1", clock=None):
        return self.sim.set_firmware(ref, path, clock)

    def set_traces(self, ref="U1", **overrides):
        """打开/关闭该器件的诊断通道档位。默认 TRACE_GPIO=3，其余为 0。

        必须在 stopped 状态调用；内部走 ADI 导入并回读校验。
        """
        values = dict(TRACES)
        values.update(overrides)
        return self.session.set_properties(ref, **values)

    def netlist(self):
        return self.session.netlist()

    def save(self):
        return self.session.save()

    def close(self):
        try:
            return self.session.close()
        except Exception:
            try:
                self.session.process.kill()
            except Exception:
                pass
            return None

    def start(self, timeout=3.0):
        """恢复运行。库的 start() 只发 576，进了 VSM 调试模式后 576 不再生效，
        所以这里按 576 → 579(Start VSM Debugging) → 577(no breakpoints) 依次尝试。
        """
        if self.status()["state"] == "running":
            return self.status()
        for command in (CMD_RUN, CMD_START_DEBUG, CMD_RUN_NOBP):
            try:
                self.cmd(command)
            except Exception:
                continue
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                st = self.status()
                if st["state"] == "running":
                    return st
                time.sleep(0.1)
        raise TimeoutError(f"无法恢复运行，当前 {self.status()}")

    def pause(self):
        return self.sim.pause()

    def stop(self):
        return self.sim.stop()

    # ---------------- 控件（按钮 / 开关）----------------
    # 2026-11-14 实测（Proteus 8.16 SP3）：
    #   * 必须在**保存工程之前**用 Circuit.bind_controls(ref...) 绑定过，否则
    #     press/release/set_switch 会抛
    #     "B1: use Circuit.bind_controls() before saving and opening the project"。
    #   * 未绑定时 controls() 仍然列得出器件，但 bound=False —— 可以当预检用。
    #   * 绑定后实测：BUTTON idle=0 press=1 release=0；SWITCH closed=1 open=0。
    def controls(self):
        """列出可编程的二进制执行器：[{ref, device, state, kind, bound}]。"""
        return self.sim.controls()

    def control_state(self, ref):
        return self.sim.control_state(ref)

    def press(self, ref):
        """按下瞬时按钮（BUTTON / SW-SPST-MOM）。需要事先 bind_controls。"""
        return self.sim.press(ref)

    def release(self, ref):
        return self.sim.release(ref)

    def set_switch(self, ref, closed):
        """设置开关位置（SWITCH / SW-SPST / LOGICSTATE）。需要事先 bind_controls。"""
        return self.sim.set_switch(ref, closed)

    # ---------------- graph / 模拟量轨迹 ----------------
    def graphs(self):
        """列出工程里已有的 graph 对象；没有 graph 时返回空列表。"""
        from measurements import graphs as _graphs
        return _graphs(self.session)

    def add_traces(self, timeout=8.0):
        """Graph/Add Traces（原生命令 562）。

        2026-11-14 实测：**工程里没有 graph 对象时，这条命令是彻底的空操作** ——
        不弹对话框、不报错、不产生任何新窗口（实测发命令后 1/2/3 秒窗口数恒为 1）。
        所以这里先断言有 graph，再确认真的弹了窗口，避免调用方以为"点了但没反应"。
        """
        if not self.graphs():
            raise RuntimeError(
                "工程里没有 graph 对象，Graph/Add Traces 会静默无响应。"
                "graph 无法程序化创建（不在器件库里、没有菜单入口、官方样例是 CDB v8 "
                "读不了、只能从工具栏放）。可行做法：在**模板工程**里手工放一个 "
                "Analogue Graph 并保存，之后所有由该模板重建的工程都自带 graph。")
        before = {r["hwnd"] for r in native.windows(self.session.pid)}
        self.cmd(562)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if {r["hwnd"] for r in native.windows(self.session.pid)} - before:
                return True
            time.sleep(0.1)
        raise RuntimeError("Graph/Add Traces 已发送，但 %ss 内没有出现新窗口" % timeout)

    def export_csv(self, graph, destination, **kwargs):
        """把一个已有 graph 的曲线导出成 CSV（依赖 measurements.export_graph）。"""
        from measurements import export_graph
        return export_graph(self.session, graph, destination, **kwargs)

    # ---------------- 原生命令直发 ----------------
    def menus(self):
        rows = [r for r in native.windows(self.session.pid) if u.GetMenu(r["hwnd"])]
        out = []
        for row in rows:
            out.extend(native.menus(u.GetMenu(row["hwnd"])))
        return out

    def cmd(self, target):
        """按菜单路径或 command_id 直接发 WM_COMMAND。返回实际发出的 command_id。"""
        window = self.session._wait(self.session._ready)
        if isinstance(target, int):
            command, path = target, None
        else:
            items = [i for i in native.menus(u.GetMenu(window["hwnd"])) if i["path"] == target]
            if len(items) != 1:
                raise ValueError(f"菜单项缺失或不唯一: {target}")
            command, path = items[0]["command_id"], target
        res = c.c_size_t()
        u.SendMessageTimeoutW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM,
                                          w.UINT, w.UINT, c.POINTER(c.c_size_t))
        u.SendMessageTimeoutW.restype = w.LPARAM
        if not u.SendMessageTimeoutW(window["hwnd"], 0x111, command, 0, 2, 3000, c.byref(res)):
            raise RuntimeError(f"命令发送超时: {path or command}")
        return command

    # ---------------- 定时运行（绕开 run_for 第二次不收敛）----------------
    def _issue_timed(self, seconds):
        self._menu("Debug/Run Simulation (timed breakpoint)")
        dialog = self.session._dialog("Execute for specified time")
        edits = [r for r in dialog["children"] if r["cls"] == "Edit" and r["control_id"] == 257]
        if len(edits) != 1:
            raise RuntimeError("未识别的定时断点输入框")
        self._message(edits[0]["hwnd"], 0xB1, 0, -1)          # EM_SETSEL 全选
        typed = format(seconds, ".6f").rstrip("0").rstrip(".")
        for ch in typed:
            self._message(edits[0]["hwnd"], 0x102, ord(ch))    # WM_CHAR
        buf = c.create_unicode_buffer(128)
        self._message(edits[0]["hwnd"], 0xD, len(buf), c.addressof(buf))
        if buf.value != typed:
            self.session._click(dialog, "Cancel")
            raise RuntimeError(f"定时输入回读不符: {buf.value!r} != {typed!r}")
        self._message(dialog["hwnd"], 0x111, 1, 0)             # OK

    def _menu(self, path):
        self.sim._menu(path)

    @staticmethod
    def _message(hwnd, message, wp=0, lp=0):
        return Simulation._message(hwnd, message, wp, lp)

    def run_exact(self, seconds, timeout=180, poll=0.05, settle=0.35, tol=1e-6):
        """精确推进 N 秒仿真时间，返回 dict（含 completion 与不确定度）。

        比 run_for 宽容：到达目标后若状态仍是 running 但秒数不再变化
        （本机的"冻住"病理），主动 pause() 并标记 completion='frozen_pause'。

        2026-11-14 修「静默卡死」：原实现只比 deadline 和目标秒数，**不看引擎死活**。
        PDS 中途崩掉/被关掉时 simulation_seconds 恒为 None，于是会空转到 timeout
        （调用方常传 1800s = 30 分钟）才返回，期间日志一片空白，看起来就是"卡死"。
        现在一旦 state 变成 exited / stopped 就立刻抛 RuntimeError，1 秒内暴露问题。
        """
        before = self.status()
        if before["state"] == "running":
            self.pause()
            before = self.status()
        start = before["simulation_seconds"] or 0.0
        target = start + float(seconds)
        self._issue_timed(seconds)

        deadline = time.monotonic() + timeout
        last, frozen = None, None
        while time.monotonic() < deadline:
            st = self.status()
            if st["state"] in ("exited", "stopped"):
                raise RuntimeError(
                    "run_exact(%s) 中止：引擎状态=%s returncode=%s 停在 %s 秒（目标 %.6f）"
                    % (seconds, st["state"], st.get("returncode"),
                       st["simulation_seconds"], target))
            value = st["simulation_seconds"]
            if value is not None and value >= target - tol:
                if st["state"] == "paused":
                    return dict(st, completion="timer_breakpoint", start_seconds=start,
                                target_seconds=target, overshoot_seconds=max(0.0, value - target))
                if last is not None and abs(value - last) < 1e-12:
                    frozen = frozen or time.monotonic()
                    if time.monotonic() - frozen >= settle:
                        paused = self.pause()
                        return dict(paused, completion="frozen_pause", start_seconds=start,
                                    target_seconds=target,
                                    overshoot_seconds=max(0.0, value - target))
                else:
                    frozen = None
            last = value
            time.sleep(poll)
        raise TimeoutError(f"run_exact({seconds}) 未在 {timeout}s 内到达 {target}: {last}")

    # ---------------- 单步 ----------------
    def step(self, count=1, command=CMD_STEP_OVER, settle=0.25):
        """连续单步。返回每次单步后的仿真秒数（用于确认"真的在走"）。

        注意：① 只在 VSM 调试模式下有效，普通 Run Simulation 下单步不动；
        ② 命令发出后要留 ~0.25s 给引擎执行，用 0.05s 读到的会是旧值。
        """
        times = []
        for _ in range(count):
            self.cmd(command)
            time.sleep(settle)
            times.append(self.status()["simulation_seconds"])
        return times

    def reset_persistent(self):
        """Debug/Reset Persistent Model Data（命令 589）。

        会弹一个 "VSM Debugger" 模态框（"Reset all Persistent Model Data to initial
        values?"），不点掉的话整个会话进入 blocked 状态。这里自动点 &Yes。
        """
        self.cmd(CMD_RESET_PERSISTENT)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            for row in native.windows(self.session.pid):
                if row.get("cls") == "#32770":
                    for kid in row.get("children") or []:
                        if kid.get("cls") == "Button" and kid.get("title") in ("&Yes", "Yes", "是(&Y)"):
                            u.PostMessageW(kid["hwnd"], 0xF5, 0, 0)
                            time.sleep(0.6)
                            return self.status()
            time.sleep(0.1)
        return self.status()

    # ---------------- 日志（绕开 log() 的 QPopup 断点）----------------
    def _log_window(self):
        rows = [r for r in native.windows(self.session.pid) if r["title"] == "Simulation Log"]
        return rows[0] if rows else None

    def _open_log(self):
        win = self._log_window()
        if win:
            return win
        candidates = ["Debug/1. Simulation Log", "Graph/View Simulation Log"]
        have = {i["path"] for i in self.menus()}
        for path in candidates:
            if path in have:
                self.cmd(path)
                break
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            win = self._log_window()
            if win:
                return win
            time.sleep(0.1)
        raise RuntimeError("Simulation Log 窗口未出现")

    def read_log(self):
        """读原生日志。先试剪贴板，内容过少再退回堆内存提取。"""
        try:
            text = self._read_log_clip()
        except Exception:
            text = ""
        if len(text) >= 200:
            return text
        heap = self._heap_text()
        return heap if len(heap) > len(text) else text

    def _read_log_clip(self):
        """只聚焦日志窗口 + Ctrl+A / Ctrl+C，读剪贴板。"""
        win = self._open_log()
        u.SetForegroundWindow(win["hwnd"])
        time.sleep(0.35)
        u.GetClipboardSequenceNumber.restype = w.DWORD
        before = u.GetClipboardSequenceNumber()
        _ctrl(0x41)      # Ctrl+A
        _ctrl(0x43)      # Ctrl+C
        for _ in range(60):
            if u.GetClipboardSequenceNumber() != before:
                return _clip_text() or ""
            time.sleep(0.05)
        return _clip_text() or ""

    # ---------------- 堆内日志提取（不依赖窗口/剪贴板）----------------
    def _heap_text(self):
        try:
            import gh_tx
        except Exception:
            return ""
        try:
            records, _ = gh_tx.extract(self.session.pid)
        except Exception:
            return ""
        lines = []
        for rec in records:
            if isinstance(rec, dict):
                lines.append(f"{rec.get('text', '')} @{rec.get('seconds')}s")
            elif isinstance(rec, (tuple, list)) and len(rec) >= 2:
                lines.append(f"{rec[0]} @{rec[1]}s")
            else:
                lines.append(str(rec))
        return "\n".join(lines)

    def gpio(self, ref=None, port=None, pin=None):
        """解析 [GPIO] drive 记录。优先用堆内存提取，失败再退回剪贴板文本。"""
        try:
            import gh_tx
            records, _ = gh_tx.extract(self.session.pid)
            events = gh_tx.gpio(records)
            out = []
            for item in events:
                p, n, v, t = item[:4]
                if (port is None or p == port) and (pin is None or n == pin):
                    out.append(dict(port=p, pin=n, value=v, seconds=t, ref=ref))
            if out:
                return out
        except Exception:
            pass
        return _parse_gpio(self.read_log(), ref=ref, port=port, pin=pin)

    def errors(self):
        lines = [ln for ln in self.read_log().splitlines()
                 if any(w in ln.lower() for w in ("error", "fatal", "failed", "failure",
                                                  "exception"))]
        return dict(messages=lines)


def open_sim(project, firmware=None, **kw):
    """一行打开：返回 Sim，调用方负责 close()。"""
    return Sim(project, firmware=firmware, **kw)

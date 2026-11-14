# -*- coding: utf-8 -*-
"""lcdlog.py <project> <hex> <seconds> [--csv]

无像素方案：不开截图也能读出 LCD 显示内容。
原理：固件每次 lcd_out() 都写 GPIOC ODR（PC0=RS, PC1=E, PC2..PC9=D0..D7），
Proteus 的 Simulation Log 会逐脚记录 `[GPIO] drive PIO2.x = v`。按序号重放这些
记录，在 E 的上升沿锁存当前 RS+数据位，再解释 HD44780 的 8 位命令流，
重建 16x2 DDRAM 文本 —— 这样锁屏 / 无桌面 / 远程会话里也能机器判读显示内容。

用法:
  lcdlog.py D:/demo/lcd/cal.pdsprj D:/demo/lcd/Objects/cal.hex 2.2 6.2
"""
import os
import re
import sys
import time
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from proteus_env import add_self_to_path

add_self_to_path()
import papi


REC = re.compile(r"@\[GPIO\] drive PIO2\.(\w) = (\d)")


def decode(lines):
    """把 GPIO 记录流解释成 16x2 文本，返回每屏快照列表。

    HD44780 的 DDRAM 地址：第 1 行 0x00-0x0F，第 2 行 0x40-0x4F（不是 0x10 起）。
    """
    pins = [0] * 10          # PC0..PC9
    ddram = [0x20] * 128     # 按 HD44780 地址存显示 RAM
    addr = 0
    screens = []
    last_text = None

    def put(a, ch):
        if 0 <= a < 128:
            ddram[a] = ch

    def next_addr(a):
        if a == 0x0F:
            return 0x40
        if a == 0x4F:
            return 0x00
        return (a + 1) & 0x7F

    def snap():
        r0 = "".join(chr(ddram[a]) for a in range(0x00, 0x10))
        r1 = "".join(chr(ddram[a]) for a in range(0x40, 0x50))
        return [r0, r1]

    for ln in lines:
        m = REC.search(ln)
        if not m:
            continue
        n = int(m.group(1), 16)
        v = int(m.group(2))
        if n > 9:
            continue
        prev_e = pins[1]
        pins[n] = v
        if n == 1 and v == 1 and prev_e == 0:
            # E 上升沿：RS 与 8 位数据此刻已就绪
            rs = pins[0]
            byte = 0
            for k in range(8):
                byte |= (pins[2 + k] & 1) << k
            if rs == 0:
                if byte & 0x80:                 # 设 DDRAM 地址
                    addr = byte & 0x7F
                elif byte & 0x40:               # 回 home
                    addr = 0
                elif byte & 0x01:               # 清屏
                    for i in range(128):
                        ddram[i] = 0x20
                    addr = 0
                elif byte & 0x02:               # 光标回原点
                    addr = 0
            else:
                put(addr, byte)
                addr = next_addr(addr)
            cur = snap()
            if cur != last_text:
                screens.append(cur)
                last_text = cur
    return screens


def main():
    if len(sys.argv) < 4:
        raise SystemExit("用法: lcdlog.py <project.pdsprj> <firmware.hex> <seconds> [more...] [--csv]")
    prj, hexfile, secs = sys.argv[1], sys.argv[2], float(sys.argv[3])

    subprocess.run(["taskkill", "/F", "/IM", "PDS.EXE"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "UV4.exe"], capture_output=True)
    time.sleep(2)
    s = papi.Sim(prj, firmware=hexfile)
    s.set_traces("U1")
    s.save()
    time.sleep(3)
    s.start()
    s.run_exact(secs, timeout=1800)
    sim = s.status().get("simulation_seconds")
    log = s.read_log()
    try:
        s.close()
    except Exception:
        pass
    lines = log.splitlines()
    print("sim=%.4f  log lines=%d (GPIO records=%d)" % (sim, len(lines), sum(1 for l in lines if "drive PIO2" in l)))
    screens = decode(lines)
    print("screen snapshots: %d" % len(screens))
    for i, sc in enumerate(screens):
        print("--- snapshot %d ---" % i)
        print("|%s|" % sc[0].rstrip())
        print("|%s|" % sc[1].rstrip())
    if screens:
        print("FINAL:")
        print("|%s|" % screens[-1][0].rstrip())
        print("|%s|" % screens[-1][1].rstrip())


main()

# -*- coding: utf-8 -*-
"""lcdgpio.py <project> <hex> <seconds1> [seconds2 ...]
无像素、无剪贴板地读出 LCD1602 的显示内容。

和 lcdlog.py 的区别：lcdlog 解析 Simulation Log 的**文本**（read_log 会走剪贴板，
桌面被别的窗口抢焦点时会读错内容）；这里直接调 papi.Sim.gpio()，它走
gh_tx 的进程堆内存提取，拿到的是一串 (port, pin, value, seconds) 事件，
按**记录顺序**重放（顺序才是时序真值，时间戳会有量化误差）。
"""
import sys
import time
import subprocess

sys.path.insert(0, r"C:\l130sim\gh")
sys.path.insert(0, r"C:\Users\32500\.workbuddy\binaries\python\envs\proteus\Lib\site-packages")
import papi


def replay(events):
    """events: [(port, pin, value, seconds)]，只关心 port==2 (GPIOC) 的 pin 0..9。"""
    pins = [0] * 10
    ddram = [0x20] * 128
    addr = 0
    screens = []
    last = None

    def snap():
        r0 = "".join(chr(ddram[a]) for a in range(0x00, 0x10))
        r1 = "".join(chr(ddram[a]) for a in range(0x40, 0x50))
        return [r0, r1]

    def next_addr(a):
        if a == 0x0F:
            return 0x40
        if a == 0x4F:
            return 0x00
        return (a + 1) & 0x7F

    for ev in events:
        port, pin, val = ev["port"], ev["pin"], ev["value"]
        if port != 2 or pin > 9:
            continue
        prev_e = pins[1]
        pins[pin] = 1 if val else 0
        if pin == 1 and pins[1] == 1 and prev_e == 0:
            rs = pins[0]
            byte = 0
            for k in range(8):
                byte |= (pins[2 + k] & 1) << k
            if rs == 0:
                if byte & 0x80:
                    addr = byte & 0x7F
                elif byte & 0x40:
                    addr = 0
                elif byte & 0x01:
                    for i in range(128):
                        ddram[i] = 0x20
                    addr = 0
                elif byte & 0x02:
                    addr = 0
            else:
                if 0 <= addr < 128:
                    ddram[addr] = byte
                addr = next_addr(addr)
            cur = snap()
            if cur != last:
                screens.append(cur)
                last = cur
    return screens


def main():
    prj, hexf = sys.argv[1], sys.argv[2]
    times = [float(x) for x in sys.argv[3:]] or [3.0]
    subprocess.run(["taskkill", "/F", "/IM", "PDS.EXE"], capture_output=True)
    time.sleep(2)
    s = papi.Sim(prj, firmware=hexf)
    s.set_traces("U1")
    s.save()
    time.sleep(3)
    s.start()
    t0 = 0.0
    for t in times:
        d = t - t0
        if d > 0:
            s.run_exact(d, timeout=1800)
        t0 = t
        sim = s.status().get("simulation_seconds")
        ev = s.gpio()
        screens = replay(ev)
        print("=== t=%.2f  sim=%.4f  gpio events=%d ===" % (t, sim or -1, len(ev)))
        if screens:
            for r in screens[-1]:
                print("|%s|" % r.rstrip())
        else:
            print("(no LCD traffic decoded)")
    s.close()


main()

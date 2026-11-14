# -*- coding: utf-8 -*-
"""shot_lcd.py <project> <hex> <outdir> <t1> [t2 ...]

在同一仿真会话里增量推进到各时刻，抓整窗截图，并**自动定位** LCD1602 的
橄榄绿面板（RGB≈(150,175,0)）后裁剪放大，因此不依赖窗口是否最大化。

用法示例:
  shot_lcd.py D:/demo/lcd/cal.pdsprj D:/demo/lcd/Objects/cal.hex D:/demo/out 4 12
"""
import os
import sys
import time
import glob
import ctypes
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from proteus_env import add_self_to_path, need_pil

add_self_to_path()
need_pil()
from PIL import Image
import papi

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

LCD_RGB = (150, 175, 0)
TOL = 26

SESS = None          # 2026-11-14：留一个句柄给退出清理，避免 PDS 残留后台


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def pds_hwnd():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if "Proteus" in buf.value and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, None)
    return found[0] if found else None


def grab(hwnd):
    user32.ShowWindow(hwnd, 3)          # SW_MAXIMIZE
    user32.BringWindowToTop(hwnd)       # 必须拿到前台，否则画布可能一直是白板
    try:
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.8)
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    user32.PrintWindow(hwnd, mdc, 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)


def find_lcd(img):
    px = img.load()
    w, h = img.size
    x0, y0, x1, y1 = w, h, -1, -1
    step = 3
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = px[x, y][:3]
            if abs(r - LCD_RGB[0]) < TOL and abs(g - LCD_RGB[1]) < TOL and abs(b - LCD_RGB[2]) < TOL:
                if x < x0: x0 = x
                if y < y0: y0 = y
                if x > x1: x1 = x
                if y > y1: y1 = y
    if x1 < 0:
        return None
    return (max(0, x0 - 40), max(0, y0 - 25), min(w, x1 + 40), min(h, y1 + 25))


def main():
    if len(sys.argv) < 4:
        raise SystemExit("用法: shot_lcd.py <project.pdsprj> <firmware.hex> <outdir> [seconds...]")
    prj, hexfile, outdir = sys.argv[1], sys.argv[2], sys.argv[3]

    times = [float(t) for t in sys.argv[4:]]
    os.makedirs(outdir, exist_ok=True)

    subprocess.run(["taskkill", "/F", "/IM", "PDS.EXE"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "UV4.exe"], capture_output=True)
    # 2026-11-14：删掉该工程遗留的 *.workspace 状态文件。PDS 关闭或崩溃时会把它记成
    # "上次正在仿真"，于是下一次打开要么自动起跑（set_firmware 报 "Stop simulation
    # before configuring firmware"），要么直接崩出 "Reports Uploading" 报告框、把主窗口
    # 置灰，之后所有菜单操作全部超时——表现为"卡死"。实测删掉即恢复。
    stem = os.path.basename(prj)
    for stale in glob.glob(os.path.join(os.path.dirname(os.path.abspath(prj)), stem + ".*.workspace")):
        try:
            os.remove(stale)
            print("removed stale state:", stale)
        except OSError:
            pass
    time.sleep(2)
    s = papi.Sim(prj, firmware=hexfile)
    global SESS
    SESS = s
    s.set_traces("U1")      # ADI 导入后当次会话立即生效，不需要 save()
    # 2026-11-14：删掉原来的 s.save()。实测它会把工程写脏（t8.pdsprj 每 save 一次 +12 B），
    # 反复保存后 PDS 会弹 "Reports Uploading" 崩溃退出，或下次打开直接自动起跑、让
    # set_firmware 报 "Stop simulation before configuring firmware"。
    time.sleep(3)
    s.start()
    print("firmware:", hexfile)

    hwnd = pds_hwnd()
    prev = 0.0
    for t in times:
        s.run_exact(t - prev, timeout=1800)
        prev = t
        # 画布有时会空白/缩到极远：先重绘再整体缩放，重复两次更稳
        for _ in range(2):
            s.cmd(320)          # View/Redraw Display
            time.sleep(0.3)
            s.cmd(331)          # View/Zoom To View Entire Sheet
            time.sleep(0.4)
        time.sleep(0.4)
        if not hwnd:
            hwnd = pds_hwnd()
        img = grab(hwnd)
        base = os.path.join(outdir, "shot_t%05.2f" % t)
        img.save(base + "_full.png")
        box = find_lcd(img)
        if box:
            crop = img.crop(box)
            crop = crop.resize((crop.width * 4, crop.height * 4), Image.LANCZOS)
            crop.save(base + "_lcd.png")
            print("t=%.2f  sim=%s  lcd box=%s" % (t, s.status().get('simulation_seconds'), box))
        else:
            print("t=%.2f  sim=%s  LCD NOT FOUND (window %s)" % (t, s.status().get('simulation_seconds'), img.size))
    print("done")


def run_once():
    """跑一轮，并在任何退出路径上关掉 PDS。

    残留的 PDS 会让下一轮启动撞上"是否恢复自动保存的工程?"或"Reports Uploading"
    模态框；模态框一弹，主窗口被置灰，所有菜单操作都超时——表现就是"卡死"。
    """
    global SESS
    SESS = None
    try:
        main()
    finally:
        if SESS is not None:
            try:
                SESS.close()
                print("pds closed")
            except Exception as e:
                print("pds close failed:", e)


# 2026-11-14：PDS 8.16 SP3 在本机有约 1/2 概率在"启动后刚推进仿真"时自杀
# （退出码 1，再启动会弹 Reports Uploading）。这不是调用方的问题，重试一轮即可恢复。
# 配合 papi.run_exact 新增的存活守卫，这里能在几秒内发现并自动重来，不会静默空转。
ATTEMPTS = 3
for _attempt in range(1, ATTEMPTS + 1):
    try:
        run_once()
        break
    except RuntimeError as _e:
        if _attempt == ATTEMPTS:
            raise
        print("attempt %d/%d failed: %s -> retry" % (_attempt, ATTEMPTS, str(_e)[:160]))
        subprocess.run(["taskkill", "/F", "/IM", "PDS.EXE"], capture_output=True)
        time.sleep(6)

# -*- coding: utf-8 -*-
"""crossings.py -- 几何检查：走线交叉数 / 走线压到无关引脚

    python crossings.py <project.pdsprj>

为什么要单独一把尺子：
    component_codec 的 wire_codec 只保证「离开引脚方向正确」，不保证线之间不交叉，
    也不保证线不压到别人的引脚（压上了 Proteus 会当成真连接）。这两件事只能几何算。

判定
----
* 交叉：两条线段严格相交于内部点（共享端点不算）。水平/垂直都算。
* 压脚：走线穿过某引脚坐标，而该引脚不属于这条线连接的两个端点 => 视为短路隐患。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from proteus_env import add_self_to_path, library_dir, default_project

add_self_to_path()
import device_library

_Orig = device_library.Library
_LIB = library_dir()


class _L(_Orig):
    def __init__(self, directory=_LIB):
        super().__init__(directory)


device_library.Library = _L
from component_codec import Circuit

PRJ = sys.argv[1] if len(sys.argv) > 1 else default_project()
if not PRJ:
    raise SystemExit("用法: crossings.py <project.pdsprj>   （或设环境变量 PROTEUS_PRJ）")

c = Circuit.open(PRJ)

pin_pos = {}
for part in c._parts:
    for p in c.pins(part["ref"]):
        pin_pos[(part["ref"], p["name"])] = p["position"]

segs = []            # (net_index, p0, p1, endpoints)
auto_routes = 0
for idx, cc in enumerate(c._connections):
    pts = cc.get("points")
    if not pts:
        auto_routes += 1
        a, b = cc["first"], cc["second"]
        pa, pb = pin_pos.get(tuple(a)), pin_pos.get(tuple(b))
        if pa and pb:
            pts = [pa, pb]
        else:
            continue
    ends = {tuple(cc["first"]), tuple(cc["second"])}
    for i in range(len(pts) - 1):
        segs.append((idx, pts[i], pts[i + 1], ends, tuple(cc["first"]), tuple(cc["second"])))

print("connections: %d  (auto-routed: %d)  segments: %d" % (len(c._connections), auto_routes, len(segs)))


def on_seg_interior(pt, a, b):
    (x, y), (x0, y0), (x1, y1) = pt, a, b
    if x0 == x1:
        return x == x0 and min(y0, y1) < y < max(y0, y1)
    if y0 == y1:
        return y == y0 and min(x0, x1) < x < max(x0, x1)
    return False


def inter(a, b, c0, d0):
    """strict interior crossing of two axis-aligned segments"""
    (ax0, ay0), (ax1, ay1) = a, b
    (cx0, cy0), (cx1, cy1) = c0, d0
    if ax0 == ax1 and cy0 == cy1:
        x, y = ax0, cy0
        return (min(ax1, ax0) < x < max(ax1, ax0) or False) and min(cx0, cx1) < x < max(cx0, cx1) \
            and min(ay0, ay1) < y < max(ay0, ay1)
    if ay0 == ay1 and cx0 == cx1:
        x, y = cx0, ay0
        return min(cx0, cx1) < x < max(cx0, cx1) and min(ax0, ax1) < x < max(ax0, ax1) \
            and min(cy0, cy1) < y < max(cy0, cy1)
    return False


crossings = []
for i in range(len(segs)):
    for j in range(i + 1, len(segs)):
        ni, a, b, ends_i, f_i, s_i = segs[i]
        nj, c0, d0, ends_j, f_j, s_j = segs[j]
        if ni == nj:
            continue
        if inter(a, b, c0, d0):
            crossings.append((ni, nj, a, b, c0, d0))

print("crossings: %d" % len(crossings))
for item in crossings[:10]:
    print("   net %d x net %d  %s-%s  vs  %s-%s" % item)


def seg_hits(pt, a, b):
    (x, y), (x0, y0), (x1, y1) = pt, a, b
    if x0 == x1:
        return x == x0 and min(y0, y1) <= y <= max(y0, y1)
    if y0 == y1:
        return y == y0 and min(x0, x1) <= x <= max(x0, x1)
    return False


bad = 0
for (ref, name), pos in pin_pos.items():
    for ni, a, b, ends, f, s in segs:
        if (ref, name) in ends:
            continue
        if seg_hits(pos, a, b):
            print("   !! 走线 net %d 压到引脚 %s.%s %s" % (ni, ref, name, pos))
            bad += 1
print("pin overlaps: %d" % bad)
print("RESULT: %s" % ("PASS" if not crossings and not bad else "FAIL"))

# -*- coding: utf-8 -*-
"""netcheck.py [project.pdsprj]

Electrical + geometric acceptance for the 8-bit STM32F103R6 / LM016L calendar
sheet.  Verifies connectivity from the netlist, not by looking at the picture.

Wiring under test (built by build_final.py):
    PC0=RS  PC1=E  PC2..PC9=D0..D7  RW=GND
    U1.NRST--R1.1, R1.2--+3.3V      U1.BOOT0--R2.1, R2.2--GND
    LCD1.VDD--+5V, VSS/VEE/RW--GND

Exit code: 0 = all checks pass, 1 = at least one failure.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from proteus_env import add_self_to_path, default_project

add_self_to_path()
from component_codec import Circuit

PRJ = sys.argv[1] if len(sys.argv) > 1 else default_project()
if not PRJ:
    raise SystemExit("用法: netcheck.py <project.pdsprj>   （或设环境变量 PROTEUS_PRJ）")

c = Circuit.open(PRJ)

failures = []


def need(cond, msg):
    print("   %-4s %s" % ("OK" if cond else "FAIL", msg))
    if not cond:
        failures.append(msg)


# ---- 1. dump nets --------------------------------------------------------
nets = c.nets()
sets = [frozenset("%s.%s" % (r, p) for r, p in n) for n in nets]
multi = [s for s in sets if len(s) > 1]
print("nets: %d total, %d with 2+ members" % (len(nets), len(multi)))
for s in sorted(multi, key=lambda s: (-len(s), sorted(s))):
    print("   " + ", ".join(sorted(s)))

# ---- 2. LCD signal nets --------------------------------------------------
print("\n[1] LCD signal nets (8-bit: PC2..PC9 = D0..D7)")
SIGNALS = [("U1.PC0", "LCD1.RS"), ("U1.PC1", "LCD1.E")] + [
    ("U1.PC%d" % (2 + i), "LCD1.D%d" % i) for i in range(8)]
for a, b in SIGNALS:
    need(frozenset([a, b]) in sets, "%-8s <=> %-8s" % (a, b))

# ---- 3. reset / boot straps ---------------------------------------------
# R1 is rotated 90 deg: its LOWER leg is pin 1 (-> NRST), upper leg pin 2
# (-> +3.3V).  The old 4-bit script wrongly expected R1.2 on NRST.
print("\n[2] reset / boot straps")
need(frozenset(["U1.NRST", "R1.1"]) in sets, "U1.NRST  <=> R1.1  (10k pull-up)")
need(frozenset(["U1.BOOT0", "R2.1"]) in sets, "U1.BOOT0 <=> R2.1  (10k pull-down)")

# ---- 4. power terminals --------------------------------------------------
print("\n[3] power terminals")
term_of = {}
for t in c._terminals:
    if t.get("pin"):
        term_of["%s.%s" % (t["pin"][0], t["pin"][1])] = t["name"]
gnd = {k for k, v in term_of.items() if v == "GND"}
v33 = {k for k, v in term_of.items() if v == "+3.3V"}
v5 = {k for k, v in term_of.items() if v == "+5V"}
need(gnd == {"U1.VSS", "R2.2", "LCD1.VSS", "LCD1.VEE", "LCD1.RW"},
     "GND   terminals: %s" % ",".join(sorted(gnd)))
need(v33 == {"U1.VDD", "U1.VBAT", "U1.VREF+", "R1.2"},
     "+3.3V terminals: %s" % ",".join(sorted(v33)))
need(v5 == {"LCD1.VDD"},
     "+5V   terminals: %s" % ",".join(sorted(v5)))
need(any(gnd <= s for s in sets), "all GND pins share one net")
need(any(v33 <= s for s in sets), "all +3.3V pins share one net")
orphans = [t["id"] for t in c._terminals if not t.get("pin")]
need(not orphans, "no orphan power terminals (found %s)" % orphans)

# ---- 5. no floating pins on LCD / resistors ------------------------------
print("\n[4] every pin of LCD1 / R1 / R2 is connected")
used = set()
for cc in c._connections:
    for side in ("first", "second"):
        used.add("%s.%s" % (cc[side][0], cc[side][1]))
used |= set(term_of)
for ref in ("LCD1", "R1", "R2"):
    pins = {"%s.%s" % (ref, p["name"]) for p in c.pins(ref)}
    missing = pins - used
    need(not missing, "%-5s wired %d/%d  missing=%s"
         % (ref, len(pins & used), len(pins), sorted(missing)))

# ---- 6. planar routing ---------------------------------------------------
print("\n[5] planar routing (no geometric crossings)")
segs = []
for ci, cc in enumerate(c._connections):
    pts = cc.get("points") or []
    for a, b in zip(pts, pts[1:]):
        if a[0] == b[0] or a[1] == b[1]:
            segs.append((ci, a, b))


def crosses(s1, s2):
    if s1[0] == s2[0]:
        return False
    h1, h2 = s1[1][1] == s1[2][1], s2[1][1] == s2[2][1]
    if h1 == h2:
        return False
    h, v = (s1, s2) if h1 else (s2, s1)
    y = h[1][1]
    x1, x2 = sorted((h[1][0], h[2][0]))
    x = v[1][0]
    y1, y2 = sorted((v[1][1], v[2][1]))
    return x1 < x < x2 and y1 < y < y2


bad = [(a, b) for i, a in enumerate(segs) for b in segs[i + 1:] if crosses(a, b)]
need(not bad, "crossings = %d %s" % (len(bad), bad[:3]))

print("\nparts=%d terminals=%d connections=%d"
      % (len(c._parts), len(c._terminals), len(c._connections)))
print("RESULT: %s (%d failure(s))" % ("PASS" if not failures else "FAIL", len(failures)))
sys.exit(0 if not failures else 1)

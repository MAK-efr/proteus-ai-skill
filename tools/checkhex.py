# -*- coding: utf-8 -*-
"""checkhex.py <file.hex> [more.hex ...]

Verify a Cortex-M3 firmware image really boots: parse Intel HEX, then assert the
two words at 0x08000000 are a sane initial SP and a Thumb reset vector in flash.

Why this exists: armcc -g / armlink --debug without a scatter file silently put
Reset_Handler's *code* at 0x08000000 and buried the RESET section elsewhere.
The build still reported success, but the core read SP/PC = garbage and locked
up (LCD backlight on, no characters). "0 errors" is NOT "it boots".

Usage:
    python checkhex.py Objects/calendar.hex
    echo $?        # 0 = all good, 1 = at least one bad vector table
"""
import sys


def load_hex(path):
    mem, base = {}, 0
    for line in open(path):
        line = line.strip()
        if not line.startswith(':'):
            continue
        count = int(line[1:3], 16)
        addr = int(line[3:7], 16)
        kind = int(line[7:9], 16)
        data = bytes.fromhex(line[9:9 + 2 * count])
        if kind == 0:
            for i, byte in enumerate(data):
                mem[base + addr + i] = byte
        elif kind == 4:
            base = int.from_bytes(data, 'big') << 16
        elif kind == 2:
            base = int.from_bytes(data, 'big') << 4
        elif kind == 1:
            break
    return mem


def check(path):
    mem = load_hex(path)
    if not mem:
        print("%s  FAIL empty / unparseable HEX" % path)
        return False

    def word(addr):
        return int.from_bytes(bytes(mem.get(addr + i, 0xFF) for i in range(4)), 'little')

    sp, pc = word(0x08000000), word(0x08000004)
    lo, hi = min(mem), max(mem)
    print("%-42s %08X..%08X (%5d bytes)  SP=%08X  RESET=%08X"
          % (path, lo, hi, len(mem), sp, pc))

    ok = True
    if any(0x08000000 + i not in mem for i in range(8)):
        print("   FAIL vector table bytes missing at 0x08000000")
        ok = False
    if not (0x20000000 <= sp <= 0x20005000):
        print("   FAIL initial SP %08X outside SRAM 0x20000000..0x20005000" % sp)
        ok = False
    if not (0x08000000 <= pc < 0x08008000 and (pc & 1)):
        print("   FAIL RESET %08X is not a Thumb address in flash" % pc)
        ok = False
    print("   %s" % ("vector table OK" if ok else "VECTOR TABLE BROKEN"))
    return ok


if __name__ == '__main__':
    paths = sys.argv[1:]
    if not paths:
        raise SystemExit("用法: checkhex.py <file.hex> [more.hex ...]")
    results = [check(p) for p in paths]
    sys.exit(0 if results and all(results) else 1)


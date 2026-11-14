# build_cal.ps1 <src.c> <out_stem>  -- PowerShell port of build_cal.sh
# Keil ARMCC build + vector-table guard.
# NOTE: no -g / no --debug.  With either flag armlink puts Reset_Handler's code
#       at 0x08000000 and buries the RESET vector section elsewhere, so the core
#       boots with SP/PC = garbage and the chip silently locks up.
param(
    [Parameter(Mandatory=$true)][string]$Src,
    [Parameter(Mandatory=$true)][string]$Out
)
$ErrorActionPreference = "Stop"
$FW  = "C:\l130sim\lcd"
$CC  = "D:\keli\ARM\ARMCC\bin\armcc.exe"
$LD  = "D:\keli\ARM\ARMCC\bin\armlink.exe"
$ELF = "D:\keli\ARM\ARMCC\bin\fromelf.exe"
$PY  = "C:\Users\32500\.workbuddy\binaries\python\envs\proteus\Scripts\python.exe"

foreach ($exe in @($CC, $LD, $ELF)) { if (-not (Test-Path $exe)) { throw "missing $exe" } }
Set-Location $FW

& $CC --c99 -c --cpu Cortex-M3 -O1 -D__MICROLIB "-I$FW" $Src -o "$Out.o"
if ($LASTEXITCODE -ne 0) { throw "armcc failed ($LASTEXITCODE)" }

& $LD --entry Reset_Handler --library_type=microlib `
      --scatter=C:/l130sim/fw2/fw2.sct `
      "$Out.o" C:/l130sim/fw2/startup.o -o "$Out.axf"
if ($LASTEXITCODE -ne 0) { throw "armlink failed ($LASTEXITCODE)" }

& $ELF --i32combined -o "$Out.hex" "$Out.axf"
if ($LASTEXITCODE -ne 0) { throw "fromelf failed ($LASTEXITCODE)" }

# --- vector table guard (same checks as build_cal.sh) ---
$chk = @'
import sys
mem, base = {}, 0
for ln in open(sys.argv[1]):
    ln = ln.strip()
    if not ln.startswith(':'):
        continue
    n = int(ln[1:3], 16); a = int(ln[3:7], 16); t = int(ln[7:9], 16)
    d = bytes.fromhex(ln[9:9 + 2 * n])
    if t == 0:
        for i, b in enumerate(d):
            mem[base + a + i] = b
    elif t == 4:
        base = int.from_bytes(d, 'big') << 16
    elif t == 2:
        base = int.from_bytes(d, 'big') << 4
w = lambda a: int.from_bytes(bytes(mem.get(a + i, 0xFF) for i in range(4)), 'little')
sp, pc = w(0x08000000), w(0x08000004)
lo, hi = min(mem), max(mem)
print("hex %08X..%08X (%d bytes)  SP=%08X  RESET=%08X" % (lo, hi, len(mem), sp, pc))
assert 0x20000000 <= sp <= 0x20005000, "bad initial SP"
assert 0x08000000 <= pc < 0x08008000 and pc & 1, "bad reset vector (not Thumb code in flash)"
print("vector table OK")
'@
$tmp = Join-Path $env:TEMP "vecguard.py"
Set-Content -Path $tmp -Value $chk -Encoding ascii
& $PY $tmp "$Out.hex"
if ($LASTEXITCODE -ne 0) { throw "vector table check FAILED" }

Get-Item "$Out.hex" | Select-Object Name, Length, LastWriteTime | Format-List

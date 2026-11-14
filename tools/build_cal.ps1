# build_cal.ps1 - Keil ARMCC build for Cortex-M3 + vector-table guard.
#
# NOTE: no -g / no --debug. With either flag armlink puts Reset_Handler's code
#       at 0x08000000 and buries the RESET vector section elsewhere, so the core
#       boots with SP/PC = garbage and the chip silently locks up.
#
# Usage:
#   .\build_cal.ps1 -Src main.c -Out Objects/app -Scatter fw.sct -Startup Objects/startup.o
#
# Everything machine-specific is a parameter or an env var, nothing is hardcoded:
#   -ArmccDir   directory holding armcc.exe / armlink.exe / fromelf.exe
#               (default: $env:ARMCC_DIR, else Keil ARMCC default install)
#   -Python     interpreter used for the vector-table guard (default: python on PATH)
param(
    [Parameter(Mandatory=$true)][string]$Src,
    [Parameter(Mandatory=$true)][string]$Out,
    [Parameter(Mandatory=$true)][string]$Scatter,
    [Parameter(Mandatory=$true)][string]$Startup,
    [string]$ArmccDir = $env:ARMCC_DIR,
    [string[]]$Include = @(),
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"

if (-not $ArmccDir) {
    $cand = @(
        "C:\Keil_v5\ARM\ARMCC\bin",
        "C:\Keil\ARM\ARMCC\bin",
        "D:\keli\ARM\ARMCC\bin"
    ) | Where-Object { Test-Path (Join-Path $_ "armcc.exe") } | Select-Object -First 1
    if (-not $cand) { throw "armcc.exe not found. Pass -ArmccDir or set ARMCC_DIR." }
    $ArmccDir = $cand
}

$CC  = Join-Path $ArmccDir "armcc.exe"
$LD  = Join-Path $ArmccDir "armlink.exe"
$ELF = Join-Path $ArmccDir "fromelf.exe"
foreach ($exe in @($CC, $LD, $ELF)) {
    if (-not (Test-Path $exe)) { throw "missing $exe" }
}
foreach ($p in @($Scatter, $Startup)) {
    if (-not (Test-Path $p)) { throw "missing $p" }
}

$IncArgs = @()
foreach ($i in $Include) { $IncArgs += "-I$i" }
if ($Include.Count -eq 0) { $IncArgs += "-I." }

& $CC --c99 -c --cpu Cortex-M3 -O1 -D__MICROLIB @IncArgs $Src -o "$Out.o"
if ($LASTEXITCODE -ne 0) { throw "armcc failed ($LASTEXITCODE)" }

& $LD --entry Reset_Handler --library_type=microlib `
      "--scatter=$Scatter" "$Out.o" $Startup -o "$Out.axf"
if ($LASTEXITCODE -ne 0) { throw "armlink failed ($LASTEXITCODE)" }

& $ELF --i32combined -o "$Out.hex" "$Out.axf"
if ($LASTEXITCODE -ne 0) { throw "fromelf failed ($LASTEXITCODE)" }

# --- vector table guard (same checks as tools/checkhex.py) ---
$chk = Join-Path $PSScriptRoot "checkhex.py"
if (-not (Test-Path $chk)) { throw "missing $chk" }
& $Python $chk "$Out.hex"
if ($LASTEXITCODE -ne 0) { throw "vector table check FAILED" }

Get-Item "$Out.hex" | Select-Object Name, Length, LastWriteTime | Format-List

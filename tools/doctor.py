# -*- coding: utf-8 -*-
"""doctor.py -- 一条命令回答"我这台机器到底能不能用这个包"。

它只做只读检查，不改任何文件、不启动 Proteus。

    python tools/doctor.py          # 检查
    python tools/doctor.py -v       # 额外打印解析到的每个路径

退出码：0 = 关键项全过；1 = 有关键项失败（输出里标 FAIL）。
WARN 表示"相关功能不可用，但其他功能正常"，不影响退出码。
"""
import hashlib
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))

try:
    import proteus_env
    proteus_env.add_self_to_path()
    ENV_ERROR = None
except Exception as _e:                      # 连路径层都坏了，后面全部降级
    proteus_env = None
    ENV_ERROR = "%s: %s" % (type(_e).__name__, _e)

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
rows = []


def rec(level, item, detail=""):
    rows.append((level, item, detail))


def main():
    verbose = "-v" in sys.argv or "--verbose" in sys.argv

    # ---- 1. Python 版本 ----
    v = sys.version_info
    if v >= (3, 10):
        rec(PASS, "Python", "%d.%d.%d" % v[:3])
    else:
        rec(FAIL, "Python", "%d.%d.%d < 3.10，语法用了 3.10+ 特性" % v[:3])

    # ---- 2. 依赖模块（这是最主要的一项）----
    if proteus_env is None:
        rec(FAIL, "proteus_env", ENV_ERROR or "导入失败")
        return report(verbose)

    mods = ["proteus_native", "proteus_project", "proteus_session", "component_codec",
            "wire_codec", "device_library", "pin_geometry", "net_edit", "net_objects",
            "measurements", "simulation", "interactive_native",
            "proteus_automatic_api", "papi"]
    ok, bad = [], []
    for m in mods:
        try:
            __import__(m)
            ok.append(m)
        except Exception as e:
            bad.append((m, "%s: %s" % (type(e).__name__, e)))
    if bad:
        for m, why in bad:
            rec(FAIL, "import " + m, why)
    rec(PASS if not bad else FAIL, "模块导入", "%d/%d" % (len(ok), len(mods)))

    # ---- 3. Pillow（只有截图工具需要）----
    try:
        import PIL
        from PIL import Image  # noqa: F401
        rec(PASS, "Pillow", getattr(PIL, "__version__", "?"))
    except Exception:
        rec(WARN, "Pillow", "未安装：截图/裁剪工具不可用。pip install pillow"
                            "（tools/lcdgpio.py 不需要它）")

    # ---- 4. PDS.EXE ----
    pds = None
    try:
        pds = proteus_env.pds_exe()
        size = os.path.getsize(pds)
        rec(PASS, "PDS.EXE", "%s (%d B)" % (pds, size))
    except Exception as e:
        rec(FAIL, "PDS.EXE", str(e).splitlines()[0])

    # ---- 5. 器件库 ----
    try:
        lib = proteus_env.library_dir()
        n = len(list(Path(lib).glob("*")))
        rec(PASS, "器件库 LIBRARY", "%s（%d 个条目）" % (lib, n))
    except Exception as e:
        rec(FAIL, "器件库 LIBRARY", str(e).splitlines()[0])

    # ---- 6. WINCORE.DLL 指纹（决定能否读仿真状态）----
    expected = "944013e93f0d31addbbce797d6eb3746bf06c7497c0d04cb77955fc0acd569bb"
    if pds:
        wincore = Path(pds).parent / "WINCORE.DLL"
        if not wincore.is_file():
            rec(WARN, "WINCORE.DLL", "找不到 %s" % wincore)
        else:
            h = hashlib.sha256(wincore.read_bytes()).hexdigest()
            if h == expected:
                rec(PASS, "WINCORE 指纹", "与验证基线一致（状态栏读取可用）")
            else:
                rec(WARN, "WINCORE 指纹",
                    "不匹配：sha256=%s...\n         simulation_seconds / status_text / "
                    "run_for 会退化成 NotImplementedError，只剩菜单状态可用。\n"
                    "         这不一定是你装错了 —— 换版本就会变，按 docs/API_MAP.md"
                    " 第 0 节重新测状态栏控件。" % h[:16])
    else:
        rec(WARN, "WINCORE 指纹", "PDS.EXE 未定位，跳过")

    # ---- 7. 工程路径约定 ----
    prj = proteus_env.default_project()
    if prj:
        rec(PASS if Path(prj).exists() else FAIL, "PROTEUS_PRJ", prj)
    else:
        rec(WARN, "PROTEUS_PRJ", "未设置（工具必须命令行传工程路径）")

    return report(verbose)


def report(verbose):
    width = max(len(r[1]) for r in rows) + 2
    print("=" * (width + 46))
    print("Proteus 自动化包 · 环境自检")
    print("=" * (width + 46))
    for level, item, detail in rows:
        mark = {PASS: "[PASS]", FAIL: "[FAIL]", WARN: "[WARN]"}[level]
        print("%s %-*s %s" % (mark, width, item, detail))
    print("-" * (width + 46))

    fails = sum(1 for r in rows if r[0] == FAIL)
    warns = sum(1 for r in rows if r[0] == WARN)
    if fails:
        print("结论：%d 项失败，%d 项警告。带 [FAIL] 的修掉才能用。" % (fails, warns))
        print("      环境变量：PROTEUS_PDS / PROTEUS_LIBRARY / PROTEUS_PRJ / PROTEUS_PYSITE")
        return 1
    print("结论：关键项全部通过%s。"
          % ("，%d 项警告（相关功能受限）" % warns if warns else ""))
    if verbose:
        print()
        print("解析到的路径：")
        print("  模块目录     :", proteus_env.add_self_to_path())
        try:
            print("  PDS.EXE      :", proteus_env.pds_exe())
        except Exception:
            print("  PDS.EXE      : 未解析")
        try:
            print("  器件库       :", proteus_env.library_dir())
        except Exception:
            print("  器件库       : 未解析")
        print("  解释器       :", sys.executable)
    return 0


if __name__ == "__main__":
    sys.exit(main())

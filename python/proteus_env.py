# -*- coding: utf-8 -*-
"""proteus_env —— 路径与依赖解析（跨机器可移植）。

为什么需要这个文件：本仓库最初是按"作者本机目录结构"写的，脚本里散落着
`sys.path.insert(0, r"C:\\<作者目录>\\gh")`、`r"D:\\Proteus\\LIBRARY"` 这类绝对路径。
别人 clone 下来一定跑不通。现在所有路径都从这里取，不要在别处写死。

三个可配置项（都走环境变量，不设则自动探测）：

    PROTEUS_PYSITE   proteus 依赖模块所在目录。本仓库自带全部 15 个模块，
                     所以默认就是本文件的同级目录 —— 一般**不需要设**。
                     只有在你想用它去覆盖系统 site-packages 里的旧版本时才设。

    PROTEUS_LIBRARY  Proteus 器件库目录（含 DEVICE.LIB 等）。默认按常见安装位置探测。

    PROTEUS_PRJ      工具脚本的默认工程路径（可选）。不设则必须命令行传参。

用法（工具脚本）：

    import sys, os
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
    from proteus_env import add_self_to_path, library_dir, need_pil
    add_self_to_path()                       # 让 import component_codec / papi 生效
    lib = library_dir()                      # 器件库目录
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent      # 本仓库 python/ 目录
REPO_ROOT = HERE.parent


def add_self_to_path():
    """把仓库自带的模块目录放到 sys.path 最前，返回该目录。

    放最前是有意的：仓库里的 `proteus_session.py` / `component_codec.py` 是**打过补丁**
    的版本，如果系统 site-packages 里装过同名模块且排在前面，会静默用回未打补丁的
    版本，表现为"补丁明明打了却没效果"。
    """
    p = str(HERE)
    while p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)
    return p


def pysite():
    """依赖模块目录。PROTEUS_PYSITE 优先，否则用仓库自带目录。"""
    env = os.environ.get("PROTEUS_PYSITE")
    if env:
        return add_path(env)
    return add_self_to_path()


def add_path(directory):
    directory = str(Path(directory).expanduser())
    if not Path(directory).is_dir():
        raise RuntimeError("PROTEUS_PYSITE 指向的目录不存在: %s" % directory)
    if directory in sys.path:
        sys.path.remove(directory)
    sys.path.insert(0, directory)
    return directory


# Proteus 各版本/安装方式的器件库可能落点。注意：库的默认值是
# C:\ProgramData\program\LIBRARY，这个路径在正常安装里并不存在，别信。
_LIBRARY_CANDIDATES = (
    r"D:\Proteus\LIBRARY",
    r"C:\Program Files (x86)\Labcenter Electronics\Proteus 8 Professional\LIBRARY",
    r"C:\Program Files\Labcenter Electronics\Proteus 8 Professional\LIBRARY",
    r"C:\Labcenter Electronics\Proteus 8 Professional\LIBRARY",
    r"C:\ProgramData\Labcenter Electronics\Proteus 8 Professional\LIBRARY",
)


def library_dir(required=True):
    """Proteus 器件库目录：PROTEUS_LIBRARY 优先，否则探测常见位置。

    找不到且 required=True 时抛异常并给出明确指引 —— 器件库读不到的话，
    Circuit 建出来的工程会缺器件定义，Proteus 打开会报红，早失败比晚失败好。
    """
    env = os.environ.get("PROTEUS_LIBRARY")
    if env:
        if not Path(env).is_dir():
            raise RuntimeError("PROTEUS_LIBRARY 指向的目录不存在: %s" % env)
        return env
    for c in _LIBRARY_CANDIDATES:
        if Path(c).is_dir():
            return c
    if not required:
        return None
    raise RuntimeError(
        "找不到 Proteus 器件库目录。请设置环境变量后重试：\n"
        "    set PROTEUS_LIBRARY=C:\\Program Files (x86)\\Labcenter Electronics\\"
        "Proteus 8 Professional\\LIBRARY\n"
        "已尝试: %s" % ", ".join(_LIBRARY_CANDIDATES)
    )


def pds_exe():
    """Proteus 主程序 PDS.EXE：PROTEUS_PDS 优先，否则探测常见安装位置。

    上游库把默认值写成 r"D:\\Proteus\\BIN\\PDS.EXE"，那是作者机器上的路径，
    换台机器就是 FileNotFoundError，所以这里改成探测。
    """
    env = os.environ.get("PROTEUS_PDS")
    if env:
        if not Path(env).is_file():
            raise RuntimeError("PROTEUS_PDS 指向的文件不存在: %s" % env)
        return env
    for c in _PDS_CANDIDATES:
        if Path(c).is_file():
            return c
    raise RuntimeError(
        "找不到 PDS.EXE。请设置环境变量后重试：\n"
        "    set PROTEUS_PDS=C:\\Program Files (x86)\\Labcenter Electronics\\"
        "Proteus 8 Professional\\BIN\\PDS.EXE\n"
        "已尝试: %s" % ", ".join(_PDS_CANDIDATES)
    )


_PDS_CANDIDATES = (
    r"D:\Proteus\BIN\PDS.EXE",
    r"C:\Program Files (x86)\Labcenter Electronics\Proteus 8 Professional\BIN\PDS.EXE",
    r"C:\Program Files\Labcenter Electronics\Proteus 8 Professional\BIN\PDS.EXE",
    r"C:\Labcenter Electronics\Proteus 8 Professional\BIN\PDS.EXE",
)


def default_project():
    """工具脚本的默认工程路径（PROTEUS_PRJ），没有就返回 None。"""
    p = os.environ.get("PROTEUS_PRJ")
    return str(Path(p).expanduser()) if p else None


def need_pil():
    """截图类工具要 Pillow。缺失时给出可执行的安装命令，而不是一句 ImportError。"""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        raise SystemExit(
            "截图/裁剪需要 Pillow，当前解释器没有装：\n"
            "    %s -m pip install pillow\n"
            "（LCD 文本判读请改用 tools/lcdgpio.py，它不需要 Pillow）" % sys.executable
        )

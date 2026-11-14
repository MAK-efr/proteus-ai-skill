"""Small Proteus 8.16 desktop API using native menus, ADI and exported SDF."""
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

import proteus_native as native

native.u.SendMessageTimeoutW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM,
                                       w.UINT, w.UINT, c.POINTER(c.c_size_t))
native.u.SendMessageTimeoutW.restype = w.LPARAM


def parse_sdf(text):
    """Parse the part/pin tables exported by the native logical SDF compiler."""
    if not text.startswith("ISIS SCHEMATIC DESCRIPTION FORMAT"):
        raise ValueError("Not an ISIS SDF netlist")
    # ponytail: exported one-line part records; reject malformed tables instead of guessing.
    def fields(line):
        result, current, quoted, pos = [], [], False, 0
        while pos < len(line):
            char = line[pos]
            if char == '"':
                if quoted and line[pos:pos + 2] == '""':
                    current.append('"')
                    pos += 1
                else:
                    quoted = not quoted
            elif char == ',' and not quoted:
                result.append(''.join(current))
                current = []
            else:
                current.append(char)
            pos += 1
        if quoted:
            raise ValueError("Unterminated SDF string")
        return result + [''.join(current)]

    def count(value):
        value = int(value)
        if not 0 <= value <= 100000:
            raise ValueError("Invalid SDF table count")
        return value

    lines = [line for line in text.splitlines() if line.strip()]
    parts, nets = {}, []
    have_parts = False
    for index, line in enumerate(lines):
        if line.startswith("*PARTLIST,"):
            size = count(line.split(",")[1])
            if have_parts or index + 1 + size > len(lines):
                raise ValueError("Duplicate or truncated SDF part table")
            have_parts = True
            for entry in lines[index + 1:index + 1 + size]:
                row = fields(entry)
                if len(row) < 3 or row[0] in parts:
                    raise ValueError("Malformed or duplicate SDF part")
                parts[row[0]] = dict(device=row[1], value=row[2], properties=dict(
                    item.split("=", 1) for item in row[3:] if "=" in item))
        if line.startswith("*NETLIST,"):
            if not have_parts:
                raise ValueError("Missing SDF part table")
            pos = index + 1
            for _ in range(count(line.split(",")[1])):
                if pos >= len(lines):
                    raise ValueError("Truncated SDF net table")
                header = fields(lines[pos])
                if len(header) < 2 or any("=" not in item for item in header[2:]):
                    raise ValueError("Malformed SDF net header")
                name, pin_count = header[:2]
                properties = dict(item.split("=", 1) for item in header[2:])
                pin_count = count(pin_count)
                pos += 1
                if pos + pin_count > len(lines):
                    raise ValueError("Truncated SDF pin table")
                pins, terminals = [], []
                for _ in range(pin_count):
                    endpoint = fields(lines[pos])
                    pos += 1
                    # PATCH 2026-11-10: 补 GT —— Proteus 8.16 给"网络标签"端点
                    # 输出的类型是 GT（此前只认 LBL/PT/PR/IT/OT/BT），缺它时
                    # export_netlist 会在文件写完之后抛 Unsupported SDF endpoint。
                    if len(endpoint) == 2 and endpoint[1] in {"LBL", "PT", "PR", "IT", "OT", "BT", "GT"}:
                        ref, kind = endpoint
                        terminals.append(dict(name=ref, kind=kind))
                        continue
                    elif len(endpoint) == 3:
                        ref, kind, pin = endpoint
                    else:
                        raise ValueError(f"Unsupported SDF endpoint: {endpoint}")
                    if pin is not None and ref not in parts:
                        raise ValueError(f"Unknown SDF part {ref}")
                    pins.append(dict(ref=ref, kind=kind, pin=pin))
                nets.append(dict(name=name, pins=pins, terminals=terminals, properties=properties))
            if pos != len(lines):
                raise ValueError("Unexpected records after SDF net table")
            return dict(parts=parts, nets=nets)
    raise ValueError("Missing SDF net table")


class Session:
    """Owns one Proteus process. Pass a disposable project copy, not an original."""

    def __init__(self, project, executable=None):
        if executable is None:
            try:
                from proteus_env import pds_exe
                executable = pds_exe()
            except ImportError:
                import os
                executable = os.environ.get("PROTEUS_PDS", r"D:\Proteus\BIN\PDS.EXE")
        self.project = Path(project).resolve(strict=True)
        executable = Path(executable).resolve(strict=True)
        if self.project.suffix.lower() != ".pdsprj" or executable.name.upper() != "PDS.EXE":
            raise ValueError("Expected a .pdsprj project and PDS.EXE")
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 1  # NETPATCH: 原值 0(SW_HIDE)，与 _ready() 要求 visible 矛盾，改为 SW_SHOWNORMAL
        self.process = subprocess.Popen([str(executable), str(self.project)], startupinfo=startup)
        self.pid = self.process.pid
        def launched():
            for row in native.windows(self.pid):
                if row["title"] == "Reports Uploading":
                    native.u.PostMessageW(row["hwnd"], 0x10, 0, 0)  # dismiss; do not submit a report
                if row["title"] == "Fatal Error" or any("Bad object record" in x["title"] for x in row["children"]):
                    raise RuntimeError(f"Proteus rejected the project: {json.dumps(row)}")
                # 强杀过 Proteus 后会出现"是否恢复自动保存的工程?"模态框，会卡死整个启动流程。
                # 我们总是从原始工程的新副本启动，恢复出来的半成品只会污染实验，一律点"否"。
                if row["cls"] == "#32770":
                    kids = row.get("children") or []
                    blob = " ".join(str(x.get("title") or "") for x in kids).lower()
                    if "autosave" in blob or "自动保存" in blob:
                        for kid in kids:
                            if kid.get("cls") == "Button" and kid.get("control_id") == 209:  # IDNO
                                native.u.PostMessageW(kid["hwnd"], 0xF5, 0, 0)
                                break
            return self._ready()
        try:
            self._wait(launched, timeout=45)
        except Exception:
            # No mutation has been requested yet; clean up this newly launched process only.
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            raise
        # ponytail: allow native child views to settle; no documented application-ready event exists.
        time.sleep(1)

    def _wait(self, condition, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Proteus exited: {self.process.returncode}")
            result = condition()
            if result:
                return result
            time.sleep(.1)
        raise TimeoutError(f"Proteus PID {self.pid}: " + json.dumps(native.windows(self.pid), ensure_ascii=True))

    def _ready(self):
        rows = [row for row in native.windows(self.pid) if native.u.GetMenu(row["hwnd"])
                and row["title"].startswith(self.project.stem + " - ") and row["enabled"]]
        return rows[0] if len(rows) == 1 else None

    # NETPATCH: 中文汉化版菜单映射（英文原始路径 -> 本机汉化路径）。找不到英文时自动回退中文。
    MENU_ALIASES = {
        "Tool/Netlist Compiler": "工具(T)/编译网络表",
        "Tool/ASCII Data Import Tool": "工具(T)/ASCII数据输入工具",
        "File/Save Project": "文件(F)/保存工程(S)",
        "File/Exit Application": "文件(F)/退出程序(x)",
    }

    def menu(self, path):
        window = self._wait(self._ready)
        wanted = {path}
        alt = self.MENU_ALIASES.get(path)
        if alt:
            wanted.add(alt)
        found = [item for item in native.menus(native.u.GetMenu(window["hwnd"]))
                 if item["path"] in wanted and item["enabled"]]
        if len(found) != 1:
            raise ValueError(f"Missing or disabled menu: {path}")
        if not native.u.PostMessageW(window["hwnd"], 0x111, found[0]["command_id"], 0):
            raise c.WinError(c.get_last_error())

    def _dialog(self, title):
        def find():
            rows = [row for row in native.windows(self.pid)
                    if row["cls"] == "#32770" and row["title"] == title and row["enabled"]]
            return rows[0] if len(rows) == 1 else None
        return self._wait(find)

    @staticmethod
    def _click(dialog, label):
        buttons = [item for item in dialog["children"] if item["cls"] == "Button"
                   and item["title"] == label and item["enabled"]]
        if len(buttons) != 1:
            raise ValueError(f"Missing or ambiguous button: {label}")
        if not native.u.PostMessageW(buttons[0]["hwnd"], 0xF5, 0, 0):
            raise c.WinError(c.get_last_error())

    def _file_dialog(self, title, path):
        dialog = self._dialog(title)
        edits = [item for item in dialog["children"]
                 if item["cls"] == "Edit" and item["control_id"] == 1148]
        if len(edits) != 1:
            raise ValueError("Unrecognized native filename control")
        buffer, result = c.create_unicode_buffer(str(path)), c.c_size_t()
        # Cross-process Edit text needs WM_SETTEXT; SetWindowText only changed the cached caption.
        if not native.u.SendMessageTimeoutW(edits[0]["hwnd"], 0xC, 0, c.addressof(buffer),
                                           2, 3000, c.byref(result)) or not result.value:
            raise RuntimeError("Could not set native filename")
        if not native.u.PostMessageW(dialog["hwnd"], 0x111, 1, 0):
            raise c.WinError(c.get_last_error())

    def export_netlist(self, destination):
        destination = Path(destination).resolve()
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.menu("Tool/Netlist Compiler")
        dialog = self._dialog("Netlist Compiler")
        for label in ["File(s)", "Logical", "Whole Design", "Flatten", "SDF", "&OK"]:
            self._click(dialog, label)
        # ponytail: these are the exact English labels observed in Proteus 8.16, including its typo.
        self._file_dialog("Complile Netlist", destination)
        self._wait(lambda: destination.exists() and self._ready())
        return parse_sdf(destination.read_text(encoding="cp1252"))

    def netlist(self):
        temporary = self.project.parent / f"api-{uuid.uuid4().hex}.sdf"
        result = self.export_netlist(temporary)
        temporary.replace(temporary.with_suffix('.keep'))
        return result

    def set_properties(self, ref, **properties):
        if not re.fullmatch(r"[A-Za-z0-9_:+.\-]+", ref) or not properties:
            raise ValueError("A literal reference and at least one property are required")
        if {"REF", "DEVICE"} & properties.keys():
            raise ValueError("REF and DEVICE changes require a separate structural operation")
        if ref not in self.netlist()["parts"]:
            raise KeyError(ref)
        lines = [f'IF REF="{ref}"']
        for key, value in properties.items():
            value = str(value)
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or any(ch in value for ch in '\r\n"\x00'):
                raise ValueError("Invalid ADI property")
            value.encode("ascii")
            lines.append(f' {key}="{value}"')
        path = self.project.parent / f"api-{uuid.uuid4().hex}.adi"
        path.write_text("\n".join(lines + ["END", ""]), encoding="ascii")
        try:
            self.menu("Tool/ASCII Data Import Tool")
            self._file_dialog("ASCII Data Import", path)
            self._wait(self._ready)
            actual = self.netlist()["parts"][ref]
            for key, value in properties.items():
                observed = actual["value"] if key == "VALUE" else actual["properties"].get(key)
                if observed != str(value):
                    raise RuntimeError(f"ADI read-back mismatch: {ref}.{key}: {observed!r}")
            return actual
        finally:
            path.replace(path.with_suffix('.keep'))

    def save(self):
        before = self.project.stat().st_mtime_ns
        self.menu("File/Save Project")
        handled = set()
        def saved():
            for dialog in native.windows(self.pid):
                if dialog["hwnd"] not in handled and any(
                        "File will be saved in version 8.16 format." in child["title"]
                        for child in dialog["children"]):
                    self._click(dialog, "&OK")
                    handled.add(dialog["hwnd"])
            return self.project.stat().st_mtime_ns != before and self._ready()
        self._wait(saved)
        return self.project

    def close(self):
        if self.process.poll() is not None:
            return self.process.returncode
        time.sleep(1)
        self.menu("File/Exit Application")
        # Do not dismiss an unexpected unsaved-changes dialog or terminate another process.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return self.process.returncode
            for dialog in native.windows(self.pid):
                if dialog["title"] == "Fatal Error":
                    self.process.terminate()
                    self.process.wait(timeout=5)
                    raise RuntimeError(f"Proteus crashed during exit: {json.dumps(dialog)}")
            time.sleep(.1)
        raise TimeoutError(f"Proteus PID {self.pid} did not exit: {json.dumps(native.windows(self.pid))}")

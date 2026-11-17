#!/usr/bin/env python3
"""真实控制台按键端到端测试：
CreateProcess 启动 dist/claude_novel.exe（新控制台）→ WriteConsoleInputW 注入
真实方向键/回车事件 → ReadConsoleOutputW 读屏验证对话框选择是否生效。
验证的就是用户反馈的"Choose a file 选不了"。"""
import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

k32 = ctypes.windll.kernel32

CREATE_NEW_CONSOLE = 0x00000010
STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
KEY_EVENT = 0x0001
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
OPEN_EXISTING = 3


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]


class CHAR_UNION(ctypes.Union):
    _fields_ = [("UnicodeChar", ctypes.c_wchar), ("AsciiChar", ctypes.c_char)]


class CHAR_INFO(ctypes.Structure):
    _fields_ = [("Char", CHAR_UNION), ("Attributes", ctypes.c_uint16)]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", ctypes.c_int32),
                ("wRepeatCount", ctypes.c_uint16),
                ("wVirtualKeyCode", ctypes.c_uint16),
                ("wVirtualScanCode", ctypes.c_uint16),
                ("UnicodeChar", ctypes.c_wchar),
                ("dwControlKeyState", ctypes.c_uint32)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", ctypes.c_uint16), ("Event", KEY_EVENT_RECORD)]


class STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("lpReserved", ctypes.c_wchar_p),
                ("lpDesktop", ctypes.c_wchar_p), ("lpTitle", ctypes.c_wchar_p),
                ("dwX", ctypes.c_uint32), ("dwY", ctypes.c_uint32),
                ("dwXSize", ctypes.c_uint32), ("dwYSize", ctypes.c_uint32),
                ("dwXCountChars", ctypes.c_uint32), ("dwYCountChars", ctypes.c_uint32),
                ("dwFillAttribute", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
                ("wShowWindow", ctypes.c_uint16), ("cbReserved2", ctypes.c_uint16),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", ctypes.c_void_p),
                ("hStdOutput", ctypes.c_void_p), ("hStdError", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", ctypes.c_void_p), ("hThread", ctypes.c_void_p),
                ("dwProcessId", ctypes.c_uint32), ("dwThreadId", ctypes.c_uint32)]


VK = {"up": 0x26, "down": 0x28, "enter": 0x0D, "esc": 0x1B, "x": 0x58}
SCAN = {"up": 0x48, "down": 0x50, "left": 0x4B, "right": 0x4D,
        "enter": 0x1C, "esc": 0x01, "x": 0x2D}


def spawn(cwd: str, target: str = "exe") -> int:
    si = STARTUPINFO()
    si.cb = ctypes.sizeof(STARTUPINFO)
    pi = PROCESS_INFORMATION()
    if target == "exe":
        cmd = f'"{Path(cwd) / "claude_novel.exe"}"'
    else:
        cmd = f'{sys.executable} "{Path(cwd).parent / "claude_novel.py"}"'
    ok = k32.CreateProcessW(None, cmd, None, None, False,
                            CREATE_NEW_CONSOLE, None, cwd,
                            ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise OSError("CreateProcess failed")
    k32.CloseHandle(pi.hThread)
    k32.CloseHandle(pi.hProcess)
    return pi.dwProcessId


def con_handles():
    h_in = k32.CreateFileW("CONIN$", GENERIC_READ | GENERIC_WRITE, 3,
                           None, OPEN_EXISTING, 0, None)
    h_out = k32.CreateFileW("CONOUT$", GENERIC_READ | GENERIC_WRITE, 3,
                            None, OPEN_EXISTING, 0, None)
    for h, name in ((h_in, "CONIN$"), (h_out, "CONOUT$")):
        if h in (-1, None) or h == ctypes.c_void_p(-1).value:
            raise OSError(f"open {name} failed: GetLastError={k32.GetLastError()}")
    return h_in, h_out


def send_key(h_in, name: str, char: str = "\x00"):
    rec = INPUT_RECORD()
    rec.EventType = KEY_EVENT
    rec.Event.bKeyDown = 1
    rec.Event.wRepeatCount = 1
    rec.Event.wVirtualKeyCode = VK[name]
    rec.Event.wVirtualScanCode = SCAN[name]  # 物理按键必带扫描码，缺它 CRT 会吞事件
    rec.Event.UnicodeChar = char or "\x00"
    written = ctypes.c_uint32()
    if not k32.WriteConsoleInputW(h_in, ctypes.byref(rec), 1, ctypes.byref(written)):
        raise OSError(f"WriteConsoleInput failed: {k32.GetLastError()}")
    # 再发一个 key-up，完全模拟物理按键
    rec.Event.bKeyDown = 0
    k32.WriteConsoleInputW(h_in, ctypes.byref(rec), 1, ctypes.byref(written))


def read_screen(h_out, cols=120, rows=40) -> list[str]:
    buf = (CHAR_INFO * (cols * rows))()
    region = SMALL_RECT(0, 0, cols - 1, rows - 1)
    got = COORD(cols, rows)
    origin = COORD(0, 0)
    if not k32.ReadConsoleOutputW(h_out, buf, got, origin, ctypes.byref(region)):
        return []
    lines = []
    for y in range(rows):
        line = "".join(buf[y * cols + x].Char.UnicodeChar or " "
                       for x in range(cols))
        lines.append(line.rstrip())
    return lines


def dump_screen(h_out, rows=40, lo=0, hi=40, tag=""):
    scr = read_screen(h_out, rows=rows)
    print(f"--- screen {tag} ---")
    for i in range(lo, min(hi, len(scr))):
        if scr[i].strip():
            print(f"{i:3d} |{scr[i][:100]}|")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "exe"
    dist = str(Path(__file__).resolve().parent.parent / "dist")
    pid = spawn(dist, target)
    print(f"spawned pid={pid} ({target}), waiting for dialog ...")
    time.sleep(2.5)
    k32.FreeConsole()
    if not k32.AttachConsole(pid):
        print("FAIL: AttachConsole")
        k32.TerminateProcess(k32.OpenProcess(1, False, pid), 1)
        return 1
    h_in, h_out = con_handles()
    ok = True

    scr = read_screen(h_out)
    dialog_row = next((i for i, l in enumerate(scr)
                      if "Choose a file" in l or "选择文件" in l), None)
    print("dialog on screen:", dialog_row is not None)
    ok &= dialog_row is not None
    if dialog_row is None:
        dump_screen(h_out, tag="startup")

    def marker_row():
        s = read_screen(h_out)
        rows = [i for i, l in enumerate(s) if "❯" in l and dialog_row and abs(i - dialog_row) <= 10]
        return rows[0] if rows else None

    r0 = marker_row()
    print("initial marker row:", r0)
    ok &= r0 is not None

    send_key(h_in, "down")
    time.sleep(0.6)
    r1 = marker_row()
    print("after DOWN marker row:", r1)
    ok &= r1 is not None and r0 is not None and r1 == r0 + 1
    if not (r1 and r1 == r0 + 1):
        dump_screen(h_out, lo=max(0, dialog_row - 3), hi=dialog_row + 9, tag="after DOWN")

    def queue_count():
        n = ctypes.c_uint32()
        k32.GetNumberOfConsoleInputEvents(h_in, ctypes.byref(n))
        return n.value

    send_key(h_in, "x", "x")
    time.sleep(0.2)
    print("queue after 'x':", queue_count())
    send_key(h_in, "enter", "\r")
    time.sleep(0.05)
    print("queue right after ENTER:", queue_count())
    time.sleep(2.0)
    print("queue 2s after ENTER:", queue_count())
    # 进程还活着吗？
    hproc = k32.OpenProcess(0x0400 | 0x0010, False, pid)  # QUERY_INFORMATION|CREATE_THREAD_READ? 用 QUERY+WAIT
    code = ctypes.c_uint32()
    if hproc:
        k32.GetExitCodeProcess(hproc, ctypes.byref(code))
        print("child exit code:", code.value, "(259=still alive)")
        k32.CloseHandle(hproc)
    scr = read_screen(h_out)
    opened = any("(main)" in l for l in scr) or any(l.startswith("> ") for l in scr)
    print("book opened after ENTER:", opened)
    ok &= opened
    if not opened:
        dump_screen(h_out, tag="after ENTER")

    print("RESULT:", "PASS" if ok else "FAIL")
    k32.FreeConsole()
    h = k32.OpenProcess(1, False, pid)
    if h:
        k32.TerminateProcess(h, 0)
        k32.CloseHandle(h)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

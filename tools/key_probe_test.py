#!/usr/bin/env python3
"""隔离实验：新控制台里跑一个最小按键探针，注入真实键事件，看 msvcrt 读到什么。"""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from real_key_test import send_key, con_handles, k32, CREATE_NEW_CONSOLE
import real_key_test as rkt

PROBE = r"""
import msvcrt, sys, time
out = open(r"%s", "w", encoding="utf-8")
deadline = time.time() + 6
n = 0
while n < 3 and time.time() < deadline:
    if msvcrt.kbhit():
        c = msvcrt.getwch()
        out.write(f"{ord(c):04X} ")
        out.flush()
        n += 1
    time.sleep(0.01)
out.write("|done")
out.close()
"""

result_path = Path(__file__).resolve().parent / "_probe_result.txt"
result_path.write_text("", encoding="utf-8")

p = subprocess.Popen(
    [sys.executable, "-c", PROBE % result_path],
    creationflags=CREATE_NEW_CONSOLE)
print("probe pid:", p.pid)
time.sleep(1.5)

k32.FreeConsole()
assert k32.AttachConsole(p.pid), "attach failed"
h_in, _ = con_handles()

send_key(h_in, "x", "x")
time.sleep(0.3)
send_key(h_in, "down")
time.sleep(0.3)
send_key(h_in, "enter", "\r")
time.sleep(1.0)
k32.FreeConsole()

print("probe read:", result_path.read_text(encoding="utf-8"))
print("expect: 0058 (x) then 00E0 0050 (\\xe0 P) then 000D (\\r)")
p.wait(timeout=10)

#!/usr/bin/env python3
"""打印 --script 输出剥离 ANSI 后的指定行区间。用法: python tools/dump.py <file> [start] [end]"""
import re
import sys

ANSI_TOKEN_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b."
)
raw = open(sys.argv[1], "rb").read().decode("utf-8", "replace")
raw2 = re.sub(r"\x1b\[[0-9;?]*H", "\n", raw)
plain = ANSI_TOKEN_RE.sub("", raw2)
lines = [l for l in plain.splitlines()]
start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
end = int(sys.argv[3]) if len(sys.argv) > 3 else len(lines)
for i in range(start, min(end, len(lines))):
    print(f"{i:4d} {repr(lines[i][:110])}")

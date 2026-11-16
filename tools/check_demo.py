#!/usr/bin/env python3
"""分析 --demo 输出：剥离 ANSI 后检查结构完整性。"""
import re
import sys
from pathlib import Path

ANSI_TOKEN_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b."
)

raw = Path(sys.argv[1]).read_bytes().decode("utf-8", "replace")
# 管道捕获时 CUP 绝对定位 = 逻辑换行；先替换再剥其余 ANSI
raw2 = re.sub(r"\x1b\[[0-9;?]*H", "\n", raw)
plain = ANSI_TOKEN_RE.sub("", raw2)
lines = plain.splitlines()
print(f"total lines: {len(lines)}")

checks = {
    "welcome box": any("Welcome to Claude Code" in l for l in lines),
    "command list": any("/boss" in l for l in lines),
    "user prompt echo": any(l.startswith("> ") for l in lines),
    "tool line": any(l.startswith("⏺ Read(") for l in lines),
    "tool result": any("⎿ Read" in l for l in lines),
    "chapter title": any("山门外的少年" in l for l in lines),
    "novel text": any("青云山" in l for l in lines),
    "spinner row": any("✳" in l and "(esc to interrupt)" in l for l in lines),
    "cwd row": any("~/books" in l for l in lines),
    "input box top": any(l.startswith("╭") and l.endswith("╮") for l in lines),
    "input prompt": any("❯" in l for l in lines),
    "hint row": any("? for shortcuts" in l for l in lines),
    "token status": any("tokens" in l for l in lines),
}
fails = 0
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    fails += 0 if ok else 1

maxw = max((len(l) for l in lines), default=0)  # 近似（含宽字符会偏大）
print(f"max raw line len (bytes-ish): {maxw}")
print("result:", "ALL PASS" if fails == 0 else f"{fails} FAILED")
sys.exit(1 if fails else 0)

#!/usr/bin/env python3
"""分析 --script 无头交互输出：CUP 视作换行后做结构断言。"""
import json
import re
import sys
from pathlib import Path

ANSI_TOKEN_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b."
)
TOOL = "⏺"


def load(path):
    raw = Path(path).read_bytes().decode("utf-8", "replace")
    raw2 = re.sub(r"\x1b\[[0-9;?]*H", "\n", raw)
    plain = ANSI_TOKEN_RE.sub("", raw2)
    return [l for l in plain.splitlines() if l.strip()]


def main():
    lines = load(sys.argv[1])
    prog = json.loads(Path(".claude-reader.json").read_text(encoding="utf-8"))
    checks = {
        "book dialog": any("Choose a file" in l for l in lines),
        "book selected echo (gbk)": any("GBK编码测试" in l for l in lines),
        "user prompt echo": any(l.startswith("> ") for l in lines),
        "fake tool Read": any(l.startswith(TOOL + " Read(") for l in lines),
        "chapter title (gbk decoded)": any("星港来客" in l for l in lines),
        "streamed text (gbk decoded)": any("林小满" in l for l in lines),
        "boss fake log": (any(l.startswith(TOOL + t) for l in lines
                              for t in (" Read(", " Edit(", " Bash(", " Grep("))
                         and any("⎿" in l and ("lines" in l or "Updated" in l
                                               or "passed" in l or "matches" in l)
                                 for l in lines)),
        "speed cmd ack": any("speed = 9" in l for l in lines),
        "progress speed==9": prog.get("speed") == 9,
        "progress book set": bool(prog.get("book")),
    }
    bad = 0
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
        bad += 0 if v else 1
    print("non-empty lines:", len(lines))
    print("result:", "ALL PASS" if bad == 0 else f"{bad} FAILED")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

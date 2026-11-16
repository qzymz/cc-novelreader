#!/usr/bin/env python3
import re
import sys

ANSI_TOKEN_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b."
)
raw = open(sys.argv[1], "rb").read().decode("utf-8", "replace")
plain = ANSI_TOKEN_RE.sub("", raw)
lines = plain.splitlines()
for i, l in enumerate(lines):
    if "Read(" in l or "╭" in l or "╮" in l or "❯" in l:
        print(i, repr(l[:110]))

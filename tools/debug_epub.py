#!/usr/bin/env python3
"""调试：构建样例 EPUB 并用 ElementTree 解析，精确定位 XML 问题。"""
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claude_novel as cn

tmp = Path(__file__).resolve().parent / "_tmp_epub"
tmp.mkdir(exist_ok=True)
epub = tmp / "sample.epub"
cn.make_sample_epub(epub)

with zipfile.ZipFile(epub) as zf:
    for name in ("META-INF/container.xml", "OEBPS/content.opf"):
        raw = zf.read(name)
        print(f"--- {name} ({len(raw)} bytes) ---")
        print(raw.decode("utf-8")[:600])
        try:
            ET.fromstring(raw)
            print("=> XML OK")
        except ET.ParseError as e:
            print("=> XML ERROR:", e)

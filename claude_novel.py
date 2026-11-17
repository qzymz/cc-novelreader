#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
claude_novel —— 外观 1:1 复刻 Claude Code 终端外壳的摸鱼小说阅读器

- 单文件、零第三方依赖，Python 3.9+，Windows / macOS / Linux
- Windows 可用 PyInstaller 打包为单文件 exe（目标机器无需 Python）
- 小说正文以 assistant 流式回复的形式逐字打出
- 老板键瞬间切换为全屏英文假工作状态

用法:
    python claude_novel.py [书路径] [--speed 1-10] [--auto] [--no-tools]
    python claude_novel.py --list
    python claude_novel.py --demo        # 无 TTY 演示
    python claude_novel.py --selftest    # 内置自检
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import random
import re
import shutil
import sys
import time
import unicodedata
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

__version__ = "0.4.0"

IS_WIN = os.name == "nt"

# ══════════════════════════════════════════════════════════════════
# 1. ANSI / 终端基础
# ══════════════════════════════════════════════════════════════════

ESC = "\x1b"
CSI = ESC + "["


def sgr(*codes) -> str:
    return f"{CSI}{';'.join(str(c) for c in codes)}m"


RESET = sgr(0)
BOLD = sgr(1)
DIM = sgr(2)

ORANGE = sgr(38, 2, 217, 119, 87)       # Claude 品牌橙 #D97757（✻ ✳ ⏺ ❯）
ORANGE_DIM = sgr(38, 2, 160, 95, 72)
GRAY = sgr(38, 2, 130, 130, 126)        # dim 灰 #82827E
GRAY_DARK = sgr(38, 2, 98, 98, 96)
RED = sgr(38, 2, 224, 100, 100)

CUP = lambda row, col: f"{CSI}{row};{col}H"
EL = CSI + "2K"
CLEAR_SCROLLBACK = CSI + "3J"
CLEAR_SCREEN = CSI + "2J" + CSI + "H"
HIDE_CURSOR = CSI + "?25l"
SHOW_CURSOR = CSI + "?25h"
RESET_SCROLL_REGION = CSI + "r"

ANSI_TOKEN_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"          # CSI 序列
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC 序列
    r"|\x1b."                           # 其他两字符转义
)


def set_title(title: str):
    sys.stdout.write(f"{ESC}]0;{title}{ESC}\\")


def strip_ansi(s: str) -> str:
    return ANSI_TOKEN_RE.sub("", s)


def enable_windows_vt():
    """旧版 conhost 兜底：仅对 stdout 开启 VT 渲染 + UTF-8 代码页。
    注意：绝不能对 stdin 开 VT 输入——那会让方向键以 ESC[A 序列到达，
    破坏 msvcrt 的 \xe0 前缀解析。"""
    if not IS_WIN:
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(-11, ctypes.byref(mode)):  # STD_OUTPUT_HANDLE
            kernel32.SetConsoleMode(-11, mode.value | 0x0004)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
# 2. CJK 宽度 / 折行（ANSI 感知：转义码计宽 0）
# ══════════════════════════════════════════════════════════════════

def wcwidth(ch: str) -> int:
    """单字符显示宽度。CJK 全角=2，组合符=0，模糊宽度(❯✳等)按西文终端习惯=1。"""
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    ea = unicodedata.east_asian_width(ch)
    return 2 if ea in ("W", "F") else 1


def dwidth(s: str) -> int:
    """显示宽度，ANSI 转义码宽度为 0。纯文本同样适用。"""
    if not s:
        return 0
    if "\x1b" not in s:
        return sum(wcwidth(c) for c in s)
    w = 0
    pos = 0
    for m in ANSI_TOKEN_RE.finditer(s):
        if m.start() > pos:
            w += sum(wcwidth(c) for c in s[pos: m.start()])
        pos = m.end()
    if pos < len(s):
        w += sum(wcwidth(c) for c in s[pos:])
    return w


def vtruncate(s: str, max_cols: int) -> str:
    """按显示宽度截断，保留行内 ANSI 码；若截掉了收尾 RESET 则补一个防颜色泄漏。"""
    if dwidth(s) <= max_cols:
        return s
    out, w, cut = [], 0, False
    pos = 0
    for m in ANSI_TOKEN_RE.finditer(s):
        for ch in s[pos: m.start()]:
            cw = wcwidth(ch)
            if w + cw > max_cols:
                cut = True
                break
            out.append(ch)
            w += cw
        if cut:
            break
        out.append(m.group())
        pos = m.end()
    if not cut:
        for ch in s[pos:]:
            cw = wcwidth(ch)
            if w + cw > max_cols:
                break
            out.append(ch)
            w += cw
    result = "".join(out)
    if "\x1b" in result and not result.endswith(RESET):
        result += RESET
    return result


_WORD_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./")


def _is_word_char(c: str) -> bool:
    return c in _WORD_CHARS


def wrap_text(text: str, width: int) -> list[str]:
    """CJK 感知折行：中文逐字可断，西文单词尽量不拆。"""
    if width < 2:
        width = 2
    lines, cur, cur_w = [], [], 0

    def endline():
        nonlocal cur, cur_w
        lines.append("".join(cur).rstrip())
        cur, cur_w = [], 0

    for ch in text.replace("\n", " "):
        w = wcwidth(ch)
        if ch == " ":
            if cur_w == 0:
                continue
            if cur_w + 1 > width:
                endline()
                continue
            cur.append(" ")
            cur_w += 1
            continue
        if cur_w + w > width:
            # 西文单词不拆：把行尾的单词残余移到下一行
            if _is_word_char(ch) and cur and _is_word_char(cur[-1]):
                word = []
                while cur and _is_word_char(cur[-1]):
                    word.insert(0, cur.pop())
                endline()
                cur.extend(word)
                cur_w = sum(map(wcwidth, word))
            else:
                endline()
        cur.append(ch)
        cur_w += w
    if cur:
        lines.append("".join(cur).rstrip())
    return lines or [""]


def fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


# ══════════════════════════════════════════════════════════════════
# 3. Claude Code 视觉主题
# ══════════════════════════════════════════════════════════════════

SYM_LOGO = "✻"
SYM_SPIN = "✳"
SYM_TOOL = "⏺"
SYM_RESULT = "⎿"
SYM_PROMPT = "❯"

WELCOME_TITLE_PLAIN = f"{SYM_LOGO} Welcome to Claude Code"

WELCOME_COMMANDS = [
    ("book", ("Open book library", "打开书库")),
    ("import", ("Browse & import a book file", "导入书本（浏览文件）")),
    ("toc", ("Show chapter list", "章节目录（分卷折叠）")),
    ("next / prev", ("Next / previous chapter", "下一章 / 上一章")),
    ("m / bm", ("Add bookmark / bookmark list", "加书签 / 书签列表")),
    ("goto · find", ("Quick jump & full-text search", "快速跳转 · 全文搜索")),
    ("speed 1-10", ("Set streaming speed", "流式速度")),
    ("resp · auto N", ("Reply length / auto-read interval", "每轮字数 / 自动阅读间隔")),
    ("boss", ("Boss mode (instant cover)", "老板模式（一键掩护）")),
    ("help", ("Show all shortcuts", "显示全部快捷键")),
]

# Claude Code 风格 spinner 短语（原味趣味进行时，纯 ASCII）
SPINNER_PHRASES = [
    "Acquitting microcharges", "Adding semantic whitespace", "Aliasing datums",
    "Aligning surreals", "Amplifying synergy", "Anticipating outcomes",
    "Applying chewy center", "Architecting paradigms", "Autocompleting emojis",
    "Awaiting API response", "Baiting hooks", "Bending spoons",
    "Boiling the ocean", "Bridging gaps", "Browsing the menu",
    "Bulldozing preconceptions", "Caching iterators", "Calculating intent",
    "Canonicalizing identifiers", "Charging flux capacitor", "Chasing loose threads",
    "Checking vibes", "Churning buffers", "Clarifying generals",
    "Coalescing clouds", "Cobbling paths", "Combobulating", "Compiling just-in-time",
    "Computing bidirectionality", "Condensing vapors", "Consulting oracles",
    "Convincing llamas", "Counting mutations", "Counting to infinity",
    "Cracking knuckles", "Crafting binaries", "Cranking widgets",
    "Crossing streams", "Crunching cookies", "Crystallizing resolves",
    "Debugging debugger", "Decanting samples", "Decomposing headers",
    "Decoupling enigmas", "Defenestrating assumptions", "Delineating namespaces",
    "Denormalizing tensors", "Dereferencing pointers", "Despooking ghosts",
    "Detangling yarn", "Dicing onions", "Discombobulating", "Dispatching moles",
    "Distilling essence", "Dividing by zero", "Downloading more RAM",
    "Dreaming of electric sheep", "Dropping breadcrumbs", "Eating cookies",
    "Eliciting schemas", "Emitting carbon", "Encoding neutrinos",
    "Engaging thrusters", "Equalizing temperatures", "Exorcising ghosts",
    "Extracting truths", "Facilitating standup", "Fermenting ideas",
    "Following the white rabbit", "Forging tokens", "Freeing radicals",
    "Frosting vertices", "Gargling bitstreams", "Generating shaders",
    "Globalizing variables", "Going full brrr", "Greasing gears",
    "Grokking whatever", "Hammering nails", "Herding cats",
    "Honking horns", "Hypnotizing chickens", "Igniting neurons",
    "Implementing wishful thinking", "Inlining caches", "Inserting subliminal messages",
    "Instantiating caffeine", "Interlocking loops", "Interviewing sprites",
    "Inverting matrices", "Ironing curtains", "Jiggling wires",
    "Juggling torches", "Kneading dough", "Laminating wood",
    "Laughing at jokes", "Levitating sheep", "Licking stamps",
    "Loading big llama", "Locating nessie", "Losing electrons",
    "Making bread", "Manifesting values", "Measuring twice",
    "Mining Bitcoin", "Mixing metaphors", "Monkey-typing Shakespeare",
    "Moving fast, breaking things", "Musing over vaporwave", "Nesting functions",
    "Normalizing bytes", "Optimizing suffering", "Organizing drawers",
    "Overclocking GPUs", "Packaging binaries", "Paging Dr. Watsup",
    "Peppering salsas", "Petting llamas", "Pivoting tables",
    "Planning attacks", "Polishing pixels", "Populating caches",
    "Pretending to work", "Pretty-printing exceptions", "Pulling levers",
    "Pushing pixels", "Questioning assumptions", "Queueing jobs",
    "Raiding dungeons", "Reticulating splines", "Reversing entropy",
    "Rewinding timelines", "Riding llamas", "Rolling for initiative",
    "Rounding corners", "Rustling shrubs", "Sailing the high bytes",
    "Salting passwords", "Searching for wally", "Shuffling decks",
    "Simplifying radicals", "Slicing bits", "Smelling roses",
    "Snacking on bytes", "Sorting books", "Spinning wheels",
    "Squashing bugs", "Stacking pancakes", "Steeling beams",
    "Sticking the landing", "Summarizing plots", "Syntactic sugar",
    "Tailoring suits", "Teaching robots to love", "Threading needles",
    "Tidying datasets", "Turtles all the way down", "Twiddling knobs",
    "Unpacking boxes", "Untangling earbuds", "Upvoting memes",
    "Vibe-checking", "Warming up GPUs", "Watering plants",
    "Whispering to GPUs", "Wrangling vectors", "Wrestling bears",
]

USER_PROMPTS = ["继续", "接着读", "然后呢", "继续下一章", "讲下去", "继续讲"]

# ---- 界面语言（/lang zh|en 切换；伪装核心元素恒为英文） ------------
_LANG = "zh"


def set_lang(lang) -> str:
    global _LANG
    _LANG = "zh" if str(lang or "").lower().startswith("zh") else "en"
    return _LANG


def tr(en: str, zh: str) -> str:
    return zh if _LANG == "zh" else en


def L(pair: tuple) -> str:
    """双语描述对 (en, zh) → 当前语言。"""
    return pair[1] if _LANG == "zh" else pair[0]


# 斜杠命令补全菜单（cmd, (英文说明, 中文说明), 需要参数）
FAKE_COMMANDS = [
    ("/auto", ("Auto-read (N = interval s)", "自动阅读（N=间隔秒）"), True),
    ("/bm", ("Bookmarks — enter jump · d delete", "书签（回车跳转 · d 删除）"), False),
    ("/bm add", ("Add bookmark (optional name)", "在当前位置加书签（可命名）"), False),
    ("/book", ("Open book library", "打开书库"), False),
    ("/boss", ("Boss mode", "老板模式"), False),
    ("/ch", ("Jump to chapter N", "跳到第 N 章"), True),
    ("/clear", ("Clear screen", "清屏"), False),
    ("/exit", ("Quit", "退出"), False),
    ("/find", ("Search in book", "全文搜索"), True),
    ("/goto", ("Jump to percent (e.g. 50%)", "按百分比跳转（如 50%）"), True),
    ("/help", ("Show shortcuts", "显示快捷键"), False),
    ("/import", ("Import a book file (browse)", "导入书本（文件选择框）"), False),
    ("/lang", ("UI language: zh / en", "界面语言：中文/English"), True),
    ("/model", ("Set status model name", "设置状态栏模型名"), True),
    ("/next", ("Next chapter", "下一章"), False),
    ("/prev", ("Previous chapter", "上一章"), False),
    ("/resp", ("Reply length in chars", "每轮回复字数"), True),
    ("/speed", ("Streaming speed 1-10", "流式速度 1-10"), True),
    ("/toc", ("Chapter list", "章节目录（分卷）"), False),
    ("/tools", ("Toggle fake tool calls", "假工具行开关"), False),
]

# 空输入框的占位提示（dim，轮换显示）
PLACEHOLDERS = {
    "zh": ['试试输入"继续"', "输入 / 查看命令", "回车继续读下一章",
           "@书名 直接切换", "随便聊点什么也行"],
    "en": ['Try "continue"', "Type / for commands", "继续读下一章",
           "@ 提到一本书直接切换", "Ask anything to keep reading"],
}


def slash_menu_items(typed: str) -> list[tuple[str, str, bool]]:
    """按已输入的命令前缀过滤补全菜单。"""
    if not typed.startswith("/") or " " in typed:
        return []
    return [c for c in FAKE_COMMANDS if c[0].startswith(typed)]


def find_book_by_token(tok: str) -> Path | None:
    """@引用：按文件名子串匹配书库。"""
    tok = tok.lower().lstrip("@")
    if not tok:
        return None
    for p in list_books():
        if tok in p.name.lower():
            return p
    return None


def fake_bash_result(cmd: str) -> str:
    """!bash 模式的假执行结果（纯 ASCII）。"""
    cl = (cmd or "").lower()
    if "test" in cl or "pytest" in cl or "jest" in cl:
        return f"{random.randint(2, 64)} passed, 0 failed ({random.uniform(0.4, 6):.1f}s)"
    if cl.startswith(("ls", "dir")):
        return random.choice(["src  dist  books  README.md",
                              "assets  config  node_modules"])
    if cl.startswith("git status"):
        return "nothing to commit, working tree clean"
    if cl.startswith("git"):
        return random.choice(["done (exit 0)", "committed to main"])
    if "build" in cl or "make" in cl or "tsc" in cl:
        return f"built in {random.uniform(0.8, 9):.1f}s"
    if cl.startswith("echo"):
        return cmd[4:].strip() or "ok"
    if cl.startswith(("rm", "del", "mv", "cp")):
        return "done (exit 0)"
    return "(exit 0)"


# ---- 原生文件选择对话框导入 ---------------------------------------

if IS_WIN:
    import ctypes as _ct
    from ctypes import wintypes as _wt

    class _OPENFILENAMEW(_ct.Structure):
        _fields_ = [
            ("lStructSize", _ct.c_uint32),
            ("hwndOwner", _ct.c_void_p),
            ("hInstance", _ct.c_void_p),
            ("lpstrFilter", _ct.c_wchar_p),
            ("lpstrCustomFilter", _ct.c_wchar_p),
            ("nMaxCustFilter", _ct.c_uint32),
            ("nFilterIndex", _ct.c_uint32),
            ("lpstrFile", _ct.c_wchar_p),
            ("nMaxFile", _ct.c_uint32),
            ("lpstrFileTitle", _ct.c_wchar_p),
            ("nMaxFileTitle", _ct.c_uint32),
            ("lpstrInitialDir", _ct.c_wchar_p),
            ("lpstrTitle", _ct.c_wchar_p),
            ("Flags", _ct.c_uint32),
            ("nFileOffset", _ct.c_uint16),
            ("nFileExtension", _ct.c_uint16),
            ("lpstrDefExt", _ct.c_wchar_p),
            ("lCustData", _ct.c_void_p),
            ("lpfnHook", _ct.c_void_p),
            ("lpTemplateName", _ct.c_wchar_p),
            ("pvReserved", _ct.c_void_p),
            ("dwReserved", _ct.c_uint32),
            ("FlagsEx", _ct.c_uint32),
        ]

    def _win_pick_file() -> Path | None:
        comdlg32 = _ct.windll.comdlg32
        buf = _ct.create_unicode_buffer(1024)
        flt = _ct.create_unicode_buffer(
            "Novels (*.txt;*.epub)\x00*.txt;*.epub\x00All files (*.*)\x00*.*\x00")
        ofn = _OPENFILENAMEW()
        ofn.lStructSize = _ct.sizeof(_OPENFILENAMEW)
        ofn.hwndOwner = _ct.windll.kernel32.GetConsoleWindow() or None
        ofn.lpstrFilter = _ct.cast(flt, _ct.c_wchar_p)
        ofn.nFilterIndex = 1
        ofn.lpstrFile = _ct.cast(buf, _ct.c_wchar_p)
        ofn.nMaxFile = 1024
        ofn.lpstrTitle = "Import a novel"
        ofn.Flags = (0x00080000 |  # OFN_EXPLORER
                     0x00001000 |  # OFN_FILEMUSTEXIST
                     0x00000800 |  # OFN_PATHMUSTEXIST
                     0x00000008 |  # OFN_NOCHANGEDIR
                     0x00000004)   # OFN_HIDEREADONLY
        if comdlg32.GetOpenFileNameW(_ct.byref(ofn)):
            p = Path(buf.value)
            return p if p.is_file() else None
        return None


def pick_file_dialog() -> Path | None:
    """弹出系统文件选择框。测试钩子：CLAUDE_NOVEL_IMPORT_PATH 指定路径直接返回。"""
    hook = os.environ.get("CLAUDE_NOVEL_IMPORT_PATH")
    if hook:
        p = Path(hook)
        return p if p.is_file() else None
    if IS_WIN:
        try:
            return _win_pick_file()
        except Exception:
            return None
    try:  # macOS / Linux 回退 tkinter（标准库自带）
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        p = filedialog.askopenfilename(
            parent=root, title="Import a novel",
            filetypes=[("Novels", "*.txt *.epub"), ("All files", "*.*")])
        root.destroy()
        return Path(p) if p else None
    except Exception:
        return None


def unique_copy(src: Path, dst_dir: Path) -> Path:
    """把 src 复制进 dst_dir；同名同内容=幂等，同名不同内容=自动加 (n) 后缀。"""
    dst_dir.mkdir(parents=True, exist_ok=True)
    target = dst_dir / src.name
    if target.exists():
        if target.read_bytes() == src.read_bytes():
            return target
        stem, suf = target.stem, target.suffix
        i = 1
        while target.exists():
            target = dst_dir / f"{stem} ({i}){suf}"
            i += 1
    shutil.copy2(src, target)
    return target

# ══════════════════════════════════════════════════════════════════
# 4. 键盘输入（跨平台 + 测试脚本注入）
# ══════════════════════════════════════════════════════════════════

class ScriptKeys:
    """--script 测试用：按时刻表注入按键。格式: enter,space,esc,wait:2,type:你好"""

    def __init__(self, spec: str):
        self.queue: list[tuple[float, tuple]] = []
        t = 0.3
        for tok in spec.split(","):
            tok = tok.strip()
            if not tok:
                continue
            if tok.startswith("wait:"):
                t += float(tok[5:])
            elif tok.startswith("type:"):
                for ch in tok[5:]:
                    self.queue.append((t, ("char", ch)))
                    t += 0.06
            else:
                mapping = {
                    "space": ("char", " "), "enter": ("key", "enter"),
                    "esc": ("key", "esc"), "up": ("key", "up"),
                    "down": ("key", "down"), "left": ("key", "left"),
                    "right": ("key", "right"), "tab": ("key", "tab"),
                    "backspace": ("key", "backspace"), "del": ("key", "del"),
                    "home": ("key", "home"), "end": ("key", "end"),
                    "pgup": ("key", "pgup"), "pgdn": ("key", "pgdn"),
                    "ctrlc": ("key", "ctrl-c"), "ctrl-c": ("key", "ctrl-c"),
                    "ctrld": ("key", "ctrl-d"), "ctrl-d": ("key", "ctrl-d"),
                    "ctrlb": ("key", "ctrl-b"), "ctrl-b": ("key", "ctrl-b"),
                    "b": ("char", "b"), "t": ("char", "t"),
                    "n": ("char", "n"), "p": ("char", "p"),
                    "m": ("char", "m"), "d": ("char", "d"),
                    "?": ("char", "?"),
                }
                if tok not in mapping:
                    raise ValueError(f"unknown --script key token: {tok!r}")
                self.queue.append((t, mapping[tok]))
                t += 0.15
        self.t0 = time.time()

    def start(self):
        pass

    def stop(self):
        pass

    def get(self, timeout: float):
        while True:
            now = time.time() - self.t0
            if self.queue and now >= self.queue[0][0]:
                return self.queue.pop(0)[1]
            if not self.queue:
                return ("key", "_script_end")
            # 模拟真实键盘的超时语义：未到事件时刻就按 timeout 返回 None，
            # 让调用方的动画/日志循环得以继续运转
            wait = min(timeout, self.queue[0][0] - now, 0.05)
            if wait > 0:
                time.sleep(wait)
            if time.time() - self.t0 < self.queue[0][0]:
                return None


if IS_WIN:
    import ctypes as _ctypes

    class _KEY_EVENT_RECORD(_ctypes.Structure):
        _fields_ = [("bKeyDown", _ctypes.c_int32),
                    ("wRepeatCount", _ctypes.c_uint16),
                    ("wVirtualKeyCode", _ctypes.c_uint16),
                    ("wVirtualScanCode", _ctypes.c_uint16),
                    ("UnicodeChar", _ctypes.c_wchar),
                    ("dwControlKeyState", _ctypes.c_uint32)]

    class _INPUT_RECORD(_ctypes.Structure):
        _fields_ = [("EventType", _ctypes.c_uint16),
                    ("Event", _KEY_EVENT_RECORD)]
else:
    _INPUT_RECORD = None


class RealKeys:
    """真实终端键盘。Windows: ReadConsoleInputW；POSIX: termios raw + select。"""

    def __init__(self):
        self._posix = not IS_WIN
        self._fd = sys.stdin.fileno() if self._posix else -1
        self._saved = None
        self._dbg = bool(os.environ.get("CLAUDE_NOVEL_DEBUG"))

    def get(self, timeout: float):
        r = self._posix_get(timeout) if self._posix else self._win_get(timeout)
        if self._dbg:
            try:
                with open(app_dir() / ".key_debug.log", "a", encoding="utf-8") as f:
                    f.write(f"{time.time():.3f} get({timeout:.2f}) -> {r!r}\n")
            except OSError:
                pass
        return r

    def start(self):
        if self._posix:
            import termios
            import tty
            try:
                self._saved = termios.tcgetattr(self._fd)
                tty.setraw(self._fd)
            except termios.error:
                self._posix = False  # 非 TTY，退化为非交互

    def stop(self):
        if self._posix and self._saved is not None:
            import termios
            termios.tcsetattr(self._fd, termios.TCSAFLUSH, self._saved)
            self._saved = None

    # -- Windows --------------------------------------------------
    # 用 ReadConsoleInputW 直读输入记录，绕开 CRT getwch 的三宗罪：
    # ① 扩展键第二段存在 CRT 内部暂存区，kbhit() 看不见（超时丢键）；
    # ② 按键释放事件也会吐 \xe0 前缀对（一次方向键触发两次）；
    # ③ 宽字符/多字节序列拆分不稳定。
    _VK_MAP = {0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
               0x21: "pgup", 0x22: "pgdn", 0x23: "end", 0x24: "home",
               0x2D: "ins", 0x2E: "del"}

    def _win_get(self, timeout: float):
        import ctypes

        k32 = ctypes.windll.kernel32
        if not hasattr(self, "_conin"):
            self._conin = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
            self._rec = _INPUT_RECORD()
            self._nread = ctypes.c_uint32()
            self._nev = ctypes.c_uint32()
        deadline = time.time() + timeout
        while True:
            if k32.GetNumberOfConsoleInputEvents(self._conin, ctypes.byref(self._nev)):
                if self._nev.value > 0:
                    if (k32.ReadConsoleInputW(self._conin, ctypes.byref(self._rec), 1,
                                              ctypes.byref(self._nread))
                            and self._nread.value == 1):
                        ev = self._classify_win_event()
                        if ev is not None:
                            if self._dbg:
                                self._dbg_log(f"ev ch={self._rec.Event.UnicodeChar!r} "
                                              f"vk={self._rec.Event.wVirtualKeyCode:#x} -> {ev!r}")
                            return ev
                    continue  # key-up / 非按键事件 / 未知扩展键：跳过继续读
            if time.time() >= deadline:
                return None
            time.sleep(0.004)

    def _classify_win_event(self):
        e = self._rec.Event
        if self._rec.EventType != 1 or not e.bKeyDown:  # 仅按下事件
            return None
        ch, vk = e.UnicodeChar, e.wVirtualKeyCode
        if ch in ("\x00", ""):  # 无字符的扩展键 → 虚拟键码识别
            name = self._VK_MAP.get(vk, "")
            return ("key", name) if name else None
        if ch in ("\r", "\n"):
            return ("key", "enter")
        if ch in ("\x7f", "\x08"):
            return ("key", "backspace")
        if ch == "\t":
            return ("key", "tab")
        if ch == "\x1b":
            return ("key", "esc")
        if ch == "\x03":
            return ("key", "ctrl-c")
        if ch == "\x04":
            return ("key", "ctrl-d")
        if ch == "\x02":
            return ("key", "ctrl-b")
        return ("char", ch)

    def _dbg_log(self, msg: str):
        try:
            with open(app_dir() / ".key_debug.log", "a", encoding="utf-8") as f:
                f.write(f"{time.time():.3f} {msg}\n")
        except OSError:
            pass

    # -- POSIX ----------------------------------------------------
    def _posix_get(self, timeout: float):
        import select
        r, _, _ = select.select([self._fd], [], [], timeout)
        if not r:
            return None
        try:
            b = os.read(self._fd, 1)
        except OSError:
            return ("key", "ctrl-d")
        if not b:
            return ("key", "ctrl-d")
        ch = b.decode("latin-1")
        if ch == "\x1b":
            r2, _, _ = select.select([self._fd], [], [], 0.02)
            if not r2:
                return ("key", "esc")
            b2 = os.read(self._fd, 1)
            if b2 == b"[":
                b3 = os.read(self._fd, 1).decode("latin-1")
                if b3 in "ABCDFH":
                    return ("key", {"A": "up", "B": "down", "C": "right", "D": "left",
                                    "H": "home", "F": "end"}[b3])
                if b3.isdigit():  # ESC[3~（Del）/ ESC[5~（PgUp）等
                    r4, _, _ = select.select([self._fd], [], [], 0.05)
                    if r4:
                        os.read(self._fd, 1)  # 吃掉 '~'
                    return ("key", {"3": "del", "5": "pgup", "6": "pgdn",
                                    "1": "home", "4": "end", "7": "home",
                                    "8": "end", "2": "ins"}.get(b3, ""))
                return ("key", "")
            return ("key", "esc")
        if ch in ("\r", "\n"):
            return ("key", "enter")
        if ch in ("\x7f", "\x08"):
            return ("key", "backspace")
        if ch == "\t":
            return ("key", "tab")
        if ch == "\x03":
            return ("key", "ctrl-c")
        if ch == "\x04":
            return ("key", "ctrl-d")
        if ch == "\x02":
            return ("key", "ctrl-b")
        if ord(ch) >= 0x80:  # 多字节 UTF-8
            need = 1 if ord(ch) & 0xE0 == 0xC0 else 2 if ord(ch) & 0xF0 == 0xE0 else 3
            more = b""
            for _ in range(need):
                r3, _, _ = select.select([self._fd], [], [], 0.1)
                if r3:
                    more += os.read(self._fd, 1)
            try:
                return ("char", (b + more).decode("utf-8"))
            except UnicodeDecodeError:
                return None
        return ("char", ch)


# ══════════════════════════════════════════════════════════════════
# 5. 书籍解析（TXT 编码检测 + 分章）
# ══════════════════════════════════════════════════════════════════

CH_NUM = "0-9０-９零〇一二三四五六七八九十百千万两"

CHAPTER_RE = re.compile(
    r"^\s{0,6}(?:"
    r"第\s*[" + CH_NUM + r"]+\s*[章节卷回部集].{0,28}"
    r"|(?:Chapter|CHAPTER)\s+\d+.{0,36}"
    r"|(?:序章|序言|楔子|引子|前言|尾声|后记|终章|番外).{0,18}"
    r")\s*$"
)


def decode_novel_bytes(data: bytes) -> str:
    """BOM 嗅探 → UTF-8 → GB18030 → Big5 级联检测。"""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"),
                     (b"\xff\xfe", "utf-16"),
                     (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                break
    for enc in ("utf-8", "gb18030", "big5"):
        try:
            s = data.decode(enc)
            if s.count("\ufffd") > len(s) * 0.001:
                continue
            return s
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", "replace")


def find_chapters(text: str) -> list[tuple[str, list[str]]]:
    """按行扫描章节标题，聚合成 [(标题, [段落...])]。段落 = 连续非空行合并。"""
    chapters: list[tuple[str, list[str]]] = []
    cur_title: str | None = None
    cur_paras: list[str] = []
    cur_lines: list[str] = []

    def flush_para():
        nonlocal cur_lines
        if cur_lines:
            joined = "".join(cur_lines).strip()
            if joined:
                cur_paras.append(joined)
            cur_lines = []

    def flush_chapter():
        nonlocal cur_title, cur_paras
        flush_para()
        if cur_paras or (cur_title is not None and VOLUME_RE.match(cur_title)):
            # 无正文的空章节（如重复标题行）丢弃；卷标题例外——留给分卷目录当分组锚点
            chapters.append((cur_title or "正文", cur_paras))
        cur_title, cur_paras = None, []

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip("\u3000 \t")
        if not line.strip():
            flush_para()
            continue
        if len(line) <= 40 and CHAPTER_RE.match(line):
            flush_chapter()
            cur_title = line.strip()
        else:
            cur_lines.append(line.strip())
    flush_chapter()
    if not chapters:
        chapters = [("正文", [])]
    return chapters


_APP_DIR: Path | None = None  # 测试注入用


def app_dir() -> Path:
    if _APP_DIR is not None:
        return _APP_DIR
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def books_dir() -> Path:
    return app_dir() / "books"


def list_books() -> list[Path]:
    d = books_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.suffix.lower() in (".txt", ".epub") and p.is_file())


# ---- EPUB 解析（纯标准库） ----------------------------------------

_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr",
               "section", "article", "blockquote", "br", "hr"}


class _HTMLTextExtractor(HTMLParser):
    """XHTML → 段落文本。块级标签转换行，script/style/head 输出丢弃，
    <title> 内容单独捕获为文档标题。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            if data.strip():
                self.title = data.strip()
            return
        if self._skip:
            return
        if not self.title and len(data.strip()) <= 30:
            self.title = data.strip()
        self.parts.append(data)

    def paragraphs(self) -> tuple[list[str], str]:
        paras = []
        for chunk in "".join(self.parts).split("\n"):
            t = chunk.strip("\u3000 \t\r")
            if t.strip():
                paras.append(t.strip())
        return paras, (self.title or "").strip()


def _epub_doc_text(zf: zipfile.ZipFile, zip_path: str) -> tuple[list[str], str]:
    raw = zf.read(zip_path)
    for enc in ("utf-8", "gb18030"):
        try:
            html = raw.decode(enc)
            if html.count("\ufffd") < len(html) * 0.001:
                break
        except (UnicodeDecodeError, ValueError):
            continue
    else:
        html = raw.decode("utf-8", "replace")
    ex = _HTMLTextExtractor()
    try:
        ex.feed(html)
        ex.close()
    except Exception:
        pass
    return ex.paragraphs()


def _local_tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def parse_epub(path: Path) -> list[tuple[str, list[str]]]:
    """EPUB → [(章节标题, [段落...])]。优先文本内章节正则，否则按 spine 文档分章。"""
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        # container.xml → OPF
        opf_path = None
        if "META-INF/container.xml" in names:
            cont = ET.fromstring(zf.read("META-INF/container.xml"))
            for el in cont.iter():
                if _local_tag(el) == "rootfile":
                    opf_path = el.get("full-path")
                    break
        if not opf_path:  # 兜底：找第一个 .opf
            for n in sorted(names):
                if n.lower().endswith(".opf"):
                    opf_path = n
                    break
        if not opf_path:
            raise ValueError("no OPF found")
        base = posixpath.dirname(opf_path)
        opf = ET.fromstring(zf.read(opf_path))

        manifest: dict[str, str] = {}
        for el in opf.iter():
            if _local_tag(el) == "item":
                iid, href = el.get("id"), el.get("href")
                if iid and href:
                    manifest[iid] = posixpath.normpath(posixpath.join(base, href)) \
                        if base else posixpath.normpath(href)
        spine_ids = [el.get("idref") for el in opf.iter()
                     if _local_tag(el) == "itemref" and el.get("idref")]

        # 依 spine 提取文档文本，过短文档并入下一个
        docs: list[tuple[str, list[str]]] = []
        pending: list[str] = []
        pending_title = ""
        for idref in spine_ids:
            href = manifest.get(idref, "")
            if not href or not re.search(r"\.x?html?$", href, re.I):
                continue
            if href not in names:
                continue
            paras, title = _epub_doc_text(zf, href)
            if not paras:
                continue
            if sum(map(len, paras)) < 40:  # 封面/版权页等短文档
                pending.extend(paras)
                pending_title = pending_title or title
                continue
            if pending:
                paras = pending + paras
                pending = []
            docs.append((title, paras))
        if pending and docs:
            docs[0] = (docs[0][0], pending + docs[0][1])

    if not docs:
        raise ValueError("no readable content in epub")

    # 文本里有"第X章"等标题 → 用正则分章（中文网文 EPUB 常见；h1 标题行已在段落里）
    joined = "\n\n".join("\n\n".join(paras) for _, paras in docs)
    chapters = find_chapters(joined)
    if len(chapters) >= 2:
        return chapters
    # 否则按 spine 文档分章，标题取文档 title / 首段截断
    result = []
    for title, paras in docs:
        t = title if title and len(title) <= 30 else (paras[0][:20] + "…" if paras else "")
        result.append((t or f"Part {len(result) + 1}", paras))
    return result


class Book:
    def __init__(self, path: Path):
        self.path = path
        self.title = path.stem
        self.chapters: list[tuple[str, list[str]]] = []
        self.total_chars = 0
        self._cum: list[list[int]] | None = None   # 每 章 -> 段落累计字符数
        self._ch_cum: list[int] | None = None      # 章 -> 章首累计字符数
        if path.suffix.lower() == ".epub":
            self._load_epub()
        else:
            self._load_txt()

    def _load_txt(self):
        text = decode_novel_bytes(self.path.read_bytes())
        self.chapters = find_chapters(text)
        self._rebuild()

    def _load_epub(self):
        self.chapters = parse_epub(self.path)
        self._rebuild()

    def _rebuild(self):
        self.total_chars = sum(len("".join(paras)) for _, paras in self.chapters)
        self.outline = build_outline(self.chapters)
        self._cum = self._ch_cum = None

    # ---- 位置定位（进度/跳转/百分比共用） --------------------------
    def _ensure_cum(self):
        if self._cum is not None:
            return
        cum, ch_cum, acc = [], [], 0
        for _, paras in self.chapters:
            ch_cum.append(acc)
            row, c = [], 0
            for p in paras:
                c += len(p)
                row.append(c)
            cum.append(row)
            acc += c
        self._cum, self._ch_cum = cum, ch_cum

    def offset_of(self, ch: int, para: int = 0) -> int:
        """某章某段之前的总字符数。"""
        self._ensure_cum()
        if not 0 <= ch < len(self._ch_cum):
            return 0
        base = self._ch_cum[ch]
        if 0 < para <= len(self._cum[ch]):
            base += self._cum[ch][para - 1]
        return base

    def locate(self, chars: int) -> tuple[int, int]:
        """按全书字符偏移定位 (章, 段)。"""
        self._ensure_cum()
        chars = max(0, min(chars, max(self.total_chars - 1, 0)))
        lo, hi = 0, len(self._ch_cum) - 1
        while lo < hi:  # 最后一个 ch_cum <= chars
            mid = (lo + hi + 1) // 2
            if self._ch_cum[mid] <= chars:
                lo = mid
            else:
                hi = mid - 1
        rest = chars - self._ch_cum[lo]
        para = 0
        for i, c in enumerate(self._cum[lo]):
            if rest >= c:
                para = i + 1
            else:
                break
        return lo, min(para, max(len(self._cum[lo]) - 1, 0))


SAMPLE_NOVEL = """第一章 山门外的少年

青云山绵延八百里，主峰终年云雾不散。山脚下的青石路上，一个背着布包的少年仰头望着云端若隐若现的山门，站了足足半个时辰。

少年姓陈名渊，今年十六岁，走了九天才到这里。布包里只有两件换洗衣裳、半袋干粮，还有母亲连夜缝的一双布鞋。

"又是一个想拜仙门的。"路旁茶棚里有人笑道，"这山门一年开一次，收徒不过十人，来的少年少说三千。"

陈渊听见了，却没有动。他知道这些话都是真的，也知道家里再拿不出第二袋干粮送他去别处。这一次，成也要成，不成也要成。

日头偏西的时候，山门方向传来一声钟响。云雾忽然裂开一线，露出一道悬空的白色石阶，三千少年同时站了起来。

第二章 引气入体

入门的考核出乎意料地简单：摸一块半人高的青色石碑，碑身亮起几尺光，便算过关。

轮到陈渊时，碑面沉寂了三息，才极缓地浮起一寸灰白的光。执事长老眼皮都未抬："下品灵根，杂役院，去吧。"

杂役院在后山，种灵田、挑山泉、劈柴烧饭。同期入门的少年们绕着碑光尺许的人称天才，日日在正院听讲，而陈渊每天能做的，是把两亩灵田翻一遍。

但陈渊发现了一件事：每当他挑水走过正院讲经堂的窗下，屋里传出的吐纳口诀，他能一字不差地记住。夜里无人时，他便按着记忆运转那门"引气诀"。

三个月后的一个雨夜，陈渊丹田里忽然升起一丝微凉的气息，顺着记忆里的路线走了整整一个周天。他睁开眼，掌心的水汽凝成了一粒露珠。

第三章 下品灵根的周天

灵田里的稻子一年三熟，是因为田下埋着一道极细的灵脉。杂役们只知道按时耕作，只有陈渊蹲在田埂上，一寸一寸地感受着地气流动的方向。

引气入体之后的半年，他把那丝凉气养成了指头粗细的一缕。下品灵根吸纳灵气极慢，他便不再贪多，只把每一个周天走得滴水不漏——慢，就慢得扎实。

同屋的杂役少年赵大柱笑他："别人筑基都开始淬体了，你还在这儿数呼吸。"

陈渊也笑："我数的是自己的呼吸，不亏。"

这年冬天，正院的天才们在演武场比试御器。一把飞剑失控坠向人群，眼看要落在一名杂役身上。陈渊几乎是本能地伸手一引——那缕被他养得凝实无比的凉气应声而出，飞剑偏了三寸，钉进了雪地。

场边一位白衣长老眯起了眼睛。当晚，杂役院的门被敲响了。
"""


def make_sample_epub(path: Path):
    """生成一本最小合法 EPUB（两章网文样张，含'第X章'标题行）。"""

    def xhtml(title, paras):
        body = "".join(f"<p>{p}</p>" for p in paras)
        return ('<?xml version="1.0" encoding="utf-8"?>\n'
                f"<html><head><title>{title}</title></head><body>"
                f"<h1>{title}</h1>{body}</body></html>")

    ch1 = xhtml("第一章 候鸟", ["北方的秋天来得早，站台上的人裹紧了外套。",
                            "列车进站时，林知夏把画夹抱在胸前，挤进了第二节车厢。",
                            "她要去南方，去把没画完的那片海画完。"])
    ch2 = xhtml("第二章 海边的小屋", ["出租屋的窗户正对着海，房东说涨潮的时候能听见礁石唱歌。",
                                "第一天夜里她没睡，就着台灯把窗外的浪画了三张速写。",
                                "第三张画完，铅笔断了，她笑着骂了一句，像是终于到了目的地。"])
    container = ('<?xml version="1.0"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles></container>')
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">'
           '<metadata><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">候鸟与海</dc:title>'
           '<dc:language xmlns:dc="http://purl.org/dc/elements/1.1/">zh</dc:language>'
           '<dc:identifier id="uid" xmlns:dc="http://purl.org/dc/elements/1.1/">urn:uuid:sample-novel</dc:identifier></metadata>'
           '<manifest>'
           '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
           '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
           '</manifest>'
           '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/ch1.xhtml", ch1)
        zf.writestr("OEBPS/ch2.xhtml", ch2)


def ensure_sample_book():
    d = books_dir()
    d.mkdir(parents=True, exist_ok=True)
    if not (d / "示例小说.txt").exists():  # 缺哪本补哪本（误删自愈）
        (d / "示例小说.txt").write_text(SAMPLE_NOVEL, encoding="utf-8")
    if not (d / "样章.epub").exists():
        try:
            make_sample_epub(d / "样章.epub")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# 6. 进度持久化
# ══════════════════════════════════════════════════════════════════

PROGRESS_FILE = ".claude-reader.json"


def progress_path() -> Path:
    return app_dir() / PROGRESS_FILE


def load_progress() -> dict:
    """进度结构 v2：
    {"last": 书路径, "books": {路径: {ch, para, off, chars, bookmarks[]}}, "settings": {...}}
    自动兼容迁移 v1（{"book": ..., "ch": ...} 扁平结构）。"""
    try:
        data = json.loads(progress_path().read_text(encoding="utf-8"))
    except Exception:
        return {"last": None, "books": {}, "settings": {}}
    if "books" not in data:  # v1 → v2 迁移
        old_book = data.get("book")
        data = {
            "last": old_book,
            "books": ({old_book: {k: v for k, v in data.items()
                                  if k in ("ch", "para", "off", "chars")}}
                      if old_book else {}),
            "settings": {k: v for k, v in data.items()
                         if k in ("speed", "auto", "tools")},
        }
    data.setdefault("last", None)
    data.setdefault("books", {})
    data.setdefault("settings", {})
    return data


def save_progress(data: dict):
    p = progress_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


# ══════════════════════════════════════════════════════════════════
# 7. 终端壳：滚动历史区 + 固定底部区（spinner/输入框/状态栏）
# ══════════════════════════════════════════════════════════════════

FOOTER_H = 5  # cwd/spinner 行 + 上边框 + 输入行 + 下边框 + hint 行


class Shell:
    def __init__(self):
        self.rows, self.cols = self._size()
        self.hcol = 1
        self.cwd = "~/books"
        self.spinner_on = False
        self.spinner_text = ""
        self.spinner_bright = True
        self.input_text = ""
        self.input_pos = 0
        self.placeholder = ""
        self.hint_l = tr("? for shortcuts", "？查看快捷键")
        self.hint_r = ""
        self.model = "opus-4.8"
        self.tokens = 0
        self._flash_restore = None

    # ---- 基础 ----------------------------------------------------
    def _size(self):
        try:
            sz = shutil.get_terminal_size((100, 30))
            return max(sz.lines, 12), max(sz.columns, 40)
        except Exception:
            return 100, 30

    def setup(self):
        enable_windows_vt()
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        self._set_margins()
        self.draw_footer()
        self.park_cursor()

    def teardown(self):
        sys.stdout.write(RESET_SCROLL_REGION + SHOW_CURSOR + RESET + CUP(self.rows, 1) + "\r\n")
        sys.stdout.flush()

    def _set_margins(self):
        bottom = max(self.rows - FOOTER_H, 1)
        sys.stdout.write(f"{CSI}1;{bottom}r" + CUP(bottom, 1))

    @property
    def hist_bottom(self) -> int:
        return max(self.rows - FOOTER_H, 1)

    def wrap_width(self) -> int:
        return max(self.cols - 2, 20)

    def check_resize(self) -> bool:
        r, c = self._size()
        if (r, c) != (self.rows, self.cols):
            self.rows, self.cols = r, c
            self._set_margins()
            self.hcol = 1
            self.draw_footer()
            return True
        return False

    # ---- 历史区 ----------------------------------------------------
    def print_line(self, styled: str = ""):
        """历史区打印一整行（滚动历史）。"""
        styled = vtruncate(styled, self.cols - 1)
        sys.stdout.write(CUP(self.hist_bottom, 1) + EL + styled + "\r\n")
        self.hcol = 1
        sys.stdout.flush()

    def print_block(self, lines):
        for ln in lines:
            self.print_line(ln)

    def type_fragment(self, frag: str):
        """历史区当前行续写（流式打字）。"""
        sys.stdout.write(CUP(self.hist_bottom, self.hcol) + frag)
        self.hcol += dwidth(frag)
        sys.stdout.flush()

    def end_line(self):
        if self.hcol > 1:
            sys.stdout.write("\r\n")
            self.hcol = 1
            sys.stdout.flush()

    # ---- 底部区 ----------------------------------------------------
    def _hint_row(self) -> str:
        left = f" {GRAY}{self.hint_l}{RESET}" if self.hint_l else ""
        if self.hint_r:
            right = f"{GRAY}{self.hint_r}{RESET}"
            pad = self.cols - dwidth(left) - dwidth(right) - 2
            if pad > 0:
                return left + " " * pad + right + "  "
        return left

    def _spinner_row(self) -> str:
        if self.spinner_on:
            star = (BOLD + ORANGE) if self.spinner_bright else ORANGE_DIM
            left = f" {star}{SYM_SPIN}{RESET} {self.spinner_text}"
            esc_hint = f"{GRAY}(esc to interrupt){RESET}"
            pad = self.cols - dwidth(left) - dwidth(esc_hint) - 3
            return left + " " * pad + esc_hint if pad > 0 else left
        cwd = self.cwd
        return f" {GRAY}{vtruncate(cwd, self.cols - 4)}{RESET}"

    def _input_row(self) -> str:
        inner = self.cols - 2
        max_text = inner - 4  # '❯ ' + 右侧 1 空格
        text = self.input_text
        if not text:
            ph = vtruncate(self.placeholder or "", inner - 5)
            shown = f"{GRAY_DARK}{ph}{RESET}" if ph else ""
            body = shown + " " * max(0, inner - 3 - dwidth(ph))
            return "│ " + f"{ORANGE}{SYM_PROMPT}{RESET} " + body + "│"
        w_total = dwidth(text)
        before = text[: self.input_pos]
        if w_total <= max_text:
            win, cursor_off = text, dwidth(before)
        else:
            cursor_off = dwidth(before)
            start_col = max(0, cursor_off - max_text + 2)
            # 从 start_col 起截 max_text 显示列
            out, col = [], start_col
            for ch in text:
                w = wcwidth(ch)
                if col + w > start_col + max_text:
                    break
                if col >= start_col:
                    out.append(ch)
                col += w
            win = "".join(out)
            cursor_off = min(cursor_off - start_col, max_text - 1)
        body = win + " " * max(0, inner - 3 - dwidth(win))
        return "│ " + f"{ORANGE}{SYM_PROMPT}{RESET} " + body + "│"

    def draw_footer(self):
        top = "╭" + "─" * (self.cols - 2) + "╮"
        bot = "╰" + "─" * (self.cols - 2) + "╯"
        rows = [
            (self.rows - 4, self._spinner_row()),
            (self.rows - 3, GRAY + top + RESET),
            (self.rows - 2, GRAY + self._input_row() + RESET),
            (self.rows - 1, GRAY + bot + RESET),
            (self.rows, self._hint_row()),
        ]
        sys.stdout.write("".join(CUP(r, 1) + EL + content for r, content in rows))
        sys.stdout.flush()

    def park_cursor(self):
        max_text = self.cols - 2 - 4
        off = dwidth(self.input_text[: self.input_pos])
        if dwidth(self.input_text) > max_text:
            off = min(off, max_text - 1)
        col = min(4 + off + 1, self.cols - 2)
        sys.stdout.write(SHOW_CURSOR + CUP(self.rows - 2, col))
        sys.stdout.flush()

    def set_spinner(self, on: bool, text: str = ""):
        self.spinner_on, self.spinner_text = on, text

    def flash_hint(self, text: str, seconds: float = 2.0):
        self.hint_l = text
        self.draw_footer()
        if seconds > 0:
            self._flash_restore = (time.time() + seconds, tr("? for shortcuts", "？查看快捷键"))

    def check_flash_restore(self):
        if self._flash_restore and time.time() > self._flash_restore[0]:
            self.hint_l = self._flash_restore[1]
            self._flash_restore = None
            self.draw_footer()

    def update_tokens(self, n: int, pct: float | None = None,
                      ch_pct: float | None = None, redraw: bool = True):
        self.tokens = n
        txt = f"{self.model} · {fmt_tokens(n)} tokens"
        if pct is not None:
            txt += f" · {pct:.0f}%"
        if ch_pct is not None:
            txt += f" · ch {ch_pct:.0f}%"
        self.hint_r = txt
        if redraw:
            self.draw_footer()


# ══════════════════════════════════════════════════════════════════
# 8. 对话框（书籍列表 / 章节目录，仿 Claude 选择框）
# ══════════════════════════════════════════════════════════════════

def draw_dialog(shell: Shell, top_row: int, title: str, items: list[str],
                sel: int, offset: int, max_rows: int) -> int:
    w = min(shell.cols, max(44, max((dwidth(i) for i in items), default=20) + 10))
    head = f"╭─ {title} "
    head += "─" * max(1, w - dwidth(head) - 1) + "╮"
    lines = [GRAY + head + RESET]
    for i, item in enumerate(items[offset: offset + max_rows]):
        idx = offset + i
        inner_w = w - 6
        shown = vtruncate(item, inner_w)
        mark = f"{ORANGE}{SYM_PROMPT} {RESET}" if idx == sel else "  "
        pad = inner_w - dwidth(shown)
        lines.append(GRAY + "│ " + RESET + mark + shown + " " * max(0, pad) + GRAY + " │" + RESET)
    lines.append(GRAY + "╰" + "─" * (w - 2) + "╯" + RESET)
    sys.stdout.write("".join(CUP(top_row + j, 1) + EL + ln for j, ln in enumerate(lines)))
    sys.stdout.flush()
    return len(lines)


def clear_dialog(shell: Shell, top_row: int, n_lines: int):
    sys.stdout.write("".join(CUP(top_row + j, 1) + EL for j in range(n_lines)))
    sys.stdout.flush()


# ══════════════════════════════════════════════════════════════════
# 9. 假工作日志生成器（老板模式，纯 ASCII）
# ══════════════════════════════════════════════════════════════════

FAKE_DIRS = ["src", "src/api", "src/components", "src/lib", "src/utils", "lib",
             "app", "server", "internal", "pkg", "web/src"]
FAKE_FILES = ["auth.ts", "session.ts", "orders.ts", "cache.go", "client.py",
              "router.ts", "store.ts", "index.ts", "main.go", "config.py",
              "pipeline.ts", "schema.ts", "retry.go", "db.py", "hooks.ts"]


def fake_repo_path():
    return f"{random.choice(FAKE_DIRS)}/{random.choice(FAKE_FILES)}"


def fake_tool_group() -> list[str]:
    kind = random.random()
    if kind < 0.34:
        head = f"{ORANGE}{BOLD}{SYM_TOOL}{RESET} {BOLD}Read{RESET}({fake_repo_path()})"
        res = f"  {GRAY}{SYM_RESULT} Read {random.randint(12, 480)} lines ({random.uniform(0.1, 1.4):.1f}s){RESET}"
    elif kind < 0.62:
        head = f"{ORANGE}{BOLD}{SYM_TOOL}{RESET} {BOLD}Edit{RESET}({fake_repo_path()})"
        res = f"  {GRAY}{SYM_RESULT} Updated {random.randint(3, 60)} lines{RESET}"
    elif kind < 0.82:
        cmd = random.choice(["npm test", "go test ./...", "pytest -q", "tsc --noEmit",
                             "eslint src --fix", "cargo check"])
        head = f"{ORANGE}{BOLD}{SYM_TOOL}{RESET} {BOLD}Bash{RESET}({cmd})"
        res = f"  {GRAY}{SYM_RESULT} {random.randint(2, 64)} passed ({random.uniform(0.4, 6):.1f}s){RESET}"
    else:
        g = random.choice(["TODO", "FIXME", "retry", "deprecated", "middleware", "timeout"])
        head = f'{ORANGE}{BOLD}{SYM_TOOL}{RESET} {BOLD}Grep{RESET}("{g}", {random.choice(FAKE_DIRS)}/)'
        res = f"  {GRAY}{SYM_RESULT} {random.randint(0, 30)} matches{RESET}"
    return [head, res]


def assert_ascii_safe():
    for _ in range(300):
        for line in fake_tool_group():
            plain = strip_ansi(line)
            assert not any("\u4e00" <= c <= "\u9fff" for c in plain), f"CJK leaked: {plain!r}"


# ══════════════════════════════════════════════════════════════════
# 10. 应用主逻辑
# ══════════════════════════════════════════════════════════════════

VOLUME_RE = re.compile(
    r"^\s*(?:第\s*[0-9０-９零〇一二三四五六七八九十百千万两]+\s*卷"
    r"|卷\s*[0-9０-９零〇一二三四五六七八九十]+"
    r"|[上中下]部|序卷|总卷).{0,30}$"
)


def build_outline(chapters: list[tuple[str, list[str]]]) -> list[dict]:
    """把扁平章节列表按"卷"分组：[{title, items:[(章下标, 章标题)]}]。
    无卷标题的书 → 单一 None 组包含全部章节。卷标题自带正文时作为
    "卷首"子项保留，保证内容可达。"""
    vols: list[dict] = []
    cur: dict = {"title": None, "items": []}
    for i, (title, paras) in enumerate(chapters):
        if VOLUME_RE.match(title):
            if cur["title"] is not None or cur["items"]:
                vols.append(cur)
            cur = {"title": title, "items": []}
            if paras:
                cur["items"].append((i, "卷首"))
            continue
        cur["items"].append((i, title))
    if cur["title"] is not None or cur["items"]:
        vols.append(cur)
    if not vols:
        vols = [{"title": None, "items": []}]
    return vols


def paras_exhausted(book: "Book", ch: int, para: int) -> bool:
    """某章是否已经读完（无内容或进度段落指针越界）。"""
    paras = book.chapters[ch][1]
    return (not paras) or para >= len(paras)


class App:
    def __init__(self, args):
        self.args = args
        set_lang((load_progress().get("settings", {}) or {}).get("lang")
                 or os.environ.get("CCNOVEL_LANG") or "zh")
        self.shell = Shell()
        self.keys = None
        self.book: Book | None = None
        self.ch = 0
        self.para = 0
        self.off = 0
        self.speed = max(1, min(10, args.speed))
        self.resp = 1200      # 每轮回复的字符额度（段边界停）
        self.auto_delay = 2.2  # 自动阅读轮间隔（秒）
        self.auto = args.auto
        self.show_tools = not args.no_tools
        self.chars_read = 0
        self.running = True
        self.bookmarks: list[dict] = []
        self._cmd_history: list[str] = []
        self._hist_idx: int | None = None
        self._pending: str | None = None
        self._menu: dict | None = None        # 补全菜单状态
        self._ph_i = random.randrange(len(PLACEHOLDERS[_LANG]))
        self._ph_t = time.time()
        self._last_save = 0.0
        self._spin_t = 0.0
        self._spin_bright = False

    # ---- 生命周期 --------------------------------------------------
    def run(self):
        sh = self.shell
        self.keys = ScriptKeys(self.args.script) if self.args.script else RealKeys()
        try:
            self.keys.start()
            sh.setup()
            set_title("Claude Code")
            self._welcome()
            if self.args.book:
                self.book = self._open_book(Path(self.args.book))
            else:
                self.book = self._choose_book()
            if self.book is None:
                return
            self._resume_progress()
            sh.cwd = f"~/books/{self.book.title} (main)"
            self._update_status()
            sh.draw_footer()
            sh.park_cursor()
            self._input_loop()
        finally:
            self._save()
            if self.keys:
                self.keys.stop()
            sh.teardown()

    def _open_book(self, path: Path) -> Book | None:
        try:
            b = Book(path.resolve())  # 统一绝对路径：进度 key / 书库查询 / 续读三方一致
            if not any(paras for _, paras in b.chapters):
                self.shell.print_line(f"{RED}empty book: {path.name}{RESET}")
                return None
            return b
        except Exception as e:
            self.shell.print_line(f"{RED}cannot open {path}: {e}{RESET}")
            return None

    def _resume_progress(self):
        p = load_progress()
        s = p.get("settings", {})
        if s:
            self.speed = s.get("speed", self.speed)
            self.resp = max(50, min(20000, s.get("resp", self.resp)))
            self.auto_delay = max(0.3, min(10.0, s.get("auto_delay", self.auto_delay)))
            self.auto = s.get("auto", self.auto)
            self.show_tools = s.get("tools", self.show_tools)
            if s.get("model"):
                self.shell.model = s["model"]
        entry = p.get("books", {}).get(str(self.book.path))
        if entry:
            self.ch = max(0, min(entry.get("ch", 0), len(self.book.chapters) - 1))
            self.para = entry.get("para", 0)
            self.off = entry.get("off", 0)
            self.chars_read = entry.get("chars", 0) or self.book.offset_of(self.ch, self.para)
            self.bookmarks = entry.get("bookmarks", []) or []
        else:
            last = p.get("last")
            if last and Path(last) == self.book.path:
                self.ch, self.para, self.off = 0, 0, 0
        paras = self.book.chapters[self.ch][1]
        if paras and self.para >= len(paras):
            self.para, self.off = len(paras) - 1, 0
        if not paras:
            self.para = self.off = 0

    def _save(self):
        if self.book is None:
            return
        p = load_progress()
        p["last"] = str(self.book.path)
        p["books"][str(self.book.path)] = {
            "ch": self.ch, "para": self.para, "off": self.off,
            "chars": self.chars_read, "bookmarks": self.bookmarks,
            "total": self.book.total_chars, "ts": time.time(),
        }
        p["settings"] = {"speed": self.speed, "auto": self.auto,
                         "resp": self.resp, "auto_delay": self.auto_delay,
                         "tools": self.show_tools, "model": self.shell.model, "lang": _LANG}
        save_progress(p)
        self._last_save = time.time()

    def _maybe_save(self):
        if time.time() - self._last_save > 2.0:
            self._save()
            self._update_status()

    def _update_status(self):
        pct = chp = None
        if self.book and self.book.total_chars > 0:
            pct = min(100.0, self.chars_read * 100.0 / self.book.total_chars)
            paras = self.book.chapters[self.ch][1]
            chap_total = sum(map(len, paras))
            if chap_total > 0:
                done = (self.book.offset_of(self.ch, self.para) + self.off
                        - self.book.offset_of(self.ch, 0))
                chp = max(0.0, min(100.0, done * 100.0 / chap_total))
        self.shell.update_tokens(self.chars_read // 2, pct, chp)

    # ---- 书签 ------------------------------------------------------
    def _bookmark_add(self, name: str = ""):
        title = self.book.chapters[self.ch][0]
        bm = {"name": (name or title)[:16] or f"ch {self.ch + 1}",
              "ch": self.ch, "para": self.para, "off": self.off,
              "ts": time.strftime("%m-%d %H:%M")}
        self.bookmarks.append(bm)
        self._save()
        self.shell.flash_hint(tr(f"bookmark added: {bm['name']} — /bm to jump", f"已加书签：{bm['name']}（/bm 跳转）"))

    def _bookmark_dialog(self):
        if not self.bookmarks:
            self.shell.flash_hint(tr("no bookmarks — press m or /bm add NAME", "还没有书签——按 m 或 /bm add 名称"))
            return
        sh = self.shell
        max_rows = min(12, sh.rows - FOOTER_H - 3, len(self.bookmarks))
        sel, offset = 0, 0

        def item(i):
            bm = self.bookmarks[i]
            return f"{bm['name']}  ·  {self.book.chapters[bm['ch']][0][:16]}  ·  {bm['ts']}"

        sh.hint_l = tr("enter jump · d delete · esc cancel", "回车 跳转 · d 删除 · Esc 取消")
        sh.draw_footer()
        n_lines = max_rows + 2
        top = sh.hist_bottom - n_lines + 1
        n_lines = draw_dialog(sh, top, tr("Bookmarks", "书签"),
                              [item(i) for i in range(len(self.bookmarks))],
                              sel, offset, max_rows)
        try:
            while True:
                ev = self.keys.get(0.06)
                if ev is None:
                    continue
                kind, val = ev
                act = ""
                if kind == "key":
                    if val == "_script_end":
                        sel, act = 0, "pick"
                    elif val == "up":
                        sel = (sel - 1) % len(self.bookmarks)
                    elif val == "down":
                        sel = (sel + 1) % len(self.bookmarks)
                    elif val == "enter":
                        act = "pick"
                    elif val in ("esc", "ctrl-c", "ctrl-d"):
                        break
                elif kind == "char":
                    if val == " ":
                        sel = (sel + 1) % len(self.bookmarks)
                    elif val == "d":
                        act = "del"
                if not act:
                    if sel < offset:
                        offset = sel
                    if sel >= offset + max_rows:
                        offset = sel - max_rows + 1
                    n_lines = draw_dialog(sh, top, tr("Bookmarks", "书签"),
                                          [item(i) for i in range(len(self.bookmarks))],
                                          sel, offset, max_rows)
                    continue
                if act == "del":
                    name = self.bookmarks[sel]["name"]
                    self.bookmarks.pop(sel)
                    self._save()
                    if not self.bookmarks:
                        sh.flash_hint(f"deleted: {name}")
                        break
                    sel = min(sel, len(self.bookmarks) - 1)
                    n_lines = draw_dialog(sh, top, tr("Bookmarks", "书签"),
                                          [item(i) for i in range(len(self.bookmarks))],
                                          sel, offset, max_rows)
                    continue
                # pick
                bm = self.bookmarks[sel]
                sh.print_line(f"{ORANGE}{SYM_PROMPT}{RESET} {GRAY}{bm['name']} → "
                              f"{self.book.chapters[bm['ch']][0]}{RESET}")
                self._jump_to(bm["ch"], bm["para"], bm["off"])
                break
        finally:
            clear_dialog(sh, top, n_lines)
            sh.hint_l = tr("? for shortcuts", "？查看快捷键")
            sh.draw_footer()
            sh.park_cursor()

    # ---- 快速跳转 / 搜索 ---------------------------------------------
    def _jump_to(self, ch: int, para: int, off: int = 0):
        self.ch, self.para, self.off = ch, para, off
        self.chars_read = self.book.offset_of(ch, para) + off
        self._save()
        self._update_status()
        self._stream_chapter()

    def _find_dialog(self, kw: str):
        kw = kw.strip()
        if not kw:
            self.shell.flash_hint(tr("usage: /find keyword", "用法：/find 关键词"))
            return
        lk = kw.lower()
        hits: list[tuple[int, int, int, str]] = []
        for ci, (title, paras) in enumerate(self.book.chapters):
            for pi, p in enumerate(paras):
                pos = p.lower().find(lk)
                if pos >= 0:
                    start = max(0, pos - 8)
                    ctx = p[start: pos + len(kw) + 12]
                    hits.append((ci, pi, pos, f"{title[:14]} · …{ctx}…"))
                    if len(hits) >= 80:
                        break
            if len(hits) >= 80:
                break
        if not hits:
            self.shell.flash_hint(tr(f"no matches: {kw[:16]}", f"没有找到：{kw[:16]}"))
            return
        sel = self._dialog(f'{tr("Find", "搜索")} "{kw[:12]}"', [h[3] for h in hits])
        if sel is None:
            return
        ci, pi, pos, _ = hits[sel]
        self._jump_to(ci, pi, pos)

    # ---- 欢迎屏 ----------------------------------------------------
    def _welcome(self):
        sh = self.shell
        w = min(sh.cols - 2, 54)
        inner = w - 2
        lines = [GRAY + "╭" + "─" * inner + "╮" + RESET]
        title = f" {ORANGE}{WELCOME_TITLE_PLAIN}{RESET}"
        pad = inner - 1 - dwidth(WELCOME_TITLE_PLAIN)
        lines.append(GRAY + "│" + RESET + title + " " * max(0, pad) + GRAY + "│" + RESET)
        lines.append(GRAY + "│" + " " * inner + "│" + RESET)
        for cmd, desc in WELCOME_COMMANDS:
            left = f"   /{cmd}"
            gap = max(1, 17 - dwidth(left))
            left_full = left + " " * gap
            desc = vtruncate(L(desc), max(8, inner - dwidth(left_full)))
            pad = inner - dwidth(left_full) - dwidth(desc)
            lines.append(GRAY + "│" + RESET + left_full + f"{GRAY}{desc}{RESET}"
                         + " " * max(0, pad) + GRAY + "│" + RESET)
        lines.append(GRAY + "╰" + "─" * inner + "╯" + RESET)
        lines.append("")
        lines.append(" " + GRAY + tr('Try "continue" to keep reading.', '试试 "继续" —— 回车即可接着读') + RESET)
        sh.print_block(lines)
        sh.print_line("")

    # ---- 对话框 ----------------------------------------------------
    def _dialog(self, title: str, items: list[str]) -> int | None:
        sh = self.shell
        if not items:
            return None
        max_rows = min(12, sh.rows - FOOTER_H - 3, len(items))
        sel, offset = 0, 0
        sh.hint_l = tr("↑/↓ select · enter confirm · esc cancel", "↑/↓ 选择 · 回车 确认 · Esc 取消")
        sh.draw_footer()
        n_lines = max_rows + 2
        top = sh.hist_bottom - n_lines + 1
        n_lines = draw_dialog(sh, top, title, items, sel, offset, max_rows)
        try:
            while True:
                ev = self.keys.get(0.06)
                if ev is None:
                    continue
                kind, val = ev
                if kind == "key":
                    if val == "_script_end":
                        return 0
                    if val == "up":
                        sel = (sel - 1) % len(items)
                    elif val == "down":
                        sel = (sel + 1) % len(items)
                    elif val == "enter":
                        return sel
                    elif val in ("esc", "ctrl-c", "ctrl-d"):
                        return None
                    else:
                        continue
                elif kind == "char":
                    if val == " ":
                        sel = (sel + 1) % len(items)
                    elif val == "b":
                        return None
                    else:
                        continue
                else:
                    continue
                if sel < offset:
                    offset = sel
                if sel >= offset + max_rows:
                    offset = sel - max_rows + 1
                n_lines = draw_dialog(sh, top, title, items, sel, offset, max_rows)
        finally:
            clear_dialog(sh, top, n_lines)
            sh.hint_l = tr("? for shortcuts", "？查看快捷键")
            sh.draw_footer()
            sh.park_cursor()

    def _library_rows(self) -> list[tuple[Path | None, str]]:
        """书库列表：[进度%] + 最近在读置顶；首项固定为导入入口。"""
        books = list_books()
        if not books:
            ensure_sample_book()
            books = list_books()
        p = load_progress()
        cur_path = self.book.path if self.book else None

        def sort_key(b: Path):
            e = p["books"].get(str(b))
            return (-(e.get("ts", 0) if e else 0), b.name.lower())

        rows: list[tuple[Path | None, str]] = [(None, tr("Import a file…", "导入文件…"))]
        for b in sorted(books, key=sort_key):
            e = p["books"].get(str(b))
            if e and e.get("total"):
                tag = f"{min(100, round(e.get('chars', 0) * 100 / e['total'])):3d}%"
            else:
                tag = tr("new ", "未读")
            mark = "●" if cur_path and b == cur_path else " "
            rows.append((b, f"{mark}[{tag}] {b.name}"))
        return rows

    def _choose_book(self) -> Book | None:
        sh = self.shell
        rows = self._library_rows()
        if len(rows) == 1:
            sh.print_line(f"{RED}" + tr(f"no books found in {books_dir()}", f"书库为空：{books_dir()}") + f"{RESET}")
            return None
        sel, offset = 1 if len(rows) > 1 else 0, 0
        max_rows = min(12, sh.rows - FOOTER_H - 3, len(rows))
        armed_del: tuple[int, str] | None = None  # 删除确认（同项再按一次 d）
        sh.hint_l = tr("↑/↓ select · enter open · d delete · esc cancel", "↑/↓ 选择 · 回车 打开 · d 删除 · Esc 取消")
        sh.draw_footer()
        top = sh.hist_bottom - (max_rows + 2) + 1
        n_lines = draw_dialog(sh, top, tr("Choose a file", "选择文件"),
                              [r[1] for r in rows], sel, offset, max_rows)
        try:
            while True:
                ev = self.keys.get(0.06)
                if ev is None:
                    continue
                kind, val = ev
                act = ""
                if kind == "key":
                    if val == "_script_end":
                        return None
                    if val == "up":
                        sel = (sel - 1) % len(rows)
                    elif val == "down":
                        sel = (sel + 1) % len(rows)
                    elif val == "enter":
                        act = "open"
                    elif val in ("esc", "ctrl-c", "ctrl-d"):
                        return None
                elif kind == "char":
                    if val == " ":
                        sel = (sel + 1) % len(rows)
                    elif val == "d":
                        act = "del"
                if not act:
                    if sel < offset:
                        offset = sel
                    if sel >= offset + max_rows:
                        offset = sel - max_rows + 1
                    n_lines = draw_dialog(sh, top, tr("Choose a file", "选择文件"),
                                          [r[1] for r in rows], sel, offset, max_rows)
                    continue
                if act == "del":
                    b = rows[sel][0]
                    if b is None or (self.book and b == self.book.path):
                        sh.flash_hint(tr("cannot delete the open book", "不能删除正在阅读的书"))
                        continue
                    if armed_del and armed_del[0] == sel:
                        try:
                            b.unlink()
                        except OSError as e:
                            sh.flash_hint(tr(f"delete failed: {e}", f"删除失败：{e}"))
                            armed_del = None
                            continue
                        p = load_progress()
                        p["books"].pop(str(b), None)
                        save_progress(p)
                        rows.pop(sel)
                        sh.flash_hint(tr(f"deleted: {b.name}", f"已删除：{b.name}"))
                        armed_del = None
                        if sel >= len(rows):
                            sel = len(rows) - 1
                        if len(rows) == 1:  # 只剩导入入口
                            return None
                    else:
                        armed_del = (sel, b.name)
                        sh.flash_hint(tr(f"press d again to delete {b.name}", f"再按一次 d 删除 {b.name}"), 3.0)
                    n_lines = draw_dialog(sh, top, tr("Choose a file", "选择文件"),
                                          [r[1] for r in rows], sel, offset, max_rows)
                    continue
                # open
                if sel == 0:
                    b = self._import_book()
                    if b is not None:
                        return b
                    rows = self._library_rows()  # 导入取消 → 刷新列表
                    if len(rows) == 1:
                        return None
                    n_lines = draw_dialog(sh, top, tr("Choose a file", "选择文件"),
                                          [r[1] for r in rows], sel, offset, max_rows)
                    continue
                sh.print_line(f"{ORANGE}{SYM_PROMPT}{RESET} {GRAY}{rows[sel][0].name}{RESET}")
                return self._open_book(rows[sel][0])
        finally:
            clear_dialog(sh, top, n_lines)
            sh.hint_l = tr("? for shortcuts", "？查看快捷键")
            sh.draw_footer()
            sh.park_cursor()

    def _import_book(self) -> Book | None:
        """弹出系统文件选择框 → 复制进 books/ → 打开。返回 Book 或 None。"""
        sh = self.shell
        sh.flash_hint(tr("opening file dialog…", "正在打开文件选择框…"), 0)
        src = pick_file_dialog()
        if src is None:
            sh.flash_hint(tr("import canceled — put files in books/ or try /import", "已取消导入——把文件放进 books/ 或用 /import"))
            return None
        try:
            target = unique_copy(src, books_dir())
        except OSError as e:
            sh.print_line(f"{RED}" + tr(f"import failed: {e}", f"导入失败：{e}") + f"{RESET}")
            return None
        b = self._open_book(target)
        if b is None:
            sh.print_line(f"{RED}" + tr(f"unsupported file: {target.name}", f"暂不支持的格式：{target.name}") + f"{RESET}")
            return None
        sh.print_line(f"{ORANGE}{SYM_PROMPT}{RESET} " + GRAY + tr(f"imported: {target.name}", f"已导入：{target.name}") + RESET)
        self.book = b
        self._resume_progress()
        sh.cwd = f"~/books/{self.book.title} (main)"
        self._update_status()
        return b

    def _toc_dialog(self):
        """分卷两级目录：卷行回车折叠/展开，章行回车跳转。
        默认只展开当前章所在卷。"""
        sh = self.shell
        outline = self.book.outline

        def build_rows(expanded: set) -> list[tuple]:
            rows = []  # ("vol", 卷下标, 标题) | ("ch", 卷下标, 标题, 章下标)
            for vi, vol in enumerate(outline):
                rows.append(("vol", vi, vol["title"] or "正文"))
                if vi in expanded:
                    for ci, t in vol["items"]:
                        rows.append(("ch", vi, t, ci))
            return rows

        expanded = {vi for vi, vol in enumerate(outline)
                    if any(ci == self.ch for ci, _ in vol["items"])}
        rows = build_rows(expanded)
        if len(outline) == 1:  # 无卷 → 直接展开
            expanded = {0}
            rows = build_rows(expanded)
        max_rows = min(12, sh.rows - FOOTER_H - 3, max(1, len(rows)))
        sel = next((i for i, r in enumerate(rows)
                    if r[0] == "ch" and r[3] == self.ch), 0)
        offset = max(0, min(sel - max_rows // 2, max(0, len(rows) - max_rows)))
        sh.hint_l = tr("↑/↓ select · enter open / expand · esc cancel", "↑/↓ 选择 · 回车 打开/展开 · Esc 取消")
        sh.draw_footer()
        top = sh.hist_bottom - (max_rows + 2) + 1

        def draw():
            w = min(sh.cols, 46)
            head = "╭─ " + tr("Chapters", "章节目录") + " "
            head += "─" * max(1, w - dwidth(head) - 1) + "╮"
            body = [GRAY + head + RESET]
            for j in range(max_rows):
                r = rows[offset + j] if offset + j < len(rows) else None
                if r is None:
                    body.append(GRAY + "│" + " " * (w - 2) + "│" + RESET)
                    continue
                kind, vi, title = r[0], r[1], r[2]
                idx = offset + j
                if kind == "vol":
                    arrow = "▾" if vi in expanded else "▸"
                    content = f"{BOLD}{arrow} {title}{RESET}"
                    plain = f"{arrow} {title}"
                else:
                    mark = "●" if r[3] == self.ch else " "
                    sel_mark = f"{ORANGE}{SYM_PROMPT}{RESET}" if idx == sel else " "
                    content = f" {sel_mark} {mark} {title}"
                    plain = f"   {mark} {title}"
                pad = w - 4 - dwidth(plain)
                body.append(GRAY + "│ " + RESET + content
                            + " " * max(0, pad) + GRAY + " │" + RESET)
            body.append(GRAY + "╰" + "─" * (w - 2) + "╯" + RESET)
            sys.stdout.write("".join(CUP(top + j, 1) + EL + ln
                                     for j, ln in enumerate(body)))
            sys.stdout.flush()
            return len(body)

        n_lines = draw()
        try:
            while True:
                ev = self.keys.get(0.06)
                if ev is None:
                    continue
                kind, val = ev
                act = ""
                if kind == "key":
                    if val == "_script_end":
                        return
                    if val == "up":
                        sel = (sel - 1) % len(rows)
                    elif val == "down":
                        sel = (sel + 1) % len(rows)
                    elif val == "enter":
                        act = "go"
                    elif val in ("esc", "ctrl-c", "ctrl-d"):
                        return
                elif kind == "char":
                    if val == " ":
                        sel = (sel + 1) % len(rows)
                if not act:
                    if sel < offset:
                        offset = sel
                    if sel >= offset + max_rows:
                        offset = sel - max_rows + 1
                    n_lines = draw()
                    continue
                row = rows[sel]
                if row[0] == "vol":  # 折叠/展开
                    vi = row[1]
                    if vi in expanded:
                        expanded.discard(vi)
                    else:
                        expanded.add(vi)
                    keep = sel
                    rows = build_rows(expanded)
                    sel = min(keep, len(rows) - 1)
                    n_lines = draw()
                    continue
                ch_idx = row[3]
                self.ch, self.para, self.off = ch_idx, 0, 0
                self.chars_read = self.book.offset_of(ch_idx, 0)
                self._save()
                sh.print_line(f"{ORANGE}{SYM_PROMPT}{RESET} {GRAY}{row[2]}{RESET}")
                self._update_status()
                self._stream_chapter()
                return
        finally:
            clear_dialog(sh, top, n_lines)
            sh.hint_l = tr("? for shortcuts", "？查看快捷键")
            sh.draw_footer()
            sh.park_cursor()

    def _help_dialog(self):
        zh = [
            "回车 / 空格      继续阅读（下一轮回复）",
            "空格（流式中）    暂停 / 继续",
            "n / p            下一章 / 上一章",
            "t                章节目录（分卷折叠）",
            "m                在当前位置加书签",
            "b / Ctrl+B       老板模式（一键掩护）",
            "Esc              中断流式 / 清空输入框",
            "↑ / ↓（输入中）   历史命令召回 / 菜单选择",
            "Tab              补全菜单选中项",
            "@书名            引用并切换到那本书",
            "!命令            假装执行一条 shell 命令",
            "i / /import      弹文件选择框导入书本",
            "/bm              书签列表（回车跳转 · d 删除）",
            "/bm add 名称     加命名书签",
            "/goto 50%        按全书百分比跳转",
            "/ch 12           跳到第 12 章",
            "/find 关键词     全文搜索并跳转",
            "/speed 1-10      流式打字速度",
            "/resp 2000       每轮回复字数（默认 1200）",
            "/auto [N]        自动阅读（N=轮间隔秒）",
            "/tools           假工具行开关",
            "/book            书库（进度% · d 删除 · 导入）",
            "/model 名称      状态栏模型名",
            "/lang zh|en      界面语言",
            "/clear           清屏",
            "/exit            退出（或 Ctrl+C 两次）",
        ]
        en = [
            "enter / space   continue reading",
            "space (stream)  pause / resume",
            "n / p           next / previous chapter",
            "t               chapter list (volumes)",
            "m               add bookmark here",
            "b / ctrl-b      boss mode",
            "esc             interrupt streaming / clear input",
            "↑/↓ (typing)    history recall / menu select",
            "tab             complete menu selection",
            "@书名           switch book by @mention",
            "!cmd            fake bash run",
            "i / /import     import a book via file dialog",
            "/bm             bookmarks (enter jump · d delete)",
            "/bm add NAME    add bookmark with a name",
            "/goto 50%       jump by percent of book",
            "/ch 12          jump to chapter #12",
            "/find keyword   full-text search & jump",
            "/speed 1-10     streaming speed",
            "/resp 2000      reply length in chars per turn",
            "/auto [N]       auto-read (N = interval seconds)",
            "/tools          toggle fake tool calls",
            "/book           library (progress% · d delete · import)",
            "/model NAME     model name in status bar",
            "/lang zh|en     UI language",
            "/clear          clear screen",
            "/exit           quit (or ctrl-c twice)",
        ]
        self._dialog(tr("Shortcuts", "快捷键"), zh if _LANG == "zh" else en)

    # ---- 输入框循环 --------------------------------------------------
    # ---- 输入框补全菜单（/ 命令、@ 书名） ---------------------------
    def _menu_recompute(self):
        sh = self.shell
        t = sh.input_text
        kind, items = None, []
        if t.startswith("/") and " " not in t:
            items = slash_menu_items(t)
            kind = "slash" if items else None
        else:
            at = t.rfind("@")
            if at >= 0 and " " not in t[at:] and "@" not in t[at + 1:]:
                tok = t[at + 1:].lower()
                items = [(p.name, f"{p.stat().st_size // 1024} KB", False)
                         for p in list_books() if tok in p.name.lower()]
                kind = "at" if items else None
        if kind is None:
            self._menu_clear()
            return
        max_rows = max(1, min(8, sh.rows - FOOTER_H - 2, len(items)))
        if self._menu and self._menu["items"] == items:
            m = self._menu
            m["sel"] = min(m["sel"], len(items) - 1)
            self._menu_draw()
            return
        self._menu_clear()
        self._menu = {"kind": kind, "items": items, "sel": 0, "offset": 0,
                      "rows": max_rows, "top": sh.hist_bottom - max_rows + 1,
                      "drawn": 0}
        self._menu_draw()

    def _menu_draw(self):
        sh = self.shell
        m = self._menu
        if not m:
            return
        out = []
        n = 0
        for i, (label, desc, _arg) in enumerate(m["items"][m["offset"]: m["offset"] + m["rows"]]):
            idx = m["offset"] + i
            desc = L(desc)
            if idx == m["sel"]:
                row = (f" {ORANGE}{SYM_PROMPT}{RESET} {BOLD}{label}{RESET}"
                       + " " * max(2, 16 - dwidth(label)) + f"{GRAY}{desc}{RESET}")
            else:
                row = f"   {label}" + " " * max(2, 16 - dwidth(label)) + f"{GRAY_DARK}{desc}{RESET}"
            out.append(CUP(m["top"] + i, 1) + EL + vtruncate(row, sh.cols - 1))
            n += 1
        # 多退少补：清掉上次多画的行
        for j in range(n, m.get("drawn", 0)):
            out.append(CUP(m["top"] + j, 1) + EL)
        m["drawn"] = n
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def _menu_clear(self):
        m = self._menu
        if not m:
            return
        out = "".join(CUP(m["top"] + j, 1) + EL for j in range(m.get("drawn", 0) or m["rows"]))
        sys.stdout.write(out)
        sys.stdout.flush()
        self._menu = None

    def _menu_complete(self, exec_after: bool):
        """Tab=补全；Enter=补全并执行（需参数的命令只补全）。"""
        sh = self.shell
        m = self._menu
        if not m:
            return
        label, _desc, needs_arg = m["items"][m["sel"]]
        if m["kind"] == "slash":
            sh.input_text = label + (" " if needs_arg else "")
            sh.input_pos = len(sh.input_text)
            self._menu_clear()
            sh.draw_footer()
            sh.park_cursor()
            if exec_after:
                if needs_arg:
                    sh.flash_hint(tr(f"usage: {label} …", f"用法：{label} …"))
                else:
                    self._submit_input()
        else:  # @ 书名
            t = sh.input_text
            at = t.rfind("@")
            sh.input_text = t[:at] + f"@{label} "
            sh.input_pos = len(sh.input_text)
            self._menu_clear()
            sh.draw_footer()
            sh.park_cursor()

    def _fake_bash(self, c: str):
        sh = self.shell
        c = c or "echo ok"
        sh.print_line("")
        sh.print_line(f"{ORANGE}{BOLD}{SYM_TOOL}{RESET} {BOLD}Bash{RESET}({c})")
        sh.print_line(f"  {GRAY}{SYM_RESULT} {fake_bash_result(c)}{RESET}")

    def _placeholder_tick(self):
        """空输入时轮换 dim 占位提示（仿 Claude Code）。"""
        sh = self.shell
        now = time.time()
        if now - self._ph_t < 4.0:
            if not sh.placeholder and not sh.input_text and not self._menu:
                sh.placeholder = PLACEHOLDERS[_LANG][self._ph_i % len(PLACEHOLDERS[_LANG])]
                sh.draw_footer()
                sh.park_cursor()
            return
        self._ph_t = now
        if sh.input_text or self._menu:
            return
        self._ph_i = (self._ph_i + 1) % len(PLACEHOLDERS[_LANG])
        sh.placeholder = PLACEHOLDERS[_LANG][self._ph_i % len(PLACEHOLDERS[_LANG])]
        sh.draw_footer()
        sh.park_cursor()

    def _submit_input(self) -> bool:
        """提交输入框内容（回车）。返回 False 表示退出。"""
        sh = self.shell
        cmd = sh.input_text.strip()
        if cmd and (not self._cmd_history or self._cmd_history[-1] != cmd):
            self._cmd_history.append(cmd)
        self._hist_idx = None
        echo = cmd if cmd else random.choice(USER_PROMPTS)
        sh.print_line(f"{GRAY}>{RESET} {echo}")
        sh.input_text, sh.input_pos = "", 0
        sh.placeholder = ""
        sh.draw_footer()
        sh.park_cursor()
        return self._dispatch(cmd)

    # ---- 输入框主循环 ------------------------------------------------
    def _input_loop(self):
        sh = self.shell
        sh.placeholder = PLACEHOLDERS[_LANG][self._ph_i % len(PLACEHOLDERS[_LANG])]
        sh.draw_footer()
        while self.running:
            ev = self.keys.get(0.1)
            resized = sh.check_resize()
            sh.check_flash_restore()
            if resized and self._menu:
                self._menu_clear()
                self._menu_recompute()
            if ev is None:
                self._placeholder_tick()
                continue
            kind, val = ev
            if kind == "key":
                if val == "_script_end":
                    break
                if val == "ctrl-c":
                    if self._confirm_exit():
                        break
                elif val == "ctrl-d":
                    break
                elif val == "ctrl-b":
                    self._menu_clear()
                    self._boss_mode()
                elif val == "enter":
                    if self._menu and self._menu["kind"] == "slash":
                        self._menu_complete(exec_after=True)
                    else:
                        self._menu_clear()
                        if not self._submit_input():
                            break
                elif val == "tab":
                    if self._menu:
                        self._menu_complete(exec_after=False)
                elif val in ("up", "down"):
                    if self._menu:  # 菜单导航优先
                        m = self._menu
                        m["sel"] = (m["sel"] + (1 if val == "down" else -1)) % len(m["items"])
                        if m["sel"] < m["offset"]:
                            m["offset"] = m["sel"]
                        if m["sel"] >= m["offset"] + m["rows"]:
                            m["offset"] = m["sel"] - m["rows"] + 1
                        self._menu_draw()
                    elif val == "up" and self._cmd_history:
                        if self._hist_idx is None:
                            self._hist_idx = len(self._cmd_history) - 1
                        else:
                            self._hist_idx = max(0, self._hist_idx - 1)
                        sh.input_text = self._cmd_history[self._hist_idx]
                        sh.input_pos = len(sh.input_text)
                        self._menu_recompute()
                        sh.draw_footer()
                        sh.park_cursor()
                    elif val == "down" and self._hist_idx is not None:
                        self._hist_idx += 1
                        if self._hist_idx < len(self._cmd_history):
                            sh.input_text = self._cmd_history[self._hist_idx]
                        else:
                            sh.input_text = ""
                            self._hist_idx = None
                        sh.input_pos = len(sh.input_text)
                        self._menu_recompute()
                        sh.draw_footer()
                        sh.park_cursor()
                elif val == "esc":
                    if self._menu:
                        self._menu_clear()
                    else:
                        sh.input_text, sh.input_pos = "", 0
                        sh.draw_footer()
                        sh.park_cursor()
                elif val == "backspace" and sh.input_pos > 0:
                    sh.input_text = sh.input_text[: sh.input_pos - 1] + sh.input_text[sh.input_pos:]
                    sh.input_pos -= 1
                    self._menu_recompute()
                    sh.draw_footer()
                    sh.park_cursor()
                elif val == "left" and sh.input_pos > 0:
                    sh.input_pos -= 1
                    sh.draw_footer()
                    sh.park_cursor()
                elif val == "right" and sh.input_pos < len(sh.input_text):
                    sh.input_pos += 1
                    sh.draw_footer()
                    sh.park_cursor()
                elif val == "home":
                    sh.input_pos = 0
                    sh.draw_footer()
                    sh.park_cursor()
                elif val == "end":
                    sh.input_pos = len(sh.input_text)
                    sh.draw_footer()
                    sh.park_cursor()
            else:  # 字符输入
                if not sh.input_text:  # 空输入时的单键快捷方式
                    if val == "?":  # 底栏提示 "? for shortcuts"——按 ? 直接弹帮助
                        self._help_dialog()
                        continue
                    if val == " ":
                        self._stream_chapter()
                        continue
                    if val in "nN":
                        self._goto_chapter(self.ch + 1)
                        continue
                    if val in "pP":
                        self._goto_chapter(self.ch - 1)
                        continue
                    if val == "t" or val == "T":
                        self._toc_dialog()
                        continue
                    if val in "mM":
                        self._bookmark_add("")
                        continue
                    if val in "iI":
                        b = self._import_book()
                        if b is not None:
                            self._stream_chapter()
                        continue
                    if val == "b":
                        self._boss_mode()
                        continue
                sh.input_text = sh.input_text[: sh.input_pos] + val + sh.input_text[sh.input_pos:]
                sh.input_pos += 1
                self._menu_recompute()
                sh.draw_footer()
                sh.park_cursor()

    def _confirm_exit(self) -> bool:
        sh = self.shell
        sh.flash_hint(tr("^C  press ctrl-c again to exit", "^C  再按一次 Ctrl+C 退出"), 2.0)
        t0 = time.time()
        while time.time() - t0 < 2.0:
            ev = self.keys.get(0.1)
            if ev == ("key", "ctrl-c"):
                return True
        sh.check_flash_restore()
        return False

    # ---- 命令分发 ----------------------------------------------------
    def _dispatch(self, cmd: str) -> bool:
        """返回 False 表示退出。"""
        sh = self.shell
        low = cmd.lower().strip()
        if cmd.startswith("!"):  # !bash 假执行
            self._fake_bash(cmd[1:].strip())
            return True
        m = re.search(r"@([^\s@]+)", cmd)
        if m:  # @书名 直接切换
            p = find_book_by_token(m.group(1))
            if p is not None:
                b = self._open_book(p)
                if b:
                    self.book = b
                    self._resume_progress()
                    sh.cwd = f"~/books/{self.book.title} (main)"
                    self._update_status()
                    self._stream_chapter()
                return True
        if low in ("exit", "quit", "/exit", "/quit", "q", "/q"):
            return False
        if low in ("/clear", "clear"):
            sys.stdout.write(CLEAR_SCROLLBACK + CLEAR_SCREEN)
            self._welcome()
            return True
        if low in ("/book", "book"):
            b = self._choose_book()
            if b:
                self.book = b
                self._resume_progress()
                sh.cwd = f"~/books/{self.book.title} (main)"
                self._update_status()
                self._stream_chapter()
            return True
        if low in ("/toc", "toc"):
            self._toc_dialog()
            return True
        if low in ("/next", "next"):
            self._goto_chapter(self.ch + 1)
            return True
        if low in ("/prev", "prev"):
            self._goto_chapter(self.ch - 1)
            return True
        if low.startswith("/speed"):
            m = re.search(r"(\d+)", cmd)
            if m:
                self.speed = max(1, min(10, int(m.group(1))))
                sh.flash_hint(tr(f"speed = {self.speed}", f"速度 = {self.speed}"))
            else:
                sh.flash_hint(tr("usage: /speed 1-10", "用法：/speed 1-10"))
            return True
        m = re.match(r"/resp\s+(\d+)", low)
        if m:
            self.resp = max(50, min(20000, int(m.group(1))))
            self._save()
            sh.flash_hint(tr(f"reply length = {self.resp} chars per turn", f"每轮回复字数 = {self.resp}"))
            return True
        if low in ("/resp", "resp"):
            sh.flash_hint(tr(f"reply length = {self.resp} — usage: /resp 2000", f"每轮字数 = {self.resp}——用法：/resp 2000"))
            return True
        m = re.match(r"/auto\s+(\d+(?:\.\d+)?)", low)
        if m:
            self.auto_delay = max(0.3, min(10.0, float(m.group(1))))
            self.auto = True
            self._save()
            sh.flash_hint(tr(f"auto-read on · interval {self.auto_delay}s", f"自动阅读 开 · 间隔 {self.auto_delay} 秒"))
            return True
        if low in ("/auto", "auto"):
            self.auto = not self.auto
            self._save()
            sh.flash_hint(tr(f"auto-read {'on' if self.auto else 'off'}", f"自动阅读 {'开' if self.auto else '关'}")
                          + (tr(f" · interval {self.auto_delay}s", f" · 间隔 {self.auto_delay} 秒") if self.auto else ""))
            return True
        if low in ("/tools", "tools"):
            self.show_tools = not self.show_tools
            sh.flash_hint(f"fake tool calls {'on' if self.show_tools else 'off'}")
            return True
        m = re.match(r"/model\s+(\S+)", cmd)
        if m:
            sh.model = m.group(1)
            sh.update_tokens(sh.tokens)
            sh.flash_hint(tr(f"model = {sh.model}", f"模型 = {sh.model}"))
            return True
        m = re.match(r"/lang\s+(zh|en|chinese|english|中文|英文)", low)
        if m:
            lang = set_lang(m.group(1))
            self._save()
            sh.flash_hint(tr("language: English", f"界面语言：{'中文' if lang == 'zh' else 'English'}"))
            return True
        if low in ("/lang", "lang"):
            sh.flash_hint(tr("usage: /lang zh | en", "用法：/lang zh | en"))
            return True
        if low in ("/boss", "boss"):
            self._boss_mode()
            return True
        if low in ("/import", "import"):
            b = self._import_book()
            if b is not None:
                self._stream_chapter()
            return True
        m = re.match(r"/bm\s+add(?:\s+(.+))?$", cmd, re.I)
        if m:
            self._bookmark_add(m.group(1) or "")
            return True
        if low in ("/bm", "/bookmark", "bm"):
            self._bookmark_dialog()
            return True
        m = re.match(r"/(?:goto|jump)\s+(\d+(?:\.\d+)?)\s*%?", low)
        if m:
            pct = max(0.0, min(100.0, float(m.group(1))))
            target = int(self.book.total_chars * pct / 100)
            ch, para = self.book.locate(target)
            sh.print_line(f"{ORANGE}{SYM_PROMPT}{RESET} " + GRAY + tr(f"jump → {pct:.0f}%", f"跳转 → {pct:.0f}%") + f" {self.book.chapters[ch][0]}{RESET}")
            self._jump_to(ch, para, 0)
            return True
        m = re.match(r"/ch\s+(\d+)", low)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(self.book.chapters):
                self._goto_chapter(idx)
            else:
                sh.flash_hint(tr(f"1-{len(self.book.chapters)} only", f"仅支持 1-{len(self.book.chapters)}"))
            return True
        m = re.match(r"/(?:find|search)\s+(.+)", cmd, re.I)
        if m:
            self._find_dialog(m.group(1))
            return True
        if low in ("/help", "help", "?"):
            self._help_dialog()
            return True
        # 任意其他输入 = 继续阅读
        self._stream_chapter()
        return True

    def _goto_chapter(self, idx: int):
        if not 0 <= idx < len(self.book.chapters):
            self.shell.flash_hint(tr("no more chapters", "没有更多章节了"))
            return
        self.ch, self.para, self.off = idx, 0, 0
        self.chars_read = self.book.offset_of(idx, 0)
        self._save()
        self._stream_chapter()

    # ---- 流式输出（核心伪装） -----------------------------------------
    def _stream_chapter(self):
        """流式输出当前章；流式期间按 n/p 翻章、auto 连播都在本循环内完成（无递归）。"""
        result = self._stream_once()
        while self.running:
            pend = self._pending
            self._pending = None
            if pend in ("next", "prev"):
                target = self.ch + (1 if pend == "next" else -1)
                if 0 <= target < len(self.book.chapters):
                    self.ch, self.para, self.off = target, 0, 0
                    self.chars_read = self.book.offset_of(target, 0)
                    self._save()
                    result = self._stream_once()
                    continue
                self.shell.flash_hint(tr("no more chapters", "没有更多章节了"))
                break
            if self.auto and self.running and (
                    result == "chunk"
                    or (result == "eof" and self.ch + 1 < len(self.book.chapters))):
                if self._auto_wait() == "go":
                    if result == "eof":  # 章结束 → 进下一章；chunk → 续读本章剩余
                        self.ch += 1
                        self.para = self.off = 0
                        self.chars_read = self.book.offset_of(self.ch, 0)
                        self._save()
                    result = self._stream_once()
                    continue
                result = None  # 停止 auto，但仍让本轮 pending（若有）生效
                continue
            break

    def _auto_wait(self) -> str:
        sh = self.shell
        sh.set_spinner(True, random.choice(SPINNER_PHRASES) + "…")
        sh.hint_l = ""
        sh.draw_footer()
        t0 = time.time()
        while time.time() - t0 < self.auto_delay:
            self._spin_tick()
            ev = self.keys.get(0.08)
            if ev:
                if self._stream_key(ev, in_text=False) in ("stop", "boss"):
                    sh.set_spinner(False)
                    sh.hint_l = tr("? for shortcuts", "？查看快捷键")
                    sh.draw_footer()
                    return "stop"
        sh.set_spinner(False)
        sh.hint_l = tr("? for shortcuts", "？查看快捷键")
        return "go"

    def _stream_once(self) -> str:
        sh = self.shell
        # 当前章已读完 → 续读自动进入下一章（否则会永远停在原地）
        if paras_exhausted(self.book, self.ch, self.para):
            if self.ch + 1 < len(self.book.chapters):
                self.ch += 1
                self.para = self.off = 0
                self.chars_read = self.book.offset_of(self.ch, 0)
                self._save()
            else:
                sh.flash_hint(tr("book finished — /goto 0% to restart", "全书读完——/goto 0% 重读"))
                return "book_end"
        title, paras = self.book.chapters[self.ch]
        fresh = self.para == 0 and self.off == 0
        if fresh:
            sh.print_line("")
            sh.print_line(f"{GRAY}>{RESET} {random.choice(USER_PROMPTS)}")
        # spinner 思考阶段
        sh.set_spinner(True, random.choice(SPINNER_PHRASES) + "…")
        sh.hint_l = ""
        sh.draw_footer()
        sys.stdout.write(HIDE_CURSOR)
        sys.stdout.flush()
        deadline = time.time() + random.uniform(0.7, 1.4)
        while time.time() < deadline:
            self._spin_tick()
            ev = self.keys.get(0.08)
            if ev and self._stream_key(ev, in_text=False) == "stop":
                self._end_stream_ui()
                return "interrupted"
        # 假工具调用
        if self.show_tools and fresh and paras:
            fake_path = f"novel/ch_{self.ch:04d}.txt"
            sh.print_line(f"{ORANGE}{BOLD}{SYM_TOOL}{RESET} {BOLD}Read{RESET}({fake_path})")
            sh.print_line(f"  {GRAY}{SYM_RESULT} Read {len(paras) + 8} lines "
                          f"({random.uniform(0.2, 0.8):.1f}s){RESET}")
        sh.set_spinner(False)
        sh.print_line("")
        if fresh:
            for ln in wrap_text(title, sh.wrap_width()):
                sh.print_line(BOLD + ln + RESET)
            sh.print_line("")
        result = self._stream_paragraphs(paras)
        self._end_stream_ui()
        if result == "eof":
            self._save()
            if self.ch + 1 < len(self.book.chapters):
                sh.flash_hint(tr("chapter finished — enter to continue", "本章读完——回车继续下一章"), 3.0)
            else:
                sh.flash_hint(tr("book finished — /goto 0% to restart", "全书读完——/goto 0% 重读"), 3.0)
        return result

    def _end_stream_ui(self):
        sh = self.shell
        sh.set_spinner(False)
        sh.hint_l = tr("? for shortcuts", "？查看快捷键")
        self._update_status()
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()

    def _stream_paragraphs(self, paras: list[str]) -> str:
        """逐字打出当前章剩余段落，受每轮回复字数额度限制。
        返回 'eof'（本章读完）| 'chunk'（额度用完，段落边界停）| 'interrupted'。"""
        sh = self.shell
        cps = 30 * self.speed
        tick = 0.03
        burst = max(1, round(cps * tick))
        budget = self.resp  # 本轮还可输出的字符数
        paused = False
        while self.para < len(paras):
            para = paras[self.para]
            lines = wrap_text(para[self.off:], sh.wrap_width())
            for line in lines:
                pos = 0
                while pos < len(line):
                    if not paused:
                        frag = line[pos: pos + burst]
                        pos += len(frag)
                        sh.type_fragment(frag)
                        self.chars_read += len(frag)
                        self.off += len(frag)
                        budget -= len(frag)
                        self._maybe_save()
                    ev = self.keys.get(tick if not paused else 0.05)
                    if ev:
                        act = self._stream_key(ev, in_text=True)
                        if act == "pause":
                            paused = not paused
                        elif act == "boss":
                            sh.end_line()
                            self._boss_mode()
                            return "interrupted"
                        elif act == "stop":
                            sh.end_line()
                            return "interrupted"
                    self._spin_tick()
                sh.end_line()
                self._maybe_save()
            self.para += 1
            self.off = 0
            sh.print_line("")
            self._save()
            if budget <= 0:  # 额度用完：在段边界优雅收尾，回车继续下一轮
                return "chunk"
        return "eof"

    def _stream_key(self, ev, in_text: bool) -> str:
        """流式期间的按键：space=暂停/继续，esc/ctrl-c=中断，b/ctrl-b=老板，
        n/p=直接翻章，enter=跳读完当前回复（仅正文阶段；思考阶段回车不中断）。"""
        kind, val = ev
        if kind == "key":
            if val == "_script_end":
                self.running = False
                return "stop"
            if val == "ctrl-b":
                return "boss"
            if val in ("ctrl-c", "esc"):
                return "stop"
            if val == "enter" and in_text:
                return "stop"
        else:
            if val == " ":
                return "pause"
            if val == "b":
                return "boss"
            if val in "nN":
                self._pending = "next"
                return "stop"
            if val in "pP":
                self._pending = "prev"
                return "stop"
        return ""

    def _spin_tick(self):
        sh = self.shell
        now = time.time()
        if now - self._spin_t > 0.4:
            self._spin_t = now
            self._spin_bright = not self._spin_bright
            sh.spinner_bright = self._spin_bright
            if random.random() < 0.2:
                sh.spinner_text = random.choice(SPINNER_PHRASES) + "…"
            sh.draw_footer()

    # ---- 老板模式 ----------------------------------------------------
    def _boss_mode(self):
        sh = self.shell
        self._save()
        sys.stdout.write(CLEAR_SCROLLBACK + CLEAR_SCREEN)
        sh.check_resize()
        sh.cwd = random.choice(["~/work/api-gateway", "~/work/admin-console",
                                "~/work/payment-svc", "~/dev/infra-tooling"])
        sh.set_spinner(True, random.choice(SPINNER_PHRASES) + "…")
        sh.hint_l = ""
        sh.input_text, sh.input_pos = "", 0
        sh.draw_footer()
        set_title("Claude Code")
        next_log = 0.0
        while True:
            ev = self.keys.get(0.05)
            now = time.time()
            self._spin_tick()
            if now >= next_log:
                next_log = now + random.uniform(0.35, 0.95)
                for ln in fake_tool_group():
                    sh.print_line(ln)
            if ev:
                kind, val = ev
                quit_key = (kind == "key" and val in ("b", "ctrl-b", "esc", "_script_end")) or \
                           (kind == "char" and val == "b")
                if quit_key:
                    break
        # 退出时再清一次，抹掉小说残留
        sys.stdout.write(CLEAR_SCROLLBACK + CLEAR_SCREEN)
        sh.cwd = f"~/books/{self.book.title} (main)"
        sh.set_spinner(False)
        sh.hint_l = tr("? for shortcuts", "？查看快捷键")
        self._update_status()


# ══════════════════════════════════════════════════════════════════
# 11. --demo：无 TTY 演示
# ══════════════════════════════════════════════════════════════════

class _NoKeys:
    def get(self, timeout):
        time.sleep(0.01)
        return None

    def start(self):
        pass

    def stop(self):
        pass


def run_demo(args):
    ensure_sample_book()
    app = App(args)
    sh = app.shell
    sh.rows, sh.cols = 30, 92
    app.keys = _NoKeys()
    app.book = Book(books_dir() / "示例小说.txt")
    app.speed = 10

    sh.setup()
    set_title("Claude Code")
    app._welcome()
    time.sleep(0.3)

    sh.print_line(f"{GRAY}>{RESET} 继续")
    time.sleep(0.2)
    sh.set_spinner(True, "")
    for phr in random.sample(SPINNER_PHRASES, 3):
        sh.spinner_text = phr + "…"
        sh.spinner_bright = not sh.spinner_bright
        sh.draw_footer()
        time.sleep(0.35)

    sh.print_line(f"{ORANGE}{BOLD}{SYM_TOOL}{RESET} {BOLD}Read{RESET}(novel/ch_0001.txt)")
    sh.print_line(f"  {GRAY}{SYM_RESULT} Read 24 lines (0.3s){RESET}")
    sh.set_spinner(False)
    sh.print_line("")

    title, paras = app.book.chapters[0]
    sh.print_line(BOLD + title + RESET)
    sh.print_line("")
    app.chars_read = 0
    for para in paras:
        for ln in wrap_text(para, sh.wrap_width()):
            sh.type_fragment(ln)
            time.sleep(0.03)
            sh.end_line()
        sh.print_line("")
        time.sleep(0.08)
        app.chars_read += len(para)
        sh.update_tokens(app.chars_read // 2)
    sh.hint_l = tr("? for shortcuts", "？查看快捷键")
    sh.update_tokens(app.chars_read // 2)
    time.sleep(0.3)
    sh.teardown()


# ══════════════════════════════════════════════════════════════════
# 12. --selftest：内置自检
# ══════════════════════════════════════════════════════════════════

def _t(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    return bool(cond)


def run_selftest():
    import tempfile

    print("claude_novel selftest")
    ok = True

    ok &= _t("wcwidth CJK=2", wcwidth("中") == 2 and wcwidth("Ａ") == 2)
    ok &= _t("wcwidth ascii=1", wcwidth("a") == 1 and wcwidth(" ") == 1)
    ok &= _t("wcwidth ambiguous=1", wcwidth(SYM_PROMPT) == 1 and wcwidth(SYM_SPIN) == 1)
    ok &= _t("dwidth plain", dwidth("ab中") == 4)
    ok &= _t("dwidth ignores ansi", dwidth(f"{ORANGE}ab中{RESET}") == 4)
    ok &= _t("vtruncate", dwidth(vtruncate("一二三四五", 6)) == 6)
    ok &= _t("vtruncate styled", strip_ansi(vtruncate(f"{BOLD}一二三四五{RESET}", 4)) == "一二")

    lines = wrap_text("青山绵绵不绝共有八个山峰相连", 10)
    ok &= _t("wrap respects width", all(dwidth(l) <= 10 for l in lines) and len(lines) >= 2)
    lines = wrap_text("hello world foo", 7)
    ok &= _t("wrap keeps words", lines[0].strip() == "hello")

    with tempfile.TemporaryDirectory() as td:
        gbk_file = Path(td) / "gbk.txt"
        gbk_file.write_bytes("第一章 测试\n\n读者你好，这是一段GBK编码的文字。\n".encode("gbk"))
        ok &= _t("decode gbk", "GBK编码" in decode_novel_bytes(gbk_file.read_bytes()))
        ok &= _t("decode utf-8-sig", "读者你好" in decode_novel_bytes(
            "第一章 测试\n\n读者你好。".encode("utf-8-sig")))

    text = ("序言\n\n写在前面的几句话。\n\n"
            "第一章 启程\n\n少年背起行囊。\n\n山路很长。\n\n"
            "第二章 山中\n\n夜里起了风。\n\n"
            "第12章 数字标题\n\n故事仍在继续。\n\n"
            "Chapter 5 English\n\nThe road goes on.\n\n"
            "番外 小店\n\n茶棚里的对话。\n\n")
    chs = find_chapters(text)
    titles = [t for t, _ in chs]
    ok &= _t("chapters count", len(chs) == 6)
    ok &= _t("chapter titles", titles[1] == "第一章 启程" and titles[3] == "第12章 数字标题"
             and titles[4] == "Chapter 5 English" and titles[5] == "番外 小店")
    ok &= _t("chapter paras", chs[1][1] == ["少年背起行囊。", "山路很长。"])
    ok &= _t("no chapters fallback", len(find_chapters("没有任何章节标记的一整段文字。第二行。")) == 1)

    # 进度（隔离目录）
    global _APP_DIR
    orig_dir = _APP_DIR
    with tempfile.TemporaryDirectory() as td:
        _APP_DIR = Path(td)
        save_progress({"last": "x", "books": {"x": {"ch": 3}}, "settings": {}})
        ok &= _t("progress roundtrip", load_progress()["books"]["x"]["ch"] == 3)
        ok &= _t("progress atomic no tmp", not (Path(td) / ".claude-reader.tmp").exists())
    _APP_DIR = orig_dir

    ok &= _t("spinner phrases >= 100", len(SPINNER_PHRASES) >= 100)
    ok &= _t("spinner unique", len(set(SPINNER_PHRASES)) == len(SPINNER_PHRASES))
    ok &= _t("phrases ascii", all(p.isascii() for p in SPINNER_PHRASES))

    try:
        assert_ascii_safe()
        ok &= _t("boss log no CJK", True)
    except AssertionError:
        ok &= _t("boss log no CJK", False)

    sh = Shell()
    sh.rows, sh.cols = 30, 80
    ok &= _t("footer input row width", dwidth(sh._input_row()) == 80)
    sh.set_spinner(True, "Reticulating splines…")
    ok &= _t("spinner row has esc hint", "(esc to interrupt)" in strip_ansi(sh._spinner_row()))
    ok &= _t("spinner row width ok", dwidth(sh._spinner_row()) <= 80)
    sh.input_text, sh.input_pos = "中文abc", 2
    ok &= _t("input row with CJK", dwidth(sh._input_row()) == 80)
    ok &= _t("fmt_tokens", fmt_tokens(3200) == "3.2k" and fmt_tokens(500) == "500")

    # ---- EPUB ----
    with tempfile.TemporaryDirectory() as td:
        epub = Path(td) / "sample.epub"
        make_sample_epub(epub)
        eb = Book(epub)
        titles = [t for t, _ in eb.chapters]
        ok &= _t("epub loads", len(eb.chapters) == 2 and titles[0] == "第一章 候鸟"
                 and titles[1] == "第二章 海边的小屋")
        ok &= _t("epub text", any("林知夏" in p for _, ps in eb.chapters for p in ps))
        # 无"第X章"标题的 EPUB → 按 spine 文档分章，标题取 <title>
        import zipfile as _zip
        nohead = Path(td) / "nohead.epub"
        with _zip.ZipFile(nohead, "w") as zf:
            zf.writestr("META-INF/container.xml",
                        '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="b.opf"/></rootfiles></container>')
            zf.writestr("b.opf",
                        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf">'
                        '<manifest><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
                        '<item id="b" href="b.xhtml" media-type="application/xhtml+xml"/></manifest>'
                        '<spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
            zf.writestr("a.xhtml", "<html><head><title>Alpha</title></head><body>"
                        + "<p>" + "word " * 30 + "</p></body></html>")
            zf.writestr("b.xhtml", "<html><head><title>Beta</title></head><body>"
                        + "<p>" + "text " * 30 + "</p></body></html>")
        nb = Book(nohead)
        ok &= _t("epub spine fallback", [t for t, _ in nb.chapters] == ["Alpha", "Beta"])

    # ---- 输入框假功能 ----
    ok &= _t("slash menu filter /b",
             [c[0] for c in slash_menu_items("/b")] == ["/bm", "/bm add", "/book", "/boss"])
    ok &= _t("slash menu all on /", len(slash_menu_items("/")) == len(FAKE_COMMANDS))
    ok &= _t("slash menu none", slash_menu_items("/zzz") == [])
    ok &= _t("slash menu arg flags", all(c[2] for c in FAKE_COMMANDS if c[0] in
             ("/goto", "/ch", "/find", "/speed", "/model")))
    ok &= _t("fake bash ascii", all(fake_bash_result(c).isascii()
             for c in ("npm test", "ls -la", "git status", "echo hi",
                       "make build", "kubectl get pods", "")))
    ok &= _t("fake bash git status", fake_bash_result("git status") ==
             "nothing to commit, working tree clean")
    ok &= _t("/import in commands", any(c[0] == "/import" for c in FAKE_COMMANDS))
    ok &= _t("/resp in commands", any(c[0] == "/resp" for c in FAKE_COMMANDS))
    ok &= _t("/lang in commands", any(c[0] == "/lang" for c in FAKE_COMMANDS))
    ok &= _t("tr/L language switch",
             tr("en-text", "中文字") == "中文字" and L(("en", "中")) == "中"
             and set_lang("en") == "en" and tr("en-text", "中文字") == "en-text"
             and L(("en", "中")) == "en")
    set_lang("zh")
    ensure_sample_book()
    _sb = Book(books_dir() / "示例小说.txt")
    ok &= _t("paras_exhausted", paras_exhausted(_sb, 0, len(_sb.chapters[0][1]))
             and not paras_exhausted(_sb, 0, 0)
             and paras_exhausted(_sb, 0, 10 ** 6))

    # ---- 分卷两级目录 ----
    vol_text = ("第一卷 风起\n\n卷首引言。\n\n第一章 起\n\n甲。\n\n第二章 承\n\n乙。\n\n"
                "第二卷 云涌\n\n第三章 转\n\n丙。\n\n第四章 合\n\n丁。\n\n")
    vch = find_chapters(vol_text)
    outline = build_outline(vch)
    ok &= _t("outline two volumes", len(outline) == 2
             and outline[0]["title"] == "第一卷 风起"
             and [t for _, t in outline[0]["items"]] == ["卷首", "第一章 起", "第二章 承"]
             and [t for _, t in outline[1]["items"]] == ["第三章 转", "第四章 合"])
    ok &= _t("outline no volume single group",
             len(build_outline(find_chapters("第一章 a\n\nx。\n\n第二章 b\n\ny。\n\n"))) == 1
             and build_outline(find_chapters("第一章 a\n\nx。\n\n"))[0]["title"] is None)
    ok &= _t("volume regex", bool(VOLUME_RE.match("第一卷 风起")) and bool(VOLUME_RE.match("上部"))
             and not VOLUME_RE.match("第一章 起") and not VOLUME_RE.match("卷帘门"))
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "a.txt"
        src.write_text("hello", encoding="utf-8")
        lib = Path(td) / "lib"
        t1 = unique_copy(src, lib)
        t2 = unique_copy(src, lib)                       # 同内容 → 幂等
        ok &= _t("unique_copy idempotent", t1 == t2 and t1.read_text() == "hello")
        src.write_text("different", encoding="utf-8")
        t3 = unique_copy(src, lib)                       # 同名不同内容 → (1)
        ok &= _t("unique_copy suffix", t3.name == "a (1).txt" and t3.read_text() == "different")
    ok &= _t("placeholders", len(PLACEHOLDERS["zh"]) >= 3 and len(PLACEHOLDERS["en"]) >= 3)
    sh2 = Shell()
    sh2.rows, sh2.cols = 30, 80
    sh2.placeholder = 'Try "continue"'
    ok &= _t("placeholder in input row", dwidth(sh2._input_row()) == 80)

    # ---- Book.locate / offset_of 定位往返 ----
    ensure_sample_book()
    b = Book(books_dir() / "示例小说.txt")
    ok &= _t("sample book loads", len(b.chapters) >= 3 and b.total_chars > 200)
    ok &= _t("locate roundtrip",
             all(b.locate(b.offset_of(c, p)) == (c, p)
                 for c in range(len(b.chapters)) for p in range(len(b.chapters[c][1]))))
    ok &= _t("locate ends in last chapter", b.locate(b.total_chars - 1)[0] == len(b.chapters) - 1)
    ok &= _t("locate clamps", b.locate(10 ** 9)[0] == len(b.chapters) - 1 and b.locate(-5) == (0, 0))

    # ---- 进度 v2 迁移 ----
    with tempfile.TemporaryDirectory() as td:
        _APP_DIR = Path(td)
        progress_path().write_text(
            json.dumps({"book": "old.txt", "ch": 2, "para": 1, "off": 5,
                        "chars": 100, "speed": 7}), encoding="utf-8")
        migrated = load_progress()
        ok &= _t("progress v1→v2", migrated["books"]["old.txt"]["ch"] == 2
                 and migrated["books"]["old.txt"]["off"] == 5
                 and migrated["settings"]["speed"] == 7 and migrated["last"] == "old.txt")
        save_progress({"last": "x", "books": {"x": {"ch": 0, "bookmarks": [{"name": "bm"}]}},
                       "settings": {}})
        ok &= _t("progress v2 roundtrip",
                 load_progress()["books"]["x"]["bookmarks"][0]["name"] == "bm")
    _APP_DIR = None

    print("\nresult:", "ALL PASS" if ok else "FAILED")
    return 0 if ok else 1


# ══════════════════════════════════════════════════════════════════
# 13. main
# ══════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Claude Code look-alike novel reader")
    ap.add_argument("book", nargs="?", help="path to a .txt novel")
    ap.add_argument("--speed", type=int, default=5, help="streaming speed 1-10 (default 5)")
    ap.add_argument("--auto", action="store_true", help="auto-continue chapters")
    ap.add_argument("--no-tools", action="store_true", help="hide fake tool calls")
    ap.add_argument("--list", action="store_true", help="list books")
    ap.add_argument("--demo", action="store_true", help="scripted non-interactive demo")
    ap.add_argument("--selftest", action="store_true", help="run built-in tests")
    ap.add_argument("--script", help="headless test keys: enter,space,esc,wait:2,type:hi")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(run_selftest())
    if args.demo:
        run_demo(args)
        sys.exit(0)
    if args.list:
        ensure_sample_book()
        for b in list_books():
            print(b.name)
        sys.exit(0)

    if not (sys.stdin.isatty() and sys.stdout.isatty()) and not args.script:
        print("stdin/stdout is not a terminal — try --demo, or run inside a terminal.",
              file=sys.stderr)
        sys.exit(2)

    App(args).run()


if __name__ == "__main__":
    main()

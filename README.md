# cc-novelreader

**外观 1:1 复刻 Claude Code 终端的摸鱼小说阅读器。**

小说正文伪装成 AI 的 assistant 流式回复，配 ✳ spinner、⏺ 工具调用、❯ 输入框、
底部 token 状态栏——旁人扫一眼你的屏幕，看到的就是一个正在跑 Claude Code 的终端。

```
 ✻ Welcome to Claude Code

   /book          Open book library
   /import        Browse & import a book file
   /toc           Show chapter list
   ...

 ~/books/示例小说 (main)
╭──────────────────────────────────────────────────╮
│ ❯ Try "continue"                                 │
╰──────────────────────────────────────────────────╯
 ? for shortcuts            opus-4.8 · 12.3k tokens · 37%
```

- **单文件、零第三方依赖** —— 一个 Python 文件（3.9+，纯标准库）
- **Windows**：PyInstaller 打包单 exe（约 8MB），目标机器**无需任何环境**
- **macOS / Linux**：`python3 claude_novel.py` 直接跑
- **TXT**（UTF-8 / GBK / GB18030 / Big5 自动识别）+ **EPUB**（纯标准库解析）
- 阅读器基本功：多书各自记忆进度 · 书签 · 百分比/章节跳转 · 全文搜索
- 老板键一键全屏英文假工作日志（清 scrollback，零中文残留）

## 快速开始

### Windows（exe）

从 [Releases](../../releases) 下载 `cc-novelreader-windows` 里的 exe
（或自己打包，见下文），放到任意目录：

```
cc-novelreader.exe
```

首次运行自动生成示例小说。把你的小说（.txt / .epub）放进 exe 旁的 `books\`
文件夹即可，四种导入方式：

1. **程序内 `i` / `/import` / 书库对话框 "Import a file…"** —— 弹系统文件
   选择框，选中即复制进书库并开读（同名不同内容自动加 `(1)` 后缀）
2. 丢进 `books\` → `/book` 或 `@书名` 选读
3. 命令行直开：`cc-novelreader.exe "D:\novels\某书.txt"`
4. 拖拽文件到 exe 图标上

### macOS / Linux

```bash
python3 claude_novel.py [某本书.txt] [--speed 1-10] [--auto]
```

## 界面与快捷键

打开后就是你熟悉的那个界面。在输入框里**直接回车（或空格）= 继续读下一章**，
正文像 AI 回复一样逐字流式打出；也可以随手输入任何话再回车——都会继续读。

| 按键 | 作用 |
|---|---|
| `Enter` / `空格` | 继续阅读（下一章 / 下一段回复） |
| `空格`（流式中） | 暂停 / 继续 |
| `Esc` | 中断流式输出（同 Claude Code）/ 清空输入框 |
| `↑` / `↓`（输入框） | 召回历史命令 |
| `↑` / `↓`（对话框/菜单） | 选择 |
| `n` / `p` | 下一章 / 上一章（空闲或流式中均可） |
| `t` | 章节目录（Claude 风格选择框） |
| `m` | 在当前位置加书签（自动以章节名命名） |
| **`b` / `Ctrl+B`** | **老板键**：清屏（含 scrollback）+ 全屏英文假工作日志，再按恢复 |
| `i` | 导入书本（系统文件选择框） |
| `Ctrl+C` ×2 / `Ctrl+D` / `/exit` | 退出（自动保存进度） |

**输入框的仿真交互**（和真 Claude Code 一样）：

- 输入 `/` 弹出**命令补全菜单**，实时过滤；`↑/↓` 选择、`Tab` 补全、
  `Enter` 补全并执行、`Esc` 关闭
- 输入 `@` 弹出**书库引用列表**（带文件大小），提交 `@书名` 直接切书
- 输入 `!命令` 回车 → `⏺ Bash(...)` **假执行**一条 shell 命令并给出仿真结果
- 空输入时轮换 dim 占位提示（`Try "continue"` …）

斜杠命令：

- **书签**：`/bm`（enter 跳转 · `d` 删除）· `/bm add 名字`
- **快速跳转**：`/goto 50%`（按全书百分比）· `/ch 12`（跳第 N 章）·
  `/find 关键词`（全文搜索，结果框回车跳转）
- **阅读**：`/book` 换书（选完即读）· `/toc` 目录 · `/next` `/prev` 翻章 ·
  `/speed 1-10` 流速 · **`/resp 2000` 每轮回复字数**（默认 1200，段边界停，
  读完一章回车自动进下一章）· **`/auto [N]` 自动阅读**（挂机连读，N 为轮间隔秒）·
  `/tools` 假工具行开关 ·
  `/model 名字` 改状态栏模型名 · `/import` 导入 · `/clear` 清屏 · `/help` 帮助

## 伪装细节

- 每章一轮"对话"：dim 用户消息（`> 继续`）→ ✳ spinner（180+ 条原味趣味短语，
  "Reticulating splines…" 那套）→ `⏺ Read(novel/ch_0001.txt)` 假工具调用 →
  正文逐字流式 → 回到输入框
- 底部状态栏实时显示 `opus-4.8 · 12.3k tokens · 37%`（token 数与百分比随
  真实阅读量增长）
- 终端窗口标题设置为 Claude Code 同款
- 老板键 `b`：`ESC[3J` 清空 scrollback 后切入假工作状态（Read/Edit/Bash/Grep
  日志持续滚动，全英文无中文），退出时再清一次
- 进度按**每本书**保存在 exe 旁的 `.claude-reader.json`，换书再换回自动接续

## 打包 exe

Windows 本机装好 Python 后双击 `build_exe.bat`，产物在 `dist\`。

或打 tag 触发 GitHub Actions 自动构建三平台二进制（见
[build.yml](.github/workflows/build.yml)）：

```bash
git tag v0.1.0 && git push --tags
```

## 测试

```bash
python claude_novel.py --selftest     # 47 项内置自检（宽度/折行/编码/分章/EPUB/进度迁移/菜单…）
python claude_novel.py --demo         # 无终端环境演示整个界面流程
python claude_novel.py --script "enter,esc,ctrlc,ctrlc"   # 无头交互测试
python tools/real_key_test.py         # Windows 真实控制台按键注入测试
```

## 目录结构

```
claude_novel.py        全部逻辑（单文件，~1600 行）
build_exe.bat          Windows 一键打包
books/                 书库（示例小说 ×2 + GBK 编码样张）
tools/                 开发与测试脚本
.github/workflows/     CI：打 tag 自动构建全平台二进制
```

## 免责声明

仅供学习终端渲染技术（ANSI 转义码 / 控制台 API / CJK 排版）与个人阅读使用。
工具本身不含任何规避公司监控的策略，请自行评估使用场景。

## License

[MIT](LICENSE)

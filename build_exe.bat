@echo off
rem 一键打包单文件 exe（本机需已装 Python 3.9+；目标机器无需任何环境）
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install pyinstaller >nul
pyinstaller --onefile --clean --name claude_novel --distpath dist --workpath build --specpath build claude_novel.py
echo.
echo 打包完成: dist\claude_novel.exe
echo 把它和 books\ 文件夹放在一起即可使用（首次运行会自动生成示例小说）
pause

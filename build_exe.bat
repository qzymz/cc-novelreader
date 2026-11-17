@echo off
rem 一键编译 ccreader.exe（本机需已装 Python 3.9+；目标机器无需任何环境）
cd /d "%~dp0"
if not exist .venv (python -m venv .venv)
call .venv\Scriptsctivate.bat
python -m pip show pyinstaller >nul 2>&1 || python -m pip install pyinstaller >nul
pyinstaller --onefile --clean --name ccreader --distpath dist --workpath build --specpath build claude_novel.py
echo.
echo 打包完成: dist\ccreader.exe
echo   便携包/安装包请运行 pack_portable.bat
pause

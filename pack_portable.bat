@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist .venv (
  echo [1/4] 创建虚拟环境 ...
  python -m venv .venv || (echo [错误] 未找到 python，请先安装 Python 3.9+ & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip show pyinstaller >nul 2>&1 || python -m pip install pyinstaller >nul

echo [2/4] 编译 ccreader.exe ...
pyinstaller --onefile --clean --name ccreader --distpath dist --workpath build --specpath build claude_novel.py
if errorlevel 1 (echo [错误] 编译失败 & pause & exit /b 1)

echo [3/4] 组装便携包 ...
for /f %%v in ('python -c "import claude_novel; print(claude_novel.__version__)"') do set VER=%%v
set REL=release\ccreader-portable
if exist %REL% rmdir /s /q %REL%
mkdir %REL%
copy /y dist\ccreader.exe %REL%\ >nul
xcopy /e /i /y books %REL%\books\ >nul
copy /y install.bat %REL%\ >nul
copy /y uninstall.bat %REL%\ >nul
copy /y 使用说明.txt %REL%\ >nul

echo [4/4] 压缩 zip ...
set ZIP=release\ccreader-portable-v%VER%.zip
powershell -NoProfile -Command "Compress-Archive -Force -Path 'release/ccreader-portable/*' -DestinationPath '%ZIP%'"
if errorlevel 1 (echo [错误] 压缩失败 & pause & exit /b 1)

echo.
echo 打包完成: %ZIP%
echo   - 便携即用: 解压 zip，双击 ccreader.exe
echo   - 命令行版: 解压后双击 install.bat，之后任意终端输入 ccreader
pause

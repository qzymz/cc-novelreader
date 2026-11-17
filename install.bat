@echo off
setlocal EnableExtensions
set "SRC=%~dp0"
set "DEST=%LOCALAPPDATA%\ccreader"

if not exist "%SRC%ccreader.exe" if exist "%SRC%dist\ccreader.exe" set "SRC=%SRC%dist\"
if not exist "%SRC%ccreader.exe" (
  echo [错误] 找不到 ccreader.exe
  echo        请在解压后的便携版目录或项目 dist 目录里运行本脚本
  pause
  exit /b 1
)

echo [1/3] 安装到 %DEST% ...
mkdir "%DEST%" 2>nul
copy /y "%SRC%ccreader.exe" "%DEST%\ccreader.exe" >nul || (echo [错误] 复制失败 & pause & exit /b 1)
if not exist "%DEST%\books" (
  mkdir "%DEST%\books"
  if exist "%SRC%books" copy "%SRC%books\*" "%DEST%\books\" >nul 2>&1
)

echo [2/3] 写入用户 PATH（不需要管理员权限）...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d='%DEST%'; $p=[Environment]::GetEnvironmentVariable('Path','User'); if(($p -split ';') -notcontains $d){[Environment]::SetEnvironmentVariable('Path', ($p.TrimEnd(';')+';'+$d),'User'); 'ADDED'}else{'ALREADY'}"

echo [3/3] 安装完成！
echo.
echo    用法：新开一个终端窗口（cmd / PowerShell / Windows Terminal），
echo          输入      ccreader      回车即可打开
echo    书库：%DEST%\books
echo          把 .txt / .epub 丢进去，或在程序里按 i 弹框导入
echo    卸载：双击 uninstall.bat
echo.
echo    注意：已打开的终端窗口不会立刻生效，新开的窗口才有 ccreader 命令。
pause

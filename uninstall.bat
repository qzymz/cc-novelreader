@echo off
setlocal EnableExtensions
set "DEST=%LOCALAPPDATA%\ccreader"

echo [1/2] 从用户 PATH 移除 %DEST% ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d='%DEST%'; $p=[Environment]::GetEnvironmentVariable('Path','User'); $np=(($p -split ';') | Where-Object {$_ -and $_ -ne $d}) -join ';'; [Environment]::SetEnvironmentVariable('Path',$np,'User'); 'OK'"

echo [2/2] 删除程序文件 ...
echo.
echo    即将删除：%DEST%
echo    注意：你的小说和阅读进度都在该目录里（books\ 与 .claude-reader.json）！
choice /c YN /m "确认删除（Y=删除全部含小说进度  N=只移除命令保留文件）"
if errorlevel 2 (
  echo 已保留 %DEST%，仅移除了 ccreader 命令。
  pause
  exit /b 0
)
rmdir /s /q "%DEST%"
echo 已完全卸载：%DEST%
pause

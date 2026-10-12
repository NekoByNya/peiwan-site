@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
echo 正在自检（用独立临时数据，不会动你的 data 目录）...
echo.
python tests\run_tests.py
echo.
pause

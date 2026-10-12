@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo ============================================
echo   陪玩预约站 · 手机测试模式
echo ============================================
echo.
echo   [!] 这个模式下，同一个 WiFi 里的任何设备
echo       都能打开你的网站和后台。测完请关掉，
echo       平时用 start.bat（只有本机能访问）。
echo.
set LANIP=
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
  if not defined LANIP set LANIP=%%a
)
if defined LANIP set LANIP=%LANIP: =%
echo   手机浏览器打开（同一 WiFi）：
echo       http://%LANIP%:8123
echo   后台：
echo       http://%LANIP%:8123/admin
echo.
echo ============================================
echo.
set HOST=0.0.0.0
python app.py
if errorlevel 1 (
  echo.
  echo [出错] 没能启动，把上面的红色报错发我。
  pause
)

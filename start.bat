@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo ============================================
echo   陪玩预约站 启动中...
echo ============================================
echo.
echo   主页：  http://127.0.0.1:8123
echo   后台：  http://127.0.0.1:8123/admin
echo.
echo   关闭这个黑窗口 = 关掉网站
echo.
echo   想让手机连上测试？关掉这个，改成双击 start-lan.bat
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [出错] 没有找到 python。
  echo 请先安装 Python 3：https://www.python.org/downloads/
  echo 安装时务必勾选 "Add python.exe to PATH"
  echo.
  pause
  exit /b 1
)

rem 3 秒后自动帮你打开浏览器（等网站先启动起来）
start "" cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8123"

python app.py
if errorlevel 1 (
  echo.
  echo [出错] 网站没能启动，请把上面的红色报错发给我。
  echo 常见原因：8123 端口被占用，可以改用 set PORT=9000 ^&^& python app.py
  pause
)

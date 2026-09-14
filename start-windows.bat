@echo off
REM ============================================================================
REM  ROBOCON 电控实训教室 · Windows 启动器
REM
REM  双击这个文件即可启动课程（不需要记命令）。
REM
REM  它做什么：
REM    1. 找到 Python（优先用 WSL 里的 Linux 环境，那是课程推荐的环境）
REM    2. 启动本地课程服务
REM    3. 自动打开浏览器
REM
REM  作者：Connor He 和 Astra
REM ============================================================================

setlocal
REM 切到 UTF-8 代码页：否则批处理里的中文在中文版 Windows 上会乱码
chcp 65001 >nul
cd /d "%~dp0"

title ROBOCON 电控实训教室

echo.
echo   ROBOCON 电控实训教室
echo   从一台电机，到一整台比赛机器人的电控系统
echo   ------------------------------------------------
echo.

REM ---- 优先检查 WSL（课程推荐环境：Linux 工具链齐全） ----
where wsl >nul 2>&1
if %errorlevel%==0 (
  wsl -e true >nul 2>&1
  if %errorlevel%==0 (
    echo   [1/3] 检测到 WSL（Linux 子系统），使用它启动课程
    echo   [2/3] 正在启动课程服务……
    echo.
    echo   浏览器稍后会自动打开 http://127.0.0.1:8770/
    echo   停止课程请在 WSL 窗口按 Ctrl+C
    echo.
    start "" http://127.0.0.1:8770/
    wsl -e bash -lc "cd \"$(wslpath '%~dp0')\" && ./start.sh"
    goto :end
  )
  echo   ! 检测到 WSL 但无法启动，改用 Windows 自带的 Python
)

REM ---- 回退：用 Windows 上的 Python 直接跑（也能用，只是没有 Linux 工具链） ----
where python >nul 2>&1
if not %errorlevel%==0 (
  where py >nul 2>&1
  if not %errorlevel%==0 (
    echo   找不到 Python。
    echo.
    echo   请二选一：
    echo     A. 安装 WSL（推荐）：以管理员身份打开 PowerShell，执行
    echo          wsl --install -d Ubuntu
    echo     B. 安装 Windows 版 Python：到 https://www.python.org/downloads/
    echo        下载安装，安装时务必勾选 "Add python.exe to PATH"
    echo.
    pause
    exit /b 1
  )
  set PYTHON=py
) else (
  set PYTHON=python
)

echo   [1/3] 使用 Windows Python 启动（没有 WSL 时的回退方式）
"%PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not %errorlevel%==0 (
  echo   Python 版本过低，课程需要 3.10 以上。
  "%PYTHON%" --version
  echo   请到 https://www.python.org/downloads/ 安装新版本。
  echo.
  pause
  exit /b 1
)

if not exist "course\curriculum.json" (
  echo   [2/3] 正在生成课程内容……
  "%PYTHON%" scripts\build_content.py
  if not %errorlevel%==0 (
    echo   生成课程内容失败。
    pause
    exit /b 1
  )
) else (
  echo   [2/3] 课程内容已就绪
)

echo   [3/3] 正在启动课程服务……
echo.
echo   浏览器稍后会自动打开 http://127.0.0.1:8770/
echo   停止课程请在本窗口按 Ctrl+C
echo.

start "" http://127.0.0.1:8770/
"%PYTHON%" -m server.app --port 8770

:end
echo.
echo   课程已停止。学习记录保存在 .state 文件夹里，下次打开还在。
pause

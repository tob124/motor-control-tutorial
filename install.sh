#!/usr/bin/env bash
# ============================================================================
#  ROBOCON 电控实训教室 · 一键安装脚本
#
#  用法（三选一，推荐第一种）：
#     bash install.sh                        # 交互式，全程有提示
#     bash install.sh --dir ~/robocon-course # 指定安装位置
#     bash install.sh --dry-run              # 只做检查，不改动任何东西
#
#  这个脚本只做四件事：检查环境、把课程放到你的电脑上、生成课程内容、
#  启动本地网页服务。它不会安装系统服务、不会改你的系统配置、
#  不会联网上传任何东西（除了从 GitHub 下载课程本身）。
#
#  作者：Connor He 和 Astra
# ============================================================================

set -euo pipefail

# ---------------------------------------------------------------- 基本设置

REPO_URL="${ROBOCON_REPO_URL:-https://github.com/tob124/motor-control-tutorial}"
REPO_TARBALL="${ROBOCON_REPO_TARBALL:-}"
DEFAULT_DIR="$HOME/robocon-control-course"
DEFAULT_PORT=8770

INSTALL_DIR=""
PORT="$DEFAULT_PORT"
DRY_RUN=0
IN_PLACE=0
NO_DOCKER=0
NO_OPEN=0
WITH_DOCKER=0
SELF_DIR=
FORCE_START=0

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------- 输出样式

# 只在真正的终端里上色，重定向到文件时输出干净文本
if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; CYAN=$'\033[36m'
else
  BOLD=""; DIM=""; RESET=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; CYAN=""
fi

STEP_N=0
step() {
  STEP_N=$((STEP_N + 1))
  printf '\n%s[%d]%s %s%s%s\n' "$BLUE$BOLD" "$STEP_N" "$RESET" "$BOLD" "$1" "$RESET"
}
ok()   { printf '    %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
info() { printf '    %s·%s %s\n' "$DIM" "$RESET" "$1"; }
warn() { printf '    %s!%s %s\n' "$YELLOW" "$RESET" "$1"; }
fail() { printf '    %s✗%s %s\n' "$RED" "$RESET" "$1"; }
dim()  { printf '%s%s%s\n' "$DIM" "$1" "$RESET"; }

die() {
  printf '\n%s安装没有完成。%s\n\n' "$RED$BOLD" "$RESET" >&2
  printf '  原因：%s\n\n' "$1" >&2
  printf '  你可以把下面这段完整复制给帮你的人，或直接贴到 issue 里：\n' >&2
  printf '%s    %s%s\n\n' "$DIM" "$2" "$RESET" >&2
  exit 1
}

banner() {
  printf '\n%s%s\n' "$CYAN$BOLD" '┌────────────────────────────────────────────────┐'
  printf '│   ROBOCON 电控实训教室 · 一键安装              │\n'
  printf '│   从一台电机，到一整台比赛机器人的电控系统     │\n'
  printf '%s\n' '└────────────────────────────────────────────────┘'
  printf '%s' "$RESET"
  printf '%s作者：Connor He 和 Astra%s\n' "$DIM" "$RESET"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '%s（预演模式：只做检查，不会改动你的电脑）%s\n' "$YELLOW" "$RESET"
  fi
}

# ---------------------------------------------------------------- 参数解析

usage() {
  cat <<'EOF'
ROBOCON 电控实训教室 · 一键安装

用法：
  bash install.sh [选项]

选项：
  --dir <路径>     安装到指定目录（默认 ~/robocon-control-course）
  --port <端口>    本地服务端口（默认 8770）
  --in-place       在当前目录安装（你已经下载好课程时用）
  --with-docker    顺带配置可选的容器实验（需要管理员密码，耗时较长）
  --no-docker      跳过容器实验相关提示
  --no-open        安装完成后不自动打开浏览器
  --dry-run        只检查环境，不做任何改动
  --start          安装完成后直接启动（脚本/CI 用；默认仅在交互时询问）
  --self-dir <路径>  内部使用：安装完成后删除这个临时目录
  -h, --help       显示这份说明

安装完成后：
  启动课程    cd <安装目录> && ./start.sh
  停止课程    在运行课程的窗口按 Ctrl+C
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) INSTALL_DIR="${2:-}"; shift 2 ;;
    --dir=*) INSTALL_DIR="${1#--dir=}"; shift ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --port=*) PORT="${1#--port=}"; shift ;;
    --in-place) IN_PLACE=1; shift ;;
    --with-docker) WITH_DOCKER=1; shift ;;
    --no-docker) NO_DOCKER=1; shift ;;
    --no-open) NO_OPEN=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --self-dir) SELF_DIR="${2:-}"; shift 2 ;;   # 由 deploy.sh 传入，用完自删
    --start) FORCE_START=1; shift ;;            # 强制安装完就启动（非交互环境用）
    -h|--help) usage; exit 0 ;;
    *) printf '未知选项：%s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
  die "端口号不合法：$PORT" "端口应为 1024–65535 之间的整数，例如 --port 8770"
fi

# ---------------------------------------------------------------- 环境检查

OS_NAME="未知"
OS_KIND="unknown"
detect_os() {
  case "$(uname -s)" in
    Linux)
      OS_KIND="linux"
      if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        OS_NAME="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-Linux}")"
      else
        OS_NAME="Linux"
      fi
      ;;
    Darwin)
      OS_KIND="macos"
      OS_NAME="macOS $(sw_vers -productVersion 2>/dev/null || printf '')"
      ;;
    MINGW*|MSYS*|CYGWIN*)
      OS_KIND="windows-shell"
      OS_NAME="Windows（Git Bash / MSYS）"
      ;;
    *) OS_NAME="$(uname -s)" ;;
  esac
}

PYTHON=""
find_python() {
  local candidate
  for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      # 必须能跑起来、且版本 >= 3.10
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        PYTHON="$(command -v "$candidate")"
        return 0
      fi
    fi
  done
  return 1
}

install_hint() {
  case "$OS_KIND" in
    linux)
      if [ -r /etc/os-release ] && grep -qi 'ubuntu\|debian' /etc/os-release; then
        printf '  Ubuntu / Debian：  sudo apt update && sudo apt install -y python3 git\n'
      elif [ -r /etc/os-release ] && grep -qi 'fedora\|rhel\|centos' /etc/os-release; then
        printf '  Fedora / RHEL：    sudo dnf install -y python3 git\n'
      elif [ -r /etc/os-release ] && grep -qi 'arch' /etc/os-release; then
        printf '  Arch：             sudo pacman -S python git\n'
      else
        printf '  请用你的发行版的软件管理器安装 python3（3.10 以上）与 git\n'
      fi
      ;;
    macos)
      printf '  macOS（推荐 Homebrew）：  brew install python git\n'
      printf '  Homebrew 官网：          https://brew.sh/zh-cn/\n'
      ;;
    windows-shell)
      printf '  Windows：建议改用 WSL2（Ubuntu），或到 python.org 下载 Python 并勾选 Add to PATH\n'
      ;;
    *)
      printf '  请先安装 Python 3.10 以上与 git\n'
      ;;
  esac
}

check_environment() {
  step '检查你的电脑环境'

  detect_os
  info "操作系统：$OS_NAME"

  if ! find_python; then
    printf '\n' >&2
    install_hint >&2
    die "没有找到 Python 3.10 或更高版本（课程服务需要它）" \
        "uname -a: $(uname -a 2>/dev/null)"
  fi
  local version
  version="$("$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
  ok "Python $version（$PYTHON）"

  # 课程只用标准库，这里顺便确认关键模块真的在
  if ! "$PYTHON" -c 'import http.server, sqlite3, json, threading, secrets' 2>/dev/null; then
    die "你的 Python 缺少课程需要的标准库模块（http.server / sqlite3 等）" \
        "$PYTHON -c 'import http.server, sqlite3'"
  fi
  ok '标准库完整（http.server / sqlite3 / threading）'

  if command -v git >/dev/null 2>&1; then
    ok "git $(git --version 2>/dev/null | awk '{print $3}')"
    HAVE_GIT=1
  else
    warn '没有找到 git：改用压缩包方式下载（不影响使用）'
    HAVE_GIT=0
  fi

  if command -v curl >/dev/null 2>&1; then
    HAVE_CURL=1
  elif command -v wget >/dev/null 2>&1; then
    HAVE_CURL=2
  else
    HAVE_CURL=0
    if [ "$HAVE_GIT" -eq 0 ] && [ "$IN_PLACE" -eq 0 ]; then
      die "既没有 git 也没有 curl/wget，无法下载课程" "command -v git curl wget"
    fi
  fi

  # 端口占用检查
  if "$PYTHON" - "$PORT" <<'PY' 2>/dev/null
import socket, sys
sock = socket.socket()
try:
    sock.bind(('127.0.0.1', int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
  then
    ok "端口 $PORT 空闲"
  else
    warn "端口 $PORT 已被占用，安装后会换一个端口启动"
  fi

  if [ "$NO_DOCKER" -eq 0 ]; then
    if command -v docker >/dev/null 2>&1; then
      ok '检测到 Docker（可选的容器实验可用，需要时会指导你配置）'
    else
      info '没有 Docker：不影响使用。课程全部网页实验都不需要它'
    fi
  fi
}

# ---------------------------------------------------------------- 获取课程

fetch_with() {
  local url="$1" out="$2"
  if [ "$HAVE_CURL" -eq 1 ]; then
    curl -fsSL --retry 3 --connect-timeout 15 "$url" -o "$out"
  else
    wget -q --tries=3 --timeout=15 "$url" -O "$out"
  fi
}

obtain_project() {
  step '把课程放到你的电脑上'

  if [ "$IN_PLACE" -eq 1 ]; then
    INSTALL_DIR="$SCRIPT_DIR"
    if [ ! -f "$INSTALL_DIR/server/app.py" ]; then
      die "当前目录看起来不是课程目录（找不到 server/app.py）" "pwd: $INSTALL_DIR"
    fi
    ok "使用当前目录：$INSTALL_DIR"
    return 0
  fi

  [ -n "$INSTALL_DIR" ] || INSTALL_DIR="$DEFAULT_DIR"
  # 展开 ~ 与相对路径
  case "$INSTALL_DIR" in
    "~"|"~/"*) INSTALL_DIR="$HOME${INSTALL_DIR#\~}" ;;
  esac
  # 若脚本本身就在课程目录里，默认原地安装——但**用户显式给了 --dir 时以用户为准**。
  # （早期版本无条件覆盖 --dir，从临时目录运行安装脚本时会装到错误的地方。）
  local explicit_dir=0
  [ -n "$INSTALL_DIR" ] && explicit_dir=1
  if [ "$explicit_dir" -eq 0 ] && [ -f "$SCRIPT_DIR/server/app.py" ]; then
    INSTALL_DIR="$SCRIPT_DIR"
    ok "已经在课程目录内：$INSTALL_DIR"
    return 0
  fi

  if [ -f "$INSTALL_DIR/server/app.py" ]; then
    ok "发现已安装的课程：$INSTALL_DIR"
    if [ "$DRY_RUN" -eq 1 ]; then
      info '预演模式：跳过更新'
      return 0
    fi
    if [ -d "$INSTALL_DIR/.git" ] && [ "$HAVE_GIT" -eq 1 ]; then
      info '尝试更新到最新版本……'
      if git -C "$INSTALL_DIR" pull --ff-only --quiet 2>/dev/null; then
        ok '已更新到最新版本'
      else
        warn '更新失败（可能是本地有改动），继续使用现有版本'
      fi
    fi
    return 0
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    info "预演模式：将把课程下载到 $INSTALL_DIR"
    return 0
  fi

  mkdir -p "$(dirname "$INSTALL_DIR")"

  if [ "$HAVE_GIT" -eq 1 ]; then
    info "正在从 GitHub 下载（浅克隆，只取最新版本）……"
    if git clone --depth 1 --quiet "$REPO_URL" "$INSTALL_DIR" 2>/dev/null; then
      ok "下载完成：$INSTALL_DIR"
      return 0
    fi
    warn 'git 克隆失败，改用压缩包方式（常见原因：网络受限）'
  fi

  if [ "$HAVE_CURL" -eq 0 ]; then
    die '没有 git 也没有 curl/wget，无法下载课程' "REPO_URL=$REPO_URL"
  fi

  local tarball url
  tarball="$(mktemp -t robocon-course-XXXXXX.tar.gz)"
  if [ -n "$REPO_TARBALL" ]; then
    url="$REPO_TARBALL"
  else
    url="${REPO_URL%.git}/archive/refs/heads/main.tar.gz"
  fi
  info "正在下载压缩包……"
  if ! fetch_with "$url" "$tarball"; then
    rm -f "$tarball"
    die "下载失败：$url" "请检查网络，或手动下载后在本目录运行 bash install.sh --in-place"
  fi
  info '正在解压……'
  mkdir -p "$INSTALL_DIR"
  if ! tar -xzf "$tarball" -C "$INSTALL_DIR" --strip-components=1; then
    rm -f "$tarball"
    die '解压失败' "tarball: $url"
  fi
  rm -f "$tarball"
  ok "下载完成：$INSTALL_DIR"
}

# ---------------------------------------------------------------- 生成内容

build_content() {
  step '生成课程内容'
  if [ "$DRY_RUN" -eq 1 ]; then
    info '预演模式：跳过'
    return 0
  fi
  if [ -f "$INSTALL_DIR/course/curriculum.json" ] && [ -f "$INSTALL_DIR/course/software.json" ]; then
    ok '课程内容已存在'
    return 0
  fi
  if ! ( cd "$INSTALL_DIR" && "$PYTHON" scripts/build_content.py >/dev/null ); then
    die '生成课程内容失败' "cd $INSTALL_DIR && $PYTHON scripts/build_content.py"
  fi
  local weeks lessons
  weeks="$("$PYTHON" -c "import json;print(len(json.load(open('$INSTALL_DIR/course/curriculum.json'))['weeks']))" 2>/dev/null || printf '?')"
  lessons="$("$PYTHON" -c "import json;print(len(json.load(open('$INSTALL_DIR/course/curriculum.json'))['lessons']))" 2>/dev/null || printf '?')"
  ok "课程内容已生成：${weeks} 周 / ${lessons} 个单元"
}

# ---------------------------------------------------------------- 自检验证

self_check() {
  step '自检验证'
  if [ "$DRY_RUN" -eq 1 ]; then
    info '预演模式：跳过'
    return 0
  fi
  local output
  if ! output="$( cd "$INSTALL_DIR" && "$PYTHON" -m server.app --check 2>&1 )"; then
    printf '%s\n' "$output" >&2
    die '课程自检没有通过' "cd $INSTALL_DIR && $PYTHON -m server.app --check"
  fi
  printf '%s\n' "$output" | sed 's/^/    /'
  ok '课程服务可以正常启动'
}

# ---------------------------------------------------------------- 可选 Docker

maybe_docker() {
  [ "$NO_DOCKER" -eq 1 ] && return 0
  [ "$DRY_RUN" -eq 1 ] && return 0
  if command -v docker >/dev/null 2>&1; then
    return 0
  fi
  step '可选的容器实验'
  info '课程里有"真实 Linux 终端实验"（编译、CAN 工具），需要 Docker。'
  info '不装也完全不影响：44 个网页实验台、课程内容、作品导出都能用。'
  printf '    %s要现在配置吗？需要管理员密码，且要下载几百 MB。%s [y/N] ' "$DIM" "$RESET"
  local answer=""
  if [ -t 0 ]; then read -r answer || answer=""; fi
  case "$answer" in
    y|Y|yes|YES)
      info '正在启动容器环境配置（会先征求你的确认）……'
      if ! bash "$INSTALL_DIR/scripts/setup-docker.sh" --mirror aliyun; then
        warn '容器环境没有配置成功。这不影响课程使用，随时可以重试：'
        warn "  bash $INSTALL_DIR/scripts/setup-docker.sh --mirror aliyun"
      fi
      ;;
    *)
      info '跳过。想启用时随时运行：'
      info "  bash $INSTALL_DIR/scripts/setup-docker.sh --mirror aliyun"
      ;;
  esac
}

# ---------------------------------------------------------------- 完成提示

print_summary() {
  local url="http://127.0.0.1:$PORT/"
  printf '\n%s%s%s\n' "$GREEN$BOLD" '════════════════════════════════════════════════' "$RESET"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '%s环境检查通过，可以正式安装了。%s\n' "$GREEN$BOLD" "$RESET"
    printf '%s再运行一次去掉 --dry-run 即可：%s\n\n' "$DIM" "$RESET"
    printf '    bash install.sh%s\n\n' "${INSTALL_DIR:+ --dir $INSTALL_DIR}"
    printf '%s════════════════════════════════════════════════%s\n\n' "$GREEN$BOLD" "$RESET"
    return 0
  fi
  printf '%s安装完成！%s\n' "$GREEN$BOLD" "$RESET"
  printf '%s════════════════════════════════════════════════%s\n\n' "$GREEN$BOLD" "$RESET"

  printf '%s以后怎么用课程%s\n' "$BOLD" "$RESET"
  printf '    %s启动课程%s\n' "$BOLD" "$RESET"
  printf '        cd %s\n        ./start.sh\n\n' "$INSTALL_DIR"
  printf '    %s然后打开浏览器访问%s\n' "$BOLD" "$RESET"
  printf '        %s%s%s\n\n' "$CYAN" "$url" "$RESET"
  printf '    %s停止课程%s\n' "$BOLD" "$RESET"
  printf '        在运行课程的窗口按 Ctrl+C\n\n'

  printf '%s遇到问题怎么办%s\n' "$BOLD" "$RESET"
  printf '    1. 端口被占用：./start.sh --port 8790\n'
  printf '    2. 想更新课程：cd %s && git pull\n' "$INSTALL_DIR"
  printf '    3. 详细说明：见 %s/README.md 与 docs/QUICKSTART.md\n' "$INSTALL_DIR"
  printf '    4. 学习记录都在 %s/.state/ 里，删掉它等于重置进度\n\n' "$INSTALL_DIR"
}

# ---------------------------------------------------------------- 启动服务

launch() {
  local url="http://127.0.0.1:$PORT/"
  open_browser() {
    [ "$NO_OPEN" -eq 1 ] && return 0
    sleep 2
    local opener=""
    case "$OS_KIND" in
      macos) opener="open" ;;
      windows-shell) opener="start" ;;
      *)
        for candidate in xdg-open sensible-browser; do
          command -v "$candidate" >/dev/null 2>&1 && { opener="$candidate"; break; }
        done
        ;;
    esac
    [ -n "$opener" ] && "$opener" "$url" >/dev/null 2>&1 || true
  }

  if [ "$DRY_RUN" -eq 1 ]; then
    info "预演模式：不会启动服务（正式安装会打开 $url）"
    return 0
  fi

  # 非交互环境（脚本、CI、管道）默认**不启动**：否则服务会一直占用前台，
  # 让自动化流程看起来"卡死"。要启动就显式加 --start。
  if [ ! -t 0 ] && [ "$FORCE_START" -eq 0 ]; then
    printf '\n'
    info '当前不是可交互终端，已跳过自动启动。'
    info "想启动请执行：cd $INSTALL_DIR && ./start.sh --port $PORT"
    printf '\n'
    return 0
  fi

  printf '%s现在启动课程吗？%s [Y/n] ' "$DIM" "$RESET"
  local answer="y"
  read -r answer || answer="y"
  case "$answer" in
    n|N|no|NO)
      printf '\n'
      info "好的。想启动时执行：cd $INSTALL_DIR && ./start.sh"
      printf '\n'
      return 0
      ;;
  esac

  printf '\n%s正在启动课程服务……%s\n' "$BOLD" "$RESET"
  printf '%s（浏览器会自动打开；停止服务请按 Ctrl+C）%s\n\n' "$DIM" "$RESET"
  open_browser &
  cd "$INSTALL_DIR"
  exec "$PYTHON" -m server.app --port "$PORT"
}

# ---------------------------------------------------------------- 主流程

main() {
  banner
  check_environment
  obtain_project
  build_content
  self_check
  maybe_docker
  print_summary
  launch
}

cleanup_self() {
  # deploy.sh 把脚本下载到临时目录后 exec 进来（exec 会让它的 trap 失效），
  # 所以由我们负责删掉那个临时目录，别在用户机器上留垃圾。
  if [ -n "$SELF_DIR" ] && [ -d "$SELF_DIR" ] && case "$SELF_DIR" in /tmp/*|/var/tmp/*) true;; *) false;; esac; then
    rm -rf "$SELF_DIR" 2>/dev/null || true
  fi
}
trap cleanup_self EXIT

main "$@"

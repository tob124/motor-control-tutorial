#!/usr/bin/env bash
# 启动本机课程服务。
#
#   ./start.sh                 默认 127.0.0.1:8770
#   ./start.sh --port 8790     换端口
#   ./start.sh --check         只做自检，不启动
#
# 只使用系统自带的 Python 3.12，不需要 pip、不需要虚拟环境、不需要联网。

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if ! command -v python3 >/dev/null 2>&1; then
  echo '找不到 python3。请先安装 Python 3.12 或更高版本。' >&2
  exit 1
fi

if [ ! -f course/curriculum.json ]; then
  echo '课程内容尚未生成，正在生成……'
  python3 scripts/build_content.py
fi

# rootless Docker 把 docker 客户端装在 ~/bin，并依赖 XDG_RUNTIME_DIR 找套接字。
# 这两项通常在 .bashrc 里设置，但服务可能从桌面快捷方式或 systemd 启动，
# 那样就继承不到。这里显式补上，让容器实验能自动被检测到。
export PATH="$HOME/bin:$PATH"
if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi

exec python3 -m server.app "$@"

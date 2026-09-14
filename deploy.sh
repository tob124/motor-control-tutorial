#!/usr/bin/env bash
# ============================================================================
#  ROBOCON 电控实训教室 · 一行命令安装
#
#  面向完全没用过命令行的同学。把下面这一整行复制到终端回车即可：
#
#     bash <(curl -fsSL https://raw.githubusercontent.com/tob124/motor-control-tutorial/main/deploy.sh)
#
#  或（有些终端不支持上面那种写法时用这个）：
#
#     curl -fsSL https://raw.githubusercontent.com/tob124/motor-control-tutorial/main/deploy.sh | bash
#
#  它会自动：下载课程 → 生成内容 → 自检 → 询问是否现在启动。
#  不会安装系统服务，不会修改你的系统配置。
#
#  作者：Connor He 和 Astra
# ============================================================================

set -euo pipefail

REPO_OWNER_REPO="${ROBOCON_REPO:-tob124/motor-control-tutorial}"
BRANCH="${ROBOCON_BRANCH:-main}"
RAW_BASE="${ROBOCON_RAW_BASE:-https://raw.githubusercontent.com/$REPO_OWNER_REPO/$BRANCH}"

printf '\n正在准备安装程序……\n'

# 先把 install.sh 取下来，再交给它干活。
# 这样"一行命令"和"手动下载后运行"走的是同一套逻辑，只是入口不同。
TMP_DIR="$(mktemp -d -t robocon-deploy-XXXXXX)"
# 注意：下面用 exec 把控制权交给 install.sh，进程会被替换，
# 所以这里注册 trap 是没用的（EXIT 不会触发到我们这段代码）。
# 改为让 install.sh 用完自己删掉临时目录。

fetch() {
  local url="$1" out="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 --connect-timeout 20 "$url" -o "$out"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --tries=3 --timeout=20 "$url" -O "$out"
  else
    printf '\033[31m需要 curl 或 wget 才能下载。\033[0m\n' >&2
    printf '  Ubuntu/Debian： sudo apt install -y curl\n' >&2
    printf '  macOS：        已自带 curl\n' >&2
    return 1
  fi
}

if ! fetch "$RAW_BASE/install.sh" "$TMP_DIR/install.sh"; then
  printf '\n\033[31m下载安装脚本失败。\033[0m\n\n' >&2
  printf '可能的原因与做法：\n' >&2
  printf '  1. 网络不通：检查能否打开 https://github.com/%s\n' "$REPO_OWNER_REPO" >&2
  printf '  2. 仓库地址或分支不对：当前用 %s（分支 %s）\n' "$REPO_OWNER_REPO" "$BRANCH" >&2
  printf '  3. 换一种写法：\n' >&2
  printf '       curl -fsSL %s/deploy.sh | bash\n\n' "$RAW_BASE" >&2
  exit 1
fi

# 把安装脚本里默认的仓库地址对齐过来，避免两处配置不一致
if [ "$REPO_OWNER_REPO" != "tob124/motor-control-tutorial" ]; then
  export ROBOCON_REPO_URL="${ROBOCON_REPO_URL:-https://github.com/$REPO_OWNER_REPO.git}"
fi

chmod +x "$TMP_DIR/install.sh"
exec bash "$TMP_DIR/install.sh" --self-dir "$TMP_DIR" "$@"

#!/usr/bin/env bash
# 构建可选的真实终端实验镜像。
#
# 课程作者：Connor He 和 Astra。
#
# 这个脚本**由你手动执行**，网页不会自动运行它。
# 它只做两件事：检查环境、构建镜像。不改宿主机的 Docker 配置。
#
#   bash scripts/prepare-labs.sh

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

IMAGE='robocon-lab:1.0'
CONTEXT='rootless'

if ! command -v docker >/dev/null 2>&1; then
  cat >&2 <<'EOF'
没有检测到 docker。

课程的全部网页实验、课程内容与作品导出都不需要 Docker。
如果你确实想要容器里的真实终端实验，请先按官方说明安装并启用 rootless 模式：

  https://docs.docker.com/engine/security/rootless/

或者先阅读仓库里的 scripts/setup-docker.sh（它会修改宿主机，必须由你审阅后手动运行）。
EOF
  exit 1
fi

if ! docker --context "$CONTEXT" info >/dev/null 2>&1; then
  echo "docker context '$CONTEXT' 不可用。" >&2
  echo "请确认 rootless 服务已启动：systemctl --user status docker" >&2
  echo "若你使用系统级 docker，可自行改为默认 context，但课程默认只使用 rootless。" >&2
  exit 1
fi

free_gb=$(df -BG --output=avail . | tail -1 | tr -dc '0-9')
if [ "${free_gb:-0}" -lt 4 ]; then
  echo "磁盘可用空间只有 ${free_gb} GB，构建镜像建议至少 4 GB。" >&2
  exit 1
fi

echo "准备构建镜像 $IMAGE …"
echo "  上下文：$(pwd)"
echo "  预计占用：300–600 MB（含 apt 包）"
docker --context "$CONTEXT" build -t "$IMAGE" -f lab/Dockerfile .

echo
echo '完成。现在可以在课程页面点击「启动第 N 周实验」了。'
docker --context "$CONTEXT" images "$IMAGE"

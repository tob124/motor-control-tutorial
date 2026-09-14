#!/usr/bin/env bash
# 收尾：在**无沙箱的普通会话**里创建 rootless 守护进程，并构建两个课程的镜像。
#
# 为什么需要单独一个脚本：
#   rootless Docker 依赖 newuidmap 这个 setuid 程序为用户命名空间写 uid_map。
#   如果当前 shell 带 no_new_privs（沙箱、某些 agent 运行时），这一步必然失败：
#       newuidmap: open of uid_map failed: Permission denied
#   这不是 Docker 配置问题，而是当前 shell 的安全限制。
#   所以本脚本必须在桌面终端（普通登录会话）里运行。
#
# 用法：
#   bash scripts/finish-docker-setup.sh                 # 本项目 + 姊妹项目基础镜像
#   bash scripts/finish-docker-setup.sh --sister-only   # 只弄姊妹项目
#   bash scripts/finish-docker-setup.sh --with-modelica # 可选：额外构建 OpenModelica 镜像
#                                                       # 本机已决定不构建（需要额外 1.5-2.5 GiB）
#
# 姊妹项目路径可用环境变量覆盖：SISTER=/path/to/linux-interactive-tutorial

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

WITH_MODELICA=0
SISTER_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --with-modelica) WITH_MODELICA=1; shift ;;
    --sister-only)   SISTER_ONLY=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

SISTER="${SISTER:-$HOME/code/linux-interactive-tutorial}"
PROJECT="$(pwd)"

# ---------------------------------------------------------------- 前置检查
IFS= read -r nnp < <(grep '^NoNewPrivs:' /proc/self/status 2>/dev/null | awk '{print $2}')
if [ "${nnp:-0}" = "1" ]; then
  cat >&2 <<'EOF'
当前 shell 带 no_new_privs（NoNewPrivs=1），rootless 装不上。

请在桌面终端（普通登录会话）里运行本脚本，或用下面这条命令确认环境：
    grep NoNewPrivs /proc/self/status    # 期望输出 0 或没有该行
EOF
  exit 1
fi

command -v docker >/dev/null 2>&1 || { echo '找不到 docker，请先运行 bash scripts/setup-docker.sh' >&2; exit 1; }

export PATH="$HOME/bin:$PATH"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DOCKER_CONTEXT=rootless

# ---------------------------------------------------------------- rootless 守护进程
if ! docker info >/dev/null 2>&1; then
  echo "### rootless 守护进程尚未运行，正在创建"
  if [ -x /usr/bin/dockerd-rootless-setuptool.sh ]; then
    dockerd-rootless-setuptool.sh install
  else
    echo '缺少 dockerd-rootless-setuptool.sh，请先安装 docker-ce-rootless-extras。' >&2
    exit 1
  fi
  systemctl --user start docker
  for _ in $(seq 1 20); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
fi

echo "### rootless 状态"
docker info --format '  版本        {{.ServerVersion}}'
docker info --format '  安全选项    {{json .SecurityOptions}}'
docker info --format '  Cgroup 版本 {{.CgroupVersion}}'
docker info --format '  数据目录    {{.DockerRootDir}}'
echo "  当前上下文  $(docker context show)"

# 姊妹项目要求：必须是 rootless，且 cgroup v2
docker info --format '{{json .SecurityOptions}}' | grep -q rootless \
  || { echo '需要 rootless Docker（当前不是 rootless 上下文）。' >&2; exit 1; }
test "$(docker info --format '{{.CgroupVersion}}')" = 2 \
  || { echo '需要 cgroup v2 才能限制容器内存与进程数。' >&2; exit 1; }

# 镜像加速器：Docker Hub 不可达时构建会在 load metadata 阶段失败
if ! docker pull --quiet ubuntu:24.04 >/dev/null 2>&1; then
  cat >&2 <<'EOF'
无法拉取 ubuntu:24.04（Docker Hub 或加速器不可达）。

如果是网络环境访问不到 Docker Hub，先配置加速器：
    bash scripts/configure-docker-mirror.sh

然后再运行本脚本。
EOF
  exit 1
fi

# 磁盘：基础镜像 4 GiB，OpenModelica 再 4 GiB
free_gib=$(python3 -c "import shutil;print(int(shutil.disk_usage('$PROJECT').free/1024**3))")
need=4
if [ "$WITH_MODELICA" -eq 1 ]; then need=8; fi
echo "### 磁盘：项目所在盘可用 ${free_gib} GiB，需要 ≥ ${need} GiB"
[ "$free_gib" -ge "$need" ] || { echo '磁盘不足，请先清理。' >&2; exit 1; }

# ---------------------------------------------------------------- 姊妹项目镜像
build_sister() {
  [ -d "$SISTER" ] || { echo "跳过：找不到姊妹项目 $SISTER（可用 SISTER=路径 指定）" >&2; return 0; }
  echo
  echo "### 构建姊妹项目基础镜像（mechlinux-base:1.0）"
  ( cd "$SISTER" && docker build -f lab/Dockerfile -t mechlinux-base:1.0 . )
  mkdir -p "$SISTER/.state"
  docker image inspect mechlinux-base:1.0 > "$SISTER/.state/base-image-manifest.json"
  echo "  清单已写入 $SISTER/.state/base-image-manifest.json"

  if [ "$WITH_MODELICA" -eq 1 ]; then
    echo
    echo "### 构建姊妹项目 OpenModelica 镜像（mechlinux-modelica:1.0）"
    ( cd "$SISTER" && docker build -f lab/Dockerfile.modelica -t mechlinux-modelica:1.0 . )
    docker image inspect mechlinux-modelica:1.0 > "$SISTER/.state/modelica-image-manifest.json"
    echo "  清单已写入 $SISTER/.state/modelica-image-manifest.json"
  else
    echo "  （按决定跳过 OpenModelica 镜像；姊妹项目第 6 周需要时再加 --with-modelica）"
  fi
}

# ---------------------------------------------------------------- 本项目镜像
build_project() {
  echo
  echo "### 构建本项目镜像（robocon-lab:1.0）"
  docker build -t robocon-lab:1.0 -f lab/Dockerfile .
  mkdir -p .state
  docker image inspect robocon-lab:1.0 > .state/lab-image-manifest.json
  echo "  清单已写入 .state/lab-image-manifest.json"
}

if [ "$SISTER_ONLY" -eq 0 ]; then
  build_project
fi
build_sister

echo
echo "### 镜像清单"
docker images --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' | grep -E "robocon-lab|mechlinux" || true
echo
echo "完成。两个课程都可以在网页里点『启动实验』了："
echo "  姊妹项目：cd $SISTER && ./start.sh"
echo "  本项目  ：cd $PROJECT && ./start.sh"

#!/usr/bin/env bash
# 为 Ubuntu 24.04 安装 Docker 并启用 rootless 模式（**可选的**宿主机改动）。
#
# 课程作者：Connor He 和 Astra。
#
# 重要：这个脚本会修改宿主机（添加软件源、安装 Docker、停用系统级 docker
# 服务与套接字、创建当前用户的 rootless 服务）。它**不会**被网页自动执行，
# 必须由你在阅读后手动运行：
#
#     bash scripts/setup-docker.sh                 # 默认用官方源
#     bash scripts/setup-docker.sh --mirror aliyun # 官方源被重置时改用镜像
#
# 如果本机已经有 Docker，脚本会直接退出，不会动你现有的配置。
# 不做这一步完全不影响课程：所有网页实验都不需要 Docker。

set -euo pipefail

MIRROR_PRESET='official'
while [ $# -gt 0 ]; do
  case "$1" in
    --mirror) MIRROR_PRESET="${2:-official}"; shift 2 ;;
    --mirror=*) MIRROR_PRESET="${1#--mirror=}"; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

case "$MIRROR_PRESET" in
  official) MIRROR='https://download.docker.com/linux/ubuntu' ;;
  aliyun)   MIRROR='https://mirrors.aliyun.com/docker-ce/linux/ubuntu' ;;
  tsinghua) MIRROR='https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu' ;;
  ustc)     MIRROR='https://mirrors.ustc.edu.cn/docker-ce/linux/ubuntu' ;;
  tencent)  MIRROR='https://mirrors.cloud.tencent.com/docker-ce/linux/ubuntu' ;;
  *) echo "未知镜像：$MIRROR_PRESET（可选 official|aliyun|tsinghua|ustc|tencent）" >&2; exit 2 ;;
esac

if [ "${EUID}" -eq 0 ]; then
  echo "请不要用 root 运行：rootless 模式必须由普通用户配置。" >&2
  exit 1
fi

# rootless 守护进程必须在一个没有 no_new_privs 的普通会话里启动。
# 受限 shell（沙箱、某些 agent 运行时）里会失败在
# "newuidmap: open of uid_map failed: Permission denied"，
# 因为 newuidmap 是 setuid 程序，no_new_privs 让它的提权失效。
IFS= read -r nnp < <(grep '^NoNewPrivs:' /proc/self/status 2>/dev/null | awk '{print $2}')
if [ "${nnp:-0}" = "1" ]; then
  cat >&2 <<'EOF'
检测到当前 shell 带 no_new_privs（NoNewPrivs=1）。

在这种环境下 newuidmap 无法为用户命名空间写 uid_map，rootless 一定装不上。
这不是 Docker 的配置问题，而是当前 shell 的安全限制。

请在你的普通登录会话（桌面终端）里重新运行本脚本：
    bash scripts/setup-docker.sh

可以用这条命令确认当前 shell 是否受限（输出应为 0 或没有该行）：
    grep NoNewPrivs /proc/self/status
EOF
  exit 1
fi

# 已经装过 Docker：不再重复装包，但继续完成 rootless 剩余步骤。
already_installed=0
if command -v dockerd >/dev/null 2>&1; then
  already_installed=1
  echo "已检测到 Docker 二进制，跳过安装步骤，只完成 rootless 配置。"
  if [ ! -x /usr/bin/dockerd-rootless-setuptool.sh ]; then
    echo "但缺少 docker-ce-rootless-extras。请先补装：" >&2
    echo "  sudo apt-get install -y docker-ce-rootless-extras" >&2
    exit 1
  fi
fi

echo "将要执行以下操作（每一项都需要你的 sudo 授权）："
cat <<PLAN
  1. 安装 apt 依赖：ca-certificates curl uidmap dbus-user-session
                    fuse-overlayfs slirp4netns
  2. 添加 Docker 软件源：$MIRROR
  3. 安装 docker-ce docker-ce-cli containerd.io docker-buildx-plugin
     docker-ce-rootless-extras
  4. 停用系统级 docker.service / docker.socket / containerd.service
     （避免 root 守护进程常驻；只保留当前用户的 rootless 运行时）
  5. 运行 dockerd-rootless-setuptool.sh install 创建用户级 docker.service
  6. 把 ~/bin 加入 PATH 并启动 rootless 服务
PLAN
read -r -p "继续？输入 yes 继续，其它任意键退出： " answer
if [ "${answer}" != "yes" ]; then
  echo "已取消，未作任何改动。"
  exit 0
fi

if [ "$already_installed" -eq 0 ]; then
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl uidmap dbus-user-session \
                          fuse-overlayfs slirp4netns

  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL "$MIRROR/gpg" -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc

  printf 'Types: deb\nURIs: %s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
    "$MIRROR" "$(. /etc/os-release && echo "$VERSION_CODENAME")" "$(dpkg --print-architecture)" \
    | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null

  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
                          docker-buildx-plugin docker-ce-rootless-extras

  # rootless 模式不需要常驻的系统级守护进程
  sudo systemctl disable --now docker.service docker.socket containerd.service 2>/dev/null || true
  sudo rm -f /var/run/docker.sock
fi

export PATH="$HOME/bin:$PATH"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
dockerd-rootless-setuptool.sh install
systemctl --user start docker

# 让后续登录也能找到 rootless 的 docker 客户端
if ! grep -q 'rootless docker PATH' "$HOME/.bashrc" 2>/dev/null; then
  printf '\n# rootless docker PATH（由课程 setup-docker.sh 添加）\nexport PATH="$HOME/bin:$PATH"\n' >> "$HOME/.bashrc"
fi

echo
echo "验证："
docker --context rootless info | head -5
echo
echo "完成。接下来构建课程实验镜像："
echo "  bash scripts/prepare-labs.sh"

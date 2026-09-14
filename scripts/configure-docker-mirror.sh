#!/usr/bin/env bash
# 为 rootless Docker 配置镜像加速器。
#
# 课程作者：Connor He 和 Astra。
#
# 为什么需要：
#   部分网络环境访问不到 Docker Hub（registry-1.docker.io 连接被拒），
#   表现为构建时卡在：
#       ERROR [internal] load metadata for docker.io/library/ubuntu:24.04
#       dial tcp ...: connect: connection refused
#   配置一个可达的加速器即可；这属于网络环境适配，不影响镜像内容。
#
# 用法：
#   bash scripts/configure-docker-mirror.sh            # 写入加速器并重启守护进程
#   bash scripts/configure-docker-mirror.sh --show     # 只看当前配置
#
# 备注：加速器是第三方服务，会看到你拉取的镜像名（看不到内容以外的隐私）。
# 如果本机能直连 Docker Hub，不要运行本脚本。

set -euo pipefail

CONFIG="${HOME}/.config/docker/daemon.json"

if [ "${1:-}" = "--show" ]; then
  echo "配置文件：$CONFIG"
  [ -f "$CONFIG" ] && cat "$CONFIG" || echo "（尚不存在）"
  echo
  echo "当前守护进程的镜像加速器："
  docker info --format '{{json .RegistryConfig.Mirrors}}' 2>/dev/null || echo "（守护进程未运行）"
  exit 0
fi

export PATH="$HOME/bin:$PATH"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DOCKER_CONTEXT=rootless

# 候选加速器按探测结果排序：前两个在本环境实测能返回 ubuntu:24.04 的 manifest。
MIRRORS_JSON='["https://dockerproxy.net","https://docker.1panel.live","https://docker.m.daocloud.io"]'

mkdir -p "$(dirname "$CONFIG")"
if [ -f "$CONFIG" ]; then
  cp "$CONFIG" "${CONFIG}.bak.$(date +%Y%m%d%H%M%S)"
  echo "已备份原有配置。"
  # 合并：保留用户已有的其他设置，只覆盖 registry-mirrors
  python3 - "$CONFIG" "$MIRRORS_JSON" <<'PY'
import json, sys
path, mirrors = sys.argv[1], json.loads(sys.argv[2])
try:
    data = json.load(open(path, encoding='utf-8'))
    if not isinstance(data, dict):
        data = {}
except (OSError, ValueError):
    data = {}
data['registry-mirrors'] = mirrors
json.dump(data, open(path, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
print('  registry-mirrors 已更新（保留其它原有配置项）')
PY
else
  cat > "$CONFIG" <<JSON
{
  "registry-mirrors": ${MIRRORS_JSON}
}
JSON
  echo "  已创建 $CONFIG"
fi

echo
echo "配置内容："
cat "$CONFIG"
echo

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "重启 rootless 守护进程…"
  systemctl --user restart docker
  for _ in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
  echo
  echo "生效后的镜像加速器："
  docker info --format '  {{json .RegistryConfig.Mirrors}}'
  echo
  echo "验证：拉取一个很小的镜像试试"
  if docker pull --quiet hello-world >/dev/null 2>&1; then
    echo "  ✓ 可以拉取镜像"
    docker rmi hello-world >/dev/null 2>&1 || true
  else
    echo "  ✗ 仍然拉不动，可能是加速器本身不可用；换一个再试（编辑 $CONFIG）"
  fi
else
  echo "守护进程未运行；配置已写入，下次启动时生效。"
fi

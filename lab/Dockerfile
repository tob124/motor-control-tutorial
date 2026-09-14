# 可选的真实终端实验镜像。
#
# 课程作者：Connor He 和 Astra。
#
# 说明：
#   * 这个镜像只用于可选的容器实验；网页实验台与全部课程内容都不依赖它。
#   * 构建由用户手动执行（需要先有可用的 rootless Docker）：
#         bash scripts/finish-docker-setup.sh
#   * 镜像里预装编译工具与 CAN 工具，用于第 5、6、9、10 周的实机对照练习。
#   * 容器不会自己退出：CMD 是一个长驻循环，学员通过 `docker exec`
#     进入同一个容器（后台 WS 终端也是这么接的）。若用 `bash -l` 作 CMD，
#     没有 TTY 时会立刻退出，容器变成 Stopped，后续 exec 全部失败。

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash nano sudo git python3 python3-pip python3-venv \
        build-essential cmake gdb make \
        can-utils iproute2 iputils-ping \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# 非特权学员账号：容器内不使用 root 交互
RUN if getent passwd 1000 >/dev/null; then \
        usermod -l student -d /home/student -m "$(getent passwd 1000 | cut -d: -f1)"; \
    else \
        useradd -m -u 1000 -s /bin/bash student; \
    fi \
    && if ! getent group student >/dev/null; then groupmod -n student "$(getent group 1000 | cut -d: -f1)"; fi \
    && usermod -g student -s /bin/bash student \
    && echo 'student:robocon' | chpasswd \
    && mkdir -p /workspace /opt/course \
    && chown student:student /workspace /home/student

# 把课程里的仿真内核放进镜像，学员可以用它做独立对照实验
COPY server/__init__.py /opt/course/server/__init__.py
COPY server/sim/ /opt/course/server/sim/

RUN printf 'export PYTHONPATH=/opt/course\n' >> /home/student/.bashrc \
    && chown -R student:student /opt/course /home/student/.bashrc \
    && python3 -c "import sys; sys.path.insert(0, '/opt/course'); \
from server.sim import spec, engine; \
cfg = spec.validate({'kind': 'dc', 'scenario': 'open_loop', 'duration': 0.2}); \
r = engine.simulate(cfg); assert r['series'], '仿真内核自检失败'; \
print('课程仿真内核已就绪')"

WORKDIR /workspace
USER student

# 长驻：等待 docker exec 进入
CMD ["sleep", "infinity"]

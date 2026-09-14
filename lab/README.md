# lab/ 目录说明

课程作者：Connor He 和 Astra。

这是**可选的**真实终端实验环境。没有 Docker 时，课程内容、全部网页实验台
与作品导出都正常使用；检测不到 Docker 时页面会如实报告，不会把实验判为通过。

- `Dockerfile`：课程实验镜像（Ubuntu 24.04 + 编译工具 + CAN 工具 + 课程仿真内核）。
- 构建：`bash scripts/finish-docker-setup.sh`（需先有可用的 rootless Docker）。

## 容器约定

| 项 | 值 |
|---|---|
| 镜像 | `robocon-lab:1.0` |
| 容器名 | `robocon-course-w<周号>` |
| 用户 | 非特权 `student`（uid 1000） |
| 工作目录 | `/workspace`（命名卷，不是宿主目录挂载） |
| 限制 | 内存 768 MB、CPU 1、PID 128、`cap-drop NET_RAW` |
| 命令行 | `docker --context rootless` |

容器用 `sleep infinity` 长驻，学员通过 `docker exec` 进入同一个容器
（网页终端也是如此）。这一点是刻意的：`bash -l` 作 CMD 时没有 TTY 会立即退出，
容器变成 Stopped，后续 `exec` 全部失败。

## 已实测

- 容器能启动并保持 Running；
- `docker exec -u student -w /workspace` 可用；
- `gcc 13.3` / `cmake 3.28` / `gdb 15.1` / `candump` / `python3` 均可调用；
- 镜像内置的课程仿真内核在构建时自检通过，容器内可直接
  `import server.sim` 做独立对照实验。

## 尚未覆盖

- 各周的任务检查器（`lab/checks.py`）还没实现。当前容器提供的是
  工具链与仿真内核，不是"自动判卷"。在补齐之前，课程页面不会声称
  容器实验"通过检查"。
- OpenModelica 镜像按决定不构建，与本项目容器无关。

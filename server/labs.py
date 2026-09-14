"""可选的 Docker 真实终端实验。

课程作者：Connor He 和 Astra。

**这一层是可选的，而且必须优雅降级。** 当前开发环境没有 Docker，
所以这里是"接口就绪、真实现、但没环境就不假装能用"：
``health()`` 会如实报告不可用，前端据此把终端面板换成准备指引。

安全边界（和姊妹项目一致）：

* 只使用名为 ``rootless`` 的 Docker context，不使用系统级 docker daemon；
* 容器带内存、CPU、PID 上限，丢弃 NET_RAW；
* 不挂载宿主机目录，只用命名卷；
* 不自动执行任何修改宿主机的命令——准备脚本由用户手动运行。
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

__all__ = ['Labs', 'LabError']

CONTEXT = 'rootless'
IMAGE = 'robocon-lab:1.0'


class LabError(Exception):
    pass


class Labs:
    def __init__(self, state_dir, root=None, prefix='robocon-course'):
        self.state_dir = Path(state_dir)
        self.root = Path(root) if root else None
        self.prefix = prefix
        self.sessions = {}

    # ------------------------------------------------------------ 环境
    def _docker(self, *args, timeout=20):
        if not shutil.which('docker'):
            raise LabError('本机没有安装 docker：真实终端实验不可用。'
                           '网页实验台与全部课程内容不受影响。')
        try:
            result = subprocess.run(
                ['docker', '--context', CONTEXT, *args],
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise LabError('docker 命令超时。请检查 rootless 服务是否在运行。')
        except OSError as error:
            raise LabError(f'无法执行 docker：{error}')
        return result

    def health(self):
        info = {'docker': bool(shutil.which('docker')), 'rootless': False,
                'image': False, 'ready': False, 'issues': [], 'sessions': list(self.sessions)}
        if not info['docker']:
            info['issues'].append('未检测到 docker：真实终端实验不可用。'
                                  '课程内容与网页实验台照常使用。')
            return info
        result = self._docker('info', '--format', '{{.ServerVersion}}')
        if result.returncode != 0:
            info['issues'].append(f'rootless docker 不可用：{result.stderr.strip()[:200]}')
            return info
        info['rootless'] = True
        images = self._docker('images', '--format', '{{.Repository}}:{{.Tag}}')
        info['image'] = IMAGE in images.stdout.split()
        if not info['image']:
            info['issues'].append(f'课程镜像 {IMAGE} 尚未构建。'
                                  '请手动运行 bash scripts/prepare-labs.sh。')
        info['ready'] = info['rootless'] and info['image']
        return info

    # ------------------------------------------------------------ 生命周期
    def name(self, week):
        return f'{self.prefix}-w{week}'

    def start(self, week):
        if not isinstance(week, int) or not 1 <= week <= 11:
            raise LabError('周号必须在 1–11 之间。')
        info = self.health()
        if not info['ready']:
            raise LabError('实验环境尚未就绪：' + '；'.join(info['issues']))
        container = self.name(week)
        self._docker('rm', '-f', container)          # 幂等：先清掉同名的
        result = self._docker(
            'run', '-d', '--name', container,
            '--label', f'org.robocon.course={self.prefix}',
            '--hostname', f'lab-w{week}',
            '--memory', '768m', '--cpus', '1', '--pids-limit', '128',
            '--cap-drop', 'NET_RAW',
            '--mount', f'type=volume,src={container}-work,dst=/workspace',
            '--mount', f'type=volume,src={container}-home,dst=/home/student',
            '-e', f'COURSE_WEEK={week}', IMAGE, timeout=60)
        if result.returncode != 0:
            raise LabError(f'启动容器失败：{result.stderr.strip()[:300]}')
        self.sessions[week] = container
        return {'week': week, 'container': container}

    def stop(self, week):
        container = self.name(week)
        self._docker('rm', '-f', container)
        self.sessions.pop(week, None)
        return {'week': week, 'stopped': True}

    def reset(self, week):
        """重置会删除当前周的工作文件。前端必须在调用前确认。"""
        container = self.name(week)
        self._docker('rm', '-f', container)
        for volume in (f'{container}-work', f'{container}-home'):
            self._docker('volume', 'rm', '-f', volume)
        self.sessions.pop(week, None)
        return {'week': week, 'reset': True,
                'note': '本周实验文件与练习密钥已清除；服务端的学习检查历史仍保留。'}

    def status(self, week):
        container = self.name(week)
        result = self._docker('inspect', '-f', '{{.State.Running}}', container)
        return {'week': week, 'container': container,
                'running': result.returncode == 0 and result.stdout.strip() == 'true'}

    # ------------------------------------------------------------ 执行与检查
    def run_check(self, week, command, timeout=90):
        """在容器里执行一条检查命令，返回 ``(returncode, stdout)``。"""
        container = self.name(week)
        result = self._docker('exec', '-u', 'student', '-w', '/workspace',
                              container, 'bash', '-lc', command, timeout=timeout)
        self._ = result
        return result.returncode, (result.stdout or '') + (result.stderr or '')

    def export(self, week):
        """把本周工作区打成 tar.gz 字节流。"""
        container = self.name(week)
        if not shutil.which('docker'):
            raise LabError('本机没有安装 docker，无法导出容器文件。')
        result = self._docker('exec', '-u', 'student', container,
                              'tar', '--exclude=.venv', '--exclude=build',
                              '-czf', '-', '-C', '/workspace', '.', timeout=120)
        if result.returncode != 0:
            raise LabError('导出失败：请先启动本周实验。')
        return result.stdout

    async def terminal_command(self, week):
        """返回连接容器终端所需的命令（供 WebSocket 桥接使用）。"""
        container = self.name(week)
        return ['docker', '--context', CONTEXT, 'exec', '-it', '-u', 'student',
                '-w', '/workspace', '-e', 'TERM=xterm-256color',
                container, 'bash', '-l']

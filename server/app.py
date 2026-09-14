"""本机课程服务：路由、内容、进度、仿真作业与导出。

课程作者：Connor He 和 Astra。

只用标准库。启动方式：

    python3 -m server.app              # 默认 127.0.0.1:8770
    python3 -m server.app --port 8790 --state .state

只监听本机回环地址；页面用一次性令牌与本地服务通信。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import secrets
import shutil
import sys
import time
import zipfile
from pathlib import Path

from . import httpd
from .jobs import JobError, JobManager
from .labs import LabError, Labs
from .sim import engine, scenarios, spec as simspec
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
VERSION = '1.0'
AUTHORS = ['Connor He', 'Astra']
DEFAULT_PORT = 8770


class Course:
    def __init__(self, root=ROOT, state_dir=None, port=DEFAULT_PORT, host='127.0.0.1'):
        self.root = Path(root)
        self.state_dir = Path(state_dir or self.root / '.state')
        self.store = Store(self.state_dir)
        self.jobs = JobManager(self.state_dir)
        self.labs = Labs(self.state_dir, root=self.root)
        self.token = secrets.token_urlsafe(32)
        self.port = port
        self.host = host
        self.started = time.time()
        self._content_cache = {}
        self.server = httpd.CourseServer(self.root, self.token, self.state_dir, host, port)
        self.server.course = self
        self._register_routes()

    # ------------------------------------------------------------ 内容
    def curriculum(self):
        return self._load_json('curriculum')

    def software(self):
        return self._load_json('software')

    def _load_json(self, name):
        path = self.root / 'course' / f'{name}.json'
        if not path.is_file():
            raise FileNotFoundError(
                f'缺少课程内容 {path.name}。请先运行 python3 scripts/build_content.py 生成。')
        stamp = path.stat().st_mtime
        cached = self._content_cache.get(name)
        if cached and cached[0] == stamp:
            return cached[1]
        value = json.loads(path.read_text(encoding='utf-8'))
        self._content_cache[name] = (stamp, value)
        return value

    def lesson(self, lesson_id):
        found = next((item for item in self.curriculum().get('lessons', [])
                      if item.get('id') == lesson_id), None)
        if not found:
            raise ValueError('未知课程单元。')
        return found

    # ------------------------------------------------------------ 路由
    def _register_routes(self):
        r = self.server.router
        r.get('/api/session', self.api_session)
        r.get('/api/health', self.api_health)
        r.get('/api/content/curriculum', self.api_curriculum)
        r.get('/api/content/software', self.api_software)
        r.get('/api/content/limits', self.api_limits)
        r.get('/api/progress', self.api_progress)
        r.post('/api/progress', self.api_progress_post)
        r.post('/api/check', self.api_check)
        r.post('/api/preferences', self.api_preferences)
        r.post('/api/sim/runs', self.api_run)
        r.get('/api/sim/runs/{id}', self.api_run_status)
        r.post('/api/sim/runs/{id}/cancel', self.api_run_cancel)
        r.get('/api/sim/runs/{id}/export', self.api_run_export)
        r.post('/api/sim/curves', self.api_curve)
        r.get('/api/sim/presets', self.api_presets)
        r.get('/api/portfolio/export', self.api_portfolio)
        r.get('/api/teacher', self.api_teacher)
        r.get('/api/labs/health', self.api_labs_health)
        r.post('/api/labs/{week}/start', self.api_lab_action)
        r.post('/api/labs/{week}/stop', self.api_lab_action)
        r.post('/api/labs/{week}/reset', self.api_lab_action)
        r.get('/api/labs/{week}/status', self.api_lab_status)

    # ------------------------------------------------------------ 处理器
    def api_session(self, handler, params, query, body):
        return 200, {'token': self.token, 'version': VERSION, 'authors': AUTHORS,
                     'started': self.started}

    def api_labs_health(self, handler, params, query, body):
        return 200, self.labs.health()

    def api_lab_status(self, handler, params, query, body):
        return 200, self.labs.status(self._week(params))

    def api_lab_action(self, handler, params, query, body):
        week = self._week(params)
        action = handler.path.rstrip('/').rsplit('/', 1)[-1]
        try:
            if action == 'start':
                return 200, self.labs.start(week)
            if action == 'stop':
                return 200, self.labs.stop(week)
            if action == 'reset':
                # 重置会删除文件，必须显式确认
                if not (body or {}).get('confirm'):
                    raise httpd.HttpError(400, '重置会删除本周实验文件，请先确认。')
                return 200, self.labs.reset(week)
        except LabError as error:
            raise httpd.HttpError(409, str(error))
        raise httpd.HttpError(404, '未知的实验操作。')

    @staticmethod
    def _week(params):
        try:
            week = int(params['week'])
        except (KeyError, TypeError, ValueError):
            raise httpd.HttpError(400, '周号不合法。')
        if not 1 <= week <= 11:
            raise httpd.HttpError(400, '周号必须在 1–11 之间。')
        return week

    def api_health(self, handler, params, query, body):
        usage = shutil.disk_usage(self.root)
        # 容器环境的状态只有一个真源：Labs.health()。
        # 早期版本把 rootless / ready 硬编码成 False，Docker 装好之后就会给出
        # 错误的结论——环境检测一旦不准，比没有更糟。
        labs = self.labs.health()
        issues = list(labs['issues'])
        if usage.free < 2 * 1024 ** 3:
            issues.append(f'磁盘可用空间只剩 {usage.free / 1024 ** 3:.1f} GiB，'
                          '构建或运行容器镜像前请先清理。')
        return 200, {
            'python': sys.version.split()[0],
            'platform': sys.platform,
            'disk_free_gb': round(usage.free / 1024 ** 3, 2),
            'disk_total_gb': round(usage.total / 1024 ** 3, 2),
            'docker': labs['docker'],
            'rootless': labs['rootless'],
            'image': labs['image'],
            'ready': labs['ready'],
            'issues': issues,
            'active_runs': len(self.jobs.jobs),
            'uptime_s': round(time.time() - self.started, 1),
            'content_ok': True,
        }

    def api_curriculum(self, handler, params, query, body):
        return 200, self.curriculum()

    def api_software(self, handler, params, query, body):
        return 200, self.software()

    def api_limits(self, handler, params, query, body):
        return 200, {'params': simspec.describe_limits(),
                     'kinds': list(simspec.KINDS),
                     'scenarios': {kind: list(names)
                                   for kind, names in simspec.SCENARIOS_BY_KIND.items()},
                     'faults': list(simspec.FAULTS),
                     'curves': list(scenarios.CURVES)}

    def api_progress(self, handler, params, query, body):
        return 200, self.store.progress()

    def api_progress_post(self, handler, params, query, body):
        """记录阅读标记或理解题得分。"""
        if not isinstance(body, dict):
            raise ValueError('请求必须是对象。')
        lesson_id = body.get('lesson')
        if not isinstance(lesson_id, str) or not lesson_id:
            raise ValueError('缺少课程编号。')
        self.lesson(lesson_id)                 # 顺带校验课程存在
        action = body.get('action', 'read')
        if action == 'read':
            self.store.record_read(lesson_id)
        elif action == 'quiz':
            answers = body.get('answers')
            if not isinstance(answers, list):
                raise ValueError('答案必须是数组。')
            lesson = self.lesson(lesson_id)
            quiz = lesson.get('quiz') or []
            if len(answers) != len(quiz):
                raise ValueError(f'本单元有 {len(quiz)} 道理解题，收到 {len(answers)} 个答案。')
            clean = []
            for index, answer in enumerate(answers):
                if answer is None or answer == -1:
                    clean.append(-1)
                    continue
                if isinstance(answer, bool) or not isinstance(answer, int):
                    raise ValueError(f'第 {index + 1} 题的答案必须是选项序号。')
                if not 0 <= answer < len(quiz[index].get('options', [])):
                    raise ValueError(f'第 {index + 1} 题的答案超出选项范围。')
                clean.append(answer)
            correct = sum(1 for index, answer in enumerate(clean)
                          if answer == quiz[index].get('answer'))
            score = round(100.0 * correct / max(1, len(quiz)))
            self.store.record_quiz(lesson_id, {'answers': clean, 'correct': correct}, score)
            return 200, {'score': score, 'correct': correct, 'total': len(quiz),
                         'pass': score >= 80}
        else:
            raise ValueError('未知的进度操作。')
        return 200, self.store.progress()

    def api_check(self, handler, params, query, body):
        """课程自检：确认单元引用的实验预设、软件条目都存在。"""
        lesson_id = (body or {}).get('lesson')
        curriculum = self.curriculum()
        software_ids = {item['id'] for item in self.software()}

        lessons = curriculum.get('lessons', [])
        known = {item['id'] for item in lessons}
        issues = []
        target = [item for item in lessons if item['id'] == lesson_id] if lesson_id else lessons
        if lesson_id and not target:
            raise ValueError('未知课程单元。')
        for lesson in target:
            for software in lesson.get('softwareIds') or []:
                if software not in software_ids:
                    issues.append({'lesson': lesson['id'], 'kind': 'software',
                                   'detail': f'引用了不存在的工具 {software}'})
            for task in lesson.get('tasks') or []:
                if not task.get('title'):
                    issues.append({'lesson': lesson['id'], 'kind': 'task',
                                   'detail': '任务缺少标题'})
            # 课程里的 labs 只是 {id, title} 引用；它指向的实验能否真正跑起来，
            # 由 /api/sim/presets 的 validate 调用与 tests/test_content.py 共同保证。
            for experiment in lesson.get('labs') or []:
                if not isinstance(experiment, dict):
                    issues.append({'lesson': lesson['id'], 'kind': 'lab',
                                   'detail': '实验条目必须是对象'})
                    continue
                if not experiment.get('id') or not experiment.get('title'):
                    issues.append({'lesson': lesson['id'], 'kind': 'lab',
                                   'detail': '实验条目缺少 id 或 title'})
        return 200, {'ok': not issues, 'issues': issues,
                     'checked_lessons': len(target), 'known_lessons': len(known)}

    def api_preferences(self, handler, params, query, body):
        self.store.save_preferences(body or {})
        return 200, self.store.progress()

    # ------------------------------------------------------------ 仿真
    def _run_simulation(self, raw_spec):
        cfg = simspec.validate(raw_spec)
        return engine.simulate(cfg)

    def api_run(self, handler, params, query, body):
        raw = (body or {}).get('spec', body)
        if not isinstance(raw, dict):
            raise ValueError('仿真参数必须是对象。')
        cfg = simspec.validate(raw)            # 先校验，让错误立刻返回而不是丢进队列
        # validate 会在结果里附上 warnings（校验阶段的产物，不属于配置本身）。
        # 作业线程会再次调用 validate，所以要把它去掉，否则会被判成"未登记字段"。
        stored = {key: value for key, value in cfg.items() if key != 'warnings'}
        run_id = self.jobs.submit(stored, self._run_simulation)
        self.store.create_run(run_id, {key: value for key, value in stored.items()
                                       if key != 'params'} | {'params': stored.get('params', {})})
        return 202, {'id': run_id, 'status': 'queued'}

    def api_run_status(self, handler, params, query, body):
        try:
            payload = self.jobs.lookup(params['id'])
        except JobError as error:
            raise httpd.HttpError(404, str(error))
        if payload.get('status') in ('succeeded', 'failed'):
            self.store.finish_run(params['id'], payload['status'],
                                  (payload.get('result') or {}).get('metrics'))
        return 200, payload

    def api_run_cancel(self, handler, params, query, body):
        try:
            self.jobs.cancel(params['id'])
        except JobError as error:
            raise httpd.HttpError(404, str(error))
        return 200, {'id': params['id'], 'status': 'failed'}

    def api_run_export(self, handler, params, query, body):
        try:
            payload = self.jobs.lookup(params['id'])
        except JobError as error:
            raise httpd.HttpError(404, str(error))
        if payload.get('status') != 'succeeded':
            raise httpd.HttpError(409, '这次实验还没有成功完成，暂时不能导出。')
        result = payload['result']
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('README.txt', _export_readme(result))
            archive.writestr('metrics.json', json.dumps(result.get('metrics', {}),
                                                        ensure_ascii=False, indent=2))
            archive.writestr('machine.json', json.dumps(result.get('machine', {}),
                                                        ensure_ascii=False, indent=2))
            warnings = list(result.get('warnings') or []) + list(result.get('notes') or [])
            archive.writestr('warnings.txt', '\n'.join(warnings) or '（无告警）\n')
            archive.writestr('trace.csv', _trace_csv(result))
        handler._send(200, buffer.getvalue(), 'application/zip',
                      {'Content-Disposition': f'attachment; filename="sim-{params["id"]}.zip"'})
        return None

    def api_curve(self, handler, params, query, body):
        raw = (body or {}).get('spec', body)
        name = (body or {}).get('curve') or query.get('curve', [''])[0]
        if not isinstance(raw, dict) or not name:
            raise ValueError('需要同时给出 curve 名称与 spec。')
        cfg = simspec.validate(raw)
        started = time.time()
        payload = scenarios.curve(cfg, name)
        payload['curve'] = name
        payload['elapsed_s'] = round(time.time() - started, 3)
        return 200, payload

    def api_presets(self, handler, params, query, body):
        """实验预设：课程单元可以直接跳到配置好的实验台。"""
        return 200, _PRESETS

    # ------------------------------------------------------------ 导出
    def api_portfolio(self, handler, params, query, body):
        text = self._portfolio_markdown()
        handler._send(200, text, 'text/markdown; charset=utf-8',
                      {'Content-Disposition':
                       'attachment; filename="robocon-control-portfolio.md"'})
        return None

    def _portfolio_markdown(self):
        progress = self.store.progress()
        preferences = progress['preferences']
        lessons = self.curriculum().get('lessons', [])
        weeks = {week['id']: week for week in self.curriculum().get('weeks', [])}
        by_id = {item['lesson']: item for item in progress['lessons']}

        def finished(lesson):
            record = by_id.get(lesson['id']) or {}
            return bool(record.get('read_at')) and record.get('score', 0) >= 80

        lines = ['# RoboCON 电控实训 · 学习与设计记录', '',
                 f'- 学员：{preferences.get("studentName") or "（未填写）"}',
                 f'- 队伍：{preferences.get("teamName") or "（未填写）"}',
                 f'- 项目：{preferences.get("projectTitle") or "RoboCON 电控系统设计"}',
                 f'- 导出时间：{time.strftime("%Y-%m-%d %H:%M:%S")}',
                 f'- 课程作者：{" 和 ".join(AUTHORS)}', '',
                 '## 总览', '',
                 f'- 已完成单元：{sum(1 for l in lessons if finished(l))} / {len(lessons)}',
                 f'- 理解题平均分：'
                 f'{_average([by_id.get(l["id"], {}).get("score", 0) for l in lessons]):.1f}',
                 f'- 已运行实验次数：{progress["attempts"]}', '',
                 '## 逐周记录', '']
        for week_id, week in weeks.items():
            week_lessons = [item for item in lessons if item.get('week') == week_id]
            done = sum(1 for item in week_lessons if finished(item))
            lines += [f'### 第 {week_id} 周 · {week.get("title", "")}',
                      f'_{week.get("project", "")}_', '',
                      f'完成 {done} / {len(week_lessons)}', '']
            for item in week_lessons:
                record = by_id.get(item['id'], {})
                marks = []
                marks.append('已读' if record.get('read_at') else '未读')
                marks.append(f'理解题 {record.get("score", 0)}%')
                if record.get('checked'):
                    marks.append('实践检查通过')
                lines.append(f'- **{item.get("title", "")}**（{item["id"]}）— '
                             + ' · '.join(marks))
                lines.append(f'  - 交付物：{item.get("deliverable", "（未定义）")}')
            lines.append('')
        reflection = preferences.get('reflection')
        lines += ['## 项目复盘', '', reflection or '（未填写）', '',
                  '## 使用说明', '',
                  '本记录由本机课程服务自动生成，用于自评与团队复盘；它不是考试认证。',
                  '复现实验时请连同样例参数与导出数据一起提交。', '']
        return '\n'.join(lines)

    def api_teacher(self, handler, params, query, body):
        path = self.root / 'docs' / 'TEACHER.md'
        text = path.read_text(encoding='utf-8') if path.is_file() else '# 教师说明尚未生成\n'
        handler._send(200, text, 'text/markdown; charset=utf-8',
                      {'Content-Disposition': 'attachment; filename="teacher-guide.md"'})
        return None

    # ------------------------------------------------------------ 生命周期
    def start(self):
        self.server_thread = httpd.serve(self.server, self.host, self.port)
        return self.server_thread

    def stop(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:                                     # noqa: BLE001
            pass
        self.jobs.shutdown()
        self.store.close()


def _average(values):
    clean = [value for value in values if isinstance(value, (int, float))]
    return sum(clean) / len(clean) if clean else 0.0


def _export_readme(result):
    metrics = result.get('metrics', {})
    return '\n'.join([
        'RoboCON 电控实训 · 仿真导出',
        '课程作者：Connor He 和 Astra',
        '',
        '本压缩包内容：',
        '  metrics.json   本次运行的全部指标（含是否收敛）',
        '  machine.json   电机模型参数与状态量定义（含单位）',
        '  trace.csv      原始轨迹，第一列是仿真时间 t（秒）',
        '  warnings.txt   教学提示、参数告警与实验条件说明',
        '',
        '说明：曲线由本机实时求解电气—机械耦合微分方程得到，不是预存动画。',
        'diverge 为 true 时表示数值发散已停止，请先检查反馈极性再调增益。',
        '',
        '关键指标：',
        '  ' + json.dumps({key: value for key, value in metrics.items()
                           if isinstance(value, (int, float, bool, str, type(None)))},
                          ensure_ascii=False),
        '',
    ])


def _trace_csv(result):
    series = result.get('series') or []
    keys = result.get('keys') or (list(series[0].keys()) if series else [])
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(keys)
    for row in series:
        writer.writerow([_csv_value(row.get(key)) for key in keys])
    return buffer.getvalue()


def _csv_value(value):
    if isinstance(value, float):
        if not (value == value and abs(value) != float('inf')):       # NaN / Inf
            return ''
        return f'{value:.9g}'
    return '' if value is None else value


_PRESETS = [
    {'id': 'first-motor', 'title': '第一台电机：开环启动',
     'summary': '只加电压，看电流如何把转子推起来。没有控制器参与。',
     'spec': {'kind': 'dc', 'scenario': 'open_loop', 'controller': 'open', 'duration': 3.0}},
    {'id': 'mechanical-curve', 'title': '机械特性：一族电压—转速直线',
     'summary': '用解析稳态与动态仿真互相核对，理解斜率与截距的物理含义。',
     'curve': 'dc_mechanical',
     'spec': {'kind': 'dc', 'scenario': 'torque_speed', 'controller': 'open', 'duration': 0.2}},
    {'id': 'current-loop', 'title': '电流环：把电流钉在给定上',
     'summary': '堵转条件下考察电流跟踪；这是所有串级控制的内环。',
     'spec': {'kind': 'dc', 'scenario': 'current_loop', 'controller': 'current',
              'duration': 0.05, 'target': 2.0}},
    {'id': 'speed-loop', 'title': '速度闭环：阶跃与抗扰',
     'summary': '先看跟踪，再看负载阶跃后能不能稳住。',
     'spec': {'kind': 'dc', 'scenario': 'speed', 'controller': 'speed',
              'duration': 5.0, 'target': 600.0, 'load': 'step', 'load_value': 0.05}},
    {'id': 'position-loop', 'title': '位置闭环：定位与量化误差',
     'summary': '带编码器量化，观察位置环的稳态误差从哪来。',
     'spec': {'kind': 'dc', 'scenario': 'position_step', 'controller': 'position',
              'duration': 5.0, 'target': 0.5}},
    {'id': 'fault-encoder', 'title': '故障注入：编码器接反',
     'summary': '最经典也最贵的一类接线事故。先看签名，再谈排查。',
     'spec': {'kind': 'dc', 'scenario': 'fault_injection', 'controller': 'speed',
              'duration': 3.0, 'target': 600.0, 'faults': ['encoder_reversed']}},
    {'id': 'svpwm-limits', 'title': 'SVPWM：母线利用率与线性区',
     'summary': '同样的母线电压，为什么空间矢量调制能多出 15.5%？',
     'curve': 'svpwm_spectrum',
     'spec': {'kind': 'dc', 'scenario': 'speed', 'vdc': 24.0}},
    {'id': 'foc-current', 'title': 'FOC：dq 电流解耦与跟踪',
     'summary': 'PMSM 堵转下把 iq 钉住、id 清零，验证 Park 变换与解耦前馈。',
     'spec': {'kind': 'pmsm_bldc', 'scenario': 'current_loop', 'controller': 'current',
              'bridge': 'svpwm', 'duration': 0.05, 'target': 2.0}},
    {'id': 'foc-bode', 'title': '电流环频率响应：到底有多快',
     'summary': '扫频测出 −3 dB 带宽，而不是背一个公式。',
     'curve': 'current_loop_bode',
     'spec': {'kind': 'pmsm_bldc', 'scenario': 'current_loop', 'controller': 'current',
              'bridge': 'svpwm', 'sweep': {'start_hz': 20, 'stop_hz': 900, 'points': 12}}},
    {'id': 'induction-curve', 'title': '异步机：转矩—转差曲线',
     'summary': '最大转矩、启动转矩、同步转速——拖动课程的三条主线。',
     'curve': 'induction_torque_slip',
     'spec': {'kind': 'induction', 'scenario': 'slip_scan', 'controller': 'open', 'duration': 0.2}},
    {'id': 'vf-family', 'title': '恒压频比：变频调速的图像',
     'summary': '保持 V/f 恒定，机械特性整体平移。',
     'curve': 'vf_family',
     'spec': {'kind': 'induction', 'scenario': 'vf_scan', 'controller': 'open', 'duration': 0.2}},
    {'id': 'robot-path', 'title': '差速底盘：路径跟踪与里程计',
     'summary': '从两台电机到一轮运动：纯追踪 + 编码器里程计。',
     'spec': {'kind': 'dc', 'scenario': 'path_follow', 'controller': 'speed',
              'duration': 8.0, 'load': 'none',
              'robot_params': {'track': 0.4, 'wheel_radius': 0.0625, 'gear': 1.0}}},
    {'id': 'can-load', 'title': 'CAN 总线：周期与负载率',
     'summary': '报文周期越短越"实时"，但总线会被占满。',
     'curve': 'can_budget',
     'spec': {'kind': 'dc', 'scenario': 'bus_can', 'can': {'bitrate': 1000000, 'period': 0.002}}},
    {'id': 'encoder-noise', 'title': '编码器分辨率：测速台阶',
     'summary': '为什么便宜的编码器会让速度环发抖。',
     'curve': 'encoder_resolution',
     'spec': {'kind': 'dc', 'scenario': 'speed', 'control_period': 0.001}},
    {'id': 'four-bar', 'title': '四连杆机构：传动比与死点',
     'summary': 'ROBOCON 取球机构里，电机最吃力的时刻在哪。',
     'curve': 'four_bar_motion',
     'spec': {'kind': 'dc', 'scenario': 'speed', 'scenario_params': {}}},
    {'id': 'power-budget', 'title': '整机电源预算',
     'summary': '峰值决定导线与保险，平均决定电池容量。',
     'curve': 'power_budget',
     'spec': {'kind': 'dc', 'scenario': 'speed', 'scenario_params': {}}},
    {'id': 'bus-sag', 'title': '母线跌落：一动就复位',
     'summary': '大加速段端电压塌下来，转矩随之丢失。',
     'spec': {'kind': 'dc', 'scenario': 'bus_can', 'controller': 'open', 'duration': 2.0,
              'faults': ['vbus_sag'], 'bus': {'resistance': 0.08}}},
]


def build_course(state_dir=None, port=DEFAULT_PORT, host='127.0.0.1', root=ROOT):
    return Course(root=root, state_dir=state_dir, port=port, host=host)


def main(argv=None):
    parser = argparse.ArgumentParser(description='RoboCON 电控实训教室 · 本机课程服务')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT,
                        help=f'监听端口（默认 {DEFAULT_PORT}）')
    parser.add_argument('--host', default='127.0.0.1', help='监听地址（默认仅本机）')
    parser.add_argument('--state', default=None, help='学习记录目录（默认 .state/）')
    parser.add_argument('--check', action='store_true', help='只做启动自检后退出')
    args = parser.parse_args(argv)

    course = build_course(state_dir=args.state, port=args.port, host=args.host)
    try:
        curriculum = course.curriculum()
    except FileNotFoundError as error:
        print(f'[错误] {error}', file=sys.stderr)
        return 2
    lessons = curriculum.get('lessons', [])
    print(f'RoboCON 电控实训教室 · 作者 {" 和 ".join(AUTHORS)}')
    print(f'  课程：{len(curriculum.get("weeks", []))} 周 / {len(lessons)} 单元')
    print(f'  状态目录：{course.state_dir}')
    if args.check:
        print('  自检通过，未启动服务。')
        course.stop()
        return 0
    if not (ROOT / 'dist' / 'index.html').is_file():
        print(f'[错误] 找不到前端页面 {ROOT / "dist" / "index.html"}', file=sys.stderr)
        course.stop()
        return 2
    try:
        course.start()
    except OSError as error:
        print(f'[错误] 无法监听 {args.host}:{args.port} —— {error}', file=sys.stderr)
        print('        端口可能已被占用。可以换一个：./start.sh --port 8790', file=sys.stderr)
        course.stop()
        return 1
    url = f'http://{args.host}:{args.port}/'
    print(f'  已启动：{url}')
    print('  按 Ctrl+C 停止。学习记录会保留在 .state/ 里。')
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print('\n正在停止……')
    finally:
        course.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""仿真作业：纯标准库的线程池任务管理。

课程作者：Connor He 和 Astra。

为什么要有这一层：一次完整仿真（10 秒时长、0.1 ms 子步）要算十万步，
虽然只要一两秒，但会占住一个线程。如果直接在 HTTP 处理线程里算，
页面会转圈；如果放任并发，4 GiB 内存的机器会被压垮。

所以这里做三件事：
1. 限流——同时只跑 ``max_workers`` 个作业，多余的在队列里等；
2. 取消——学员改参数后可以立刻中断上一次计算；
3. 落盘——结果写到 ``.state/runs/<id>/``，服务重启不会假装还在跑。
"""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

__all__ = ['JobManager', 'JobError']

#: 单次仿真最多保留的轨迹行数（按 5 ms 采样、10 s 时长约 2000 行）。
MAX_TRACE_ROWS = 4000
#: 单次仿真允许的最长计算时间（秒）。超时如实报告，不悄悄给半截结果。
JOB_TIMEOUT_S = 60.0


class JobError(Exception):
    pass


class JobManager:
    def __init__(self, state_dir, max_workers=2):
        self.dir = Path(state_dir) / 'runs'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=max_workers,
                                           thread_name_prefix='sim')
        self.lock = threading.RLock()
        self.jobs = {}
        self._clean_stale()

    def _clean_stale(self):
        """服务重启后，把上次遗留的 running 作业如实标成 failed。"""
        for record in self.dir.glob('*/status.json'):
            try:
                status = json.loads(record.read_text())
            except (OSError, ValueError):
                continue
            if status.get('status') in ('queued', 'running'):
                status.update(status='failed',
                              error='本地服务已重启，这次计算没有完成，请重新运行。',
                              finished=time.time())
                try:
                    record.write_text(json.dumps(status, ensure_ascii=False))
                except OSError:
                    pass
                run_dir = record.parent
                result_path = run_dir / 'result.json'
                if not result_path.exists():
                    try:
                        result_path.write_text(json.dumps(
                            {'error': '本地服务已重启，结果不可用。'}, ensure_ascii=False))
                    except OSError:
                        pass

    # -------------------------------------------------------------- 提交
    def submit(self, spec, runner):
        """提交一次仿真。``runner(spec)`` 返回结果字典。"""
        run_id = uuid.uuid4().hex[:16]
        run_dir = self.dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        record = {'id': run_id, 'status': 'queued', 'created': time.time(),
                  'spec': spec, 'dir': str(run_dir)}
        self._write(run_dir, record)
        with self.lock:
            self.jobs[run_id] = record
        self.executor.submit(self._execute, run_id, spec, runner)
        return run_id

    def _execute(self, run_id, spec, runner):
        with self.lock:
            record = self.jobs.get(run_id)
            if record is None or record.get('cancelled'):
                return
            record['status'] = 'running'
            record['started'] = time.time()
        self._write(Path(record['dir']), record)
        try:
            result = runner(spec)
        except ValueError as error:
            self._finish(run_id, 'failed', error=str(error)[:400])
            return
        except Exception:                                     # noqa: BLE001
            # 计算过程里的意外错误也要如实记下来，不能吞掉
            self._finish(run_id, 'failed',
                         error='仿真计算失败：' + traceback.format_exc()[-500:])
            return
        elapsed = time.time() - record.get('started', time.time())
        if elapsed > JOB_TIMEOUT_S:
            self._finish(run_id, 'failed', error=f'计算超过 {JOB_TIMEOUT_S:.0f} 秒上限，已放弃。')
            return
        if len(result.get('series', ())) > MAX_TRACE_ROWS:
            result['series'] = result['series'][:MAX_TRACE_ROWS]
            result.setdefault('warnings', []).append(
                f'轨迹行数超过 {MAX_TRACE_ROWS} 上限，已截断显示；完整数据请导出。')
        self._finish(run_id, 'succeeded', result=result, elapsed=elapsed)

    def _finish(self, run_id, status, result=None, error=None, elapsed=None):
        with self.lock:
            record = self.jobs.get(run_id)
            if record is None or record.get('cancelled'):
                return
            record.update(status=status, finished=time.time())
            if error:
                record['error'] = error
            if elapsed is not None:
                record['elapsed'] = elapsed
            cancelled = False
        path = Path(record['dir'])
        if result is not None:
            self._write_json(path / 'result.json', result)
        else:
            self._write_json(path / 'result.json', {'error': error or '计算未完成。'})
        self._write(path, record)
        _ = cancelled

    def _write(self, run_dir, record):
        payload = {key: value for key, value in record.items() if key != 'dir'}
        self._write_json(run_dir / 'status.json', payload)

    @staticmethod
    def _write_json(path, value):
        try:
            path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
        except (OSError, ValueError):
            # 结果里混进了非有限值时，绝不写出非法 JSON：如实标记失败
            path.write_text(json.dumps({'error': '结果包含非法数值，已丢弃。'},
                                       ensure_ascii=False))

    # -------------------------------------------------------------- 查询
    def status(self, run_id):
        record = self._require(run_id)
        return {'id': run_id, 'status': record['status'],
                'error': record.get('error'),
                'elapsed': record.get('elapsed'),
                'created': record.get('created'), 'finished': record.get('finished')}

    def result(self, run_id):
        record = self._require(run_id)
        if record['status'] not in ('succeeded', 'failed'):
            raise JobError('计算还没有结束。')
        path = Path(record['dir']) / 'result.json'
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            raise JobError('结果文件不可用，请重新运行实验。')

    def lookup(self, run_id):
        """状态与结果一次取出，供前端轮询。"""
        record = self._require(run_id)
        payload = {'id': run_id, 'status': record['status'], 'error': record.get('error'),
                   'elapsed': record.get('elapsed')}
        if record['status'] in ('succeeded', 'failed'):
            payload['result'] = self.result(run_id)
        return payload

    def cancel(self, run_id):
        self._require(run_id)
        with self.lock:
            record = self.jobs[run_id]
            if record['status'] in ('queued', 'running'):
                record['cancelled'] = True
                record['status'] = 'failed'
                record['error'] = '已按你的请求停止计算。'
                record['finished'] = time.time()
        self._write(Path(record['dir']), record)
        self._write_json(Path(record['dir']) / 'result.json',
                         {'error': '已按你的请求停止计算。'})
        return True

    def _require(self, run_id):
        if not isinstance(run_id, str) or not run_id or len(run_id) > 64:
            raise JobError('实验编号不合法。')
        if any(char not in '0123456789abcdef' for char in run_id):
            raise JobError('实验编号不合法。')
        with self.lock:
            record = self.jobs.get(run_id)
        if record is None:
            # 允许跨重启读取已落盘的历史作业
            path = self.dir / run_id / 'status.json'
            if path.exists():
                try:
                    record = json.loads(path.read_text())
                except (OSError, ValueError):
                    record = None
                if record:
                    record['dir'] = str(self.dir / run_id)
                    with self.lock:
                        self.jobs[run_id] = record
        if record is None:
            raise JobError('找不到这个实验编号，请重新运行。')
        return record

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)

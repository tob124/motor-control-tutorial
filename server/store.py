"""学习进度与作品记录的本地存储。

课程作者：Connor He 和 Astra。

用标准库 ``sqlite3``，库文件放在 ``.state/progress.sqlite3``。
设计上刻意保持简单：单机单人、单写者、WAL 模式。
所有写入都在同一个连接上加锁，避免多线程服务里出现"数据库被锁"。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

__all__ = ['Store']

SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS progress(
    lesson TEXT PRIMARY KEY,
    read_at REAL,
    quiz TEXT,
    score INTEGER DEFAULT 0,
    checked INTEGER DEFAULT 0,
    checked_at REAL
);
CREATE TABLE IF NOT EXISTS attempts(
    id INTEGER PRIMARY KEY,
    lesson TEXT NOT NULL,
    created REAL NOT NULL,
    result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sim_runs(
    id TEXT PRIMARY KEY,
    created REAL NOT NULL,
    spec TEXT NOT NULL,
    status TEXT NOT NULL,
    metrics TEXT
);
'''

#: 允许通过 /api/preferences 保存的键。白名单，防止写进奇怪的东西。
PREFERENCE_KEYS = ('studentName', 'teamName', 'projectTitle', 'reflection',
                   'lastLesson', 'preferredUnits', 'notes')


class Store:
    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / 'runs').mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.dir / 'progress.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()

    # ------------------------------------------------------------ 进度
    def progress(self):
        with self.lock:
            rows = self.db.execute(
                'SELECT lesson, read_at, quiz, score, checked, checked_at FROM progress').fetchall()
            attempts = self.db.execute('SELECT COUNT(*) AS n FROM attempts').fetchone()['n']
            preferences = {row['key']: row['value']
                           for row in self.db.execute('SELECT key, value FROM preferences')}
        lessons = []
        for row in rows:
            try:
                quiz = json.loads(row['quiz']) if row['quiz'] else {}
            except (TypeError, ValueError):
                quiz = {}
            lessons.append({'lesson': row['lesson'], 'read_at': row['read_at'],
                            'quiz': quiz, 'score': row['score'],
                            'checked': bool(row['checked']), 'checked_at': row['checked_at']})
        return {'lessons': lessons, 'attempts': attempts, 'preferences': preferences}

    def record_read(self, lesson, read_at=None):
        with self.lock:
            self.db.execute(
                'INSERT INTO progress(lesson, read_at) VALUES(?, ?) '
                'ON CONFLICT(lesson) DO UPDATE SET read_at=excluded.read_at',
                (lesson, time.time() if read_at is None else read_at))

    def record_quiz(self, lesson, quiz, score):
        with self.lock:
            self.db.execute(
                'INSERT INTO progress(lesson, quiz, score) VALUES(?, ?, ?) '
                'ON CONFLICT(lesson) DO UPDATE SET quiz=excluded.quiz, score=excluded.score',
                (lesson, json.dumps(quiz, ensure_ascii=False), int(score)))

    def record_check(self, lesson, passed, result):
        """记录一次任务检查。``result`` 会被截断后存进 attempts 表。"""
        payload = json.dumps(result, ensure_ascii=False)
        if len(payload) > 20000:
            payload = payload[:20000] + '…'
        with self.lock:
            self.db.execute('INSERT INTO attempts(lesson, created, result) VALUES(?, ?, ?)',
                            (lesson, time.time(), payload))
            if passed:
                self.db.execute(
                    'INSERT INTO progress(lesson, checked, checked_at) VALUES(?, 1, ?) '
                    'ON CONFLICT(lesson) DO UPDATE SET checked=1, checked_at=excluded.checked_at',
                    (lesson, time.time()))
        return passed

    def attempts_for(self, lesson, limit=10):
        with self.lock:
            rows = self.db.execute(
                'SELECT created, result FROM attempts WHERE lesson=? ORDER BY id DESC LIMIT ?',
                (lesson, limit)).fetchall()
        out = []
        for row in rows:
            try:
                parsed = json.loads(row['result'])
            except (TypeError, ValueError):
                parsed = {'raw': row['result']}
            out.append({'created': row['created'], 'result': parsed})
        return out

    # ------------------------------------------------------------ 偏好
    def save_preferences(self, values):
        if not isinstance(values, dict):
            raise ValueError('偏好设置必须是对象。')
        unknown = sorted(set(values) - set(PREFERENCE_KEYS))
        if unknown:
            raise ValueError(f'未登记的偏好字段：{"、".join(unknown)}。')
        with self.lock:
            for key, value in values.items():
                if value is None:
                    self.db.execute('DELETE FROM preferences WHERE key=?', (key,))
                    continue
                text = str(value)
                if len(text) > 20000:
                    raise ValueError(f'{key} 内容过长（上限 20000 字符）。')
                self.db.execute(
                    'INSERT INTO preferences(key, value) VALUES(?, ?) '
                    'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, text))

    def preference(self, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT value FROM preferences WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default

    # ------------------------------------------------------------ 仿真作业
    def create_run(self, run_id, spec):
        with self.lock:
            self.db.execute('INSERT INTO sim_runs(id, created, spec, status) VALUES(?, ?, ?, ?)',
                            (run_id, time.time(), json.dumps(spec, ensure_ascii=False), 'queued'))

    def finish_run(self, run_id, status, metrics=None):
        with self.lock:
            self.db.execute('UPDATE sim_runs SET status=?, metrics=? WHERE id=?',
                            (status, json.dumps(metrics or {}, ensure_ascii=False), run_id))

    def recent_runs(self, limit=20):
        with self.lock:
            rows = self.db.execute(
                'SELECT id, created, status, metrics FROM sim_runs ORDER BY created DESC LIMIT ?',
                (limit,)).fetchall()
        out = []
        for row in rows:
            try:
                metrics = json.loads(row['metrics']) if row['metrics'] else {}
            except (TypeError, ValueError):
                metrics = {}
            out.append({'id': row['id'], 'created': row['created'],
                        'status': row['status'], 'metrics': metrics})
        return out

    def close(self):
        with self.lock:
            self.db.commit()
            self.db.close()

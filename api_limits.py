"""Persistent request budgets. Limits count accepted attempts, including upstream failures."""
import os
import sqlite3
import threading
import time
from pathlib import Path
from contextlib import closing


class LimitExceeded(Exception):
    def __init__(self, message, retry_after=60):
        super().__init__(message)
        self.retry_after = retry_after


class RequestLimits:
    def __init__(self, path=None, clock=time.time):
        self.path = path or os.environ.get('LIMITS_DB_PATH', '.state/request_limits.sqlite3')
        self.clock = clock
        self.lock = threading.Lock()
        self.active = {'chat': 0, 'image': 0}
        self.clients = {}
        self.minute = {'chat': self.setting('CHAT_PER_MINUTE', 12), 'image': self.setting('IMAGE_PER_MINUTE', 4)}
        self.daily = {'chat': self.setting('CHAT_DAILY_LIMIT', 600), 'image': self.setting('IMAGE_DAILY_LIMIT', 120)}
        self.concurrent = {'chat': self.setting('CHAT_CONCURRENCY', 8), 'image': self.setting('IMAGE_CONCURRENCY', 3)}
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS usage (bucket TEXT, subject TEXT, kind TEXT, count INTEGER NOT NULL, PRIMARY KEY(bucket,subject,kind))')

    @staticmethod
    def setting(name, default):
        value = int(os.environ.get(name, default))
        if value < 1:
            raise ValueError(name + ' must be positive')
        return value

    def acquire(self, kind, client):
        with self.lock:
            if self.active[kind] >= self.concurrent[kind] or self.clients.get(client, 0) >= 2:
                raise LimitExceeded('当前请求较多，请稍后重试。', 10)
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                now = int(self.clock())
                minute, day = 'm:' + str(now // 60), 'd:' + str(now // 86400)
                checks = [(minute, client, self.minute[kind], 60 - now % 60),
                          (day, '*', self.daily[kind], 86400 - now % 86400)]
                for bucket, subject, limit, retry in checks:
                    row = conn.execute('SELECT count FROM usage WHERE bucket=? AND subject=? AND kind=?', (bucket, subject, kind)).fetchone()
                    if row and row[0] >= limit:
                        message = '操作有些频繁，请稍后重试。' if bucket == minute else '今日服务额度已用完，进度已保留，请明日继续。'
                        raise LimitExceeded(message, retry)
                conn.execute("DELETE FROM usage WHERE (bucket LIKE 'm:%' AND CAST(substr(bucket,3) AS INTEGER) < ?) OR (bucket LIKE 'd:%' AND CAST(substr(bucket,3) AS INTEGER) < ?)", (now // 60 - 1, now // 86400 - 1))
                for bucket, subject, _, _ in checks:
                    conn.execute('INSERT INTO usage VALUES (?,?,?,1) ON CONFLICT(bucket,subject,kind) DO UPDATE SET count=count+1', (bucket, subject, kind))
            self.active[kind] += 1
            self.clients[client] = self.clients.get(client, 0) + 1

    def release(self, kind, client):
        with self.lock:
            self.active[kind] = max(0, self.active[kind] - 1)
            remaining = self.clients.get(client, 0) - 1
            if remaining > 0:
                self.clients[client] = remaining
            else:
                self.clients.pop(client, None)

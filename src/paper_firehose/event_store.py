"""Bounded first-party activity events and durable daily totals."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

EVENT_TYPES = {'page_view', 'page_leave', 'click', 'filter_change', 'search'}
PAGES = {'welcome', 'overview', 'discover', 'venues', 'topics', 'library', 'docs', 'paper', 'account'}


class EventStore:
    def __init__(self, path: Path, retention_days=90, daily_limit=100000):
        self.path = Path(path)
        self.retention_days = retention_days
        self.daily_limit = daily_limit
        self._maintenance_lock = threading.Lock()
        self._last_cleanup = 0
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self._connect()) as conn, conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA auto_vacuum=INCREMENTAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, received_at REAL NOT NULL, day TEXT NOT NULL,
                    user_id TEXT, visitor_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    type TEXT NOT NULL, page TEXT NOT NULL, paper_id TEXT NOT NULL,
                    target TEXT NOT NULL, details TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_received ON events(received_at);
                CREATE INDEX IF NOT EXISTS events_user ON events(user_id,received_at);
                CREATE TABLE IF NOT EXISTS daily_counts (
                    day TEXT NOT NULL, type TEXT NOT NULL, page TEXT NOT NULL,
                    count INTEGER NOT NULL, PRIMARY KEY(day,type,page));
                CREATE TABLE IF NOT EXISTS daily_unique (
                    day TEXT NOT NULL, kind TEXT NOT NULL, identifier TEXT NOT NULL,
                    PRIMARY KEY(day,kind,identifier));
                CREATE TABLE IF NOT EXISTS daily_visitors (
                    day TEXT PRIMARY KEY, visitors INTEGER NOT NULL DEFAULT 0,
                    users INTEGER NOT NULL DEFAULT 0, sessions INTEGER NOT NULL DEFAULT 0);
            ''')
        self.path.chmod(0o600)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, items: list[dict], visitor_id: str, user_id: str | None):
        now = time.time()
        day = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).date().isoformat()
        inserted = 0
        with closing(self._connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            total = conn.execute('SELECT coalesce(sum(count),0) FROM daily_counts WHERE day=?', (day,)).fetchone()[0]
            identifiers = {item['id'] for item in items}
            existing = {row[0] for row in conn.execute('SELECT id FROM events WHERE id IN (' + ','.join('?' for _ in identifiers) + ')', list(identifiers))}
            if total + len(identifiers - existing) > self.daily_limit:
                return None
            for item in items:
                changed = conn.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)', (
                    item['id'], now, day, user_id, visitor_id, item['session_id'], item['type'],
                    item['page'], item.get('paper_id', ''), item.get('target', ''),
                    json.dumps(item.get('details', {}), separators=(',', ':')))).rowcount
                if not changed:
                    continue
                inserted += 1
                conn.execute('INSERT INTO daily_counts VALUES (?,?,?,1) ON CONFLICT(day,type,page) DO UPDATE SET count=count+1',
                             (day, item['type'], item['page']))
                conn.execute('INSERT OR IGNORE INTO daily_visitors(day) VALUES (?)', (day,))
                identities = [('visitors', visitor_id), ('sessions', visitor_id + ':' + item['session_id'])]
                if user_id:
                    identities.append(('users', user_id))
                for kind, identifier in identities:
                    if conn.execute('INSERT OR IGNORE INTO daily_unique VALUES (?,?,?)', (day, kind, identifier)).rowcount:
                        conn.execute(f'UPDATE daily_visitors SET {kind}={kind}+1 WHERE day=?', (day,))
        self.maintain()
        return inserted

    def maintain(self):
        # Deletion happens in small batches; freed pages are reusable by new events.
        if time.time() - self._last_cleanup < 3600:
            return
        with self._maintenance_lock:
            if time.time() - self._last_cleanup < 3600:
                return
            cutoff = time.time() - self.retention_days * 86400
            day = datetime.fromtimestamp(cutoff, ZoneInfo('Asia/Shanghai')).date().isoformat()
            with closing(self._connect()) as conn, conn:
                conn.execute('DELETE FROM events WHERE rowid IN (SELECT rowid FROM events WHERE received_at<? LIMIT 10000)', (cutoff,))
                conn.execute('DELETE FROM daily_unique WHERE rowid IN (SELECT rowid FROM daily_unique WHERE day<? LIMIT 10000)', (day,))
                conn.execute('PRAGMA incremental_vacuum(256)')
            self._last_cleanup = time.time()

    def report(self, days=30):
        since = (datetime.now(ZoneInfo('Asia/Shanghai')).date() - timedelta(days=days - 1)).isoformat()
        with closing(self._connect()) as conn:
            daily = [dict(row) for row in conn.execute('SELECT * FROM daily_visitors WHERE day>=? ORDER BY day', (since,))]
            actions = [dict(row) for row in conn.execute('SELECT day,type,page,count FROM daily_counts WHERE day>=? ORDER BY day,type,page', (since,))]
        return {'timezone': 'Asia/Shanghai', 'retention_days': self.retention_days, 'daily': daily, 'actions': actions}

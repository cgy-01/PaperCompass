"""Mutable accounts and collections, separate from all paper databases."""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class UserStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, github_id INTEGER NOT NULL UNIQUE,
                    login TEXT NOT NULL, name TEXT NOT NULL, avatar_url TEXT NOT NULL,
                    created_at REAL NOT NULL, last_login_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state_hash TEXT PRIMARY KEY, binding_hash TEXT NOT NULL,
                    verifier TEXT NOT NULL, return_to TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS favorites (
                    user_id TEXT NOT NULL REFERENCES users(id), paper_id TEXT NOT NULL,
                    created_at REAL NOT NULL, PRIMARY KEY(user_id,paper_id));
                CREATE TABLE IF NOT EXISTS reading (
                    user_id TEXT NOT NULL REFERENCES users(id), paper_id TEXT NOT NULL,
                    created_at REAL NOT NULL, PRIMARY KEY(user_id,paper_id));
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def begin_login(self, return_to: str):
        state, binding, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        now = time.time()
        with self.connect() as conn:
            conn.execute('DELETE FROM oauth_states WHERE expires_at<=?', (now,))
            conn.execute('DELETE FROM sessions WHERE expires_at<=?', (now,))
            conn.execute('INSERT INTO oauth_states VALUES (?,?,?,?,?)',
                         (digest(state), digest(binding), verifier, return_to, now + 600))
        return state, binding, verifier

    def consume_login(self, state: str, binding: str):
        with self.connect() as conn:
            # Serialize consumption so the same code/state cannot be redeemed twice.
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM oauth_states WHERE state_hash=?', (digest(state),)).fetchone()
            if not row or row['expires_at'] <= time.time() or not secrets.compare_digest(row['binding_hash'], digest(binding)):
                return None
            conn.execute('DELETE FROM oauth_states WHERE state_hash=?', (digest(state),))
            return dict(row)

    def login(self, profile: dict):
        now, identifier = time.time(), uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute('''INSERT INTO users VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(github_id) DO UPDATE SET login=excluded.login,name=excluded.name,
                avatar_url=excluded.avatar_url,last_login_at=excluded.last_login_at''',
                (identifier, profile['id'], profile['login'], profile.get('name') or profile['login'],
                 profile.get('avatar_url') or '', now, now))
            user_id = conn.execute('SELECT id FROM users WHERE github_id=?', (profile['id'],)).fetchone()[0]
            token = secrets.token_urlsafe(32)
            conn.execute('INSERT INTO sessions VALUES (?,?,?)', (digest(token), user_id, now + 30 * 86400))
        return token

    def user(self, token: str):
        if not token or len(token) > 128:
            return None
        with self.connect() as conn:
            row = conn.execute('''SELECT u.id,u.login,u.name,u.avatar_url FROM users u
                JOIN sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>?''',
                (digest(token), time.time())).fetchone()
            return dict(row) if row else None

    def logout(self, token: str):
        with self.connect() as conn:
            conn.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))

    def backup(self, destination: Path):
        """Create a consistent private account backup, including committed WAL data."""
        destination = Path(destination)
        if destination.resolve() == self.path.resolve() or destination.exists():
            raise ValueError('Backup destination must be a new file')
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with self.connect() as source:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target)
                finally:
                    target.close()
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def list_ids(self, user_id: str, kind: str, page=1):
        if kind not in ('favorites', 'reading'):
            raise ValueError('Unknown collection')
        with self.connect() as conn:
            total = conn.execute(f'SELECT count(*) FROM {kind} WHERE user_id=?', (user_id,)).fetchone()[0]
            rows = conn.execute(f'SELECT paper_id FROM {kind} WHERE user_id=? ORDER BY created_at DESC,paper_id LIMIT 250 OFFSET ?',
                                (user_id, (page - 1) * 250))
            return {'ids': [row[0] for row in rows], 'total': total, 'page': page, 'pages': max(1, (total + 249) // 250)}

    def set_ids(self, user_id: str, kind: str, ids: list[str], enabled=True):
        if kind not in ('favorites', 'reading'):
            raise ValueError('Unknown collection')
        with self.connect() as conn:
            if enabled:
                conn.executemany(f'INSERT OR IGNORE INTO {kind} VALUES (?,?,?)',
                                 [(user_id, identifier, time.time()) for identifier in ids])
            else:
                conn.executemany(f'DELETE FROM {kind} WHERE user_id=? AND paper_id=?',
                                 [(user_id, identifier) for identifier in ids])

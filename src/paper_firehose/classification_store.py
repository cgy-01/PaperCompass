"""SQLite jobs, accepted assignments and a per-request classification cost ledger."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo

from .core.paths import resolve_data_path

BEIJING = ZoneInfo('Asia/Shanghai')
TERMINAL = {'classified', 'out_of_scope', 'needs_review', 'waiting_metadata', 'failed', 'invalid_input'}


def now():
    return datetime.now(timezone.utc)


def next_day():
    return (now().astimezone(BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)).astimezone(timezone.utc).isoformat()


def off_peak_resume_at(stamp, *, margin_seconds=0):
    """Return the next safe start, or None if a request may start now.

    DeepSeek's weekday peaks are 09–12 and 14–18 in Beijing. Weekends
    are off-peak. We conservatively keep weekday peaks on public holidays:
    missing a holiday discount is preferable to assuming an expensive slot
    is discounted. No holiday service or additional dependency is needed.
    """
    local = stamp.astimezone(BEIJING)
    finish = local + timedelta(seconds=margin_seconds)
    for offset in range(8):
        day = local.date() + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for start_hour, end_hour in ((9, 12), (14, 18)):
            start = datetime.combine(day, time(start_hour), BEIJING)
            end = datetime.combine(day, time(end_hour), BEIJING)
            if local >= end:
                continue
            if finish < start:
                return None
            return end.astimezone(timezone.utc)
    raise RuntimeError('Unable to determine the next classification window')


@dataclass(frozen=True)
class Limits:
    daily_micro: int
    daily_requests: int
    daily_tokens: int
    input_price: Decimal
    cached_price: Decimal
    output_price: Decimal

    @classmethod
    def from_env(cls):
        try:
            budget = Decimal(os.getenv('PAPER_COMPASS_CLASSIFICATION_DAILY_BUDGET_CNY', '3'))
            prices = [Decimal(os.getenv('PAPER_COMPASS_CLASSIFICATION_PRICE_' + key, default))
                      for key, default in [('INPUT', '1'), ('CACHED_INPUT', '0.02'), ('OUTPUT', '4')]]
            requests = int(os.getenv('PAPER_COMPASS_CLASSIFICATION_DAILY_REQUEST_LIMIT', '-1'))
            tokens = int(os.getenv('PAPER_COMPASS_CLASSIFICATION_DAILY_TOKEN_LIMIT', '-1'))
            if (any(not p.is_finite() or p < 0 for p in [budget, *prices]) or requests < -1 or tokens < -1
                    or prices[1] > prices[0] or prices[0] == 0 or prices[2] == 0):
                raise ValueError
            return cls(int(budget * 1000000), requests, tokens, *prices)
        except (ValueError, InvalidOperation, OverflowError):
            raise ValueError('Invalid classification budget or token prices') from None

    def fee(self, input_tokens, output_tokens, cached_tokens=0):
        # Prices are CNY/million tokens, hence tokens*price is micro-CNY.
        return int(((input_tokens - cached_tokens) * self.input_price
                    + cached_tokens * self.cached_price + output_tokens * self.output_price)
                   .to_integral_value(rounding=ROUND_CEILING))


class ResultStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else resolve_data_path('classification', 'api.db', ensure_parent=True)
        self.conn = sqlite3.connect(self.path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name IN "
                             "('matched_entries','entries','feed_entries','papers')").fetchone():
            self.conn.close()
            raise ValueError('Refusing to use a paper database as the API classification cache')
        self.conn.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA cache_size=-4096;
            CREATE TABLE IF NOT EXISTS predictions (
                cache_key TEXT PRIMARY KEY, result TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
                paper_id TEXT PRIMARY KEY, text_hash TEXT NOT NULL, cache_key TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', last_attempt TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt TEXT NOT NULL DEFAULT '',
                lease_until TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS assignments (
                paper_id TEXT PRIMARY KEY, text_hash TEXT NOT NULL, cache_key TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tracked_papers (
                paper_id TEXT PRIMARY KEY,scope TEXT NOT NULL,discovered_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT OR IGNORE INTO state VALUES ('revision','0');
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY,day TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,
                status TEXT NOT NULL,policy TEXT NOT NULL,model TEXT NOT NULL,
                reserved_micro INTEGER NOT NULL,charged_micro INTEGER NOT NULL,
                reserved_tokens INTEGER NOT NULL,charged_tokens INTEGER NOT NULL,
                usage TEXT,prices TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS requests_day ON requests(day);
            CREATE TEMP TABLE worker_seen (paper_id TEXT PRIMARY KEY);
        ''')
        columns = {row[1] for row in self.conn.execute('PRAGMA table_info(jobs)')}
        for key, declaration in [('last_attempt', "TEXT NOT NULL DEFAULT ''"),
                                 ('attempts', 'INTEGER NOT NULL DEFAULT 0'),
                                 ('next_attempt', "TEXT NOT NULL DEFAULT ''"),
                                 ('lease_until', "TEXT NOT NULL DEFAULT ''")]:
            if key not in columns:
                self.conn.execute('ALTER TABLE jobs ADD COLUMN ' + key + ' ' + declaration)
        self.conn.execute('CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status,next_attempt)')
        # Migrate only our sidecar, never the raw or published paper databases.
        for row in self.conn.execute("SELECT jobs.*,predictions.result FROM jobs JOIN predictions USING(cache_key) WHERE status='done'").fetchall():
            result = json.loads(row['result'])
            status = 'needs_review' if result.get('needs_review') else 'classified'
            self.conn.execute('UPDATE jobs SET status=? WHERE paper_id=?', (status, row['paper_id']))
            if status == 'classified':
                self._assign(row['paper_id'], row['text_hash'], row['cache_key'])
        self.conn.commit()

    def close(self):
        self.conn.close()

    def cached(self, key):
        row = self.conn.execute('SELECT result FROM predictions WHERE cache_key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, key, result):
        self.conn.execute('INSERT OR REPLACE INTO predictions VALUES (?,?,?)',
                          (key, json.dumps(result, ensure_ascii=False), now().isoformat()))
        self.conn.commit()

    def enqueue(self, identifier, paper, settings):
        from .api_classification import digest
        text_hash, key = digest(paper), settings.cache_key(paper)
        old = self.conn.execute('SELECT * FROM jobs WHERE paper_id=?', (identifier,)).fetchone()
        if old and old['text_hash'] == text_hash and old['cache_key'].startswith('manual:'):
            return False
        if not old or old['text_hash'] != text_hash or old['cache_key'] != key:
            self.conn.execute('INSERT OR REPLACE INTO jobs '
                              '(paper_id,text_hash,cache_key,status) VALUES (?,?,?,?)',
                              (identifier, text_hash, key, 'pending'))
            return True
        if old['status'] in {'classified', 'out_of_scope'} and not self.cached(key):
            self.conn.execute("UPDATE jobs SET status='pending',attempts=0,next_attempt='' WHERE paper_id=?", (identifier,))
            return True
        return False

    def _assign(self, identifier, text_hash, key):
        old = self.conn.execute('SELECT text_hash,cache_key FROM assignments WHERE paper_id=?', (identifier,)).fetchone()
        if old and tuple(old) == (text_hash, key):
            return
        self.conn.execute('INSERT OR REPLACE INTO assignments VALUES (?,?,?)', (identifier, text_hash, key))
        self.conn.execute("UPDATE state SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

    def complete(self, identifier, expected_key, result):
        job = self.conn.execute('SELECT * FROM jobs WHERE paper_id=?', (identifier,)).fetchone()
        if not job or job['cache_key'] != expected_key:
            return False  # A producer queued newer text while this request ran.
        decision = result.get('decision', 'needs_review' if result['needs_review'] else 'classified')
        self.conn.execute("UPDATE jobs SET status=?,error='',next_attempt='',lease_until='' WHERE paper_id=?",
                          (decision, identifier))
        if decision in {'classified', 'out_of_scope'}:
            self._assign(identifier, job['text_hash'], expected_key)
        self.conn.commit()
        return True

    def due(self, limit, *, include_history=False):
        stamp = now().isoformat()
        self.conn.execute("UPDATE jobs SET status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'retry_wait' END,"
                          "error='Worker lease expired',lease_until='',next_attempt=? "
                          "WHERE status='running' AND lease_until<=?", (stamp, stamp))
        self.conn.commit()
        scope = '' if include_history else "AND paper_id IN (SELECT paper_id FROM tracked_papers WHERE scope='new') "
        return self.conn.execute("SELECT * FROM jobs WHERE status IN ('pending','retry_wait') "
                                 "AND next_attempt<=? AND paper_id NOT IN (SELECT paper_id FROM worker_seen) "
                                 + scope + "ORDER BY next_attempt,last_attempt,paper_id LIMIT ?",
                                 (stamp, limit)).fetchall()

    def claim(self, identifier, expected_key, timeout):
        stamp = now()
        changed = self.conn.execute("UPDATE jobs SET status='running',attempts=attempts+1,last_attempt=?,lease_until=? "
                                    "WHERE paper_id=? AND cache_key=? AND status IN ('pending','retry_wait')",
                                    (stamp.isoformat(), (stamp + timedelta(seconds=timeout + 60)).isoformat(),
                                     identifier, expected_key)).rowcount
        self.conn.commit()
        return bool(changed)

    def defer(self, identifier, expected_key, message, *, category='retry', resume_at=None):
        job = self.conn.execute('SELECT attempts FROM jobs WHERE paper_id=? AND cache_key=?', (identifier, expected_key)).fetchone()
        if not job:
            return
        attempts = job['attempts']
        status, retry = 'retry_wait', (now() + timedelta(seconds=min(3600, 300 * 2 ** max(0, attempts - 1)))).isoformat()
        if category in {'waiting_metadata', 'invalid_input'}:
            status, retry = category, ''
        elif category == 'budget':
            status, retry, attempts = 'pending', next_day(), max(0, attempts - 1)
        elif category == 'window':
            if not resume_at:
                raise ValueError('An off-peak pause requires a resume time')
            status, retry, attempts = 'pending', resume_at, max(0, attempts - 1)
        elif category == 'circuit':
            retry, attempts = (now() + timedelta(hours=1)).isoformat(), max(0, attempts - 1)
        elif attempts >= 3:
            status, retry = 'failed', ''
        self.conn.execute('UPDATE jobs SET status=?,error=?,next_attempt=?,attempts=?,lease_until=\'\' '
                          'WHERE paper_id=? AND cache_key=?', (status, message, retry, attempts, identifier, expected_key))
        self.conn.commit()

    def trip(self, seconds=900):
        until = (now() + timedelta(seconds=seconds)).isoformat()
        self.conn.execute("INSERT OR REPLACE INTO state VALUES ('circuit_until',?)", (until,))
        self.conn.commit()

    def reserve(self, settings, paper, input_bound):
        from .api_classification import BudgetExceeded, OffPeakPause
        limits = Limits.from_env()
        amount = limits.fee(input_bound, settings.max_tokens)
        tokens = input_bound + settings.max_tokens
        try:
            self.conn.execute('BEGIN IMMEDIATE')
            # Read the clock after acquiring the write lock, not before a
            # possible SQLite wait. Leave time for connect/read timeout plus
            # a small margin instead of starting on the price boundary.
            stamp = now()
            resume = off_peak_resume_at(stamp, margin_seconds=settings.timeout + 15)
            if resume:
                raise OffPeakPause(resume)
            day = stamp.astimezone(BEIJING).date().isoformat()
            circuit = self.conn.execute("SELECT value FROM state WHERE key='circuit_until'").fetchone()
            if circuit and circuit[0] > stamp.isoformat():
                raise BudgetExceeded('Classification circuit is open; no API request made')
            totals = self.conn.execute('SELECT count(*),COALESCE(sum(charged_micro),0),COALESCE(sum(charged_tokens),0) '
                                       'FROM requests WHERE day=?', (day,)).fetchone()
            if ((limits.daily_requests >= 0 and totals[0] >= limits.daily_requests)
                    or totals[1] + amount > limits.daily_micro
                    or (limits.daily_tokens >= 0 and totals[2] + tokens > limits.daily_tokens)):
                raise BudgetExceeded('Daily classification budget/request/token limit reached; no API request made')
            identifier = uuid.uuid4().hex
            prices = json.dumps([str(limits.input_price), str(limits.cached_price), str(limits.output_price)])
            self.conn.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                              (identifier, day, stamp.isoformat(), None, 'reserved', settings.policy, settings.model,
                               amount, amount, tokens, tokens, None, prices))
            self.conn.commit()
            return identifier
        except Exception:
            self.conn.rollback()
            raise

    def finish_request(self, identifier, outcome, usage):
        row = self.conn.execute('SELECT * FROM requests WHERE id=?', (identifier,)).fetchone()
        charge, tokens = row['reserved_micro'], row['reserved_tokens']
        known = usage and all(type(usage.get(k)) is int for k in ('prompt_tokens', 'completion_tokens'))
        if known:
            input_tokens, output_tokens = usage['prompt_tokens'], usage['completion_tokens']
            hit = usage.get('prompt_cache_hit_tokens', 0)
            if 0 <= hit <= input_tokens and input_tokens >= 0 and output_tokens >= 0:
                rates = [Decimal(p) for p in json.loads(row['prices'])]
                limits = Limits(0, 0, 0, *rates)
                charge, tokens = limits.fee(input_tokens, output_tokens, hit), input_tokens + output_tokens
        self.conn.execute('UPDATE requests SET status=?,finished_at=?,charged_micro=?,charged_tokens=?,usage=? WHERE id=?',
                          (outcome, now().isoformat(), charge, tokens, json.dumps(usage) if usage else None, identifier))
        self.conn.commit()
        if charge > row['reserved_micro'] or tokens > row['reserved_tokens']:
            self.trip(3600)

    def status(self):
        day = now().astimezone(BEIJING).date().isoformat()
        counts = dict(self.conn.execute('SELECT status,count(*) FROM jobs GROUP BY status').fetchall())
        totals = self.conn.execute('SELECT count(*),COALESCE(sum(charged_micro),0),COALESCE(sum(charged_tokens),0),'
                                   "sum(CASE WHEN usage IS NULL THEN 1 ELSE 0 END) FROM requests WHERE day=?", (day,)).fetchone()
        circuit = self.conn.execute("SELECT value FROM state WHERE key='circuit_until'").fetchone()
        scope = self.conn.execute("SELECT value FROM state WHERE key='new_only_scope'").fetchone()
        new_pending = self.conn.execute("SELECT count(*) FROM jobs JOIN tracked_papers USING(paper_id) "
                                        "WHERE scope='new' AND status IN ('pending','retry_wait','running')").fetchone()[0]
        return {'jobs': counts, 'day': day, 'requests': totals[0], 'estimated_cny': totals[1] / 1000000,
                'accounted_tokens': totals[2], 'unknown_usage_requests': totals[3] or 0,
                'circuit_until': circuit[0] if circuit else None,
                'new_only_scope': json.loads(scope[0]) if scope else None, 'pending_new': new_pending,
                'assignment_revision': int(self.conn.execute("SELECT value FROM state WHERE key='revision'").fetchone()[0])}


class AssignmentReader:
    """Pin one sidecar read transaction, never issue a request or migrate a DB."""
    def __init__(self, path=None):
        path = Path(path) if path else resolve_data_path('classification', 'api.db')
        self.conn, self.revision, self.has_scope = None, 0, False
        if path.is_file():
            self.conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
            self.conn.execute('PRAGMA cache_size=-2048')
            self.conn.execute('BEGIN')
            modern = self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='assignments'").fetchone()
            self.modern = bool(modern)
            self.has_scope = bool(modern and self.conn.execute("SELECT 1 FROM state WHERE key='new_only_scope'").fetchone())
            if modern:
                self.revision = int(self.conn.execute("SELECT value FROM state WHERE key='revision'").fetchone()[0])

    def close(self):
        if self.conn:
            self.conn.close()

    def is_historical(self, identifier):
        return bool(self.conn and self.has_scope and self.conn.execute(
            "SELECT 1 FROM tracked_papers WHERE paper_id=? AND scope='historical'", (identifier,)).fetchone())

    def get(self, row):
        from .api_classification import digest, paper_text, validate_stored
        if self.conn is None:
            return None
        table = 'assignments' if self.modern else 'jobs'
        condition = '' if self.modern else " AND status='done'"
        found = self.conn.execute(f'SELECT {table}.text_hash,predictions.result FROM {table} '
                                  f'JOIN predictions USING(cache_key) WHERE paper_id=?' + condition,
                                  (row['entry_id'],)).fetchone()
        paper = paper_text(dict(row))
        if not found or found[0] != digest(paper):
            return None
        result = validate_stored(json.loads(found[1]), paper)
        return result if result['decision'] != 'needs_review' else None

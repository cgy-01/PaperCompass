"""Immutable, versioned SQLite read models derived from a read-only source snapshot."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import tempfile
import time
import uuid
import threading
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .commands import dashboard
from .core.config import ConfigManager
from .core.paths import resolve_data_file, resolve_data_path

LOGGER = logging.getLogger(__name__)
FORMAT_VERSION = 1


def public_id(identifier):
    """Stable URL identity, independent of publication versions and raw ID format."""
    return hashlib.sha256(('paper-compass:' + identifier).encode('utf-8')).hexdigest()


def present_paper(paper, *, compact=False):
    result = {**paper, 'publicId': public_id(paper['id'])}
    if compact:
        result['summaryPreview'] = result.pop('summary', '')[:320]
        result.pop('insight', None)
    return result


def read_connection(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def current_path():
    root = resolve_data_path('query-store')
    manifest = json.loads((root / 'current.json').read_text(encoding='utf-8'))
    name = manifest['database']
    if not isinstance(name, str) or Path(name).name != name or not name.startswith('query-'):
        raise ValueError('Invalid published query database path')
    path = root / name
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _timestamp(paper):
    try:
        return datetime.fromisoformat(paper['published'].replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0.0


class _Snapshot:
    def __init__(self, connection):
        self.connection = connection

    def get_connection(self, _):
        # dashboard's converter only needs a connection context, not a DatabaseManager.
        return self.connection


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def build(config_path, *, updated_at=None):
    """Copy the last read model, update changed rows, validate, then publish.

    Raw databases are opened with mode=ro and never initialized or migrated here.
    A failed build cannot change the published pointer.
    """
    import fcntl
    root = resolve_data_path('query-store')
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'build.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('A query database build is already running') from exc
        manager = ConfigManager(config_path)
        if not manager.validate_config():
            raise ValueError('Configuration validation failed')
        catalog = dashboard._topic_catalog(manager)
        signature = hashlib.sha256(json.dumps({
            'version': FORMAT_VERSION, 'catalog': catalog,
            'journals': dashboard.CORE_JOURNAL_PATTERNS,
        }, sort_keys=True).encode()).hexdigest()
        source = resolve_data_file(manager.load_config()['database']['history_path'])
        name = 'query-' + uuid.uuid4().hex + '.db'
        destination = root / name
        published = False
        try:
            with tempfile.TemporaryDirectory(prefix='build-', dir=root) as scratch:
                snapshot_path = Path(scratch) / 'source.db'
                with closing(read_connection(source)) as raw, closing(sqlite3.connect(snapshot_path)) as snapshot:
                    raw.backup(snapshot)
                candidate = Path(scratch) / 'candidate.db'
                try:
                    previous = current_path()
                except FileNotFoundError:
                    previous = None
                if previous:
                    with closing(read_connection(previous)) as old, closing(sqlite3.connect(candidate)) as new:
                        old.backup(new)
                with closing(sqlite3.connect(candidate)) as conn, closing(read_connection(snapshot_path)) as snapshot:
                    conn.executescript('''
                        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS fingerprints (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS papers (
                            id TEXT PRIMARY KEY, payload TEXT NOT NULL, search TEXT NOT NULL,
                            timestamp REAL NOT NULL, score REAL NOT NULL, title TEXT NOT NULL,
                            source TEXT NOT NULL, venue_type TEXT NOT NULL, theme TEXT NOT NULL,
                            complete INTEGER NOT NULL, year INTEGER, base_date TEXT NOT NULL,
                            source_rowid INTEGER NOT NULL);
                        CREATE TABLE IF NOT EXISTS paper_topics (
                            paper_id TEXT NOT NULL, topic TEXT NOT NULL, PRIMARY KEY(paper_id,topic));
                        CREATE INDEX IF NOT EXISTS papers_date ON papers(timestamp DESC, score DESC);
                        CREATE INDEX IF NOT EXISTS papers_source ON papers(source);
                        CREATE INDEX IF NOT EXISTS papers_type ON papers(venue_type);
                        CREATE INDEX IF NOT EXISTS papers_title ON papers(title);
                        CREATE INDEX IF NOT EXISTS topics_name ON paper_topics(topic,paper_id);
                        CREATE TABLE IF NOT EXISTS public_paper_ids (
                            public_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL UNIQUE);
                    ''')
                    stored = conn.execute("SELECT value FROM metadata WHERE key='signature'").fetchone()
                    if not stored or stored[0] != signature:
                        conn.executescript('DELETE FROM papers; DELETE FROM fingerprints; DELETE FROM paper_topics;')
                    conn.execute('CREATE TEMP TABLE seen (id TEXT PRIMARY KEY)')
                    stats = {'scanned': 0, 'changed': 0, 'visible_updated': 0}
                    positions = {}

                    def changed_rows():
                        for row in snapshot.execute('SELECT rowid AS source_rowid, * FROM matched_entries'):
                            key = row['entry_id']
                            stats['scanned'] += 1
                            conn.execute('INSERT INTO seen VALUES (?)', (key,))
                            fingerprint = hashlib.sha256(json.dumps(dict(row), sort_keys=True).encode()).hexdigest()
                            old = conn.execute('SELECT fingerprint FROM fingerprints WHERE id=?', (key,)).fetchone()
                            if old and old[0] == fingerprint:
                                continue
                            stats['changed'] += 1
                            conn.execute('DELETE FROM papers WHERE id=?', (key,))
                            conn.execute('DELETE FROM paper_topics WHERE paper_id=?', (key,))
                            conn.execute('INSERT OR REPLACE INTO fingerprints VALUES (?,?)', (key, fingerprint))
                            positions[key] = (row['published_date'] if row['published_date'] is not None else row['matched_date'], row['source_rowid'])
                            yield row
                            positions.pop(key, None)

                    for paper in dashboard._iter_entries(_Snapshot(snapshot), None, catalog, rows=changed_rows()):
                        key = paper['id']
                        base_date, rowid = positions[key]
                        complete = bool(paper['authors'] != '作者未知' and paper['summary'] != '暂无摘要。'
                                        and paper['published'] and paper['source'] and paper['url'])
                        year = int(paper['published'][:4]) if paper['published'][:4].isdigit() else None
                        timestamp = _timestamp(paper)
                        if not math.isfinite(timestamp):
                            raise ValueError('Invalid paper timestamp: ' + key)
                        search = ' '.join([paper['title'], paper['authors'], paper['summary'],
                                           paper['source'], *paper['topics']]).casefold()
                        conn.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                            key, json.dumps(paper, ensure_ascii=False), search, timestamp, paper['score'],
                            paper['title'].casefold(), paper['source'], paper['venueType'], paper['theme'],
                            int(complete), year, base_date or '', rowid))
                        conn.executemany('INSERT INTO paper_topics VALUES (?,?)', [(key, topic) for topic in paper['topics']])
                        stats['visible_updated'] += 1
                    stats['deleted'] = conn.execute('SELECT count(*) FROM fingerprints WHERE id NOT IN (SELECT id FROM seen)').fetchone()[0]
                    conn.execute('DELETE FROM papers WHERE id NOT IN (SELECT id FROM seen)')
                    conn.execute('DELETE FROM paper_topics WHERE paper_id NOT IN (SELECT id FROM papers)')
                    conn.execute('DELETE FROM fingerprints WHERE id NOT IN (SELECT id FROM seen)')
                    # Only the disposable read model gains a URL lookup index. Existing
                    # snapshots and original IDs remain valid for older API clients.
                    conn.create_function('paper_public_id', 1, public_id, deterministic=True)
                    conn.execute('DELETE FROM public_paper_ids WHERE paper_id NOT IN (SELECT id FROM papers)')
                    conn.execute('INSERT OR IGNORE INTO public_paper_ids SELECT paper_public_id(id),id FROM papers')
                    public_catalog = [{k: v for k, v in topic.items() if k != 'pattern'} for topic in catalog]
                    previous_time = conn.execute("SELECT value FROM metadata WHERE key='updated_at'").fetchone()
                    if updated_at is None:
                        updated_at = (json.loads(previous_time[0]) if previous_time else
                                      datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat())
                    conn.execute("DELETE FROM metadata WHERE key='bootstrap'")
                    for key, value in {'signature': signature, 'catalog': public_catalog,
                                       'updated_at': updated_at}.items():
                        conn.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', (key, json.dumps(value)))
                    # Signature is stored as plain text to allow direct equality checks.
                    conn.execute("UPDATE metadata SET value=? WHERE key='signature'", (signature,))
                    conn.commit()
                    validate(conn)
                    stats['total'] = conn.execute('SELECT count(*) FROM papers').fetchone()[0]
                    if previous:
                        with closing(read_connection(previous)) as old:
                            old_total = old.execute('SELECT count(*) FROM papers').fetchone()[0]
                        if old_total and stats['total'] < old_total * 0.8:
                            raise ValueError('Refusing publication: visible paper count dropped by more than 20%')
                # Aggregates are prepared offline too: startup only reads a small JSON record.
                bootstrap = PublishedRepository(candidate).bootstrap()
                with closing(sqlite3.connect(candidate)) as conn:
                    conn.execute("INSERT OR REPLACE INTO metadata VALUES ('bootstrap',?)",
                                 (json.dumps(bootstrap, ensure_ascii=False),))
                    conn.commit()
                os.replace(candidate, destination)
                with destination.open('rb') as database_file:
                    os.fsync(database_file.fileno())
                manifest = root / ('manifest-' + uuid.uuid4().hex + '.tmp')
                try:
                    with manifest.open('w', encoding='utf-8') as output:
                        json.dump({'database': name, 'published_at': datetime.now(timezone.utc).isoformat()}, output)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(manifest, root / 'current.json')
                    published = True
                    _sync_directory(root)
                finally:
                    manifest.unlink(missing_ok=True)
            LOGGER.info('Published query database %s: %s', name, stats)
            # Read requests already holding SQLite connections remain valid after
            # unlink on Linux. Give idle repository objects two days to observe the pointer.
            try:
                versions = sorted(root.glob('query-*.db'), key=lambda path: path.stat().st_mtime, reverse=True)
                for expired in versions[3:]:
                    if expired != destination and expired.stat().st_mtime < time.time() - 172800:
                        expired.unlink()
            except OSError:
                LOGGER.warning('Unable to prune expired query versions', exc_info=True)
            return stats
        finally:
            if not published:
                destination.unlink(missing_ok=True)


def validate(conn):
    if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Query database integrity check failed')
    if conn.execute('SELECT count(*) FROM paper_topics WHERE paper_id NOT IN (SELECT id FROM papers)').fetchone()[0]:
        raise ValueError('Query database has orphan topic associations')
    for identifier, payload in conn.execute('SELECT id,payload FROM papers'):
        paper = json.loads(payload)
        if paper['id'] != identifier:
            raise ValueError('Query database paper identity mismatch')
        associated = {row[0] for row in conn.execute(
            'SELECT topic FROM paper_topics WHERE paper_id=?', (identifier,))}
        if associated != set(paper['topics']):
            raise ValueError('Query database paper topics mismatch')


class PublishedRepository:
    """One request pins one immutable version; no raw database or full-memory index."""

    def __init__(self, path=None):
        self.path = Path(path) if path else current_path()
        self._counts = OrderedDict()
        self._count_lock = threading.Lock()

    def _count(self, conn, where, values):
        # Cache only exact filter predicates, never rows or complete abstracts.
        # Including the connected immutable version prevents cross-version reuse.
        version = conn.execute('PRAGMA database_list').fetchone()[2]
        key = (version, where, tuple(values))
        now = time.monotonic()
        with self._count_lock:
            cached = self._counts.get(key)
            if cached and now - cached[0] < 60:
                self._counts.move_to_end(key)
                return cached[1]
        total = conn.execute('SELECT count(*) FROM papers' + where, values).fetchone()[0]
        with self._count_lock:
            self._counts[key] = (now, total)
            while len(self._counts) > 128:
                self._counts.popitem(last=False)
        return total

    def _connect(self, *, pinned=False):
        try:
            return read_connection(self.path)
        except sqlite3.OperationalError:
            if self.path.exists():
                raise
            if pinned:
                raise FileNotFoundError('Published query version expired')
            # A very old idle reader may resume after retention cleanup. Pin the
            # current complete version before doing any query or count.
            return read_connection(current_path())

    def detail(self, identifier, *, pinned=False):
        """Resolve a hashed URL without relying on previously visited list pages."""
        if len(identifier) != 64 or any(c not in '0123456789abcdef' for c in identifier):
            return None
        with closing(self._connect(pinned=pinned)) as conn:
            indexed = conn.execute("SELECT 1 FROM sqlite_master WHERE name='public_paper_ids'").fetchone()
            if indexed:
                row = conn.execute('SELECT payload FROM papers JOIN public_paper_ids '
                                   'ON papers.id=public_paper_ids.paper_id WHERE public_id=?', (identifier,)).fetchone()
            else:
                # Compatibility for v1.0 snapshots. The next build-query creates the
                # indexed mapping offline; no migration runs in the web process.
                conn.create_function('paper_public_id', 1, public_id, deterministic=True)
                row = conn.execute('SELECT payload FROM papers WHERE paper_public_id(id)=?', (identifier,)).fetchone()
            return present_paper(json.loads(row[0])) if row else None

    def query(self, params, *, pinned=False):
        with closing(self._connect(pinned=pinned)) as conn:
            ids = [key for value in params.get('ids', []) for key in value.split(',') if key][:250]
            if ids:
                found = {row['id']: json.loads(row['payload']) for row in conn.execute(
                    'SELECT id,payload FROM papers WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids)}
                items = [found[key] for key in ids if key in found]
                return {'items': items, 'total': len(items), 'page': 1, 'pages': 1}
            values, clauses = [], []
            for parameter, column in (('type', 'venue_type'), ('source', 'source')):
                value = params.get(parameter, [''])[0]
                if value:
                    clauses.append(column + '=?')
                    values.append(value)
            theme = params.get('theme', [''])[0]
            if theme:
                clauses.append('id IN (SELECT paper_id FROM paper_topics WHERE topic=?)')
                values.append(theme)
            query = params.get('q', [''])[0].strip().casefold()
            if query:
                clauses.append('instr(search,?)>0')
                values.append(query)
            try:
                days = max(0, int(params.get('days', ['0'])[0] or 0))
                page = max(1, int(params.get('page', ['1'])[0]))
                size = min(100, max(1, int(params.get('page_size', ['30'])[0])))
            except ValueError:
                days, page, size = 0, 1, 30
            if days:
                clauses.append('timestamp>=?')
                cutoff = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(days=days)
                values.append(cutoff.timestamp())
            where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
            total = self._count(conn, where, values)
            pages = max(1, (total + size - 1) // size)
            page = min(page, pages)
            order = {'date': 'timestamp DESC,score DESC,base_date DESC,source_rowid',
                     'score': 'score DESC,base_date DESC,source_rowid',
                     'title': 'title,base_date DESC,score DESC,source_rowid'}.get(params.get('sort', ['date'])[0],
                                                                                  'timestamp DESC,score DESC,base_date DESC,source_rowid')
            rows = conn.execute('SELECT payload FROM papers' + where + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?',
                                [*values, size, (page - 1) * size])
            return {'items': [json.loads(row[0]) for row in rows], 'total': total, 'page': page, 'pages': pages}

    def bootstrap(self):
        with closing(self._connect()) as conn:
            cached = conn.execute("SELECT value FROM metadata WHERE key='bootstrap'").fetchone()
            if cached:
                return json.loads(cached[0])
            catalog = json.loads(conn.execute("SELECT value FROM metadata WHERE key='catalog'").fetchone()[0])
            counts = dict(conn.execute('SELECT topic,count(*) FROM paper_topics GROUP BY topic'))
            topics = [{**topic, 'count': counts.get(topic['name'], 0)} for topic in catalog]
            sources = [{'name': row[0], 'type': row[1], 'count': row[2]} for row in conn.execute(
                'SELECT source,venue_type,count(*) AS n FROM papers GROUP BY source,venue_type ORDER BY n DESC,source,venue_type')]
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
            total, classified, complete, recent = conn.execute(
                "SELECT count(*),coalesce(sum(theme!=''),0),coalesce(sum(complete),0),coalesce(sum(timestamp>=?),0) FROM papers", (cutoff,)).fetchone()
            themes = list(conn.execute("SELECT theme,count(*) AS n FROM papers WHERE theme!='' GROUP BY theme ORDER BY n DESC, max(base_date) DESC"))
            segments = [{'name': row[0], 'count': row[1]} for row in themes[:7]]
            other = sum(row[1] for row in themes[7:])
            if other:
                segments.append({'name': '其他主题', 'count': other})
            yearly = [{'year': row[0], 'count': row[1]} for row in conn.execute(
                'SELECT year,count(*) FROM papers WHERE year BETWEEN 2000 AND ? GROUP BY year ORDER BY year',
                (datetime.now().year + 1,))][-6:]
            latest = [json.loads(row[0]) for row in conn.execute(
                'SELECT payload FROM papers ORDER BY timestamp DESC,score DESC,base_date DESC,source_rowid LIMIT 6')]
            result = {'topics': topics, 'sources': sources, 'overview': {
                'total': total, 'classified': classified, 'informationComplete': complete,
                'recent30': recent, 'topicCount': len(topics), 'sourceCount': len(sources),
                'themeSegments': segments, 'yearly': yearly, 'latest': latest}}
            updated = json.loads(conn.execute("SELECT value FROM metadata WHERE key='updated_at'").fetchone()[0])
            if updated:
                result['dataUpdatedAt'] = updated
            return result

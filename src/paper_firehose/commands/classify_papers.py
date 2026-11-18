"""Administrator-only API classification for JSON samples or incremental papers."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..api_classification import (APIClassifier, ClassificationError, FatalAPIError, OffPeakPause,
                                  ResultStore, Settings, BudgetExceeded, digest, paper_text, validate_stored, validate_text)
from ..core.config import ConfigManager
from ..core.paths import resolve_data_file, resolve_data_path

LOGGER = logging.getLogger(__name__)


@contextmanager
def _writer(name='api.lock'):
    import fcntl
    path = resolve_data_path('classification', name, ensure_parent=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another API classification task is running') from exc
        yield


def _predict(paper, store, settings, client):
    validate_text(paper)
    key = settings.cache_key(paper)
    cached = store.cached(key)
    if cached is not None:
        return validate_stored(cached, paper), True
    result = client.classify(paper)
    store.save(key, result)
    return result, False


def _export(path, papers):
    """Atomic, no-clobber export; never replace a source JSON or database."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.classification-', delete=False) as output:
            temporary = Path(output.name)
            json.dump(papers, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def classify_json(input_path, output_path, *, limit=50):
    if not 0 <= limit <= 10000:
        raise ValueError('JSON classification limit must be between 0 and 10000 (0 means the whole file)')
    if Path(output_path).exists():
        raise ValueError('Output already exists; choose a new path')
    if Path(input_path).stat().st_size > 16 * 1024 * 1024:
        raise ValueError('JSON sample exceeds 16 MiB; split it into smaller batches')
    papers = json.loads(Path(input_path).read_text(encoding='utf-8'))
    if not isinstance(papers, list) or not 1 <= len(papers) <= (limit or 10000):
        raise ValueError('Input must be a nonempty JSON array within --limit')
    # Validate the entire batch before the first billable request.
    for paper in papers:
        validate_text(paper)
    settings = Settings.from_env()
    report = {'total': len(papers), 'processed': 0, 'classified': 0, 'cached': 0,
              'failed': 0, 'needs_review': 0, 'out_of_scope': 0}
    results, stopped = [], False
    with _writer(), closing(ResultStore()) as store:
        client = APIClassifier(settings, store)
        for index, original in enumerate(papers, 1):
            paper = {k: original[k] for k in ('title', 'abstract')}
            try:
                if stopped:
                    raise ClassificationError('Not attempted after fatal API error')
                result, cached = _predict(paper, store, settings, client)
                results.append({**result, 'index': index})
                report['processed'] += 1
                report[result['decision']] += 1
                report['cached'] += int(cached)
            except ClassificationError as exc:
                stopped = stopped or isinstance(exc, (FatalAPIError, BudgetExceeded))
                results.append({'index': index, **paper, 'status': 'pending', 'error': str(exc)})
                report['failed'] += 1
                LOGGER.warning('API classification sample index=%d: %s', index, exc)
                if isinstance(exc, FatalAPIError):
                    store.trip(3600)
            LOGGER.info('API classification sample progress %d/%d', index, len(papers))
        _export(output_path, results)
        store_status = store.status()
    return {**report, 'ok': report['failed'] == 0, 'output': str(output_path), 'accounting': store_status}


def prepare_scope(config_path):
    """Freeze existing IDs before collection; no model calls or raw DB writes."""
    from .. import query_store
    manager = ConfigManager(config_path)
    source = resolve_data_file(manager.load_config()['database']['history_path']).resolve()
    with _writer('scope.lock'), closing(ResultStore()) as store:
        previous = store.conn.execute("SELECT value FROM state WHERE key='new_only_scope'").fetchone()
        if previous:
            scope = json.loads(previous[0])
            if scope['source'] != str(source):
                raise ValueError('Classification baseline belongs to another database; use a separate data directory')
            return {'ok': True, 'created': False, **scope}
        count = 0
        stamp = datetime.now(timezone.utc).isoformat()
        with closing(query_store.read_connection(source)) as raw:
            for row in raw.execute('SELECT entry_id FROM matched_entries'):
                store.conn.execute("INSERT OR IGNORE INTO tracked_papers VALUES (?,'historical',?)", (row['entry_id'], stamp))
                count += 1
                if count % 1000 == 0:
                    store.conn.commit()
        # Existing unfinished work is historical too. Retain predictions and
        # accepted assignments for audit, but never spend on this old queue.
        store.conn.execute("UPDATE jobs SET status='historical',lease_until='' WHERE paper_id IN "
                           "(SELECT paper_id FROM tracked_papers WHERE scope='historical') "
                           "AND status IN ('pending','retry_wait','running','waiting_metadata','needs_review','failed','invalid_input')")
        scope = {'source': str(source), 'initialized_at': stamp, 'historical_count': count}
        store.conn.execute("INSERT INTO state VALUES ('new_only_scope',?)", (json.dumps(scope),))
        store.conn.commit()
        return {'ok': True, 'created': True, **scope}


def queue(config_path, *, all_papers=False):
    """Default: enqueue only identities first seen after the historical baseline."""
    from .. import query_store
    baseline = prepare_scope(config_path)
    settings = Settings.from_env()
    source = Path(baseline['source'])
    count = 0
    with closing(ResultStore()) as store, closing(query_store.read_connection(source)) as raw:
        stamp = datetime.now(timezone.utc).isoformat()
        for index, row in enumerate(raw.execute('SELECT entry_id,title,abstract,summary,topics FROM matched_entries'), 1):
            if row['topics'] == 'demo_topic':
                continue
            tracked = store.conn.execute('SELECT scope FROM tracked_papers WHERE paper_id=?', (row['entry_id'],)).fetchone()
            if all_papers and tracked and tracked['scope'] == 'historical':
                store.conn.execute("UPDATE tracked_papers SET scope='manual' WHERE paper_id=?", (row['entry_id'],))
            elif tracked and tracked['scope'] != 'new' and not all_papers:
                continue
            elif not tracked:
                store.conn.execute("INSERT INTO tracked_papers VALUES (?,'new',?)", (row['entry_id'], stamp))
            paper = paper_text(dict(row))
            job = store.conn.execute('SELECT text_hash,status FROM jobs WHERE paper_id=?', (row['entry_id'],)).fetchone()
            if all_papers and job and job['status'] == 'historical':
                store.conn.execute("UPDATE jobs SET status='pending',attempts=0,next_attempt='',error='' WHERE paper_id=?", (row['entry_id'],))
            # A policy/model change alone must not silently reclassify accepted
            # work. Explicit --all-papers remains the administrator escape hatch.
            if all_papers or not job or job['text_hash'] != digest(paper) or job['status'] in {'pending', 'retry_wait'}:
                count += int(store.enqueue(row['entry_id'], paper, settings))
            if index % 200 == 0:
                store.conn.commit()
        store.conn.commit()
    return {'queued': count, 'ok': True, 'historical_count': baseline['historical_count']}


def _due_jobs(store, limit, include_history):
    # Zero means no paper-count cap, not an unbounded fetchall. Memory stays
    # bounded to 100 small job records plus one paper, even for a large intake.
    processed = 0
    while not limit or processed < limit:
        batch = store.due(min(100, limit - processed) if limit else 100, include_history=include_history)
        if not batch:
            return
        for job in batch:
            # Text can change while HTTP runs. Process that new version next
            # run, rather than letting one rapidly edited paper loop forever.
            store.conn.execute('INSERT OR IGNORE INTO worker_seen VALUES (?)', (job['paper_id'],))
            store.conn.commit()
            yield job
            processed += 1


def publish_results(config_path):
    """One batched publication only when accepted assignments changed."""
    from .. import query_store
    from ..api_classification import api_enabled
    if not api_enabled():
        raise ValueError('Publishing API classifications requires PAPER_COMPASS_CLASSIFIER=api')
    with closing(ResultStore()) as store:
        revision = store.status()['assignment_revision']
    try:
        with closing(query_store.read_connection(query_store.current_path())) as conn:
            previous = conn.execute("SELECT value FROM metadata WHERE key='classification_revision'").fetchone()
            if previous and json.loads(previous[0]) == revision:
                return {'ok': True, 'skipped': True, 'reason': 'no accepted assignment changes'}
    except FileNotFoundError:
        pass
    return {'ok': True, 'result': query_store.build(config_path)}


def run(config_path, *, limit=0, all_papers=False, queued_only=False, publish=False, retry_failed=False):
    """Bounded single worker. Read one paper at a time; never fetch sources here."""
    from .. import query_store
    if limit < 0 or (queued_only and all_papers):
        raise ValueError('Invalid classification limit or queued-only/all-papers combination')
    settings = Settings.from_env()
    stats = {'started_at': datetime.now(timezone.utc).isoformat(),
             'queued': 0, 'classified': 0, 'cached': 0, 'failed': 0,
             'needs_review': 0, 'out_of_scope': 0, 'waiting_metadata': 0, 'deferred': False}
    if not queued_only:
        stats.update(queue(config_path, all_papers=all_papers))
    else:
        prepare_scope(config_path)
    manager = ConfigManager(config_path)
    source = resolve_data_file(manager.load_config()['database']['history_path'])
    with _writer(), closing(ResultStore()) as store:
        if retry_failed:
            scope_sql = '' if all_papers else " AND paper_id IN (SELECT paper_id FROM tracked_papers WHERE scope='new')"
            store.conn.execute("UPDATE jobs SET status='pending',attempts=0,next_attempt='' "
                               "WHERE (status IN ('failed','invalid_input','retry_wait') "
                               "OR (status='pending' AND error!=''))" + scope_sql)
            store.conn.commit()
        jobs = _due_jobs(store, limit, all_papers)
        client = None
        consecutive_errors = 0
        for index, job in enumerate(jobs, 1):
            if client is None:
                client = APIClassifier(settings, store)
            identifier = job['paper_id']
            # No source read connection or transaction remains open during HTTP.
            with closing(query_store.read_connection(source)) as raw:
                row = raw.execute('SELECT * FROM matched_entries WHERE entry_id=?', (identifier,)).fetchone()
            if row is None:
                store.conn.execute('DELETE FROM jobs WHERE paper_id=?', (identifier,))
                store.conn.commit()
                continue
            paper = paper_text(dict(row))
            key = settings.cache_key(paper)
            store.enqueue(identifier, paper, settings)
            store.conn.commit()
            try:
                validate_text(paper)
            except ValueError as exc:
                category = 'waiting_metadata' if not paper['title'] or not paper['abstract'] or 'Missing' in str(exc) else 'invalid_input'
                store.defer(identifier, key, str(exc), category=category)
                stats['waiting_metadata' if category == 'waiting_metadata' else 'failed'] += 1
                continue
            if not store.claim(identifier, key, settings.timeout):
                continue
            try:
                result, cached = _predict(paper, store, settings, client)
                if store.complete(identifier, key, result):
                    stats[result['decision']] += 1
                stats['cached'] += int(cached)
                consecutive_errors = 0
            except OffPeakPause as exc:
                store.defer(identifier, key, str(exc), category='window', resume_at=exc.resume_at)
                stats.update(deferred=True, deferred_reason='off_peak_window', resume_at=exc.resume_at)
                LOGGER.info('API classification paused until %s: off-peak window closed or closing', exc.resume_at)
                break
            except BudgetExceeded as exc:
                category = 'circuit' if 'circuit' in str(exc) else 'budget'
                store.defer(identifier, key, str(exc), category=category)
                stats['deferred'] = True
                stats['deferred_reason'] = category
                LOGGER.info('API classification paused: %s', category)
                break
            except ClassificationError as exc:
                store.defer(identifier, key, str(exc))
                stats['failed'] += 1
                consecutive_errors += 1
                LOGGER.warning('API classification failed paper=%s: %s', query_store.public_id(identifier), exc)
                if isinstance(exc, FatalAPIError) or consecutive_errors >= 3:
                    store.trip(3600 if isinstance(exc, FatalAPIError) else 900)
                    stats['deferred'] = True
                    break
            LOGGER.info('API classification progress %d/%s classified=%d review=%d failed=%d',
                        index, limit or 'all-new', stats['classified'], stats['needs_review'], stats['failed'])
        accounting = store.status()
        stats['pending'] = (sum(accounting['jobs'].get(s, 0) for s in ('pending', 'retry_wait', 'running'))
                            if all_papers else accounting['pending_new'])
        stats['accounting'] = accounting
        stats['ok'] = stats['failed'] == 0
        if publish:
            try:
                stats['publication'] = publish_results(config_path)
            except Exception as exc:
                # Accepted decisions stay durable. A busy/failed publisher is retried
                # next run even if there are no remaining API jobs.
                LOGGER.warning('Classification publication deferred (%s)', type(exc).__name__)
                stats['publication'] = {'ok': False, 'error': 'Publication deferred; cached results retained'}
                stats['ok'] = False
        stats['finished_at'] = datetime.now(timezone.utc).isoformat()
        stats['status'] = 'partial_failure' if not stats['ok'] else ('deferred' if stats['deferred'] else 'batch_complete')
        _save_worker_status(stats)
    return stats


def _save_worker_status(report):
    path = resolve_data_path('classification', 'status.json', ensure_parent=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def status(*, reset_circuit=False):
    with closing(ResultStore()) as store:
        if reset_circuit:
            store.conn.execute("DELETE FROM state WHERE key='circuit_until'")
            store.conn.commit()
        result = store.status()
        proposals = store.conn.execute("SELECT jobs.paper_id,predictions.result FROM jobs JOIN predictions USING(cache_key) "
                                        "WHERE jobs.status='needs_review' ORDER BY last_attempt DESC LIMIT 20").fetchall()
        from ..query_store import public_id
        result['review'] = [{'paper': public_id(row['paper_id']), **{k: json.loads(row['result']).get(k)
                            for k in ('title', 'topic_key', 'reason', 'evidence')}} for row in proposals]
        return result


def review(config_path, identifier, *, topic=None, out_of_scope=False, note, evidence=None):
    from ..api_classification import TOPICS, digest
    from .. import query_store
    if bool(topic) == bool(out_of_scope) or (topic and topic not in TOPICS) or not note.strip():
        raise ValueError('Choose a valid topic or out-of-scope and provide a review note')
    if len(identifier) != 64 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('Use the hashed paper identifier from classification-status')
    manager = ConfigManager(config_path)
    source = resolve_data_file(manager.load_config()['database']['history_path'])
    with _writer(), closing(ResultStore()) as store:
        store.conn.create_function('public_id', 1, query_store.public_id)
        job = store.conn.execute("SELECT * FROM jobs WHERE public_id(paper_id)=? "
                                  "AND status IN ('needs_review','classified','out_of_scope')", (identifier,)).fetchone()
        if not job:
            raise ValueError('No reviewable classification for this paper')
        with closing(query_store.read_connection(source)) as raw:
            row = raw.execute('SELECT * FROM matched_entries WHERE entry_id=?', (job['paper_id'],)).fetchone()
        if not row or digest(paper_text(dict(row))) != job['text_hash']:
            raise ValueError('Paper text changed; classify its current version before reviewing')
        result = validate_stored(store.cached(job['cache_key']), paper_text(dict(row)))
        proposal = {**result, 'decision': 'out_of_scope' if out_of_scope else 'classified',
                    'topic_key': topic, 'topic_name': TOPICS.get(topic), 'needs_review': False,
                    'model_confidence': result['confidence'], 'confidence': 'high',
                    'reason': note.strip(), 'evidence': evidence if evidence is not None else result['evidence'],
                    'reviewed_by': 'manual-cli', 'reviewed_at': datetime.now(timezone.utc).isoformat()}
        proposal = validate_stored(proposal, paper_text(dict(row)))
        key = 'manual:' + digest(proposal)
        store.save(key, proposal)
        store.conn.execute("UPDATE tracked_papers SET scope='manual' WHERE paper_id=? AND scope='historical'", (job['paper_id'],))
        store.conn.execute('UPDATE jobs SET cache_key=? WHERE paper_id=?', (key, job['paper_id']))
        store.complete(job['paper_id'], key, proposal)
    return {'ok': True, 'paper': identifier, 'decision': proposal['decision'], 'topic_key': topic}

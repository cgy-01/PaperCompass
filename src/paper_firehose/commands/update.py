"""Administrator-only daily ingestion, with a durable publication marker."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from . import arxiv, official_sources, openalex
from .dashboard import CURATED_TOPIC_KEYS
from ..core.config import ConfigManager
from ..core.database import DatabaseManager
from ..core.paths import resolve_data_path
from .. import query_store

LOGGER = logging.getLogger(__name__)


class _Failures(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _save_status(report):
    marker = resolve_data_path('update-status.json')
    temporary = marker.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, marker)
    if report.get('scope') in {'arxiv', 'conferences'}:
        scoped = resolve_data_path('update-status-' + report['scope'] + '.json')
        temporary = scoped.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, scoped)


def _publish(config_path, report, name):
    LOGGER.info('Update stage started: %s', name)
    try:
        result = query_store.build(config_path, updated_at=datetime.now(timezone.utc).isoformat())
        report['stages'][name] = {'ok': True, 'result': result}
    except Exception as exc:
        LOGGER.exception('Query database publication failed; previous version remains active')
        report['stages'][name] = {'ok': False, 'error': str(exc)}
    _save_status(report)


def run(config_path: str, *, include_openalex: bool = False, scope: str = 'all') -> dict:
    """Run all configured sources once; never delete historical papers."""
    import fcntl  # Server scheduler targets Linux; other CLI commands remain portable.
    if scope not in {'all', 'arxiv', 'conferences'}:
        raise ValueError('Unknown update scope: ' + scope)
    lock_path = resolve_data_path('update.lock')
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another paper update is already running') from exc
        manager = ConfigManager(config_path)
        if not manager.validate_config():
            raise ValueError('Configuration validation failed')
        db = DatabaseManager(manager.load_config())
        try:
            backups = db.backup_important_databases()
            if not {'history', 'all_feeds'}.issubset(backups):
                raise RuntimeError('Database backup incomplete; refusing to collect papers')
        finally:
            db.close_all_connections()
        available = set(manager.get_available_topics())
        topics = [key for key in CURATED_TOPIC_KEYS if key in available]
        if not topics:
            raise ValueError('No curated research topics configured')
        report = {'started_at': datetime.now(timezone.utc).isoformat(), 'scope': scope, 'status': 'running', 'stages': {}}
        stages = ([('rss', lambda: arxiv.run(config_path, catch_up=False)),
                   ('arxiv_catchup', lambda: arxiv.run(config_path, rss=False))]
                  if scope != 'conferences' else [])
        if include_openalex:
            stages.append(('openalex', lambda: openalex.run(config_path, days=30, paginate=True)))
        else:
            report['stages']['openalex'] = {'ok': True, 'skipped': True, 'reason': 'disabled'}
            LOGGER.info('OpenAlex disabled for this update')
        if scope != 'arxiv':
            stages.append(('conferences', lambda: official_sources.run(config_path, family='all', metadata_workers=2,
                                                                      incremental=True)))
        _save_status(report)
        for name, action in stages:
            capture = _Failures()
            log = logging.getLogger('paper_firehose')
            log.addHandler(capture)
            LOGGER.info('Update stage started: %s', name)
            try:
                result = action()
                failed = isinstance(result, dict) and bool(result.get('metadata_failed') or result.get('failed'))
                report['stages'][name] = {'result': result, 'warnings': capture.messages,
                                         'ok': not capture.messages and not failed}
            except Exception as exc:
                LOGGER.exception('Update stage failed: %s', name)
                report['stages'][name] = {'ok': False, 'error': str(exc)}
            finally:
                log.removeHandler(capture)
            _save_status(report)
            if name == 'rss' and 'error' not in report['stages'][name]:
                _publish(config_path, report, 'publication_rss')
        _publish(config_path, report, 'publication')
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report['ok'] = all(stage['ok'] for stage in report['stages'].values())
        report['status'] = 'complete' if report['ok'] else 'partial_failure'
        _save_status(report)
        LOGGER.info('Paper update finished: ok=%s', report['ok'])
        return report

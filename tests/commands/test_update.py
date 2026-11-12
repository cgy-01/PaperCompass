import fcntl
import json
import logging
from unittest.mock import Mock

import pytest

from paper_firehose.commands import update


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(update, 'resolve_data_path', lambda name: tmp_path / name)
    manager = Mock()
    manager.validate_config.return_value = True
    manager.get_available_topics.return_value = ['llm', 'ai_infra', 'demo_topic']
    monkeypatch.setattr(update, 'ConfigManager', Mock(return_value=manager))
    database = Mock()
    database.backup_important_databases.return_value = {'history': 'history.bak', 'all_feeds': 'feeds.bak'}
    monkeypatch.setattr(update, 'DatabaseManager', Mock(return_value=database))
    for module in (update.arxiv, update.openalex, update.official_sources):
        monkeypatch.setattr(module, 'run', Mock(return_value={'inserted': 1}))
    monkeypatch.setattr(update.query_store, 'build', Mock(return_value={'changed': 1}))
    return tmp_path, database


def test_update_all_sources_and_publish(pipeline):
    directory, database = pipeline
    report = update.run('config.yaml')
    assert report['ok']
    database.backup_important_databases.assert_called_once()
    assert update.arxiv.run.call_count == 2
    assert update.arxiv.run.call_args_list[0].kwargs == {'catch_up': False}
    assert update.arxiv.run.call_args_list[1].kwargs == {'rss': False}
    update.official_sources.run.assert_called_once_with(
        'config.yaml', family='all', metadata_workers=2, incremental=True)
    update.openalex.run.assert_not_called()
    assert report['stages']['openalex']['skipped']
    assert update.query_store.build.call_count == 2
    assert json.loads((directory / 'update-status.json').read_text()) == report
    assert not (directory / 'update-status.tmp').exists()


def test_partial_failure_continues_and_releases_lock(pipeline):
    update.arxiv.run.side_effect = RuntimeError('offline')
    report = update.run('config.yaml')
    assert not report['ok']
    assert report['stages']['conferences']['ok']
    update.arxiv.run.side_effect = None
    assert update.run('config.yaml')['ok']


def test_warning_and_metadata_failure_are_reported(pipeline):
    def warn(*args, **kwargs):
        logging.getLogger('paper_firehose.commands.filter').warning('feed unavailable')
    update.arxiv.run.side_effect = warn
    update.official_sources.run.return_value = {'metadata_failed': 1}
    report = update.run('config.yaml')
    assert not report['stages']['rss']['ok']
    assert not report['stages']['conferences']['ok']


def test_overlapping_run_is_rejected(pipeline):
    directory, _ = pipeline
    with (directory / 'update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='already running'):
            update.run('config.yaml')
    update.arxiv.run.assert_not_called()


def test_backup_failure_does_not_publish_or_collect(pipeline):
    directory, database = pipeline
    database.backup_important_databases.side_effect = OSError('disk full')
    with pytest.raises(OSError):
        update.run('config.yaml')
    assert not (directory / 'update-status.json').exists()
    update.arxiv.run.assert_not_called()


def test_incomplete_backup_result_stops_collection(pipeline):
    _, database = pipeline
    database.backup_important_databases.return_value = {}
    with pytest.raises(RuntimeError, match='backup incomplete'):
        update.run('config.yaml')
    update.arxiv.run.assert_not_called()


def test_publication_failure_marks_task_failed(pipeline):
    update.query_store.build.side_effect = ValueError('invalid candidate')
    report = update.run('config.yaml')
    assert not report['ok']
    assert not report['stages']['publication']['ok']


def test_openalex_remains_explicitly_available(pipeline):
    assert update.run('config.yaml', include_openalex=True)['ok']
    update.openalex.run.assert_called_once_with('config.yaml', days=30, paginate=True)


def test_rss_is_published_and_status_saved_before_conferences(pipeline):
    directory, _ = pipeline
    def conferences(*args, **kwargs):
        report = json.loads((directory / 'update-status.json').read_text())
        assert report['status'] == 'running'
        assert report['stages']['publication_rss']['ok']
        assert update.query_store.build.call_count == 1
        return {'inserted': 0}
    update.official_sources.run.side_effect = conferences
    assert update.run('config.yaml')['status'] == 'complete'


def test_arxiv_scope_never_waits_for_conferences(pipeline):
    directory, _ = pipeline
    report = update.run('config.yaml', scope='arxiv')
    assert report['ok']
    update.official_sources.run.assert_not_called()
    assert json.loads((directory / 'update-status-arxiv.json').read_text())['scope'] == 'arxiv'


def test_conferences_scope_does_not_refetch_arxiv(pipeline):
    directory, _ = pipeline
    assert update.run('config.yaml', scope='conferences')['ok']
    update.arxiv.run.assert_not_called()
    assert (directory / 'update-status-conferences.json').exists()

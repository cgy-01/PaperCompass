import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests

from paper_firehose.commands import official_sources as source


@pytest.fixture
def incremental_source(tmp_path, monkeypatch):
    manager = Mock()
    manager.get_available_topics.return_value = ['llm']
    manager.load_topic_config.return_value = {'filter': {'pattern': 'LLM'}}
    monkeypatch.setattr(source, 'ConfigManager', Mock(return_value=manager))
    conn = sqlite3.connect(tmp_path / 'raw.db')
    db = Mock()
    db.db_paths = {'history': str(tmp_path / 'raw.db')}
    db.get_connection.return_value = conn
    monkeypatch.setattr(source, 'DatabaseManager', Mock(return_value=db))
    monkeypatch.setattr(source, 'resolve_data_path', lambda *parts: tmp_path.joinpath(*parts))
    venue = 'ICML ' + str(datetime.now(timezone.utc).year)
    monkeypatch.setattr(source, 'PMLR_VOLUMES', {'https://official.test/': (venue, '2026-07-01')})
    monkeypatch.setattr(source, 'OFFICIAL_INDEXES', {})
    items = [{'title': 'LLM one', 'url': 'https://official.test/1'},
             {'title': 'LLM two', 'url': 'https://official.test/2'}]
    fetch = Mock(return_value=items)
    upsert = Mock(return_value='inserted')
    monkeypatch.setattr(source, '_fetch_html', fetch)
    monkeypatch.setattr(source, '_upsert', upsert)
    def state_path():
        return next((tmp_path / 'conference-sync').glob('*.json'))
    def make_due():
        path = state_path()
        state = json.loads(path.read_text())
        state['last_success'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        path.write_text(json.dumps(state))
        return state
    yield fetch, upsert, items, state_path, make_due, manager
    conn.close()


def run():
    return source.run('unused', incremental=True, enrich_metadata=False)


def test_same_day_skips_network(incremental_source):
    fetch, upsert, *_ = incremental_source
    run()
    fetch.reset_mock()
    upsert.reset_mock()
    assert run()['sources_deferred'] == 1
    fetch.assert_not_called()
    upsert.assert_not_called()


def test_changed_directory_only_processes_new_records(incremental_source):
    fetch, upsert, items, _, make_due, _ = incremental_source
    run()
    make_due()
    upsert.reset_mock()
    items.append({'title': 'LLM three', 'url': 'https://official.test/3'})
    result = run()
    assert result['items_unchanged'] == 2
    upsert.assert_called_once()
    assert upsert.call_args.args[1]['id'] == 'https://official.test/3'


def test_failure_does_not_advance_success_time(incremental_source):
    fetch, _, _, path, make_due, _ = incremental_source
    run()
    before = make_due()
    fetch.side_effect = requests.Timeout('offline')
    assert run()['failed'] == 1
    assert json.loads(path().read_text()) == before


def test_unchanged_directory_skips_all_upserts(incremental_source):
    _, upsert, _, _, make_due, _ = incremental_source
    run()
    make_due()
    upsert.reset_mock()
    assert run()['items_unchanged'] == 2
    upsert.assert_not_called()


def test_rule_change_bypasses_schedule_and_reprocesses(incremental_source):
    _, upsert, _, _, _, manager = incremental_source
    run()
    manager.load_topic_config.return_value = {'filter': {'pattern': 'LLM|Agent'}}
    upsert.reset_mock()
    run()
    assert upsert.call_count == 2


def test_empty_response_does_not_mark_success(incremental_source):
    fetch, _, _, path, make_due, _ = incremental_source
    run()
    before = make_due()
    fetch.return_value = []
    assert run()['failed'] == 1
    assert json.loads(path().read_text()) == before


def test_304_skips_processing_and_advances_check(incremental_source):
    fetch, upsert, _, path, make_due, _ = incremental_source
    run()
    before = make_due()
    fetch.return_value = None
    upsert.reset_mock()
    assert run()['sources_unchanged'] == 1
    assert json.loads(path().read_text())['last_success'] != before['last_success']
    upsert.assert_not_called()


def test_metadata_retries_even_when_directory_deferred(incremental_source, monkeypatch):
    from paper_firehose.commands import conference_metadata
    run()
    hydrate = Mock(return_value={'updated': 1, 'failed': 0})
    monkeypatch.setattr(conference_metadata, 'run', hydrate)
    result = source.run('unused', incremental=True)
    assert result['sources_deferred'] == 1
    assert result['metadata_updated'] == 1
    hydrate.assert_called_once()


def test_recent_daily_and_old_weekly_calendar():
    now = datetime(2026, 11, 12, 20, tzinfo=timezone.utc)  # Beijing Nov 13, 04:00
    yesterday = {'last_success': '2026-11-12T04:00:00+00:00'}
    assert source._source_due(yesterday, 2026, now)
    assert not source._source_due(yesterday, 2023, now)
    assert source._source_due({'last_success': '2026-11-05T20:00:00+00:00'}, 2023, now)
    assert source._source_due({}, 2023, now)


def test_conditional_http_304_does_not_parse(monkeypatch):
    response = Mock(status_code=304, headers={'ETag': 'v1'})
    fetch = Mock(return_value=response)
    monkeypatch.setattr(source.requests, 'get', fetch)
    metadata = {}
    assert source._fetch_html('https://official.test/', 'pmlr', validators={'etag': 'v1'},
                              response_metadata=metadata) is None
    assert fetch.call_args.kwargs['headers']['If-None-Match'] == 'v1'
    assert metadata['etag'] == 'v1'

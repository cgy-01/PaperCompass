"""Read-model publication is isolated from the authoritative raw database."""

import hashlib
import json
import sqlite3
from unittest.mock import Mock

import pytest

from paper_firehose import query_store as store


@pytest.fixture
def catalog_store(tmp_path, monkeypatch):
    raw = tmp_path / 'raw.db'
    with sqlite3.connect(raw) as conn:
        conn.execute('''CREATE TABLE matched_entries (
            entry_id TEXT PRIMARY KEY, feed_name TEXT, topics TEXT, title TEXT, link TEXT,
            summary TEXT, authors TEXT, abstract TEXT, doi TEXT, published_date TEXT,
            matched_date TEXT, llm_summary TEXT, paper_qa_summary TEXT, rank_score REAL,
            venue_name TEXT, venue_type TEXT, external_id TEXT, cited_by_count INTEGER)''')
        for key, date, score in [('new', '2026-04-23', .8), ('old', '2024-01-01', .9)]:
            conn.execute('''INSERT INTO matched_entries VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                key, 'ICLR 2026', '', 'Paper ' + key, 'https://example.test/' + key,
                'An abstract about infrastructure', 'Ada Lovelace',
                'An abstract about infrastructure', '', date, '2026-04-24', '', '',
                score, 'ICLR 2026', '会议', 'https://official.test/' + key, 0))
    manager = Mock()
    manager.validate_config.return_value = True
    manager.load_config.return_value = {'database': {'history_path': str(raw)}}
    monkeypatch.setattr(store, 'ConfigManager', Mock(return_value=manager))
    monkeypatch.setattr(store, 'resolve_data_path', lambda *parts: tmp_path.joinpath(*parts))
    catalog = [{'key': 'ai_infra', 'name': 'AI Infra', 'description': '', 'icon': 'cpu',
                'pattern': 'infrastructure', 'min_matches': 1}]
    monkeypatch.setattr(store.dashboard, '_topic_catalog', lambda _: catalog)
    return raw, catalog


def test_incremental_build_and_raw_safety(catalog_store):
    raw, _ = catalog_store
    before = hashlib.sha256(raw.read_bytes()).hexdigest()
    assert store.build('unused')['changed'] == 2
    assert store.build('unused')['changed'] == 0
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == before
    with sqlite3.connect(raw) as conn:
        conn.execute("UPDATE matched_entries SET abstract='Updated infrastructure' WHERE entry_id='new'")
    assert store.build('unused')['changed'] == 1
    assert store.PublishedRepository().query({'ids': ['new']})['items'][0]['summary'] == 'Updated infrastructure'


def test_failed_candidate_keeps_pointer_and_old_requests(catalog_store, monkeypatch):
    raw, _ = catalog_store
    store.build('unused')
    old = store.PublishedRepository()
    old_path = store.current_path()
    with sqlite3.connect(raw) as conn:
        conn.execute("UPDATE matched_entries SET title='Changed' WHERE entry_id='new'")
    def fail(_):
        raise ValueError('validation failed')
    with monkeypatch.context() as patch:
        patch.setattr(store, 'validate', fail)
        with pytest.raises(ValueError, match='validation failed'):
            store.build('unused')
    assert store.current_path() == old_path
    store.build('unused')
    assert old.query({'ids': ['new']})['items'][0]['title'] == 'Paper new'
    assert store.PublishedRepository().query({'ids': ['new']})['items'][0]['title'] == 'Changed'


def test_rules_change_reclassifies_unchanged_papers(catalog_store):
    _, catalog = catalog_store
    store.build('unused')
    catalog[0]['pattern'] = 'does not match'
    assert store.build('unused')['changed'] == 2
    # Official papers remain visible even without AI topic matches.
    assert store.PublishedRepository().bootstrap()['overview']['classified'] == 0


def test_suspicious_drop_is_not_published(catalog_store):
    raw, _ = catalog_store
    store.build('unused')
    old_path = store.current_path()
    with sqlite3.connect(raw) as conn:
        conn.execute('DELETE FROM matched_entries')
    with pytest.raises(ValueError, match='dropped'):
        store.build('unused')
    assert store.current_path() == old_path


def test_api_contract_and_literal_search(catalog_store):
    raw, _ = catalog_store
    with sqlite3.connect(raw) as conn:
        conn.execute("UPDATE matched_entries SET abstract='100% GPU_test infrastructure' WHERE entry_id='new'")
    store.build('unused')
    repository = store.PublishedRepository()
    assert repository.query({'q': ['100% gpu_']})['total'] == 1
    assert repository.query({'q': ['AI Infra']})['total'] == 2
    assert repository.query({'source': ['other']})['total'] == 0
    assert repository.query({'theme': ['AI Infra'], 'page_size': ['1']})['pages'] == 2
    assert repository.query({'sort': ['score']})['items'][0]['id'] == 'old'
    assert repository.query({'sort': ['date']})['items'][0]['id'] == 'new'
    assert repository.query({})['items'][0]['id'] == 'new'
    assert [p['id'] for p in repository.query({'ids': ['old,new']})['items']] == ['old', 'new']
    assert repository.bootstrap()['overview']['total'] == 2


def test_raw_missing_does_not_create_empty_database(catalog_store):
    raw, _ = catalog_store
    raw.unlink()
    with pytest.raises(sqlite3.OperationalError):
        store.build('unused')
    assert not raw.exists()


def test_immutable_snapshot_ignores_raw_writes(catalog_store):
    raw, _ = catalog_store
    store.build('unused', updated_at='2026-11-11T20:00:00+00:00')
    repository = store.PublishedRepository()
    with sqlite3.connect(raw) as conn:
        conn.execute("UPDATE matched_entries SET title='Not published' WHERE entry_id='new'")
    assert repository.query({'ids': ['new']})['items'][0]['title'] == 'Paper new'
    assert repository.bootstrap()['dataUpdatedAt'] == '2026-11-11T20:00:00+00:00'


def test_requests_during_validation_keep_serving_old_version(catalog_store, monkeypatch):
    raw, _ = catalog_store
    store.build('unused')
    with sqlite3.connect(raw) as conn:
        conn.execute("UPDATE matched_entries SET title='New version' WHERE entry_id='new'")
    original_validate = store.validate
    def validate_while_reading(connection):
        assert store.PublishedRepository().query({'ids': ['new']})['items'][0]['title'] == 'Paper new'
        original_validate(connection)
    monkeypatch.setattr(store, 'validate', validate_while_reading)
    store.build('unused')
    assert store.PublishedRepository().query({'ids': ['new']})['items'][0]['title'] == 'New version'


def test_build_lock_rejects_overlapping_publication(catalog_store):
    import fcntl
    root = store.resolve_data_path('query-store')
    root.mkdir()
    with (root / 'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='already running'):
            store.build('unused')


def test_retention_keeps_active_connection_and_idle_reader_working(catalog_store):
    import os
    import time
    store.build('unused')
    old = store.PublishedRepository()
    active = old._connect()
    try:
        paths = [old.path]
        for _ in range(3):
            store.build('unused')
            paths.append(store.current_path())
        for index, path in enumerate(paths):
            stamp = time.time() - 259200 + index
            os.utime(path, (stamp, stamp))
        store.build('unused')
        assert not old.path.exists()
        assert active.execute('SELECT count(*) FROM papers').fetchone()[0] == 2
        assert old.query({})['total'] == 2
        assert len(list(store.current_path().parent.glob('query-*.db'))) == 3
    finally:
        active.close()

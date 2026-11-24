"""Safety and error handling of the isolated search-engine experiment."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


@pytest.fixture
def trial():
    spec = importlib.util.spec_from_file_location(
        'title_search_trial', Path(__file__).resolve().parents[1] / 'scripts/try_title_search.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_title_export_keeps_metadata_but_excludes_abstract(trial):
    with sqlite3.connect(':memory:') as connection:
        connection.execute('CREATE TABLE papers (id TEXT, payload TEXT)')
        connection.execute('INSERT INTO papers VALUES (?, ?)', ('private-original-id', json.dumps({
            'title': 'Attention Is All You Need', 'summary': 'Complete abstract',
            'source': 'NeurIPS 2017', 'url': 'https://example.org/paper',
        })))
        document, = trial.documents(connection)
        assert document['title'] == 'Attention Is All You Need'
        assert document['source'] == 'NeurIPS 2017'
        assert len(document['id']) == 64
        assert 'private-original-id' not in json.dumps(document)
        assert 'abstract' not in document
        abstract_document, = trial.documents(connection, with_abstract=True)
        assert abstract_document['abstract'] == 'Complete abstract'


@pytest.mark.parametrize('name', ['../outside.db', '/tmp/outside.db', 'unrelated.db'])
def test_rejects_unsafe_published_pointer(trial, tmp_path, name):
    directory = tmp_path / 'query-store'
    directory.mkdir()
    (directory / 'current.json').write_text(json.dumps({'database': name}))
    with pytest.raises(ValueError, match='Invalid published'):
        trial.published_database(tmp_path)


def test_task_failure_is_reported(trial, monkeypatch):
    client = trial.Client(7700, 'local-trial-key')
    monkeypatch.setattr(client, 'request', lambda *_: {
        'status': 'failed', 'error': {'message': 'Invalid document identifier'},
    })
    with pytest.raises(RuntimeError, match='Invalid document identifier'):
        client.task({'taskUid': 12})


def test_task_timeout_is_reported(trial, monkeypatch):
    client = trial.Client(7700, 'local-trial-key')
    clock = iter((0, 601))
    monkeypatch.setattr(trial.time, 'monotonic', lambda: next(clock))
    with pytest.raises(TimeoutError, match='ten minutes'):
        client.task({'taskUid': 12})


def test_local_client_does_not_use_environment_proxy(trial, monkeypatch):
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:9')
    handlers = trial.Client(7700, 'local-trial-key').opener.handlers
    assert not any(getattr(handler, 'proxies', {}) for handler in handlers)

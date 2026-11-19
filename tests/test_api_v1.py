"""Exercise the public HTTP contract with an isolated published catalog."""

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from paper_firehose import local_app, query_store
from test_query_store import catalog_store


@pytest.fixture
def application(catalog_store, tmp_path):
    query_store.build('unused')
    app = local_app.LocalApplication('unused', tmp_path)
    app.load_repository()
    return app


@contextmanager
def running(app):
    server = ThreadingHTTPServer(('127.0.0.1', 0), local_app._handler(app))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = HTTPConnection(*server.server_address, timeout=5)
    try:
        def get(path):
            connection.request('GET', path)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        yield get
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join()


def test_compact_list_and_independent_full_detail(application):
    with running(application) as get:
        status, data = get('/api/v1/papers?page_size=1')
        assert status == 200
        assert data['total'] == 2
        paper = data['items'][0]
        assert 'summary' not in paper and 'insight' not in paper
        assert paper['summaryPreview']
        assert len(paper['publicId']) == 64
        assert paper['publicId'] != paper['id']
        status, detail = get('/api/v1/papers/' + query_store.public_id('old'))
        assert status == 200
        assert detail['paper']['id'] == 'old'
        assert detail['paper']['summary'] == 'An abstract about infrastructure'
        assert detail['dataVersion'] == data['dataVersion']
        assert get('/api/v1/papers/old')[0] == 404
        assert get('/api/v1/papers/' + 'a' * 64)[0] == 404
        legacy = get('/api/papers')[1]
        assert 'summary' in legacy['items'][0]
        assert 'publicId' not in legacy['items'][0]


@pytest.mark.parametrize('query', ['page=-1', 'page=bad', 'page_size=0', 'days=-1', 'sort=sql', 'version=../other'])
def test_invalid_parameters_are_rejected(application, query):
    with running(application) as get:
        assert get('/api/v1/papers?' + query)[0] == 400


def test_pinned_version_survives_publication(application, catalog_store):
    with running(application) as get:
        old_version = get('/api/v1/bootstrap')[1]['dataVersion']
        with sqlite3.connect(catalog_store[0]) as conn:
            conn.execute("UPDATE matched_entries SET title='Changed title' WHERE entry_id='new'")
        query_store.build('unused')
        application.load_repository()
        assert get('/api/v1/papers')[1]['items'][0]['title'] == 'Changed title'
        pinned = get('/api/v1/papers?version=' + old_version)[1]
        assert pinned['items'][0]['title'] == 'Paper new'
        assert get('/api/v1/papers?version=' + '0' * 32)[0] == 409


def test_public_identity_and_old_snapshot_compatibility(application, catalog_store):
    before = hashlib.sha256(catalog_store[0].read_bytes()).hexdigest()
    repository = application.repository
    identifier = query_store.public_id('new')
    assert repository.detail(identifier)['title'] == 'Paper new'
    with sqlite3.connect(repository.path) as conn:
        plan = conn.execute('EXPLAIN QUERY PLAN SELECT paper_id FROM public_paper_ids WHERE public_id=?', (identifier,)).fetchall()
        assert any('SEARCH' in row[3] for row in plan)
        conn.execute('DROP TABLE public_paper_ids')
    assert repository.detail(identifier)['title'] == 'Paper new'
    query_store.build('unused')
    assert query_store.PublishedRepository().detail(identifier)['publicId'] == identifier
    assert hashlib.sha256(catalog_store[0].read_bytes()).hexdigest() == before


def test_count_cache_is_bounded(application):
    repository = application.repository
    for i in range(140):
        repository.query({'q': [str(i)]})
    assert len(repository._counts) == 128


def test_expired_pinned_repository_never_silently_changes_version(application):
    repository = application.repository
    query_store.build('unused')
    repository.path.unlink()
    with pytest.raises(FileNotFoundError):
        repository.query({}, pinned=True)
    # Legacy callers keep their existing fallback behavior.
    assert repository.query({})['total'] == 2


def test_only_one_previous_version_remains_and_older_api_version_returns_409(application):
    with running(application) as get:
        oldest = get('/api/v1/bootstrap')[1]['dataVersion']
        query_store.build('unused')
        application.load_repository()
        previous = get('/api/v1/bootstrap')[1]['dataVersion']
        query_store.build('unused')
        application.load_repository()
        assert get('/api/v1/papers?version=' + previous)[0] == 200
        assert get('/api/v1/papers?version=' + oldest)[0] == 409
        assert get('/api/v1/papers')[0] == 200

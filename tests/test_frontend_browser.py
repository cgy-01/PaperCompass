"""Opt-in Chrome smoke test; no browser or npm dependencies added to the project."""
import os
import sqlite3
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from paper_firehose import local_app, query_store
from paper_firehose.accounts import AccountAPI, Settings
from paper_firehose.commands import dashboard
from paper_firehose.core.paths import get_system_path
from test_query_store import catalog_store


@pytest.mark.skipif(not os.environ.get('PAPER_COMPASS_BROWSER'), reason='Optional Chrome executable not configured')
def test_browser_navigation(catalog_store, tmp_path, monkeypatch):
    monkeypatch.setenv('PAPER_FIREHOSE_DATA_DIR', str(tmp_path))
    # Exercise the full research directory with real labels/icons and isolated papers.
    catalog = catalog_store[1]
    infra = catalog[0]
    catalog[:] = []
    for key in dashboard.CURATED_TOPIC_KEYS:
        config = yaml.safe_load(get_system_path('config', 'topics', key + '.yaml').read_text(encoding='utf-8'))
        catalog.append(infra if key == 'ai_infra' else {
            'key': key, 'name': config['name'], 'description': config.get('description', ''),
            'icon': dashboard.TOPIC_ICONS[key], 'pattern': r'(?!)', 'min_matches': 1,
        })
    with sqlite3.connect(catalog_store[0]) as conn:
        fields = [row[1] for row in conn.execute('PRAGMA table_info(matched_entries)')]
        record = dict(zip(fields, conn.execute("SELECT * FROM matched_entries WHERE entry_id='new'").fetchone()))
        for i in range(35):
            record.update(entry_id=f'fixture-{i}', title=f'Paper {i}')
            conn.execute('INSERT INTO matched_entries VALUES (' + ','.join('?' for _ in fields) + ')', [record[key] for key in fields])
    query_store.build('unused')
    monkeypatch.setattr(dashboard, 'ConfigManager', query_store.ConfigManager)
    dashboard.run('unused', output=str(tmp_path / 'index.html'))
    app = local_app.LocalApplication('unused', tmp_path)
    app.load_repository()
    server = ThreadingHTTPServer(('127.0.0.1', 0), local_app._handler(app))
    app.accounts = AccountAPI(tmp_path, Settings(f'http://127.0.0.1:{server.server_port}', 'test-client', 'test-secret'))
    # Only the external provider is simulated; browser state, PKCE, callback,
    # session cookies, collections and logout use the real HTTP handlers.
    monkeypatch.setattr(app.accounts, '_profile', lambda code, verifier: {
        'id': 42, 'login': 'browser-researcher'})
    session_token = app.accounts.users.login({'id': 42, 'login': 'browser-researcher'})
    other_session = app.accounts.users.login({'id': 43, 'login': 'another-researcher'})
    other_id = app.accounts.users.user(other_session)['id']
    app.accounts.users.set_ids(other_id, 'favorites', ['fixture-0'])
    user_id = app.accounts.users.user(session_token)['id']
    app.accounts.users.set_ids(user_id, 'favorites', ['new'])
    monkeypatch.setenv('PAPER_COMPASS_TEST_LOGIN', session_token)
    monkeypatch.setenv('PAPER_COMPASS_TEST_OTHER_LOGIN', other_session)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        subprocess.run(['node', str(Path(__file__).with_name('frontend_browser.mjs')),
                        f'http://127.0.0.1:{server.server_port}'], check=True, timeout=60)
        with sqlite3.connect(app.accounts.events.path) as conn:
            assert conn.execute('SELECT count(*) FROM events WHERE user_id=?', (user_id,)).fetchone()[0] > 0
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.skipif(not os.environ.get('PAPER_COMPASS_BROWSER'), reason='Optional Chrome executable not configured')
def test_browser_local_developer_without_github(catalog_store, tmp_path, monkeypatch):
    monkeypatch.setenv('PAPER_FIREHOSE_DATA_DIR', str(tmp_path))
    query_store.build('unused')
    monkeypatch.setattr(dashboard, 'ConfigManager', query_store.ConfigManager)
    dashboard.run('unused', output=str(tmp_path / 'index.html'))
    app = local_app.LocalApplication('unused', tmp_path)
    app.load_repository()
    server = ThreadingHTTPServer(('127.0.0.1', 0), local_app._handler(app))
    app.accounts = AccountAPI(tmp_path, Settings(
        f'http://127.0.0.1:{server.server_port}', events_enabled=False, dev_login=True))
    monkeypatch.setenv('PAPER_COMPASS_TEST_DEV_LOGIN', '1')
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        subprocess.run(['node', str(Path(__file__).with_name('frontend_browser.mjs')),
                        f'http://127.0.0.1:{server.server_port}'], check=True, timeout=60)
        assert (tmp_path / 'users' / 'development.db').is_file()
        assert not (tmp_path / 'users' / 'users.db').exists()
    finally:
        server.shutdown()
        server.server_close()
        worker.join()

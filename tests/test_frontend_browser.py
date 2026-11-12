"""Opt-in Chrome smoke test; no browser or npm dependencies added to the project."""
import os
import sqlite3
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from paper_firehose import local_app, query_store
from paper_firehose.commands import dashboard
from test_query_store import catalog_store


@pytest.mark.skipif(not os.environ.get('PAPER_COMPASS_BROWSER'), reason='Optional Chrome executable not configured')
def test_browser_navigation(catalog_store, tmp_path, monkeypatch):
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
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        subprocess.run(['node', str(Path(__file__).with_name('frontend_browser.mjs')),
                        f'http://127.0.0.1:{server.server_port}'], check=True, timeout=60)
    finally:
        server.shutdown()
        server.server_close()
        worker.join()

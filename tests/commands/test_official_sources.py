from paper_firehose.commands.official_sources import _ProceedingsParser
import sqlite3
import logging
from unittest.mock import Mock
import pytest


@pytest.mark.parametrize('interrupt', [False, True])
@pytest.mark.parametrize('strategy', ['keywords', 'api'])
def test_sync_commits_batches_and_reports_progress(tmp_path, monkeypatch, caplog, interrupt, strategy):
    from paper_firehose.commands import official_sources as source
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', strategy)
    manager = Mock()
    manager.get_available_topics.return_value = ['llm']
    manager.load_topic_config.return_value = {'filter': {'pattern': 'LLM'}}
    monkeypatch.setattr(source, 'ConfigManager', Mock(return_value=manager))
    database = tmp_path / 'batch.db'
    conn = sqlite3.connect(database)
    conn.execute('CREATE TABLE processed (id INTEGER PRIMARY KEY)')
    db = Mock()
    db.get_connection.return_value = conn
    monkeypatch.setattr(source, 'DatabaseManager', Mock(return_value=db))
    monkeypatch.setattr(source, 'PMLR_VOLUMES', {'https://official.test/': ('ICML 2026', '2026-07-01')})
    monkeypatch.setattr(source, 'OFFICIAL_INDEXES', {})
    monkeypatch.setattr(source, '_fetch_html', lambda *args: [
        {'title': 'LLM paper', 'url': 'https://official.test/' + str(index)} for index in range(201)])
    calls = []
    def upsert(connection, work, key, **kwargs):
        calls.append(key)
        if interrupt and len(calls) == 201:
            raise RuntimeError('interrupted')
        connection.execute('INSERT INTO processed VALUES (?)', (len(calls),))
        return 'inserted'
    monkeypatch.setattr(source, '_upsert', upsert)
    caplog.set_level(logging.INFO)
    if interrupt:
        with pytest.raises(RuntimeError, match='interrupted'):
            source.run('unused', enrich_metadata=False)
    else:
        assert source.run('unused', enrich_metadata=False)['inserted'] == 201
    with sqlite3.connect(database) as check:
        assert check.execute('SELECT count(*) FROM processed').fetchone()[0] == (200 if interrupt else 201)
    assert '200/201' in caplog.text
    assert set(calls) == ({'ccf_a_all'} if strategy == 'api' else {'llm'})
    manager.load_topic_config.assert_called_once_with('llm')
    conn.close()


def _items(base: str, mode: str, html: str):
    parser = _ProceedingsParser(base, mode)
    parser.feed(html)
    return parser.items


def test_iclr_parser_reads_official_accepted_papers_only():
    items = _items(
        "https://iclr.cc/virtual/2025/papers.html",
        "iclr",
        '<a href="/virtual/2025/poster/123">An Accepted Paper</a>'
        '<a href="/virtual/2025/events/invited-talk">An Invited Talk</a>',
    )
    assert items == [{"title": "An Accepted Paper", "url": "https://iclr.cc/virtual/2025/poster/123"}]


def test_mlsys_parser_supports_old_and_new_url_formats():
    html = (
        '<a href="/paper_files/paper/2023/hash/a-Abstract-mlsys2023.html">Old Format Paper</a>'
        '<a href="/paper_files/paper/2025/hash/b-Abstract-Conference.html">New Format Paper</a>'
    )
    assert len(_items("https://proceedings.mlsys.org/", "mlsys", html)) == 2


def test_usenix_parser_requires_paper_heading_from_same_conference():
    html = (
        '<article class="node node-paper"><h2><a href="/conference/osdi25/presentation/a">'
        'A Systems Paper</a></h2></article>'
        '<article class="node node-paper"><h2><a href="/conference/atc25/presentation/keynote">'
        'A Co-located Keynote</a></h2></article>'
    )
    items = _items("https://www.usenix.org/conference/osdi25/technical-sessions", "usenix", html)
    assert items == [{"title": "A Systems Paper", "url": "https://www.usenix.org/conference/osdi25/presentation/a"}]

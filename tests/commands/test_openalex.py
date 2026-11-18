import sqlite3

from paper_firehose.commands.openalex import _abstract, _source, _upsert
from unittest.mock import Mock
import pytest


@pytest.mark.parametrize('paginate,expected', [(False, 1), (True, 2)])
def test_optional_cursor_pagination_preserves_default(tmp_path, monkeypatch, paginate, expected):
    from paper_firehose.commands import openalex
    manager = Mock()
    manager.get_available_topics.return_value = ['llm']
    manager.load_topic_config.return_value = {'ranking': {'query': 'LLM'}, 'filter': {'pattern': 'LLM'}}
    monkeypatch.setattr(openalex, 'ConfigManager', Mock(return_value=manager))
    conn = sqlite3.connect(':memory:')
    db = Mock()
    db.get_connection.return_value = conn
    monkeypatch.setattr(openalex, 'DatabaseManager', Mock(return_value=db))
    request = Mock(side_effect=[
        {'results': [{'title': 'LLM one'}], 'meta': {'next_cursor': 'next'}},
        {'results': [{'title': 'LLM two'}], 'meta': {}},
    ])
    monkeypatch.setattr(openalex, '_request', request)
    monkeypatch.setattr(openalex, '_upsert', Mock(return_value='inserted'))
    assert openalex.run('unused', paginate=paginate)['inserted'] == expected
    assert request.call_count == expected
    conn.close()


def test_reconstructs_abstract_and_classifies_source():
    assert _abstract({"world": [1], "hello": [0]}) == "hello world"
    assert _source({"primary_location": {"source": {"display_name": "ICML", "type": "conference"}}}) == ("ICML", "会议")


def test_explicit_custom_topic_outside_website_menu_is_still_supported(monkeypatch):
    from paper_firehose.commands import openalex
    manager = Mock()
    manager.get_available_topics.return_value = ['custom']
    manager.load_topic_config.return_value = {'name': 'Custom', 'filter': {'pattern': r'\bcustom\b'}}
    monkeypatch.setattr(openalex, 'ConfigManager', Mock(return_value=manager))
    conn = sqlite3.connect(':memory:')
    database = Mock()
    database.get_connection.return_value = conn
    monkeypatch.setattr(openalex, 'DatabaseManager', Mock(return_value=database))
    monkeypatch.setattr(openalex, '_request', lambda _: {'results': [{'title': 'Custom research'}]})
    upsert = Mock(return_value='inserted')
    monkeypatch.setattr(openalex, '_upsert', upsert)
    try:
        assert openalex.run('unused', topic='custom')['inserted'] == 1
        assert upsert.call_args.args[2] == 'custom'
    finally:
        conn.close()


def test_upsert_adds_formal_venue_to_existing_preprint():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE matched_entries (
        entry_id TEXT PRIMARY KEY, feed_name TEXT, topics TEXT, title TEXT, link TEXT,
        summary TEXT, authors TEXT, abstract TEXT, doi TEXT, published_date TEXT,
        matched_date TEXT, rank_score REAL, venue_name TEXT, venue_type TEXT,
        external_id TEXT, cited_by_count INTEGER)""")
    conn.execute("INSERT INTO matched_entries (entry_id,feed_name,topics,title,link,doi) VALUES (?,?,?,?,?,?)",
                 ("arxiv-1", "arXiv cs.AI", "llm", "A Great Language Model", "https://arxiv.org/x", ""))
    work = {
        "id": "https://openalex.org/W1", "title": "A Great Language Model",
        "doi": "https://doi.org/10.1/example", "publication_date": "2025-07-01",
        "primary_location": {"source": {"display_name": "ICML 2025", "type": "conference"}},
        "cited_by_count": 12, "authorships": [], "abstract_inverted_index": {},
    }
    assert _upsert(conn, work, "llm") == "updated"
    row = conn.execute("SELECT * FROM matched_entries").fetchone()
    assert row["venue_type"] == "会议"
    assert row["venue_name"] == "ICML 2025"
    assert row["external_id"] == "https://openalex.org/W1"


def test_official_repeat_uses_primary_key_and_does_not_rewrite_metadata():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE matched_entries (
        entry_id TEXT PRIMARY KEY, feed_name TEXT, topics TEXT, title TEXT, link TEXT,
        summary TEXT, authors TEXT, abstract TEXT, doi TEXT, published_date TEXT,
        matched_date TEXT, rank_score REAL, venue_name TEXT, venue_type TEXT,
        external_id TEXT, cited_by_count INTEGER)''')
    work = {'id': 'https://official.test/paper/123', 'title': 'An LLM paper',
            'publication_date': '2026-01-01', 'authorships': [],
            'primary_location': {'landing_page_url': 'https://official.test/paper/123',
                                 'source': {'display_name': 'ICML 2026', 'type': 'conference'}}}
    assert _upsert(conn, work, 'llm', dedupe_title=False) == 'inserted'
    conn.execute("UPDATE matched_entries SET authors='Ada', abstract='Full abstract', summary='Full abstract'")
    statements = []
    conn.set_trace_callback(statements.append)
    before = conn.total_changes
    assert _upsert(conn, work, 'llm', dedupe_title=False) == 'skipped'
    assert conn.total_changes == before
    assert not any(' OR ' in sql for sql in statements)
    assert not any(sql.startswith('UPDATE') for sql in statements)
    assert _upsert(conn, work, 'ai_infra', dedupe_title=False) == 'updated'
    row = conn.execute('SELECT * FROM matched_entries').fetchone()
    assert row['topics'] == 'ai_infra, llm'
    assert row['authors'] == 'Ada'
    assert row['abstract'] == 'Full abstract'
    conn.close()

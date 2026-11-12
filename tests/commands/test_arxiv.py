import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock
from xml.sax.saxutils import escape

import pytest
import requests

from paper_firehose.commands import arxiv
from paper_firehose.core.database import DatabaseManager


def entry(base='2611.12345', version=1, title='LLM systems', updated=None):
    stamp = updated or datetime.now(timezone.utc).isoformat()
    return {'id': f'https://arxiv.org/abs/{base}v{version}', 'link': f'https://arxiv.org/abs/{base}v{version}',
            'title': title, 'summary': 'LLM infrastructure research', 'authors': [{'name': 'Ada'}],
            'published': '2026-11-10T00:00:00Z', 'updated': stamp}


def response(entries=(), *, total=None):
    metadata = f'<opensearch:totalResults>{total}</opensearch:totalResults>' if total is not None else ''
    contents = ''.join(f'''<entry><id>{escape(e['id'])}</id><title>{escape(e['title'])}</title>
        <summary>{escape(e['summary'])}</summary><published>{e['published']}</published>
        <updated>{e['updated']}</updated><author><name>Ada</name></author></entry>''' for e in entries)
    return Mock(content=f'''<feed xmlns="http://www.w3.org/2005/Atom"
        xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
        <title>arXiv recent papers</title>{metadata}{contents}</feed>'''.encode())


@pytest.fixture
def environment(tmp_path, monkeypatch):
    config = {'database': {'path': str(tmp_path / 'current.db'),
                          'history_path': str(tmp_path / 'history.db'),
                          'all_feeds_path': str(tmp_path / 'feeds.db')}}
    manager = Mock()
    manager.validate_config.return_value = True
    manager.load_config.return_value = config
    manager.get_available_topics.return_value = ['llm']
    manager.get_enabled_feeds.return_value = {'ai': {'url': 'https://rss.arxiv.org/rss/cs.AI', 'name': 'arXiv cs.AI'}}
    manager.load_topic_config.return_value = {'feeds': ['ai'], 'filter': {'pattern': 'LLM', 'min_matches': 1}}
    monkeypatch.setattr(arxiv, 'ConfigManager', Mock(return_value=manager))
    monkeypatch.setattr(arxiv, 'resolve_data_path', lambda name: tmp_path / name)
    client = MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr(arxiv, 'RetryableHTTPClient', Mock(return_value=client))
    db = DatabaseManager(config)
    yield client, db, tmp_path, manager
    db.close_all_connections()


def test_stable_id_not_title_deduplication(environment):
    client, db, _, _ = environment
    client.get_with_retry.return_value = response([entry(), entry('2611.12346')])
    assert arxiv.run('unused', catch_up=False)['inserted'] == 2
    assert arxiv.run('unused', catch_up=False)['inserted'] == 0
    with db.get_connection('history') as conn:
        assert conn.execute('SELECT count(*) FROM matched_entries').fetchone()[0] == 2


def test_version_updates_keep_legacy_id_date_and_score(environment):
    client, db, _, _ = environment
    with db.get_connection('history') as conn:
        conn.execute('''INSERT INTO matched_entries
            (entry_id,feed_name,topics,title,link,summary,authors,abstract,published_date,rank_score)
            VALUES ('legacy-sha','arXiv cs.AI','llm','LLM old','https://arxiv.org/abs/2611.12345v1',
                    'LLM old','Ada','LLM old','2024-01-01',0.8)''')
    client.get_with_retry.return_value = response([entry(version=2, title='LLM new title')])
    assert arxiv.run('unused', catch_up=False)['updated'] == 1
    client.get_with_retry.return_value = response([entry(version=1, title='LLM stale')])
    assert arxiv.run('unused', catch_up=False)['updated'] == 0
    with db.get_connection('history') as conn:
        rows = conn.execute('SELECT * FROM matched_entries').fetchall()
        assert len(rows) == 1
        assert rows[0]['entry_id'] == 'legacy-sha'
        assert rows[0]['title'] == 'LLM new title'
        assert rows[0]['published_date'] == '2024-01-01'
        assert rows[0]['rank_score'] == .8


def test_invalid_response_is_not_successful_zero(environment):
    client, _, path, _ = environment
    client.get_with_retry.return_value = Mock(content=b'<html>service unavailable</html>')
    result = arxiv.run('unused', catch_up=False)
    assert result['failed'] == 1
    assert not result['sources']['cs.AI']['rss']['ok']
    assert not (path / 'arxiv-sync-state.json').exists()


def test_valid_empty_feed_is_successful(environment):
    client, _, path, _ = environment
    client.get_with_retry.return_value = response()
    result = arxiv.run('unused', catch_up=False)
    assert result['failed'] == 0
    assert result['sources']['cs.AI']['rss']['empty']
    assert json.loads((path / 'arxiv-sync-state.json').read_text())['sources']['cs.AI']['rss_success']


def test_api_failure_preserves_watermark(environment):
    client, _, path, _ = environment
    previous = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    (path / 'arxiv-sync-state.json').write_text(json.dumps({'sources': {'cs.AI': {'api_success': previous}}, 'versions': {}}))
    client.get_with_retry.side_effect = requests.Timeout('offline')
    assert arxiv.run('unused', rss=False)['failed'] == 1
    assert json.loads((path / 'arxiv-sync-state.json').read_text())['sources']['cs.AI']['api_success'] == previous


def test_api_recovers_recent_revision_of_old_submission(environment):
    client, db, _, _ = environment
    old = entry('1801.12345', version=3)
    old['published'] = '2018-01-01T00:00:00Z'
    client.get_with_retry.return_value = response([old], total=1)
    result = arxiv.run('unused', rss=False)
    assert result['inserted'] == 1
    assert client.get_with_retry.call_args.kwargs['params']['sortBy'] == 'lastUpdatedDate'
    with db.get_connection('history') as conn:
        assert conn.execute('SELECT published_date FROM matched_entries').fetchone()[0] == '2018-01-01T00:00:00+00:00'


def test_api_pagination_and_boundary():
    client = Mock()
    now = datetime.now(timezone.utc)
    first = [entry(f'2611.{10000+i}') for i in range(200)]
    old = entry('2001.10000', updated=(now - timedelta(days=9)).isoformat())
    client.get_with_retry.side_effect = [response(first, total=100000), response([entry('2611.20001'), old], total=100000)]
    result = arxiv._api_entries(client, 'cs.AI', now - timedelta(days=7), now + timedelta(seconds=1))
    assert len(result) == 201
    assert client.get_with_retry.call_args.kwargs['params']['start'] == 200


def test_identity_supports_oai_and_versions():
    assert arxiv.identity({'id': 'oai:arXiv.org:2611.12345', 'summary': 'arXiv:2611.12345v2 Announce Type: replace'}) == ('2611.12345', 2)
    assert arxiv.identity({'link': 'https://arxiv.org/pdf/hep-th/9901001v3.pdf'}) == ('hep-th/9901001', 3)


def test_rss_rfc822_date_is_normalized_for_time_sorting(environment):
    client, db, _, _ = environment
    paper = entry()
    paper['published'] = 'Thu, 12 Nov 2026 00:00:00 GMT'
    client.get_with_retry.return_value = response([paper])
    assert arxiv.run('unused', catch_up=False)['inserted'] == 1
    with db.get_connection('history') as conn:
        assert conn.execute('SELECT published_date FROM matched_entries').fetchone()[0] == '2026-11-12T00:00:00+00:00'

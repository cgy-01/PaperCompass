"""API failures, paid-call bounds, resumability and raw/publication isolation."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import requests
from click.testing import CliRunner

from paper_firehose import api_classification as api, query_store
from paper_firehose import classification_store as storage
from paper_firehose.commands import classify_papers as command
from paper_firehose.cli import cli

PAPER = {'title': 'Efficient LLM serving',
         'abstract': 'We introduce a serving system that schedules GPU workloads and reduces KV cache memory.'}


def answer(paper=PAPER):
    return {'index': 1, **paper, 'topic_key': 'ai_infra', 'topic_name': 'AI Infra',
            'confidence': 'high', 'needs_review': False,
            'reason': '主要贡献是面向大模型推理的系统调度与缓存管理。',
            'evidence': paper['abstract']}


def compact_answer(paper=PAPER):
    return {k: v for k, v in {**answer(paper), 'decision': 'classified'}.items()
            if k in {'index', 'decision', 'topic_key', 'confidence', 'reason', 'evidence'}}


def response(paper=PAPER, *, status=200, finish='stop', content=None):
    result = Mock(status_code=status, content=b'bounded response')
    result.json.return_value = {
        'choices': [{'finish_reason': finish, 'message': {
            'content': content if content is not None else json.dumps({'papers': [compact_answer(paper)]})}}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}}
    return result


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'now', lambda: datetime(2026, 11, 18, 10, 10, tzinfo=timezone.utc))
    for name in ('PAPER_COMPASS_CLASSIFIER', 'PAPER_COMPASS_CLASSIFICATION_BASE_URL',
                 'PAPER_COMPASS_CLASSIFICATION_MODEL', 'PAPER_COMPASS_CLASSIFICATION_TIMEOUT',
                 'PAPER_COMPASS_CLASSIFICATION_MAX_TOKENS', 'PAPER_COMPASS_CLASSIFICATION_REASONING_EFFORT'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_API_KEY', 'test-only-not-a-secret')
    def path(*parts, **kwargs):
        target = tmp_path.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
    monkeypatch.setattr(api, 'resolve_data_path', path)
    monkeypatch.setattr(command, 'resolve_data_path', path)
    monkeypatch.setattr(query_store, 'resolve_data_path', path)
    post = Mock(return_value=response())
    monkeypatch.setattr(api.requests, 'post', post)
    return post


def test_request_uses_reviewed_prompt_full_text_and_json_mode(isolated):
    settings = api.Settings.from_env()
    result = api.APIClassifier(settings).classify(PAPER)
    assert result['topic_key'] == 'ai_infra'
    assert result['usage']['total_tokens'] == 150
    args, kwargs = isolated.call_args
    assert args == ('https://api.deepseek.com/chat/completions',)
    assert kwargs['allow_redirects'] is False
    assert kwargs['timeout'] == (10, 60)
    payload = kwargs['json']
    assert payload['response_format'] == {'type': 'json_object'}
    assert payload['reasoning_effort'] == 'none'
    assert json.loads(payload['messages'][1]['content']) == [PAPER]
    assert '每篇论文仍只输出一个最可能的主题' in payload['messages'][0]['content']
    assert all(key in payload['messages'][0]['content'] for key in api.TOPICS)
    assert 'test-only-not-a-secret' not in json.dumps(result)


def test_unsupported_provider_can_omit_reasoning_parameter(isolated, monkeypatch):
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_REASONING_EFFORT', 'auto')
    settings = api.Settings.from_env()
    api.APIClassifier(settings).classify(PAPER)
    assert 'reasoning_effort' not in isolated.call_args.kwargs['json']


def test_thinking_mode_changes_cache_policy(monkeypatch):
    settings = api.Settings.from_env()
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_REASONING_EFFORT', 'high')
    assert api.Settings.from_env().cache_key(PAPER) != settings.cache_key(PAPER)
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_REASONING_EFFORT', 'invalid')
    with pytest.raises(ValueError, match='reasoning'):
        api.Settings.from_env()


def test_provider_cache_and_reasoning_usage_are_kept_for_cost_auditing(isolated):
    isolated.return_value.json.return_value['usage'].update({
        'prompt_cache_hit_tokens': 80, 'prompt_cache_miss_tokens': 20,
        'completion_tokens_details': {'reasoning_tokens': 0}, 'ignored_provider_field': 'private'})
    usage = api.APIClassifier(api.Settings.from_env()).classify(PAPER)['usage']
    assert usage['prompt_cache_hit_tokens'] == 80
    assert usage['prompt_cache_miss_tokens'] == 20
    assert usage['reasoning_tokens'] == 0
    assert 'ignored_provider_field' not in usage


@pytest.mark.parametrize('change', [
    {'topic_key': 'other'}, {'topic_key': ['ai_infra']}, {'topic_name': 'wrong'},
    {'index': True}, {'index': 2}, {'title': 'wrong paper'}, {'abstract': 'truncated'},
    {'confidence': '0.9'}, {'needs_review': 0}, {'confidence': 'low'},
    {'evidence': 'invented evidence'}, {'evidence': None}, {'reason': ''},
])
def test_invalid_decisions_are_rejected(change):
    result = {**answer(), **change}
    with pytest.raises(api.ClassificationError):
        api.validate_result(json.dumps({'papers': [result]}), PAPER)


@pytest.mark.parametrize('payload', [' ', '```json\n{}\n```', '[]', '{}',
                                     '{"papers": []}', '{"papers": [null]}'])
def test_invalid_envelopes(payload):
    with pytest.raises(api.ClassificationError):
        api.validate_result(payload, PAPER)


def test_low_confidence_and_missing_evidence_require_explicit_review():
    result = {**answer(), 'confidence': 'low', 'needs_review': True, 'evidence': None}
    assert api.validate_result(json.dumps({'papers': [result]}), PAPER)['needs_review']


def test_network_failures_do_not_trigger_hidden_paid_retries(isolated):
    isolated.side_effect = requests.Timeout('sensitive provider error')
    with pytest.raises(api.ClassificationError):
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    assert isolated.call_count == 1


@pytest.mark.parametrize('status', [301, 400, 401, 402, 403, 404])
def test_fatal_http_errors_do_not_retry_or_leak_body(isolated, status):
    isolated.return_value = response(status=status)
    with pytest.raises(api.FatalAPIError, match=f'HTTP {status}') as exc:
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    assert isolated.call_count == 1
    assert 'test-only-not-a-secret' not in str(exc.value)


@pytest.mark.parametrize('status', [429, 500, 503])
def test_exhausted_transient_errors_are_safe(isolated, status):
    isolated.return_value = response(status=status)
    with pytest.raises(api.ClassificationError):
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    assert isolated.call_count == 1


def test_timeout_exception_text_is_not_exposed(isolated):
    isolated.side_effect = requests.Timeout('Bearer test-only-not-a-secret')
    with pytest.raises(api.ClassificationError) as exc:
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    assert 'test-only-not-a-secret' not in str(exc.value)


@pytest.mark.parametrize('finish,content', [('length', None), ('stop', ' '), ('stop', '{"papers": []}')])
def test_empty_truncated_or_invalid_successes_are_not_cached(isolated, finish, content):
    isolated.return_value = response(finish=finish, content=content)
    with pytest.raises(api.ClassificationError):
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)


def test_oversize_or_missing_abstract_never_calls_provider(isolated):
    client = api.APIClassifier(api.Settings.from_env())
    for abstract in ('', '暂无摘要。', 'x' * 60001):
        with pytest.raises(ValueError):
            client.classify({**PAPER, 'abstract': abstract})
    isolated.assert_not_called()


def test_settings_and_policy_invalidate_cache_without_including_key(monkeypatch):
    original = api.Settings.from_env()
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_API_KEY', 'different-key')
    assert api.Settings.from_env().cache_key(PAPER) == original.cache_key(PAPER)
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_MODEL', 'other-model')
    assert api.Settings.from_env().cache_key(PAPER) != original.cache_key(PAPER)
    assert original.cache_key({**PAPER, 'abstract': 'new text'}) != original.cache_key(PAPER)
    assert api.digest(original.system_prompt + 'changed') != api.digest(original.system_prompt)


@pytest.mark.parametrize('url', ['http://example.test', 'https://user:password@example.test',
                               'https://example.test?key=secret', 'https://example.test#fragment'])
def test_unsafe_base_url_rejected(monkeypatch, url):
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_BASE_URL', url)
    with pytest.raises(ValueError):
        api.Settings.from_env()


def test_json_export_preserves_order_reuses_cache_and_does_not_touch_input(tmp_path, isolated):
    source, output = tmp_path / 'sample.json', tmp_path / 'results.json'
    source.write_text(json.dumps([PAPER, PAPER]))
    before = source.read_bytes()
    report = command.classify_json(source, output)
    assert report['classified'] == 2 and report['cached'] == 1
    assert isolated.call_count == 1
    results = json.loads(output.read_text())
    assert [p['index'] for p in results] == [1, 2]
    assert all(p['title'] == PAPER['title'] and p['abstract'] == PAPER['abstract'] for p in results)
    assert source.read_bytes() == before
    with pytest.raises(ValueError, match='already exists'):
        command.classify_json(source, output)
    assert isolated.call_count == 1
    assert command.classify_json(source, tmp_path / 'results-again.json')['cached'] == 2


def test_json_validates_whole_batch_before_spending(tmp_path, isolated):
    source = tmp_path / 'input.json'
    source.write_text(json.dumps([PAPER, {'title': 'missing abstract'}]))
    with pytest.raises(ValueError):
        command.classify_json(source, tmp_path / 'output.json')
    isolated.assert_not_called()


def test_json_fatal_error_stops_remaining_calls_and_marks_pending(tmp_path, isolated):
    source, output = tmp_path / 'input.json', tmp_path / 'output.json'
    source.write_text(json.dumps([PAPER, {**PAPER, 'title': 'Second'}]))
    isolated.return_value = response(status=401)
    report = command.classify_json(source, output)
    assert not report['ok'] and report['failed'] == 2
    assert isolated.call_count == 1
    assert all(p['status'] == 'pending' and 'topic_key' not in p for p in json.loads(output.read_text()))


@pytest.fixture
def raw_store(tmp_path, monkeypatch):
    raw = tmp_path / 'raw.db'
    with sqlite3.connect(raw) as conn:
        conn.execute('''CREATE TABLE matched_entries (
            entry_id TEXT PRIMARY KEY, feed_name TEXT, topics TEXT, title TEXT, link TEXT,
            summary TEXT, authors TEXT, abstract TEXT, doi TEXT, published_date TEXT,
            matched_date TEXT, llm_summary TEXT, paper_qa_summary TEXT, rank_score REAL,
            venue_name TEXT, venue_type TEXT, external_id TEXT, cited_by_count INTEGER)''')
    manager = Mock()
    manager.validate_config.return_value = True
    manager.load_config.return_value = {'database': {'history_path': str(raw)}}
    monkeypatch.setattr(query_store, 'ConfigManager', Mock(return_value=manager))
    monkeypatch.setattr(command, 'ConfigManager', Mock(return_value=manager))
    catalog = [{'key': key, 'name': name, 'description': '', 'icon': 'cpu',
                'pattern': r'\b' + ('serving' if key == 'ai_infra' else key) + r'\b', 'min_matches': 1}
               for key, name in api.TOPICS.items()]
    monkeypatch.setattr(query_store.dashboard, '_topic_catalog', lambda _: catalog)
    def insert(identifier, paper=PAPER):
        with sqlite3.connect(raw) as conn:
            conn.execute('''INSERT INTO matched_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                identifier, 'ICLR 2026', '', paper['title'], 'https://example.test/' + identifier,
                paper['abstract'], 'Ada', paper['abstract'], '', '2026-11-18', '2026-11-18',
                '', '', .8, 'ICLR 2026', '会议', 'https://official.test/' + identifier, 0))
    return raw, insert


def test_incremental_api_classifies_only_new_papers_and_publishes_single_topic(raw_store, monkeypatch, isolated):
    raw, insert = raw_store
    insert('old')
    query_store.build('unused')
    old_payload = query_store.PublishedRepository().query({'ids': ['old']})['items'][0]
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    command.prepare_scope('unused')
    insert('new')
    before = hashlib.sha256(raw.read_bytes()).hexdigest()
    report = command.run('unused')
    assert report['classified'] == 1 and report['ok']
    assert isolated.call_count == 1
    assert query_store.build('unused')['changed'] == 1
    repo = query_store.PublishedRepository()
    paper = repo.query({'ids': ['new']})['items'][0]
    assert paper['topicKeys'] == ['ai_infra'] and paper['topics'] == ['AI Infra']
    assert repo.query({'ids': ['old']})['items'][0] == old_payload
    assert command.run('unused')['classified'] == 0
    assert query_store.build('unused')['changed'] == 0
    assert isolated.call_count == 1
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == before


def test_failed_job_retries_even_after_publication_advanced(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('old')
    query_store.build('unused')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    command.prepare_scope('unused')
    insert('new')
    isolated.return_value = response(status=503)
    report = command.run('unused')
    assert report['failed'] == 1 and report['pending'] == 1
    query_store.build('unused')
    assert query_store.PublishedRepository().query({'ids': ['new']})['items'][0]['topics'] == []
    isolated.return_value = response()
    assert command.run('unused')['classified'] == 0  # Backoff survives publication.
    with api.ResultStore().conn as conn:
        conn.execute("UPDATE jobs SET next_attempt=''")
    assert command.run('unused')['classified'] == 1
    assert query_store.build('unused')['changed'] == 1
    assert query_store.PublishedRepository().query({'ids': ['new']})['items'][0]['topicKeys'] == ['ai_infra']


def test_manual_build_before_classification_does_not_lose_pending_work(raw_store, monkeypatch):
    _, insert = raw_store
    insert('old')
    query_store.build('unused')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    command.prepare_scope('unused')
    insert('new')
    query_store.build('unused')
    assert query_store.PublishedRepository().query({'ids': ['new']})['items'][0]['topics'] == []
    assert command.run('unused')['classified'] == 1
    query_store.build('unused')
    assert query_store.PublishedRepository().query({'ids': ['new']})['items'][0]['topicKeys'] == ['ai_infra']


def test_api_switch_does_not_spend_on_existing_history_or_need_key_to_publish(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('old')
    query_store.build('unused')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    assert command.run('unused')['classified'] == 0
    isolated.assert_not_called()
    monkeypatch.delenv('PAPER_COMPASS_CLASSIFICATION_API_KEY')
    assert query_store.build('unused')['changed'] == 0


def test_all_papers_is_explicit_and_cache_is_shared_across_source_ids(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('a')
    insert('b')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    assert command.run('unused')['classified'] == 0
    isolated.assert_not_called()
    report = command.run('unused', all_papers=True)
    assert report['classified'] == 2 and report['cached'] == 1
    assert isolated.call_count == 1
    query_store.build('unused')
    assert command.run('unused')['classified'] == 0


def test_text_change_invalidates_assignment_without_serving_stale_label(raw_store, monkeypatch, isolated):
    raw, insert = raw_store
    command.prepare_scope('unused')
    insert('a')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    command.run('unused', all_papers=True)
    query_store.build('unused')
    changed = {**PAPER, 'abstract': 'We introduce an entirely different GPU serving system.'}
    with sqlite3.connect(raw) as conn:
        conn.execute('UPDATE matched_entries SET abstract=?,summary=?', (changed['abstract'], changed['abstract']))
    isolated.return_value = response(changed)
    assert command.run('unused')['classified'] == 1
    assert isolated.call_count == 2
    query_store.build('unused')
    assert query_store.PublishedRepository().query({})['items'][0]['summary'] == changed['abstract']


def test_keyword_fallback_is_manual_and_replaces_only_api_decisions(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('a')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    command.run('unused', all_papers=True)
    query_store.build('unused')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'keywords')
    assert query_store.build('unused')['changed'] == 1
    assert isolated.call_count == 1


def test_manual_keyword_fallback_also_classifies_failed_api_candidates(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('old')
    query_store.build('unused')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    command.prepare_scope('unused')
    insert('new')
    isolated.return_value = response(status=401)
    assert command.run('unused')['failed'] == 1
    query_store.build('unused')
    assert query_store.PublishedRepository().query({'ids': ['new']})['items'][0]['topics'] == []
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'keywords')
    assert query_store.build('unused')['changed'] == 1
    assert query_store.PublishedRepository().query({'ids': ['new']})['items'][0]['topicKeys'] == ['ai_infra', 'llm']


def test_limit_is_durable_and_missing_abstract_does_not_starve_other_jobs(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    command.prepare_scope('unused')
    insert('a', {**PAPER, 'abstract': ''})
    insert('b')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    report = command.run('unused', all_papers=True, limit=1)
    assert report['waiting_metadata'] == 1 and report['pending'] == 1
    isolated.assert_not_called()
    query_store.build('unused')
    report = command.run('unused', limit=1)
    assert report['classified'] == 1 and report['pending'] == 0
    assert isolated.call_count == 1


def test_api_publication_is_network_free_and_never_runs_keywords(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('a')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    command.run('unused', all_papers=True)
    isolated.reset_mock()
    monkeypatch.setattr(query_store.dashboard, 'TopicClassifier', Mock(side_effect=AssertionError('keywords invoked')))
    query_store.build('unused')
    assert query_store.PublishedRepository().query({})['items'][0]['topicKeys'] == ['ai_infra']
    isolated.assert_not_called()


def test_bad_sidecar_evidence_cannot_replace_active_snapshot(raw_store, monkeypatch):
    _, insert = raw_store
    insert('a')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    command.run('unused', all_papers=True)
    query_store.build('unused')
    previous = query_store.current_path()
    with sqlite3.connect(previous.parent.parent / 'classification' / 'api.db') as conn:
        key, payload = conn.execute('SELECT cache_key,result FROM predictions').fetchone()
        result = json.loads(payload)
        result['evidence'] = 'fabricated passage'
        conn.execute('UPDATE predictions SET result=? WHERE cache_key=?', (json.dumps(result), key))
    with pytest.raises(api.ClassificationError, match='evidence'):
        query_store.build('unused')
    assert query_store.current_path() == previous


def test_model_change_only_does_not_automatically_reclassify_history(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('a')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    command.run('unused', all_papers=True)
    query_store.build('unused')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_MODEL', 'other-model')
    assert command.run('unused')['classified'] == 0
    assert query_store.build('unused')['changed'] == 0
    assert isolated.call_count == 1


def test_cache_refuses_a_raw_database_without_modifying_it(raw_store):
    raw, insert = raw_store
    insert('a')
    before = raw.read_bytes()
    with pytest.raises(ValueError, match='paper database'):
        api.ResultStore(raw)
    assert raw.read_bytes() == before


def test_cli_rejects_unpaired_paths_before_making_requests(tmp_path, isolated):
    source = tmp_path / 'input.json'
    source.write_text(json.dumps([PAPER]))
    result = CliRunner().invoke(cli, ['classify-papers', '--input', str(source)])
    assert result.exit_code == 2
    isolated.assert_not_called()


def test_cli_exports_partial_results_but_returns_failure(tmp_path, isolated):
    source, output = tmp_path / 'input.json', tmp_path / 'output.json'
    source.write_text(json.dumps([PAPER]))
    isolated.return_value = response(status=401)
    result = CliRunner().invoke(cli, ['classify-papers', '--input', str(source), '--output', str(output)])
    assert result.exit_code != 0
    assert json.loads(output.read_text())[0]['status'] == 'pending'
    assert 'test-only-not-a-secret' not in result.output


def test_cli_exports_validated_results_on_success(tmp_path):
    source, output = tmp_path / 'input.json', tmp_path / 'output.json'
    source.write_text(json.dumps([PAPER]))
    result = CliRunner().invoke(cli, ['classify-papers', '--input', str(source), '--output', str(output)])
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text())[0]['topic_key'] == 'ai_infra'


@pytest.mark.parametrize('change', [
    {'decision': 'other'}, {'decision': 'out_of_scope'}, {'topic_key': None},
    {'topic_key': ['ai_infra']}, {'confidence': 'low'}, {'evidence': None},
    {'evidence': 'invented passage'}, {'title': PAPER['title']},
])
def test_compact_protocol_rejects_invalid_or_redundant_fields(change):
    item = {**compact_answer(), **change}
    with pytest.raises(api.ClassificationError):
        api.validate_result(json.dumps({'papers': [item]}), PAPER, compact=True)


def test_real_wire_protocol_omits_original_text_in_response_and_rejoins_locally(isolated):
    result = api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    assert result['title'] == PAPER['title'] and result['abstract'] == PAPER['abstract']
    assert result['topic_name'] == 'AI Infra'
    payload = isolated.call_args.kwargs['json']
    assert payload['max_tokens'] == 512
    assert '不回传标题、摘要或主题中文名' in payload['messages'][0]['content']
    assert 'title 和 abstract 必须完整保留' not in payload['messages'][0]['content']


def test_collection_queue_works_without_api_credentials(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('old')
    query_store.build('unused')
    command.prepare_scope('unused')
    insert('new')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    monkeypatch.delenv('PAPER_COMPASS_CLASSIFICATION_API_KEY')
    assert command.queue('unused')['queued'] == 1
    query_store.build('unused')
    assert query_store.PublishedRepository().query({'ids': ['new']})['items']
    isolated.assert_not_called()


def test_zero_budget_makes_no_requests_and_keeps_job_durable(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('a')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_DAILY_BUDGET_CNY', '0')
    report = command.run('unused', all_papers=True)
    assert report['deferred'] and report['pending'] == 1
    assert report['accounting']['requests'] == 0
    isolated.assert_not_called()
    with api.ResultStore().conn as conn:
        job = conn.execute('SELECT status,attempts,next_attempt FROM jobs').fetchone()
        assert job[0] == 'pending' and job[1] == 0 and job[2]


def test_daily_request_limit_is_shared_across_invocations(isolated, monkeypatch):
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_DAILY_REQUEST_LIMIT', '1')
    api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    with pytest.raises(api.BudgetExceeded):
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    assert isolated.call_count == 1


def test_daily_token_limit_blocks_before_http(isolated, monkeypatch):
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_DAILY_TOKEN_LIMIT', '10')
    with pytest.raises(api.BudgetExceeded):
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    isolated.assert_not_called()


def test_cost_ledger_counts_requests_not_duplicate_or_cached_exports(tmp_path, isolated):
    source = tmp_path / 'papers.json'
    source.write_text(json.dumps([PAPER, PAPER]))
    first = command.classify_json(source, tmp_path / 'first.json')
    second = command.classify_json(source, tmp_path / 'second.json')
    assert first['accounting']['requests'] == second['accounting']['requests'] == 1
    assert second['cached'] == 2
    assert first['accounting']['estimated_cny'] == pytest.approx(0.0003)
    assert isolated.call_count == 1


def test_timeout_retains_conservative_reservation_and_does_not_retry(isolated):
    isolated.side_effect = requests.Timeout('private upstream value')
    with pytest.raises(api.ClassificationError):
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    store = api.ResultStore()
    try:
        row = store.conn.execute('SELECT * FROM requests').fetchone()
        assert row['status'] == 'unknown'
        assert row['charged_micro'] == row['reserved_micro'] > 0
        assert store.status()['unknown_usage_requests'] == 1
    finally:
        store.close()
    assert isolated.call_count == 1


def test_invalid_output_is_still_accounted_as_a_real_request(isolated):
    isolated.return_value = response(content='bad json')
    with pytest.raises(api.ClassificationError):
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    store = api.ResultStore()
    try:
        assert store.status()['requests'] == 1
        assert store.status()['estimated_cny'] == pytest.approx(0.0003)
        assert store.conn.execute('SELECT count(*) FROM predictions').fetchone()[0] == 0
    finally:
        store.close()


def test_daily_budget_resets_at_beijing_midnight(isolated, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_DAILY_REQUEST_LIMIT', '1')
    monkeypatch.setattr(storage, 'now', lambda: datetime(2026, 11, 18, 15, 59, tzinfo=timezone.utc))
    api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    with pytest.raises(api.BudgetExceeded):
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    monkeypatch.setattr(storage, 'now', lambda: datetime(2026, 11, 18, 16, 1, tzinfo=timezone.utc))
    api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    assert isolated.call_count == 2


def test_out_of_scope_keeps_paper_searchable_without_forcing_topic(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('a')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    item = {**compact_answer(), 'decision': 'out_of_scope', 'topic_key': None}
    isolated.return_value = response(content=json.dumps({'papers': [item]}))
    report = command.run('unused', all_papers=True)
    assert report['out_of_scope'] == 1 and report['classified'] == 0
    query_store.build('unused')
    paper = query_store.PublishedRepository().query({})['items'][0]
    assert paper['title'] == PAPER['title'] and paper['topics'] == []


def test_review_gate_manual_acceptance_and_correction_survive_requeue(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('a')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    item = {**compact_answer(), 'decision': 'needs_review', 'confidence': 'low'}
    isolated.return_value = response(content=json.dumps({'papers': [item]}))
    report = command.run('unused', all_papers=True)
    assert report['needs_review'] == 1
    query_store.build('unused')
    assert query_store.PublishedRepository().query({})['items'][0]['topics'] == []
    assert command.run('unused')['needs_review'] == 0
    assert isolated.call_count == 1
    proposal = command.status()['review'][0]
    command.review('unused', proposal['paper'], topic='ai_infra', note='人工核对后确认主要贡献为推理系统。')
    command.publish_results('unused')
    assert query_store.PublishedRepository().query({})['items'][0]['topicKeys'] == ['ai_infra']
    assert command.run('unused', all_papers=True)['classified'] == 0
    assert isolated.call_count == 1
    # Human decisions remain editable without another paid request.
    command.review('unused', proposal['paper'], out_of_scope=True, note='人工复核确认不在收集范围内。')
    command.publish_results('unused')
    assert query_store.PublishedRepository().query({})['items'][0]['topics'] == []


def test_worker_handles_only_durable_queue_and_batches_publication(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    command.prepare_scope('unused')
    insert('a')
    insert('b')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    command.queue('unused', all_papers=True)
    monkeypatch.setattr(command, 'queue', Mock(side_effect=AssertionError('worker rescanned corpus')))
    build = Mock(wraps=query_store.build)
    monkeypatch.setattr(query_store, 'build', build)
    report = command.run('unused', queued_only=True, publish=True)
    assert report['classified'] == 2 and report['cached'] == 1
    assert build.call_count == 1 and isolated.call_count == 1
    report = command.run('unused', queued_only=True, publish=True)
    assert report['publication']['skipped']
    assert build.call_count == 1 and isolated.call_count == 1


def test_publication_failure_retries_without_reclassifying(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    command.prepare_scope('unused')
    insert('a')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    command.queue('unused', all_papers=True)
    with monkeypatch.context() as patch:
        patch.setattr(query_store, 'build', Mock(side_effect=OSError('disk unavailable')))
        report = command.run('unused', queued_only=True, publish=True)
        assert not report['publication']['ok']
    report = command.run('unused', queued_only=True, publish=True)
    assert report['publication']['ok'] and isolated.call_count == 1


def test_missing_metadata_waits_for_text_change_without_repeated_requests(raw_store, monkeypatch, isolated):
    raw, insert = raw_store
    insert('a', {**PAPER, 'abstract': ''})
    command.run('unused', all_papers=True)
    assert command.status()['jobs']['waiting_metadata'] == 1
    assert command.run('unused', all_papers=True)['waiting_metadata'] == 0
    isolated.assert_not_called()
    with sqlite3.connect(raw) as conn:
        conn.execute('UPDATE matched_entries SET abstract=?,summary=?', (PAPER['abstract'], PAPER['abstract']))
    assert command.run('unused', all_papers=True)['classified'] == 1
    assert isolated.call_count == 1


def test_credential_failure_opens_persistent_circuit(raw_store, isolated):
    _, insert = raw_store
    insert('a')
    insert('b')
    isolated.return_value = response(status=401)
    report = command.run('unused', all_papers=True)
    assert report['deferred'] and report['failed'] == 1
    assert command.status()['circuit_until']
    command.run('unused', queued_only=True)
    assert isolated.call_count == 1


def test_failed_retries_are_capped_and_require_manual_resume(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    command.prepare_scope('unused')
    insert('a')
    isolated.return_value = response(status=503)
    command.run('unused', all_papers=True)
    for _ in range(2):
        store = api.ResultStore()
        store.conn.execute("UPDATE jobs SET next_attempt=''")
        store.conn.commit()
        store.close()
        command.run('unused', queued_only=True)
    assert command.status()['jobs']['failed'] == 1
    command.run('unused', queued_only=True)
    assert isolated.call_count == 3
    isolated.return_value = response()
    assert command.run('unused', queued_only=True, retry_failed=True)['classified'] == 1


def test_newer_job_text_is_not_overwritten_by_in_flight_result(raw_store, monkeypatch, isolated):
    raw, insert = raw_store
    insert('a')
    newer = {**PAPER, 'abstract': 'New text describes an entirely different research contribution.'}
    def remote(*args, **kwargs):
        with sqlite3.connect(raw) as conn:
            conn.execute('UPDATE matched_entries SET abstract=?,summary=?', (newer['abstract'], newer['abstract']))
        store = api.ResultStore()
        store.enqueue('a', newer, api.Settings.from_env())
        store.conn.commit()
        store.close()
        return response()
    isolated.side_effect = remote
    assert command.run('unused', all_papers=True)['classified'] == 0
    assert command.status()['jobs']['pending'] == 1


def test_running_lease_recovers_after_worker_crash(raw_store, monkeypatch):
    from datetime import datetime, timedelta, timezone
    _, insert = raw_store
    command.prepare_scope('unused')
    insert('a')
    command.queue('unused', all_papers=True)
    clock = datetime(2026, 11, 18, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(storage, 'now', lambda: clock)
    store = api.ResultStore()
    key = store.conn.execute('SELECT cache_key FROM jobs').fetchone()[0]
    assert store.claim('a', key, 60)
    store.close()
    assert command.run('unused', queued_only=True)['classified'] == 0
    clock += timedelta(minutes=3)
    assert command.run('unused', queued_only=True)['classified'] == 1


@pytest.mark.parametrize('review', [False, True])
def test_legacy_sidecar_migration_preserves_predictions_and_respects_review_gate(tmp_path, review):
    path = tmp_path / 'legacy-api.db'
    original = {**answer(), 'needs_review': review}
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE predictions (cache_key TEXT PRIMARY KEY,result TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE jobs (paper_id TEXT PRIMARY KEY,text_hash TEXT NOT NULL,cache_key TEXT NOT NULL,
                              status TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',last_attempt TEXT NOT NULL DEFAULT '');
        ''')
        conn.execute('INSERT INTO predictions VALUES (?,?,?)', ('old-policy', json.dumps(original), '2026-11-18'))
        conn.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)', ('legacy', api.digest(PAPER), 'old-policy', 'done', '', ''))
    store = api.ResultStore(path)
    try:
        assert store.cached('old-policy') == original
        assert store.status()['jobs'] == {'needs_review' if review else 'classified': 1}
        assert 'attempts' in {r[1] for r in store.conn.execute('PRAGMA table_info(jobs)')}
    finally:
        store.close()
    reader = api.AssignmentReader(path)
    try:
        result = reader.get({'entry_id': 'legacy', **PAPER})
        assert (result is None) == review
        if result:
            assert result['topic_key'] == 'ai_infra'
    finally:
        reader.close()


def test_crash_before_response_keeps_reserved_daily_budget(isolated, monkeypatch):
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_DAILY_REQUEST_LIMIT', '1')
    store = api.ResultStore()
    store.reserve(api.Settings.from_env(), PAPER, 1000)
    store.close()  # Simulate process death without finish_request.
    with pytest.raises(api.BudgetExceeded):
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    isolated.assert_not_called()


def test_repeated_worker_deaths_do_not_bypass_attempt_limit(raw_store, isolated):
    _, insert = raw_store
    command.prepare_scope('unused')
    insert('a')
    command.queue('unused', all_papers=True)
    store = api.ResultStore()
    store.conn.execute("UPDATE jobs SET status='running',attempts=3,lease_until='2020-01-01'")
    store.conn.commit()
    store.close()
    command.run('unused', queued_only=True)
    assert command.status()['jobs']['failed'] == 1
    isolated.assert_not_called()


@pytest.mark.parametrize('value', ['NaN', '-1', 'not-a-price'])
def test_invalid_budget_fails_before_claiming_or_sending(raw_store, monkeypatch, isolated, value):
    _, insert = raw_store
    command.prepare_scope('unused')
    insert('a')
    command.queue('unused', all_papers=True)
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_DAILY_BUDGET_CNY', value)
    with pytest.raises(ValueError, match='budget'):
        command.run('unused', queued_only=True)
    assert command.status()['jobs']['pending'] == 1
    isolated.assert_not_called()


def test_baseline_freezes_old_jobs_and_metadata_changes_without_spending(raw_store, monkeypatch, isolated):
    raw, insert = raw_store
    insert('old')
    query_store.build('unused')
    original = query_store.PublishedRepository().query({})['items'][0]
    store = api.ResultStore()
    store.enqueue('old', PAPER, api.Settings.from_env())
    store.conn.commit()
    store.close()
    before = raw.read_bytes()
    assert command.prepare_scope('unused')['historical_count'] == 1
    assert raw.read_bytes() == before
    assert not command.prepare_scope('unused')['created']
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    with sqlite3.connect(raw) as conn:
        conn.execute("UPDATE matched_entries SET authors='New author',title='New title',abstract='Changed abstract'")
    assert command.run('unused', queued_only=True, retry_failed=True)['classified'] == 0
    assert command.queue('unused')['queued'] == 0
    assert command.status()['jobs'] == {'historical': 1}
    query_store.build('unused')
    updated = query_store.PublishedRepository().query({})['items'][0]
    assert updated['title'] == 'New title' and updated['authors'] == 'New author'
    assert updated['topics'] == original['topics']
    assert updated['topicKeys'] == original['topicKeys']
    isolated.assert_not_called()


def test_historical_hidden_paper_is_not_surfaced_when_metadata_changes(raw_store, monkeypatch, isolated):
    raw, insert = raw_store
    insert('visible')
    insert('hidden')
    with sqlite3.connect(raw) as conn:
        conn.execute("UPDATE matched_entries SET venue_type='期刊',venue_name='untracked journal' WHERE entry_id='hidden'")
    query_store.build('unused')
    command.prepare_scope('unused')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    with sqlite3.connect(raw) as conn:
        conn.execute("UPDATE matched_entries SET venue_type='会议',title='Edited hidden' WHERE entry_id='hidden'")
    query_store.build('unused')
    assert not query_store.PublishedRepository().query({'ids': ['hidden']})['items']
    isolated.assert_not_called()


def test_unlimited_worker_drains_more_than_200_in_bounded_batches(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    command.prepare_scope('unused')
    for index in range(305):
        insert(f'new-{index:04}')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    batches = []
    original = storage.ResultStore.due
    def due(self, limit, **kwargs):
        batches.append(limit)
        return original(self, limit, **kwargs)
    monkeypatch.setattr(storage.ResultStore, 'due', due)
    report = command.run('unused')
    assert report['classified'] == 305 and report['pending'] == 0
    assert report['cached'] == 304 and isolated.call_count == 1
    assert len(batches) >= 4 and max(batches) <= 100


def test_default_worker_never_resumes_explicit_historical_backlog(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('a')
    insert('b')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    assert command.run('unused', all_papers=True, limit=1)['classified'] == 1
    assert command.run('unused', queued_only=True)['classified'] == 0
    assert command.run('unused', all_papers=True)['classified'] == 1
    assert isolated.call_count == 1


def test_source_switch_cannot_reuse_a_different_history_boundary(raw_store, tmp_path, monkeypatch):
    command.prepare_scope('unused')
    monkeypatch.setattr(command, 'resolve_data_file', lambda _: tmp_path / 'different.db')
    with pytest.raises(ValueError, match='another database'):
        command.prepare_scope('unused')


def test_unlimited_count_controls_keep_the_monetary_guard(isolated, monkeypatch):
    limits = storage.Limits.from_env()
    assert limits.daily_requests == limits.daily_tokens == -1
    assert limits.daily_micro == 3000000
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_DAILY_BUDGET_CNY', '0')
    with pytest.raises(api.BudgetExceeded):
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    isolated.assert_not_called()


@pytest.mark.parametrize('key', ['DAILY_REQUEST_LIMIT', 'DAILY_TOKEN_LIMIT'])
def test_invalid_negative_count_limits_fail_closed(key, monkeypatch):
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFICATION_' + key, '-2')
    with pytest.raises(ValueError, match='budget'):
        storage.Limits.from_env()


def test_explicit_admin_override_can_resume_a_frozen_historical_job(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    insert('old')
    store = api.ResultStore()
    store.enqueue('old', PAPER, api.Settings.from_env())
    store.conn.commit()
    store.close()
    command.prepare_scope('unused')
    assert command.run('unused')['classified'] == 0
    assert command.status()['jobs'] == {'historical': 1}
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    assert command.run('unused', all_papers=True)['classified'] == 1
    query_store.build('unused')
    assert query_store.PublishedRepository().query({})['items'][0]['topicKeys'] == ['ai_infra']
    assert isolated.call_count == 1


def test_no_snapshot_cannot_silently_hide_all_frozen_history(raw_store, monkeypatch):
    _, insert = raw_store
    insert('old')
    command.prepare_scope('unused')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    with pytest.raises(ValueError, match='existing query snapshot'):
        query_store.build('unused')
    assert not list(query_store.resolve_data_path('query-store').glob('query-*.db'))


@pytest.mark.parametrize('local,resume', [
    ('2026-11-19T08:58:44+08:00', None),
    ('2026-11-19T08:58:45+08:00', '2026-11-19T12:00:00+08:00'),
    ('2026-11-19T09:00:00+08:00', '2026-11-19T12:00:00+08:00'),
    ('2026-11-19T11:59:59+08:00', '2026-11-19T12:00:00+08:00'),
    ('2026-11-19T12:00:00+08:00', None),
    ('2026-11-19T13:58:45+08:00', '2026-11-19T18:00:00+08:00'),
    ('2026-11-19T14:00:00+08:00', '2026-11-19T18:00:00+08:00'),
    ('2026-11-19T17:59:59+08:00', '2026-11-19T18:00:00+08:00'),
    ('2026-11-19T18:00:00+08:00', None),
    ('2026-11-19T23:59:59+08:00', None),
    ('2026-11-21T10:00:00+08:00', None),  # Saturday
    ('2026-11-22T15:00:00+08:00', None),  # Sunday
    ('2026-11-23T08:59:00+08:00', '2026-11-23T12:00:00+08:00'),
])
def test_off_peak_windows_use_beijing_time_and_leave_request_margin(local, resume):
    # Feed UTC, as the server may use any system timezone.
    stamp = datetime.fromisoformat(local).astimezone(timezone.utc)
    actual = storage.off_peak_resume_at(stamp, margin_seconds=75)
    expected = datetime.fromisoformat(resume).astimezone(timezone.utc) if resume else None
    assert actual == expected


@pytest.mark.parametrize('local', ['2026-11-19T10:00:00+08:00', '2026-11-19T13:59:00+08:00'])
def test_peak_or_closing_window_blocks_http_without_a_cost_reservation(local, monkeypatch, isolated):
    monkeypatch.setattr(storage, 'now', lambda: datetime.fromisoformat(local))
    with pytest.raises(api.OffPeakPause) as caught:
        api.APIClassifier(api.Settings.from_env()).classify(PAPER)
    assert datetime.fromisoformat(caught.value.resume_at) > storage.now()
    store = api.ResultStore()
    try:
        assert store.conn.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
        assert store.status()['estimated_cny'] == 0
    finally:
        store.close()
    isolated.assert_not_called()


def test_worker_pauses_before_peak_publishes_completed_work_and_resumes(raw_store, monkeypatch, isolated):
    _, insert = raw_store
    command.prepare_scope('unused')
    other = {**PAPER, 'title': 'Another inference scheduling method'}
    insert('a')
    insert('b', other)
    clock = datetime.fromisoformat('2026-11-19T08:58:44+08:00')
    monkeypatch.setenv('PAPER_COMPASS_CLASSIFIER', 'api')
    monkeypatch.setattr(storage, 'now', lambda: clock)
    def finish_first(*args, **kwargs):
        nonlocal clock
        clock = datetime.fromisoformat('2026-11-19T08:58:45+08:00')
        return response()
    isolated.side_effect = finish_first
    report = command.run('unused', publish=True)
    assert report['classified'] == 1 and report['pending'] == 1
    assert report['deferred_reason'] == 'off_peak_window' and report['deferred']
    assert report['publication']['ok']
    with api.ResultStore().conn as conn:
        job = conn.execute("SELECT status,attempts,next_attempt FROM jobs WHERE paper_id='b'").fetchone()
    assert job['status'] == 'pending' and job['attempts'] == 0
    assert job['next_attempt'] == report['resume_at'] == '2026-11-19T04:00:00+00:00'
    assert isolated.call_count == 1
    clock = datetime.fromisoformat('2026-11-19T10:00:00+08:00')
    assert command.run('unused', queued_only=True)['classified'] == 0
    assert isolated.call_count == 1
    clock = datetime.fromisoformat('2026-11-19T12:00:00+08:00')
    isolated.side_effect = None
    isolated.return_value = response(other)
    report = command.run('unused', queued_only=True, publish=True)
    assert report['classified'] == 1 and report['pending'] == 0
    assert isolated.call_count == 2


def test_three_yuan_budget_is_shared_and_resets_by_beijing_day(monkeypatch, isolated):
    clock = datetime.fromisoformat('2026-11-19T23:50:00+08:00')
    monkeypatch.setattr(storage, 'now', lambda: clock)
    settings = api.Settings.from_env()
    store = api.ResultStore()
    try:
        # Simulate usage already charged today, leaving too little to reserve
        # the next request. Rates and the reservation are persisted per request.
        request = store.reserve(settings, PAPER, 2997000)
        store.finish_request(request, 'success', {'prompt_tokens': 2997000, 'completion_tokens': 0})
        assert store.status()['estimated_cny'] == pytest.approx(2.997)
    finally:
        store.close()
    with pytest.raises(api.BudgetExceeded, match='Daily classification budget'):
        api.APIClassifier(settings).classify(PAPER)
    isolated.assert_not_called()
    clock = datetime.fromisoformat('2026-11-20T00:01:00+08:00')
    api.APIClassifier(settings).classify(PAPER)
    assert isolated.call_count == 1
    assert command.status()['estimated_cny'] == pytest.approx(0.0003)


def test_json_classification_also_obeys_peak_guard_but_can_reuse_cache(tmp_path, monkeypatch, isolated):
    source = tmp_path / 'sample.json'
    source.write_text(json.dumps([PAPER]))
    assert command.classify_json(source, tmp_path / 'first.json')['classified'] == 1
    monkeypatch.setattr(storage, 'now', lambda: datetime.fromisoformat('2026-11-19T10:00:00+08:00'))
    assert command.classify_json(source, tmp_path / 'cached.json')['cached'] == 1
    source.write_text(json.dumps([{**PAPER, 'title': 'Uncached new paper'}]))
    report = command.classify_json(source, tmp_path / 'paused.json')
    assert not report['ok'] and report['failed'] == 1
    assert report['accounting']['requests'] == 0
    assert json.loads((tmp_path / 'paused.json').read_text())[0]['status'] == 'pending'
    assert isolated.call_count == 1

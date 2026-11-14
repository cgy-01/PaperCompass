"""Account isolation, OAuth/CSRF boundaries and telemetry retention."""
import json
import sqlite3
import threading
import time
import uuid
from http.client import HTTPConnection
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest
import requests
from click.testing import CliRunner

from paper_firehose import accounts, local_app
from paper_firehose.accounts import AccountAPI, Settings, validate_events
from paper_firehose.event_store import EventStore
from paper_firehose.user_store import UserStore, digest
from paper_firehose.cli import cli


@pytest.fixture
def service(tmp_path):
    return AccountAPI(tmp_path, Settings('http://127.0.0.1', 'client', 'secret'))


@pytest.fixture
def http_service(tmp_path, service):
    app = local_app.LocalApplication('unused', tmp_path / 'html')
    app.accounts = service
    server = ThreadingHTTPServer(('127.0.0.1', 0), local_app._handler(app))
    origin = 'http://127.0.0.1:' + str(server.server_port)
    service.settings = Settings(origin, 'client', 'secret')
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    cookies = SimpleCookie()

    def request(method, path, body=None, headers=None):
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        request_headers = {'Origin': origin}
        if cookies:
            request_headers['Cookie'] = '; '.join(key + '=' + item.value for key, item in cookies.items())
        payload = json.dumps(body) if body is not None else None
        if payload is not None:
            request_headers['Content-Type'] = 'application/json'
        request_headers.update(headers or {})
        try:
            connection.request(method, path, body=payload, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            response_headers = response.getheaders()
            for key, value in response_headers:
                if key.lower() == 'set-cookie':
                    for name, item in SimpleCookie(value).items():
                        if item['max-age'] == '0':
                            cookies.pop(name, None)
                        else:
                            cookies[name] = item.value
            return response.status, json.loads(data) if data else {}, dict(response_headers)
        finally:
            connection.close()
    try:
        yield service, request, cookies, origin
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def login(service, request, monkeypatch, github_id=1):
    profile = Mock(return_value={'id': github_id, 'login': 'researcher' + str(github_id)})
    monkeypatch.setattr(service, '_profile', profile)
    status, _, headers = request('GET', '/auth/github/login')
    assert status == 302
    params = parse_qs(urlsplit(headers['Location']).query)
    assert params['code_challenge_method'] == ['S256']
    assert len(params['code_challenge'][0]) == 43
    status, _, headers = request('GET', '/auth/github/callback?code=example&state=' + params['state'][0])
    assert status == 302
    assert headers['Location'].endswith('/#account')
    assert profile.call_args.args[0] == 'example'
    return request('GET', '/api/account')[1]['csrfToken'], params['state'][0]


def event(**overrides):
    return {'id': uuid.uuid4().hex, 'session_id': uuid.uuid4().hex,
            'type': 'page_view', 'page': 'discover', **overrides}


def test_user_collections_and_sessions_are_isolated(service):
    first = service.users.login({'id': 1, 'login': 'alice'})
    second = service.users.login({'id': 2, 'login': 'bob'})
    alice, bob = service.users.user(first), service.users.user(second)
    service.users.set_ids(alice['id'], 'favorites', ['old', 'new'])
    assert service.users.list_ids(bob['id'], 'favorites')['ids'] == []
    assert service.users.list_ids(alice['id'], 'favorites')['total'] == 2
    service.users.set_ids(alice['id'], 'favorites', ['old'], False)
    assert service.users.list_ids(alice['id'], 'favorites')['ids'] == ['new']
    with service.users.connect() as conn:
        assert conn.execute('SELECT token_hash FROM sessions WHERE user_id=?', (alice['id'],)).fetchone()[0] == digest(first)
        conn.execute('UPDATE sessions SET expires_at=0 WHERE user_id=?', (bob['id'],))
    assert service.users.user(second) is None
    service.users.logout(first)
    assert service.users.user(first) is None


def test_github_identity_survives_login_rename(service):
    first = service.users.login({'id': 1, 'login': 'alice'})
    user = service.users.user(first)
    service.users.set_ids(user['id'], 'favorites', ['new'])
    second = service.users.login({'id': 1, 'login': 'renamed'})
    assert service.users.user(second)['id'] == user['id']
    assert service.users.user(second)['login'] == 'renamed'
    assert service.users.list_ids(user['id'], 'favorites')['ids'] == ['new']


def test_http_input_limits_and_identity_requirements(http_service):
    service, request, _, _ = http_service
    assert request('GET', '/api/account/favorites')[0] == 401
    assert request('POST', '/api/events', {'events': [event()]})[0] == 400
    request('GET', '/api/account')
    assert request('POST', '/api/events', {'events': [event()]}, {'Origin': ''})[0] == 403
    assert request('POST', '/api/events', {'events': []})[0] == 400
    assert request('POST', '/api/events', {'padding': 'x' * 33000})[0] == 413
    service.settings = Settings(service.settings.public_url, events_enabled=False)
    assert request('POST', '/api/events', {'events': [event()]})[1]['accepted'] == 0


@pytest.mark.parametrize('origin', ['http://public.test', 'https://example.test/path',
    'https://name:secret@example.test', 'https://example.test?key=value'])
def test_unsafe_auth_origins_are_rejected(monkeypatch, origin):
    monkeypatch.setenv('PAPER_COMPASS_PUBLIC_URL', origin)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_oauth_cookie_binding_expiry_and_single_use(service):
    state, binding, verifier = service.users.begin_login('#discover')
    assert service.users.consume_login(state, 'wrong') is None
    assert service.users.consume_login(state, binding)['verifier'] == verifier
    assert service.users.consume_login(state, binding) is None
    state, binding, _ = service.users.begin_login('#home')
    with service.users.connect() as conn:
        conn.execute('UPDATE oauth_states SET expires_at=0')
    assert service.users.consume_login(state, binding) is None


def test_oauth_and_authenticated_collection_http(http_service, monkeypatch):
    service, request, cookies, _ = http_service
    status, data, headers = request('GET', '/api/account')
    assert status == 200 and data['user'] is None
    assert headers['Cache-Control'] == 'no-store'
    csrf, state = login(service, request, monkeypatch)
    assert request('GET', '/auth/github/callback?code=example&state=' + state)[0] == 400
    assert request('POST', '/api/account/favorites', {'ids': ['new']})[0] == 403
    headers = {'X-CSRF-Token': csrf}
    assert request('POST', '/api/account/favorites', {'ids': ['new']}, headers)[0] == 200
    assert request('GET', '/api/account/favorites')[1]['ids'] == ['new']
    assert request('POST', '/api/account/reading', {'ids': ['new']}, headers)[0] == 200
    assert request('GET', '/api/account/reading')[1]['ids'] == ['new']
    assert request('POST', '/api/account/favorites', {'ids': ['old']}, {**headers, 'Origin': 'https://evil.test'})[0] == 403
    assert request('POST', '/api/refresh', {})[0] == 405
    assert request('POST', '/api/account/logout', {}, headers)[0] == 200
    assert request('GET', '/api/account/favorites')[0] == 401


def test_callback_failures_and_safe_redirect(http_service, monkeypatch, caplog):
    service, request, _, origin = http_service
    _, _, headers = request('GET', '/auth/github/login?return_to=https://evil.test')
    state = parse_qs(urlsplit(headers['Location']).query)['state'][0]
    monkeypatch.setattr(service, '_profile', Mock(side_effect=requests.Timeout('SECRET')))
    assert request('GET', '/auth/github/callback?code=PRIVATE_CODE&state=' + state)[0] == 502
    assert 'SECRET' not in caplog.text and 'PRIVATE_CODE' not in caplog.text
    login(service, request, monkeypatch)


def test_github_exchange_uses_pkce_and_revalidates_identity(service, monkeypatch):
    token_response = Mock()
    token_response.json.return_value = {'access_token': 'private-token'}
    profile_response = Mock()
    profile_response.json.return_value = {'id': 42, 'login': 'alice'}
    post, get = Mock(return_value=token_response), Mock(return_value=profile_response)
    monkeypatch.setattr(accounts.requests, 'post', post)
    monkeypatch.setattr(accounts.requests, 'get', get)
    assert service._profile('code', 'verifier')['id'] == 42
    assert post.call_args.kwargs['data']['code_verifier'] == 'verifier'
    assert get.call_args.kwargs['headers']['Authorization'] == 'Bearer private-token'
    assert post.call_args.kwargs['allow_redirects'] is False


def test_disabled_login_and_secure_cookie(tmp_path):
    service = AccountAPI(tmp_path, Settings())
    assert service.settings.login_enabled is False
    name, value = Settings('https://papercompass.cn').cookie('session', 'value')
    assert '__Host-pc_session=' in value and 'HttpOnly' in value and 'Secure' in value
    assert 'SameSite=Lax' in value and 'Path=/' in value


def test_telemetry_duplicates_quota_and_server_identity(http_service, monkeypatch):
    service, request, _, _ = http_service
    request('GET', '/api/account')
    item = event()
    assert request('POST', '/api/events', {'events': [item]})[1]['accepted'] == 1
    assert request('POST', '/api/events', {'events': [item]})[1]['accepted'] == 0
    assert request('POST', '/api/events', {'events': [event(user_id='forged')]})[0] == 400
    assert request('POST', '/api/events', {'events': [event(details={'query': 'private'})]})[0] == 400
    csrf, _ = login(service, request, monkeypatch)
    request('POST', '/api/events', {'events': [event()]})
    report = service.events.report()
    assert report['daily'][0]['visitors'] == 1
    assert report['daily'][0]['users'] == 1
    assert sum(row['count'] for row in report['actions']) == 2
    service.events.daily_limit = 2
    assert request('POST', '/api/events', {'events': [event()]})[0] == 429
    # Retries stay idempotent even after the daily limit is reached.
    assert request('POST', '/api/events', {'events': [item]})[1]['accepted'] == 0


def test_retention_preserves_daily_totals_and_accounts(service, monkeypatch):
    now = time.time()
    with monkeypatch.context() as patch:
        patch.setattr('paper_firehose.event_store.time.time', lambda: now - 100 * 86400)
        service.events.record([event()], 'visitor', None)
    service.events.maintain()
    with sqlite3.connect(service.events.path) as conn:
        assert conn.execute('SELECT count(*) FROM events').fetchone()[0] == 0
        assert conn.execute('SELECT sum(count) FROM daily_counts').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM daily_unique').fetchone()[0] == 0


def test_user_backup_preserves_live_collections(service, tmp_path):
    token = service.users.login({'id': 1, 'login': 'alice'})
    user = service.users.user(token)
    service.users.set_ids(user['id'], 'favorites', ['new'])
    destination = tmp_path / 'backup' / 'users.db'
    service.users.backup(destination)
    with sqlite3.connect(destination) as conn:
        assert conn.execute('SELECT paper_id FROM favorites').fetchone()[0] == 'new'
    assert destination.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        service.users.backup(destination)


def test_private_analytics_and_backup_commands(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPER_FIREHOSE_DATA_DIR', str(tmp_path / 'runtime'))
    runner = CliRunner()
    result = runner.invoke(cli, ['analytics', '--days', '7'])
    assert result.exit_code == 0
    assert json.loads(result.output)['timezone'] == 'Asia/Shanghai'
    destination = tmp_path / 'private-backup.db'
    result = runner.invoke(cli, ['backup-users', '--output', str(destination)])
    assert result.exit_code == 0 and destination.exists()
    assert runner.invoke(cli, ['backup-users', '--output', str(destination)]).exit_code != 0


@pytest.mark.parametrize('items', [[], [event()] * 51, [event(page='unknown')],
    [event(id='bad')], [event(details={'query_length': -1})], [event(details={'value': 'x' * 121})]])
def test_invalid_activity_batches(items):
    with pytest.raises(ValueError):
        validate_events(items)

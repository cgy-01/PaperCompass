"""GitHub sign-in, user-owned collections and bounded activity ingestion."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
from dataclasses import dataclass, field
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import requests

from .core.paths import resolve_data_path
from .event_store import EventStore, EVENT_TYPES, PAGES
from .user_store import UserStore, digest

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    public_url: str = ''
    client_id: str = ''
    client_secret: str = field(default='', repr=False)
    events_enabled: bool = True
    retention_days: int = 90
    daily_limit: int = 100000
    dev_login: bool = False

    def __post_init__(self):
        if self.dev_login:
            parsed = urlsplit(self.public_url)
            if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
                    or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                    or self.client_id or self.client_secret):
                raise ValueError('Development login requires an HTTP loopback origin and no GitHub credentials')

    @classmethod
    def from_env(cls):
        url = os.getenv('PAPER_COMPASS_PUBLIC_URL', '').rstrip('/')
        if url:
            parsed = urlsplit(url)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
                    or parsed.password or parsed.path or parsed.query or parsed.fragment
                    or (parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1'))):
                raise ValueError('PAPER_COMPASS_PUBLIC_URL must be an HTTPS origin, or HTTP localhost')
        return cls(url, os.getenv('GITHUB_CLIENT_ID', ''), os.getenv('GITHUB_CLIENT_SECRET', ''),
                   os.getenv('PAPER_COMPASS_EVENTS_ENABLED', '1') == '1',
                   max(1, min(365, int(os.getenv('PAPER_COMPASS_EVENTS_RETENTION_DAYS', '90')))),
                   max(100, min(1000000, int(os.getenv('PAPER_COMPASS_EVENTS_DAILY_LIMIT', '100000')))),
                   os.getenv('PAPER_COMPASS_DEV_LOGIN', '0') == '1')

    @property
    def login_enabled(self):
        return self.dev_login or bool(self.public_url and self.client_id and self.client_secret)

    def cookie_name(self, kind):
        if self.dev_login:
            return 'pc_dev_' + kind
        return ('__Host-' if self.public_url.startswith('https://') else '') + 'pc_' + kind

    def cookie(self, kind, value, age=30 * 86400):
        cookie = SimpleCookie()
        name = self.cookie_name(kind)
        cookie[name] = value
        cookie[name]['path'] = '/'
        cookie[name]['httponly'] = True
        cookie[name]['samesite'] = 'Lax'
        cookie[name]['max-age'] = age
        if self.public_url.startswith('https://'):
            cookie[name]['secure'] = True
        return ('Set-Cookie', cookie.output(header='').strip())


def valid_ids(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 250:
        raise ValueError('Expected 1 to 250 paper identifiers')
    if any(not isinstance(item, str) or not item or len(item) > 512 or any(ord(c) < 32 for c in item) for item in value):
        raise ValueError('Invalid paper identifier')
    return value


def validate_events(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 50:
        raise ValueError('Expected 1 to 50 activity events')
    for item in value:
        if not isinstance(item, dict) or item.get('type') not in EVENT_TYPES or item.get('page') not in PAGES:
            raise ValueError('Invalid activity event')
        if set(item) - {'id', 'session_id', 'type', 'page', 'paper_id', 'target', 'details'}:
            raise ValueError('Unknown activity field')
        for key in ('id', 'session_id'):
            if not isinstance(item.get(key), str) or not re.fullmatch(r'[a-f0-9-]{32,36}', item[key]):
                raise ValueError('Invalid activity identity')
        if item.get('paper_id') and not re.fullmatch(r'[a-f0-9]{64}', str(item['paper_id'])):
            raise ValueError('Invalid public paper identity')
        if not isinstance(item.get('target', ''), str) or len(item.get('target', '')) > 80:
            raise ValueError('Invalid activity target')
        details = item.get('details', {})
        if not isinstance(details, dict) or set(details) - {'value', 'page_number', 'query_length', 'duration_ms'}:
            raise ValueError('Invalid activity details')
        for key, field in details.items():
            if key == 'value':
                if not isinstance(field, str) or len(field) > 120:
                    raise ValueError('Activity value is too long')
            elif type(field) is not int or not 0 <= field <= 3600000:
                raise ValueError('Invalid activity measurement')
    return value


class AccountAPI:
    def __init__(self, root: Path | None = None, settings: Settings | None = None):
        root = root or resolve_data_path()
        self.settings = settings or Settings.from_env()
        self.users = UserStore(root / 'users' / ('development.db' if self.settings.dev_login else 'users.db'))
        self.events = EventStore(root / 'analytics' / 'events.db', self.settings.retention_days, self.settings.daily_limit)

    def _profile(self, code, verifier):
        settings = self.settings
        response = requests.post('https://github.com/login/oauth/access_token', data={
            'client_id': settings.client_id, 'client_secret': settings.client_secret,
            'code': code, 'code_verifier': verifier,
            'redirect_uri': settings.public_url + '/auth/github/callback'},
            headers={'Accept': 'application/json'}, timeout=(5, 15), allow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        token = payload.get('access_token') if isinstance(payload, dict) else None
        if not token:
            raise ValueError('GitHub authorization was not completed')
        response = requests.get('https://api.github.com/user',
            headers={'Accept': 'application/vnd.github+json', 'Authorization': 'Bearer ' + token},
            timeout=(5, 15), allow_redirects=False)
        response.raise_for_status()
        profile = response.json()
        if not isinstance(profile, dict) or type(profile.get('id')) is not int or profile['id'] <= 0 or not isinstance(profile.get('login'), str):
            raise ValueError('GitHub returned an invalid user profile')
        return {'id': profile['id'], 'login': profile['login'][:100],
                'name': str(profile.get('name') or profile['login'])[:200],
                'avatar_url': str(profile.get('avatar_url') or '')[:1024]}

    def handle(self, handler, method, path, params):
        if not (path.startswith('/api/account') or path.startswith('/auth/github/') or path == '/api/events'):
            return False
        settings = self.settings
        def reply(body, status=200, headers=()):
            handler._json(body, status, headers=headers, cache_control='no-store')

        def redirect(location, headers=()):
            handler.send_response(302)
            handler.send_header('Location', location)
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('Referrer-Policy', 'no-referrer')
            for key, value in headers:
                handler.send_header(key, value)
            handler.send_header('Content-Length', '0')
            handler.end_headers()

        def auth_failure(reason, message, status, headers=(), return_to='#home'):
            # Browser navigation returns to the application; API clients retain
            # the existing JSON error/status contract. Never reflect provider text.
            if 'text/html' in handler.headers.get('Accept', ''):
                redirect((settings.public_url or '') + '/#welcome?' + urlencode({
                    'auth': reason, 'return_to': return_to}), headers)
            else:
                reply({'error': message}, status, headers)

        if settings.dev_login:
            # Do not trust forwarded headers: a production reverse proxy must
            # never turn a public request into a development login.
            try:
                local_peer = ipaddress.ip_address(handler.client_address[0]).is_loopback
            except ValueError:
                local_peer = False
            if not local_peer or handler.headers.get('Host', '').lower() != urlsplit(settings.public_url).netloc.lower():
                reply({'error': 'development login is only available on localhost'}, 403)
                return True

        try:
            cookies = SimpleCookie(handler.headers.get('Cookie', ''))
        except CookieError:
            cookies = SimpleCookie()
        def cookie_value(kind):
            item = cookies.get(settings.cookie_name(kind))
            return item.value if item else ''
        token = cookie_value('session')
        user = self.users.user(token)
        development_headers = []
        if settings.dev_login and method == 'GET' and path in ('/api/account', '/auth/github/login') and not user:
            token = self.users.login({'id': -1, 'login': 'local-developer', 'name': '本地开发账号'})
            user = self.users.user(token)
            development_headers.append(settings.cookie('session', token))
            LOGGER.info('Local development account session created')
        try:
            if method == 'GET' and path == '/auth/github/login':
                return_to = params.get('return_to', ['#account'])[0]
                if not return_to.startswith('#') or len(return_to) > 2048 or '\r' in return_to or '\n' in return_to:
                    return_to = '#account'
                if user:
                    redirect((settings.public_url or '') + '/' + quote(return_to, safe='#/?=&%:+,'), development_headers)
                    return True
                if not settings.login_enabled:
                    auth_failure('unavailable', 'GitHub 登录暂不可用', 503, return_to=return_to)
                    return True
                state, binding, verifier = self.users.begin_login(return_to)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                redirect('https://github.com/login/oauth/authorize?' + urlencode({
                    'client_id': settings.client_id, 'redirect_uri': settings.public_url + '/auth/github/callback',
                    'scope': '', 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'}),
                    [settings.cookie('oauth', binding, 600)])
                return True
            if method == 'GET' and path == '/auth/github/callback':
                if settings.dev_login:
                    reply({'error': 'GitHub callback is disabled in development login mode'}, 404)
                    return True
                state, code = params.get('state', [''])[0], params.get('code', [''])[0]
                login = self.users.consume_login(state, cookie_value('oauth')) if settings.login_enabled and state else None
                clear = settings.cookie('oauth', '', 0)
                if not login or not code or len(code) > 512 or params.get('error'):
                    reason = 'cancelled' if login and params.get('error') == ['access_denied'] else 'expired'
                    auth_failure(reason, '登录请求已失效，请重新登录', 400, [clear], login['return_to'] if login else '#home')
                    return True
                try:
                    session = self.users.login(self._profile(code, login['verifier']))
                except (requests.RequestException, ValueError, KeyError, TypeError):
                    # Never log provider responses, codes, tokens or client secrets.
                    LOGGER.warning('GitHub sign-in could not be completed')
                    auth_failure('failed', 'GitHub 登录失败，请稍后重试', 502, [clear], login['return_to'])
                    return True
                self.users.logout(token)
                redirect(settings.public_url + '/' + quote(login['return_to'], safe='#/?=&%:+,'),
                         [clear, settings.cookie('session', session)])
                return True
            if method == 'GET' and path == '/api/account':
                visitor = cookie_value('visitor')
                headers = development_headers.copy()
                if not re.fullmatch(r'[a-f0-9]{64}', visitor):
                    visitor = secrets.token_hex(32)
                    headers.append(settings.cookie('visitor', visitor))
                reply({'user': user, 'csrfToken': digest('csrf:' + token) if user else '',
                       'loginEnabled': settings.login_enabled, 'eventsEnabled': settings.events_enabled,
                       'retentionDays': settings.retention_days, 'devMode': settings.dev_login}, headers=headers)
                return True
            if method == 'GET' and path in ('/api/account/favorites', '/api/account/reading'):
                if not user:
                    reply({'error': 'login required'}, 401)
                    return True
                page = int(params.get('page', ['1'])[0])
                if not 1 <= page <= 100000:
                    raise ValueError('Invalid page')
                reply(self.users.list_ids(user['id'], path.rsplit('/', 1)[1], page))
                return True
            if method != 'POST':
                reply({'error': 'not found'}, 404)
                return True
            origin = handler.headers.get('Origin', '')
            allowed = settings.public_url or ('http://' + handler.headers.get('Host', ''))
            if origin != allowed:
                reply({'error': 'origin rejected'}, 403)
                return True
            if path != '/api/events':
                if not user:
                    reply({'error': 'login required'}, 401)
                    return True
                if not secrets.compare_digest(handler.headers.get('X-CSRF-Token', ''), digest('csrf:' + token)):
                    reply({'error': 'csrf rejected'}, 403)
                    return True
            if handler.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                reply({'error': 'JSON required'}, 415)
                return True
            length = int(handler.headers.get('Content-Length', '0'))
            if not 0 < length <= 32768 or handler.headers.get('Transfer-Encoding'):
                reply({'error': 'invalid request size'}, 413)
                return True
            handler.connection.settimeout(15)
            body = json.loads(handler.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Expected an object')
            if path == '/api/events':
                if not settings.events_enabled:
                    reply({'accepted': 0})
                    return True
                visitor = cookie_value('visitor')
                if not re.fullmatch(r'[a-f0-9]{64}', visitor):
                    reply({'error': 'initialize browser session first'}, 400)
                    return True
                items = validate_events(body.get('events'))
                if shutil.disk_usage(self.events.path.parent).free < 1024 ** 3:
                    reply({'error': 'activity storage temporarily unavailable'}, 503)
                    return True
                accepted = self.events.record(items, digest(visitor), user['id'] if user else None)
                if accepted is None:
                    reply({'error': 'activity daily limit reached'}, 429)
                else:
                    reply({'accepted': accepted})
            elif path in ('/api/account/favorites', '/api/account/reading'):
                identifiers = valid_ids(body.get('ids'))
                if type(body.get('enabled', True)) is not bool:
                    raise ValueError('enabled must be a boolean')
                self.users.set_ids(user['id'], path.rsplit('/', 1)[1], identifiers, body.get('enabled', True))
                reply({'ok': True})
            elif path == '/api/account/logout':
                self.users.logout(token)
                reply({'ok': True}, headers=[settings.cookie('session', '', 0)])
            else:
                reply({'error': 'not found'}, 404)
        except (ValueError, TypeError, OverflowError):
            reply({'error': 'invalid request'}, 400)
        except TimeoutError:
            reply({'error': 'request timed out'}, 408)
        except sqlite3.Error:
            LOGGER.exception('User storage request failed')
            reply({'error': 'user storage temporarily unavailable'}, 503)
        return True

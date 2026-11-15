"""Compression, immutable release publication and cache safety regressions."""
import gzip
import json
import re
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_firehose.frontend_assets import publish
from paper_firehose.local_app import LocalApplication, _handler, _accepts_gzip

PACKAGE = Path(__file__).resolve().parents[1] / 'node_modules' / 'lucide'


def test_release_is_deterministic_preserves_previous_and_changes_deep_imports(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'app.js').write_text('const icon="star";\n' * 100)
    version = publish(source, '<i data-lucide="arrow-right">', PACKAGE, tmp_path / 'site', ['brain'])
    release = tmp_path / 'site' / 'assets' / version
    original = (release / 'app.js').read_bytes()
    assert gzip.decompress((release / 'app.js.gz').read_bytes()) == original
    assert publish(source, '<i data-lucide="arrow-right">', PACKAGE, tmp_path / 'site', ['brain']) == version
    data = (release / 'icon-data.js').read_text().split('export default ', 1)[1].rstrip(';\n')
    assert set(json.loads(data)) == {'star', 'arrow-right', 'brain'}
    (source / 'app.js').write_text('const icon="star";\n// changed dependency')
    new = publish(source, '<i data-lucide="arrow-right">', PACKAGE, tmp_path / 'site', ['brain'])
    assert new != version
    assert (release / 'app.js').read_bytes() == original
    assert (tmp_path / 'site' / 'assets' / new / 'app.js').is_file()
    assert not list((tmp_path / 'site' / 'assets').glob('.build-*'))


def test_missing_icon_package_never_publishes_a_partial_release(tmp_path):
    with pytest.raises(RuntimeError, match='npm ci'):
        publish(tmp_path, '', tmp_path / 'missing-package', tmp_path / 'site')
    assert not (tmp_path / 'site').exists()


def test_lucide_aliases_keep_the_exact_original_geometry(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    version = publish(source, '<i data-lucide="align-left">', PACKAGE, tmp_path / 'site')
    registry = (tmp_path / 'site' / 'assets' / version / 'icon-data.js').read_text()
    nodes = json.loads(registry.split('export default ', 1)[1].rstrip(';\n'))
    assert 'align-left' in nodes and len(nodes['align-left']) == 3
    with pytest.raises(ValueError, match='missing required icons'):
        publish(source, '<i data-lucide="missing-test-icon">', PACKAGE, tmp_path / 'site')


@pytest.mark.parametrize('header,expected', [('', False), ('gzip', True), ('br, gzip;q=0.5', True),
    ('gzip;q=0, *;q=1', False), ('*;q=1', True), ('gzip;q=bad', False), ('gzip;q=2', False)])
def test_accept_encoding_negotiation(header, expected):
    assert _accepts_gzip(header) is expected


@contextmanager
def http_site(root):
    app = LocalApplication('unused', root)
    app.repository = SimpleNamespace(bootstrap=lambda: {'content': '论文' * 2000})
    # Session-like response used to verify private data is not compressed/cached.
    app.accounts = SimpleNamespace(handle=lambda h, method, path, params:
        (h._json({'content': 'private' * 500}, cache_control='no-store'), True)[1])
    server = ThreadingHTTPServer(('127.0.0.1', 0), _handler(app))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = HTTPConnection(*server.server_address, timeout=5)
    try:
        def request(path, method='GET', **headers):
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        yield request
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join()


def test_static_compression_conditional_requests_and_cache_isolation(tmp_path):
    version = 'a' * 20
    folder = tmp_path / 'assets' / version
    folder.mkdir(parents=True)
    body = b'const value = "paper";\n' * 200
    (folder / 'app.js').write_bytes(body)
    (folder / 'app.js.gz').write_bytes(gzip.compress(body, mtime=0))
    (tmp_path / 'assets' / 'app.js').write_bytes(body)
    # Stale legacy sidecars must not override the current mutable alias.
    (tmp_path / 'assets' / 'app.js.gz').write_bytes(gzip.compress(b'old content', mtime=0))
    (tmp_path / 'index.html').write_bytes(b'<html>Paper Compass</html>')
    url = f'/assets/{version}/app.js'
    with http_site(tmp_path) as request:
        status, headers, compressed = request(url, **{'Accept-Encoding': 'gzip'})
        assert status == 200 and gzip.decompress(compressed) == body
        assert headers['Content-Encoding'] == 'gzip'
        assert headers['Vary'] == 'Accept-Encoding'
        assert 'immutable' in headers['Cache-Control']
        assert len(compressed) < len(body) / 4
        assert request(url, **{'Accept-Encoding': 'gzip', 'If-None-Match': headers['ETag']})[0] == 304
        status, identity_headers, identity = request(url, **{'Accept-Encoding': 'gzip;q=0'})
        assert identity == body and 'Content-Encoding' not in identity_headers
        assert identity_headers['ETag'] != headers['ETag']
        status, head_headers, head = request(url, 'HEAD', **{'Accept-Encoding': 'gzip'})
        assert status == 200 and head == b'' and int(head_headers['Content-Length']) == len(compressed)
        assert request('/')[1]['Cache-Control'] == 'no-cache'
        _, alias_headers, alias = request('/assets/app.js', **{'Accept-Encoding': 'gzip'})
        assert alias_headers['Cache-Control'] == 'no-cache' and gzip.decompress(alias) == body
        assert request(url + '-missing')[0] == 404
        assert request(url + '-missing')[1]['Cache-Control'] == 'no-cache'
        assert request(f'/assets/{version}/../app.js')[1]['Cache-Control'] == 'no-cache'
        assert request(f'/assets/{version}/%2e%2e/app.js')[1]['Cache-Control'] == 'no-cache'


def test_paper_json_compresses_but_account_data_does_not(tmp_path):
    with http_site(tmp_path) as request:
        status, headers, body = request('/api/bootstrap', **{'Accept-Encoding': 'gzip'})
        assert status == 200 and headers['Content-Encoding'] == 'gzip'
        assert json.loads(gzip.decompress(body))['content'].startswith('论文')
        _, headers, body = request('/api/account', **{'Accept-Encoding': 'gzip'})
        assert headers['Cache-Control'] == 'no-store' and 'Content-Encoding' not in headers
        assert json.loads(body)['content'].startswith('private')
        status, headers, _ = request('/api/unknown')
        assert status == 404 and headers['Cache-Control'] == 'no-store'

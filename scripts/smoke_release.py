#!/usr/bin/env python3
"""Verify and start an extracted release with isolated data and existing dependencies."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import venv
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def extract_verified(bundle_dir: Path, destination: Path) -> Path:
    for line in (bundle_dir / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        if Path(name).name != name or hashlib.sha256((bundle_dir / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Release checksum mismatch: ' + name)
    archives = list(bundle_dir.glob('*.tar.gz'))
    if len(archives) != 1:
        raise ValueError('Expected exactly one application archive')
    roots, seen = set(), set()
    with tarfile.open(archives[0], 'r:gz') as tar:
        for member in tar:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or '..' in name.parts or '\\' in member.name
                    or not member.isfile() or len(name.parts) < 2 or member.name in seen):
                raise ValueError('Unsafe release archive entry: ' + member.name)
            seen.add(member.name)
            roots.add(name.parts[0])
            target = destination.joinpath(*name.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
            target.chmod(member.mode & 0o777)
    if len(roots) != 1:
        raise ValueError('Archive must have one project directory')
    root = destination / roots.pop()
    manifest_bytes = (bundle_dir / 'RELEASE_MANIFEST.json').read_bytes()
    if (root / 'RELEASE_MANIFEST.json').read_bytes() != manifest_bytes:
        raise ValueError('Archive and external manifest disagree')
    manifest = json.loads(manifest_bytes)
    expected = set(manifest['files']) | {'RELEASE_MANIFEST.json'}
    actual = {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()}
    if actual != expected:
        raise ValueError('Archive file list differs from manifest')
    for name, digest in manifest['files'].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Manifest file hash mismatch: ' + name)
    return root


def smoke(bundle_dir: Path):
    with tempfile.TemporaryDirectory(prefix='paper-compass-release-smoke-') as temporary:
        scratch = Path(temporary)
        root = extract_verified(bundle_dir, scratch)
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(scratch / 'venv')
        python = str(scratch / 'venv/bin/python')
        runtime = scratch / 'runtime'
        runtime.mkdir()
        env = dict(os.environ, PAPER_FIREHOSE_DATA_DIR=str(runtime),
                   PAPER_COMPASS_DEV_LOGIN='0', PAPER_COMPASS_EVENTS_ENABLED='0',
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                   PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_INDEX='1')
        env.pop('PYTHONPATH', None)
        subprocess.run([python, '-m', 'pip', 'install', '--no-deps', '--no-build-isolation',
                        '--no-index', '-e', str(root)], env=env, cwd=scratch, check=True)
        config = runtime / 'config/config.yaml'
        prepare = '''import sys
from pathlib import Path
import paper_firehose
from paper_firehose.core.config import ConfigManager
from paper_firehose.core.database import DatabaseManager
from paper_firehose import query_store
assert Path(paper_firehose.__file__).resolve().is_relative_to(Path(sys.argv[2]))
manager=ConfigManager(sys.argv[1])
database=DatabaseManager(manager.load_config())
with database.get_connection('history'):
    pass
database.close_all_connections()
query_store.build(sys.argv[1])
'''
        subprocess.run([python, '-c', prepare, str(config), str(root)], env=env, cwd=scratch, check=True)
        with socket.socket() as port_socket:
            port_socket.bind(('127.0.0.1', 0))
            port = port_socket.getsockname()[1]
        base = f'http://127.0.0.1:{port}'

        def get(path):
            with urlopen(base + path, timeout=3) as response:
                return response.read()

        with (scratch / 'server.log').open('w+') as log:
            process = subprocess.Popen([python, '-m', 'paper_firehose.cli', '--config', str(config),
                                        'serve', '--host', '127.0.0.1', '--port', str(port)],
                                       env=env, cwd=scratch, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        if json.loads(get('/api/health'))['status'] == 'ready':
                            break
                    except (OSError, ValueError):
                        pass
                    if process.poll() is not None:
                        raise RuntimeError('Extracted release server exited before readiness')
                    time.sleep(0.25)
                else:
                    raise RuntimeError('Extracted release readiness timed out')
                html = get('/').decode()
                asset = re.search(r'assets/[a-f0-9]{20}/app\.js', html)
                if asset is None:
                    raise ValueError('Versioned frontend module missing from HTML')
                for name in ('app.js', 'icon-data.js', 'styles/app.css'):
                    if not get('/' + asset[0].removesuffix('app.js') + name):
                        raise ValueError('Empty frontend asset: ' + name)
                assert json.loads(get('/api/v1/papers?page_size=1'))['total'] == 0
                assert json.loads(get('/api/bootstrap'))['topics']
                docs = json.loads(get('/api/docs'))
                assert len(docs['documents']) == 2
                for slug in ('mcp', 'account'):
                    assert json.loads(get('/api/docs/' + slug))['html']
                try:
                    urlopen(Request(base + '/api/refresh', method='POST'), timeout=3)
                    raise AssertionError('Public refresh unexpectedly allowed')
                except HTTPError as error:
                    assert error.code == 405
                check_mcp = '''import asyncio,sys
from paper_firehose.mcp_server import create_mcp_server
server=create_mcp_server(api_url=sys.argv[1])
tools=server._tool_manager.list_tools()
assert {tool.name for tool in tools}=={'search','fetch','recent_papers','list_topics','list_sources'}
assert all(tool.annotations.readOnlyHint for tool in tools)
result=asyncio.run(server._tool_manager.call_tool('search',{'query':'smoke','limit':1}))
assert result['total']==0
'''
                subprocess.run([python, '-c', check_mcp, base], env=env, cwd=scratch, check=True)
                print('Release smoke passed: isolated install, HTTP, assets, docs, read-only API and MCP.')
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle_dir', type=Path)
    smoke(parser.parse_args().bundle_dir.resolve())

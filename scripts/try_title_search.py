#!/usr/bin/env python3
"""Isolated Meilisearch title-search trial; never writes a Paper Compass database.

Uses Python's standard library and an explicitly installed Meilisearch binary.
The index is disposable. No models, paid APIs, environment files, or PDFs are used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


LOGGER = logging.getLogger('title_search_trial')
ROOT = Path(__file__).resolve().parents[1]
QUERIES = (
    'reinforcement learning', 'distributed training', 'agent memory',
    'FlashAttention', 'FlashAttentin',
    'I/O Complexity of Attention, or How Optimal is FlashAttention?',
    '强化学习', 'zzzxqnotarealpaper987654',
)


def published_database(runtime):
    directory = runtime / 'query-store'
    name = json.loads((directory / 'current.json').read_text())['database']
    if Path(name).name != name or not name.startswith('query-'):
        raise ValueError('Invalid published database path')
    return directory / name


def documents(connection, *, with_abstract=False):
    for identifier, payload in connection.execute('SELECT id, payload FROM papers ORDER BY id'):
        paper = json.loads(payload)
        document = {
            'id': hashlib.sha256(('paper-compass:' + identifier).encode()).hexdigest(),
            'title': paper['title'], 'source': paper.get('source', ''),
            'published': paper.get('published', ''),
            'topics': paper.get('topics', []), 'venueType': paper.get('venueType', ''),
            'url': paper.get('url', ''),
        }
        if with_abstract:
            document['abstract'] = paper.get('summary', '')
        yield document


class Client:
    def __init__(self, port, key):
        self.base = f'http://127.0.0.1:{port}'
        self.key = key
        # Local paper metadata must never pass through a configured HTTP proxy.
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, payload=None, *, method=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = Request(self.base + path, data=body, method=method,
                          headers={'Authorization': 'Bearer ' + self.key,
                                   'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            detail = error.read().decode('utf-8', errors='replace')[:1000]
            raise RuntimeError(f'Meilisearch HTTP {error.code}: {detail}') from None

    def task(self, response):
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            status = self.request('/tasks/' + str(response['taskUid']))
            if status['status'] == 'succeeded':
                return status
            if status['status'] in ('failed', 'canceled'):
                raise RuntimeError('Indexing failed: ' + json.dumps(status.get('error')))
            time.sleep(0.1)
        raise TimeoutError('Indexing task exceeded ten minutes')


class MemorySampler:
    """Linux resident memory, including short indexing peaks sampled every 50 ms."""
    def __init__(self, pid):
        self.pid = pid
        self.peak_kib = 0
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._sample, daemon=True)

    def read(self):
        try:
            values = {}
            for line in Path(f'/proc/{self.pid}/status').read_text().splitlines():
                if line.startswith(('VmRSS:', 'VmHWM:', 'RssAnon:', 'RssFile:')):
                    key, value, *_ = line.split()
                    values[key.rstrip(':')] = int(value)
            self.peak_kib = max(self.peak_kib, values.get('VmHWM', 0), values.get('VmRSS', 0))
            return values.get('VmRSS', 0)
        except FileNotFoundError:
            return 0

    def _sample(self):
        while not self.stop.is_set():
            self.read()
            self.stop.wait(0.05)


def allocated_bytes(directory):
    return sum(path.stat().st_blocks * 512 for path in directory.rglob('*') if path.is_file())


def run(args):
    binary = args.binary.resolve()
    if not binary.is_file():
        raise FileNotFoundError(f'Install the official Meilisearch community binary at {binary}')
    source = args.source.resolve() if args.source else published_database(ROOT / '.runtime-demo').resolve()
    runtime = args.runtime.resolve()
    if runtime == source.parent or runtime in source.parents:
        raise ValueError('Trial runtime must be separate from the source database')
    runtime.mkdir(parents=True, exist_ok=True)
    key_path = runtime / 'master.key'
    if not key_path.exists():
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(secrets.token_urlsafe(32))
    key = key_path.read_text().strip()
    client = Client(args.port, key)
    version = subprocess.check_output([str(binary), '--version'], text=True).strip()
    command = [str(binary), '--db-path', str(runtime / 'index'),
               '--http-addr', f'127.0.0.1:{args.port}', '--no-analytics',
               '--max-indexing-memory', '128MiB', '--max-indexing-threads', '1']
    env = dict(os.environ, MEILI_MASTER_KEY=key)
    with (runtime / 'server.log').open('a') as log:
        started = time.monotonic()
        process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        sampler = MemorySampler(process.pid)
        sampler.worker.start()
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f'Meilisearch exited; see {runtime / "server.log"}')
                try:
                    client.request('/health')
                    break
                except (URLError, RuntimeError, TimeoutError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Meilisearch did not become ready in 30 seconds')
                    time.sleep(0.1)
            ready_seconds = time.monotonic() - started
            index = 'papers_title_abstract' if args.with_abstract else 'papers_title'
            # Upserting this isolated index supports repeatable trials without deletion.
            existing = client.request('/indexes?limit=100')['results']
            if index not in {item['uid'] for item in existing}:
                if args.reuse_index:
                    raise ValueError('No trial index to reuse; first run without --reuse-index')
                client.task(client.request('/indexes', {'uid': index, 'primaryKey': 'id'}))
            prefix = '/indexes/' + index
            fields = ['title', 'abstract'] if args.with_abstract else ['title']
            client.task(client.request(prefix + '/settings', {
                'searchableAttributes': fields,
                'filterableAttributes': ['source', 'topics', 'venueType'],
                'sortableAttributes': ['published'],
                'displayedAttributes': ['id', 'title', 'source', 'published', 'topics', 'venueType', 'url'],
            }, method='PATCH'))
            LOGGER.info('%s %s from read-only database %s',
                        'Reusing index for' if args.reuse_index else 'Indexing',
                        ', '.join(fields), source.name)
            imported, batch = 0, []
            indexing_started = time.monotonic()
            with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as connection:
                imported = connection.execute('SELECT count(*) FROM papers').fetchone()[0] if args.reuse_index else 0
                for document in (() if args.reuse_index else documents(connection, with_abstract=args.with_abstract)):
                    batch.append(document)
                    if len(batch) == 1000:
                        client.task(client.request(prefix + '/documents', batch))
                        imported += len(batch)
                        batch.clear()
                        if imported % 10000 == 0:
                            LOGGER.info('Indexed %d papers', imported)
                if batch:
                    client.task(client.request(prefix + '/documents', batch))
                    imported += len(batch)
            indexing_seconds = time.monotonic() - indexing_started
            rss_before_queries = sampler.read()
            tests = []
            for query in QUERIES:
                timings = []
                for _ in range(10):
                    tick = time.perf_counter()
                    response = client.request(prefix + '/search', {
                        'q': query, 'limit': 5, 'matchingStrategy': 'all',
                        'showRankingScore': True,
                    })
                    timings.append((time.perf_counter() - tick) * 1000)
                test = {'query': query, 'estimated_matches': response['estimatedTotalHits'],
                        'first_ms': round(timings[0], 3),
                        'median_ms': round(sorted(timings)[len(timings) // 2], 3),
                        'top': response['hits']}
                tests.append(test)
                LOGGER.info('%r: %d matches, %.2f ms', query, test['estimated_matches'], test['median_ms'])
            stats = client.request(prefix + '/stats')
            if stats['numberOfDocuments'] != imported:
                raise ValueError('Trial index document count differs from source; rebuild without --reuse-index')
            result = {
                'engine': version, 'source': source.name, 'fields': fields,
                'papers': imported, 'index_documents': stats['numberOfDocuments'],
                'ready_seconds': round(ready_seconds, 3),
                'indexing_seconds': round(indexing_seconds, 3),
                'reused_index': args.reuse_index,
                'engine_rss_before_queries_mib': round(rss_before_queries / 1024, 2),
                'engine_rss_mib': round(sampler.read() / 1024, 2),
                'engine_peak_rss_mib': round(sampler.peak_kib / 1024, 2),
                'index_allocated_mib': round(allocated_bytes(runtime / 'index') / 1024**2, 2),
                'binary_mib': round(binary.stat().st_size / 1024**2, 2),
                'model_loaded': False, 'paid_api_calls': 0,
                'indexing_memory_setting_mib': 128, 'indexing_threads': 1,
                'timing_note': 'Sequential local HTTP requests; not a concurrency/load benchmark',
                'queries': tests,
            }
            report = runtime / ('results-reused.json' if args.reuse_index else 'results.json')
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            LOGGER.info('Report: %s', report)
            if args.serve:
                LOGGER.info('Search preview: %s (index: %s)', client.base, index)
                LOGGER.info('Local trial key file: %s; Ctrl+C stops the trial', key_path)
                process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            sampler.stop.set()
            sampler.worker.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'tmp/meilisearch-trial/bin/meilisearch')
    parser.add_argument('--source', type=Path, help='Existing published query database; read-only')
    parser.add_argument('--runtime', type=Path, default=ROOT / '.runtime-meilisearch-trial')
    parser.add_argument('--port', type=int, default=7700)
    parser.add_argument('--with-abstract', action='store_true', help='Search title and abstract instead of title only')
    parser.add_argument('--serve', action='store_true', help='Keep the built-in local search preview running')
    parser.add_argument('--reuse-index', action='store_true', help='Measure a fresh process using an already built trial index')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use a local unprivileged port between 1024 and 65535')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        run(args)
    except KeyboardInterrupt:
        LOGGER.info('Trial stopped')
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        LOGGER.error('%s', error)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()

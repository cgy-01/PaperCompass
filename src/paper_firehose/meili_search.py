"""Publish and query title indexes alongside immutable SQLite snapshots."""
import hashlib
import json
import os
import time
from itertools import islice
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


MAX_HITS = 5000


class SearchUnavailable(RuntimeError):
    """The search service or its published index is unavailable."""


def enabled():
    return bool(os.environ.get('PAPER_COMPASS_MEILI_URL'))


def index_name(database):
    namespace = hashlib.sha256(str(Path(database).resolve()).encode()).hexdigest()[:32]
    return 'pc_titles_' + namespace


class MeiliSearch:
    def __init__(self, *, writer=False):
        self.url = os.environ['PAPER_COMPASS_MEILI_URL'].rstrip('/')
        parsed = urlsplit(self.url)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', '::1', 'localhost'}
                or parsed.username is not None or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('Meilisearch URL must be a loopback HTTP origin')
        self.key = os.environ.get('PAPER_COMPASS_MEILI_WRITE_KEY' if writer else 'PAPER_COMPASS_MEILI_SEARCH_KEY', '')
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, data=None, *, method=None, missing=False):
        headers = {'Content-Type': 'application/json'}
        if self.key:
            headers['Authorization'] = 'Bearer ' + self.key
        request = Request(self.url + path, data=None if data is None else json.dumps(data).encode(),
                          method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=5) as response:
                return json.load(response)
        except HTTPError as error:
            if missing and error.code == 404:
                return None
            raise SearchUnavailable(f'Meilisearch returned HTTP {error.code}') from None
        except (URLError, OSError, ValueError):
            raise SearchUnavailable('Meilisearch request failed') from None

    def task(self, path, data=None, *, method=None):
        job = self.request(path, data, method=method)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            task = self.request('/tasks/' + str(job['taskUid']))
            if task['status'] == 'succeeded':
                return
            if task['status'] in {'failed', 'canceled'}:
                code = (task.get('error') or {}).get('code', task['status'])
                raise SearchUnavailable(f'Meilisearch indexing task failed: {code}')
            time.sleep(0.1)
        raise SearchUnavailable('Meilisearch indexing task timed out')

    def build(self, database, conn):
        uid = index_name(database)
        path = '/indexes/' + uid
        exists = self.request(path, missing=True)
        expected = conn.execute('SELECT count(*) FROM papers').fetchone()[0]
        if exists:
            if self.request(path + '/stats')['numberOfDocuments'] == expected:
                return
            self.task(path, method='DELETE')
        self.task('/indexes', {'uid': uid, 'primaryKey': 'row_id'})
        try:
            self.task(path + '/settings', {
                'searchableAttributes': ['title'],
                'filterableAttributes': ['source', 'venue_type', 'topics', 'timestamp'],
                'displayedAttributes': ['row_id'],
                'pagination': {'maxTotalHits': MAX_HITS},
            }, method='PATCH')
            rows = iter(conn.execute('SELECT rowid, title, source, venue_type, timestamp, payload FROM papers'))
            while batch := list(islice(rows, 2000)):
                documents = [
                    {'row_id': rowid, 'title': title, 'source': source, 'venue_type': venue,
                     'timestamp': timestamp, 'topics': json.loads(payload)['topics']}
                    for rowid, title, source, venue, timestamp, payload in batch
                ]
                self.task(path + '/documents', documents)
            if self.request(path + '/stats')['numberOfDocuments'] != expected:
                raise SearchUnavailable('Meilisearch document count mismatch')
        except Exception:
            try:
                self.remove(database)
            except SearchUnavailable:
                pass
            raise

    def remove(self, database):
        path = '/indexes/' + index_name(database)
        if self.request(path, missing=True):
            self.task(path, method='DELETE')

    def search(self, conn, query, params, cutoff, page, size):
        if len(query) > 4096:
            raise ValueError('Search query is too long')
        database = conn.execute('PRAGMA database_list').fetchone()[2]
        path = '/indexes/' + index_name(database) + '/search'
        filters = []
        for parameter, field in [('type', 'venue_type'), ('source', 'source'), ('theme', 'topics')]:
            value = params.get(parameter, [''])[0]
            if value:
                filters.append(f'{field} = ' + json.dumps(value, ensure_ascii=False))
        if cutoff is not None:
            filters.append(f'timestamp >= {cutoff}')
        body = {'q': query, 'filter': filters, 'matchingStrategy': 'all',
                'page': min(page, (MAX_HITS + size - 1) // size), 'hitsPerPage': size}
        result = self.request(path, body)
        pages = max(1, result['totalPages'])
        page = min(page, pages)
        if page != result['page']:
            result = self.request(path, dict(body, page=page))
        rowids = [hit['row_id'] for hit in result['hits']]
        found = {}
        if rowids:
            placeholders = ','.join('?' for _ in rowids)
            found = dict(conn.execute(f'SELECT rowid,payload FROM papers WHERE rowid IN ({placeholders})', rowids))
        if len(found) != len(rowids):
            raise SearchUnavailable('Meilisearch and SQLite snapshots differ')
        return {'items': [json.loads(found[rowid]) for rowid in rowids],
                'total': result['totalHits'], 'page': page, 'pages': pages,
                'searchMode': 'meilisearch', 'sort': 'relevance', 'searchField': 'title'}

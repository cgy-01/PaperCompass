"""Incremental arXiv monitoring with stable identities and bounded API catch-up."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import feedparser

from ..core.config import ConfigManager
from ..core.database import DatabaseManager
from ..core.http_client import RetryableHTTPClient
from ..core.paths import resolve_data_path
from .dashboard import CURATED_TOPIC_KEYS, _topic_catalog
from ..classification import PENDING_TOPIC, collection_classifier

LOGGER = logging.getLogger(__name__)
API_URL = 'https://export.arxiv.org/api/query'
IDENTIFIER = re.compile(r'(?:arxiv:|arxiv\.org/(?:abs|pdf)/|oai:arxiv\.org:)([a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v(\d+))?', re.I)


def identity(entry):
    found = None
    for value in (entry.get('id', ''), entry.get('link', ''), entry.get('summary', '')):
        match = IDENTIFIER.search(value)
        if match:
            base, version = match[1], int(match[2] or 0)
            if found is None:
                found = (base, version)
            elif found[0] == base:
                found = (base, max(found[1], version))
    if found:
        return found
    raise ValueError('Missing valid arXiv identifier')


def _window(previous, now):
    if not previous:
        return now - timedelta(days=7), now
    prior = datetime.fromisoformat(previous).astimezone(timezone.utc)
    if prior > now:
        raise ValueError('arXiv watermark is in the future')
    return prior - timedelta(days=2), now


def _parse(response, *, api=False):
    parsed = feedparser.parse(response.content)
    if parsed.bozo or not parsed.get('feed') or not parsed.feed.get('title'):
        raise ValueError('Invalid arXiv feed response; not an empty successful result')
    if api and 'opensearch_totalresults' not in parsed.feed:
        raise ValueError('arXiv API response lacks pagination metadata')
    return parsed


def _api_entries(client, category, start, end):
    # submittedDate would miss new revisions of old papers. Walk updates until
    # the overlap boundary, with a hard cap that fails rather than hiding gaps.
    query = f'cat:{category}'
    items = []
    for offset in range(0, 10000, 200):
        response = client.get_with_retry(API_URL, params={
            'search_query': query, 'start': offset, 'max_results': 200,
            'sortBy': 'lastUpdatedDate', 'sortOrder': 'descending'}, return_none_on_404=False)
        if response is None:
            raise RuntimeError('arXiv API retries exhausted')
        parsed = _parse(response, api=True)
        total = int(parsed.feed.opensearch_totalresults)
        if not parsed.entries and offset < total:
            raise ValueError('arXiv API returned a premature empty page')
        boundary = False
        for entry in parsed.entries:
            identity(entry)  # Reject API error entries before marking the page successful.
            stamp = datetime.fromisoformat((entry.get('updated') or entry.get('published') or '').replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('Missing timezone in arXiv API entry')
            if stamp < start:
                boundary = True
            elif stamp <= end:
                items.append(entry)
        LOGGER.info('arXiv API progress %s: %d/%d', category, len(items), total)
        if boundary or offset + len(parsed.entries) >= total:
            return items
    raise ValueError('arXiv API pagination did not finish')


def _targets(manager):
    enabled = manager.get_enabled_feeds()
    targets = {}
    classifier = collection_classifier(_topic_catalog(manager))
    available = set(manager.get_available_topics())
    for key in CURATED_TOPIC_KEYS:
        if key not in available:
            continue
        config = manager.load_topic_config(key)
        for feed in config.get('feeds', []):
            settings = enabled.get(feed, {})
            url = settings.get('url', '')
            parsed = urlparse(url)
            if parsed.hostname not in {'rss.arxiv.org', 'export.arxiv.org', 'arxiv.org'}:
                continue
            category = parsed.path.rstrip('/').rsplit('/', 1)[-1]
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9.-]*', category):
                continue
            # Sources determine what to fetch, never which research directions
            # a paper is allowed to belong to (e.g. an Agent paper in cs.CV).
            targets.setdefault(category, {'url': url, 'name': settings.get('name', feed),
                                          'classifier': classifier})
    return targets


def _save(path, state):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(state, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _ingest(conn, entry, target, existing, versions, stats):
    base, version = identity(entry)
    old = existing.get(base)
    known_version = versions.get(base, 0)
    if version and version < known_version:
        stats['unchanged'] += 1
        return
    title = ' '.join(entry.get('title', '').split())
    summary = entry.get('summary', '').strip()
    authors = ', '.join(author.get('name', '') for author in entry.get('authors', []) if author.get('name')) or entry.get('author', '')
    if not title or not summary or not authors:
        raise ValueError('Incomplete arXiv title, abstract or authors: ' + base)
    topics = {match.topic['key'] for match in target['classifier'].classify(title, summary)}
    pending = not topics and target['classifier'].is_candidate(title, summary)
    if not topics and not old and not pending:
        stats['irrelevant'] += 1
        return
    if old:
        topics.update(part.strip() for part in (old['topics'] or '').split(',')
                      if part.strip() and part.strip() != PENDING_TOPIC)
    if pending:
        topics.add(PENDING_TOPIC)
    published = entry.get('published') or entry.get('updated')
    if not published:
        raise ValueError('Missing arXiv publication date: ' + base)
    try:
        date = datetime.fromisoformat(published.replace('Z', '+00:00'))
    except ValueError:
        date = parsedate_to_datetime(published)
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    published = date.astimezone(timezone.utc).isoformat()
    # Keep legacy SHA-1 IDs and original publication dates so saved references survive.
    key = old['entry_id'] if old else 'arxiv:' + base
    link = 'https://arxiv.org/abs/' + base
    values = {'title': title, 'link': link, 'summary': summary, 'abstract': summary,
              'authors': authors, 'topics': ', '.join(sorted(topics))}
    if old:
        if any(old[field] != value for field, value in values.items()):
            conn.execute('''UPDATE matched_entries SET title=?,link=?,summary=?,abstract=?,authors=?,topics=?
                            WHERE entry_id=?''', (*values.values(), key))
            old.update(values)
            stats['updated'] += 1
        else:
            stats['unchanged'] += 1
    else:
        conn.execute('''INSERT INTO matched_entries
            (entry_id,feed_name,topics,title,link,summary,authors,abstract,published_date,matched_date)
            VALUES (?,?,?,?,?,?,?,?,?,?)''', (
            key, target['name'], values['topics'], title, link, summary, authors, summary,
            published, datetime.now(timezone.utc).isoformat()))
        existing[base] = {'entry_id': key, **values}
        stats['inserted'] += 1
        if pending:
            stats['inserted_pending'] += 1
    versions[base] = max(version, known_version)


def run(config_path, *, catch_up=True, rss=True):
    """Read current RSS and recover bounded submission windows, never full history."""
    manager = ConfigManager(config_path)
    if not manager.validate_config():
        raise ValueError('Configuration validation failed')
    targets = _targets(manager)
    if not targets:
        raise ValueError('No configured arXiv feeds for curated research topics')
    path = resolve_data_path('arxiv-sync-state.json')
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(state.get('sources'), dict) or not isinstance(state.get('versions'), dict):
            raise ValueError('Invalid arXiv state')
    except FileNotFoundError:
        state = {'sources': {}, 'versions': {}}
    db = DatabaseManager(manager.load_config())
    stats = {'inserted': 0, 'inserted_pending': 0, 'updated': 0, 'unchanged': 0,
             'irrelevant': 0, 'failed': 0, 'sources': {}}
    try:
        with db.get_connection('history') as conn:
            existing = {}
            for row in conn.execute("SELECT entry_id,link,topics,title,authors,summary,abstract FROM matched_entries WHERE lower(feed_name) LIKE 'arxiv%'"):
                try:
                    base, revision = identity({'link': row['link']})
                except ValueError:
                    continue
                existing.setdefault(base, dict(row))
                state['versions'][base] = max(state['versions'].get(base, 0), revision)
            # arXiv legacy API terms: one connection and at least three seconds between requests.
            with RetryableHTTPClient(rps=1/3, timeout=45) as client:
                for category, target in targets.items():
                    report = stats['sources'][category] = {}
                    source_state = state['sources'].setdefault(category, {})
                    for channel in (['rss'] if rss else []) + (['api'] if catch_up else []):
                        try:
                            now = datetime.now(timezone.utc)
                            before = {key: stats[key] for key in ['inserted', 'inserted_pending', 'updated', 'unchanged', 'irrelevant']}
                            details = {}
                            if channel == 'rss':
                                response = client.get_with_retry(target['url'], return_none_on_404=False)
                                if response is None:
                                    raise RuntimeError('arXiv RSS retries exhausted')
                                parsed = _parse(response)
                                entries = parsed.entries
                                success_time = now
                                fingerprint = hashlib.sha256(response.content).hexdigest()
                                details = {'unchanged_feed': fingerprint == source_state.get('rss_fingerprint'),
                                           'source_updated': parsed.feed.get('updated') or parsed.feed.get('lastbuilddate')}
                            else:
                                start, success_time = _window(source_state.get('api_success'), now)
                                LOGGER.info('arXiv catch-up %s: %s to %s', category, start.isoformat(), success_time.isoformat())
                                entries = _api_entries(client, category, start, success_time)
                            for index, entry in enumerate(entries, 1):
                                _ingest(conn, entry, target, existing, state['versions'], stats)
                                if index % 100 == 0:
                                    conn.commit()
                            conn.commit()
                            source_state[channel + '_success'] = success_time.isoformat()
                            if channel == 'rss':
                                source_state['rss_fingerprint'] = fingerprint
                            report[channel] = {'ok': True, 'received': len(entries), 'empty': not entries,
                                               **details, **{key: stats[key] - value for key, value in before.items()}}
                            _save(path, state)
                            LOGGER.info('arXiv %s %s: received=%d totals=%s', channel, category, len(entries),
                                        {key: value for key, value in stats.items() if key != 'sources'})
                        except Exception as exc:
                            # Earlier committed batches are safe to replay by stable ID.
                            conn.commit()
                            stats['failed'] += 1
                            report[channel] = {'ok': False, 'error': str(exc)}
                            LOGGER.exception('arXiv %s failed: %s', channel, category)
            return stats
    finally:
        db.close_all_connections()

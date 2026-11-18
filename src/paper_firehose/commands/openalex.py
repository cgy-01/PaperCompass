"""Retrieve historical journal and conference papers from OpenAlex."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import time
from datetime import date, timedelta
from typing import Any

import requests

from ..core.config import ConfigManager
from ..core.database import DatabaseManager
from .dashboard import CURATED_TOPIC_KEYS, _topic_catalog
from ..classification import TopicClassifier

logger = logging.getLogger(__name__)
API_URL = "https://api.openalex.org/works"

# Stable OpenAlex source identifiers for high-priority AI/CS venues. Acronym
# search is unreliable (for example, ICLR also means a law review), so these
# resolved IDs provide a deterministic top-venue lane alongside broad search.
TOP_VENUE_SOURCE_IDS = {
    "S4306419644",  # ICML
    "S4306419637",  # ICLR
    "S4363606243",  # NeurIPS proceedings metadata
    "S4306418267",  # EMNLP
    "S4306418318",  # ECCV
    "S4210191458",  # AAAI
    "S4306421123",  # USENIX Security
    "S4306417705",  # ASPLOS
    "S4306418317",  # EuroSys
}


def _request(params: dict[str, Any]) -> dict[str, Any] | None:
    """Call OpenAlex with bounded 429 retries; preserve local results on quota exhaustion."""
    for attempt in range(3):
        response = requests.get(API_URL, params=params, timeout=45)
        if response.status_code != 429:
            response.raise_for_status()
            return response.json()
        if attempt < 2:
            delay = min(float(response.headers.get("Retry-After", 2 ** (attempt + 1))), 10)
            logger.warning("OpenAlex rate limited; retrying in %.1fs", delay)
            time.sleep(delay)
    logger.warning("OpenAlex quota exhausted; keeping already stored results")
    return None


def _abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    words = [(position, word) for word, positions in index.items() for position in positions]
    return " ".join(word for _, word in sorted(words))


def _authors(work: dict[str, Any]) -> str:
    return ", ".join(
        item.get("author", {}).get("display_name", "")
        for item in work.get("authorships", [])
        if item.get("author", {}).get("display_name")
    )


def _normal_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _source(work: dict[str, Any]) -> tuple[str, str]:
    source = (work.get("primary_location") or {}).get("source") or {}
    source_type = source.get("type") or ""
    source_id = (source.get("id") or "").rsplit("/", 1)[-1]
    venue_type = "会议" if source_id in TOP_VENUE_SOURCE_IDS else {
        "conference": "会议", "journal": "期刊"
    }.get(source_type, "")
    return source.get("display_name") or "Unknown venue", venue_type


def _upsert(conn, work: dict[str, Any], topic: str, *, dedupe_title: bool = True) -> str:
    title = (work.get("title") or work.get("display_name") or "").strip()
    if not title:
        return "skipped"
    venue, venue_type = _source(work)
    if not venue_type:
        return "skipped"
    doi = (work.get("doi") or "").removeprefix("https://doi.org/")
    external_id = work.get("id") or ""
    if external_id.startswith("https://openalex.org/"):
        entry_id = "openalex:" + external_id.rsplit("/", 1)[-1]
    else:
        # Official proceedings often reuse numeric/sluggified URL tails across
        # years and venues. Hash the complete canonical URL to preserve each
        # venue-edition membership independently.
        identity = external_id or f"{venue}\n{title}"
        entry_id = "official:" + hashlib.sha256(identity.encode()).hexdigest()
    normalized = _normal_title(title)
    location = work.get("primary_location") or {}
    link = location.get("landing_page_url") or work.get("doi") or external_id
    abstract = _abstract(work.get("abstract_inverted_index"))
    authors = _authors(work)

    # Resolve the indexed primary key first. Combining it with an unindexed
    # external_id in an OR forces a full table scan on the historical database.
    candidates = conn.execute(
        "SELECT * FROM matched_entries WHERE entry_id = ?", (entry_id,),
    ).fetchall()
    if not candidates and dedupe_title:
        candidates = conn.execute(
            "SELECT * FROM matched_entries WHERE external_id = ?", (external_id,),
        ).fetchall()
    if not candidates and dedupe_title and doi:
        candidates = conn.execute(
        "SELECT * FROM matched_entries WHERE doi = ?", (doi,),
        ).fetchall()
    if not candidates and dedupe_title:
        candidates = conn.execute(
            "SELECT * FROM matched_entries WHERE lower(title) = lower(?)",
            (title,),
        ).fetchall()
    existing = next((row for row in candidates if row["entry_id"] == entry_id or (dedupe_title and _normal_title(row["title"]) == normalized)), None)
    topics = {topic}
    if existing:
        topics.update(part.strip() for part in (existing["topics"] or "").split(",") if part.strip())
        if not dedupe_title:
            # Official directory rescans frequently contain exactly the same
            # record. Avoid UPDATE and its full-text-index triggers in that case.
            desired = {
                'topics': ', '.join(sorted(topics)), 'venue_name': venue,
                'venue_type': venue_type, 'external_id': external_id, 'link': link,
                'summary': abstract or existing['summary'], 'authors': authors or existing['authors'],
                'abstract': abstract or existing['abstract'], 'doi': existing['doi'] or doi,
                'cited_by_count': int(work.get('cited_by_count') or 0),
                'published_date': existing['published_date'] or work.get('publication_date'),
            }
            if all(existing[key] == value for key, value in desired.items()):
                return 'skipped'
        conn.execute(
            """UPDATE matched_entries SET topics=?, venue_name=?, venue_type=?, external_id=?, link=?,
                      summary=COALESCE(NULLIF(?,''),summary), authors=COALESCE(NULLIF(?,''),authors),
                      abstract=COALESCE(NULLIF(?,''),abstract),
                      doi=COALESCE(NULLIF(doi,''),?), cited_by_count=?,
                      published_date=COALESCE(NULLIF(published_date,''),?) WHERE entry_id=?""",
            (", ".join(sorted(topics)), venue, venue_type, external_id, link,
             abstract, authors, abstract, doi,
             int(work.get("cited_by_count") or 0), work.get("publication_date"), existing["entry_id"]),
        )
        return "updated"

    cited = int(work.get("cited_by_count") or 0)
    # A stable display score for historical records; topic matching was already
    # established by OpenAlex search and local regex validation below.
    score = min(0.95, 0.45 + math.log1p(cited) / 20)
    conn.execute(
        """INSERT INTO matched_entries
           (entry_id, feed_name, topics, title, link, summary, authors, abstract, doi,
            published_date, matched_date, rank_score, venue_name, venue_type,
            external_id, cited_by_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'),?,?,?,?,?)""",
        (entry_id, venue, topic, title, link, abstract,
         authors, abstract, doi,
         work.get("publication_date"), score, venue, venue_type, external_id, cited),
    )
    return "inserted"


def run(config_path: str, topic: str | None = None, *, days: int = 1095,
        per_topic: int = 100, paginate: bool = False) -> dict[str, Any]:
    """Search journal/conference metadata and merge it into history."""
    manager = ConfigManager(config_path)
    config = manager.load_config()
    available = set(manager.get_available_topics())
    topics = [topic] if topic else [key for key in CURATED_TOPIC_KEYS if key in available]
    catalog = _topic_catalog(manager)
    if topic and not any(item['key'] == topic for item in catalog):
        # The manual --topic command also supports topics outside the curated
        # website menu. Keep that existing CLI contract intact.
        custom = manager.load_topic_config(topic)
        entry = {'key': topic, 'pattern': custom['filter']['pattern'],
                 'min_matches': custom['filter'].get('min_matches', 1)}
        if 'classification' in custom:
            entry['classification'] = custom['classification']
        catalog.append(entry)
    classifier = TopicClassifier(catalog)
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    since = (date.today() - timedelta(days=days)).isoformat()
    db = DatabaseManager(config)
    totals = {"inserted": 0, "updated": 0, "skipped": 0}
    try:
        with db.get_connection("history") as conn:
            for key in topics:
                topic_config = manager.load_topic_config(key)
                query = (topic_config.get("ranking") or {}).get("query") or topic_config.get("name", key)
                # Query each formal source class independently. A combined
                # query sorted by date is easily monopolized by journals,
                # making the conference filter in the UI appear empty.
                # Conference acceptance is authoritative only when ingested
                # from the conference-owned adapters in official_sources.py.
                # OpenAlex remains useful for journals and metadata enrichment.
                for source_type in ("journal",):
                    params = {
                        "search": query,
                        "filter": f"from_publication_date:{since},primary_location.source.type:{source_type}",
                        "per_page": min(max(per_topic, 1), 100),
                        "sort": "publication_date:desc",
                        "select": "id,doi,title,publication_date,authorships,abstract_inverted_index,primary_location,cited_by_count",
                    }
                    if api_key:
                        params["api_key"] = api_key
                    if paginate:
                        params['cursor'] = '*'
                    seen_cursors = set()
                    while True:
                        payload = _request(params)
                        if payload is None:
                            logger.warning('OpenAlex query failed for %s', key)
                            break
                        for work in payload.get("results", []):
                            matched = {match.topic['key'] for match in classifier.classify(
                                work.get('title') or '', _abstract(work.get('abstract_inverted_index')))}
                            if key not in matched:
                                totals["skipped"] += 1
                                continue
                            totals[_upsert(conn, work, key)] += 1
                        conn.commit()
                        cursor = payload.get('meta', {}).get('next_cursor')
                        if not paginate or not payload.get('results') or not cursor:
                            break
                        if cursor in seen_cursors:
                            logger.warning('OpenAlex repeated cursor for %s', key)
                            break
                        seen_cursors.add(cursor)
                        params['cursor'] = cursor
                    logger.info("OpenAlex topic '%s' source type '%s' processed", key, source_type)

    finally:
        db.close_all_connections()
    return totals

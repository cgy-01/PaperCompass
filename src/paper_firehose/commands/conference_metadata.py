"""Incrementally enrich official conference papers from their detail pages."""

from __future__ import annotations

import html
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from typing import Any, Callable

import requests

from ..core.config import ConfigManager
from ..core.database import DatabaseManager

logger = logging.getLogger(__name__)


class _MetadataParser(HTMLParser):
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__()
        self.authors: list[str] = []
        self.author_parts: list[str] = []
        self.abstract_parts: list[str] = []
        self.description = ""
        self._author_capture_depth = 0
        self._author_em_depth = 0
        self._capture_depth = 0
        self._heading = ""
        self._in_heading = False
        self._next_paragraph = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        key = values.get("name") or values.get("property") or ""
        if tag == "meta" and key.lower() == "citation_author" and values.get("content"):
            self.authors.append(html.unescape(values["content"]).strip())
        if tag == "meta" and key.lower() in {"og:description", "twitter:description", "description"}:
            candidate = html.unescape(values.get("content", "")).strip()
            if len(candidate) > len(self.description): self.description = candidate

        classes = set(values.get("class", "").lower().split())
        identifier = values.get("id", "").lower()
        is_usenix_authors = "field-name-field-paper-people-text" in classes
        if not self._author_capture_depth and is_usenix_authors:
            self._author_capture_depth = 1
        elif self._author_capture_depth and tag not in self._VOID:
            self._author_capture_depth += 1
        if self._author_capture_depth and tag == "em":
            self._author_em_depth += 1
        is_abstract = (
            identifier in {"abstract", "abstracttext"}
            or bool(classes & {"abstract-content", "abstract-text-inner", "acl-abstract"})
            or any("paper-description" in item for item in classes)
        )
        if not self._capture_depth and is_abstract:
            self._capture_depth = 1
        elif self._capture_depth and tag not in self._VOID:
            self._capture_depth += 1
        if tag in {"h2", "h3", "h4", "h5"}:
            self._in_heading = True; self._heading = ""
        if tag == "p" and self._next_paragraph and not self._capture_depth:
            self._capture_depth = 1; self._next_paragraph = False

    def handle_data(self, data: str) -> None:
        if self._in_heading: self._heading += data
        if self._author_capture_depth and not self._author_em_depth:
            self.author_parts.append(data)
        if self._capture_depth: self.abstract_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._author_capture_depth:
            self._author_capture_depth -= 1
        if tag == "em" and self._author_em_depth:
            self._author_em_depth -= 1
        if self._capture_depth:
            self._capture_depth -= 1
        if tag in {"h2", "h3", "h4", "h5"} and self._in_heading:
            self._next_paragraph = self._heading.strip().lower() == "abstract"
            self._in_heading = False

    def result(self) -> tuple[str, str]:
        authors = ", ".join(dict.fromkeys(name for name in self.authors if name))
        if not authors and self.author_parts:
            authors = " ".join(" ".join(self.author_parts).split()).strip(" ,;")
        abstract = " ".join(" ".join(self.abstract_parts).split())
        abstract = re.sub(r"^Abstract\s*", "", abstract, flags=re.IGNORECASE)
        if len(abstract) < 80 and len(self.description) >= 80:
            abstract = self.description
        return authors, abstract


def _fetch(url: str) -> tuple[str, str]:
    headers = {"User-Agent": "PaperCompass/0.5 (local academic metadata tool)"}
    for attempt in range(3):
        response = requests.get(url, headers=headers, timeout=60)
        if response.status_code == 429:
            time.sleep(min(float(response.headers.get("Retry-After", 2 ** attempt)), 15)); continue
        response.raise_for_status()
        parser = _MetadataParser(); parser.feed(response.text)
        return parser.result()
    return "", ""


def run(config_path: str, *, workers: int = 6, limit: int | None = None,
        venue: str | None = None, venues: list[str] | None = None,
        progress_callback: Callable[[dict[str, int]], None] | None = None) -> dict[str, int]:
    """Fill missing authors/abstracts for official proceedings; safe to resume."""
    manager = ConfigManager(config_path); db = DatabaseManager(manager.load_config())
    totals = {"candidates": 0, "updated": 0, "failed": 0}
    try:
        with db.get_connection("history") as conn:
            sql = """SELECT entry_id, link FROM matched_entries
                     WHERE entry_id LIKE 'official:%'
                       AND (trim(coalesce(authors,''))='' OR trim(coalesce(abstract,''))='')"""
            params: list[Any] = []
            selected_venues = ([venue] if venue else []) + (venues or [])
            selected_venues = list(dict.fromkeys(selected_venues))
            if selected_venues:
                sql += " AND venue_name IN (" + ",".join("?" for _ in selected_venues) + ")"
                params.extend(selected_venues)
            # Prefer the newest proceedings so the most useful current-year
            # metadata appears first during a large legacy migration.
            sql += " ORDER BY published_date DESC, venue_name"
            if limit is not None:
                sql += " LIMIT ?"; params.append(limit)
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
            totals["candidates"] = len(rows)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_fetch, row["link"]): row for row in rows}
                pending: list[tuple[str, str, str, str]] = []
                for future in as_completed(futures):
                    row = futures[future]
                    try: authors, abstract = future.result()
                    except requests.RequestException as exc:
                        logger.debug("Metadata fetch failed %s: %s", row["link"], exc)
                        totals["failed"] += 1; continue
                    if not authors and not abstract:
                        totals["failed"] += 1; continue
                    pending.append((authors, abstract, abstract, row["entry_id"]))
                    if len(pending) >= 100:
                        conn.executemany("""UPDATE matched_entries SET
                            authors=COALESCE(NULLIF(?,''),authors),
                            abstract=COALESCE(NULLIF(?,''),abstract),
                            summary=COALESCE(NULLIF(?,''),summary)
                            WHERE entry_id=?""", pending)
                        conn.commit(); totals["updated"] += len(pending); pending.clear()
                        if progress_callback: progress_callback(dict(totals))
                if pending:
                    conn.executemany("""UPDATE matched_entries SET
                        authors=COALESCE(NULLIF(?,''),authors),
                        abstract=COALESCE(NULLIF(?,''),abstract),
                        summary=COALESCE(NULLIF(?,''),summary)
                        WHERE entry_id=?""", pending)
                    conn.commit(); totals["updated"] += len(pending)
                    if progress_callback: progress_callback(dict(totals))
    finally:
        db.close_all_connections()
    return totals

"""Ingest accepted papers from conference-owned publication sources."""

from __future__ import annotations

import logging
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo
from typing import Any
from urllib.parse import urljoin

import requests

from ..core.config import ConfigManager
from ..core.database import DatabaseManager
from ..core.paths import resolve_data_path
from .dashboard import _topic_catalog
from ..classification import TopicClassifier, collection_classifier
from .openalex import _upsert

logger = logging.getLogger(__name__)

# Official proceedings only. Adding a year is a data change, not a scraper rewrite.
PMLR_VOLUMES = {
    "https://proceedings.mlr.press/v202/": ("ICML 2023", "2023-07-23"),
    "https://proceedings.mlr.press/v235/": ("ICML 2024", "2024-07-21"),
    "https://proceedings.mlr.press/v267/": ("ICML 2025", "2025-07-13"),
}
OFFICIAL_INDEXES = {
    "https://openaccess.thecvf.com/CVPR2023?day=all": ("CVPR 2023", "2023-06-18", "cvf"),
    "https://openaccess.thecvf.com/CVPR2024?day=all": ("CVPR 2024", "2024-06-17", "cvf"),
    "https://openaccess.thecvf.com/CVPR2025?day=all": ("CVPR 2025", "2025-06-11", "cvf"),
    "https://openaccess.thecvf.com/CVPR2026?day=all": ("CVPR 2026", "2026-06-05", "cvf"),
    "https://openaccess.thecvf.com/ICCV2023?day=all": ("ICCV 2023", "2023-10-02", "cvf"),
    "https://openaccess.thecvf.com/ICCV2025?day=all": ("ICCV 2025", "2025-10-19", "cvf"),
    "https://proceedings.neurips.cc/paper_files/paper/2023": ("NeurIPS 2023", "2023-12-10", "neurips"),
    "https://proceedings.neurips.cc/paper_files/paper/2024": ("NeurIPS 2024", "2024-12-10", "neurips"),
    "https://proceedings.neurips.cc/paper_files/paper/2025/vol38-main-conference": ("NeurIPS 2025", "2025-12-02", "neurips"),
    "https://aclanthology.org/events/acl-2024/": ("ACL 2024", "2024-08-11", "acl"),
    "https://aclanthology.org/events/acl-2025/": ("ACL 2025", "2025-07-27", "acl"),
    "https://aclanthology.org/events/acl-2026/": ("ACL 2026", "2026-07-02", "acl"),
    "https://aclanthology.org/events/emnlp-2024/": ("EMNLP 2024", "2024-11-12", "emnlp"),
    "https://aclanthology.org/events/emnlp-2025/": ("EMNLP 2025", "2025-11-05", "emnlp"),
    "https://aclanthology.org/events/naacl-2024/": ("NAACL 2024", "2024-06-16", "naacl"),
    "https://aclanthology.org/events/naacl-2025/": ("NAACL 2025", "2025-04-29", "naacl"),
    "https://iclr.cc/virtual/2023/papers.html": ("ICLR 2023", "2023-05-01", "iclr"),
    "https://iclr.cc/virtual/2024/papers.html": ("ICLR 2024", "2024-05-07", "iclr"),
    "https://iclr.cc/virtual/2025/papers.html": ("ICLR 2025", "2025-04-24", "iclr"),
    "https://iclr.cc/virtual/2026/papers.html": ("ICLR 2026", "2026-04-23", "iclr"),
    "https://proceedings.mlsys.org/paper_files/paper/2023": ("MLSys 2023", "2023-06-04", "mlsys"),
    "https://proceedings.mlsys.org/paper_files/paper/2024": ("MLSys 2024", "2024-05-13", "mlsys"),
    "https://proceedings.mlsys.org/paper_files/paper/2025": ("MLSys 2025", "2025-05-12", "mlsys"),
    "https://proceedings.mlsys.org/paper_files/paper/2026": ("MLSys 2026", "2026-05-17", "mlsys"),
    "https://www.usenix.org/conference/osdi23/technical-sessions": ("OSDI 2023", "2023-07-10", "usenix"),
    "https://www.usenix.org/conference/osdi24/technical-sessions": ("OSDI 2024", "2024-07-10", "usenix"),
    "https://www.usenix.org/conference/osdi25/technical-sessions": ("OSDI 2025", "2025-07-07", "usenix"),
    "https://www.usenix.org/conference/nsdi23/technical-sessions": ("NSDI 2023", "2023-04-17", "usenix"),
    "https://www.usenix.org/conference/nsdi24/technical-sessions": ("NSDI 2024", "2024-04-16", "usenix"),
    "https://www.usenix.org/conference/nsdi25/technical-sessions": ("NSDI 2025", "2025-04-28", "usenix"),
    "https://www.usenix.org/conference/atc23/technical-sessions": ("USENIX ATC 2023", "2023-07-10", "usenix"),
    "https://www.usenix.org/conference/atc24/technical-sessions": ("USENIX ATC 2024", "2024-07-10", "usenix"),
    "https://www.usenix.org/conference/atc25/technical-sessions": ("USENIX ATC 2025", "2025-07-07", "usenix"),
}


class _ProceedingsParser(HTMLParser):
    def __init__(self, base: str, mode: str):
        super().__init__(); self.base=base; self.mode=mode; self.items=[]; self.href=""; self.text=[]; self.in_title=False; self.in_heading=False; self.link_in_heading=False; self.in_paper=False
        match=re.search(r"(/conference/(?:osdi|nsdi|atc)\d+/)",base)
        self.conference_path=match.group(1) if match else ""
    def handle_starttag(self, tag, attrs):
        values=dict(attrs)
        if tag == "article" and "node-paper" in values.get("class", "").split(): self.in_paper=True
        if tag == "p" and "title" in values.get("class", "").split(): self.in_title=True; self.text=[]
        if tag in {"h2","h3"}: self.in_heading=True
        if tag == "a": self.href=values.get("href", ""); self.text=[]; self.link_in_heading=self.in_heading
    def handle_data(self, data):
        if self.href or self.in_title: self.text.append(data)
    def handle_endtag(self, tag):
        if tag == "p" and self.in_title:
            title=" ".join("".join(self.text).split()); self.in_title=False
            if title: self.items.append({"title":title,"url":""})
        if tag == "a" and self.href:
            text=" ".join("".join(self.text).split()); href=urljoin(self.base,self.href)
            valid=(self.mode=="neurips" and "Abstract-Conference.html" in href) or (self.mode=="mlsys" and re.search(r"-Abstract-(?:Conference|mlsys20\d\d)\.html$",href)) or (self.mode=="iclr" and re.search(r"/virtual/20\d\d/(?:poster|oral)/\d+$",href)) or (self.mode=="cvf" and "/html/" in href and href.endswith("_paper.html")) or (self.mode in {"acl","emnlp","naacl"} and re.search(rf"/20\d\d\.{self.mode}-(?:main|long|short)\.\d+/?$",href)) or (self.mode=="usenix" and self.in_paper and self.link_in_heading and self.conference_path in href and re.search(r"/presentation/[^/?#]+$",href))
            if valid and text and len(text)>12: self.items.append({"title":text,"url":href})
            elif self.mode=="pmlr" and self.items and not self.items[-1]["url"] and href.endswith(".html"): self.items[-1]["url"]=href
            self.href=""; self.text=[]; self.link_in_heading=False
        if tag in {"h2","h3"}: self.in_heading=False
        if tag == "article": self.in_paper=False


def _work(item: dict[str,Any], venue: str, published: str) -> dict[str,Any]:
    abstract=item.get("abstract","")
    inverted: dict[str,list[int]]={}
    for position,word in enumerate(abstract.split()): inverted.setdefault(word,[]).append(position)
    return {"id":item["url"],"title":item["title"],"publication_date":published,"doi":"",
            "authorships":[{"author":{"display_name":name}} for name in item.get("authors",[])],
            "abstract_inverted_index":inverted,"cited_by_count":0,
            "primary_location":{"landing_page_url":item["url"],"source":{"display_name":venue,"type":"conference"}}}


def _topics(manager: ConfigManager, title: str) -> list[str]:
    return [match.topic['key'] for match in TopicClassifier(_topic_catalog(manager)).classify(title)]


def _fetch_html(url: str, mode: str, *, validators=None, response_metadata=None) -> list[dict[str,str]] | None:
    headers = {"User-Agent":"PaperCompass/0.4 (local research tool)"}
    for key, header in [('etag', 'If-None-Match'), ('last_modified', 'If-Modified-Since')]:
        if validators and validators.get(key):
            headers[header] = validators[key]
    response=requests.get(url,timeout=60,headers=headers)
    response.raise_for_status()
    if response_metadata is not None:
        response_metadata.update({key: response.headers.get(header) for key, header in
                                  [('etag', 'ETag'), ('last_modified', 'Last-Modified')]})
    if response.status_code == 304:
        return None
    parser=_ProceedingsParser(url,mode); parser.feed(response.text)
    seen=set(); result=[]
    for item in parser.items:
        if item["url"] and item["title"] not in seen: seen.add(item["title"]); result.append(item)
    return result


def _fetch_iclr(year: int, *, strict: bool = False) -> list[dict[str,Any]]:
    """Fetch one ICLR edition from its two official bulk metadata files."""
    base=f"https://iclr.cc/static/virtual/data/iclr-{year}"
    headers={"User-Agent":"PaperCompass/0.5 (local research tool)"}
    response=requests.get(f"{base}-orals-posters.json",timeout=180,headers=headers)
    response.raise_for_status()
    try:
        abstracts_response=requests.get(f"{base}-abstracts.json",timeout=180,headers=headers)
        abstracts_response.raise_for_status(); abstracts=abstracts_response.json()
    except requests.RequestException:
        if strict:
            raise
        abstracts={}
    items=[]
    for raw in response.json().get("results",[]):
        if raw.get("eventtype") != "Poster": continue
        paper_id=str(raw.get("id") or "")
        title=(raw.get("name") or "").strip()
        url=raw.get("virtualsite_url") or f"https://iclr.cc/virtual/{year}/poster/{paper_id}"
        if url.startswith("/"): url=urljoin("https://iclr.cc",url)
        authors=[a.get("fullname","").strip() for a in raw.get("authors",[]) if a.get("fullname")]
        abstract=abstracts.get(paper_id) or raw.get("abstract") or ""
        if title and paper_id: items.append({"title":title,"url":url,"authors":authors,"abstract":abstract})
    return items


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _source_due(state, year, now):
    try:
        previous = datetime.fromisoformat(state['last_success']).astimezone(ZoneInfo('Asia/Shanghai'))
    except (KeyError, ValueError, TypeError):
        return True
    today = now.astimezone(ZoneInfo('Asia/Shanghai'))
    elapsed = (today.date() - previous.date()).days
    return elapsed >= (1 if year >= today.year - 1 else 7)


def _save_source_state(path, state):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(state, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def run(config_path: str, family: str = "all", *, enrich_metadata: bool = True,
        metadata_workers: int = 12, incremental: bool = False) -> dict[str,int]:
    manager=ConfigManager(config_path); db=DatabaseManager(manager.load_config()); totals={"inserted":0,"updated":0,"skipped":0}
    sources=[(u,*meta,"pmlr") for u,meta in PMLR_VOLUMES.items()]+[(u,*meta) for u,meta in OFFICIAL_INDEXES.items()]
    if family == "cvf": sources=[item for item in sources if item[3] == "cvf"]
    elif family == "ml": sources=[item for item in sources if item[3] in {"pmlr","neurips","iclr"}]
    elif family == "nlp": sources=[item for item in sources if item[3] in {"acl","emnlp","naacl"}]
    elif family == "infra": sources=[item for item in sources if item[3] in {"mlsys","usenix"}]
    elif family == "iclr": sources=[item for item in sources if item[3] == "iclr"]
    elif family == "iclr2026": sources=[item for item in sources if item[1] == "ICLR 2026"]
    elif family == "latest": sources=[item for item in sources if item[1] in {"CVPR 2026","NeurIPS 2025","ACL 2026","MLSys 2026","ICLR 2026"}]
    selected_venues=[item[1] for item in sources]
    catalog = _topic_catalog(manager)
    classifier = collection_classifier(catalog)
    totals['failed'] = 0
    totals.update({'sources_deferred': 0, 'sources_unchanged': 0, 'items_unchanged': 0})
    now = datetime.now(timezone.utc)
    rules = _fingerprint([(topic['key'], topic['pattern']) for topic in catalog])
    state_root = resolve_data_path('conference-sync') if incremental else None
    if state_root:
        state_root.mkdir(parents=True, exist_ok=True)
    try:
        with db.get_connection("history") as conn:
            for url,venue,published,mode in sources:
                state, state_path = {}, None
                identity = _fingerprint([str(db.db_paths['history']), url, venue, published, mode, rules]) if incremental else None
                if incremental:
                    state_path = state_root / (_fingerprint(url) + '.json')
                    try:
                        state = json.loads(state_path.read_text(encoding='utf-8'))
                        if (not isinstance(state, dict) or state.get('identity') != identity
                                or not isinstance(state.get('items'), dict)):
                            state = {}
                    except FileNotFoundError:
                        pass
                    except (ValueError, TypeError):
                        logger.warning('Invalid source state; rescanning %s', venue)
                        state = {}
                    year = int(venue.rsplit(' ', 1)[-1])
                    if not _source_due(state, year, now):
                        totals['sources_deferred'] += 1
                        logger.info('Official source deferred by incremental schedule: %s', venue)
                        continue
                logger.info('Official source starting: %s (%s)', venue, url)
                response_metadata = {}
                try:
                    if mode == 'iclr':
                        year = int(venue.rsplit(' ', 1)[-1])
                        items = _fetch_iclr(year, strict=True) if incremental else _fetch_iclr(year)
                    elif incremental:
                        items = _fetch_html(url, mode, validators=state.get('validators'),
                                            response_metadata=response_metadata)
                    else:
                        items = _fetch_html(url, mode)
                except requests.RequestException as exc:
                    totals['failed'] += 1
                    logger.warning("Official source failed %s: %s",url,exc)
                    continue
                if items is None:
                    if not state.get('last_success'):
                        totals['failed'] += 1
                        logger.warning('Unexpected 304 without a previous successful sync: %s', venue)
                        continue
                    state['last_success'] = datetime.now(timezone.utc).isoformat()
                    _save_source_state(state_path, state)
                    totals['sources_unchanged'] += 1
                    logger.info('Official source unchanged (HTTP 304): %s', venue)
                    continue
                if not items:
                    totals['failed'] += 1
                    logger.warning('Official source returned an empty directory: %s', venue)
                    continue
                logger.info('Official source fetched: %s, %d papers', venue, len(items))
                fingerprints = {item['url']: _fingerprint(item) for item in items} if incremental else {}
                previous_items = state.get('items', {})
                for index, item in enumerate(items, 1):
                    if incremental and previous_items.get(item['url']) == fingerprints[item['url']]:
                        totals['items_unchanged'] += 1
                        if index % 200 == 0 or index == len(items):
                            conn.commit()
                            logger.info('Official source progress %s: %d/%d; unchanged=%d',
                                        venue, index, len(items), totals['items_unchanged'])
                        continue
                    keys = [match.topic['key'] for match in classifier.classify(item['title'], item.get('abstract', ''))]
                    # Conference completeness comes before topic classification.
                    # Untagged papers remain browseable by venue and can receive
                    # curated AI labels later without being fetched again.
                    if not keys: keys=["ccf_a_all"]
                    for key in keys: totals[_upsert(conn,_work(item,venue,published),key,dedupe_title=False)]+=1
                    if index % 200 == 0 or index == len(items):
                        conn.commit()
                        logger.info('Official source progress %s: %d/%d; totals=%s',
                                    venue, index, len(items), totals)
                logger.info("Official source %s: %d papers scanned",venue,len(items))
                conn.commit()  # Release write locks before fetching the next proceedings page.
                if incremental:
                    _save_source_state(state_path, {'identity': identity, 'last_success': datetime.now(timezone.utc).isoformat(),
                                                   'items': fingerprints, 'validators': response_metadata})
    finally: db.close_all_connections()
    if enrich_metadata and selected_venues:
        # Metadata hydration is part of conference ingestion, not a separate
        # user workflow. It is resumable because already complete rows are
        # excluded by conference_metadata.run.
        from . import conference_metadata
        logger.info('Official metadata hydration starting: %d venues, %d workers',
                    len(selected_venues), metadata_workers)
        metadata=conference_metadata.run(config_path,workers=metadata_workers,venues=selected_venues)
        totals["metadata_updated"]=metadata["updated"]
        totals["metadata_failed"]=metadata["failed"]
        logger.info('Official metadata hydration finished: %s', metadata)
    return totals

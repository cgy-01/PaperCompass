"""Generate the self-contained Paper Compass research dashboard."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..core.config import ConfigManager
from ..core.database import DatabaseManager
from ..core.paths import get_system_path, resolve_data_path


THEME_GROUPS = {
    "AI 与机器学习": {"AI", "LG", "NE", "MA"},
    "视觉、图形与多媒体": {"CV", "GR", "CG", "MM"},
    "语言、检索与知识": {"CL", "IR", "DL"},
    "系统与计算架构": {"AR", "DC", "OS", "PF", "CE"},
    "网络与安全": {"NI", "CR"},
    "数据、软件与编程语言": {"DB", "SE", "PL"},
    "理论计算机科学": {"CC", "DM", "DS", "FL", "LO", "GT"},
    "人机交互与计算社会": {"HC", "CY", "SI"},
    "机器人与控制": {"RO", "SY"},
    "科学计算与信息论": {"NA", "SC", "MS", "IT"},
}

CONFERENCE_MARKERS = {
    "neurips", "icml", "iclr", "aaai", "ijcai", "acl", "emnlp", "naacl",
    "cvpr", "iccv", "eccv", "sigcomm", "nsdi", "osdi", "sosp", "usenix",
    "sigmod", "vldb", "kdd", "www", "chi", "security", "oakland",
}

CURATED_TOPIC_KEYS = [
    "llm", "agents", "ai_infra", "reasoning_post_training",
    "multimodal_generative", "ai_safety_alignment", "data_evaluation",
    "embodied_robotics", "reinforcement_learning", "ai_for_science",
    "vision_graphics", "ml_foundations", "language_speech_retrieval",
    "systems_architecture",
]

TOPIC_ICONS = {
    "llm": "message-square-code", "agents": "waypoints", "ai_infra": "server-cog",
    "reasoning_post_training": "brain-circuit", "multimodal_generative": "images",
    "ai_safety_alignment": "shield-check", "data_evaluation": "database",
    "embodied_robotics": "bot", "reinforcement_learning": "repeat-2",
    "ai_for_science": "flask-conical",
    "vision_graphics": "scan-eye", "ml_foundations": "binary",
    "language_speech_retrieval": "languages", "systems_architecture": "cpu",
}

# The product is conference-first. Journals outside this compact computer-
# science shortlist stay in the database but do not clutter the dashboard.
CORE_JOURNAL_PATTERNS = (
    r"\bjournal of machine learning research\b|\bjmlr\b",
    r"\btransactions on pattern analysis and machine intelligence\b|\btpami\b",
    r"^artificial intelligence$|\bartificial intelligence journal\b",
    r"\bieee/acm transactions on networking\b|\btransactions on networking\b",
    r"\bacm transactions on computer systems\b|\btocs\b",
    r"\bacm transactions on storage\b",
    r"\bacm transactions on architecture and code optimization\b|\btaco\b",
)


def _is_core_journal(name: str) -> bool:
    return any(re.search(pattern, name, re.IGNORECASE) for pattern in CORE_JOURNAL_PATTERNS)


def _feed_code(feed_name: str) -> str:
    value = feed_name.strip()
    if value.lower().startswith("arxiv cs."):
        return value.rsplit(".", 1)[-1].upper()
    return ""


def _classify(feed_name: str) -> tuple[str, str]:
    lowered = feed_name.lower()
    code = _feed_code(feed_name)
    if code:
        venue_type = "预印本"
    elif any(re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", lowered) for marker in CONFERENCE_MARKERS):
        venue_type = "会议"
    else:
        venue_type = "期刊"

    theme = "跨学科与其他"
    for group, codes in THEME_GROUPS.items():
        if code in codes:
            theme = group
            break
    return venue_type, theme


def _topic_catalog(manager: ConfigManager) -> list[dict[str, Any]]:
    catalog = []
    available = set(manager.get_available_topics())
    for key in CURATED_TOPIC_KEYS:
        if key not in available:
            continue
        config = manager.load_topic_config(key)
        catalog.append({
            "key": key,
            "name": config.get("name", key),
            "description": config.get("description", ""),
            "icon": TOPIC_ICONS.get(key, "circle-dot"),
            "pattern": config.get("filter", {}).get("pattern", ""),
            "min_matches": int(config.get("filter", {}).get("min_matches", 1)),
        })
    return catalog


def _read_entries(db: DatabaseManager, days: int | None, catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(_iter_entries(db, days, catalog))


def _iter_entries(db: DatabaseManager, days: int | None, catalog: list[dict[str, Any]], *, rows=None):
    compiled_topics: list[tuple[dict[str, Any], re.Pattern[str]]] = []
    for topic in catalog:
        if not topic["pattern"]:
            continue
        try:
            compiled_topics.append((topic, re.compile(topic["pattern"], re.IGNORECASE)))
        except re.error:
            continue
    sql = """SELECT entry_id, feed_name, topics, title, link, summary, authors,
                    abstract, doi, published_date, matched_date, llm_summary,
                    paper_qa_summary, rank_score, venue_name, venue_type,
                    external_id, cited_by_count
             FROM matched_entries"""
    params: list[Any] = []
    if days is not None:
        sql += " WHERE date(COALESCE(published_date, matched_date)) >= date('now', ?)"
        params.append(f"-{days} days")
    sql += " ORDER BY COALESCE(published_date, matched_date) DESC, rank_score DESC"

    with db.get_connection("history") as conn:
        for row in (rows if rows is not None else conn.execute(sql, params)):
            item = dict(row)
            inferred_type, fallback_theme = _classify(item.get("feed_name") or "未知来源")
            venue_type = item.get("venue_type") or inferred_type
            if venue_type == "会议" and str(item.get("external_id") or "").startswith("https://openalex.org/"):
                venue_type = "待核验来源"
            source_name = item.get("venue_name") or item.get("feed_name") or "未知来源"
            title = item.get("title") or ""
            summary = item.get("summary") or ""
            abstract = item.get("abstract") or ""
            # Official adapters intentionally store the abstract in both legacy
            # columns. Count it once so min_matches remains a real relevance guard.
            searchable = abstract if abstract.strip() == summary.strip() else f"{summary} {abstract}"
            matched_topics = []
            for topic, pattern in compiled_topics:
                title_hit = pattern.search(title)
                body_hits = pattern.findall(searchable)
                if title_hit or len(body_hits) >= topic["min_matches"]:
                    matched_topics.append(topic)
            topic_names = [topic["name"] for topic in matched_topics]
            topic_keys = [topic["key"] for topic in matched_topics]
            external_id = str(item.get("external_id") or "")
            is_official_conference = venue_type == "会议" and external_id and not external_id.startswith("https://openalex.org/")
            if venue_type == "期刊" and not _is_core_journal(source_name):
                continue
            # Official conference proceedings are complete venue collections;
            # preprints and journals still require a selected AI-topic match.
            if catalog and not matched_topics and not is_official_conference:
                continue
            # Untagged official proceedings stay browseable by venue, but an
            # internal absence of a topic must not become a user-facing tag.
            theme = topic_names[0] if topic_names else ""
            yield {
                "id": item.get("entry_id") or "",
                "title": item.get("title") or "未命名论文",
                "url": item.get("link") or "",
                "source": source_name,
                "venueType": venue_type,
                "theme": theme,
                "topics": topic_names,
                "topicKeys": topic_keys,
                "authors": item.get("authors") or "作者未知",
                "summary": item.get("abstract") or item.get("summary") or "暂无摘要。",
                "insight": item.get("paper_qa_summary") or item.get("llm_summary") or "",
                "doi": item.get("doi") or "",
                "published": item.get("published_date") or item.get("matched_date") or "",
                "matched": item.get("matched_date") or "",
                "score": round(float(item.get("rank_score") or 0), 4),
                "citations": int(item.get("cited_by_count") or 0),
            }


def run(config_path: str, output: str | None = None, days: int | None = None) -> Path:
    """Render the lightweight dashboard shell; paper data is served by APIs."""
    manager = ConfigManager(config_path)
    if not manager.validate_config():
        raise ValueError("Configuration validation failed")

    template = get_system_path("templates", "dashboard_template.html").read_text(encoding="utf-8")
    generated_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    generated_iso = generated_at.isoformat(timespec="minutes")
    generated_display = generated_at.strftime("%Y-%m-%d %H:%M（UTC+8）")
    html = (template.replace("{{GENERATED_AT_ISO}}", generated_iso)
            .replace("{{GENERATED_AT}}", generated_display))

    output_path = Path(output).expanduser() if output else resolve_data_path("html", "index.html", ensure_parent=True)
    if not output_path.is_absolute():
        output_path = resolve_data_path("html", str(output_path), ensure_parent=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(get_system_path('frontend'), output_path.parent / 'assets', dirs_exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    lucide_source = Path(__file__).resolve().parents[3] / "node_modules" / "lucide" / "dist" / "umd" / "lucide.min.js"
    if lucide_source.is_file():
        shutil.copyfile(lucide_source, output_path.parent / "lucide.min.js")
    return output_path

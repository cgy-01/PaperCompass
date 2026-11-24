"""Read-only MCP facade for the Paper Compass HTTP API."""

from __future__ import annotations

import os
from math import ceil
from typing import Any, Literal
from urllib.parse import urlparse

import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations


DEFAULT_API_URL = "http://127.0.0.1:8765"
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


class PaperCompassClient:
    """Small bounded client for the existing read-only Paper Compass API."""

    def __init__(
        self,
        base_url: str = DEFAULT_API_URL,
        *,
        timeout: float = 15.0,
        session: requests.Session | None = None,
    ) -> None:
        value = base_url.rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("api_url must be an absolute http(s) URL")
        self.base_url = value
        self.timeout = timeout
        self.session = session or requests.Session()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}{path}", params=params, timeout=self.timeout
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(f"Paper Compass API unavailable: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Paper Compass API returned an invalid response")
        return payload

    def search(
        self,
        *,
        query: str = "",
        topic: str = "",
        source: str = "",
        venue_type: str = "",
        days: int = 0,
        sort: str = "similarity",
        page: int = 1,
        limit: int = 10,
        search_field: str = "all",
    ) -> dict[str, Any]:
        page = max(1, page)
        limit = min(50, max(1, limit))
        params: dict[str, Any] = {
            "q": query.strip(),
            "theme": topic.strip(),
            "source": source.strip(),
            "type": venue_type.strip(),
            "days": max(0, days),
            "sort": sort if sort in {"date", "score", "title", "similarity"} else "similarity",
            "page": page,
            "page_size": limit,
            "search_field": search_field if search_field in {'all', 'title'} else 'all',
        }
        return self._get("/api/papers", params=params)

    def fetch(self, paper_id: str) -> dict[str, Any] | None:
        payload = self._get("/api/papers", params={"ids": paper_id})
        items = payload.get("items") or []
        return items[0] if items else None

    def bootstrap(self) -> dict[str, Any]:
        return self._get("/api/bootstrap")


def _paper_result(paper: dict[str, Any]) -> dict[str, Any]:
    abstract = paper.get("summary") or "暂无摘要。"
    authors = paper.get("authors") or "作者未知"
    source = paper.get("source") or "未知来源"
    published = paper.get("published") or "日期未知"
    topics = paper.get("topics") or []
    return {
        "id": paper.get("id", ""),
        "title": paper.get("title", "未命名论文"),
        "url": paper.get("url", ""),
        "text": (
            f"作者：{authors}\n来源：{source}\n发布日期：{published}\n"
            f"主题：{', '.join(topics) or '未归类'}\n摘要：{abstract}"
        ),
        "metadata": {
            "authors": authors,
            "source": source,
            "venue_type": paper.get("venueType", ""),
            "published": published,
            "topics": topics,
            "doi": paper.get("doi", ""),
            "citations": paper.get("citations", 0),
            "relevance_score": paper.get("score", 0),
        },
    }


def _paged_result(
    payload: dict[str, Any],
    *,
    requested_page: int,
    page_size: int,
) -> dict[str, Any]:
    """Convert a paginated API response into an MCP-friendly continuation."""
    items = payload.get("items", [])
    total = int(payload.get("total", 0))
    page = int(payload.get("page", requested_page))
    pages = int(payload.get("pages", max(1, ceil(total / page_size))))
    has_more = page < pages
    return {
        "results": [_paper_result(item) for item in items],
        "total": total,
        "returned": len(items),
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "has_more": has_more,
        "next_page": page + 1 if has_more else None,
    }


def create_mcp_server(
    *,
    api_url: str = DEFAULT_API_URL,
    host: str = "127.0.0.1",
    port: int = 8766,
    allowed_hosts: list[str] | None = None,
    client: PaperCompassClient | None = None,
) -> FastMCP:
    """Create the MCP server without opening sockets (useful for tests too)."""
    paper_api = client or PaperCompassClient(api_url)
    host_allowlist = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"]
    host_allowlist.extend(allowed_hosts or [])
    server = FastMCP(
        "Paper Compass",
        instructions=(
            "Search the read-only Paper Compass AI-paper catalog by title or keywords. "
            "Use search for keywords and find_paper for a known title. "
            "Search results are ranked by relevance; "
            "use recent_papers for date-ordered literature monitoring. Results contain metadata and abstracts, "
            "not the complete paper; use each result URL to access the original source. "
            ""
            ""
            "Search responses are paginated. Do not automatically exhaust all pages. "
            "Use total and has_more to judge coverage, and request next_page only when another "
            "page is useful for the user's stated goal. Stop when enough relevant evidence has "
            "been collected. Traverse every page only when the user explicitly asks for all "
            "matches or an exhaustive result set; keep filters and sort unchanged when paging."
        ),
        website_url="https://papercompass.cn",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=host_allowlist),
    )

    @server.tool(
        name="search",
        description=(
            "Search papers by title keywords. "
            "Results are ordered by relevance when the query is nonempty. "
            "Optional filters use exact Paper Compass topic/source display names. Results are "
            "paginated; use next_page only when more results are needed for the user's goal."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def search(
        query: str,
        topic: str = "",
        source: str = "",
        venue_type: Literal["", "会议", "期刊", "预印本", "待核验来源"] = "",
        days: int = 0,
        sort: Literal["date", "score", "title", "similarity"] = "similarity",
        page: int = 1,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Return one bounded result page and an explicit continuation."""
        page = max(1, page)
        page_size = min(50, max(1, limit))
        payload = paper_api.search(
            query=query,
            topic=topic,
            source=source,
            venue_type=venue_type,
            days=days,
            sort=sort,
            page=page,
            limit=page_size,
        )
        result = _paged_result(payload, requested_page=page, page_size=page_size)
        result["search_mode"] = payload.get("searchMode", "title")
        result["sort"] = payload.get("sort", "date" if sort == "similarity" else sort) if query.strip() else sort
        return result

    @server.tool(
        name="find_paper",
        description=(
            "Find a specific paper by its complete or approximate title, with spelling tolerance. "
            "Returns compact candidate metadata, IDs and original URLs, without abstracts; verify title/authors/source "
            "before choosing a candidate, then use fetch(id) to read its complete abstract."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def find_paper(title: str, source: str = "", page: int = 1, limit: int = 5) -> dict[str, Any]:
        if not title.strip():
            raise ValueError('A paper title is required')
        page, page_size = max(1, page), min(50, max(1, limit))
        payload = paper_api.search(query=title, source=source, sort='similarity',
                                   search_field='title', page=page, limit=page_size)
        result = _paged_result(payload, requested_page=page, page_size=page_size)
        for candidate in result['results']:
            candidate.pop('text', None)
        result.update(search_mode=payload.get('searchMode', 'title'), search_field='title',
                      sort=payload.get('sort', 'date'))
        return result

    @server.tool(
        name="fetch",
        description="Fetch complete catalog metadata and abstract for one Paper Compass paper ID.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def fetch(id: str) -> dict[str, Any]:
        """Fetch a single paper previously returned by search."""
        paper = paper_api.fetch(id)
        if paper is None:
            raise ValueError(f"Paper not found: {id}")
        result = _paper_result(paper)
        result["metadata"]["matched"] = paper.get("matched", "")
        result["metadata"]["insight"] = paper.get("insight", "")
        return result

    @server.tool(
        name="recent_papers",
        description=(
            "List the newest papers, optionally filtered by topic, source or venue type. "
            "Results are paginated; use next_page only when more results are needed."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def recent_papers(
        days: int = 7,
        topic: str = "",
        source: str = "",
        venue_type: Literal["", "会议", "期刊", "预印本", "待核验来源"] = "",
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(50, max(1, limit))
        payload = paper_api.search(
            topic=topic,
            source=source,
            venue_type=venue_type,
            days=max(1, days),
            sort="date",
            page=page,
            limit=page_size,
        )
        result = _paged_result(payload, requested_page=page, page_size=page_size)
        result["window_days"] = max(1, days)
        return result

    @server.tool(
        name="list_topics",
        description="List valid research-topic filters and their current paper counts.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def list_topics() -> dict[str, Any]:
        topics = paper_api.bootstrap().get("topics", [])
        return {"topics": topics, "count": len(topics)}

    @server.tool(
        name="list_sources",
        description="List conference, journal and preprint sources with paper counts.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def list_sources(venue_type: str = "", limit: int = 100) -> dict[str, Any]:
        sources = paper_api.bootstrap().get("sources", [])
        if venue_type:
            sources = [item for item in sources if item.get("type") == venue_type]
        bounded = sources[: min(250, max(1, limit))]
        return {"sources": bounded, "count": len(bounded), "total": len(sources)}

    return server


def run_mcp_server(
    *,
    transport: Literal["stdio", "streamable-http"] = "stdio",
    api_url: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8766,
    allowed_hosts: list[str] | None = None,
) -> None:
    """Run the MCP facade using stdio locally or Streamable HTTP remotely."""
    server = create_mcp_server(
        api_url=api_url or os.getenv("PAPER_COMPASS_API_URL", DEFAULT_API_URL),
        host=host,
        port=port,
        allowed_hosts=allowed_hosts,
    )
    server.run(transport=transport)

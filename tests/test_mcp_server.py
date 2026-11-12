import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from paper_firehose.mcp_server import PaperCompassClient, create_mcp_server


PAPER = {
    "id": "official:test-paper",
    "title": "Efficient LLM Serving",
    "authors": "Ada Lovelace; Alan Turing",
    "summary": "A serving systems paper.",
    "source": "MLSys 2026",
    "venueType": "会议",
    "topics": ["AI Infra", "大模型（LLM）"],
    "published": "2026-05-01",
    "matched": "2026-05-02",
    "url": "https://example.test/paper",
    "doi": "10.1000/test",
    "score": 0.82,
    "citations": 4,
    "insight": "Cached summary",
}


class FakePaperClient:
    def __init__(self):
        self.last_search = None

    def search(self, **kwargs):
        self.last_search = kwargs
        page = kwargs.get("page", 1)
        return {"items": [PAPER], "total": 12, "page": page, "pages": 3}

    def fetch(self, paper_id):
        return PAPER if paper_id == PAPER["id"] else None

    def bootstrap(self):
        return {
            "topics": [{"key": "ai_infra", "name": "AI Infra", "count": 8}],
            "sources": [
                {"name": "MLSys 2026", "type": "会议", "count": 6},
                {"name": "arXiv cs.DC", "type": "预印本", "count": 2},
            ],
        }


def _call(server, name, arguments=None):
    return asyncio.run(server._tool_manager.call_tool(name, arguments or {}))


def test_mcp_exposes_only_bounded_read_tools():
    server = create_mcp_server(client=FakePaperClient())
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
    assert set(tools) == {"search", "fetch", "recent_papers", "list_topics", "list_sources"}
    assert all(tool.annotations.readOnlyHint for tool in tools.values())
    assert all(tool.annotations.destructiveHint is False for tool in tools.values())


def test_mcp_pagination_instructions_do_not_encourage_automatic_exhaustion():
    server = create_mcp_server(client=FakePaperClient())
    assert "Do not automatically exhaust all pages" in server.instructions
    assert "only when the user explicitly asks" in server.instructions


def test_search_returns_agent_friendly_paginated_results_and_bounds_limit():
    client = FakePaperClient()
    server = create_mcp_server(client=client)
    result = _call(
        server,
        "search",
        {"query": "serving", "topic": "AI Infra", "page": 2, "limit": 500},
    )
    assert result["total"] == 12
    assert result["results"][0]["id"] == PAPER["id"]
    assert "摘要：A serving systems paper." in result["results"][0]["text"]
    assert client.last_search["query"] == "serving"
    assert client.last_search["page"] == 2
    assert client.last_search["limit"] == 50
    assert result["page"] == 2
    assert result["pages"] == 3
    assert result["has_more"] is True
    assert result["next_page"] == 3


def test_search_last_page_has_no_continuation():
    server = create_mcp_server(client=FakePaperClient())
    result = _call(server, "search", {"query": "serving", "page": 3})
    assert result["has_more"] is False
    assert result["next_page"] is None


def test_recent_papers_supports_pagination():
    client = FakePaperClient()
    server = create_mcp_server(client=client)
    result = _call(server, "recent_papers", {"days": 30, "page": 2, "limit": 5})
    assert client.last_search["page"] == 2
    assert client.last_search["limit"] == 5
    assert result["page"] == 2
    assert result["next_page"] == 3
    assert result["window_days"] == 30


def test_fetch_and_catalog_tools():
    server = create_mcp_server(client=FakePaperClient())
    paper = _call(server, "fetch", {"id": PAPER["id"]})
    assert paper["metadata"]["doi"] == "10.1000/test"
    assert paper["metadata"]["insight"] == "Cached summary"
    assert _call(server, "list_topics")["topics"][0]["name"] == "AI Infra"
    sources = _call(server, "list_sources", {"venue_type": "会议"})
    assert [item["name"] for item in sources["sources"]] == ["MLSys 2026"]


def test_fetch_unknown_paper_fails_cleanly():
    server = create_mcp_server(client=FakePaperClient())
    with pytest.raises(ToolError, match="Paper not found"):
        _call(server, "fetch", {"id": "missing"})


def test_api_url_must_be_http():
    with pytest.raises(ValueError, match="absolute http"):
        PaperCompassClient("file:///tmp/papers.db")

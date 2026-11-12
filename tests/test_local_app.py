import pytest
from unittest.mock import Mock

from paper_firehose.local_app import DocumentationRepository, PaperRepository
from paper_firehose import query_store
from test_query_store import catalog_store


@pytest.fixture
def repository(catalog_store):
    query_store.build('unused')
    return PaperRepository('unused')


def test_failed_index_refresh_keeps_previous_repository(monkeypatch, repository):
    from paper_firehose import local_app
    app = local_app.LocalApplication.__new__(local_app.LocalApplication)
    app.config_path = 'unused'
    app.repository = repository
    monkeypatch.setattr(local_app, 'PaperRepository', lambda _: (_ for _ in ()).throw(RuntimeError('busy')))
    app.load_repository()
    assert app.repository is repository
    assert app.repository_error == 'busy'


def test_read_api_filters_and_paginates(repository):
    result = repository.query({'theme': ['AI Infra'], 'page': ['1'], 'page_size': ['1']})
    assert result['total'] == 2
    assert result['pages'] == 2
    assert [paper['id'] for paper in result['items']] == ['new']


def test_read_api_searches_and_resolves_saved_ids(repository):
    assert repository.query({'q': ['infrastructure']})['total'] == 2
    result = repository.query({'ids': ['old,new']})
    assert [paper['id'] for paper in result['items']] == ['old', 'new']


def test_startup_never_classifies_or_opens_raw_database(repository, monkeypatch):
    monkeypatch.setattr(query_store.dashboard, '_iter_entries',
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('classified at startup')))
    monkeypatch.setattr(query_store, 'ConfigManager',
                        lambda *args: (_ for _ in ()).throw(AssertionError('raw configuration loaded')))
    reopened = PaperRepository('unused')
    assert reopened.query({'page': ['2'], 'page_size': ['1']})['items'][0]['id'] == 'old'


def test_bootstrap_contains_only_aggregates_and_latest_subset(repository):
    payload = repository.bootstrap()
    assert payload['overview']['total'] == 2
    assert payload['topics'][0]['count'] == 2
    assert payload['sources'] == [{'name': 'ICLR 2026', 'type': '会议', 'count': 2}]
    assert len(payload['overview']['latest']) == 2


def test_serve_generates_shell_starts_loader_and_closes_server(tmp_path, monkeypatch):
    from paper_firehose import local_app
    monkeypatch.setattr(local_app, 'resolve_data_path', lambda *args, **kwargs: tmp_path)
    render = Mock()
    monkeypatch.setattr(local_app.dashboard, 'run', render)
    app = Mock()
    monkeypatch.setattr(local_app, 'LocalApplication', Mock(return_value=app))
    server = Mock()
    server.serve_forever.side_effect = KeyboardInterrupt
    factory = Mock(return_value=server)
    monkeypatch.setattr(local_app, 'ThreadingHTTPServer', factory)

    local_app.serve('config.yaml', host='127.0.0.1', port=8765)

    render.assert_called_once_with('config.yaml', output=str(tmp_path / 'index.html'))
    assert factory.call_args.args[0] == ('127.0.0.1', 8765)
    app.start_repository_loader.assert_called_once()
    server.serve_forever.assert_called_once()
    server.server_close.assert_called_once()


def test_documentation_repository_renders_allowlisted_markdown(tmp_path):
    (tmp_path / "MCP_USER_GUIDE.md").write_text(
        "# MCP 用户指南\n\n```bash\ncodex mcp list\n```\n\n"
        "[部署](mcp_setup.md)\n\n![MCP 设置](./img/mcp_guide.png)",
        encoding="utf-8",
    )
    (tmp_path / "mcp_setup.md").write_text("# 部署说明", encoding="utf-8")
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "mcp_guide.png").write_bytes(b"png")
    repository = DocumentationRepository(tmp_path)

    assert [item["slug"] for item in repository.list()] == ["mcp"]
    document = repository.get("mcp")
    assert document["title"] == "MCP 用户指南"
    assert '<pre><code class="language-bash">' in document["html"]
    assert 'src="/api/docs/assets/img/mcp_guide.png"' in document["html"]
    assert 'data-doc-slug="mcp-deployment"' not in document["html"]
    assert repository.get("mcp-deployment") is None
    assert repository.get("../../secrets") is None
    assert repository.asset("img/mcp_guide.png") == tmp_path / "img" / "mcp_guide.png"
    assert repository.asset("../MCP_USER_GUIDE.md") is None

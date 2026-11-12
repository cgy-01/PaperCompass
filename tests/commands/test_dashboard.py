from pathlib import Path

from paper_firehose.commands import dashboard


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""
database:
  path: "{tmp_path / 'papers.db'}"
  all_feeds_path: "{tmp_path / 'all.db'}"
  history_path: "{tmp_path / 'history.db'}"
feeds: {{}}
priority_journals: []
defaults:
  time_window_days: 365
  top_n_per_topic: 10
  rank_threshold: 0.3
  ranking_negative_penalty: 0.25
""",
        encoding="utf-8",
    )
    return path


def test_classify_arxiv_and_conference():
    assert dashboard._classify("arXiv cs.DC") == ("预印本", "系统与计算架构")
    assert dashboard._classify("ACM SIGCOMM") == ("会议", "跨学科与其他")
    assert dashboard._classify("Nature Machine Intelligence")[0] == "期刊"
    assert dashboard._is_core_journal("Journal of Machine Learning Research")
    assert dashboard._is_core_journal("IEEE Transactions on Pattern Analysis and Machine Intelligence")
    assert not dashboard._is_core_journal("Nature Materials")


def test_dashboard_generates_lightweight_api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_FIREHOSE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(dashboard.ConfigManager, "validate_config", lambda self: True)
    config = _config(tmp_path)

    output = tmp_path / "dashboard.html"
    dashboard.run(str(config), output=str(output))

    html = output.read_text(encoding="utf-8")
    assert "Paper Compass · AI 研究工作台" in html
    assert "主要主题分布" in html
    assert 'id="sourceDonut"' in html
    assert 'id="yearTrend"' in html
    assert "function drawYearTrend()" in html
    assert "近 30 天新增" in html
    assert "论文信息完整度" in html
    assert "Agent Systems" not in html
    assert "Complete Proceedings Paper" not in html
    assert "Unrelated Journal Paper" not in html
    assert "sort:'date'" in html
    assert '<option value="date">按时间：最新优先</option><option value="score">按分数：高分优先</option>' in html
    assert "api('/api/bootstrap')" in html
    assert "api('/api/papers?'" in html
    assert "api('/api/docs')" in html
    assert "api('/api/docs/'" in html
    assert "error.status===503" in html
    assert "正在载入论文索引" in html
    assert "['docs','使用文档']" in html
    assert 'data-lucide="book-open-text"' not in html
    assert "docs:'book-open-text'" in html
    assert 'id="docsDirectory"' in html
    assert 'id="docArticle"' in html
    assert "function openDiscover(filters)" in html
    assert "openDiscover({source:c.dataset.source,type:c.dataset.sourceType})" in html
    assert "openDiscover({theme:c.dataset.theme})" in html
    assert "const themes=topicCatalog.map(t=>t.name).sort()" in html
    assert "paper-data" not in html
    assert "检索更新" not in html
    assert "/api/refresh" not in html
    assert '<h3>${esc(name)}</h3><p>${esc(p.theme)}</p>' not in html
    assert '<script src="lucide.min.js"></script>' in html
    assert 'data-lucide="compass"' in html
    assert "stroke-width':1.75" in html
    assert "<svg" not in html
    assert (tmp_path / "lucide.min.js").is_file()
    assert output.stat().st_size < 100_000

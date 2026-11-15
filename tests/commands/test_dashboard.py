from pathlib import Path
import re

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
    version = re.search(r'assets/([a-f0-9]{20})/app.js', html)[1]
    assert f'type="module" src="assets/{version}/app.js"' in html
    assert f'href="assets/{version}/styles/app.css"' in html
    assert f'href="assets/{version}/styles/welcome.css"' in html
    assert '<script defer src="lucide.min.js">' not in html
    assert 'id="welcome"' in html
    assert '进入工作区' in html
    assert '<h1>新的研究，<br><em>从下一篇</em><br><span>论文开始。</span></h1>' in html
    assert 'name="description"' in html
    assert 'property="og:image"' in html
    assert f'https://papercompass.cn/assets/{version}/share/paper-compass.png' in html
    assert 'id="paperDetail"' in html
    assert 'id="drawer"' not in html
    assert '<script>' not in html
    assert 'Complete Proceedings Paper' not in html
    assert '<option value="date">按时间：最新优先</option>' in html
    assets = tmp_path / 'assets'
    assert (assets / 'app.js').is_file()
    assert (assets / 'pages' / 'paper.js').is_file()
    assert (assets / 'router.js').is_file()
    assert (assets / 'styles' / 'app.css').is_file()
    assert (tmp_path / 'lucide.min.js').is_file()
    assert (assets / version / 'icon-data.js').is_file()
    assert (assets / version / 'share' / 'paper-compass.png').is_file()
    assert output.stat().st_size < 24000

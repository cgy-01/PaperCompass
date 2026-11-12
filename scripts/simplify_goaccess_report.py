#!/usr/bin/env python3
"""Turn a GoAccess HTML export into a small, annotated Chinese dashboard."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


MARKER = "var json_data="


def load_report(path: Path) -> dict:
    source = path.read_text(encoding="latin-1")
    start = source.index(MARKER) + len(MARKER)
    data, _ = json.JSONDecoder().raw_decode(source[start:])
    return data


def count(item: dict | None, field: str = "hits") -> int:
    return int(((item or {}).get(field) or {}).get("count", 0))


def find_request(rows: list[dict], path: str) -> dict:
    return next((row for row in rows if row.get("data") == path), {})


def request_prefix(rows: list[dict], prefix: str) -> tuple[int, int]:
    selected = [row for row in rows if str(row.get("data", "")).startswith(prefix)]
    return sum(count(row) for row in selected), max((count(row, "visitors") for row in selected), default=0)


def metric(value: str, label: str, note: str, tone: str = "") -> str:
    return f"""<article class="metric {tone}"><strong>{html.escape(value)}</strong>
    <h2>{html.escape(label)}</h2><p>{html.escape(note)}</p></article>"""


def make_report(data: dict) -> str:
    general = data["general"]
    requests = data["requests"]["data"]
    statuses = data["status_codes"]["data"]
    hours = data["visit_time"]["data"]

    home = find_request(requests, "/")
    bootstrap = find_request(requests, "/api/bootstrap")
    papers_hits, papers_visitors = request_prefix(requests, "/api/papers")
    success = next((row for row in statuses if str(row.get("data", "")).startswith("2xx")), {})
    client_errors = next((row for row in statuses if str(row.get("data", "")).startswith("4xx")), {})

    raw_visitors = int(general.get("unique_visitors", 0))
    app_loads = count(bootstrap)
    app_visitors = count(bootstrap, "visitors")
    home_views = count(home)
    homepage_visitors = count(home, "visitors")
    errors_4xx = count(client_errors)
    success_hits = count(success)
    total = int(general.get("valid_requests", 0)) or 1
    bandwidth_mb = int(general.get("bandwidth", 0)) / 1024 / 1024

    max_hour = max((count(row) for row in hours), default=1)
    hour_bars = "".join(
        f"""<div class="hour" title="{html.escape(str(row['data']))}:00 · {count(row)} 次请求">
        <i style="height:{max(3, count(row) / max_hour * 100):.1f}%"></i><span>{html.escape(str(row['data']))}</span></div>"""
        for row in hours
    )

    usage_rows = [
        ("打开网站首页", home_views, homepage_visitors, "包含部分机器人直接访问首页"),
        ("完整加载 Paper Compass", app_loads, app_visitors, "前端成功请求初始化数据，最接近真实打开应用"),
        ("加载或筛选论文列表", papers_hits, papers_visitors, "说明访问者进入了发现页或使用了筛选/分页"),
    ]
    usage_table = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{hits:,}</td><td>{visitors:,}</td><td>{html.escape(note)}</td></tr>"
        for name, hits, visitors, note in usage_rows
    )

    interpretation = (
        f"日志识别出 {raw_visitors:,} 个不同的 IP/浏览器组合，但其中包含自动扫描与未识别的机器人。"
        f"更保守、更适合对外描述的数据是：约 {app_visitors:,} 个访客标识完成了应用加载，"
        f"产生 {app_loads:,} 次完整加载；约 {papers_visitors:,} 个访客标识进一步访问了论文列表。"
    )

    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Paper Compass 流量简报</title>
<style>
:root{{--ink:#18322b;--muted:#687871;--line:#dce4df;--paper:#f6f4ed;--card:#fff;--green:#12664f;--orange:#ee8c49;--red:#a8483c}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1120px;margin:auto;padding:48px 28px 80px}}.eyebrow{{color:var(--green);font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}}
h1{{font:700 38px/1.2 Georgia,serif;margin:5px 0}}.sub{{margin:0;color:var(--muted)}}.callout{{margin:28px 0;padding:18px 20px;border-left:4px solid var(--green);background:#e8f1ec}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}}.metric{{background:var(--card);border:1px solid var(--line);padding:20px;border-radius:12px}}
.metric strong{{display:block;font-size:30px;line-height:1.1}}.metric h2{{font-size:14px;margin:8px 0 3px}}.metric p{{font-size:12px;color:var(--muted);margin:0}}.metric.warn strong{{color:var(--red)}}
section{{margin-top:34px}}section h2{{font:700 23px/1.3 Georgia,serif;margin:0 0 6px}}section>p{{color:var(--muted);margin:0 0 14px}}
table{{width:100%;border-collapse:collapse;background:white;border-top:1px solid var(--line)}}th,td{{padding:13px 14px;border-bottom:1px solid var(--line);text-align:left}}th{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}}td:nth-child(2),td:nth-child(3),th:nth-child(2),th:nth-child(3){{text-align:right;white-space:nowrap}}
.chart{{height:220px;display:flex;align-items:end;gap:8px;padding:20px 12px 0;background:white;border:1px solid var(--line)}}.hour{{height:100%;flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:7px}}.hour i{{width:70%;max-width:24px;background:var(--green);border-radius:3px 3px 0 0;min-height:3px}}.hour span{{font-size:10px;color:var(--muted)}}
.notes{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}.note{{padding:18px;background:white;border-top:3px solid var(--green)}}.note h3{{margin:0 0 5px;font-size:15px}}.note p{{margin:0;color:var(--muted);font-size:13px}}footer{{margin-top:38px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}}
@media(max-width:760px){{main{{padding:28px 15px 60px}}.metrics{{grid-template-columns:repeat(2,1fr)}}.notes{{grid-template-columns:1fr}}.chart{{gap:3px}}.hour span{{font-size:8px}}table{{font-size:12px}}th,td{{padding:10px 7px}}}}
</style></head><body><main>
<header><span class="eyebrow">Paper Compass · Traffic Brief</span><h1>网站访问简报</h1>
<p class="sub">统计时间：{html.escape(general.get('start_date',''))} · 报告生成：{html.escape(general.get('date_time',''))}</p></header>
<div class="callout"><strong>先看结论：</strong>{html.escape(interpretation)}</div>
<div class="metrics">
{metric(f'{app_visitors:,}', '大致有效访客', '完成前端初始化的访客标识；仍不是精确真人数')}
{metric(f'{app_loads:,}', '完整页面加载', '成功请求 /api/bootstrap 的次数')}
{metric(f'{papers_visitors:,}', '进一步浏览论文', f'共触发 {papers_hits:,} 次论文列表请求')}
{metric(f'{errors_4xx / total * 100:.0f}%', '扫描与无效请求', f'{errors_4xx:,} 次 4xx，大多不是正常用户', 'warn')}
</div>
<section><h2>访问者实际上做了什么</h2><p>“次数”是动作发生次数；“访客标识”按 IP 与浏览器估算，同一个人可能被重复计算。</p>
<table><thead><tr><th>行为</th><th>次数</th><th>访客标识</th><th>怎样理解</th></tr></thead><tbody>{usage_table}</tbody></table></section>
<section><h2>一天内的访问节奏</h2><p>横轴为服务器时间（UTC+8），柱高表示该小时的全部有效请求数，包含页面资源和接口请求。</p><div class="chart">{hour_bars}</div></section>
<section><h2>运维指标怎么理解</h2><div class="notes">
<div class="note"><h3>{success_hits:,} 次成功请求</h3><p>服务器成功返回页面、脚本或 API 数据。一个访客通常会产生多次请求，不能当作用户数。</p></div>
<div class="note"><h3>{bandwidth_mb:.1f} MB 流量</h3><p>当天 Nginx 返回的数据量。当前体量很小，服务器带宽和流量没有压力。</p></div>
<div class="note"><h3>{raw_visitors:,} 个原始访客标识</h3><p>这是 GoAccess 的原始去重结果，包含扫描器和伪装成浏览器的自动程序，不建议直接对外宣传。</p></div>
<div class="note"><h3>{errors_4xx:,} 次 4xx</h3><p>大量请求在探测 .env、.git/config 等不存在的文件，是公网服务器常见扫描，不代表网站故障。</p></div>
</div></section>
<footer>本报告由 Nginx 访问日志生成，不使用账户或 Cookie。数据只能用于趋势判断，不能代表精确自然人数。</footer>
</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="GoAccess HTML report")
    parser.add_argument("output", type=Path, help="Output simplified HTML")
    args = parser.parse_args()
    data = load_report(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(make_report(data), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()

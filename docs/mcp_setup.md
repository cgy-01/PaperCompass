# Paper Compass MCP 部署说明

> 文档受众：项目维护者和服务器管理员。本文档不会通过 Paper Compass 网站公开展示。

Paper Compass MCP 是现有只读 Web API 的轻量适配层。它不直接打开 SQLite，不抓取论文，也没有更新、删除或刷新工具。网页服务仍运行在 `127.0.0.1:8765`；MCP 默认调用该 API。

## 本地测试

先启动 Paper Compass 网页服务：

```bash
./start_dashboard.sh
```

另开一个终端，用 MCP Inspector 检查 stdio 服务：

```bash
npx @modelcontextprotocol/inspector \
  .venv/bin/paper-firehose mcp
```

可用工具：

- `search`：按关键词、主题、来源、类型和时间检索；
- `fetch`：按检索结果 ID 读取一篇论文的完整目录元数据与摘要；
- `recent_papers`：读取最近若干天的新论文；
- `list_topics`：列出合法主题名称和数量；
- `list_sources`：列出会议、期刊、预印本来源和数量。

当前关键词查询是数据库现有 Web API 的字面子串检索，不是向量语义检索。返回的是题录、作者、摘要、主题和原文 URL，不包含论文 PDF 全文。

`search` 和 `recent_papers` 是有界分页接口，单页最多返回 50 篇。响应会返回
`page`、`pages`、`has_more` 和 `next_page`。客户端不应默认遍历全部页面，而应
根据用户目标与当前结果决定是否继续；只有用户明确要求穷尽结果时，才保持筛选及
排序参数不变，持续请求 `next_page` 直到其为 `null`。

## 部署为远程 MCP

服务器上新增 `/etc/systemd/system/paper-compass-mcp.service`：

```ini
[Unit]
Description=Paper Compass Read-only MCP Service
After=network.target paper-compass.service
Requires=paper-compass.service

[Service]
Type=simple
User=papercompass
Group=papercompass
WorkingDirectory=/opt/paper-compass
EnvironmentFile=-/opt/paper-compass/.env
ExecStart=/opt/paper-compass/.venv/bin/paper-firehose mcp --transport streamable-http --host 127.0.0.1 --port 8766 --allowed-host papercompass.cn --allowed-host www.papercompass.cn
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

在现有 Nginx HTTPS `server` 块中加入：

```nginx
location /mcp {
    proxy_pass http://127.0.0.1:8766;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 300s;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

随后执行：

```bash
systemctl daemon-reload
systemctl enable --now paper-compass-mcp
nginx -t && systemctl reload nginx
```

远程地址为 `https://papercompass.cn/mcp`。MCP 是公开只读接口；上线后应在 Nginx 对 `/mcp` 增加合理限速，并继续确保 8766 只监听本机。

## 客户端接入

Codex：

```bash
codex mcp add paper-compass --url https://papercompass.cn/mcp
```

Claude Code：

```bash
claude mcp add --transport http paper-compass https://papercompass.cn/mcp
```

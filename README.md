# Paper Compass

面向 AI 与计算机研究者的论文工作台：跟踪 arXiv 新论文，收录会议官方目录，按主题浏览，并通过 MCP 为研究 Agent 提供文献检索。

[在线网站](https://papercompass.cn) · [MCP 用户指南](docs/MCP_USER_GUIDE.md) · [配置说明](docs/configuration.md) · [部署与自动更新](docs/admin/automatic_updates.md)

## 功能

- **产品首页**：研究方向导航、论文预览、阅读工作流和 MCP 连接介绍，通过主按钮进入研究工作区。
- **论文发现**：按时间、主题、会议／期刊和关键词筛选，分页浏览摘要和原文链接。
- **研究分类**：覆盖大模型、Agent、AI Infra、推理、多模态、安全、强化学习等方向，支持交叉主题。
- **arXiv 追踪**：RSS 发现、API 近期补漏、稳定 ID 去重、新版本更新及逐分类同步报告。
- **会议目录**：直接读取已接入的官方 proceedings，缺失作者和摘要可重试补全。
- **研究总览**：自动计算论文数量、主题覆盖、信息完整度、主题分布和年度趋势。
- **阅读状态**：未登录时在当前浏览器保存收藏和已读；v1.2 开发版新增 GitHub 登录、账号文献库同步和主动导入本机记录。
- **使用情况分析（v1.2 开发）**：独立行为库、按天统计与默认 90 天明细保留，用户可关闭当前浏览器的上报。
- **MCP**：提供 search、fetch、recent_papers、list_topics、list_sources，供 Agent 查询题录和摘要。

## 架构

```text
arXiv RSS / API ─┐
会议官方目录 ────┼─ 管理员采集任务 ─ 原始 SQLite 论文库
OpenAlex（可选）─┘                         │
                               一致快照、增量分类与校验
                                          │
                                  版本化 SQLite 查询库
                                          │
                                    只读 HTTP API
                                     ├─ Web 前端
                                     └─ MCP 服务 ─ 研究 Agent
```

采集与在线阅读分开运行。网站按页查询已发布数据库，不把全库交给浏览器，也不在启动时重新分类全部论文。候选版本校验成功后才切换，构建失败继续使用旧版。

前端使用 HTML、CSS、JavaScript 和 Lucide；后端使用 Python、Click、SQLite 与 MCP SDK。Node.js 用于准备图标资源，不是独立的前端服务。

## 从源码本地运行

推荐 Linux 或 WSL、Python 3.11 和 Node.js/npm。自动采集使用 Linux 文件锁，定时部署使用 systemd。当前以**从源码安装**为支持路径；内部包名和命令名称保持为 `paper_firehose` / `paper-firehose`。

```bash
git clone https://github.com/cgy-01/PaperCompass.git paper-compass
cd paper-compass
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
npm ci

# 独立保存运行数据；首次使用自动复制默认配置。
export PAPER_FIREHOSE_DATA_DIR="$PWD/.runtime-demo"
paper-firehose status

# 不采集网络数据也可以先启动空的工作台。
paper-firehose build-query
paper-firehose serve --host 127.0.0.1 --port 8765
```

打开 <http://127.0.0.1:8765>。重新打开终端后，需要重新激活环境，并设置同一个数据目录。

另开终端，在相同环境和数据目录下采集：

```bash
source .venv/bin/activate
export PAPER_FIREHOSE_DATA_DIR="$PWD/.runtime-demo"
paper-firehose update --scope arxiv
```

RSS 完成后先发布，API 补漏完成后再发布。网站自动检测新版本，刷新页面即可读取更新。首次补漏查询近期记录，耗时取决于分类数量、来源响应与机器资源。

本地启动不会自动安装定时任务。已有配置和查询库时也可运行 `./start_dashboard.sh`；该脚本现有环境查找行为见[开发说明](docs/development.md)。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `paper-firehose status` | 检查配置并初始化原始数据库 |
| `paper-firehose update --scope arxiv` | arXiv RSS 发现、API 补漏与发布 |
| `paper-firehose update --scope conferences` | 增量检查会议并补全元数据 |
| `paper-firehose sync-conferences --family all` | 手动完整核对已配置会议目录 |
| `paper-firehose build-query` | 从原始库生成并发布查询库，不抓取论文 |
| `paper-firehose serve` | 启动只读网站与 HTTP API |
| `paper-firehose mcp` | 启动 MCP 服务，默认调用本机 Web API |
| `paper-firehose --help` | 查看全部命令 |

全局配置参数放在子命令前，例如 `paper-firehose --config /path/config.yaml status`。其他排名、邮件和摘要处理命令仍保留，但不是当前 arXiv 自动更新的必经步骤。

## 自动更新

仓库提供两组 systemd 示例，需管理员显式安装：

| 任务 | 默认北京时间 | 行为 |
| --- | --- | --- |
| arXiv 主任务 | 每天 04:00 | RSS、API 补漏与发布，失败有限重试 |
| 会议次要任务 | 每天 06:30 | 近期届次每日检查，较早届次每 7 天检查 |

OpenAlex 默认跳过，需要时显式添加 `--openalex`。原始库、状态文件和查询库保存在运行目录，不随源码分发。详细安装和回滚见[管理员文档](docs/admin/automatic_updates.md)。

## 连接 MCP

公开服务地址为 `https://papercompass.cn/mcp`，连接方式与示例见[MCP 用户指南](docs/MCP_USER_GUIDE.md)。自托管见[MCP 部署说明](docs/mcp_setup.md)。MCP 查询已有论文，不触发采集和数据库写入。

## 文档与开发

- [文档索引](docs/README.md)
- [配置与环境变量](docs/configuration.md)
- [开发、测试与目录结构](docs/development.md)
- [部署与自动更新](docs/admin/automatic_updates.md)
- [贡献指南](CONTRIBUTING.md)
- [安全问题报告](SECURITY.md)
- [第三方素材说明](THIRD_PARTY_NOTICES.md)
- [源码发布检查](docs/admin/source_release.md)

源码和示例配置可以公开；实际配置、密钥、数据库、备份、日志、虚拟环境和同步状态不应提交。

## 许可证与联系

代码许可证见 [LICENSE](LICENSE)，保留其中已有版权和许可声明。第三方图标和高校标识单独说明，不因代码许可证而自动获得同一授权。

联系邮箱：<gavinchan041@gmail.com>。

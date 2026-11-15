# 配置与数据目录

Paper Compass 的配置分成默认模板、实际运行配置和部署参数。修改仓库默认模板不会自动覆盖已有运行配置。

## 配置入口

| 位置 | 用途 | 是否提交 |
| --- | --- | --- |
| `src/paper_firehose/system/config/` | 首次初始化复制的默认模板 | 是，仅示例数据 |
| `$PAPER_FIREHOSE_DATA_DIR/config/config.yaml` | 当前实例的来源与全局配置 | 否 |
| `$PAPER_FIREHOSE_DATA_DIR/config/topics/*.yaml` | 当前实例实际使用的主题规则 | 否 |
| `deploy/systemd/` | Linux 定时任务示例 | 是 |
| `.env.example` | 环境变量清单，不会自动加载 | 是 |

数据目录默认是 `~/.paper_firehose`。本地开发建议显式设置 `PAPER_FIREHOSE_DATA_DIR="$PWD/.runtime-demo"`。所有管理命令与服务必须使用同一个数据目录；仅设置 `--config` 不会自动把相对数据库路径改为配置文件所在目录。

第一次运行会从默认模板复制不存在的配置目录，已有目录不会自动覆盖。增加新主题时应将对应模板复制到实际配置目录，再检查 `paper-firehose status`。

根目录 `config.yaml`、`topics/`、`data/` 等本地副本不是标准模板入口，已从源码提交范围排除。`github_actions_config/` 是单独的手动 Actions 配置集合，不是生产服务器的实际配置。

## 全局配置

`database.path`、`database.history_path`、`database.all_feeds_path` 分别指定本轮处理库、原始历史库与旧 RSS 去重库。相对路径在运行数据目录下解析。不要把原始库配置到 `query-store/` 中。

`feeds` 将 feed key 映射为 `name`、`url` 和 `enabled`。主题文件引用这些 key。arXiv 专用采集从已启用的研究主题 feed 中识别 arXiv 分类，不读取所有非 arXiv RSS。

## 主题配置

```yaml
name: "AI Agent"
description: "规划、工具调用与多智能体协作"
feeds:
  - arxiv-cs-ai
filter:
  pattern: '\b(agentic|multi-agent|tool use)\b'
  min_matches: 1
  fields: [title, summary]
output:
  archive: true
```

这是字段示意，已有默认文件还包含排名等可选处理配置。`filter.pattern` 为 Python 正则；当前查询库分类采用“标题命中，或摘要命中次数达到 min_matches”。允许交叉分类，主题规则变化会使派生分类重新计算。新增主题不只需增加 YAML：还应更新 `dashboard.CURATED_TOPIC_KEYS`、图标映射和对应测试。

`ranking`、`paperqa`、`abstract_fetch` 等字段用于其他可选命令。自动 arXiv 采集不加载语义排名模型，也不调用 LLM 或下载全文。

## 环境变量

| 变量 | 作用 |
| --- | --- |
| `PAPER_FIREHOSE_DATA_DIR` | 所有运行数据和状态目录 |
| `PAPER_COMPASS_DOCS_DIR` | 可选，自托管的 Markdown 文档目录 |
| `PAPER_COMPASS_API_URL` | MCP 调用的 Web API 地址，默认本机服务 |
| `PAPER_COMPASS_HOST` | 本地启动脚本监听地址，默认 127.0.0.1 |
| `PAPER_COMPASS_PUBLIC_URL` | 账号服务的规范站点 Origin，生产必须为 HTTPS |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | GitHub OAuth App 凭据，仅供后端使用 |
| `PAPER_COMPASS_DEV_LOGIN` | 默认 0；本地开发账号开关，开发脚本默认开启。仅 HTTP loopback Origin、本机连接可用，使用独立开发用户库 |
| `PAPER_COMPASS_EVENTS_ENABLED` | 行为事件总开关，默认 1；0 关闭接收 |
| `PAPER_COMPASS_EVENTS_RETENTION_DAYS` | 行为明细保留天数，默认 90，范围 1～365 |
| `PAPER_COMPASS_EVENTS_DAILY_LIMIT` | 每日事件接收上限，默认 100000 |
| `OPENALEX_API_KEY` | 显式启用 OpenAlex 时使用 |
| `OPENAI_API_KEY` | 可选 LLM/摘要处理功能使用 |

`.env.example` 只是清单，应用不会自动读取任意 `.env`。使用 shell 导出环境变量或 systemd `EnvironmentFile` 注入；不要将真实密钥写入源码模板。

## 尚由代码或部署配置控制的参数

当前没有统一的调度 YAML，以下参数不能通过随意添加 YAML 字段修改：

- 执行时间、CPU/内存上限和重试：`deploy/systemd/`。
- arXiv 首次七天、重叠两天及分页上限：`commands/arxiv.py`。
- 官方会议名单与届次、历史检查间隔：`commands/official_sources.py`。
- 核心期刊展示白名单、研究主题清单：`commands/dashboard.py`。
- 默认是否调用 OpenAlex、任务阶段：`commands/update.py` 和 CLI 参数。

数据流、来源成功水位及回滚见[自动更新说明](admin/automatic_updates.md)。

账号库与分析库位于运行目录的 `users/`、`analytics/`，不进入论文查询快照。GitHub 回调、凭据配置、私有统计和备份见[账号部署说明](admin/accounts_analytics.md)。

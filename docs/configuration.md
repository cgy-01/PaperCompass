# 配置与数据目录

所有 29 项业务环境变量集中在项目根目录的一份 `.env`，包括网站、登录、行为记录、分类和可选外部服务。论文来源与主题定义仍使用既有 YAML，不修改数据库结构。修改仓库默认模板不会自动覆盖实际运行配置。

## 配置入口

| 位置 | 用途 | 是否提交 |
| --- | --- | --- |
| `src/paper_firehose/system/config/` | 首次初始化复制的默认模板 | 是，仅示例数据 |
| `$PAPER_FIREHOSE_DATA_DIR/config/config.yaml` | 当前实例的来源与全局配置 | 否 |
| `$PAPER_FIREHOSE_DATA_DIR/config/topics/*.yaml` | 当前实例实际使用的主题规则 | 否 |
| `deploy/systemd/` | Linux 定时任务示例 | 是 |
| `.env.example` | 29 项环境变量的分组模板 | 是 |
| `.env` | 当前实例唯一业务环境配置，应用与启动脚本自动读取 | 否 |

首次使用将 `.env.example` 复制为 `.env`，权限设为 600。模板默认使用 `.runtime-demo`；没有 `.env` 且未设置环境变量时仍默认 `~/.paper_firehose`。所有管理命令与服务必须使用同一个数据目录；仅设置 `--config` 不会自动把相对数据库路径改为配置文件所在目录。

CLI、Python 包、普通启动脚本和前端开发脚本使用同一个加载器。源码安装固定读取项目根目录 `.env`，不受终端当前目录影响；非源码安装只读取当前工作目录 `.env`，不向父目录搜索。修改配置后重启相关常驻进程；下次执行管理命令时自动重新读取。

优先级为：**显式命令行参数 > 已有进程环境变量 > `.env` > 代码默认值**。显式空环境变量也保留，可用于本地开发屏蔽正式凭据。相对的 `PAPER_FIREHOSE_DATA_DIR`、`PAPER_COMPASS_DOCS_DIR` 从 `.env` 所在目录解析；不要写 `$PWD` 或 `${HOME}`。

文件支持 `KEY=value`、单/双引号、空行和注释。密钥含空格或 `#` 时使用引号。值按字面读取，不执行 shell、不插入其他变量、不支持多行；拼错变量名、重复定义或引号未闭合会明确报错，错误不包含密钥值。这里只接受下面表格中的 29 项业务变量，CI/内部库选项仍由相应运行环境管理。

不再单独维护 `classification.env` 或 `accounts.env`。迁移已有部署时，将真实值合并到 `.env`，再移除旧的导出或 systemd 环境文件引用；否则旧进程环境仍会优先。不要在日志或聊天里输出整份 `.env`。

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

这是字段示意，已有默认文件还包含排名等可选处理配置。`filter.pattern` 为 Python 正则；默认规则分类采用标题/贡献句等证据加权，允许交叉分类，详见[规则分类说明](admin/topic_classification.md)。可显式启用[API 单主题分类](admin/api_classification.md)。主题目录配置变化仍可能使派生分类重新计算。新增主题不只需增加 YAML：还应更新 `dashboard.CURATED_TOPIC_KEYS`、图标映射、API 提示词/主题映射和对应测试。

`ranking`、`paperqa`、`abstract_fetch` 等字段用于其他可选命令。自动 arXiv 采集不加载语义排名模型或下载全文；显式启用 API 分类后，采集任务只排队，独立分类任务才调用模型。

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
| `MAILTO` | 可选 Crossref 等元数据请求联系邮箱 |
| `SMTP_PASSWORD` | 可选邮件发送密码，优先于旧 YAML/密码文件；旧配置继续兼容 |
| `PAPER_COMPASS_CLASSIFIER` | `keywords`（兼容默认）或 `api`，API 失败不会自动切回关键词 |
| `PAPER_COMPASS_CLASSIFICATION_API_KEY` | 分类专用密钥，模型任务使用，不复用其他 API Key |
| `PAPER_COMPASS_CLASSIFICATION_BASE_URL` | HTTPS Chat Completions 兼容端点，默认 `https://api.deepseek.com` |
| `PAPER_COMPASS_CLASSIFICATION_MODEL` | 分类模型，默认 `deepseek-flash` |
| `PAPER_COMPASS_CLASSIFICATION_REASONING_EFFORT` | 默认 `none`，关闭思考以节省分类成本；不支持此参数的兼容服务可用 `auto` 省略参数 |
| `PAPER_COMPASS_CLASSIFICATION_TIMEOUT` | 单请求读取超时，默认 60 秒 |
| `PAPER_COMPASS_CLASSIFICATION_MAX_TOKENS` | 输出 token 上限，默认 512 |
| `PAPER_COMPASS_CLASSIFICATION_DAILY_BUDGET_CNY` | 每个北京时间自然日估算金额上限，默认 3 元；0 禁止新请求 |
| `PAPER_COMPASS_CLASSIFICATION_DAILY_REQUEST_LIMIT` | 当日真实 API 尝试数上限，默认 -1 不限；0 禁止请求 |
| `PAPER_COMPASS_CLASSIFICATION_DAILY_TOKEN_LIMIT` | 当日用量/预留 Token 上限，默认 -1 不限；0 禁止请求 |
| `PAPER_COMPASS_CLASSIFICATION_PRICE_INPUT` | 输入估算单价，人民币/百万 Token，谷时默认 1 |
| `PAPER_COMPASS_CLASSIFICATION_PRICE_CACHED_INPUT` | 缓存命中输入估算单价，谷时默认 0.02 |
| `PAPER_COMPASS_CLASSIFICATION_PRICE_OUTPUT` | 输出估算单价，谷时默认 4；换模型/端点须重新核对价格 |
| `PAPER_COMPASS_CLASSIFICATION_LIMIT` | 每轮处理篇数，默认 0 处理完当前可执行的新论文；日金额保护仍生效，CLI 用 `--limit` |

分类 API 只在允许的北京时间谷时窗口发起请求，临近高峰提前暂停；手动 CLI 同样受限。暂停与日预算恢复规则见[API 分类维护说明](admin/api_classification.md)。

真实密钥只填入 `.env`，不填写 `.env.example`。`.env` 已被 Git 忽略，发布包与 Docker 构建上下文排除它。systemd 和 Docker Compose 也使用同一份 `.env`，不另建模块专用环境文件；服务器文件需归运行用户所有并保持 600 权限。现有进程环境注入方式继续兼容。

## 尚由代码或部署配置控制的参数

当前没有统一的调度 YAML，以下参数不能通过随意添加 YAML 字段修改：

- 执行时间、CPU/内存上限和重试：`deploy/systemd/`。
- arXiv 首次七天、重叠两天及分页上限：`commands/arxiv.py`。
- 官方会议名单与届次、历史检查间隔：`commands/official_sources.py`。
- 核心期刊展示白名单、研究主题清单：`commands/dashboard.py`。
- 默认是否调用 OpenAlex、任务阶段：`commands/update.py` 和 CLI 参数。

数据流、来源成功水位及回滚见[自动更新说明](admin/automatic_updates.md)。

账号库与分析库位于运行目录的 `users/`、`analytics/`，不进入论文查询快照。GitHub 回调、凭据配置、私有统计和备份见[账号部署说明](admin/accounts_analytics.md)。

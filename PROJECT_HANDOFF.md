# Paper Compass 项目交付文档

> 当前实现说明｜更新于 2026-09-29
> 运行形态：SQLite 数据库、Python 只读 API、按需加载的 Web 前端、只读 MCP

v1.2 开发新增独立账号与行为服务：原始论文库及论文 API 继续只读，用户写入位于 `users/users.db`，行为明细与每日汇总位于 `analytics/events.db`。GitHub 登录需要环境凭据；配置、备份和回滚见 [账号与分析部署说明](docs/admin/accounts_analytics.md)。
> 产品定位：面向计算机与 AI 研究者的论文雷达，优先覆盖顶会完整论文集，并用主题标签辅助筛选。

## 1. 产品与数据流

Paper Compass 不是每次切换页面就临时搜索几篇论文的搜索框，而是一套本地数据流水线：

```text
arXiv RSS ───────────────┐
会议官方 proceedings ───┼─> SQLite 历史库 ─> 元数据补全 ─> 服务端只读缓存 ─> JSON API
OpenAlex ────────────────┘                                            ├─ 前端分页读取
                                                                     └─ 聚合统计读取
```

当前采用两条互补路线：

- **会议路线：以会议官方论文集为准，先保证指定届次的论文目录完整，再做主题分类。**
- **主题路线：从 arXiv 和 OpenAlex 按关键词召回近期论文，只保留明确命中重点主题的结果。**

前端 HTML 不再内嵌全部论文。网站只读取独立的、版本化发布的 SQLite 查询库：启动加载预生成的统计，正文按页读取，不扫描原始库或执行全量分类。首页通过 `/api/bootstrap` 获取小型聚合数据，发现页通过 `/api/papers` 按 30 篇分页读取。公开服务不提供数据库更新接口。管理员执行 `paper-firehose update`，顺序更新 RSS、OpenAlex 和所有已配置会议来源，再从原始库只读快照构建候选查询库；按内容指纹只重新分类变化记录，校验后原子切换发布指针。旧请求继续使用旧版本，失败保留旧发布库。arXiv 主 timer 每天北京时间 04:00 启动一次，会议次要 timer 在 06:30 独立运行，共用写锁避免并发写入。首次部署须先执行 `build-query`，后续网站重启不再等待全库分类。安装、保留版本和回滚见 `docs/admin/automatic_updates.md`。默认运行目录为 `.runtime-demo/`，派生查询库位于其 `query-store/` 子目录，原始库结构不变。

MCP 与采集流水线解耦：独立 MCP 进程通过上述只读 HTTP API 检索现有论文，不直接加载 SQLite，也不包含抓取、同步、刷新、删除等写操作。Codex、Claude 等 Agent 可调用 `search`、`fetch`、`recent_papers`、`list_topics` 和 `list_sources` 获取题录与摘要。用户连接与使用方式见 `docs/MCP_USER_GUIDE.md`，服务器部署见 `docs/mcp_setup.md`。

“CVPR 2025”等会议入口代表官方论文集；“大模型”“Agent”等入口代表规则分类后的子集，两者数量不应相等。

## 2. 真实论文来源

### 2.1 顶会官方论文集：完整性主来源

下表直接读取会议或出版方的官方 accepted-paper/proceedings 页面，不依赖 OpenAlex 猜测论文属于哪届会议。

| 方向 | 已接入会议与年份 | 官方来源 | 获取内容 |
|---|---|---|---|
| 机器学习 | ICML 2023–2025 | PMLR Proceedings | 完整目录、详情链接，再补作者和摘要 |
| 机器学习 | NeurIPS 2023–2025 | NeurIPS Proceedings | 完整主会目录，再补作者和摘要 |
| 机器学习 | ICLR 2023–2026 | ICLR 官方 virtual/bulk JSON | Poster accepted-paper 目录、作者、摘要 |
| 视觉 | CVPR 2023–2026 | CVF Open Access | 完整目录，再从详情页补元数据 |
| 视觉 | ICCV 2023、2025 | CVF Open Access | 完整目录，再从详情页补元数据 |
| 自然语言处理 | ACL 2024–2026 | ACL Anthology | 主会 main/long/short 目录和详情页 |
| 自然语言处理 | EMNLP 2024–2025 | ACL Anthology | 主会目录和详情页 |
| 自然语言处理 | NAACL 2024–2025 | ACL Anthology | 主会目录和详情页 |
| AI 系统 | MLSys 2023–2026 | MLSys Proceedings | 完整目录和详情页 |
| 系统 | OSDI 2023–2025 | USENIX Technical Sessions | 正式技术论文和详情页 |
| 网络/系统 | NSDI 2023–2025 | USENIX Technical Sessions | 正式技术论文和详情页 |
| 系统 | USENIX ATC 2023–2025 | USENIX Technical Sessions | 正式技术论文和详情页 |

实现入口：`src/paper_firehose/commands/official_sources.py`。

会议同步遵循“**完整收录优先于主题命中**”。正式会议论文即使未命中重点 AI 主题，也会以内部主题 `ccf_a_all` 入库，并可从会议页浏览；以后更新分类规则时无需重新抓取整届会议。

这里的“完整”仅表示适配器读取表中指定届次的官方论文目录，且不会因关键词不匹配丢弃论文。它不表示已覆盖所有 CCF A 会议、所有 workshop/demo/findings，也不保证未正式发布的未来届次或每篇元数据始终可用。

### 2.2 arXiv：最新预印本主来源

arXiv RSS 用于发现最新工作。各主题分别监听 `cs.CL`、`cs.AI`、`cs.LG`、`cs.CV`、`cs.RO`、`cs.DC`、`cs.OS`、`cs.PF` 等相关分类。

- 更新快，通常包含标题、作者、摘要和原文链接；
- 属于预印本，不代表已被某个会议录用；
- 使用主题 feed 与关键词召回，不承诺某个领域的全量历史覆盖。

### 2.3 OpenAlex：期刊和出版元数据补充

OpenAlex 用于按主题补充近三年的出版记录、期刊名称、引用数和 DOI。当前刷新默认每个主题最多取 100 条候选。

OpenAlex **不是顶会 accepted-paper 完整性的权威来源**：其会议名称、届次、类型和摘要覆盖不统一。由 OpenAlex 推断为会议的记录会在前端标为“待核验来源”，不能与官方适配器混为一谈。

前端当前只展示精简的计算机核心期刊：JMLR、IEEE TPAMI、Artificial Intelligence（AIJ）、IEEE/ACM Transactions on Networking、ACM TOCS、ACM Transactions on Storage、ACM TACO。

这只是展示白名单，不是逐年完整目录。目前没有接入各期刊官网 TOC，因此不能对期刊完整性作出与顶会相同的承诺。

### 2.4 作者与摘要补充

- 会议官方详情页：会议入库时同步或随后补充作者和摘要；
- ICLR 官方批量 JSON：目录、作者、摘要一次性获取；
- Crossref：按 DOI 或标题为高相关论文补摘要；
- 代码保留 Semantic Scholar、OpenAlex、PubMed fallback，但它们不是当前顶会目录的主来源。

详情页补全可断点续跑：已有作者和摘要的记录会跳过；限流或页面异常导致的失败可在下次启动或同步时继续。正常抓取流程已经包含元数据补充，平时不需要人工“续跑”。

## 3. 入库、标识与去重

主历史库：`.runtime-demo/matched_entries_history.db`。

1. 官方论文以规范详情页 URL 生成稳定 ID，标识前缀为 `official:`。
2. OpenAlex 记录使用 OpenAlex work ID，标识前缀为 `openalex:`。
3. 官方论文集不使用纯标题跨会议去重，避免同名论文或不同届记录被错误合并。
4. 同一论文命中多个主题时共用论文身份，在前端表现为多个主题标签。
5. 元数据采用 upsert，补充作者、摘要、DOI、引用数等字段，而不是重复插入卡片。

## 4. 主题分类体系

| 配置键 | 页面名称 | 分类意图 |
|---|---|---|
| `llm` | 大模型（LLM） | 明确的大语言模型或主流大模型家族 |
| `agents` | AI Agent | 规划、记忆、工具调用、多智能体、计算机操作 |
| `ai_infra` | AI Infra | 训练/推理系统、分布式、并行、编译、加速和服务系统 |
| `reasoning_post_training` | 推理与后训练 | 推理、RLHF/RLAIF、对齐后训练、蒸馏 |
| `multimodal_generative` | 多模态与生成式 AI | 视觉语言、多模态理解与生成、扩散模型 |
| `ai_safety_alignment` | AI 安全与对齐 | 对齐、安全、鲁棒、越狱、红队、可解释性 |
| `data_evaluation` | 数据与评测 | 数据集构建、数据方法、benchmark 和评估方法学 |
| `embodied_robotics` | 具身智能与机器人 | 机器人学习、导航、操作、视觉语言动作 |
| `reinforcement_learning` | 强化学习 | RL 算法、策略优化、离线/在线及多智能体 RL |
| `ai_for_science` | AI for Science | 生物、化学、材料、物理、气候等科学智能 |
| `vision_graphics` | 视觉与图形 | 传统视觉、图像视频理解、三维视觉与图形学 |
| `ml_foundations` | 机器学习基础 | 学习理论、优化、表示学习、图学习与因果推断 |
| `language_speech_retrieval` | 语言、语音与检索 | 传统 NLP、计算语言学、语音音频、搜索与检索 |
| `systems_architecture` | 系统与计算架构 | 操作系统、分布式、网络、存储、数据库、编译与体系结构 |

前 10 类是产品重点追踪主题，后 4 类是基础方向。分类顺序保证重点主题优先成为主标签，基础方向主要承接原先未命中重点主题的正式会议论文。

规则源文件：`src/paper_firehose/system/config/topics/*.yaml`。

### 4.1 分类算法

仪表盘每次生成时都用**当前规则**重新检查标题、摘要和 summary，不直接沿用历史旧标签：

```text
标题命中规则，或摘要/summary 命中次数 >= min_matches
```

- 允许多标签，一篇论文可同时属于 LLM、Agent 和安全；
- 标题命中是强信号，一次即可分类；
- 摘要命中次数用于降低宽泛词误收；
- `summary` 与 `abstract` 相同则只计算一次；
- 未命中主题的官方会议论文仍进入会议视图；未命中的预印本和期刊不进入聚焦页面。

### 4.2 LLM 严格规则

LLM 不再用 `language model`、`transformer`、`foundation model`、`prompting`、`RAG` 等泛词单独归类。必须明确出现 large language model/LLM、large multimodal model/LMM、LVLM，或 GPT、Llama、Qwen、Mistral/Mixtral、Claude、Gemini、DeepSeek、PaLM、ChatGPT 等模型家族。

标题命中即可；仅在摘要命中时默认至少需要两次，目标是排除普通小模型和只顺带使用 Transformer 的论文。

### 4.3 数据与评测严格规则

不会因为出现 `data`、`dataset`、`benchmark` 或 `evaluation` 就自动收录。规则要求明确讨论：

- 数据集构建、发布、收集、整理、治理、质量、筛选、去重、蒸馏或压缩；
- 合成数据，训练数据选择、过滤、配比或归因；
- benchmark 构建、设计或套件；
- 评估框架、方法学、协议或指标设计；
- 明确 introduce/present/release/construct/create/curate/collect/build dataset 或 benchmark。

标题命中即可；只在摘要命中时默认至少需要两次。仅仅“在某数据集上做实验”不会因此入类。

### 4.4 分类限制

当前是可审计的正则多标签分类器，不是 LLM 判别器或训练分类模型。优点是离线、透明、规则更新后可全量重算；限制是同义表达可能漏召回，宽泛主题仍可能混入边缘论文。后续应按固定人工样本评估 precision/recall，而不是用“分类数量越多”衡量效果。

## 5. 前端筛选、排序与百分比

v1.1 开发版将前端实现拆到 `system/frontend/` 的页面、组件、路由、请求与样式模块，HTML 模板只保留外壳。新前端使用 `/api/v1/bootstrap`、`/api/v1/papers` 轻量列表和 `/api/v1/papers/<hash>` 完整详情；以下旧接口仍兼容 MCP 和既有客户端。页面采用 `#home` 等 hash 路由，论文地址使用稳定 SHA-256 标识，标题与卡片空白处均进入站内详情，再由用户选择打开原文。升级、版本分页与回滚见 [网站 API v1](docs/api_v1.md)。

前端支持按论文类型、具体会议/期刊、上述主题、时间窗口和关键词筛选，并在浏览器本地保存收藏和已读状态。会议卡片只显示会议名和数量，不给整届会议强行挂研究主题；点击会议或主题时会清除冲突的旧筛选条件。筛选条件会发送到只读 `/api/papers`，后端只返回当前页，不再把完整论文库交给浏览器。

默认排序是**发布日期降序，同一天再按相关度降序**。用户也可切换到相关度或标题排序。搜索由后端对标题、作者、摘要、来源和主题执行文本匹配。

卡片百分比是 `rank_score × 100`，不是录用概率、质量分或分类置信度：

- arXiv/RSS 主要由 `all-MiniLM-L6-v2` 计算主题查询与标题的语义相似度，并应用负面词惩罚和可选作者加权；
- OpenAlex/官方记录使用基于引用数的初始参考分，无引用信息的官方论文通常约为 45%；
- 不同来源算法不同，不适合严格横向比较。

所以产品默认最新优先，不让相关度分数压过发布时间。

## 6. 启动、部署与数据更新

项目根目录执行：

```bash
./start_dashboard.sh
```

浏览器打开 `http://127.0.0.1:8765`。指定端口可运行 `./start_dashboard.sh 9000`。

脚本优先使用专用 Miniconda 环境 `/home/cgy/miniconda3/envs/paper-compass`。服务是只读的，仅提供：

- `GET /api/bootstrap`：首页统计、主题、来源和最新论文；
- `GET /api/papers`：分页搜索、筛选、排序以及收藏 ID 查询；
- `GET /api/docs`：可公开查看的项目文档目录；
- `GET /api/docs/<slug>`：渲染后的单篇 Markdown 文档；
- 所有 `POST /api/*`：返回 `405 read-only service`。

服务器部署可设置 `PAPER_COMPASS_HOST=0.0.0.0`，再由 Nginx 或 Caddy 反向代理并提供 HTTPS。SQLite 和 `.runtime-demo/config/` 必须作为持久化数据保存。

管理员更新数据时依次执行：

1. arXiv/feed 抓取和关键词过滤；
2. 语义相关度计算；
3. 已配置会议官方论文集同步；
4. 会议作者和摘要补全；
5. OpenAlex 近三年主题记录补充；
6. 重启只读服务，使服务端缓存读取最新数据库。

仅在故障恢复时手动执行：

```bash
./enrich_conference_metadata.sh
```

单独同步官方会议：

```bash
PAPER_FIREHOSE_DATA_DIR=.runtime-demo \
/home/cgy/miniconda3/envs/paper-compass/bin/paper-firehose \
  --config .runtime-demo/config/config.yaml sync-conferences --family all
```

`family` 可选：`all`、`ml`、`nlp`、`cvf`、`infra`、`iclr`、`iclr2026`、`latest`。

## 7. 关键文件

| 文件/目录 | 作用 |
|---|---|
| `start_dashboard.sh` | 本地一键启动 |
| `src/paper_firehose/local_app.py` | 只读论文 API、分页查询和服务端缓存 |
| `src/paper_firehose/mcp_server.py` | 面向 Agent 的只读 MCP 适配层 |
| `docs/README.md` | 项目用户手册目录 |
| `docs/MCP_USER_GUIDE.md` | MCP 连接、工具说明、使用示例与排错 |
| `docs/mcp_setup.md` | MCP 本地测试、服务器部署和客户端接入 |
| `src/paper_firehose/commands/official_sources.py` | 官方会议适配器 |
| `src/paper_firehose/commands/conference_metadata.py` | 会议作者、摘要补全 |
| `src/paper_firehose/commands/openalex.py` | OpenAlex 补充数据 |
| `src/paper_firehose/commands/dashboard.py` | 生成轻量前端 HTML 壳 |
| `src/paper_firehose/system/config/topics/` | 主题和排序配置源文件 |
| `src/paper_firehose/system/templates/dashboard_template.html` | 前端模板 |
| `.runtime-demo/matched_entries_history.db` | 当前历史论文库 |
| `.runtime-demo/html/index.html` | 不含论文全集的轻量前端页面 |
| `.runtime-demo/config/` | 当前运行配置副本 |

代码侧 `src/paper_firehose/system/config/topics/` 是配置源，`.runtime-demo/config/topics/` 是运行副本。修改规则或新增主题后必须同步两者，避免页面继续使用旧配置。

## 8. 当前覆盖缺口与风险

尚未形成完整 CCF A 会议矩阵。下一阶段建议优先接入：

1. 通用 AI：AAAI、IJCAI；
2. 视觉：ECCV；
3. AI Infra/体系结构：SOSP、ASPLOS、EuroSys、SIGCOMM、SC、HPCA、PPoPP；
4. 数据与挖掘：KDD、SIGMOD、VLDB、ICDE、WWW；
5. 安全：IEEE S&P、USENIX Security、CCS、NDSS。

这些会议应继续各自接入官方 proceedings，不能扩大 OpenAlex 搜索数量来冒充全量覆盖。

其他风险：

- 官方站点 HTML 改版时解析器需要维护；
- 少数详情页会因限流、异常 HTML 或缺少 meta 标签而暂缺摘要；
- ACL Anthology 可能混入前言等非研究正文，需要持续清洗；
- 会议使用届次级日期，不一定是每篇论文首次公开日；
- 期刊目前只是主题召回和展示白名单，不是完整年度目录；
- 主题规则需要持续人工抽样校准。

## 9. 交付验收清单

每次更新数据源或主题规则后至少检查：

1. 抽一个会议，将官方目录数量与本地该届数量对照；
2. 会议筛选能否看到未命中任何主题的论文；
3. 随机抽查作者、摘要和原文链接；
4. LLM 分类是否混入仅讨论普通 language model/Transformer 的论文；
5. 数据与评测是否混入大量仅使用现有数据集做实验的论文；
6. 点击会议和主题后是否存在旧筛选冲突；
7. 默认是否为最新排序；
8. 重启后元数据补全能否自动恢复且不重复插入。

## 10. 后续建设建议

1. 补齐 AI Infra 和通用 AI 的 A 类会议官方源；
2. 为核心期刊接入官方 TOC，使期刊完整性成为可承诺能力；
3. 建立每主题固定标注集，记录 precision、漏召回案例和规则版本；
4. 在管理页展示每个来源的最后成功时间、数量变化和错误；
5. 元数据稳定后增加 LLM 自动精读和推文草稿，但保留原文引用、模型版本和人工审核状态。

总原则：**来源完整性由官方目录保证，主题精度由可审计规则保证，最新性由默认时间排序保证；LLM 负责后续理解与表达，不负责伪造来源事实。**

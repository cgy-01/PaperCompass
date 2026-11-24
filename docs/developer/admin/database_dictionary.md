# Paper Compass 数据库字段字典

“数据库”可以理解为一个文件，“表”像文件里的工作表，“字段 / 列”就是表头。下面按服务器实际结构逐项解释。

核验时间：2026-11-19T10:57:19.874940+08:00（北京时间）。只读取 sqlite_master、PRAGMA table_list 和 table_xinfo，没有读取用户记录、论文内容或密钥，没有修改表结构。

共 **7 类数据库、23 张业务表、140 个业务字段**；另有 6 张全文搜索虚拟表和 24 张搜索内部表。备份与上一版查询库沿用同样结构，不重复列一遍。原始结构快照见 [schema.json](../reports/2026-11-19-database-schema/schema.json)。本页仅供维护者使用，不进入网站用户文档白名单。

`TEXT` 是文本，`INTEGER` 是整数，`REAL` 是小数，`BLOB` 是程序内部二进制。SQLite 中不少 JSON 也用 TEXT 保存。PK 表示主键，联合主键的每个字段会带顺序；必填标记按实际 SQL 的 NOT NULL 声明列出，业务程序还可能有额外检查。


## 数据库与表目录

| 数据库 | 业务表 | 主要用途 |
| --- | --- | --- |
| `matched_entries_history.db` | `matched_entries` | 论文主库 |
| `query-store/query-*.db` | `fingerprints`, `metadata`, `paper_topics`, `papers`, `public_paper_ids` | 网站查询库 |
| `classification/api.db` | `assignments`, `jobs`, `predictions`, `requests`, `state`, `tracked_papers` | 分类任务与费用库 |
| `users/users.db` | `favorites`, `oauth_states`, `reading`, `sessions`, `users` | 账号与个人文献库 |
| `analytics/events.db` | `daily_counts`, `daily_unique`, `daily_visitors`, `events` | 行为统计库 |
| `all_feed_entries.db` | `feed_entries` | 旧采集去重登记库 |
| `papers.db` | `entries` | 旧批处理暂存库 |

## 1. 论文主库：`matched_entries_history.db`

真正的论文底本。采集器把论文信息长期保存到这里。

### `matched_entries`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `entry_id` | TEXT | 论文的站内原始唯一编号，关联收藏、分类任务和查询数据。 | PK 1 |
| `feed_name` | TEXT | 采集入口名称，例如某个 arXiv 分类或会议来源。 | 必填 |
| `topics` | TEXT | 原始库保存的主题字符串，可能为历史多个主题或内部待分类标记；当前 API 分类结果另存。 | 必填 |
| `title` | TEXT | 原始论文标题。 | 必填 |
| `link` | TEXT | 原文页面链接，通常是论文介绍页；不一定直接指向 PDF。 | 必填 |
| `summary` | TEXT | 采集入口给出的摘要或简介，部分来源与 abstract 保存相同内容。 | — |
| `authors` | TEXT | 作者信息，目前按文本保存。 | — |
| `abstract` | TEXT | 论文摘要原文；不等于模型生成的总结。 | — |
| `doi` | TEXT | 论文的 DOI 标识，缺少时可为空。 | — |
| `published_date` | TEXT | 来源提供的论文发表 / 公开日期，不等于本项目抓取日期。 | — |
| `matched_date` | TEXT | 入库或匹配时间；当前 arXiv 更新既有记录时保留，旧通用 RSS 合并主题曾可能重写。 | — |
| `llm_summary` | TEXT | 可选 LLM 摘要功能生成的总结；保留字段不代表每天都在生成。 | — |
| `paper_qa_summary` | TEXT | 可选 PaperQA 流程生成的总结，日常 API 分类不依赖它。 | — |
| `rank_score` | REAL | 历史排名 / 相关度分数，不是新 API 分类的 confidence，也不是论文质量分。 | — |
| `venue_name` | TEXT | 正式期刊或会议的显示名称。 | — |
| `venue_type` | TEXT | 出版渠道类型，例如会议、期刊、预印本。 | — |
| `external_id` | TEXT | 外部来源的论文标识或 URL，用于识别和去重。 | — |
| `cited_by_count` | INTEGER | 来源提供的被引次数，可能不完整或非实时。 | — |


## 2. 网站查询库：`query-store/query-*.db`

从主库和已接受的分类结果生成，供网页和 MCP 读取；保留当前版和上一版。

### `fingerprints`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `id` | TEXT | 论文的原始 ID。 | PK 1 |
| `fingerprint` | TEXT | 源记录内容指纹，可附分类决定指纹或待分类标记；用于判断哪些记录需要更新。 | 必填 |

### `metadata`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `key` | TEXT | 查询版本的配置项名称，例如 catalog、bootstrap、updated_at、classification_revision。 | PK 1 |
| `value` | TEXT | 对应配置值，多数为 JSON 文本；signature 等项保存字符串。 | 必填 |

### `paper_topics`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `paper_id` | TEXT | 对应查询库 papers.id。 | PK 1；必填 |
| `topic` | TEXT | 该论文关联的主题显示名称，便于按主题筛选和计数。 | PK 2；必填 |

### `papers`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `id` | TEXT | 对应论文主库的原始 entry_id，保证同一论文可关联。 | PK 1 |
| `payload` | TEXT | 完整的网页论文对象，以 JSON 文本保存；包含标题、作者、摘要、主题和链接等。 | 必填 |
| `search` | TEXT | 旧版保存字面查询文本；新版仅为结构兼容保留并清空，搜索不再使用。 | 必填 |
| `timestamp` | REAL | 从论文公开日期等展示日期转换出的排序用数值时间。 | 必填 |
| `score` | REAL | 从原始 rank_score 得到的历史排序分数，不是 API 分类信心。 | 必填 |
| `title` | TEXT | 规范化大小写后的标题，用于标题排序；原始展示标题在 payload 中。 | 必填 |
| `source` | TEXT | 供来源筛选的显示名称，例如 ICLR 2026。 | 必填 |
| `venue_type` | TEXT | 供类型筛选的渠道类型。 | 必填 |
| `theme` | TEXT | 当前主主题的显示名称；无主题时为空。 | 必填 |
| `complete` | INTEGER | 论文信息齐全标志，1/0；按作者、摘要、日期、来源、链接等是否有有效占位值判断，不是正确性评分。 | 必填 |
| `year` | INTEGER | 从论文展示日期提取的年份，用于统计。 | — |
| `base_date` | TEXT | 保留用于转换和日期处理的源日期，优先发表日期，否则匹配日期。 | 必填 |
| `source_rowid` | INTEGER | 来源主表的 SQLite 内部行号，用于稳定顺序等内部处理，不是论文公开编号。 | 必填 |

### `public_paper_ids`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `public_id` | TEXT | 对原始 ID 加固定命名空间后生成的 SHA-256 标识，用于前端论文详情 URL。 | PK 1 |
| `paper_id` | TEXT | 这个公开哈希标识对应的站内原始论文 ID。 | 必填 |

## 3. 分类任务与费用库：`classification/api.db`

保存历史基线、任务、模型结果、人工修订和实际请求账本。

### `assignments`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `paper_id` | TEXT | 原始论文 ID。 | PK 1 |
| `text_hash` | TEXT | 这份已接受分类所针对的标题摘要指纹。 | 必填 |
| `cache_key` | TEXT | 指向 predictions 中的已接受结果；只有文本匹配时才可发布。 | 必填 |

### `jobs`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `paper_id` | TEXT | 需要分类的原始论文 ID。 | PK 1 |
| `text_hash` | TEXT | 完整标题与摘要的指纹，用于发现文本变化。 | 必填 |
| `cache_key` | TEXT | 当前任务应使用的预测缓存 key，包含文本和分类策略。 | 必填 |
| `status` | TEXT | 任务状态，例如 pending、running、classified、needs_review、out_of_scope、failed。 | 必填 |
| `error` | TEXT | 最近错误或暂停原因，按可安全记录的文本保存。 | 必填 |
| `last_attempt` | TEXT | 最近一次领取处理的时间。 | 必填 |
| `attempts` | INTEGER | 任务尝试次数，不等于实际 HTTP 调用次数；预算和窗口暂停会还原本次次数。 | 必填 |
| `next_attempt` | TEXT | 最早可再执行的时间，不表示到时会自动启动新进程。 | 必填 |
| `lease_until` | TEXT | 当前 worker 的占用租约到期时间，用于异常退出后的恢复。 | 必填 |

### `predictions`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `cache_key` | TEXT | 预测或人工修订缓存的唯一 key。 | PK 1 |
| `result` | TEXT | 完整分类结果 JSON：决定、主题、理由、证据、模型版本与用量等。 | 必填 |
| `created_at` | TEXT | 这份缓存结果保存的时间。 | 必填 |

### `requests`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `id` | TEXT | 本项目生成的请求账本 UUID，不是提供商服务端请求 ID。 | PK 1 |
| `day` | TEXT | 登记请求时的北京时间日期，用于每日 3 元预算累计。 | 必填 |
| `started_at` | TEXT | 请求登记 / 预留开始时间。 | 必填 |
| `finished_at` | TEXT | 请求结算完成时间；未完成时可为空。 | — |
| `status` | TEXT | 调用结果：reserved、success、unknown、http_error、rejected、invalid_output；与 jobs.status 的含义不同。 | 必填 |
| `policy` | TEXT | 本次分类策略哈希，反映模型、端点和提示词等。 | 必填 |
| `model` | TEXT | 请求的模型别名，例如 deepseek-flash。 | 必填 |
| `reserved_micro` | INTEGER | 发起请求前预留的费用，单位为微元，1 元 = 1,000,000 微元。 | 必填 |
| `charged_micro` | INTEGER | 当前记账费用，仍是程序估算；用量未知时保留预留金额。 | 必填 |
| `reserved_tokens` | INTEGER | 请求前预留的 Token 数量。 | 必填 |
| `charged_tokens` | INTEGER | 当前记账 Token 数，收到可靠 usage 后按实际值结算。 | 必填 |
| `usage` | TEXT | 提供商返回的 Token 用量 JSON，包括输入、输出与输入缓存命中等。 | — |
| `prices` | TEXT | 请求登记时使用的单价 JSON 数组，顺序为输入、缓存输入、输出；单位元 / 百万 Token。 | 必填 |

### `state`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `key` | TEXT | 分类全局状态名称，例如 revision、circuit_until、new_only_scope。 | PK 1 |
| `value` | TEXT | 状态值：已接受修订号、熔断到期时间或历史基线 JSON 等。 | 必填 |

### `tracked_papers`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `paper_id` | TEXT | 原始论文 ID。 | PK 1 |
| `scope` | TEXT | historical 历史基线、new 基线后新入库、manual 显式放行的历史手动任务。 | 必填 |
| `discovered_at` | TEXT | 分类范围登记时间；历史条目是基线建立时间，不是论文发表时间。 | 必填 |


## 4. 账号与个人文献库：`users/users.db`

登录、用户资料、收藏和已读记录；此处只说明字段，不展示任何用户值。

### `favorites`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `user_id` | TEXT | 收藏该论文的站内用户 ID。 | PK 1；必填 |
| `paper_id` | TEXT | 收藏的原始论文 ID，不复制论文正文。 | PK 2；必填 |
| `created_at` | REAL | 收藏时间，Unix 秒。 | 必填 |

### `oauth_states`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `state_hash` | TEXT | GitHub 授权流程 state 的哈希，检查回调是否来自本次登录。 | PK 1 |
| `binding_hash` | TEXT | 绑定浏览器校验信息的哈希，避免授权流程被错误复用。 | 必填 |
| `verifier` | TEXT | 本次 OAuth/PKCE 流程的一次性校验信息，属于敏感临时值。 | 必填 |
| `return_to` | TEXT | 登录成功后回到的站内页面。 | 必填 |
| `expires_at` | REAL | 这次临时授权流程的到期时间，Unix 秒。 | 必填 |

### `reading`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `user_id` | TEXT | 标记已读的站内用户 ID。 | PK 1；必填 |
| `paper_id` | TEXT | 已读论文的原始 ID。 | PK 2；必填 |
| `created_at` | REAL | 标记已读的时间，Unix 秒。 | 必填 |

### `sessions`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `token_hash` | TEXT | 登录凭证的哈希，用于识别有效登录状态，不保存原始浏览器登录 token。 | PK 1 |
| `user_id` | TEXT | 对应 users.id。 | 必填 |
| `expires_at` | REAL | 登录状态到期时间，Unix 秒。 | 必填 |

### `users`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `id` | TEXT | 本产品的站内用户 UUID。 | PK 1 |
| `github_id` | INTEGER | GitHub 返回的账号数字 ID，用来识别同一 GitHub 用户。 | 必填 |
| `login` | TEXT | GitHub 用户名。 | 必填 |
| `name` | TEXT | 用户展示名称。 | 必填 |
| `avatar_url` | TEXT | GitHub 头像 URL。 | 必填 |
| `created_at` | REAL | 首次建立站内账号的时间，Unix 秒。 | 必填 |
| `last_login_at` | REAL | 最近一次成功登录的时间，Unix 秒。 | 必填 |


## 5. 行为统计库：`analytics/events.db`

记录已接入埋点的行为，并维护每天的计数。

### `daily_counts`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `day` | TEXT | 北京时间统计日期。 | PK 1；必填 |
| `type` | TEXT | 事件类型。 | PK 2；必填 |
| `page` | TEXT | 事件所在页面。 | PK 3；必填 |
| `count` | INTEGER | 该日期、类型和页面组合的累计事件数。 | 必填 |

### `daily_unique`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `day` | TEXT | 北京时间统计日期。 | PK 1；必填 |
| `kind` | TEXT | 去重对象类别：visitors 访客、users 登录用户、sessions 访问会话。 | PK 2；必填 |
| `identifier` | TEXT | 该类别的去重标识；同一天同类别同标识只记一次。 | PK 3；必填 |

### `daily_visitors`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `day` | TEXT | 北京时间统计日期。 | PK 1 |
| `visitors` | INTEGER | 当天去重访客数。 | 必填 |
| `users` | INTEGER | 当天去重登录用户数。 | 必填 |
| `sessions` | INTEGER | 当天去重访问会话数；不是登录次数。 | 必填 |

### `events`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `id` | TEXT | 行为事件唯一 ID，重复上报不会重复计数。 | PK 1 |
| `received_at` | REAL | 服务器接收到事件的时间，Unix 秒。 | 必填 |
| `day` | TEXT | 按服务器接收时间计算的北京时间日期。 | 必填 |
| `user_id` | TEXT | 站内用户 ID；未登录时可以为空。 | — |
| `visitor_id` | TEXT | 浏览器访客标识，用于区分访客，不是登录用户 ID。 | 必填 |
| `session_id` | TEXT | 浏览器访问会话标识，区别于 users.sessions 中的登录凭证。 | 必填 |
| `type` | TEXT | 事件类型，例如页面访问、搜索或论文打开等已接入行为。 | 必填 |
| `page` | TEXT | 事件发生的页面。 | 必填 |
| `paper_id` | TEXT | 相关论文 ID，没有关联论文时可为空字符串。 | 必填 |
| `target` | TEXT | 操作对象，例如筛选项或按钮标识。 | 必填 |
| `details` | TEXT | 该事件的附加信息 JSON。 | 必填 |


## 6. 旧采集去重登记库：`all_feed_entries.db`

兼容旧 RSS 流程，当前 arXiv 主流程另用论文 ID 和同步状态判断重复。

### `feed_entries`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `entry_id` | TEXT | 论文的站内原始唯一编号，关联收藏、分类任务和查询数据。 | PK 1 |
| `feed_name` | TEXT | 采集入口名称，例如某个 arXiv 分类或会议来源。 | 必填 |
| `title` | TEXT | 原始论文标题。 | 必填 |
| `link` | TEXT | 原文页面链接，通常是论文介绍页；不一定直接指向 PDF。 | 必填 |
| `summary` | TEXT | 采集入口给出的摘要或简介，部分来源与 abstract 保存相同内容。 | — |
| `authors` | TEXT | 作者信息，目前按文本保存。 | — |
| `published_date` | TEXT | 来源提供的论文发表 / 公开日期，不等于本项目抓取日期。 | — |
| `first_seen` | TEXT | 在旧抓取登记簿中第一次见到的时间。 | — |
| `last_seen` | TEXT | 最近一次抓取到的时间。 | — |


## 7. 旧批处理暂存库：`papers.db`

旧筛选、排名与总结任务的当轮工作区，当前网页不以它作为总论文库。

### `entries`

| 字段 | 类型 | 含义 | 约束 |
| --- | --- | --- | --- |
| `id` | TEXT | 旧批处理流程中论文的原始编号，与 topic 共同组成主键。 | PK 1；必填 |
| `topic` | TEXT | 这一行所属的旧任务主题，通常是配置中的英文主题 key。 | PK 2；必填 |
| `feed_name` | TEXT | 采集入口名称，例如某个 arXiv 分类或会议来源。 | 必填 |
| `title` | TEXT | 原始论文标题。 | 必填 |
| `link` | TEXT | 原文页面链接，通常是论文介绍页；不一定直接指向 PDF。 | 必填 |
| `summary` | TEXT | 采集入口给出的摘要或简介，部分来源与 abstract 保存相同内容。 | — |
| `authors` | TEXT | 作者信息，目前按文本保存。 | — |
| `abstract` | TEXT | 论文摘要原文；不等于模型生成的总结。 | — |
| `doi` | TEXT | 论文的 DOI 标识，缺少时可为空。 | — |
| `published_date` | TEXT | 来源提供的论文发表 / 公开日期，不等于本项目抓取日期。 | — |
| `discovered_date` | TEXT | 旧批处理任务发现或写入该记录的时间。 | — |
| `status` | TEXT | 旧流程状态：new 新建、filtered 已筛选、ranked 已排名、summarized 已总结。 | — |
| `rank_score` | REAL | 历史排名 / 相关度分数，不是新 API 分类的 confidence，也不是论文质量分。 | — |
| `rank_reasoning` | TEXT | 旧排名流程给出的排序依据 / 理由。 | — |
| `llm_summary` | TEXT | 可选 LLM 摘要功能生成的总结；保留字段不代表每天都在生成。 | — |
| `paper_qa_summary` | TEXT | 可选 PaperQA 流程生成的总结，日常 API 分类不依赖它。 | — |

## 8. JSON 文本里还有什么

这些是 JSON 对象内的属性，不是数据库额外的独立列。

| 所在字段 | 主要属性与含义 |
| --- | --- |
| 查询库 `papers.payload` | id 原始编号、title 原标题、url 原文地址、source 来源、venueType 类型、theme 主主题、topics 主题显示名列表、topicKeys 英文 key 列表、authors 作者、summary 摘要、insight 可选总结、doi、published 公开日期、matched 入库日期、score 旧排序分数、citations 被引次数 |
| 分类库 `predictions.result` | decision 分类 / 待审核 / 范围外；topic_key 和 topic_name 单主题；confidence high / medium / low；reason 理由；evidence 原文依据；needs_review 是否待审核；另有原文、版本、模型、策略哈希、用量和本地请求 ID，人工修订可能增加审核说明与时间 |
| 分类库 `requests.usage` | prompt_tokens 输入量、completion_tokens 输出量、total_tokens 总量、prompt_cache_hit_tokens / prompt_cache_miss_tokens 输入缓存命中 / 未命中，以及提供商返回时的 reasoning_tokens |
| 行为库 `events.details` | 不同行为所需的补充信息，只记录已接入并校验的项目，不代表保存了所有鼠标操作 |

API 单主题决定的 `confidence` 是模型自评，不等同于 query.score 或 rank_score，也不是论文质量评分。当前每篇新接受的 API 结果最多有一个主题，但旧论文保留原分类，payload 中仍使用主题数组以兼容历史。

## 9. 全文搜索表与 SQLite 内部表

下面的 `_fts` 为三字符索引入口，`_kw` 为分词检索入口；它们与后缀内部表由 SQLite 自动维护，是既有数据库内的搜索结构，不是额外收录了一批论文，也不是 PDF 文件。当前网站的查询库另有自己的 search 字段。

| 所在数据库 | 虚拟表 | 字段（含隐藏列） |
| --- | --- | --- |

| `matched_entries_history.db` | `matched_entries_fts` | `title`, `summary`, `abstract`, `authors`, `matched_entries_fts`（隐藏）, `rank`（隐藏） |
| `matched_entries_history.db` | `matched_entries_kw` | `title`, `summary`, `abstract`, `authors`, `matched_entries_kw`（隐藏）, `rank`（隐藏） |
| `all_feed_entries.db` | `feed_entries_fts` | `title`, `summary`, `authors`, `feed_entries_fts`（隐藏）, `rank`（隐藏） |
| `all_feed_entries.db` | `feed_entries_kw` | `title`, `summary`, `authors`, `feed_entries_kw`（隐藏）, `rank`（隐藏） |
| `papers.db` | `entries_fts` | `title`, `summary`, `abstract`, `authors`, `entries_fts`（隐藏）, `rank`（隐藏） |
| `papers.db` | `entries_kw` | `title`, `summary`, `abstract`, `authors`, `entries_kw`（隐藏）, `rank`（隐藏） |

虚拟表里的 title、summary、abstract、authors 对应主表的待检索文本；与表同名的隐藏列用于 MATCH 等控制语法；隐藏 rank 是 SQLite 搜索相关的运行时排序字段，不是 API confidence。

每个虚拟表都有下列内部表；表名前缀不同，但字段和用途相同：

| 后缀 | 表头 | 用途 |
| --- | --- | --- |
| `_config` | k、v | 搜索索引自身的配置 |
| `_data` | id、block | 压缩后的索引数据块，block 为二进制 |
| `_docsize` | id、sz | 文档长度等索引元信息，sz 为二进制 |
| `_idx` | segid、term、pgno | 将词项关联到索引段和页的位置 |

例如主库存在 matched_entries_fts_data 和 matched_entries_kw_data 等。全部实际表名、SQL 定义和精确字段类型均保存在 schema.json，不应人工修改这些索引内部表。

## 10. 常见混淆与关联

- 论文主库的 `entry_id`、查询库的 `papers.id`、收藏和分类里的 `paper_id` 指向同一原始论文身份；网页 URL 使用 public_id 哈希映射。跨数据库的论文关联由代码完成，不是跨文件 SQL 外键。
- `topics`（主库历史主题）、`topic`（旧任务主题）、`theme / paper_topics.topic`（查询库显示名称）与 API 的 `topic_key` 所在位置不同，不能只凭列名相近就当作同一份结果。
- published_date 是来源公开日期，matched_date 是本项目匹配 / 入库日期，discovered_at 是分类范围登记时间，created_at 还要看属于哪张表。
- 用户和行为库大多数 REAL 时间是 Unix 秒；论文日期与分类 TEXT 时间可能是纯日期、UTC ISO 时间或旧格式，解释时要看字段用途。每日预算和行为统计的 day 按北京时间生成。
- api.db 的 requests 记录真实调用；jobs 是论文任务，缓存命中、暂停与一次任务的多次重试都会使任务数与请求数不同。
- 分类 worker 还会创建连接内临时表 `worker_seen(paper_id)`，只记录本轮已尝试的 ID；它不是独立数据库，也不会永久保存，服务器结构查询不会看到其他进程的临时表。
- 数据库里有 summary、abstract、llm_summary 等不同文本字段，不表示已经保存论文 PDF 全文；当前 API 分类只使用标题和完整摘要 / summary 回退文本。

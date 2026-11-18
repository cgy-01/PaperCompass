# API 分类流水线：队列、费用与审核

本页仅供维护者使用，不加入网站公开文档白名单。

当前策略的完整定稿、14 主题边界、每日调度、谷时价格、3 元预算、暂停续跑、部署状态和验证证据见 [2026-11-19 分类策略归档](../reports/2026-11-19-api-classification-policy/README.md)。本页持续维护，归档保留当时快照。

已完成一次 10 篇真实论文抽样，逐次 Token、调用时间、费用公式及一万篇规模预期见[2026-11-18 实测归档](../reports/2026-11-18-deepseek-classification-benchmark/README.md)。该归档区分实测、线性外推、内部保守记账和提供商实际账单。

## 数据流与执行边界

```
采集任务 → 原始论文库 → 持久分类队列 → 立即发布可检索的论文
                              ↓
                       独立的单进程分类任务
                              ↓
                  校验 → 预测缓存 → 接受的分类结果
                              ↓
                      每轮至多一次批量发布
```

`update` 只抓取、排队和发布，不调用模型，不等待分类。缺少密钥、模型超时或分类额度用尽不阻止采集；排队失败时仍发布带内部待处理标记的候选，后续可重新对账。网页和 MCP 始终只读已发布查询快照，不调用模型。

原始论文库结构、论文 ID、收藏引用和公开 API 字段保持兼容。分类数据独立存储于 `$PAPER_FIREHOSE_DATA_DIR/classification/api.db`，包括 jobs、predictions、assignments、requests、tracked_papers 和状态表。升级只迁移这份旁路数据库。旧分类缓存中的 `done` 任务会迁移为明确状态；需要复核的旧结果不自动成为已接受标签。

默认策略仍为 `keywords`，兼容既有部署；测试通过后在唯一的根目录 `.env` 中设为 `api`。API 错误不自动切换到关键词。

## 精简模型协议

14 个主题的定义、边界和证据要求沿用已确认提示词：

```
src/paper_firehose/system/prompts/paper_topic_classification.txt
```

程序用精简协议替换该提示词的输出章节。每次请求仍传入完整标题和完整摘要，单篇超过 60,000 字符或缺摘要不调用模型。模型只返回：

```json
{"papers":[{"index":1,"decision":"classified","topic_key":"ai_infra","confidence":"high","reason":"主要贡献是面向大模型推理的系统优化。","evidence":"输入中的连续原文"}]}
```

不再让模型复述标题、摘要或主题中文名。本地校验单篇数量、序号、合法主题、决定与信心的组合及原文证据，再补回原始标题和摘要，导出的 JSON 仍保留它们。

`decision` 有三种值：

- `classified`：证据明确，赋予一个主题并可发布；不能为 low。
- `needs_review`：边界歧义或把握低，保存候选主题；不自动发布新标签。相同文本已有人工或已接受标签时保留旧标签。
- `out_of_scope`：明确超出 14 主题范围，topic_key 为 null；论文仍可检索，不硬塞进某一主题，也不新增前端“其他”标签。

除了 needs_review，证据不得为空，且必须为标题或摘要的连续片段。模型自报 high 不等于经过校准的概率；证据是原文也不代表分类一定正确。

## 根目录 .env

所有业务变量仍只放在这一份文件中，自动加载，不需 source。已填密钥不会被模板覆盖。

```ini
PAPER_COMPASS_CLASSIFIER=keywords
PAPER_COMPASS_CLASSIFICATION_API_KEY=填写实际密钥
PAPER_COMPASS_CLASSIFICATION_BASE_URL=https://api.deepseek.com
PAPER_COMPASS_CLASSIFICATION_MODEL=deepseek-flash
PAPER_COMPASS_CLASSIFICATION_REASONING_EFFORT=none
PAPER_COMPASS_CLASSIFICATION_TIMEOUT=60
PAPER_COMPASS_CLASSIFICATION_MAX_TOKENS=512
PAPER_COMPASS_CLASSIFICATION_LIMIT=0
PAPER_COMPASS_CLASSIFICATION_DAILY_BUDGET_CNY=3
PAPER_COMPASS_CLASSIFICATION_DAILY_REQUEST_LIMIT=-1
PAPER_COMPASS_CLASSIFICATION_DAILY_TOKEN_LIMIT=-1
PAPER_COMPASS_CLASSIFICATION_PRICE_INPUT=1
PAPER_COMPASS_CLASSIFICATION_PRICE_CACHED_INPUT=0.02
PAPER_COMPASS_CLASSIFICATION_PRICE_OUTPUT=4
```

三个价格的单位是人民币/百万 Token，采用 2026-11-19 核验的 Flash 空闲时段单价；代码逐请求阻止高峰调用，不需要实时切换价格。更换模型、端点或官方调价后必须重新核对。默认关闭思考；不支持 reasoning_effort 的兼容服务可以设 auto 省略字段，但不能保证关闭思考。

## 费用控制

每次真实请求前都在 SQLite 事务中登记请求并预留额度，同时检查当天请求数、Token 和金额三项上限；任意一项达到即停止。所有 JSON 测试和数据库任务共用这些限额。金额 0 表示禁止新请求；请求数/Token 限额设为 -1 表示不设该项数量上限，设为 0 则禁止新请求。默认取消数量限制，但仍保留每天 3 元的金额保护。自然日按北京时间计算。

预留使用输入序列化 UTF-8 字节数加模板余量，以及输出 Token 上限估算；收到有效 usage 后，按实际输入、缓存命中与输出 Token 及记录的单价结算估算值。无效 JSON 也会记录费用；超时、进程退出或缺少用量时保留预留值，不视为免费。用量明显超过预留时熔断后续请求。

这是按配置价格计算的保守额度控制，不是提供商账单保证。错误价格、服务端用量差异，以及使用同一密钥的其他程序都可能使实际账单不同。HTTP 返回后、缓存落盘前进程退出，重跑仍可能再次请求，不能承诺绝对不重复收费。

`requests` 是按真实调用记账；缓存读取不新增请求记录。不能把每篇导出结果中的历史 usage 相加当作本次花费。`classification-status` 返回当天请求数、估算费用、包含未知用量预留的 Token 数及未知用量请求数。

本地成功预测按完整文本和策略缓存，重复来源共享预测。改变模型、提示词或思考设置会改变缓存 key，但不会自动将未变化的整份历史库重新排队。人工确认的同版本文本结果不会被常规排队或 --all-papers 覆盖。

## 测试 50 篇 JSON

```bash
paper-firehose classify-papers \
  --input tmp/paper-samples/papers_50_title_abstract_20261118.json \
  --output tmp/paper-samples/papers_50_classified_v2.json \
  --limit 50
```

这条命令显式调用 API，无需把在线策略切到 api；只读输入、保存缓存和新的输出文件，不修改原始库或查询库指针。输入文件最多 16 MiB，已有输出文件不覆盖。输出包含原文、主题/决定、证据、版本、请求 ID 和用量；汇总分别统计 classified、needs_review、out_of_scope、cached、failed。有失败项返回非零退出码，但已成功结果保存，可换输出路径复用缓存。

## 采集与独立分类

启用 API 模式前先保留一份正常查询库，用于保留历史展示标签。首次启用时建立永久的论文身份基线，而不是根据发表日期、最新查询快照或本轮抓取窗口猜测哪些是新论文。建议先显式初始化（无模型调用，不修改原始库）：

```bash
paper-firehose --config .runtime-demo/config/config.yaml classification-baseline
```

`update` 也会在任何抓取写入前自动执行这一步；已有基线不会被再次覆盖。同一个旁路分类库不能绑定另一份原始库，切换数据集应使用独立运行目录。初始化记录建立时的全部论文 ID、时间、原始库路径和数量。已有未完成历史分类队列被标记为 historical，不再自动付费处理；预测与已接受结果保留。采集照常执行：

```bash
paper-firehose --config .runtime-demo/config/config.yaml update --scope arxiv
```

只有基线建立后首次出现的论文身份进入自动队列。历史论文即使后来补摘要、改标题或作者，也不排队；发布时只更新元数据，沿用上次已发布的主题标签和可见性。新论文缺少完整摘要时等待补全，不发模型请求；补全后自动恢复。

新论文后续文本变化可重新分类；仅修改模型、提示词或思考设置不会重分已完成论文。API 模式下 arXiv 候选不再用关键词预过滤；来源和抓取窗口仍受已有配置限制。OpenAlex 仍受其上游检索词限制，不代表全量召回。

分类任务处理持久队列，每次仅从原始库读取一篇，HTTP 期间不保持原始库读事务，不扫描整库，也不抓取来源：

```bash
paper-firehose --config .runtime-demo/config/config.yaml \
  classify-papers --queued-only --publish
```

--publish 要求 api 模式。本轮结束时，只有接受的分类修订号变化才构建一次查询快照；没有变化不复制数据库。发布失败不会丢失分类结果，下一轮即使没有模型任务也能重试发布。发布读取固定分类快照，并检查文本哈希，避免将旧文本结论贴到新版论文。

默认 `--limit 0` / `PAPER_COMPASS_CLASSIFICATION_LIMIT=0`，取消原先每轮 200 篇的限制。worker 每次取最多 100 条轻量队列记录、逐篇读取摘要并串行处理，持续到当前可执行的新论文处理完毕；不会把所有摘要一次性载入内存。同一论文每轮最多尝试一次，即使请求期间原文再次变化，也留到下轮处理。需要小批量调试时仍可显式传 `--limit 50`。

**不限篇数不等于取消费用保护。** 默认每个北京时间自然日 3 元，按谷时单价与用量记账。每个请求先预留输入估算与输出上限所需的费用，余额不足以预留下一篇就暂停，实际用完金额可能略低于 3 元。多次运行、手动 JSON 测试与自动 worker 共用预算，未用额度不累计到下一天。近期容量统计见[服务器近期新增量](../reports/2026-11-19-server-paper-growth/README.md)；该统计报告记录的是调整前配置。

## 只在谷时调用模型

截至 2026-11-19 的[官方价格规则](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)：北京时间周一至周五（不含中国法定节假日）09:00–12:00、14:00–18:00 为高峰，其余时段及周末全天为空闲时段。项目不引入节假日日历依赖，保守地对所有周一至周五使用这两个禁用时段；即使碰到法定假日也不会擅自扩大可调用窗口。

约束放在付费请求前的 SQLite 额度预留逻辑里，而不只依赖定时器。`Persistent=true` 的服务补跑、手动 CLI、JSON 样本分类和重试均不能绕过。时区固定为 Asia/Shanghai，不依赖服务器系统时区。

临近高峰时，会提前停止发起新请求：预留 `读取超时 + 15 秒` 的缓冲，默认约 75 秒。已有请求正常完成，不强行取消，以免提供商已计费而本地丢失结果；超时和未知用量仍保留保守费用预留。

谷时结束或接近结束，worker 保存 `deferred_reason=off_peak_window` 与 `resume_at`，任务恢复为 pending，次数不计作失败重试。达到日预算同样保留队列，次日额度重置。已完成的分类可照常批量发布，论文检索与采集继续运行。本地缓存命中不发送请求、不收费，可在高峰复用。

`resume_at` 是最早允许再次尝试的时间，并不额外创建定时任务。默认仍每天 18:10 执行一次；剩余任务由下一次定时运行继续处理，也可在安全谷时手动运行同一命令。断点恢复不重做已经成功并缓存的同版本文本。

省略 --queued-only 的命令会先扫描原始库，对账未来新身份与它们的文本变化；扫描历史 ID 不会向模型提交历史论文。只有明确传入 --all-papers 才允许历史分类，它与默认自动队列分开：未完成的历史手动任务必须继续用 --all-papers，不会由每天的自动 worker 偷偷续跑。初次效果评估优先用 JSON 或隔离数据库副本。

## 任务状态与审核

| 状态 | 行为 |
| --- | --- |
| pending | 待执行；额度不足时延至下个北京时间自然日，谷时暂停时延至下个允许窗口 |
| running | 单 worker 执行，有超时租约，异常退出后可恢复 |
| retry_wait | 网络、限流、格式错误等，第 1、2 次失败后退避 5、10 分钟；第 3 次失败进入 failed |
| waiting_metadata | 缺标题/摘要；等待源文本变化，不消耗 API 重试额度 |
| invalid_input | 输入过大等；等待文本修正或手动恢复 |
| needs_review | 保存模型建议，等待维护者确认，不重复付费请求 |
| classified / out_of_scope | 处理完成，可发布相应主题或无主题结果 |
| failed | 最多 3 次处理尝试后停止，需手动恢复 |
| historical | 启用新身份基线前的遗留任务；自动 worker 不处理 |

每次处理只发一次 HTTP，不在客户端隐藏重试。鉴权、计费或其他不可重试 HTTP 错误会停止本轮并熔断一小时；连续三次暂时错误熔断十五分钟。熔断时间持久化，下一进程也会遵守。新文本在模型请求期间到达时，旧响应只能进入文本缓存，不能覆盖新任务。

```bash
# 查看状态、当天用量与前 20 个待审核建议（使用哈希论文 ID）
paper-firehose classification-status

# 修复凭据后显式清除熔断；随后可恢复失败/延后的任务，日预算仍然生效
paper-firehose classification-status --reset-circuit
paper-firehose --config .runtime-demo/config/config.yaml classify-papers --queued-only --retry-failed --publish

# 人工确认或纠正主题；若模型没有证据，须补 --evidence 指定原文片段
paper-firehose --config .runtime-demo/config/config.yaml review-classification \
  --paper '<哈希论文ID>' --topic ai_infra --note '人工核对确认主要贡献为推理系统。'

# 人工结果随后批量发布；没有新的模型任务时不会产生 API 请求
paper-firehose --config .runtime-demo/config/config.yaml classify-papers --queued-only --publish
```

也可用 --out-of-scope 替代 --topic。人工确认保留模型原始信心、审核说明和时间；原始预测不修改，修订结果单独保存。原文变化后旧人工分类也需要重新评估。

## 2 核 / 2 GiB 部署

不引入 Redis、向量模型或额外队列服务。分类串行执行，不限总篇数，每批最多读取 100 个队列记录，不把全部摘要加载到内存。旁路 SQLite 页缓存约 4 MiB，发布读取缓存约 2 MiB；这些不是整个 Python 进程的内存占用保证。

现有采集时间不变：arXiv 14:00、会议 06:30。新增分类定时器默认每天北京时间 18:10，位于当前官方空闲时段；请求前仍逐次检查窗口。若当时采集任务仍在运行，分类服务跳过本次；可待采集结束后手动启动。队列保留至下一次，不会丢失。价格优先意味着主题可能晚于可检索论文出现。

模板服务限制 CPUQuota=50%、MemoryHigh=256M、MemoryMax=384M、6 小时超时，不自动重启重试收费任务。它们是资源上限与调度选择，实际 RSS 和云盘性能仍需部署后测量。Web 继续服务旧快照；数据库构建仍采用完整源快照和本地指纹扫描保障一致性，此轮没有将发布改为不安全的文件时间戳判断。

管理员手动安装新增服务，当前源码修改不会自行更新服务器：

```bash
cp deploy/systemd/paper-compass-classify.service /etc/systemd/system/
cp deploy/systemd/paper-compass-classify.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now paper-compass-classify.timer
```

所有服务共用 `/opt/paper-compass/.env`；归运行用户所有、权限 600，不进入源码或静态目录。设置 classifier=api 后 worker 才会执行。状态写入 classification/status.json，采集状态仍在原 update-status 文件。

## 回滚与兼容

关键词逻辑保留。设置 classifier=keywords 并停止分类 timer 后执行 build-query，可将已有 API 标签及 API 待处理行按旧规则重新计算。精确恢复公开数据可以恢复旧 current.json 与其仍存在的查询快照。原始库、用户库、采集水位均不迁移、不删除。历史基线属于分类旁路库，不要删除或重建它来提高额度；保留它才能维持“只处理未来新身份”的边界。

参考：[DeepSeek 价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[上下文缓存](https://api-docs.deepseek.com/guides/kv_cache/)。

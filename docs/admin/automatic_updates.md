# 自动采集与版本化查询库

此文档供管理员使用，不加入公开用户文档目录。

arXiv 是首要更新源：主定时器每天北京时间 04:00 执行一次 `update --scope arxiv`。会议为独立的次要任务，在 06:30 执行 `update --scope conferences`。OpenAlex 默认跳过，只有显式添加 `--openalex` 才检索近 30 天期刊记录（翻页需要 API key）。不执行语义模型排名、PDF 下载或 LLM 总结。

## arXiv 每日监测与补漏

主任务先读取重点研究主题配置所用的 arXiv 分类 RSS，完成后立刻发布查询库；再使用 arXiv API 按 `lastUpdatedDate` 降序补漏并最终发布，不等待会议。API 按更新时间回看，既可补新提交，也可补旧论文的新版本。所有 arXiv 请求单连接、至少间隔三秒，带超时和有限重试。

论文使用去掉版本号的 arXiv ID 去重，标题变化和跨分类不会创建重复记录。已有论文保留原来的 SHA-1 entry_id，收藏引用不改变；新记录使用 `arxiv:编号`。新版本更新标题、作者、摘要，保留原发表日期、排名及其他已存在信息；明确较旧的版本不会覆盖新版本。本次不会删除已经存在的历史重复记录。手动旧 `filter` 命令仍保留原行为，不应与新 arXiv 任务混用来重复采集同一数据集。

`arxiv-sync-state.json` 保存各分类 RSS/API 成功时间、RSS 内容指纹和已见版本。首次 API 补最近七天；以后从上次成功时间前两天开始回看，处理至本次开始时间。每页 200 条，最多检查 10,000 条更新记录；若未到达时间边界就触及上限，会报失败并保留旧水位，不把不完整扫描记成成功。长时间停机超过这个恢复能力时，需要人工分段补抓。

状态报告逐分类列出 RSS/API 的成功、失败、返回数量、新增、更新和未变化数量。有效空 feed 记录为成功且 `empty=true`；HTML 错误页、异常 XML、API 缺分页信息、超时和提前空页都会记录为失败。RSS 的 `unchanged_feed` 和来源更新时间有助于区分来源尚未更新与请求失败。RSS 成功不推进 API 水位，API 失败不影响已经发布的 RSS 数据。

原始库只插入或更新相关论文，不改变表结构，也不清空旧论文。API 的首次范围是短期补漏，不承诺从旧库最后一篇论文日期追溯全部缺失历史。

## 日常增量检查策略

`update` 自动使用会议增量模式，不按论文的“昨天发表”做截断：官网今天新增的历史日期论文也能收录。当前年及上一年届次每天检查一次，更早届次在上次成功检查满 7 个北京时间日历日后再检查。来源失败会保留原成功时间，下次运行继续尝试。

官网 HTML 目录支持 ETag 或 Last-Modified 时发送条件请求，HTTP 304 直接跳过目录处理；其他情况重新读取目录，但通过条目指纹仅处理新增或变化论文。ICLR 的目录与摘要来自两个批量 JSON，目前会读取这两个文件后比较条目，不使用单文件 304，以免漏掉另一文件中的摘要更新。缺失作者和摘要的补全不受目录检查间隔限制。

同步状态按来源保存在运行目录 `conference-sync/*.json`，包含上次成功时间、HTTP 校验信息、条目指纹和分类规则标识。数据库提交成功后才写入状态；异常和空目录不标记成功。已有原始记录不会因为官网目录移除某个条目而自动删除。

首次升级没有这些状态文件，需要完整核对一次已配置来源；以后按增量策略执行。主题规则或来源配置变化会使对应缓存失效。旧命令 `sync-conferences --family all` 默认仍为强制完整目录同步，不受增量间隔限制。恢复原始库备份或切换到不同数据集后，应将旧 `conference-sync` 目录移至备份位置，让下次更新重新核对，避免把旧数据集的同步状态当作新数据集的进度。

RSS 只读取 feed 当前提供的近期列表，再做已有记录去重，不下载全年论文。保留其原有日期保护窗口，不缩成严格一天，避免任务停机或延迟收录时漏抓；feed 已不再提供的历史记录仍需专门补抓。

## 数据职责与每日更新

- 原始库仍为现有三个 SQLite 数据库，保留原有结构。采集流程向原始库插入、去重或补全；备份不完整时停止采集。
- 网站和 MCP 的 HTTP 后端只读取独立的已发布查询库，不直接读取正在采集的原始库。
- 每日采集结束后，用 SQLite backup API 从只读连接取得一致快照。在后台复制上一版查询库为候选版本，通过原始记录内容指纹识别新增、修改和删除。
- 仅新增或修改的记录重新分类、更新搜索文本和主题关系；未变化记录不重新分类。分类规则或查询格式版本变化时全量重新分类。
- 每次仍需复制查询库文件、扫描原始记录指纹、重新汇总统计并校验。这不是重新抓取、重写全部原始论文。
- 官方会议查重先使用既有主键索引，无变化记录跳过写入；不会为每篇已有论文扫描整张原始表。主题规则每轮预加载一次。每 200 篇提交并记录进度，异常时此前已提交的批次保留，后续可重跑去重补齐。
- 校验数据库完整性、论文身份及主题关联后发布。如果可展示论文数比上一版骤降超过 20%，拒绝发布，需管理员检查数据和规则。
- 查询库位于运行目录的 `query-store/query-*.db`，`current.json` 是当前发布指针。原子替换指针，候选失败保留旧版本。
- 网站每 15 秒检测发布指针，切换到已生成的查询库。每个查询在同一个不可变版本上完成计数和取页；已打开旧版连接的请求可以继续读完。
- 保留至少最近三个查询库版本；成功发布后清理更旧且超过 48 小时的版本。旧连接在 Linux 下仍可继续读取已打开文件；长时间闲置的读取对象若遇到版本已清理，会在查询开始前打开当前版本。
- 04:00 是采集开始时间。用户继续读取旧版数据，直到新版本完成校验和发布；不重启网站、不清空公开数据。共享 CPU 和磁盘可能导致延迟波动，服务配置降低后台优先级并限制最多使用一个 CPU 核心的算力。

RSS 会清空本轮 `papers.db` 再填充，历史库不会因此清空。RSS 长时间断更后的缺失记录受来源保留窗口限制。旧手动抓取命令不自动发布，执行后需运行 `build-query`。

## 首次上线与升级

在服务器上执行，假设项目位于 `/opt/paper-compass`，账户为 `papercompass`，使用现有虚拟环境。首次构建不访问网络、不采集论文、不修改原始库。

必须先构建查询库，再重启新版网站。首次全量分类可能约需一至数分钟；此时旧网站可继续提供服务。

```bash
cd /opt/paper-compass
sudo -u papercompass env PAPER_FIREHOSE_DATA_DIR=/opt/paper-compass/.runtime-demo \
  /opt/paper-compass/.venv/bin/paper-firehose \
  --config /opt/paper-compass/.runtime-demo/config/config.yaml build-query

cp deploy/systemd/paper-compass-update.service deploy/systemd/paper-compass-conferences.service /etc/systemd/system/
cp deploy/systemd/paper-compass-update.timer deploy/systemd/paper-compass-conferences.timer /etc/systemd/system/
systemctl daemon-reload
systemctl restart paper-compass
systemctl enable --now paper-compass-update.timer
systemctl list-timers paper-compass-update.timer
```

先确认 arXiv 主任务可以完成，再启用次要会议定时器：

```bash
systemctl enable --now paper-compass-conferences.timer
systemctl list-timers --all 'paper-compass-*.timer'
```

如果未生成查询库，Web 服务会报告需要执行 `build-query`，不会在用户请求中临时扫描全库。

`Persistent=true` 会补执行错过的任务，安装 timer 时可能立即采集。arXiv 服务限时两小时，失败后间隔五分钟重试，一小时最多启动三次；会议服务限时 45 分钟。两者内存硬上限 900 MiB，CPU 配额为 100%（相当于一个核心）。共用写锁，不能同时写原始库；错峰调度用于让会议不占用主任务时段，手动重叠运行仍可能触发锁占用失败，不能保证在任意人为调度下无等待。

构建期间需同时容纳原始库快照、候选查询库及保留版本，部署前检查磁盘剩余空间。当前实测原始库约 1.2 GiB、每份查询库约 329 MiB；建议另留至少 4 GiB 空间供快照、候选版本及保留版本使用，原始库备份还需额外空间。不要把原始库放到 `query-store` 目录。

## 本地执行

```bash
cd ~/projects/paper-compass
export PAPER_FIREHOSE_DATA_DIR="$PWD/.runtime-demo"

/home/cgy/miniconda3/envs/paper-compass/bin/paper-firehose \
  --config "$PAPER_FIREHOSE_DATA_DIR/config/config.yaml" build-query

./start_dashboard.sh
```

手动采集并自动发布：

```bash
/home/cgy/miniconda3/envs/paper-compass/bin/paper-firehose \
  --config "$PAPER_FIREHOSE_DATA_DIR/config/config.yaml" update --scope arxiv
```

`update.lock` 防止重复自动采集，`query-store/build.lock` 防止重复构建。手动旧抓取命令不受更新锁保护，应避免和自动采集同时写入原始库。采集进行中不要手动运行 `build-query`，以免发布尚未完成补全的记录。

`update --scope conferences` 仅同步会议；不指定 scope 的 `update` 仍顺序执行全部阶段，保留手动管理入口。`update-status-arxiv.json` 与 `update-status-conferences.json` 分别保存两类任务状态，`update-status.json` 保留最近一次任务报告。

## 日志、失败与数据时间

```bash
systemctl start --no-block paper-compass-update.service
systemctl status paper-compass-update.service --no-pager
journalctl -u paper-compass-update.service -n 100 --no-pager
cat /opt/paper-compass/.runtime-demo/update-status-arxiv.json
cat /opt/paper-compass/.runtime-demo/query-store/current.json
```

单个采集阶段失败后继续其他阶段；警告或元数据补全失败也会标记任务未完全成功。成功入库的记录保留，并尝试发布完整、一致的候选查询库。状态报告在每个阶段完成后原子写入：运行期间 `status=running`，最终为 `complete` 或 `partial_failure`。`publication_rss` 是 RSS 后的提前发布，`publication` 是最终发布。任务被终止时可能停留在 `running`，应结合 systemd 状态判断。发布失败时旧查询库继续可用。

前端刷新后获得新版本，浏览器 HTTP 缓存可能额外延迟约一分钟。MCP 通过原 API 获得更新，无需客户端分类或索引。可选字段 `dataUpdatedAt` 由查询库携带，前端以 UTC+8 显示；部分采集成功也可发布，完整性请查管理员日志。

分类/转换代码发生语义变化时，维护者应递增 `query_store.FORMAT_VERSION`。仅修改主题 YAML 的分类规则会自动识别并全量重算。

## 本地验证结果

同一份原始库共 62,484 条记录，可展示 61,661 篇。首次构建前后原始数据库 SHA-256 一致。

| 指标 | 本地单次实测 |
| --- | --- |
| 首次后台构建 | 82.34 秒 |
| 无变化的增量构建 | 6.48 秒，重新分类 0 条 |
| 已发布查询库加载 | 0.0008 秒 |
| 第一页查询 | 0.0023 秒 |
| 首次搜索 inference | 0.2285 秒，7,903 条 |
| 查询进程峰值内存（包含一次搜索） | 40.0 MiB |

启动加载耗时不包含 Python 解释器和模块导入；实际 HTTP 健康检查、首页及分页接口已验证返回 200。论文总数、来源数、已分类数、信息完整数及主题、年度分布与旧实现一致。以上不是云服务器响应时间保证。

## 停用与回滚

```bash
systemctl disable --now paper-compass-update.timer
systemctl disable --now paper-compass-conferences.timer
```

停用 timer 不终止当前任务；需要时另行停止 `paper-compass-update.service`。代码回滚无需迁移原始数据库；旧实现仍可读取原始库。

需要回退发布版本时，先停止采集和网站，备份 `current.json`，将其中 `database` 指向仍保留的已验证 `query-*.db` 文件，再启动网站。不要覆盖运行中的数据库文件。修复后运行 `build-query` 可重新生成最新查询库；查询库是可重建派生数据，不用于反向恢复原始库。

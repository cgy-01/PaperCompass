# Meilisearch 轻量标题检索试验

这是本地独立试验，不是生产搜索切换。现有网站和 MCP 的搜索实现没有因此改变。

## 范围与选择

采用 Meilisearch Community Edition，固定版本 `v1.57.0`。只搜索标题；通过 `--with-abstract` 可另建标题加摘要索引。不下载、解析或搜索 PDF，不调用付费 API，不加载语义模型，不升级 Python 依赖。

“全文搜索引擎”是技术类型，不代表必须索引论文全文。默认 `searchableAttributes` 只有 `title`；来源、日期、主题、原文 URL 是返回或筛选用的元数据，不参加文本匹配。

这次试验使用词项检索、前缀和拼写容错，**不是语义搜索**。中文查询不会自动翻译成英文论文关键词。用于先验证标题查找的资源、速度和可用性；跨语言效果仍需单独选择和评测模型，不能把此前向量排序的问题说成已解决。

## 获取固定社区版

Linux x86-64，从官方 GitHub Release 下载社区版；不要选择 enterprise 文件：

```bash
cd /home/cgy/projects/paper-compass
mkdir -p tmp/meilisearch-trial/bin
curl -fL --retry 2 \
  https://github.com/meilisearch/meilisearch/releases/download/v1.57.0/meilisearch-linux-amd64 \
  -o tmp/meilisearch-trial/bin/meilisearch
```

运行前核对官方 release 资产 SHA-256：

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib
path = Path('tmp/meilisearch-trial/bin/meilisearch')
expected = 'cc5c8825d6effd5295c534fd2fb92f14410eb9a14a88a012805cb239f2dc7969'
if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
    raise SystemExit('Checksum mismatch; do not execute this binary')
path.chmod(0o755)
PY
```

## 执行试验

```bash
python3 scripts/try_title_search.py
```

默认只读 `.runtime-demo/query-store/current.json` 指向的查询数据库，派生索引写入独立 `.runtime-meilisearch-trial/index/`。源数据库使用 `mode=ro` 打开。脚本不加载项目 `.env`，不导入业务采集、分类或向量建库模块。

Meilisearch 只监听 `127.0.0.1:7700`，禁用遥测，索引线程设为 1，索引内存配置为 128 MiB。该配置不是整个进程的硬内存上限；实测 RSS 和峰值另行记录。脚本生成独立本地 master key（文件权限 `0600`），不会使用生产密钥。HTTP 访问禁用环境代理。

需要手动体验内置搜索界面时：

```bash
python3 scripts/try_title_search.py --serve
```

打开 `http://127.0.0.1:7700`，如提示输入 master key，在另一个终端读取：

```bash
cat .runtime-meilisearch-trial/master.key
```

选择 `papers_title` 索引。Ctrl+C 停止试验进程；索引保留，可重复执行。此地址是搜索引擎的开发预览，不是 Paper Compass 正式前端；不应暴露到公网。

添加摘要对照测试：

```bash
python3 scripts/try_title_search.py --with-abstract
```

它使用同一试验实例的另一个 `papers_title_abstract` 索引，不修改标题索引。若已有 `--serve` 进程，请先 Ctrl+C 停止，再执行新命令。

## 测量与限制

每次结果写入 `.runtime-meilisearch-trial/results.json`，包含索引数量、构建时间、进程 RSS/峰值、索引实际占用磁盘、二进制大小，以及每条查询 10 次本机 HTTP 请求的首轮和中位耗时、前五条结果。

首次建库完成并停止进程后，可以只复用索引，测量重启后的查询占用：

```bash
python3 scripts/try_title_search.py --reuse-index --serve
```

此时结果单独保存为 `results-reused.json`，不覆盖第一次建库的测量结果。

测试涵盖标题、英文关键词、拼写错误、中文输入和无结果输入。基准请求使用 `matchingStrategy=all`，不自动删除查询词凑满结果。内置网页预览可能使用自己的默认匹配设置，不能把两者当作同一查询参数。

测试是顺序本机 HTTP 请求，不是并发压测，也不是检索准确率评估。服务器内存还需为网站、采集、分类、SQLite 和操作系统留出空间；本地结果不能直接保证 2 GiB 生产环境负载。

回滚只需结束试验进程。现有主库、查询快照指针、网站 API、MCP 和服务器均不依赖本试验，不需回滚数据。

## 验证代码

```bash
python -m pytest tests/test_title_search_trial.py -q
```

覆盖只导出允许字段、可选摘要、哈希 ID、非法快照路径、索引任务失败与超时、绕过环境代理。真实引擎索引和查询由上面的独立脚本验证。

官方依据：[社区版仓库](https://github.com/meilisearch/meilisearch)、[固定版本发布](https://github.com/meilisearch/meilisearch/releases/tag/v1.57.0)、[实例资源配置](https://www.meilisearch.com/docs/resources/self_hosting/configuration/reference)。

## 本地实测：2026-11-23

使用现有已发布查询库中的全部 61,661 篇论文，只索引标题。官方社区版二进制 SHA-256 已核验，实际版本为 `meilisearch 1.57.0`。

| 项目 | 实测 |
| --- | --- |
| 首次导入和建立标题索引 | 13.275 秒 |
| 重新启动引擎至健康可用 | 约 0.10 秒，使用已有索引，操作系统缓存未清空 |
| 重启后、查询前进程 RSS | 71.90 MiB |
| 重启后跑完 8 组测试的进程 RSS / 峰值 | 179.48 MiB |
| 首次建库及随后查询的进程 RSS 峰值 | 442.29 MiB |
| 标题加展示元数据的派生索引实际磁盘占用 | 211.36 MiB |
| 社区版二进制文件大小 | 340.79 MiB，独立于索引大小 |
| 本机顺序 HTTP 查询中位耗时 | 约 0.95–2.59 毫秒，按查询重复 10 次 |
| 付费调用 / 新增论文向量编码 | 0 / 0 |

结果示例（数量是引擎的 `estimatedTotalHits`，不能当作人工标注的相关论文总数）：

| 输入 | 估算匹配数 | 观测 |
| --- | --- | --- |
| `reinforcement learning` | 1,834 | 首篇为 Reinforcement Learning with Verifiable Rewards for Small Search Agents |
| `distributed training` | 36 | 首篇为 Quantized Distributed Training of Large Models with Convergence Guarantees |
| `agent memory` | 76 | 首篇为 Agent Memory with Episodic Retrieval for Financial Decision-Making |
| `FlashAttention` | 11 | 前列返回 FlashAttention 系列论文 |
| 拼写错误 `FlashAttentin` | 7 | 仍返回 FlashAttention 系列论文 |
| `I/O Complexity of Attention, or How Optimal is FlashAttention?` | 1 | 精确找到该论文 |
| `强化学习` | 0 | 未实现中文到英文的语义匹配，不视为已解决跨语言搜索 |
| 无意义随机输入 | 0 | 不把整库作为候选结果返回 |

只读源查询库的 `PRAGMA quick_check` 为 `ok`，数量仍为 61,661；主 `current.json` 的 SHA-256 前后相同。网站、MCP 与服务器未切换。7 项试验脚本测试通过；真实引擎的 61,661 条索引数量与源库一致。摘要模式已提供开关和字段导出单元测试，本次没有对摘要索引做资源基准。

判断：可以作为轻量标题查找候选，资源和速度值得继续验证。还需要与实际产品入口接通并检验筛选、分页、排序、增量更新，再决定是否替换现有实验方案。约 179 MiB 是本次有限查询工作集的 RSS，不是未来任意并发与任意查询的内存上限。

## 接入 Paper Compass

单机无密钥模式可直接使用官方社区版。以开发模式启动，只绑定回环地址，服务环境中省略 `MEILI_MASTER_KEY`：

```bash
meilisearch --env development --http-addr 127.0.0.1:7700 --db-path .runtime-meilisearch/index --no-analytics --max-indexing-memory 128MiB --max-indexing-threads 1
```

项目 `.env` 只需要 `PAPER_COMPASS_MEILI_URL=http://127.0.0.1:7700`，省略两个密钥变量。同机进程可以直接读写该实例的索引。Nginx 只代理 Paper Compass 网站和 MCP 入口。

需要限制同机访问时，可为 Meilisearch 设置主密钥，并在 Paper Compass 配置 `PAPER_COMPASS_MEILI_SEARCH_KEY` 和 `PAPER_COMPASS_MEILI_WRITE_KEY`。前者用于搜索，后者用于发布索引。

使用原有 Python 环境执行 `paper-firehose build-search`，为目前发布的 SQLite 快照建立索引，然后启动网站。网站和 MCP 共用此索引。未配置 Meilisearch URL 时，网站使用 SQLite 标题子串查找。升级时请从实际 `.env` 移除废弃的 `PAPER_COMPASS_SEARCH_*` 变量。新的查询快照会移除旧向量表，已有快照和模型文件可按需另外清理。

后续 `build-query` 和定时发布会为新快照分批构建标题索引，索引成功后才切换快照，保留上一版用于已有请求。当前实现每次发布重建的是派生标题索引，启动网站时复用已有索引。关键词结果按相关性排序，支持来源、主题、类型和时间筛选，并最多分页浏览 5,000 条。摘要从 SQLite 读取，中文输入不会自动翻译英文标题。

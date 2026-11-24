# 网站只读 API v1

新接口与旧 `/api/bootstrap`、`/api/papers` 并行。网页与 MCP 共用标题搜索。

## 读取方式

- `GET /api/v1/bootstrap`：主题、来源、主页统计、最近论文与 `dataVersion`。
- `GET /api/v1/papers`：分页轻量列表；支持 `q`、`type`、`theme`、`source`、`days`、`sort`、`page`、`page_size`、`ids`、`search_field`。
- `GET /api/v1/papers/<publicId>`：完整论文详情，返回 `{paper, dataVersion}`；独立可访问。

列表返回 `{items,total,page,pages,dataVersion}`。每篇论文包含 `publicId`、标题、作者、来源、日期、主题、原文 URL 和最多 320 字符的 `summaryPreview`，不返回完整 `summary` 或研究笔记 `insight`。详情返回完整内容。列表不会自动下载或解析 PDF。

`publicId = SHA-256("paper-compass:" + 原始 ID)`，64 位十六进制；在多次发布间稳定。页面地址仅使用这个哈希，原始 ID 保留在响应与浏览器收藏中以兼容旧记录。哈希是路由标识，不是访问凭据或权限控制。

`page` 从 1 开始，默认每页 30，最多 100；`ids` 每次最多 250。没有关键词时保留 `date`（默认）、`score`、`title` 浏览排序。

配置 Meilisearch 后，`q` 按标题检索并按相关性排序，可结合来源、类型、主题和日期筛选。

Meilisearch 响应包含 `searchMode=meilisearch`、`sort=relevance` 和 `searchField=title`，可分页查看最多 5,000 条匹配。未配置引擎时使用 SQLite 标题子串查找。

不再执行字面子串搜索，不回退到旧算法。`ids` 精确读取与详情哈希查找继续可用。

## 发布版本与错误

新列表可带 bootstrap 返回的 `version=<dataVersion>`。仍保留的旧版本可继续分页；已清理版本返回 409，前端重新取得 bootstrap 后再读取。每次 COUNT 与列表查询使用同一 SQLite 连接。过滤计数缓存最多 128 项、60 秒，并按实际连接的数据库版本隔离；前端响应缓存最多 40 项、30 秒，列表对象缓存最多 180 篇。

参数无效返回 400，详情不存在返回 404，旧版本过期返回 409，服务或索引不可用返回 503。

## 升级与回滚

无需更改原始数据库或升级依赖。首次部署新代码后，运行原有 `build-query` 命令可在候选查询库中生成 `public_paper_ids` 哈希索引；完成校验后原子发布，网站可以继续读取旧版本。这个过程不会因增加哈希索引而强制重新分类全部论文。

旧查询快照也可直接用于新代码；详情哈希在旧快照上通过只读计算查找，直到下一次查询库发布建立索引。生产环境建议先完成一次 `build-query` 再切换代码，以获得索引查询性能。

回滚数据视图使用现有查询快照流程。

首次启用搜索须为当前快照执行 `build-search`。详见[Meilisearch 接入说明](admin/meilisearch_title_trial.md)。

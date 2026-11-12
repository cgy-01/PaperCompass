# 开发与测试

## 开发环境

使用 README 的源码安装步骤。开发测试另外安装已固定版本的测试依赖：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
npm ci
python -m pytest -q
```

现有测试包含 CLI、数据库、分类、发布版本、采集模拟和 MCP。测试使用临时目录和模拟来源，不应指向生产运行目录。真实外部来源的可达性与限流需单独检查。

## 目录结构

```text
src/paper_firehose/
  cli.py                   管理命令
  local_app.py             只读网站、API 与文档白名单
  query_store.py           派生查询库构建、校验与发布
  mcp_server.py            只读 MCP 适配层
  commands/                arXiv、会议、更新编排等命令
  core/                    配置、数据库、路径与 HTTP 工具
  processors/              可选处理模块
  system/config/           默认配置模板
  system/templates/        前端与其他 HTML 模板
deploy/systemd/            采集任务部署示例
docs/                      用户、管理员和开发者文档
tests/                     测试
```

`paper_firehose` 是保留的内部包名，不需要为修改产品文案而重命名它。不要重命名现有论文 ID、数据库表和公开 API 字段。

## 前端开发

主要页面是 `system/templates/dashboard_template.html`，通过 `dashboard` 命令生成轻量 HTML 外壳；论文来自 `/api/bootstrap` 与 `/api/papers`。

```bash
paper-firehose dashboard
```

保存模板后重新生成页面并刷新浏览器。Node 仅准备 Lucide 资源：必须先执行 `npm ci`；Python 的 `dashboard` 会从仓库 `node_modules/lucide/` 复制脚本。不会自动从 CDN 下载图标。

本地辅助脚本保留现有行为：`start_dashboard.sh` 优先选择原开发环境 `/home/cgy/miniconda3/envs/paper-compass`，否则选择仓库 `.venv`；`start_frontend_dev.sh` 使用相同约定，且依赖 Linux `stat`。其他环境建议直接使用已激活环境中的 `paper-firehose serve`，不必修改脚本或创建同名个人路径。

## 文档与打包

网站只展示 `DocumentationRepository.CATALOG` 显式登记的用户文档。增加管理员文档不等于将其加入网站导航。

当前以源码安装为支持路径。独立 wheel 的用户文档和 Node 图标资源尚未作为完整分发流程验证，不应宣称 `pip install` 某个同名发行包即可得到当前产品。

运行数据、依赖环境和生成的 `*.egg-info` 都不是源码。修改配置模板时，请同时说明已有实例如何手动更新，避免覆盖本地配置。

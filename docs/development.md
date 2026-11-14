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
  accounts.py              GitHub OAuth、账号与行为上报接口
  user_store.py            独立用户、会话、收藏和已读数据库
  event_store.py           独立行为库、去重、每日统计和保留策略
  commands/                arXiv、会议、更新编排等命令
  core/                    配置、数据库、路径与 HTTP 工具
  processors/              可选处理模块
  system/config/           默认配置模板
  system/templates/        前端与其他 HTML 模板
  system/frontend/         页面、路由、请求层、公共组件与样式
deploy/systemd/            采集任务部署示例
docs/                      用户、管理员和开发者文档
tests/                     测试
```

`paper_firehose` 是保留的内部包名，不需要为修改产品文案而重命名它。不要重命名现有论文 ID、数据库表和公开 API 字段。

## 前端开发

`system/templates/dashboard_template.html` 只保留页面外壳。`system/frontend/app.js` 负责装配，`pages/` 按页面组织，`components/` 放公共组件，`state.js` 保存浏览器状态，`router.js` 管理 hash 路由，`api.js` 管理请求取消、超时与有界缓存，`styles/app.css` 保存样式。使用浏览器原生 ES modules，不新增构建框架或运行依赖。

默认入口为产品介绍页 `#welcome`，主按钮进入工作区总览 `#home`；原有 `#discover`、`#venues`、`#topics`、`#library`、`#docs`、`#account` 与 `#paper/<SHA-256 哈希>` 仍可直接打开。介绍页主体不等待文献接口，数字和最近论文预览在后台加载，接口失败不会挡住介绍内容。筛选与页码写入 URL，浏览器前进后退恢复筛选和当前会话滚动位置。直接刷新详情会独立请求数据，不依赖列表缓存。标题是站内链接，点击卡片空白处同样进入详情，原文链接在详情页提供。

前端使用新增的 `/api/v1/bootstrap`、`/api/v1/papers` 和 `/api/v1/papers/<hash>`，详见 [接口说明](api_v1.md)。旧 API 保留供现有客户端和 MCP 使用。

```bash
paper-firehose dashboard
```

保存模板或前端模块后重新生成页面并刷新浏览器。`start_frontend_dev.sh` 会监听两者，重新生成外壳并复制模块，服务继续运行。Node 仅准备 Lucide 资源：必须先执行 `npm ci`；Python 的 `dashboard` 会从仓库 `node_modules/lucide/` 复制脚本。不会自动从 CDN 下载图标。

运行 `./start_frontend_dev.sh 9000` 启动开发预览。脚本先检查端口，并在健康检查通过后提示就绪。在 WSL 中默认监听 `0.0.0.0`，同时打印 WSL IP 链接；Windows 的 localhost 转发失效时使用该链接。IP 可能随 WSL 重启变化，以本次输出为准。此模式仅用于可信网络；可用 `PAPER_COMPASS_HOST=127.0.0.1 ./start_frontend_dev.sh 9000` 限制为回环监听。云服务器的 systemd 服务不受影响。

前端路由单元测试：`node --test tests/frontend_routes.mjs`。可选浏览器回归测试：将 `PAPER_COMPASS_BROWSER` 设置为本机 Chrome 可执行文件，再执行 `python -m pytest tests/test_frontend_browser.py -q`；需要 Node 22 或更新版本的原生 WebSocket，但不需要安装浏览器测试框架。测试使用临时数据库与随机本地端口。

宣传页由 `pages/welcome.js` 和独立的 `styles/welcome.css` 增强；首屏、瀑布流等视觉结构参考 [Digest 模板预览](https://digest-zebno66.app.builtwithrocket.new/digest)，使用本项目的原生模块重新实现，不依赖 Rocket 或 Next.js。首屏论文统计和论文预览使用 bootstrap 中的真实数据；浮动终端与 Loss Curve 使用指定的演示日志，不会实际执行训练命令。中文排版与显示层间距由 `styles/typography.css` 和 `typography.js` 统一处理，不改写数据库字段、筛选值和代码。页内滚动不改变 `#welcome`，滚动监听只安装一次，进入工作区后隐藏宣传页浮层。

宣传页的研究领域区直接使用 bootstrap 的 `topics` 名单、图标和说明，不维护另一份主题清单。主题链接按准确名称编码到 `#discover?theme=...`，在已发布目录更新并刷新页面后同步展示。

本地辅助脚本保留现有行为：`start_dashboard.sh` 优先选择原开发环境 `/home/cgy/miniconda3/envs/paper-compass`，否则选择仓库 `.venv`；`start_frontend_dev.sh` 使用相同约定，且依赖 Linux `stat`。其他环境建议直接使用已激活环境中的 `paper-firehose serve`，不必修改脚本或创建同名个人路径。

## 文档与打包

网站只展示 `DocumentationRepository.CATALOG` 显式登记的用户文档。增加管理员文档不等于将其加入网站导航。

当前以源码安装为支持路径。独立 wheel 的用户文档和 Node 图标资源尚未作为完整分发流程验证，不应宣称 `pip install` 某个同名发行包即可得到当前产品。

运行数据、依赖环境和生成的 `*.egg-info` 都不是源码。修改配置模板时，请同时说明已有实例如何手动更新，避免覆盖本地配置。

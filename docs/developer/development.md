# 开发与测试

## 开发环境

使用 README 的源码安装步骤。开发测试另外安装已固定版本的测试依赖：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
npm ci
python -m pytest tests -q
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
deploy/systemd/            采集与独立分类任务部署示例
docs/user/                 网站与 MCP 用户手册
docs/developer/            开发、接口、部署、运维与报告
docs/img/                  共享文档图片
tests/                     测试
```

`paper_firehose` 是保留的内部包名，不需要为修改产品文案而重命名它。不要重命名现有论文 ID、数据库表和公开 API 字段。

## 前端开发

业务配置统一放在根目录 `.env`，脚本和 CLI 自动加载。首次可复制 `.env.example` 并设置 600 权限；已有文件不要覆盖。不需要为 GitHub、分类或开发账号另建 env 文件。变量优先级和字面值格式见[配置说明](configuration.md)。

本地直接运行 `./start_frontend_dev.sh 9000`，打开 `http://127.0.0.1:9000`。脚本默认启用 `local-developer` 开发账号，自动建立本地会话，进入工作区无需 GitHub 配置。收藏与已读走现有真实接口，保存在 `.runtime-demo/users/development.db`；重启后保留，不写入正式 `users/users.db`。默认关闭本地行为上报，可显式设置 `PAPER_COMPASS_EVENTS_ENABLED=1` 调试。

开发账号模式强制监听 loopback 地址，只接受本机连接与配置匹配的 Host，不支持通过 WSL IP 或局域网地址登录。WSL 的浏览器请使用 localhost 转发到 `127.0.0.1:9000`。如已导出其他 `PAPER_COMPASS_PUBLIC_URL`，先 `unset PAPER_COMPASS_PUBLIC_URL` 再启动。脚本仅在自己的子进程环境中忽略 GitHub 凭据，不修改父终端或服务器配置。

生产服务使用 `paper-firehose serve`，`PAPER_COMPASS_DEV_LOGIN` 默认 0，继续配置正式 GitHub OAuth。如需本地验证真实授权，运行 `PAPER_COMPASS_DEV_LOGIN=0 ./start_frontend_dev.sh 9000` 并显式注入测试 OAuth 配置。仅上传源码不会开启服务器开发账号；正式域名或 GitHub 凭据与开发开关混用时会拒绝启用。

welcome 页所有工作区入口使用原生 `/auth/github/login?return_to=<hash>` 链接。后端每次检查真实会话：有效时直达目标页，匿名时发起 GitHub OAuth。没有中间登录页；旧 `#login` 书签保留为直接授权跳转。页内滚动、外部 GitHub 仓库与邮件链接保持原行为。此处是网站入口交互，原有只读论文 API 与 MCP 的访问方式不变。

`system/templates/dashboard_template.html` 只保留页面外壳。`system/frontend/app.js` 负责装配，`pages/` 按页面组织，`components/` 放公共组件，`state.js` 保存浏览器状态，`router.js` 管理 hash 路由，`api.js` 管理请求取消、超时与有界缓存，`styles/app.css` 保存样式。使用浏览器原生 ES modules，不新增构建框架或运行依赖。

默认入口为产品介绍页 `#welcome`，主按钮进入工作区总览 `#home`；原有 `#discover`、`#venues`、`#topics`、`#library`、`#docs`、`#account` 与 `#paper/<SHA-256 哈希>` 仍可直接打开。介绍页主体不等待文献接口，数字和最近论文预览在后台加载，接口失败不会挡住介绍内容。筛选与页码写入 URL，浏览器前进后退恢复筛选和当前会话滚动位置。直接刷新详情会独立请求数据，不依赖列表缓存。标题是站内链接，点击卡片空白处同样进入详情，原文链接在详情页提供。

前端使用新增的 `/api/v1/bootstrap`、`/api/v1/papers` 和 `/api/v1/papers/<hash>`，详见 [接口说明](api_v1.md)。旧 API 保留供现有客户端和 MCP 使用。

```bash
paper-firehose dashboard
```

保存模板或前端模块后重新生成页面并刷新浏览器。`start_frontend_dev.sh` 会监听两者，重新发布页面外壳与资源，服务继续运行。必须先执行 `npm ci` 准备锁定版本的 Lucide；Python 的 `dashboard` 从官方包中提取本项目使用的图标（含别名），保留原始图形和许可证。浏览器不再下载完整图标库，也不从 CDN 下载图标。

运行 `./start_frontend_dev.sh 9000` 启动开发预览。脚本先检查端口，并在健康检查通过后提示就绪。在 WSL 中默认监听 `0.0.0.0`，同时打印 WSL IP 链接；Windows 的 localhost 转发失效时使用该链接。IP 可能随 WSL 重启变化，以本次输出为准。此模式仅用于可信网络；可用 `PAPER_COMPASS_HOST=127.0.0.1 ./start_frontend_dev.sh 9000` 限制为回环监听。云服务器的 systemd 服务不受影响。

前端单元测试：`node --test tests/frontend_routes.mjs tests/frontend_typography.mjs tests/frontend_api.mjs tests/frontend_experience.mjs`。可选浏览器回归测试：将 `PAPER_COMPASS_BROWSER` 设置为本机 Chrome 可执行文件，再执行 `python -m pytest tests/test_frontend_browser.py -q`；需要 Node 22 或更新版本的原生 WebSocket，但不需要安装浏览器测试框架。测试使用临时数据库与随机本地端口。设置 `PAPER_COMPASS_VISUAL_FREEZE=1` 可把循环动画固定到相同帧用于视觉对比，不改变产品代码中的动效。

宣传页由 `pages/welcome.js` 和独立的 `styles/welcome.css` 增强；首屏、瀑布流等视觉结构参考 [Digest 模板预览](https://digest-zebno66.app.builtwithrocket.new/digest)，使用本项目的原生模块重新实现，不依赖 Rocket 或 Next.js。首屏论文统计和论文预览使用 bootstrap 中的真实数据；浮动终端与 Loss Curve 使用指定的演示日志，不会实际执行训练命令。中文排版与显示层间距由 `styles/typography.css` 和 `typography.js` 统一处理，不改写数据库字段、筛选值和代码。页内滚动不改变 `#welcome`，滚动监听只安装一次，进入工作区后隐藏宣传页浮层。

宣传页的研究领域区直接使用 bootstrap 的 `topics` 名单、图标和说明，不维护另一份主题清单。主题链接按准确名称编码到 `#discover?theme=...`，在已发布目录更新并刷新页面后同步展示。

首屏由独立入口 `welcome-boot.js` 提前初始化终端、曲线与必要图标，不等待工作区模块或论文接口。工作区页面按需导入，在用户悬停、聚焦或点击工作区入口时预热总览；开启省流量设置时不做预热。图标只替换未绘制的占位元素，账号就绪不再重建宣传页。终端与来源条带的 CSS 动效参数不变。浏览器回归包含阻断主应用下载的首屏检查、按需加载检查与图标节点复用检查。

前端资源使用整组内容版本 `/assets/<20 位哈希>/...`，所有相对导入锁定同一版本；旧版本保留给已经打开的标签页。脚本启动时自动生成静态 gzip 副本；HTTP 层提供 gzip 协商、ETag、HEAD/304 和不可变资源缓存，HTML 与未版本化兼容链接仍重新验证。详见 [前端性能与发布](admin/frontend_performance.md)。

本地辅助脚本保留现有行为：`start_dashboard.sh` 优先选择原开发环境 `/home/cgy/miniconda3/envs/paper-compass`，否则选择仓库 `.venv`；`start_frontend_dev.sh` 使用相同约定，且依赖 Linux `stat`。其他环境建议直接使用已激活环境中的 `paper-firehose serve`，不必修改脚本或创建同名个人路径。

在 WSL 中，两份启动脚本会检测 Windows 的 TCP 保留端口。默认端口落入保留范围时自动选择可用替代端口，以终端打印的地址为准；显式指定的保留端口会报错并提示换端口，不会悄悄更改。Linux 内绑定成功并不代表 Windows 能转发该端口，此检查不修改系统保留范围、防火墙或 WSL 配置。

## 文档与打包

文档按读者分为 `docs/user/` 与 `docs/developer/`。部署、运维、研究报告均归入开发者文档，详见[文档总入口](../README.md)。网站只展示 `DocumentationRepository.CATALOG` 显式登记的用户文档；增加开发者文档不等于将其加入网站导航。共享图片仍位于 `docs/img/`，网站文档 API 和图片地址不变，旧的自定义平铺文档目录继续兼容。

当前以源码安装为支持路径。独立 wheel 的用户文档和 Node 图标资源尚未作为完整分发流程验证，不应宣称 `pip install` 某个同名发行包即可得到当前产品。

GitHub 上的日常提交只运行 CI，不自动发布版本或连接服务器。版本标签会触发源码应用包的完整验证与 GitHub Release 交付；服务器由维护者手动下载更新。详见 [GitHub CI 与版本交付](admin/github_ci_cd.md)。

运行数据、依赖环境和生成的 `*.egg-info` 都不是源码。修改配置模板时，请同时说明已有实例如何手动更新，避免覆盖本地配置。

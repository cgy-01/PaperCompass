# GitHub 账号与用户行为记录（v1.2 开发）

账号模块不改动任何原始论文表，也不向论文查询快照写入用户数据。新增逻辑不升级运行依赖。

## 独立存储

在 `PAPER_FIREHOSE_DATA_DIR` 下创建：

- `users/users.db`：GitHub 数字 ID、平台 UUID、公开昵称、头像 URL、登录时间、会话哈希、短期 OAuth state、收藏和已读关系。
- `analytics/events.db`：行为事件及按北京时间日历日汇总的事件量、浏览器访客、登录账号和访问会话数量。

两个库使用 WAL、每次操作独立连接、有限锁等待；目录默认权限 700、文件 600。它们不进入 `build-query`、论文快照轮换或论文库备份。Web 实例在首次访问账号或上报接口时创建这些库；纯论文 API 与 MCP 读取不要求登录。

## 配置 GitHub 登录

在 GitHub 创建 OAuth App，生产设置示例：

- Homepage URL：`https://papercompass.cn`
- Authorization callback URL：`https://papercompass.cn/auth/github/callback`

为 Web systemd 服务添加 `EnvironmentFile`，文件放在仓库与静态目录以外，权限设为 600：

```ini
PAPER_COMPASS_PUBLIC_URL=https://papercompass.cn
GITHUB_CLIENT_ID=替换为实际值
GITHUB_CLIENT_SECRET=替换为实际值
PAPER_COMPASS_EVENTS_ENABLED=1
PAPER_COMPASS_EVENTS_RETENTION_DAYS=90
PAPER_COMPASS_EVENTS_DAILY_LIMIT=100000
```

例如 drop-in 文件 `/etc/systemd/system/paper-compass.service.d/account.conf`：

```ini
[Service]
EnvironmentFile=/etc/paper-compass/accounts.env
```

更新环境后执行 `systemctl daemon-reload` 和 `systemctl restart paper-compass`。不要把实际密钥填写进源码、公开文档或前端。项目不会自动加载 `.env`。

未配置完整凭据时，登录按钮显示暂不可用，论文服务与匿名本机文献库仍可使用。匿名行为记录默认开启，可通过环境变量关闭。生产环境必须填写 HTTPS 公共地址；本地开发可使用 `http://127.0.0.1:9000`，需单独配置对应回调的测试 OAuth App。

登录使用授权码流程、state 与浏览器 cookie 绑定、一次性 state 消费、PKCE S256。GitHub token 仅用于服务端即时读取 `/user`，不存入本地数据库或浏览器。平台会话固定有效 30 天，cookie 使用 HttpOnly、SameSite=Lax；HTTPS 下使用 Secure 与 `__Host-` 前缀。账号接口不缓存响应，写接口同时校验 Origin 和 CSRF token。

参考：[GitHub 官方 OAuth 授权流程](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)。

## 反向代理

让域名别名重定向至 `PAPER_COMPASS_PUBLIC_URL` 的规范域名；保留原有 `Host` 转发。生产写接口仅接受配置的精确 Origin。

应用日志不记录 OAuth 路径的查询参数。Nginx 标准日志仍可能记录授权码，因此建议对回调路径关闭访问日志：

```nginx
location = /auth/github/callback {
    access_log off;
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

此模块不需要重启 MCP，也不改变只读论文 API。`POST /api/refresh` 仍返回 405。

## 数据与分析

事件包含事件 UUID、服务端接收时间、服务端确认的账号 ID、浏览器标识哈希、访问会话 ID、页面、事件类型、公开论文哈希、控件名称与少量白名单字段。账号身份不接受客户端上报。事件包括 `page_view`、`page_leave`、`click`、`filter_change`、`search`；不记录搜索词正文、输入框文字、原始 IP、完整 URL、GitHub token 或邮件地址。

浏览器批量上报，每批最多 50 条、请求体最多 32 KiB；队列最多 100 条，失败不阻塞论文浏览。默认每日接收上限 10 万条，可配置；磁盘可用不足 1 GiB 时拒绝行为写入。重试通过事件 UUID 去重，不会重复累计统计。客户端拦截、断网、关闭页面等因素会造成漏报，浏览器访客数也不等于真实人数。

默认保留 90 天明细。Web 后台每小时分批清理过期事件和逐日去重标识，按天汇总的数值长期保存；进程关闭期间不执行清理，重启后逐批恢复。删除后 SQLite 页可重用，不承诺文件立即缩小。不要在线对数据库执行全量 `VACUUM`。账号收藏不随行为保留期删除。

使用实际运行目录查看私有统计（不会公开到网站）：

```bash
PAPER_FIREHOSE_DATA_DIR=/opt/paper-compass/.runtime-demo \
  /opt/paper-compass/.venv/bin/paper-firehose analytics --days 7
```

结果中 `daily.visitors` 是当日浏览器标识数量，`users` 是当日登录账号数量，`sessions` 是按浏览器和约 30 分钟闲置划分的访问会话数量，`actions` 按日期、事件类型和页面统计。事件时间统一按接收日归属北京时间。管理员可对私有 `events` 表按 `user_id`、`visitor_id`、`session_id` 和 `received_at` 查询近期行为序列；不提供公开的管理员统计 HTTP 接口。

## 备份与回滚

账号库是不可重建的业务数据，应单独安排备份，不能只备份论文库。在线备份使用 SQLite backup API，包含已提交 WAL 数据：

```bash
PAPER_FIREHOSE_DATA_DIR=/opt/paper-compass/.runtime-demo \
  /opt/paper-compass/.venv/bin/paper-firehose backup-users \
  --output /root/paper-compass-users-20261113.db
```

目标文件必须尚不存在。建议管理员每日执行并定期复制到服务器外；本版本不自动创建账号备份定时器。分析库如需备份同样使用 SQLite backup API，不能只复制正在写入的 `.db` 文件而忽略 WAL。

回滚 Web 源码到 v1.1 不影响论文库；保留新增 `users/`、`analytics/` 目录，旧版不会读取或删除它们。回滚后账号入口和上报停止，本机旧收藏仍保留。不要用论文快照恢复用户收藏。

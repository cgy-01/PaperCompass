# 前端性能与发布

本次优化保持所有 CSS 视觉参数、字体栈、布局、动效时间与交互入口不变；不修改论文数据库、配置结构或公开 API 的 JSON 结构，不升级依赖。

## 加载与渲染

- `welcome-boot.js` 是独立的首屏入口，优先级为 high；主应用入口为 low。不支持 Fetch Priority 的浏览器仍按原生模块顺序加载。
- `app.js` 使用动态导入加载当前页面；用户对工作区入口表达意图时预热总览，省流量模式下跳过预热。模块加载失败仍可重试。
- 发现、文献库、详情请求保留独立取消通道；切换页面时取消已经不需要的请求。缓存最多 40 个响应，论文对象索引最多 180 条。
- Lucide 仅发布项目所用的官方图标数据，解析官方导出表以保留 `align-left` 等别名；缺少模板必需图标时拒绝发布。保留官方渲染器与 ISC 许可证，不生成自定义图形。
- 已绘制 SVG 不会在普通刷新中被重新替换。账号就绪不重绘宣传页；主题、论文预览按数据引用更新；发现页收藏操作仅更新按钮和数量。
- 中文间距处理忽略 SVG/图标，合并重叠的变化区域，避免重复扫描整个页面。工作区筛选控件进入工作区时再初始化。
- 异步页面加载期间的新滚动意图优先于旧路由的位置恢复，避免页内导航被拉回。

没有对首屏使用 `content-visibility`、更改卡片尺寸、缩短动画或取消模糊效果来换取指标。

## 资源版本与压缩

`dashboard` 自动准备完整资源集，计算内容哈希并原子发布至 `/assets/<20 位哈希>/`。相对 ESM 导入始终处于同一发布版本，不会混入新旧文件。页面外壳在资源准备完成后原子替换。

| 响应 | 策略 |
|---|---|
| 成功的版本化资源 | `public, max-age=31536000, immutable` |
| HTML、未版本化兼容资源 | `no-cache`，使用 ETag 重新验证 |
| 公开论文 JSON | 保持原有 `private, max-age=60`，协商 gzip |
| 会话、账号、收藏与错误 JSON | `no-store`，不压缩敏感响应 |
| 不存在的资源 | 不使用 immutable 缓存 |

大小至少为 1 KiB 的文本资源可使用 gzip；版本化 JS/CSS 在发布阶段预压缩，避免每次请求重复压缩。响应包含 `Vary: Accept-Encoding`，遵守 `gzip;q=0`；gzip 与未压缩表示使用不同 ETag。支持 HEAD 与 If-None-Match/304。

压缩在应用层即可生效，不要求修改线上 Nginx 配置。Nginx 应透传 `Content-Encoding`、`Vary`、ETag 与 Cache-Control；不要对 `/api/account` 或 OAuth 回调开启公共代理缓存。

保留 `/assets/app.js` 等未版本化别名及原 `/lucide.min.js` 文件供旧页面兼容；新版 HTML 不请求完整图标库。

## 部署与回滚

1. 在构建/部署环境执行 `npm ci`，保持现有 lockfile。
2. 上传完整源码（包括 `frontend_assets.py`）、文档与前端目录。
3. 重启 Web 服务；或者开发时运行 `dashboard`，自动发布新资源版本。
4. 检查健康接口、HTML 中的版本路径、资源 Content-Encoding/Cache-Control 和页面。

不需要 `build-query`、数据库迁移、MCP 重启或修改采集定时器。真实部署时仍应先备份代码并验证健康状态。

不要只更新 HTML 或单个 JS，也不要使用 `rsync --delete` 清空运行目录或版本化资源。已经打开的标签页可能稍后才加载旧版本的某个页面模块。资源保留量随前端发布次数增长，与论文条数无关；清理历史版本前需给旧标签页留出兼容窗口。

回滚时恢复上一版源码，重新生成外壳并重启 Web 服务；论文、账号和分析数据库均不需要恢复。旧版本资源保留，回滚不依赖清空浏览器缓存。

## 验证

```bash
python -m pytest -q
node --test tests/frontend_routes.mjs tests/frontend_typography.mjs tests/frontend_api.mjs
PAPER_COMPASS_BROWSER=/path/to/chrome PAPER_COMPASS_VISUAL_FREEZE=1 \
  python -m pytest tests/test_frontend_browser.py -q
```

HTTP 回归位于 `tests/test_frontend_delivery.py`，覆盖稳定内容版本、旧版本保留、官方图标别名、缺失图标失败、gzip 协商、压缩与原文一致、敏感接口隔离、HEAD、ETag/304、缓存隔离及路径规范化。浏览器测试覆盖首屏独立加载、工作区按需加载、SVG 节点复用、收藏、登录、筛选、详情、路由前进后退和桌面/手机排版。

性能比较应使用相同数据、网络条件、CPU 降速与冷缓存设置，并将固定帧视觉比较与真实时间性能采样分开。单次实验的 FCP/LCP 不代表线上真实用户指标；线上分位数需要后续单独采样。

### 2026-11-15 本地对照记录

同一只读论文集、Chrome Headless 139、100 ms 延迟、1.6 Mbps 下行、CPU 降速 4 倍、冷缓存，加载后采样 12 秒。临时实例关闭账号与行为写入；不对生产数据库执行操作。

| 指标 | 优化前 | 优化后 |
|---|---:|---:|
| 资源/API 请求数（不含文档请求） | 37 | 24 |
| 解码后响应体总量 | 541,075 B | 158,404 B |
| 响应体载荷，不含 HTML 与响应头 | 约 541 KB（原版 JS/CSS/JSON 未压缩） | 49,680 B |
| FCP | 1,252 ms | 812 ms |
| LCP | 2,888 ms | 2,392 ms |
| ScriptDuration | 178.6 ms | 65.5 ms |
| SVG 累计插入计数 | 322 | 130 |
| CLS | 0 | 0 |

SVG 插入计数包含真正新增节点，不能全部视为浪费。固定帧截图对照覆盖桌面、宽屏、手机、标题入场、研究领域、论文预览、工作流和详情；除更新时间文字及极少量抗锯齿差异外保持一致。三份 CSS 与优化前逐字相同。最终检查：403 项 Python 测试、9 项 Node 测试通过。

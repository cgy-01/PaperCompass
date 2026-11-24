# 开发者文档

面向开发者、项目维护者和自托管管理员。本文档不进入网站用户手册导航；用户操作说明见 [用户文档](../user/README.md)。

## 入门与接口

| 文档 | 内容 |
| --- | --- |
| [开发与测试](development.md) | 目录结构、本地启动、开发账号、前端调试与测试 |
| [配置与环境变量](configuration.md) | 统一 `.env`、运行目录、主题与来源配置 |
| [网站只读 API v1](api_v1.md) | 分页、筛选、排序、详情与版本兼容 |
| [Meilisearch 接入说明](admin/meilisearch_title_trial.md) | 标题索引、配置与本地验证 |
| [MCP 部署说明](mcp_setup.md) | MCP 进程、本地测试、服务器接入与反向代理 |
| [CLI 与 Python 使用参考](usage.rst) | 可选命令与处理流程；实际命令参数以当前 `--help` 为准 |
| [Python API](api.rst) | Sphinx API 参考与自动生成入口 |
| [生成的 Python API 页面](generated/) | 已有模块参考页；以当前源码和重新构建结果为准 |

## 采集、分类与数据库

| 文档 | 内容 |
| --- | --- |
| [自动更新与版本化查询库](admin/automatic_updates.md) | 采集任务、来源水位、后台构建与原子发布 |
| [API 论文分类](admin/api_classification.md) | 模型输入输出、新论文队列、谷时、预算与人工复核 |
| [关键词分类备用方案](admin/topic_classification.md) | 保留的规则策略及手动切换方式 |
| [分类优化研究计划](admin/classification_optimization_plan.md) | 候选方案与评估思路，区别于已部署功能 |
| [数据库字段字典](admin/database_dictionary.md) | 各库业务表和字段含义 |
| [数据库架构与规模报告](reports/2026-11-19-database-architecture/README.md) | 真实容量、记录数、数据流和各环节读写职责 |

## 部署、运维与前端实现

| 文档 | 内容 |
| --- | --- |
| [账号与行为统计部署](admin/accounts_analytics.md) | GitHub OAuth、用户库、行为库与服务器配置 |
| [磁盘与备份保留](admin/storage_retention.md) | 当前查询版本、上一版、单份备份与清理策略 |
| [前端性能与发布](admin/frontend_performance.md) | 按需加载、资源版本、压缩与回滚 |
| [主页动效测量](admin/welcome_motion.md) | 参考视频、量化时间及实现记录 |
| [GitHub CI 与版本交付](admin/github_ci_cd.md) | 日常校验、版本构建和手动部署流程 |
| [源码发布检查](admin/source_release.md) | 发行内容、敏感数据排除与检查步骤 |
| [历史 Actions 流水线参考](github_actions_setup.rst) | 已停用的 Pages / 采集流程，仅供历史查阅 |
| [可选高校图片说明](../img/institutions/README.md) | 本地图片路径、授权与 Git 忽略规则 |

## 报告与证据归档

以下材料保留采样当时的数据与配置，不代替持续维护的操作手册。历史源码指纹也不代表当前提交；目录整理不会重新运行采样、改写账本或更新测量结论。

| 报告 | 日期 |
| --- | --- |
| [DeepSeek 分类 Token、耗时与费用实测](reports/2026-11-18-deepseek-classification-benchmark/README.md) | 2026-11-18 |
| [新增论文 API 分类规则、谷时与预算](reports/2026-11-19-api-classification-policy/README.md) | 2026-11-19 |
| [服务器近期论文新增量统计](reports/2026-11-19-server-paper-growth/README.md) | 2026-11-19 |
| [数据库结构只读快照](reports/2026-11-19-database-schema/README.md) | 2026-11-19 |
| [数据库架构、规模与运行职责](reports/2026-11-19-database-architecture/README.md) | 2026-11-19 |

## 仓库级说明

- [项目介绍与安装](../../README.md)
- [贡献指南](../../CONTRIBUTING.md)
- [项目交接记录](../../PROJECT_HANDOFF.md)：历史记录较多，运行操作优先参照对应维护手册。
- [安全问题反馈](../../SECURITY.md)
- [版本记录](../../CHANGELOG.md)
- [第三方许可说明](../../THIRD_PARTY_NOTICES.md)

Sphinx 仍从 `docs/` 构建，入口见 [index.rst](../index.rst)；不需要安装新的依赖。

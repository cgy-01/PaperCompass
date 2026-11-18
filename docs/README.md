# Paper Compass 文档中心

本目录同时保存公开用户手册和项目内部文档。文档文件不会因为放入 `docs/` 就自动出现在网站；公开内容必须经过审核，并显式加入 `DocumentationRepository.CATALOG` 白名单。

## 公开用户文档

| 文档 | 受众 | 网站公开 |
|---|---|---|
| [MCP 用户指南](MCP_USER_GUIDE.md) | Paper Compass 用户 | 是 |
| [账号与文献库](ACCOUNT_USER_GUIDE.md) | Paper Compass 用户 | 是 |

公开文档只应包含用户实际需要的信息，例如产品能力、客户端连接、使用示例、常见问题和能力边界。不得包含服务器路径、systemd、Nginx、数据库维护或部署凭据。

## 管理员文档

| 文档 | 受众 | 网站公开 |
|---|---|---|
| [MCP 部署说明](mcp_setup.md) | 服务器管理员 | 否 |
| [自动更新、查询库与部署](admin/automatic_updates.md) | 服务器管理员 | 否 |
| [论文主题分类与增量升级](admin/topic_classification.md) | 项目维护者 | 否 |
| [外部大模型 API 论文分类](admin/api_classification.md) | 项目维护者 | 否 |
| [新增论文 API 分类规则、谷时与预算归档](reports/2026-11-19-api-classification-policy/README.md) | 项目维护者 | 否 |
| [服务器近期论文新增量统计](reports/2026-11-19-server-paper-growth/README.md) | 项目维护者 | 否 |
| [DeepSeek 分类 Token、耗时与费用实测](reports/2026-11-18-deepseek-classification-benchmark/README.md) | 项目维护者 | 否 |
| [语义多标签分类优化计划](admin/classification_optimization_plan.md) | 项目维护者 | 否 |
| [账号与使用情况统计](admin/accounts_analytics.md) | 服务器管理员 | 否 |
| [源码发布检查](admin/source_release.md) | 项目维护者 | 否 |
| [GitHub Actions 配置](github_actions_setup.rst) | 项目维护者 | 否 |

## 开发者参考

| 文档 | 受众 | 网站公开 |
|---|---|---|
| [CLI 与 Python 使用说明](usage.rst) | 开发者 | 否 |
| [配置与环境变量](configuration.md) | 开发者和自托管用户 | 否 |
| [开发与测试](development.md) | 开发者 | 否 |
| [网站只读 API v1](api_v1.md) | 开发者 | 否 |
| [Python API](api.rst) | 开发者 | 否 |
| [Sphinx 文档入口](index.rst) | 文档维护者 | 否 |
| [`generated/`](generated/) | API 自动生成内容 | 否 |

## 发布规则

1. 新文档先明确受众：用户、管理员或开发者。
2. 只有用户文档可以登记到网站公开白名单。
3. 用户文档不得链接未公开的内部文档。
4. 管理员和开发者文档可以相互引用，但不应出现在前端导航。
5. 删除或重命名文档时同步检查 `PROJECT_HANDOFF.md`、Sphinx `toctree` 和代码白名单。

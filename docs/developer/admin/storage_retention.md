# 磁盘与单份备份保留规则

自 2026-11-19 起，当前线上库不算备份；**额外历史查询库最多 1 份，每个数据库的备份跨目录最多 1 份，代码与配置的部署回滚目录最多 1 个**。不同用途的数据库分别保留，不能把论文库、用户库、行为库当作同一个文件去重。

每次成功发布查询库后，立即保留当前版与其直接上一版，不再保留 48 小时内全部版本。发布失败不清理历史。Linux 下已打开的 SQLite 连接可继续读完；更旧的显式版本请求沿用 HTTP 409，让调用方重新获取当前版本；旧的非固定版本读取对象自动打开当前版。

原始库采集前的自动备份继续按每个数据库最新 1 份轮换。部署可能另外创建数据库副本，维护脚本跨运行目录和部署备份目录去重：按修改时间保留最新备份，删除重复旧副本。删除旧部署目录前，将其中仍是唯一或最新的用户库等备份移入保留的部署目录。当前线上数据库、分类结果库、查询清单、配置和同步水位均不清空。

部署目录内不额外保留查询库副本，查询历史只存在运行目录 `query-store`。保留的部署目录写有 `retention.json`，列明实际保留的数据库备份位置；恢复时按此清单取文件，不假定所有原始库仍位于部署目录。部署产生新回滚目录后应立即执行维护脚本，不等待下一天。

```bash
# 默认仅预览；--apply 才执行清理。
python3 scripts/prune_storage.py \
  --data-dir /opt/paper-compass/.runtime-demo \
  --backup-dir /var/backups/paper-compass
```

执行前同时取得采集锁和查询发布锁；任务忙时退出 75，暂不清理。删除前校验全部保留数据库的 quick_check；保留文件损坏或清单异常则拒绝删除。只处理命名符合约定的查询文件、自动备份和部署目录，拒绝相关符号链接，不清理 Python 环境或业务日志。

服务器维护任务每天北京时间 03:30 兜底执行，无模型请求。为避免 root 运行可被服务账户修改的代码，需将脚本安装为 root 所有的独立副本，并使用 Python 隔离模式；不是直接以 root 运行项目目录中的脚本：

```bash
install -o root -g root -m 0755 scripts/prune_storage.py /usr/local/libexec/paper-compass-retention.py
cp deploy/systemd/paper-compass-retention.service deploy/systemd/paper-compass-retention.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now paper-compass-retention.timer
```

每次备份生成期间可短暂同时存在新旧文件，成功完成并轮换后只保留一份；清理遇到权限或锁冲突时应看日志，不把失败当成已达到保留上限。每日维护不是替代每次部署后的即时清理步骤。

2026-11-19 分类策略归档保留其原始源码指纹，不重写历史报告；当前存储规则以本页为准。此变更不调整主题分类、每日 3 元预算、谷时策略或三项采集 / 分类时间。

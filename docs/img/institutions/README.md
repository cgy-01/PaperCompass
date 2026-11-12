# 可选高校标识

公开源码不包含校徽图片。本地或服务器已有图片不会被删除，也不影响原有展示。

自托管时可在此目录放置有权使用的图片，并在前端模板的 `institutionCatalog` 中设置名称与图片路径，例如 `/api/docs/assets/img/institutions/my-university.png`。缺失的图片会显示默认占位图标。

此目录除本说明以外的文件默认不进入 Git 提交。不要使用带 `--delete` 的同步命令覆盖生产素材目录。

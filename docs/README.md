# 项目文档

| 文档 | 内容 | 当前状态 |
| --- | --- | --- |
| [需求文档](project-requirements.md) | 产品范围、D-01～D-09 决策、AC-01～AC-22 验收 | 已确认 |
| [技术设计](../.trellis/tasks/10-19-telegram-media-downloader/design.md) | 架构、数据、状态、存储、代理、接口与运维契约 | 已确认，已实施本地版本 |
| [实施与验证计划](../.trellis/tasks/10-19-telegram-media-downloader/implement.md) | M0～M6 里程碑、验证命令、验收映射与回滚 | 实施中，实机验收未完成 |
| [技术证据与实验清单](../.trellis/tasks/10-19-telegram-media-downloader/research/technical-evidence.md) | 官方资料、仓库现状、V-01～V-08 技术门槛 | 文档核实完成，真实实验未执行 |
| [部署指南](deployment.md) | 根目录 Compose 文件位置、完整源码构建、飞牛 OS 命令行/界面、`.env` 与初始化 | 本地应用容器已验证，飞牛 OS 待验收 |
| [Windows 本地运行](local-run.md) | 云端接入、当前电脑配置位置、后续同界面切换本地 API | 云端仅需 Bot Token；本地需 API 凭据 |
| [维护指南](operations.md) | 备份、升级、恢复及排障 | 已编写 |
| [验证记录](verification.md) | 自动检查、浏览器结果与真实验收边界 | 持续更新 |
| [验收工具](acceptance.md) | 服务检查脚本、脱敏输出与实机验收步骤 | 本地测试已实现 |
| [Docker 隔离验收](docker-acceptance.md) | 生产应用镜像、密码边界、重建与停服备份恢复 | 工具已实现，实际结果见验证记录 |

2026-10-20：后端、WebUI、部署配置和本地自动验收已通过检查，Windows Docker 应用 WebUI 已启动。Docker 隔离验收结果以验证记录为准；真实 Bot API 完整栈、Telegram 和飞牛 OS 验收仍待执行。

---
layout: home
hero:
  name: TGDL
  text: Telegram 媒体下载助手
  tagline: 转发媒体，确认下载，保存到本地或 NAS。
  actions:
    - theme: brand
      text: 安装与部署
      link: ./deployment
    - theme: alt
      text: 第一次下载
      link: ./quick-start
features:
  - title: 按消息整理
    details: 单文件按日保存，同组图片与视频存放在同一目录，用文字摘要帮助识别任务。
  - title: WebUI 管理
    details: 顶部查看连接与队列，按使用流程设置 Bot、保存方式、提示及备份；调试详情默认隐藏。
  - title: 简单自托管
    details: Docker 部署，数据与媒体分开持久化；保留源码构建，配套镜像待首次发布。
---

云端下载上限 **20 MB**；大文件可在同一 WebUI 切换到本地 Bot API。先完成 [第一次下载](quick-start.md)，再按需调整 [日常设置](settings.md)、查看 [任务与进度](usage.md) 或 [备份配置](configuration-backup.md)。

当前处于开发阶段，版本与本轮变更见 [更新日志](changelog.md)。真实 Telegram、Docker 宿主机与代理链路的验收范围见 [验证说明](verification.md)。仅处理实际发送或转发给 Bot 的媒体，不处理消息链接，不登录个人账号。

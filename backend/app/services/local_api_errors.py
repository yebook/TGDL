"""Fixed supervisor failure categories shared by status and diagnostic projection."""

START_FAILURE_MESSAGES = {
    "invalid_configuration": "本地 API 启动指令格式无效，请检查应用与管理组件版本是否一致",
    "invalid_credentials": "本地 API 凭据解密或格式校验失败，请核对并重新保存 API ID / Hash",
    "invalid_proxy": "本地 API 代理配置校验失败，请核对并重新保存代理配置",
    "control_permissions": "本地 API 控制文件权限不正确，请检查控制卷的 UID/GID 和权限",
    "control_io_failed": "本地 API 启动所需文件读写失败，请检查控制卷、API 缓存目录权限及剩余空间",
    "stale_revision": "本地 API 拒绝了旧版本启动指令，请检查应用与管理组件的部署配置",
    "command_reused": "本地 API 启动指令编号冲突，请检查是否存在重复管理实例",
    "process_exited": "本地 API 进程启动后退出，请检查 API 缓存目录权限、运行环境及凭据",
    "listen_timeout": "本地 API 进程未在期限内监听服务端口，请检查启动环境、端口及存储状态",
    "process_start_failed": "本地 API 进程创建失败，请检查可执行文件、运行环境及目录权限",
}

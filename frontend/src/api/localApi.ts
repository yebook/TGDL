import type { LocalApiStatus, Settings } from './types'

export function validationBlockReason(status: LocalApiStatus | undefined, proxy: Settings['proxy_status'] | undefined): string {
  if (!status) return ''
  if (status.phase !== 'idle') {
    if (['failed', 'blocked', 'logout_uncertain'].includes(status.phase)) return (status.message || localApiPhases[status.phase]) + '；请在连接状态中处理后再校验。'
    return '正在应用 API 或代理配置：' + localApiPhases[status.phase] + '。操作完成后可校验。'
  }
  if (status.managed && status.desired_mode === 'local' && proxy && !['active', 'disabled'].includes(proxy.state)) return '正在等待本地 API 应用网络配置，请查看上方服务状态。'
  return ''
}

export const localBotConnectionHint = '本地 API 可响应请求；Telegram 连通性请以实际收发消息确认。若 Bot 无回应，请检查代理或网络。'
export function botConnectionLabel(state: string, mode: 'cloud' | 'local' | null): string {
  if (state === 'online' || state === 'connected') return mode === 'local' ? '本地 API 可用' : '已连接'
  return ({ connecting: '正在连接', unconfigured: '尚未配置', disconnected: '未连接', error: '连接异常' } as Record<string, string>)[state] || '状态未知'
}

export const modeLabel = (mode: 'cloud' | 'local' | null) => mode === 'local' ? '本地 API' : mode === 'cloud' ? '官方云端' : '尚未确认'
export const localApiStates: Record<LocalApiStatus['state'], string> = {
  unavailable: '管理组件未连接', waiting_credentials: '等待应用凭据', stopped: '已停止', starting: '正在启动',
  running: '进程运行中', stopping: '正在停止', failed: '进程异常',
}
export const localApiPhases: Record<LocalApiStatus['phase'], string> = {
  idle: '无进行中的操作', draining: '正在准备切换连接', logging_out: '正在退出云端', starting: '正在启动本地 API',
  verifying: '正在验证 Bot 连接', cooldown: '等待云端冷却', blocked: '操作受阻', logout_uncertain: '云端退出结果待确认', failed: '操作失败',
}
export function localApiTone(status: LocalApiStatus): 'success' | 'warning' | 'danger' | 'info' {
  if (['failed', 'logout_uncertain', 'blocked'].includes(status.phase) || status.state === 'failed') return 'danger'
  if (status.state === 'running' && status.phase === 'idle') return 'success'
  return status.state === 'unavailable' || status.state === 'waiting_credentials' ? 'warning' : 'info'
}
export function cooldownText(until: string | null, now: number): string {
  if (!until) return ''
  const seconds = Math.ceil((Date.parse(until) - now) / 1000)
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  return `云端最早可用：${new Date(until).toLocaleTimeString('zh-CN', { hour12: false })}（约 ${Math.ceil(seconds / 60)} 分钟后）`
}

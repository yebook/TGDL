import type { BotRequestSafety, LocalApiStatus } from './types'

export const safetyLabels: Record<BotRequestSafety['state'], string> = {
  ready: '请求正常', throttled: '请求限流，等待恢复', backoff: '连接异常，稍后重试', auth_paused: '认证异常，已暂停请求',
}

export function retryTime(until: string | null): string {
  if (!until || !Number.isFinite(Date.parse(until))) return ''
  return `最早重试时间：${new Date(until).toLocaleString('zh-CN', { hour12: false })}`
}

export function safetyRecoveryHint(status: BotRequestSafety, api?: LocalApiStatus, stale = false): string {
  if (stale) return '当前是历史状态，请先刷新；不能据此判断下一次尝试时间。'
  if (api && ['failed', 'blocked', 'logout_uncertain'].includes(api.phase)) return 'API 操作已停止，需要手动处理；请求等待结束不会自动重跑失败操作。可先进行网络连通测试。'
  if (status.state === 'auth_paused') return '认证已暂停，不会自动恢复；请核对已保存的 Bot 凭据，再按允许条件手动重试。'
  if (status.state === 'ready') return ''
  if (!status.retry_at || !Number.isFinite(Date.parse(status.retry_at))) return '尚无可确认的下次尝试时间，请检查请求保护状态与诊断日志；不能据此判断连接会自动恢复。'
  if (Date.parse(status.retry_at) <= Date.now()) return '等待期限已到，请刷新连接状态确认是否继续尝试；期限届满不代表连接已经恢复。'
  if (!api || api.phase !== 'idle' || !api.applied_mode) return '等待期限仅约束 Bot 请求，不表示 API 操作会在该时刻完成；请同时查看连接进度。'
  if (api.managed && api.applied_mode === 'local' && api.state !== 'running') return '本地 API 进程尚未确认运行，请检查服务状态；请求等待结束不表示服务会自动恢复。'
  return status.state === 'backoff' ? '当前运行连接将在退避结束后自动继续尝试，不会自动重启 API；不确定的下载仍保留恢复保护。' : '当前运行连接将在服务器等待结束后自动继续；不要通过重启或切换模式绕过冷却。'
}

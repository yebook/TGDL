import type { BotRequestSafety } from './types'

export const safetyLabels: Record<BotRequestSafety['state'], string> = {
  ready: '请求正常', throttled: '请求限流，等待恢复', backoff: '连接异常，稍后重试', auth_paused: '认证异常，已暂停请求',
}

export function retryTime(until: string | null): string {
  if (!until || !Number.isFinite(Date.parse(until))) return ''
  return `最早重试时间：${new Date(until).toLocaleString('zh-CN', { hour12: false })}`
}

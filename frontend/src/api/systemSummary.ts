import type { SystemStatus } from './types'
import { localApiPhases } from './localApi'

type StatusTone = 'neutral' | 'success' | 'warning' | 'danger'
export function telegramSummary(data: SystemStatus, stale: boolean) {
  const api = data.local_api
  const mode = (value: typeof api.applied_mode) => value === 'local' ? '本地' : value === 'cloud' ? '云端' : '待确认'
  const changing = api.phase !== 'idle' || api.applied_mode !== null && api.applied_mode !== api.desired_mode
  const apiType = mode(api.applied_mode) + (api.applied_mode !== api.desired_mode ? ` → ${mode(api.desired_mode)}` : '')
  const identity = data.bot.username ? `@${data.bot.username}` : data.bot.state === 'unconfigured' ? '未配置' : '待确认'
  let apiState = '待校验'
  let apiTone: StatusTone = 'neutral'
  let botState = '待确认'
  let botTone: StatusTone = 'neutral'
  const safety = data.bot.request_safety
  if (stale) return { apiType, apiState: '待更新', apiTone: 'warning' as StatusTone, identity, botState: '待更新', botTone: 'warning' as StatusTone, historical: true }

  if (changing) {
    const phases: Record<string, string> = { draining: '准备切换', logging_out: '退出云端中', starting: '启动中', verifying: '校验中', cooldown: '冷却等待', blocked: '操作受阻', logout_uncertain: '退出待确认', failed: '操作失败' }
    apiState = phases[api.phase] || '等待切换'
    if (api.phase === 'draining' && api.drain_reason === 'downloads') apiState = '等待下载结束'
    apiTone = ['blocked', 'logout_uncertain', 'failed'].includes(api.phase) ? 'danger' : 'warning'
  } else if (api.applied_mode === null || safety && safety.state !== 'ready') {
    apiState = '待确认'; apiTone = 'warning'
  } else if (api.applied_mode === 'local' && api.managed && api.state !== 'running') {
    apiState = ({ unavailable: '服务状态待确认', waiting_credentials: '等待凭据', stopped: '已停止', starting: '启动中', stopping: '停止中', failed: '进程异常' } as Record<string, string>)[api.state] || '待确认'
    apiTone = api.state === 'failed' ? 'danger' : 'warning'
  } else if (['online', 'connected'].includes(data.bot.state)) {
    apiState = '可响应'; apiTone = 'success'
  } else if (data.bot.state === 'error' || data.bot.state === 'disconnected') {
    apiState = '请求异常'; apiTone = 'danger'
  } else if (api.applied_mode === 'local' && api.managed && api.state === 'running') {
    apiState = '进程运行中'
  } else if (data.bot.state === 'connecting') {
    apiState = '连接中'
  }

  if (safety && safety.state !== 'ready') {
    botState = ({ auth_paused: '认证暂停', throttled: '限流等待', backoff: '连接退避' } as const)[safety.state]
    botTone = safety.state === 'auth_paused' ? 'danger' : 'warning'
  } else if (api.applied_mode === null) {
    botState = '待确认'; botTone = 'warning'
  } else if (changing) {
    botState = '接收暂停'; botTone = 'warning'
  } else if (api.applied_mode === 'local' && api.managed && api.state !== 'running') {
    botState = '未就绪'; botTone = 'warning'
  } else if (['online', 'connected'].includes(data.bot.state) && data.bot.username && api.applied_mode !== null) {
    botState = '已就绪'; botTone = 'success'
  } else if (data.bot.state === 'error' || data.bot.state === 'disconnected') {
    botState = '连接异常'; botTone = 'danger'
  } else if (data.bot.state === 'connecting') {
    botState = '连接中'
  } else if (data.bot.state === 'unconfigured') {
    botState = '未就绪'
  }
  return { apiType, apiState, apiTone, identity, botState, botTone, historical: false }
}

/** User-actionable exceptions remain visible regardless of diagnostic presentation. */
export function systemIssues(data: SystemStatus): string[] {
  const issues: string[] = []
  const safety = data.bot.request_safety
  if (safety && safety.state !== 'ready') issues.push(safety.message || 'Bot 请求已暂停，请查看连接设置。')
  if (['error', 'disconnected'].includes(data.bot.state)) issues.push(data.bot.message || 'Bot 未连接，请检查连接设置。')
  const api = data.local_api
  const used = api.desired_mode === 'local' || api.applied_mode === 'local'
  if (api.phase !== 'idle') issues.push(api.message || localApiPhases[api.phase])
  else if (used && api.managed && ['unavailable', 'failed', 'waiting_credentials'].includes(api.state)) issues.push(api.message || '本地 API 尚未就绪。')
  if (['failed', 'unavailable'].includes(data.proxy.state)) issues.push(data.proxy.message || '代理连接不可用。')
  for (const root of data.storage.roots) {
    if (!root.available) issues.push(`${root.label}：${root.message || '存储不可用，请检查挂载与权限。'}`)
  }
  if (used && data.storage.cache.free_bytes === null) issues.push('无法获取 Telegram 缓存空间，请检查存储。')
  if (data.recovery.blocked_jobs) issues.push(`${data.recovery.blocked_jobs} 个任务等待恢复处理。${data.recovery.message}`)
  if (data.diagnostic_log?.state === 'error') issues.push(data.diagnostic_log.message || '诊断日志记录异常。')
  return [...new Set(issues)]
}

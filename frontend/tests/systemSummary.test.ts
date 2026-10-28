import { describe, expect, it } from 'vitest'
import { connectionSummary, systemIssues, telegramSummary } from '../src/api/systemSummary'
import { localApiPhaseLabel } from '../src/api/localApi'
import type { SystemStatus } from '../src/api/types'

function status(): SystemStatus {
  return {
    bot: { state: 'online', username: 'fixture_bot', message: '', request_safety: { state: 'ready', retry_at: null, message: '', can_retry: false } },
    local_api: { managed: true, state: 'unavailable', desired_mode: 'cloud', applied_mode: 'cloud', operation_id: null, phase: 'idle', drain_reason: null, message: '', generation: null, last_heartbeat_at: null, cloud_available_at: null, can_restart: false, can_retry: false },
    storage: { selected_root_id: 'second', roots: [{ id: 'first', label: '媒体', available: true, free_bytes: 12, total_bytes: 40, message: '' }, { id: 'second', label: '备份盘', available: true, free_bytes: 20, total_bytes: 30, message: '' }], cache: { free_bytes: null, total_bytes: null } },
    proxy: { state: 'disabled', desired_revision: 1, applied_revision: 1, message: '' }, queue: { active: 0, pending: 0, concurrency: 3 }, version: '0.1.0', recovery: { blocked_jobs: 0, message: '' },
  }
}
describe('persistent user status', () => {
  it('uses the same saved-connection presentation for panel and topbar through recovery', () => {
    const data = status()
    for (const phase of ['draining', 'starting', 'verifying', 'failed', 'logout_uncertain', 'idle'] as const) {
      data.local_api.phase = phase
      expect(connectionSummary(data.local_api, data.bot)).toEqual(telegramSummary(data, false))
    }
    data.bot.state = 'error'
    expect(connectionSummary(data.local_api, data.bot).botState).toBe('连接异常')
    data.bot.state = 'online'
    expect(connectionSummary(data.local_api, data.bot)).toMatchObject({ identity: '@fixture_bot', botState: '已就绪' })
    expect(connectionSummary(data.local_api, data.bot, true).botState).toBe('待更新')
  })
  it('describes cloud-target start as stopping the local process, not starting it', () => {
    const data = status()
    data.local_api.phase = 'starting'
    expect(localApiPhaseLabel(data.local_api)).toBe('正在停止本地 API')
    expect(telegramSummary(data, false).apiState).toBe('停止本地中')
    data.local_api.desired_mode = 'local'
    expect(localApiPhaseLabel(data.local_api)).toBe('正在启动本地 API')
    expect(telegramSummary(data, false).apiState).toBe('启动本地中')
  })
  it('shows an operation failure without duplicating a stale transport alarm', () => {
    const data = status()
    data.local_api.phase = 'failed'
    data.local_api.message = '切换失败，请重试'
    data.bot.state = 'error'; data.bot.message = '旧连接异常'
    expect(systemIssues(data)).toEqual(['切换失败，请重试'])
    expect(telegramSummary(data, false)).toMatchObject({ apiState: '操作失败', botState: '接收暂停' })
  })
  it('separates known cloud API response from identified Bot readiness', () => {
    expect(telegramSummary(status(), false)).toMatchObject({ apiType: '云端', apiState: '可响应', apiTone: 'success', identity: '@fixture_bot', botState: '已就绪' })
  })
  it('keeps local API response distinct from process-only readiness and upstream delivery', () => {
    const data = status()
    data.local_api.desired_mode = 'local'; data.local_api.applied_mode = 'local'; data.local_api.state = 'running'
    expect(telegramSummary(data, false)).toMatchObject({ apiType: '本地', apiState: '可响应', botState: '已就绪' })
    data.bot = { state: 'unconfigured', username: '', message: '' }
    expect(telegramSummary(data, false)).toMatchObject({ apiState: '进程运行中', identity: '未配置', botState: '未就绪', apiTone: 'neutral' })
    data.local_api.state = 'unavailable'
    expect(telegramSummary(data, false)).toMatchObject({ apiState: '服务状态待确认', botState: '未就绪' })
  })
  it('prioritizes transitions over old online Bot state and shows actual and target modes', () => {
    const data = status()
    data.local_api.desired_mode = 'local'; data.local_api.phase = 'draining'
    expect(telegramSummary(data, false)).toMatchObject({ apiType: '云端 → 本地', apiState: '准备切换', apiTone: 'warning', botState: '接收暂停', botTone: 'warning' })
    expect(systemIssues(data)).not.toContain('等待当前下载完成后切换连接')
    data.local_api.drain_reason = 'connection'
    data.queue.active = 3
    expect(telegramSummary(data, false).apiState).toBe('准备切换')
    data.local_api.drain_reason = 'downloads'
    expect(telegramSummary(data, false).apiState).toBe('等待下载结束')
    data.local_api.message = '等待当前下载完成后切换连接'
    expect(systemIssues(data)).not.toContain('等待当前下载完成后切换连接')
    data.local_api.phase = 'logout_uncertain'
    expect(telegramSummary(data, false)).toMatchObject({ apiState: '退出待确认', apiTone: 'danger' })
  })
  it('shows routine switching as progress while preserving recovery alarms', () => {
    const data = status()
    data.local_api.desired_mode = 'local'
    data.storage.cache.free_bytes = 100
    for (const phase of ['draining', 'logging_out', 'starting', 'verifying', 'cooldown'] as const) {
      data.local_api.phase = phase
      data.local_api.message = '正常切换进度'
      expect(systemIssues(data)).toEqual([])
      expect(telegramSummary(data, false).apiTone).toBe('warning')
    }
    for (const phase of ['failed', 'blocked', 'logout_uncertain'] as const) {
      data.local_api.phase = phase
      data.local_api.message = '需要处理的操作'
      expect(systemIssues(data)).toEqual(['需要处理的操作'])
    }
  })
  it('shows protection before readiness without diagnosing the API as down', () => {
    const data = status()
    data.bot.request_safety = { state: 'auth_paused', retry_at: null, message: '', can_retry: true }
    expect(telegramSummary(data, false)).toMatchObject({ apiState: '待确认', apiTone: 'warning', botState: '认证暂停', botTone: 'danger' })
    data.bot.request_safety.state = 'throttled'
    expect(telegramSummary(data, false).botState).toBe('限流等待')
  })
  it('does not invent current mode or Bot identity', () => {
    const data = status()
    data.bot.username = ''
    expect(telegramSummary(data, false)).toMatchObject({ apiType: '云端', apiState: '可响应', identity: '待确认', botState: '待确认', botTone: 'neutral' })
    data.local_api.applied_mode = null; data.bot.username = ''
    expect(telegramSummary(data, false)).toMatchObject({ apiType: '待确认 → 云端', identity: '待确认', apiTone: 'warning', botTone: 'warning' })
  })
  it('marks stale API and Bot observations as historical, never green', () => {
    expect(telegramSummary(status(), true)).toMatchObject({ apiState: '待更新', apiTone: 'warning', botState: '待更新', botTone: 'warning', historical: true })
  })
  it('does not alarm about an unused cloud-mode local service or invent a storage threshold', () => {
    expect(systemIssues(status())).toEqual([])
  })
  it('does not treat an externally managed local API as a failed managed process', () => {
    const data = status()
    data.local_api.managed = false
    data.local_api.desired_mode = 'local'
    data.local_api.applied_mode = 'local'
    data.storage.cache.free_bytes = 100
    expect(systemIssues(data)).toEqual([])
    expect(telegramSummary(data, false)).toMatchObject({ apiType: '本地', apiState: '可响应', apiTone: 'success' })
  })
  it('keeps request protection, transitions, every storage error and recovery visible', () => {
    const data = status()
    data.bot.request_safety = { state: 'auth_paused', message: '认证异常，已暂停请求', retry_at: null, can_retry: true }
    data.local_api.desired_mode = 'local'; data.local_api.phase = 'logout_uncertain'
    data.storage.roots[0]!.available = false
    data.storage.roots[0]!.message = '请检查挂载'
    data.recovery = { blocked_jobs: 2, message: '需要处理' }
    const issues = systemIssues(data)
    expect(issues).toContain('认证异常，已暂停请求')
    expect(issues).toContain('云端退出结果待确认')
    expect(issues).toContain('媒体：请检查挂载')
    expect(issues).toContain('无法获取 Telegram 缓存空间，请检查存储。')
    expect(issues).toContain('2 个任务等待恢复处理。需要处理')
  })
})

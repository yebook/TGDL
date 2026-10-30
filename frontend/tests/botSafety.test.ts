import { describe, expect, it } from 'vitest'
import { retryTime, safetyLabels, safetyRecoveryHint } from '../src/api/botSafety'
import type { BotRequestSafety, LocalApiStatus } from '../src/api/types'

describe('Bot request safety labels', () => {
  it('distinguishes authentication pause from ordinary waits', () => {
    expect(safetyLabels.auth_paused).toContain('认证异常')
    expect(safetyLabels.throttled).toContain('限流')
    expect(safetyLabels.backoff).toContain('稍后重试')
  })
  it('only renders valid persisted retry deadlines', () => {
    expect(retryTime(null)).toBe('')
    expect(retryTime('bad')).toBe('')
    expect(retryTime('2030-01-01T12:30:00Z')).toMatch(/^最早重试时间：.+/)
  })
  it('never promises automatic recovery of terminal operations or stale status', () => {
    const safety: BotRequestSafety = { state: 'backoff', retry_at: '2030-01-01T12:30:00Z', message: '', can_retry: false }
    const api: LocalApiStatus = { managed: true, state: 'running', desired_mode: 'local', applied_mode: 'local', operation_id: null, phase: 'idle', drain_reason: null, message: '', generation: null, last_heartbeat_at: null, cloud_available_at: null, can_restart: true, can_retry: false }
    expect(safetyRecoveryHint(safety, api)).toContain('自动继续尝试')
    api.phase = 'failed'
    expect(safetyRecoveryHint(safety, api)).toContain('需要手动处理')
    expect(safetyRecoveryHint(safety, api, true)).toContain('历史状态')
    api.phase = 'idle'; safety.state = 'auth_paused'
    expect(safetyRecoveryHint(safety, api)).toContain('不会自动恢复')
  })
  it('does not promise automatic recovery without a known wait deadline or running local API', () => {
    const safety: BotRequestSafety = { state: 'backoff', retry_at: null, message: '请求保护状态不可用', can_retry: false }
    const api: LocalApiStatus = { managed: true, state: 'running', desired_mode: 'local', applied_mode: 'local', operation_id: null, phase: 'idle', drain_reason: null, message: '', generation: null, last_heartbeat_at: null, cloud_available_at: null, can_restart: true, can_retry: false }
    expect(safetyRecoveryHint(safety, api)).toContain('尚无可确认的下次尝试时间')
    safety.retry_at = 'invalid'
    expect(safetyRecoveryHint(safety, api)).toContain('不能据此判断连接会自动恢复')
    safety.retry_at = '2000-01-01T12:30:00Z'
    expect(safetyRecoveryHint(safety, api)).toContain('等待期限已到，请刷新连接状态')
    safety.retry_at = '2030-01-01T12:30:00Z'
    api.state = 'unavailable'
    expect(safetyRecoveryHint(safety, api)).toContain('本地 API 进程尚未确认运行')
    api.state = 'failed'
    expect(safetyRecoveryHint(safety, api)).toContain('不表示服务会自动恢复')
    api.applied_mode = 'cloud'
    expect(safetyRecoveryHint(safety, api)).toContain('自动继续尝试')
  })
})

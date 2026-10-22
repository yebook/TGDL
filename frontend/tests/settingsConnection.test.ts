import { describe, expect, it } from 'vitest'
import { connectionEditBlockReason } from '../src/api/settingsConnection'
import type { LocalApiStatus } from '../src/api/types'

const status: LocalApiStatus = { managed: true, state: 'running', desired_mode: 'local', applied_mode: 'local', operation_id: null, phase: 'idle', drain_reason: null, message: '', generation: null, last_heartbeat_at: null, cloud_available_at: null, can_restart: true, can_retry: false }

describe('connection editing barrier', () => {
  it('locks every active operation, including cloud reconnection without a local supervisor', () => {
    for (const phase of ['draining', 'logging_out', 'starting', 'verifying', 'cooldown'] as const) {
      expect(connectionEditBlockReason({ ...status, phase }, false)).toContain('其他设置可照常保存')
      expect(connectionEditBlockReason({ ...status, phase, managed: false, desired_mode: 'cloud' }, false)).toContain('正在应用连接配置')
    }
  })
  it('keeps missing, stale and not-yet-acknowledged status from reopening the connection form', () => {
    expect(connectionEditBlockReason(undefined, false)).toContain('正在确认')
    expect(connectionEditBlockReason(status, false, true)).toContain('正在确认')
    expect(connectionEditBlockReason(status, true)).toContain('状态更新失败')
    expect(connectionEditBlockReason(status, false)).toBe('')
  })
  it('allows correcting failed or blocked settings while requiring explicit uncertain-logout recovery', () => {
    for (const phase of ['failed', 'blocked'] as const) expect(connectionEditBlockReason({ ...status, phase }, false)).toBe('')
    expect(connectionEditBlockReason({ ...status, phase: 'logout_uncertain' }, false)).toContain('明确确认后继续')
  })
})

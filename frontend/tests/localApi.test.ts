import { describe, expect, it } from 'vitest'
import { botConnectionLabel, cooldownText, localApiPhases, modeLabel, validationBlockReason } from '../src/api/localApi'
import type { LocalApiStatus } from '../src/api/types'

describe('managed API diagnostics', () => {
  const status: LocalApiStatus = { managed: true, state: 'running', desired_mode: 'local', applied_mode: 'local', operation_id: null, phase: 'idle', message: '', generation: 'test', last_heartbeat_at: null, cloud_available_at: null, can_restart: true, can_retry: false }
  it('blocks validation immediately while saved network settings await application', () => {
    expect(validationBlockReason(status, { state: 'pending', message: '', desired_revision: 9, applied_revision: null })).toContain('应用网络配置')
    expect(validationBlockReason(status, { state: 'disabled', message: '', desired_revision: 9, applied_revision: 9 })).toBe('')
  })
  it('distinguishes ongoing configuration application from a failed operation requiring action', () => {
    expect(validationBlockReason({ ...status, phase: 'starting' }, undefined)).toContain('操作完成后可校验')
    expect(validationBlockReason({ ...status, phase: 'failed', message: 'Bot 连接验证失败' }, undefined)).toContain('服务状态中处理后再校验')
    expect(validationBlockReason({ ...status, phase: 'logout_uncertain' }, undefined)).toContain('退出结果待确认')
    expect(validationBlockReason({ ...status, managed: false, phase: 'starting' }, undefined)).toBe('')
    expect(validationBlockReason({ ...status, desired_mode: 'cloud' }, { state: 'pending', message: '', desired_revision: 9, applied_revision: null })).toBe('')
  })
  it('distinguishes a responsive local API from an upstream Bot connection', () => {
    expect(botConnectionLabel('online', 'local')).toBe('本地 API 可用')
    expect(botConnectionLabel('connected', 'local')).toBe('本地 API 可用')
    expect(botConnectionLabel('online', 'cloud')).toBe('已连接')
    expect(botConnectionLabel('error', 'local')).toBe('连接异常')
  })
  it('explains the local API startup phase before a mode switch', () => {
    expect(localApiPhases.starting).toBe('正在启动本地 API')
  })
  it('does not present an unknown applied mode as a successful switch', () => {
    expect(modeLabel(null)).toBe('尚未确认')
  })
  it('keeps the last partial minute visible until the cloud deadline', () => {
    const deadline = '2026-10-20T03:10:00Z'
    const until = Date.parse(deadline)
    expect(cooldownText(deadline, until - 600_000)).toContain('10 分钟后')
    expect(cooldownText(deadline, until - 60_001)).toContain('2 分钟后')
    expect(cooldownText(deadline, until - 1)).toContain('1 分钟后')
    expect(cooldownText(deadline, until)).toBe('')
    expect(cooldownText(deadline, until + 1)).toBe('')
  })
  it('does not invent a deadline from missing or malformed data', () => {
    expect(cooldownText(null, Date.now())).toBe('')
    expect(cooldownText('invalid', Date.now())).toBe('')
  })
})

import { describe, expect, it } from 'vitest'
import { cooldownText, localApiPhases, modeLabel } from '../src/api/localApi'

describe('managed API diagnostics', () => {
  it('explains the network preparation phase before a mode switch', () => {
    expect(localApiPhases.waiting_network).toBe('等待网络配置就绪')
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

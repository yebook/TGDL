import { describe, expect, it } from 'vitest'
import { retryTime, safetyLabels } from '../src/api/botSafety'

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
})

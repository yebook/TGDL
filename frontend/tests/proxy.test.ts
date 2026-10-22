import { describe, expect, it } from 'vitest'
import { proxySummary } from '../src/api/proxy'

describe('proxy summary', () => {
  it.each([
    ['disabled', '直连'], ['pending', '代理等待应用'], ['applying', '代理正在应用'],
    ['active', '代理已应用'], ['failed', '代理应用失败'], ['unavailable', '代理网络组件未连接'],
    ['unknown', '代理状态待确认'],
  ])('presents %s without claiming Telegram connectivity', (state, label) => {
    expect(proxySummary(state, false)).toBe(label)
    expect(proxySummary(state, true)).toBe(`上次状态：${label}`)
  })
})

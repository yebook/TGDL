import { describe, expect, it } from 'vitest'
import { attemptResult, bytes, duration, mediaTypeLabel, parseUserIds, percent, taskIssueTone } from '../src/api/format'

describe('file type labels', () => {
  it.each([
    ['video', '视频'], ['image', '图片'], ['audio', '音频'], ['voice', '语音'],
    ['animation', '动画'], ['video_note', '视频留言'], ['document', '文件'],
    ['future_type', '文件'], ['', '文件'], ['constructor', '文件'],
  ])('labels %s as %s without treating other files as images', (type, label) => {
    expect(mediaTypeLabel(type)).toBe(label)
  })
})

describe('request waits and real task failures', () => {
  it('distinguishes ordinary pacing, protection pauses and failures', () => {
    expect(taskIssueTone('BOT_REQUEST_WAITING')).toBe('info')
    expect(attemptResult('BOT_REQUEST_WAITING', '2026-10-31')).toBe('等待请求间隔，自动继续')
    expect(taskIssueTone('BOT_REQUEST_PAUSED')).toBe('warning')
    expect(attemptResult('BOT_REQUEST_PAUSED', '2026-10-31')).toBe('请求暂缓，等待恢复')
    expect(taskIssueTone('NETWORK_ERROR')).toBe('error')
    expect(attemptResult('NETWORK_ERROR', '2026-10-31')).toBe('NETWORK_ERROR')
    expect(attemptResult(null, null)).toBe('执行中')
    expect(attemptResult(null, '2026-10-31')).toBe('已结束')
  })
})

describe('human-readable duration', () => {
  it.each([
    [0, '0秒'], [0.9, '0秒'], [45, '45秒'], [59.9, '59秒'],
    [60, '1分'], [135, '2分15秒'], [3599, '59分59秒'],
    [3600, '1小时'], [4110, '1小时8分30秒'], [86400, '24小时'],
  ])('formats %s seconds as %s', (value, expected) => {
    expect(duration(value)).toBe(expected)
  })
  it.each([null, undefined, NaN, Infinity, -Infinity, -1, -0.1])('does not invent a duration for %s', value => {
    expect(duration(value)).toBe('—')
  })
})

describe('accurate unknown progress and user input', () => {
  it('does not manufacture progress when Telegram has not reported a size', () => {
    expect(bytes(null)).toBe('未知')
    expect(percent(1024, null)).toBeUndefined()
    expect(percent(0, 0)).toBeUndefined()
    expect(percent(256, 1024)).toBe(25)
    expect(percent(2000, 1024)).toBe(100)
  })
  it('accepts Telegram IDs separated by lines and commas without duplicate entries', () => {
    expect(parseUserIds('1234567890123\n42，42, 7')).toEqual([1234567890123, 42, 7])
    expect(parseUserIds('')).toEqual([])
  })
  it.each(['-3', '0', '123abc', '1.5', '9007199254740993'])('rejects invalid or lossy whitelist ID %s', value => {
    expect(() => parseUserIds(value)).toThrow('白名单')
  })
})

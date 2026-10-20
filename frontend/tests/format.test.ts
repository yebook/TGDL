import { describe, expect, it } from 'vitest'
import { bytes, parseUserIds, percent } from '../src/api/format'

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

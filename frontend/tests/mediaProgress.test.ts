import { describe, expect, it } from 'vitest'
import { stateLabel } from '../src/api/format'
import { mediaProgress, mediaState } from '../src/api/mediaProgress'

describe('measured transfer progress and honest unknown download progress', () => {
  const transfer = { status: 'fetching', phase: 'fetching', acquisition_mode: 'local' as const, completed_bytes: 0, total_bytes: 1024 }
  it('labels downloads consistently without treating a known size as measured remote progress', () => {
    expect(stateLabel('fetching')).toBe('下载中')
    expect(stateLabel('saving')).toBe('处理中')
    expect(mediaProgress(transfer)).toMatchObject({ label: '下载中', measured: false, percentage: undefined })
    // A stale count from a previous attempt must not produce a false percentage.
    expect(mediaProgress({ ...transfer, completed_bytes: 512 }).percentage).toBeUndefined()
  })
  it('distinguishes local cache saving from actual cloud HTTP downloading', () => {
    const saving = { ...transfer, status: 'saving', phase: 'saving', completed_bytes: 512 }
    expect(mediaProgress(saving)).toMatchObject({ label: '保存中', verb: '已保存', measured: true, percentage: 50 })
    expect(stateLabel(mediaState(saving))).toBe('保存中')
    const cloud = { ...saving, acquisition_mode: 'cloud' as const }
    expect(mediaProgress(cloud)).toMatchObject({ label: '下载中', verb: '已下载', measured: true, percentage: 50 })
    expect(mediaState(cloud)).toBe('downloading')
  })
  it('shows measured bytes without manufacturing a percentage for unknown totals', () => {
    const cloud = { ...transfer, status: 'saving', phase: 'saving', acquisition_mode: 'cloud' as const, completed_bytes: 512, total_bytes: null }
    expect(mediaProgress(cloud)).toMatchObject({ verb: '已下载', measured: true, percentage: undefined })
  })
  it('preserves cancellation and does not treat missing ownership as cloud downloading', () => {
    expect(mediaState({ ...transfer, status: 'cancelling' })).toBe('cancelling')
    expect(mediaProgress({ ...transfer, status: 'cancelling' }).label).toBe('取消中')
    expect(mediaProgress({ ...transfer, status: 'cancelling' }).percentage).toBeUndefined()
    expect(mediaProgress({ ...transfer, status: 'saving', phase: 'saving', acquisition_mode: null }).verb).toBe('已保存')
  })
})

import { describe, expect, it } from 'vitest'
import { stateLabel } from '../src/api/format'
import { mediaProgress, mediaState } from '../src/api/mediaProgress'
import type { DownloadProgress } from '../src/api/types'

describe('measured transfer progress and honest unknown download progress', () => {
  const transfer = { status: 'fetching', phase: 'fetching', acquisition_mode: 'local' as const, completed_bytes: 0, total_bytes: 1024, speed_bps: null, download_progress: null as DownloadProgress | null }
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
  const snapshot: DownloadProgress = { downloaded_bytes: 256, total_bytes: 1024, total_kind: 'exact', speed_bps: 128, active: true, completed: false }
  it('uses fresh local acquisition counters rather than persisted saving counters', () => {
    expect(mediaProgress({ ...transfer, completed_bytes: 999, speed_bps: 99, download_progress: snapshot })).toMatchObject({
      label: '下载中', verb: '已下载', measured: true, percentage: 25, completedBytes: 256, totalBytes: 1024, speedBps: 128, estimated: false,
    })
    expect(mediaProgress({ ...transfer, download_progress: { ...snapshot, downloaded_bytes: 0, speed_bps: 0 } })).toMatchObject({
      measured: true, percentage: 0, completedBytes: 0, speedBps: 0,
    })
    expect(mediaProgress(transfer)).toMatchObject({ measured: false, completedBytes: null, percentage: undefined })
  })
  it('marks estimated totals and never converts them into exact percentages', () => {
    expect(mediaProgress({ ...transfer, download_progress: { ...snapshot, total_kind: 'estimated', downloaded_bytes: 2048 } })).toMatchObject({
      measured: true, percentage: undefined, completedBytes: 2048, totalBytes: 1024, estimated: true,
    })
  })
  it('shows real bytes and optional speed with unknown totals', () => {
    expect(mediaProgress({ ...transfer, download_progress: { ...snapshot, total_kind: 'unknown', total_bytes: null, speed_bps: null } })).toMatchObject({
      measured: true, percentage: undefined, totalBytes: null, completedBytes: 256, speedBps: null, estimated: false,
    })
    expect(mediaProgress({ ...transfer, download_progress: { ...snapshot, total_bytes: 0 } }).percentage).toBeUndefined()
  })
  it('keeps cache completion separate from final saving and file success', () => {
    const cached = { ...snapshot, downloaded_bytes: 1024, completed: true, active: false }
    expect(mediaProgress({ ...transfer, download_progress: cached })).toMatchObject({
      label: '下载中', percentage: 100, explanation: '媒体已下载，等待开始保存',
    })
    expect(mediaState({ ...transfer, download_progress: cached })).toBe('fetching')
    expect(mediaProgress({ ...transfer, status: 'saving', phase: 'saving', download_progress: cached, completed_bytes: 0 })).toMatchObject({
      label: '保存中', verb: '已保存', percentage: 0, completedBytes: 0,
    })
    expect(mediaProgress({ ...transfer, download_progress: { ...snapshot, active: false } }).explanation).toBe('等待下载继续，任务尚未完成')
  })
  it('ignores late native snapshots for cancellation, cloud and unknown mode', () => {
    expect(mediaProgress({ ...transfer, status: 'cancelling', download_progress: snapshot })).toMatchObject({
      label: '取消中', measured: false, percentage: undefined, completedBytes: null,
    })
    for (const acquisition_mode of ['cloud', null] as const) {
      expect(mediaProgress({ ...transfer, acquisition_mode, download_progress: snapshot })).toMatchObject({ measured: false, percentage: undefined })
    }
  })
})

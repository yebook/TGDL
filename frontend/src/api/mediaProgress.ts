import type { MediaItem } from './types'
import { percent, stateLabel } from './format'

type Transfer = Pick<MediaItem, 'status' | 'phase' | 'acquisition_mode' | 'completed_bytes' | 'total_bytes' | 'speed_bps' | 'download_progress'>

function validTotal(value: number | null): value is number {
  return value != null && Number.isSafeInteger(value) && value > 0
}

// Cloud streams arrive while saving; local saving copies an already downloaded cache file.
export function mediaState(item: Transfer): string {
  if (item.status !== 'saving') return item.status
  return item.acquisition_mode === 'cloud' ? 'downloading' : 'copying'
}

export function mediaProgress(item: Transfer) {
  const streaming = item.phase === 'saving'
  const download = !streaming || item.acquisition_mode === 'cloud'
  // The backend only exposes fresh, attempt-bound native observations. Do not
  // reuse either these counters during saving or persisted copy counters while fetching.
  const native = item.acquisition_mode === 'local' && item.phase === 'fetching'
    && item.status === 'fetching' ? item.download_progress : null
  // TDLib may only have expected_size while Telegram's message metadata already
  // supplies the exact file size. Keep the native byte counter, but use that
  // known size instead of treating the whole acquisition as indeterminate.
  const nativeExact = native?.total_kind === 'exact' && validTotal(native.total_bytes)
  const metadataTotal = native && !nativeExact && validTotal(item.total_bytes)
    && native.downloaded_bytes <= item.total_bytes
  const total = native ? nativeExact ? native.total_bytes : metadataTotal ? item.total_bytes
    : native.total_kind === 'estimated' && validTotal(native.total_bytes) ? native.total_bytes : null : streaming ? item.total_bytes : null
  const exactTotal = nativeExact || metadataTotal
  // A conflicting sample must remain visible without clamping an overrun into
  // a misleading 100%. A later native exact size can resolve stale metadata.
  const nativePercentage = native && exactTotal && total != null
    && Number.isSafeInteger(native.downloaded_bytes) && native.downloaded_bytes >= 0 && native.downloaded_bytes <= total
    ? percent(native.downloaded_bytes, total) : undefined
  const measured = streaming || native != null
  const explanation = native?.completed
    ? '文件已下载，等待开始保存'
    : native && !native.active ? '等待下载继续，任务尚未完成' : ''
  return {
    label: item.status === 'cancelling' ? stateLabel('cancelling') : streaming ? (download ? '下载中' : '保存中') : stateLabel(item.phase || item.status),
    percentage: native ? nativePercentage : streaming ? percent(item.completed_bytes, total) : undefined,
    measured,
    completedBytes: native ? native.downloaded_bytes : streaming ? item.completed_bytes : null,
    totalBytes: total,
    estimated: native?.total_kind === 'estimated' && !metadataTotal,
    speedBps: native ? native.speed_bps : streaming ? item.speed_bps : null,
    verb: download ? '已下载' : '已保存',
    explanation: item.status === 'cancelling'
      ? streaming ? (download ? '正在停止下载并清理临时文件' : '正在停止保存并清理临时文件') : '正在停止下载并等待安全确认'
      : measured ? explanation : '暂无法获取下载进度，任务仍在执行',
  }
}

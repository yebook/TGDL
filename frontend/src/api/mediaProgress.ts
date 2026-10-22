import type { MediaItem } from './types'
import { percent, stateLabel } from './format'

type Transfer = Pick<MediaItem, 'status' | 'phase' | 'acquisition_mode' | 'completed_bytes' | 'total_bytes' | 'speed_bps' | 'download_progress'>

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
  const total = native ? (native.total_kind === 'unknown' ? null : native.total_bytes) : streaming ? item.total_bytes : null
  const measured = streaming || native != null
  const explanation = native?.completed
    ? '媒体已下载，等待开始保存'
    : native && !native.active ? '等待下载继续，任务尚未完成' : ''
  return {
    label: item.status === 'cancelling' ? stateLabel('cancelling') : streaming ? (download ? '下载中' : '保存中') : stateLabel(item.phase || item.status),
    percentage: native ? native.total_kind === 'exact' ? percent(native.downloaded_bytes, total) : undefined
      : streaming ? percent(item.completed_bytes, total) : undefined,
    measured,
    completedBytes: native ? native.downloaded_bytes : streaming ? item.completed_bytes : null,
    totalBytes: total,
    estimated: native?.total_kind === 'estimated',
    speedBps: native ? native.speed_bps : streaming ? item.speed_bps : null,
    verb: download ? '已下载' : '已保存',
    explanation: measured ? explanation : item.status === 'cancelling' ? '正在等待当前下载安全结束'
      : '暂无法获取下载进度，任务仍在执行',
  }
}

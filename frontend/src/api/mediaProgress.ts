import type { MediaItem } from './types'
import { percent, stateLabel } from './format'

type Transfer = Pick<MediaItem, 'status' | 'phase' | 'acquisition_mode' | 'completed_bytes' | 'total_bytes'>

// Cloud streams arrive while saving; local saving copies an already downloaded cache file.
export function mediaState(item: Transfer): string {
  if (item.status !== 'saving') return item.status
  return item.acquisition_mode === 'cloud' ? 'downloading' : 'copying'
}

export function mediaProgress(item: Transfer) {
  const streaming = item.phase === 'saving'
  const download = !streaming || item.acquisition_mode === 'cloud'
  return {
    label: item.status === 'cancelling' ? stateLabel('cancelling') : streaming ? (download ? '下载中' : '保存中') : stateLabel(item.phase || item.status),
    percentage: streaming ? percent(item.completed_bytes, item.total_bytes) : undefined,
    measured: streaming,
    verb: download ? '已下载' : '已保存',
    explanation: streaming ? '' : 'Telegram 未提供此阶段的下载进度',
  }
}

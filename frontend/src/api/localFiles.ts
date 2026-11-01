import type { LocalFileCounts, LocalFileState, TaskDeleteResponse } from './types'

// Download outcomes are historical. These labels describe only the current
// original saved path, without guessing why a file is absent or changing state.
export function localFileLabel(state: LocalFileState): string {
  return ({ present: '本地文件存在', missing: '本地文件不存在', changed: '本地文件已变化',
    unavailable: '暂时无法检查本地文件', not_saved: '' })[state] ?? ''
}

export function localFilesSummary(counts?: LocalFileCounts): string {
  if (!counts) return ''
  const parts: string[] = []
  const saved = counts.present + counts.missing + counts.changed + counts.unavailable
  if (counts.missing) parts.push(counts.missing === saved ? '本地文件不存在' : `${counts.missing} 个本地文件不存在`)
  if (counts.changed) parts.push(`${counts.changed} 个本地文件已变化`)
  if (counts.unavailable) parts.push('暂时无法检查本地文件')
  return parts.join(' · ')
}

export function taskDeletionFeedback(result: TaskDeleteResponse, deleteFiles: boolean): { message: string; warning: boolean } {
  const parts = [`已删除 ${result.deleted_ids.length} 条任务记录`]
  if (!deleteFiles) return { message: `${parts[0]}，文件已保留`, warning: false }
  const files = result.files
  if (files.deleted) parts.push(`已删除 ${files.deleted} 个文件`)
  if (files.missing) parts.push(`${files.missing} 个文件已不存在`)
  if (files.retained_shared) parts.push(`${files.retained_shared} 个文件仍被其他任务引用，已保留`)
  if (files.retained_unverified) parts.push(`${files.retained_unverified} 个文件未通过安全检查，已保留`)
  if (files.failed) parts.push(`${files.failed} 个文件删除失败`)
  if (files.uncertain) parts.push(`${files.uncertain} 个文件的删除结果未确认，请检查保存目录`)
  if (!Object.values(files).some(count => count > 0)) parts.push('没有可删除的已下载文件')
  return { message: parts.join('；'), warning: files.retained_shared + files.retained_unverified + files.failed + files.uncertain > 0 }
}

import { describe, expect, it } from 'vitest'
import { localFileLabel, localFilesSummary, taskDeletionFeedback } from '../src/api/localFiles'
import type { LocalFileCounts, TaskDeleteResponse } from '../src/api/types'

const counts: LocalFileCounts = { present: 0, missing: 0, changed: 0, unavailable: 0, not_saved: 0 }
const result: TaskDeleteResponse = { deleted_ids: ['a', 'b'], operation_id: 'a'.repeat(32),
  files: { deleted: 0, missing: 0, retained_shared: 0, retained_unverified: 0, failed: 0, uncertain: 0 } }

describe('local availability independent of historic task result', () => {
  it('distinguishes missing from inaccessible/changed without asserting a deletion cause', () => {
    expect(localFileLabel('missing')).toBe('本地文件不存在')
    expect(localFileLabel('unavailable')).toBe('暂时无法检查本地文件')
    expect(localFileLabel('changed')).toBe('本地文件已变化')
    expect(localFileLabel('not_saved')).toBe('')
    expect(localFileLabel('present')).toBe('本地文件存在')
  })
  it('summarizes partial groups and omits pending/normal files', () => {
    expect(localFilesSummary()).toBe('')
    expect(localFilesSummary({ ...counts, not_saved: 2 })).toBe('')
    expect(localFilesSummary({ ...counts, present: 2 })).toBe('')
    expect(localFilesSummary({ ...counts, present: 1, missing: 1 })).toBe('1 个本地文件不存在')
    expect(localFilesSummary({ ...counts, missing: 2 })).toBe('本地文件不存在')
    expect(localFilesSummary({ ...counts, present: 1, unavailable: 1 })).toBe('暂时无法检查本地文件')
  })
})

describe('honest saved file deletion feedback', () => {
  it('preserves the default record-only guarantee', () => {
    expect(taskDeletionFeedback(result, false)).toEqual({ message: '已删除 2 条任务记录，文件已保留', warning: false })
  })
  it('states actual removals and already missing files', () => {
    expect(taskDeletionFeedback({ ...result, files: { ...result.files, deleted: 1, missing: 2 } }, true))
      .toEqual({ message: '已删除 2 条任务记录；已删除 1 个文件；2 个文件已不存在', warning: false })
  })
  it('warns for shared, unsafe, failed or uncertain files without claiming all succeeded', () => {
    const feedback = taskDeletionFeedback({ ...result, files: { deleted: 1, missing: 0,
      retained_shared: 2, retained_unverified: 3, failed: 4, uncertain: 5 } }, true)
    expect(feedback.warning).toBe(true)
    expect(feedback.message).toContain('2 个文件仍被其他任务引用，已保留')
    expect(feedback.message).toContain('3 个文件未通过安全检查，已保留')
    expect(feedback.message).toContain('4 个文件删除失败')
    expect(feedback.message).toContain('5 个文件的删除结果未确认')
  })
  it('does not imply unsaved jobs had files deleted', () => {
    expect(taskDeletionFeedback(result, true).message).toContain('没有可删除的已下载文件')
  })
})

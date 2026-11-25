import { describe, expect, it } from 'vitest'
import { detailNeedsFastPolling, detailSummaryKey } from '../src/api/taskDetailPolling'
import type { ItemCache, MediaItem, TaskDetail } from '../src/api/types'

const file: MediaItem = {
  id: 'file', job_id: 'job', media_type: 'video', original_name: null, size_bytes: 10, duration_seconds: null,
  status: 'succeeded', phase: null, completed_bytes: 10, total_bytes: 10, acquisition_mode: 'local',
  download_progress: null, speed_bps: null, elapsed_seconds: null, relative_path: 'media.mp4',
  error_code: null, error_message: null, linked_job_id: null, attempts: [], local_file_state: 'present',
}
const cache: ItemCache = {
  id: 'cache', state: 'retained', completeness: 'complete', path: null, size_bytes: 10, allocated_bytes: null,
  can_clean: false, can_save: false, can_resume_download: false, can_redownload: false,
  unavailable_reason: null, resume_unavailable_reason: null, recovery_guidance: null, operation: null,
}
const detail: TaskDetail = {
  id: 'task', title: 'Task', kind: 'single', status: 'succeeded', terminal: true, created_at: '', updated_at: '',
  item_count: 1, completed_count: 1, total_bytes: 10, completed_bytes: 10, effective_destination: null,
  notification_status: 'sent', can_cancel: false, can_retry: false, can_delete: true, expires_at: null,
  local_files: { present: 1, missing: 0, changed: 0, unavailable: 0, not_saved: 0 }, items: [file],
}

describe('task detail cadence', () => {
  it('slows only settled responses, not active work or pending notifications or cancellation cleanup', () => {
    expect(detailNeedsFastPolling(detail)).toBe(false)
    expect(detailNeedsFastPolling({ ...detail, terminal: false })).toBe(true)
    expect(detailNeedsFastPolling({ ...detail, notification_status: 'pending' })).toBe(true)
    for (const state of ['pending', 'running', 'partial', 'complete'] as const) {
      const cancel_cleanup = { state, message: '', can_retry: false, entries: [] }
      expect(detailNeedsFastPolling({ ...detail, cancel_cleanup })).toBe(state === 'pending' || state === 'running')
    }
  })
  it('keeps polling unresolved cache operations even when the task is terminal', () => {
    for (const state of ['queued', 'running', 'uncertain', 'succeeded', 'failed'] as const) {
      const action: ItemCache = { ...cache, operation: { id: 'operation', kind: 'clean', state } }
      const task = { ...detail, items: [{ ...file, caches: [cache, action] }] }
      expect(detailNeedsFastPolling(task)).toBe(state === 'queued' || state === 'running' || state === 'uncertain')
    }
  })
  it('distinguishes external file observations from unchanged list polls', () => {
    expect(detailSummaryKey({ ...detail, local_files: { ...detail.local_files } })).toBe(detailSummaryKey(detail))
    expect(detailSummaryKey({ ...detail, local_files: { ...detail.local_files, present: 0, missing: 1 } })).not.toBe(detailSummaryKey(detail))
  })
})

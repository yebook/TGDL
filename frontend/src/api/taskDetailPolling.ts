import type { Task, TaskDetail } from './types'

export function detailNeedsFastPolling(task: TaskDetail): boolean {
  const cleanup = task.cancel_cleanup?.state
  if (!task.terminal || task.notification_status === 'pending' || cleanup === 'pending' || cleanup === 'running') return true
  return task.items.some(item => (item.caches ?? [item.cache]).some(cache => {
    const state = cache?.operation?.state
    return state === 'queued' || state === 'running' || state === 'uncertain'
  }))
}

/** A visible list row is already checked at the live cadence. Ignore equivalent polls. */
export function detailSummaryKey(task: Task): string {
  return JSON.stringify([task.id, task.status, task.updated_at, task.notification_status,
    task.can_cancel, task.can_retry, task.can_delete, task.local_files])
}

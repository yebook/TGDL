import type { TaskDeleteRequest } from './types'
import { ApiError } from './client'
export { operationId as taskDeletionOperationId } from './operationId'

const key = 'tgdl.pending-task-file-deletion'

export function deletionOutcomeUncertain(cause: unknown, previouslyUncertain: boolean): boolean {
  // Rejection of a retry says nothing about the first accepted request. Only a
  // successful result can reconcile an already unresolved destructive attempt.
  return previouslyUncertain || !(cause instanceof ApiError && cause.status >= 400 && cause.status < 500
    && !['FILE_DELETE_RUNNING', 'FILE_DELETE_CONFLICT'].includes(cause.code))
}

// Retain only an unresolved operation's public IDs in this tab. A reload must
// not create a fresh destructive operation after the original response is lost.
export function pendingTaskDeletion(): TaskDeleteRequest | undefined {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key) ?? 'null')
    if (!value || typeof value !== 'object') return
    const payload = value as Partial<TaskDeleteRequest>
    if (typeof payload.delete_files !== 'boolean' || typeof payload.operation_id !== 'string' || !/^[a-f0-9]{32}$/.test(payload.operation_id)
      || !Array.isArray(payload.ids) || !payload.ids.length || payload.ids.length > 100
      || !payload.ids.every(id => typeof id === 'string' && id.length > 0 && id.length < 200)
      || new Set(payload.ids).size !== payload.ids.length) return
    return { ids: payload.ids, delete_files: payload.delete_files, operation_id: payload.operation_id }
  } catch { return undefined }
}

export function retainTaskDeletion(value?: TaskDeleteRequest) {
  try {
    if (value) sessionStorage.setItem(key, JSON.stringify(value))
    else sessionStorage.removeItem(key)
  } catch { /* Browser storage can be unavailable; the mounted dialog retains scope. */ }
}

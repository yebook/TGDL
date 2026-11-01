import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { deletionOutcomeUncertain, pendingTaskDeletion, retainTaskDeletion, taskDeletionOperationId } from '../src/api/taskDeletion'
import { ApiError } from '../src/api/client'

const values = new Map<string, string>()
beforeEach(() => {
  values.clear()
  vi.stubGlobal('sessionStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  })
})
afterEach(() => vi.unstubAllGlobals())

describe('explicit unresolved deletion recovery', () => {
  it('retains an earlier unknown outcome when a retry is rejected before admission', () => {
    for (const status of [401, 403, 404, 409, 422]) {
      const rejectedRetry = new ApiError('当前请求未获准', status, 'PREFLIGHT_ERROR')
      expect(deletionOutcomeUncertain(rejectedRetry, true)).toBe(true)
      expect(deletionOutcomeUncertain(rejectedRetry, false)).toBe(false)
    }
    expect(deletionOutcomeUncertain(new ApiError('处理中', 409, 'FILE_DELETE_RUNNING'), false)).toBe(true)
    expect(deletionOutcomeUncertain(new ApiError('失败', 500), false)).toBe(true)
  })
  it('generates hex32 IDs on ordinary LAN HTTP without secure-context randomUUID', () => {
    vi.stubGlobal('crypto', { getRandomValues: (target: Uint8Array) => { target.set(Array.from({ length: 16 }, (_, i) => i)); return target } })
    expect(taskDeletionOperationId()).toBe('000102030405060708090a0b0c0d0e0f')
  })
  it('retains exact record-only and file-deleting payloads for the same operation', () => {
    for (const delete_files of [false, true]) {
      const payload = { ids: ['task-a', 'task-b'], delete_files, operation_id: 'a'.repeat(32) }
      retainTaskDeletion(payload)
      expect(pendingTaskDeletion()).toEqual(payload)
    }
    retainTaskDeletion()
    expect(pendingTaskDeletion()).toBeUndefined()
  })
  it('does not recover malformed or duplicate scopes as an authorized operation', () => {
    for (const payload of [null, {}, { ids: ['a'], delete_files: true, operation_id: 'bad' },
      { ids: ['a', 'a'], delete_files: true, operation_id: 'a'.repeat(32) },
      { ids: ['a'], delete_files: 'true', operation_id: 'a'.repeat(32) }]) {
      values.set('tgdl.pending-task-file-deletion', JSON.stringify(payload))
      expect(pendingTaskDeletion()).toBeUndefined()
    }
  })
  it('tolerates denied browser storage without changing the mounted operation', () => {
    vi.stubGlobal('sessionStorage', { getItem: () => { throw Error('denied') }, setItem: () => { throw Error('denied') } })
    expect(pendingTaskDeletion()).toBeUndefined()
    expect(() => retainTaskDeletion({ ids: ['a'], delete_files: true, operation_id: 'a'.repeat(32) })).not.toThrow()
  })
})

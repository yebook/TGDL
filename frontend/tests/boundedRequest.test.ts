import { afterEach, describe, expect, it, vi } from 'vitest'
import { boundedRequest } from '../src/api/boundedRequest'
import { onSessionExpired } from '../src/api/client'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('bounded workspace requests', () => {
  it('releases a hung request even when transport ignores abort, without retrying a mutation', async () => {
    vi.useFakeTimers()
    let resolve!: (response: Response) => void
    const fetchMock = vi.fn().mockReturnValue(new Promise<Response>(done => { resolve = done }))
    vi.stubGlobal('fetch', fetchMock)
    const response = boundedRequest('/settings', { method: 'PATCH', body: '{}' }, 100)
    const rejected = expect(response).rejects.toMatchObject({ code: 'request_timeout' })
    await vi.advanceTimersByTimeAsync(100)
    await rejected
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0]![1].signal.aborted).toBe(true)
    resolve(new Response('{"revision":2}'))
    await Promise.resolve()
    expect(vi.getTimerCount()).toBe(0)
  })
  it('also bounds a response body that never finishes', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => new Promise(() => {}) }))
    const rejected = expect(boundedRequest('/system/status', {}, 100)).rejects.toMatchObject({ code: 'request_timeout' })
    await vi.advanceTimersByTimeAsync(100)
    await rejected
    expect(vi.getTimerCount()).toBe(0)
  })
  it('cancels on unmount and leaves later attempts independent', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockReturnValueOnce(new Promise(() => {})).mockResolvedValueOnce(new Response('{"ready":true}'))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()
    const rejected = expect(boundedRequest('/system/status', { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
    controller.abort()
    await rejected
    expect(vi.getTimerCount()).toBe(0)
    await expect(boundedRequest('/system/status')).resolves.toEqual({ ready: true })
  })
  it('does not let a timed-out late unauthorized body invalidate a later session', async () => {
    vi.useFakeTimers()
    const expired = vi.fn()
    onSessionExpired(expired)
    let resolve!: (value: unknown) => void
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, json: () => new Promise(done => { resolve = done }) }))
    const rejected = expect(boundedRequest('/system/status', {}, 100)).rejects.toMatchObject({ code: 'request_timeout' })
    await vi.advanceTimersByTimeAsync(100)
    await rejected
    resolve({ error: { code: 'session_expired' } })
    await Promise.resolve(); await Promise.resolve()
    expect(expired).not.toHaveBeenCalled()
  })
})

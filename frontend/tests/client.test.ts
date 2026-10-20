import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, onSessionExpired, request, setCsrfToken } from '../src/api/client'

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken('') })
describe('session API boundary', () => {
  it('sends the cookie and CSRF header for mutations, never puts secrets in URLs', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ revision: 2 }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    setCsrfToken('csrf-secret')
    await request('/settings', { method: 'PATCH', body: JSON.stringify({ bot_token: 'private-token' }) })
    const [url, options] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/v1/settings')
    expect(options.credentials).toBe('same-origin')
    expect(options.headers.get('X-CSRF-Token')).toBe('csrf-secret')
    expect(options.headers.get('Content-Type')).toBe('application/json')
  })
  it('clears the session on expiry and preserves structured error diagnostics', async () => {
    const expired = vi.fn()
    onSessionExpired(expired)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: {
      code: 'session_expired', message: '会话过期', request_id: 'safe-id',
    } }), { status: 401 })))
    await expect(request('/tasks')).rejects.toMatchObject({ status: 401, code: 'session_expired', requestId: 'safe-id' })
    expect(expired).toHaveBeenCalledOnce()
  })
  it('does not mistake rejected login for an expired established session', async () => {
    const expired = vi.fn()
    onSessionExpired(expired)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 401 })))
    await expect(request('/auth/login', { method: 'POST', body: '{}' })).rejects.toBeInstanceOf(ApiError)
    expect(expired).not.toHaveBeenCalled()
  })
  it('leaves cancellation silent instead of rewriting it as a network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('aborted', 'AbortError')))
    await expect(request('/tasks')).rejects.toMatchObject({ name: 'AbortError' })
  })
  it('handles no-content logout and unavailable backend responses', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response('<html>unavailable</html>', { status: 502 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(request('/auth/logout', { method: 'POST' })).resolves.toBeUndefined()
    await expect(request('/tasks')).rejects.toMatchObject({ status: 502, message: '请求未完成，请稍后重试。' })
  })
})

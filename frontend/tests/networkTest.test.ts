import { afterEach, describe, expect, it, vi } from 'vitest'
import { networkAdvice, networkProbeLabel, networkResultHistorical, useNetworkTest } from '../src/api/networkTest'
import type { NetworkProbeResult, NetworkTestResult } from '../src/api/types'

const reachable: NetworkProbeResult = { state: 'reachable', latency_ms: 12, http_status: 302, failure_kind: null, failure_stage: null }
function result(): NetworkTestResult {
  return { checked_at: new Date().toISOString(), settings_revision: 2, origin: 'app', route: 'direct', proxy_scheme: null, bot_api_mode: 'local', telegram: { ...reachable }, google: { ...reachable }, local_api_tcp: { ...reachable, http_status: null }, local_api: null, proxy_status: { state: 'disabled', desired_revision: 2, applied_revision: 2, message: '' }, local_api_upstream: 'unverified', next_allowed_at: new Date(Date.now() + 10000).toISOString() }
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
describe('manual dual-target network diagnostics', () => {
  it('labels each outcome without interpreting HTTP success as Bot readiness', () => {
    expect(networkProbeLabel(reachable)).toBe('可达')
    expect(networkProbeLabel({ ...reachable, state: 'not_applicable' })).toBe('不适用')
    expect(networkProbeLabel({ ...reachable, state: 'timeout' })).toBe('超时')
    expect(networkProbeLabel({ ...reachable, state: 'failed', failure_kind: 'proxy_auth' })).toBe('代理认证失败')
    const value = result()
    expect(networkAdvice(value).join('')).toContain('不代表 Bot 凭据有效')
    value.google.state = 'failed'
    expect(networkAdvice(value).join('')).toContain('不能据此判定整体网络故障')
    value.telegram.state = 'failed'; value.google.state = 'reachable'
    expect(networkAdvice(value).join('')).toContain('不能据此判定 Bot 被限制')
    value.google.state = 'failed'; value.local_api_tcp.state = 'timeout'
    expect(networkAdvice(value).join('')).toContain('内部服务名称解析')
    expect(networkAdvice(value).join('')).toContain('两个 HTTPS 入口均未通过')
  })
  it('invalidates results after saves or expiry, not because a draft is edited', () => {
    const value = result()
    expect(networkResultHistorical(value, 2, Date.now())).toBe(false)
    expect(networkResultHistorical(value, 3, Date.now())).toBe(true)
    expect(networkResultHistorical(value, 2, Date.now() + 300000)).toBe(true)
  })
  it('starts only one round and enforces the local ten-second interval', async () => {
    vi.useFakeTimers()
    let resolve!: (response: Response) => void
    const fetchMock = vi.fn().mockReturnValue(new Promise<Response>(done => { resolve = done }))
    vi.stubGlobal('fetch', fetchMock)
    const state = useNetworkTest()
    const pending = state.run()
    await state.run()
    expect(fetchMock).toHaveBeenCalledOnce()
    resolve(new Response(JSON.stringify(result())))
    await pending
    await state.run()
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(state.busy.value).toBe(false)
    expect(state.result.value?.local_api_upstream).toBe('unverified')
    expect(fetchMock.mock.calls[0]![1].body).toBeUndefined()
  })
  it('discards a late response after section leave, without publishing error feedback', async () => {
    let resolve!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise<Response>(done => { resolve = done })))
    const state = useNetworkTest()
    const pending = state.run()
    state.cancel()
    resolve(new Response(JSON.stringify(result())))
    await pending
    expect(state.result.value).toBeUndefined()
    expect(state.busy.value).toBe(false)
    expect(state.error.value).toBe('')
  })
  it('bounds hung response-body reading to fifteen seconds and never retries', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => new Promise(() => {}) })
    vi.stubGlobal('fetch', fetchMock)
    const state = useNetworkTest()
    const pending = state.run()
    await vi.advanceTimersByTimeAsync(15000)
    await pending
    expect(state.busy.value).toBe(false)
    expect(state.error.value).toContain('不会自动重复检测')
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(vi.getTimerCount()).toBe(0)
  })
  it('honors a server cooldown without scheduling automatic work', async () => {
    const retryAt = new Date(Date.now() + 30000).toISOString()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'NETWORK_TEST_RATE_LIMITED', message: '稍后再试', details: { retry_at: retryAt } } }), { status: 429 })))
    const state = useNetworkTest()
    await state.run()
    expect(state.nextAllowedAt.value).toBe(Date.parse(retryAt))
    expect(state.error.value).toBe('稍后再试')
    expect(state.result.value).toBeUndefined()
  })
})

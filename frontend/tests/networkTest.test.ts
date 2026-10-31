import { afterEach, describe, expect, it, vi } from 'vitest'
import { networkAdvice, networkProbeLabel, networkResultHistorical, telegramAppliedRouteLabel, telegramProtocolLabel, telegramTargetLabel, useNetworkTest } from '../src/api/networkTest'
import type { NetworkProbeResult, NetworkTestResult } from '../src/api/types'

const reachable: NetworkProbeResult = { state: 'reachable', latency_ms: 12, http_status: 302, failure_kind: null, failure_stage: null }
function result(): NetworkTestResult {
  return { checked_at: new Date().toISOString(), settings_revision: 2, origin: 'app', route: 'direct', proxy_scheme: null, bot_api_mode: 'local', telegram: { ...reachable }, google: { ...reachable }, local_api_tcp: { ...reachable, http_status: null }, local_api: null, proxy_status: { state: 'disabled', desired_revision: 2, applied_revision: 2, message: '' }, telegram_protocol: { state: 'timeout', origin: 'bot_api', route: 'direct', settings_revision: 2, coverage: 'fixed_production_dcs', targets: [{ dc: 1, host: '149.154.175.50', port: 443, state: 'timeout', latency_ms: 5000, failure_kind: 'timeout' }], applied_route: 'matched', observed_generation: 'fixture-generation', observed_command_id: 'fixture-command', reason: null }, next_allowed_at: new Date(Date.now() + 10000).toISOString() }
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
describe('manual network diagnostics', () => {
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
  it('identifies HTTPS success with protocol timeout without declaring overall network success', () => {
    const value = result()
    expect(telegramProtocolLabel(value.telegram_protocol)).toBe('Telegram 协议连接超时')
    expect(networkAdvice(value)[0]).toContain('HTTPS 可达，但本地 API 容器中的 Telegram 协议连接未通过')
    expect(telegramTargetLabel(value.telegram_protocol.targets[0]!)).toBe('超时')
    value.telegram_protocol.targets[0]!.state = 'failed'
    value.telegram_protocol.targets[0]!.failure_kind = 'protocol'
    expect(telegramTargetLabel(value.telegram_protocol.targets[0]!)).toBe('协议握手未通过')
  })
  it('retains finite coverage and a failed API operation after protocol success', () => {
    const value = result()
    value.telegram_protocol.state = 'reachable'
    value.local_api = { managed: true, state: 'running', desired_mode: 'local', applied_mode: 'local', operation_id: 'fixture-operation', phase: 'failed', drain_reason: null, message: '连接失败', generation: 'fixture-generation', last_heartbeat_at: null, cloud_available_at: null, can_restart: true, can_retry: true }
    expect(telegramProtocolLabel(value.telegram_protocol)).toBe('被测线路握手通过')
    const advice = networkAdvice(value).join('')
    expect(advice).toContain('这不验证 Bot 凭据、所有数据中心或实际消息收发')
    expect(advice).toContain('API 操作仍为失败状态')
    expect(advice).toContain('本次检测不会自动恢复操作')
  })
  it('keeps partial DC failures and applied-route mismatch separate from saved-route success', () => {
    const value = result()
    value.telegram_protocol.state = 'partial'
    value.telegram_protocol.applied_route = 'different'
    expect(telegramProtocolLabel(value.telegram_protocol)).toBe('部分线路握手失败')
    expect(telegramAppliedRouteLabel(value.telegram_protocol)).toContain('与本次检测不同')
    expect(networkAdvice(value).join('')).toContain('不能将其视为全部线路正常')
    expect(networkAdvice(value).join('')).toContain('API 进程仍使用不同路线')
  })
  it.each(['helper_unavailable', 'stale_observation', 'configuration_changed', 'round_timeout'] as const)('explains unavailable protocol result: %s', reason => {
    const value = result()
    value.telegram_protocol = { ...value.telegram_protocol, state: 'unavailable', reason, targets: [], applied_route: 'unavailable' }
    expect(telegramProtocolLabel(value.telegram_protocol)).toBe('协议检测暂不可用')
    expect(networkAdvice(value).join('')).toContain('重新检测')
    expect(networkAdvice(value).join('')).toContain('尚未确认 API 进程实际使用的路线')
  })
  it('does not substitute app HTTPS for unsupported local API diagnostics or cloud mode', () => {
    const value = result()
    value.telegram_protocol.state = 'unsupported'
    expect(networkAdvice(value).join('')).toContain('应用侧 HTTPS 成功不能代替这项检测')
    value.bot_api_mode = 'cloud'; value.telegram_protocol.state = 'not_applicable'
    expect(telegramProtocolLabel(value.telegram_protocol)).toBe('云端模式不适用')
    expect(networkAdvice(value).join('')).not.toContain('API 容器中的 Telegram 协议')
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
    expect(state.result.value?.telegram_protocol.state).toBe('timeout')
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
  it('retains the previous protocol and API observations when a later round fails', async () => {
    vi.useFakeTimers()
    const previous = result()
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(previous)))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'TEST_FAILURE', message: '检测失败' } }), { status: 503 }))
    vi.stubGlobal('fetch', fetchMock)
    const state = useNetworkTest()
    await state.run()
    await vi.advanceTimersByTimeAsync(11000)
    await state.run()
    expect(state.result.value).toEqual(previous)
    expect(state.error.value).toBe('检测失败')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(state.busy.value).toBe(false)
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

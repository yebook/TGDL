import { ref } from 'vue'
import { boundedRequest } from './boundedRequest'
import { ApiError, errorMessage, isAbort } from './client'
import type { NetworkProbeResult, NetworkTestResult } from './types'

export const networkResultLifetimeMs = 5 * 60 * 1000
const failureLabels: Record<NonNullable<NetworkProbeResult['failure_kind']>, string> = {
  dns: '名称解析失败', proxy_connection: '代理连接失败', proxy_auth: '代理认证失败',
  tcp: 'TCP 连接失败', tls: 'TLS 校验或握手失败', timeout: '请求超时', network_unknown: '网络失败，原因尚未确定',
}
export function networkProbeLabel(result: NetworkProbeResult): string {
  switch (result.state) {
    case 'reachable': return '可达'
    case 'timeout': return '超时'
    case 'not_run': return '未执行'
    case 'not_applicable': return '不适用'
    case 'failed': return result.failure_kind ? failureLabels[result.failure_kind] : '失败，原因尚未确定'
  }
}
export function networkResultHistorical(result: NetworkTestResult, revision: number, now: number): boolean {
  return result.settings_revision !== revision || !Number.isFinite(Date.parse(result.checked_at)) || now - Date.parse(result.checked_at) >= networkResultLifetimeMs
}
export function networkAdvice(result: NetworkTestResult): string[] {
  const telegram = result.telegram.state === 'reachable'
  const google = result.google.state === 'reachable'
  const hints: string[] = []
  if (telegram && google) hints.push('应用侧两个 HTTPS 入口均可达；这不代表 Bot 凭据有效或消息收发正常。')
  else if (!telegram && google) hints.push('Google 可达，Telegram 入口未通过：请检查 Telegram 目标的路由或代理规则，不能据此判定 Bot 被限制。')
  else if (telegram && !google) hints.push('Telegram 入口可达；Google 可能受地区或路由规则影响，不能据此判定整体网络故障。')
  else hints.push('两个 HTTPS 入口均未通过，请结合失败类别检查容器网络、代理服务与 DNS；原因尚未确定时需进一步核对部署日志。')
  if ([result.telegram, result.google].some(item => item.failure_kind === 'proxy_auth')) hints.push('请核对已保存的代理用户名与密码；检测不会修改或切换代理。')
  if (result.bot_api_mode === 'local') {
    if (result.local_api_tcp.state !== 'reachable') hints.push('应用到本地 API 端口未通过：优先检查内部服务名称解析、端口和服务状态。')
    else hints.push('本地 API 端口可达仅证明 TCP 接入；若 Bot 验证仍失败，请核对 API 日志、已应用配置与上游链路，原因不能仅归于 Telegram 出口。')
  }
  if (['pending', 'applying', 'failed', 'unavailable'].includes(result.proxy_status.state)) hints.push('测试使用已保存路线；代理生效状态尚未确认，测试成功不会将其改为已应用。')
  return hints
}

/** One owner for the round; cancellation invalidates even transports that ignore abort. */
export function useNetworkTest() {
  const result = ref<NetworkTestResult>()
  const busy = ref(false)
  const error = ref('')
  const nextAllowedAt = ref(0)
  let generation = 0
  let controller: AbortController | undefined
  function cancel() {
    generation++
    controller?.abort()
    controller = undefined
    busy.value = false
  }
  async function run() {
    if (busy.value || Date.now() < nextAllowedAt.value) return
    const current = ++generation
    const requestController = new AbortController()
    controller = requestController
    busy.value = true
    error.value = ''
    nextAllowedAt.value = Date.now() + 10000
    try {
      const response = await boundedRequest<NetworkTestResult>('/system/network-test', { method: 'POST', signal: requestController.signal }, 15000)
      if (generation !== current || requestController.signal.aborted) return
      result.value = response
      const next = Date.parse(response.next_allowed_at)
      if (Number.isFinite(next)) nextAllowedAt.value = Math.max(nextAllowedAt.value, next)
    } catch (cause) {
      if (generation !== current || isAbort(cause)) return
      error.value = cause instanceof ApiError && cause.code === 'request_timeout'
        ? '检测等待已超时；服务端仍受单轮时限约束。请稍后手动重试，不会自动重复检测。' : errorMessage(cause)
      if (cause instanceof ApiError && ['NETWORK_TEST_BUSY', 'NETWORK_TEST_RATE_LIMITED'].includes(cause.code)) {
        const details = cause.details
        if (details && typeof details === 'object' && 'retry_at' in details && typeof details.retry_at === 'string') {
          const next = Date.parse(details.retry_at)
          if (Number.isFinite(next)) nextAllowedAt.value = Math.max(nextAllowedAt.value, next)
        }
      }
    } finally {
      if (generation === current) { busy.value = false; controller = undefined }
    }
  }
  return { result, busy, error, nextAllowedAt, run, cancel }
}

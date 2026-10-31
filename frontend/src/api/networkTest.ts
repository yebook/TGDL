import { ref } from 'vue'
import { boundedRequest } from './boundedRequest'
import { ApiError, errorMessage, isAbort } from './client'
import type { NetworkProbeResult, NetworkTestResult, TelegramProtocolResult, TelegramProtocolTarget } from './types'

export const networkResultLifetimeMs = 5 * 60 * 1000
const failureLabels: Record<NonNullable<NetworkProbeResult['failure_kind'] | TelegramProtocolTarget['failure_kind']>, string> = {
  dns: '名称解析失败', proxy_connection: '代理连接失败', proxy_auth: '代理认证失败',
  tcp: 'TCP 连接失败', tls: 'TLS 校验或握手失败', timeout: '请求超时', protocol: '协议握手未通过', network_unknown: '网络失败，原因尚未确定',
}
export function telegramProtocolLabel(result: TelegramProtocolResult): string {
  return ({ reachable: '被测线路握手通过', partial: '部分线路握手失败', failed: 'Telegram 协议连接失败', timeout: 'Telegram 协议连接超时', unavailable: '协议检测暂不可用', unsupported: '此 API 不支持协议检测', not_applicable: '云端模式不适用' })[result.state]
}
export function telegramTargetLabel(result: TelegramProtocolTarget): string {
  if (result.state === 'reachable') return '握手通过'
  if (result.state === 'timeout') return '超时'
  return result.failure_kind ? failureLabels[result.failure_kind] : '失败，原因尚未确定'
}
export function telegramAppliedRouteLabel(result: TelegramProtocolResult): string {
  return ({ matched: 'API 进程已应用被测路线', different: 'API 进程路线与本次检测不同', unavailable: 'API 进程当前路线尚未确认' })[result.applied_route]
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
  const protocol = result.telegram_protocol
  if (result.bot_api_mode === 'local') {
    if (['failed', 'timeout'].includes(protocol.state)) hints.push(telegram && google
      ? 'HTTPS 可达，但本地 API 容器中的 Telegram 协议连接未通过。请检查 Telegram 数据中心线路、防火墙或代理规则。'
      : '本地 API 容器中的 Telegram 协议连接未通过；请结合下方 HTTPS 结果排查容器出口、Telegram 数据中心线路和代理规则。')
    if (protocol.state === 'partial') hints.push('仅部分被测 Telegram 线路握手通过；请查看失败的数据中心与端口，不能将其视为全部线路正常。')
    if (protocol.state === 'reachable') hints.push('被测 Telegram 线路握手通过；这不验证 Bot 凭据、所有数据中心或实际消息收发。')
    if (protocol.state === 'unsupported') hints.push('当前 API 为外部管理或不支持容器内协议检测，请在 API 所在容器核对网络；应用侧 HTTPS 成功不能代替这项检测。')
    if (protocol.state === 'unavailable') hints.push(protocol.reason === 'configuration_changed'
      ? '检测期间配置或 API 进程发生变化，本次协议结果不可用于判断当前连接，请重新检测。'
      : protocol.reason === 'stale_observation'
        ? 'API 管理状态或协议结果已过期，无法确认当前连接，请检查服务状态后重新检测。'
        : protocol.reason === 'round_timeout'
          ? '协议检测未在本轮时限内完成，请检查 API 服务状态后重新检测。'
          : 'API 容器内协议检测暂不可用，请检查管理组件与镜像是否已更新，再重新检测。')
    if (protocol.applied_route === 'different') hints.push('本次检测使用已保存的路线，API 进程仍使用不同路线；检测成功不能说明该进程已恢复。')
    if (protocol.applied_route === 'unavailable' && protocol.state !== 'unsupported') hints.push('尚未确认 API 进程实际使用的路线；请结合连接状态核对配置是否已生效。')
    if (result.local_api?.phase === 'failed') hints.push('API 操作仍为失败状态。修复网络后，可在上方连接状态中手动重试操作或重启 API 进程；本次检测不会自动恢复操作。')
  }
  if (telegram && google) hints.push('应用侧两个 HTTPS 入口均可达；这不代表 Bot 凭据有效或消息收发正常。')
  else if (!telegram && google) hints.push('Google 可达，Telegram 入口未通过：请检查 Telegram 目标的路由或代理规则，不能据此判定 Bot 被限制。')
  else if (telegram && !google) hints.push('Telegram 入口可达；Google 可能受地区或路由规则影响，不能据此判定整体网络故障。')
  else hints.push('两个 HTTPS 入口均未通过，请结合失败类别检查容器网络、代理服务与 DNS；原因尚未确定时需进一步核对部署日志。')
  if ([result.telegram, result.google, ...protocol.targets].some(item => item.failure_kind === 'proxy_auth')) hints.push('请核对已保存的代理用户名与密码；检测不会修改或切换代理。')
  if (result.bot_api_mode === 'local') {
    if (result.local_api_tcp.state !== 'reachable') hints.push('应用到本地 API 端口未通过：优先检查内部服务名称解析、端口和服务状态。')
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

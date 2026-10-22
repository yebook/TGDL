import type { LocalApiStatus } from './types'
import { localApiPhases } from './localApi'

export const connectionFields = new Set(['bot_api_mode', 'bot_token', 'telegram_api_id', 'telegram_api_hash', 'proxy', 'clear_bot_token', 'clear_proxy_password', 'clear_telegram_api_hash'])

/** Connection controls follow the durable barrier, including externally managed cloud reconnects. */
export function connectionEditBlockReason(status: LocalApiStatus | undefined, stale: boolean, awaitingStatus = false): string {
  if (stale) return '服务状态更新失败，连接设置暂不可修改；状态恢复后可继续。其他设置可照常保存。'
  if (awaitingStatus || !status) return '正在确认连接配置的生效状态，请稍候。其他设置可照常保存。'
  if (status.phase === 'logout_uncertain') return '云端退出结果待确认，请在下方服务状态中明确确认后继续。其他设置可照常保存。'
  if (['draining', 'logging_out', 'starting', 'verifying', 'cooldown'].includes(status.phase)) return `正在应用连接配置：${status.message || localApiPhases[status.phase]}。完成后可继续修改连接；其他设置可照常保存。`
  return ''
}

export function bytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '未知'
  if (value === 0) return '0 B'
  const unit = Math.min(Math.floor(Math.log(Math.max(value, 1)) / Math.log(1024)), 4)
  return `${(value / 1024 ** unit).toFixed(unit ? 1 : 0)} ${['B', 'KB', 'MB', 'GB', 'TB'][unit]}`
}
export function dateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}
export const states: Record<string, { label: string; tone: 'success' | 'warning' | 'danger' | 'info' | 'primary' }> = {
  collecting: { label: '正在接收', tone: 'info' },
  aggregating: { label: '正在接收', tone: 'info' },
  awaiting_confirmation: { label: '等待 Bot 确认', tone: 'warning' },
  queued: { label: '排队中', tone: 'info' }, fetching: { label: '下载中', tone: 'primary' },
  saving: { label: '处理中', tone: 'primary' }, downloading: { label: '下载中', tone: 'primary' },
  copying: { label: '保存中', tone: 'primary' },
  active: { label: '下载中', tone: 'primary' }, retry_wait: { label: '等待重试', tone: 'warning' },
  succeeded: { label: '已完成', tone: 'success' }, partial_success: { label: '部分完成', tone: 'warning' },
  failed: { label: '失败', tone: 'danger' }, cancelled: { label: '已取消', tone: 'info' },
  cancelling: { label: '取消中', tone: 'warning' }, expired: { label: '确认已过期', tone: 'info' },
  stored: { label: '已保存', tone: 'success' }, already_stored: { label: '已保存', tone: 'success' },
  deduplicated: { label: '已保存', tone: 'success' }, waiting_existing: { label: '等待已有任务', tone: 'info' },
  already_exists: { label: '已保存', tone: 'success' }, recovery_blocked: { label: '等待恢复处理', tone: 'warning' },
}
export const stateLabel = (state: string) => states[state]?.label ?? state
export function percent(done: number, total: number | null): number | undefined {
  return total != null && total > 0 ? Math.min(100, Math.max(0, Math.round(done / total * 100))) : undefined
}
export function parseUserIds(text: string): number[] {
  const values = text.split(/[\s,，]+/).filter(Boolean)
  if (values.some(value => !/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) <= 0)) {
    throw new Error('白名单须为正整数 Telegram 用户 ID，以逗号或换行分隔。')
  }
  return [...new Set(values.map(Number))]
}

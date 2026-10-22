export const proxyLabels: Record<string, string> = {
  disabled: '未启用', pending: '等待应用', applying: '正在应用', active: '已应用',
  failed: '应用失败', unavailable: '网络组件未连接',
}

export function proxySummary(state: string, stale: boolean): string {
  const label = state === 'disabled' ? '直连' : `代理${proxyLabels[state] || '状态待确认'}`
  return stale ? `上次状态：${label}` : label
}

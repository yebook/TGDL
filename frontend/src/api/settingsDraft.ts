import type { ProxyConfig, SettingsValues } from './types'

export interface SettingsDraft {
  form: SettingsValues; userIds: string; clearBotToken: boolean; clearProxyPassword: boolean; clearApiHash: boolean
}

/** Overlay only local edits on a newly read baseline, including write-only secrets and clear intentions. */
export function changedSettingsDraft(baseline: SettingsDraft, current: SettingsDraft) {
  const form: Partial<Omit<SettingsValues, 'proxy'>> = {}
  const proxy: Partial<ProxyConfig> = {}
  for (const key of Object.keys(current.form) as (keyof SettingsValues)[]) {
    if (key === 'proxy') continue
    if (JSON.stringify(current.form[key]) !== JSON.stringify(baseline.form[key])) Object.assign(form, { [key]: structuredClone(current.form[key]) })
  }
  for (const key of Object.keys(current.form.proxy) as (keyof ProxyConfig)[]) {
    if (current.form.proxy[key] !== baseline.form.proxy[key]) Object.assign(proxy, { [key]: current.form.proxy[key] })
  }
  const result: Omit<Partial<SettingsDraft>, 'form'> & { form: typeof form; proxy: typeof proxy } = { form, proxy }
  for (const key of ['userIds', 'clearBotToken', 'clearProxyPassword', 'clearApiHash'] as const) {
    if (current[key] !== baseline[key]) Object.assign(result, { [key]: current[key] })
  }
  return result
}

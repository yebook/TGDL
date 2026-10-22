import { describe, expect, it } from 'vitest'
import { changedSettingsDraft, type SettingsDraft } from '../src/api/settingsDraft'
import type { SettingsValues } from '../src/api/types'

function draft(): SettingsDraft {
  return { form: { bot_api_mode: 'cloud', telegram_api_id: '', telegram_api_hash: '', bot_token: '', allowed_user_ids: [42], download_concurrency: 3, filename_template: '{name}', proxy: { enabled: true, scheme: 'socks5', host: 'old-host', port: 1080, username: '', password: '' } } as SettingsValues, userIds: '42', clearBotToken: false, clearProxyPassword: false, clearApiHash: false }
}

describe('status reconciliation keeps intentional edits', () => {
  it('preserves only local edits and clear intentions against a newly loaded server baseline', () => {
    const baseline = draft()
    const local = structuredClone(baseline)
    local.form.bot_token = 'offline-unsaved-token'
    local.form.download_concurrency = 5
    local.clearApiHash = true
    local.userIds = '42\n43'
    const edits = changedSettingsDraft(baseline, local)
    const remote = draft()
    remote.form.filename_template = '{name}_remote'
    Object.assign(remote.form, edits.form)
    expect(remote.form.filename_template).toBe('{name}_remote')
    expect(edits).toMatchObject({ form: { bot_token: 'offline-unsaved-token', download_concurrency: 5 }, clearApiHash: true, userIds: '42\n43' })
    expect(edits).not.toHaveProperty('clearProxyPassword')
  })
  it('does not overwrite remote proxy routing when only a local password was edited', () => {
    const baseline = draft()
    const local = structuredClone(baseline)
    local.form.proxy.password = 'offline-unsaved-password'
    const edits = changedSettingsDraft(baseline, local)
    const remote = draft()
    Object.assign(remote.form.proxy, { host: 'remote-host', port: 2080, enabled: false })
    Object.assign(remote.form.proxy, edits.proxy)
    expect(remote.form.proxy).toMatchObject({ host: 'remote-host', port: 2080, enabled: false, password: 'offline-unsaved-password' })
    expect(edits.form).not.toHaveProperty('proxy')
  })
})

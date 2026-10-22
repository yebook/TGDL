import { expect, test, type Page } from '@playwright/test'

const scenario = process.env.E2E_MANAGED_API
async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('管理员账号', { exact: true }).fill('e2e-admin')
  await page.getByLabel('密码', { exact: true }).fill('local-e2e-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('link', { name: '设置', exact: true }).click()
  await expect(page.getByTestId('local-api-status')).toBeVisible()
}
async function screenshot(page: Page, name: string) {
  await page.getByTestId('local-api-status').scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await expect(page.locator('.el-message')).toHaveCount(0, { timeout: 6000 })
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
    window.scrollTo(0, 0)
  })
  await page.screenshot({ path: `../.runtime/managed-api/${name}.png`, fullPage: true })
}

test('managed credentials save offline without echo and polling preserves edits', async ({ page }) => {
  test.skip(scenario !== 'credentials')
  await login(page)
  await expect(page.getByTestId('local-api-status')).toContainText('等待应用凭据')
  await page.getByLabel('API ID', { exact: true }).fill('123456')
  await page.getByLabel('API Hash', { exact: true }).fill('0123456789abcdef0123456789abcdef')
  const statusPoll = page.waitForResponse(response => response.url().endsWith('/system/status'))
  await statusPoll
  await expect(page.getByLabel('API ID', { exact: true })).toHaveValue('123456')
  await expect(page.getByLabel('API Hash', { exact: true })).toHaveValue('0123456789abcdef0123456789abcdef')
  await expect(page.getByRole('button', { name: '重启 API 进程', exact: true })).toBeDisabled()
  const saved = page.waitForResponse(response => response.url().endsWith('/settings') && response.request().method() === 'PATCH')
  await page.getByRole('button', { name: '保存设置', exact: true }).click()
  const result = await (await saved).json()
  expect(result.values.telegram_api_hash).toBe('')
  expect(result.configured.telegram_api_hash).toBe(true)
  await expect(page.getByLabel('API Hash', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('API Hash', { exact: true })).toHaveAttribute('placeholder', '已配置 · 留空保持不变')
  await expect(page.getByTestId('local-api-status')).toContainText('正在准备切换连接')
  await screenshot(page, 'settings-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await screenshot(page, 'settings-mobile')
})

test('connection switch handshake stays locked when a manual refresh supersedes the save refresh', async ({ page }) => {
  test.skip(scenario !== 'credentials')
  await login(page)
  await page.getByLabel('API ID', { exact: true }).fill('123456')
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/system/status', async route => {
    await gate
    await route.continue().catch(() => {})
  })
  const saved = page.waitForResponse(response => response.url().endsWith('/settings') && response.request().method() === 'PATCH')
  const refreshing = page.waitForRequest(request => request.url().endsWith('/system/status'))
  await page.getByRole('button', { name: '保存设置', exact: true }).click()
  expect((await saved).ok()).toBe(true)
  await refreshing
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  // The old save refresh was aborted; its HTTP busy state is now released, but the connection is still locked.
  await page.getByRole('button', { name: '下载与保存', exact: true }).click()
  await expect(page.getByLabel('确认有效期（分钟）', { exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '连接 Telegram', exact: true }).click()
  await expect(page.getByLabel('API ID', { exact: true })).toBeDisabled()
  await expect(page.locator('.connection-application-notice')).toContainText('正在确认')
  release()
  await expect(page.locator('.connection-application-notice')).toContainText('正在应用连接配置')
  await expect(page.getByRole('combobox', { name: 'Bot API 服务', exact: true })).toBeDisabled()
  await page.unroute('**/api/v1/system/status')
})

test('ordinary saves preserve connection candidates and clear flags during an existing transition', async ({ page }) => {
  test.skip(scenario !== 'connection')
  await login(page)
  await page.getByLabel('Bot Token', { exact: true }).fill('98765:unsaved-offline-candidate-token')
  await page.getByLabel('API ID', { exact: true }).fill('not-yet-valid')
  await page.getByText('清除已保存的 API Hash', { exact: true }).click()
  const csrf = (await (await page.request.get('/api/v1/auth/me')).json()).csrf_token
  const restarted = await page.request.post('/api/v1/system/local-api/restart', { headers: { 'X-CSRF-Token': csrf } })
  expect(restarted.status()).toBe(202)
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(page.getByLabel('Bot Token', { exact: true })).toBeDisabled()
  await expect(page.getByRole('checkbox', { name: '清除已保存的 API Hash', exact: true })).toBeDisabled()
  await expect(page.getByLabel('允许使用的 Telegram 用户 ID', { exact: true })).toBeEnabled()
  for (const minutes of ['31', '32']) {
    await page.getByRole('button', { name: '下载与保存', exact: true }).click()
    await page.getByLabel('确认有效期（分钟）', { exact: true }).fill(minutes)
    const saved = page.waitForResponse(response => response.url().endsWith('/settings') && response.request().method() === 'PATCH')
    await page.getByRole('button', { name: '保存设置', exact: true }).click()
    const response = await saved
    expect(response.ok()).toBe(true)
    const patch = response.request().postDataJSON()
    expect(patch.confirmation_timeout_seconds).toBe(Number(minutes) * 60)
    for (const key of ['bot_token', 'bot_api_mode', 'telegram_api_id', 'telegram_api_hash', 'proxy', 'clear_telegram_api_hash']) expect(patch).not.toHaveProperty(key)
    const actual = await response.json()
    expect(actual.configured.telegram_api_hash).toBe(true)
    await page.getByRole('button', { name: '连接 Telegram', exact: true }).click()
    await expect(page.getByLabel('Bot Token', { exact: true })).toHaveValue('98765:unsaved-offline-candidate-token')
    await expect(page.getByLabel('API ID', { exact: true })).toHaveValue('not-yet-valid')
    await expect(page.getByRole('checkbox', { name: '清除已保存的 API Hash', exact: true })).toBeChecked()
    await expect(page.getByLabel('API ID', { exact: true })).toBeDisabled()
  }
})

test('standalone cloud proxy disable remains locked until the real connection barrier completes', async ({ page }) => {
  test.skip(scenario !== 'cloud-connection')
  await login(page)
  await expect(page.getByRole('switch', { name: '启用代理', exact: true })).toBeChecked()
  await page.locator('.el-switch').filter({ has: page.locator('#proxy-enabled') }).click()
  const saved = page.waitForResponse(response => response.url().endsWith('/settings') && response.request().method() === 'PATCH')
  await page.getByRole('button', { name: '保存设置', exact: true }).click()
  expect((await (await saved).json()).proxy_status.state).toBe('disabled')
  await expect(page.locator('.connection-application-notice')).toContainText('正在应用连接配置')
  const status = await (await page.request.get('/api/v1/system/status')).json()
  expect(status.local_api.managed).toBe(false)
  expect(status.local_api.phase).toBe('draining')
  await expect(page.getByRole('switch', { name: '启用代理', exact: true })).toBeDisabled()
  await expect(page.getByLabel('Bot Token', { exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '重启 API 进程', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '下载与保存', exact: true }).click()
  await page.getByLabel('同时下载的文件数', { exact: true }).fill('4')
  const ordinarySaved = page.waitForResponse(response => response.url().endsWith('/settings') && response.request().method() === 'PATCH')
  await page.getByRole('button', { name: '保存设置', exact: true }).click()
  const result = await ordinarySaved
  expect(result.ok()).toBe(true)
  expect(result.request().postDataJSON()).toMatchObject({ download_concurrency: 4 })
  expect(result.request().postDataJSON()).not.toHaveProperty('proxy')
})

test('connection recovery reconciles a committed save whose response was lost without resending it', async ({ page }) => {
  test.skip(!!scenario)
  test.setTimeout(45000)
  await login(page)
  await page.getByLabel('API ID', { exact: true }).fill('777002')
  await page.getByLabel('API Hash', { exact: true }).fill('0123456789abcdef0123456789abcdef')
  let writes = 0
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/settings', async route => {
    if (route.request().method() !== 'PATCH') { await route.continue(); return }
    writes++
    const response = await route.fetch() // Commit through the real API, then lose only the browser's reply.
    await gate
    await route.fulfill({ response }).catch(() => {})
  })
  await page.getByRole('button', { name: '保存设置', exact: true }).click()
  await expect(page.locator('.connection-application-notice')).toContainText('上次保存的结果尚未确认', { timeout: 20000 })
  expect(writes).toBe(1)
  await expect(page.getByLabel('API ID', { exact: true })).toHaveValue('777002')
  await expect(page.getByLabel('API ID', { exact: true })).toBeDisabled()
  release()
  await page.unroute('**/api/v1/settings')
  await page.getByRole('button', { name: '重新同步状态', exact: true }).click()
  await expect(page.getByLabel('API ID', { exact: true })).toBeEnabled()
  await expect(page.getByLabel('API Hash', { exact: true })).toHaveValue('0123456789abcdef0123456789abcdef')
  expect((await (await page.request.get('/api/v1/settings')).json()).configured.telegram_api_hash).toBe(true)
  expect(writes).toBe(1)
})

test('connection recovery bounds a hung status request and preserves edits through failed resync and page reentry', async ({ page }) => {
  test.skip(!!scenario)
  test.setTimeout(45000)
  await login(page)
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/system/status', async route => { await gate; await route.continue().catch(() => {}) })
  await page.getByLabel('API ID', { exact: true }).fill('777003')
  await page.getByRole('button', { name: '保存设置', exact: true }).click()
  await expect(page.locator('.connection-application-notice')).toContainText('状态更新失败', { timeout: 15000 })
  await expect(page.getByRole('button', { name: '重新同步状态', exact: true })).toBeEnabled()
  await page.getByLabel('允许使用的 Telegram 用户 ID', { exact: true }).fill('42\n43')
  await expect(page.getByLabel('API ID', { exact: true })).toBeDisabled()
  release()
  await page.unroute('**/api/v1/system/status')
  await page.route('**/api/v1/system/status', route => route.abort())
  await page.getByRole('button', { name: '重新同步状态', exact: true }).click()
  await expect(page.locator('.connection-application-notice')).toContainText('状态更新失败')
  await expect(page.getByLabel('允许使用的 Telegram 用户 ID', { exact: true })).toHaveValue('42\n43')
  await page.unroute('**/api/v1/system/status')
  await page.getByRole('button', { name: '重新同步状态', exact: true }).click()
  await expect(page.getByLabel('API ID', { exact: true })).toBeEnabled()
  await expect(page.getByLabel('允许使用的 Telegram 用户 ID', { exact: true })).toHaveValue('42\n43')
  await page.getByRole('link', { name: '下载任务', exact: true }).click()
  await page.getByRole('button', { name: '放弃修改并离开', exact: true }).click()
  await page.getByRole('link', { name: '设置', exact: true }).click()
  await expect(page.getByLabel('API ID', { exact: true })).toBeEnabled()
  // Leaving during an explicit re-sync cancels its read and does not carry a busy state into a new view.
  await page.route('**/api/v1/system/status', route => route.abort())
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(page.locator('.connection-application-notice')).toContainText('状态更新失败')
  let releaseRead!: () => void
  const readGate = new Promise<void>(resolve => { releaseRead = resolve })
  await page.route('**/api/v1/settings', async route => {
    const response = await route.fetch()
    await readGate
    await route.fulfill({ response }).catch(() => {})
  })
  await page.getByRole('button', { name: '重新同步状态', exact: true }).click()
  await expect(page.getByRole('button', { name: '正在同步状态…', exact: true })).toBeDisabled()
  await page.getByRole('link', { name: '下载任务', exact: true }).click()
  releaseRead()
  await page.unroute('**/api/v1/settings')
  await page.unroute('**/api/v1/system/status')
  await page.getByRole('link', { name: '设置', exact: true }).click()
  await expect(page.getByLabel('API ID', { exact: true })).toBeEnabled()
})

test('connection recovery retries saved cloud settings with a draft and observes a lost operation response', async ({ page }) => {
  test.skip(scenario !== 'cloud-recovery')
  test.setTimeout(45000)
  await login(page)
  await page.getByLabel('Bot Token', { exact: true }).fill('98765:offline-unsaved-candidate-token')
  await expect(page.getByRole('button', { name: '重试连接', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '重试连接', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('当前未保存的输入会保留')
  let writes = 0
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/system/local-api/retry', async route => {
    writes++
    const response = await route.fetch()
    expect(response.status()).toBe(202)
    await gate
    await route.fulfill({ response }).catch(() => {})
  })
  await page.getByRole('button', { name: '使用已保存配置重试', exact: true }).click()
  await expect(page.locator('.el-message').filter({ hasText: '操作可能已受理' })).toBeVisible({ timeout: 20000 })
  await expect(page.locator('.connection-application-notice')).toContainText('正在应用连接配置')
  await expect(page.getByLabel('Bot Token', { exact: true })).toHaveValue('98765:offline-unsaved-candidate-token')
  expect(writes).toBe(1)
  release()
  await page.unroute('**/api/v1/system/local-api/retry')
})

test('leaving settings closes a pending restart confirmation without sending an operation', async ({ page }) => {
  test.skip(scenario !== 'restart')
  await login(page)
  let calls = 0
  page.on('request', request => { if (request.url().endsWith('/local-api/restart')) calls++ })
  await page.getByRole('button', { name: '重启 API 进程', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.evaluate(() => { location.hash = '/tasks' })
  await expect(page.getByRole('link', { name: '下载任务', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(calls).toBe(0)
  await page.getByRole('link', { name: '设置', exact: true }).click()
  await expect(page.getByRole('button', { name: '重启 API 进程', exact: true })).toBeEnabled()
})

test('managed manual restart confirms once and reports accepted drain separately from Bot', async ({ page }) => {
  test.skip(scenario !== 'restart')
  await login(page)
  await expect(page.getByTestId('local-api-status')).toContainText('进程运行中')
  await expect(page.getByTestId('local-api-status')).toContainText('尚未配置')
  await page.getByRole('button', { name: '重启 API 进程', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('WebUI 保持运行')
  const accepted = page.waitForResponse(response => response.url().endsWith('/local-api/restart'))
  await page.getByRole('button', { name: '确认重启', exact: true }).click()
  expect((await accepted).status()).toBe(202)
  await expect(page.getByTestId('local-api-status')).toContainText('正在准备切换连接')
  await expect(page.getByRole('button', { name: '重启 API 进程', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '维护与备份', exact: true }).click()
  await page.locator('.debug-toggle .el-switch').click()
  await page.getByRole('button', { name: '连接 Telegram', exact: true }).click()
  await page.getByText('服务运行详情', { exact: true }).click()
  await expect(page.getByTestId('local-api-status')).toContainText('e2e-process-instance')
  await screenshot(page, 'system-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await screenshot(page, 'system-mobile')
})

test('uncertain logout requires explicit acknowledgement and cancel sends no mutation', async ({ page }) => {
  test.skip(scenario !== 'uncertain')
  await login(page)
  await expect(page.getByTestId('local-api-status')).toContainText('云端退出结果待确认')
  await expect(page.getByRole('button', { name: '重试操作', exact: true })).toHaveCount(0)
  let calls = 0
  page.on('request', request => { if (request.url().endsWith('/local-api/retry')) calls++ })
  await page.getByRole('button', { name: '确认云端已退出并继续', exact: true }).click()
  await page.getByRole('button', { name: '取消', exact: true }).click()
  expect(calls).toBe(0)
  await page.getByRole('button', { name: '确认云端已退出并继续', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('不会再次执行云端退出')
  const accepted = page.waitForResponse(response => response.url().endsWith('/local-api/retry'))
  await page.getByRole('button', { name: '已核实退出，继续本地连接', exact: true }).click()
  const response = await accepted
  expect(response.status()).toBe(202)
  expect(response.request().postDataJSON()).toEqual({ acknowledge_logout_uncertain: true })
  await expect(page.getByTestId('local-api-status')).toContainText('正在准备切换连接')
  expect(calls).toBe(1)
})

test('cooldown is persistent service state with conflicting actions disabled', async ({ page }) => {
  test.skip(scenario !== 'cooldown')
  await login(page)
  await expect(page.getByTestId('local-api-status')).toContainText('等待云端冷却')
  await expect(page.getByTestId('local-api-status')).toContainText('云端最早可用')
  await expect(page.getByRole('button', { name: '重启 API 进程', exact: true })).toBeDisabled()
  await page.setViewportSize({ width: 390, height: 844 })
  await screenshot(page, 'cooldown-mobile')
})

test('external API has no process controls and explicit Hash clear remains available', async ({ page }) => {
  test.skip(!!scenario)
  await login(page)
  await expect(page.getByTestId('local-api-status')).toContainText('外部管理')
  await expect(page.getByRole('button', { name: '重启 API 进程', exact: true })).toHaveCount(0)
  await page.getByLabel('API ID', { exact: true }).fill('123456')
  await page.getByLabel('API Hash', { exact: true }).fill('0123456789abcdef0123456789abcdef')
  await page.getByRole('button', { name: '保存设置', exact: true }).click()
  await expect(page.getByLabel('API Hash', { exact: true })).toHaveAttribute('placeholder', '已配置 · 留空保持不变')
  await page.getByText('清除已保存的 API Hash', { exact: true }).click()
  await expect(page.getByRole('checkbox', { name: '清除已保存的 API Hash', exact: true })).toBeChecked()
  const cleared = page.waitForResponse(response => response.url().endsWith('/settings') && response.request().method() === 'PATCH')
  await page.getByRole('button', { name: '保存设置', exact: true }).click()
  const response = await cleared
  expect(response.request().postDataJSON().clear_telegram_api_hash).toBe(true)
  expect((await response.json()).configured.telegram_api_hash).toBe(false)
  await expect(page.getByLabel('API Hash', { exact: true })).toHaveAttribute('placeholder', '填写 API Hash')
})

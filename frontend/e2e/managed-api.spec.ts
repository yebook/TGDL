import { expect, test, type Page } from '@playwright/test'

const scenario = process.env.E2E_MANAGED_API
async function login(page: Page, view: '设置' | '系统状态' = '设置') {
  await page.goto('/')
  await page.getByLabel('管理员账号', { exact: true }).fill('e2e-admin')
  await page.getByLabel('密码', { exact: true }).fill('local-e2e-only-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('link', { name: view, exact: true }).click()
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
  await expect(page.getByTestId('local-api-status')).toContainText('等待下载与连接安全结束')
  await screenshot(page, 'settings-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await screenshot(page, 'settings-mobile')
})

test('managed manual restart confirms once and reports accepted drain separately from Bot', async ({ page }) => {
  test.skip(scenario !== 'restart')
  await login(page, '系统状态')
  await expect(page.getByTestId('local-api-status')).toContainText('进程运行中')
  await expect(page.getByTestId('local-api-status')).toContainText('Bot 尚未配置')
  await page.getByRole('button', { name: '重启 API 进程', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('WebUI 保持运行')
  const accepted = page.waitForResponse(response => response.url().endsWith('/local-api/restart'))
  await page.getByRole('button', { name: '确认重启', exact: true }).click()
  expect((await accepted).status()).toBe(202)
  await expect(page.getByTestId('local-api-status')).toContainText('等待下载与连接安全结束')
  await expect(page.getByRole('button', { name: '重启 API 进程', exact: true })).toBeDisabled()
  await page.getByText('服务运行详情', { exact: true }).click()
  await expect(page.getByTestId('local-api-status')).toContainText('e2e-process-instance')
  await screenshot(page, 'system-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await screenshot(page, 'system-mobile')
})

test('uncertain logout requires explicit acknowledgement and cancel sends no mutation', async ({ page }) => {
  test.skip(scenario !== 'uncertain')
  await login(page, '系统状态')
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
  await expect(page.getByTestId('local-api-status')).toContainText('等待下载与连接安全结束')
  expect(calls).toBe(1)
})

test('cooldown is persistent service state with conflicting actions disabled', async ({ page }) => {
  test.skip(scenario !== 'cooldown')
  await login(page, '系统状态')
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

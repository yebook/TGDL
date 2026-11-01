import { expect, test, type Locator, type Page } from '@playwright/test'

// Fresh tools.e2e_app with E2E_CACHE_STORAGE=1. Only the private native
// boundary is substituted; REST, CSRF, worker cancellation and cleanup are real.
test.describe.configure({ mode: 'serial' })

async function openSettings(page: Page) {
  await page.goto('/')
  await page.getByLabel('管理员账号', { exact: true }).fill('e2e-admin')
  await page.getByLabel('密码', { exact: true }).fill('local-e2e-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('link', { name: '设置', exact: true }).click()
  await page.getByRole('button', { name: '下载与保存', exact: true }).click()
}
const cleanButton = (panel: Locator) => panel.getByRole('button', { name: /^清理媒体缓存/ })
const refreshButton = (panel: Locator) => panel.getByRole('button', { name: '刷新占用', exact: true })
async function confirmClean(page: Page, panel: Locator) {
  await cleanButton(panel).click()
  await page.getByRole('dialog', { name: '清理媒体缓存', exact: true })
    .getByRole('button', { name: '确认清理', exact: true }).click()
}
function observeActions(page: Page) {
  const counts = { refresh: 0, clean: 0 }
  page.on('request', request => {
    if (request.method() !== 'POST') return
    const path = new URL(request.url()).pathname
    if (path === '/api/v1/system/cache/refresh') counts.refresh++
    if (path === '/api/v1/system/cache/clean') {
      counts.clean++
      expect(request.headers()['x-csrf-token']).toBeTruthy()
    }
  })
  return counts
}

for (const width of [1440, 390]) {
  test(`active getFile allows cache refresh and explains disabled clear at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 600 : 1000 })
    const counts = observeActions(page)
    await openSettings(page)
    const panel = page.getByTestId('cache-storage-panel')
    await expect(panel).toContainText('4.0 GB · 3 个文件')
    await expect(cleanButton(panel)).toBeDisabled()
    await expect(cleanButton(panel)).toHaveAccessibleName(/等待下载和保存完成/)
    await panel.locator('.cache-clean-control').focus()
    await expect(page.getByRole('tooltip')).toContainText('等待下载和保存完成')
    await refreshButton(panel).focus()
    await expect(refreshButton(panel)).toBeEnabled()
    await refreshButton(panel).click()
    await expect.poll(() => counts.refresh).toBe(2)
    await expect(refreshButton(panel)).toBeEnabled()
    await expect(panel).toContainText('4.0 GB · 3 个文件')
    for (const text of ['暂不可用', '统计时间', '缓存盘剩余', '已统计', '以上为上次统计', '未知']) {
      await expect(panel).not.toContainText(text)
    }
    expect(counts.clean).toBe(0)
    await panel.scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`cache-active-${width}.png`), fullPage: true })
  })
}

test('real cancellation settles both native owners and enables idle cleanup', async ({ page }) => {
  await openSettings(page)
  const session = await (await page.request.get('/api/v1/auth/me')).json()
  for (const width of [1440, 390]) {
    const response = await page.request.post(`/api/v1/tasks/cancel-acquisition-${width}/cancel`,
      { headers: { 'X-CSRF-Token': session.csrf_token } })
    expect(response.ok()).toBe(true)
  }
  await expect.poll(async () => {
    const status = await (await page.request.get('/api/v1/system/status')).json()
    return status.storage.cache.maintenance.can_clean
  }).toBe(true)
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(cleanButton(page.getByTestId('cache-storage-panel'))).toBeEnabled()
})

for (const width of [1440, 390]) {
  test(`cache confirmation, actual asynchronous clear and draft preservation at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 600 : 1000 })
    const counts = observeActions(page)
    await openSettings(page)
    const panel = page.getByTestId('cache-storage-panel')
    await expect(cleanButton(panel)).toBeEnabled({ timeout: 10_000 })
    await expect.poll(() => counts.refresh).toBe(1)
    const input = page.getByRole('spinbutton', { name: '确认有效期（分钟）', exact: true })
    await input.fill('4')
    await input.press('Tab')
    const inputValue = await input.inputValue()
    await cleanButton(panel).click()
    const dialog = page.getByRole('dialog', { name: '清理媒体缓存', exact: true })
    await expect(dialog).toContainText('已保存文件和账号数据会保留')
    await expect(dialog).toContainText('未保存设置会保留')
    await dialog.getByRole('button', { name: '取消', exact: true }).click()
    expect(counts.clean).toBe(0)
    await confirmClean(page, panel)
    await expect(panel.getByRole('button', { name: /^清理媒体缓存：正在清理/ })).toBeDisabled()
    await expect(refreshButton(panel)).toBeEnabled()
    await expect(input).toHaveValue(inputValue)
    expect(counts.clean).toBe(1)
    await expect(panel).toContainText('0 B · 0 个文件', { timeout: 10_000 })
    await expect(cleanButton(panel)).toBeEnabled({ timeout: 10_000 })
    await expect(page.locator('.el-message--success')).toContainText('缓存清理完成')
    await expect(panel).not.toContainText('上次清理')
    await expect(input).toHaveValue(inputValue)
    await panel.scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`cache-cleared-${width}.png`), fullPage: true })
    await page.getByRole('button', { name: '连接 Telegram', exact: true }).click()
    await page.getByRole('button', { name: '下载与保存', exact: true }).click()
    expect(counts.refresh).toBe(1)
  })
}

test('unreceived clear is never replayed or unlocked by older idle data', async ({ page }) => {
  const counts = observeActions(page)
  await page.route('**/api/v1/system/cache/clean', route => route.abort('failed'))
  await openSettings(page)
  const panel = page.getByTestId('cache-storage-panel')
  await expect(cleanButton(panel)).toBeEnabled()
  await confirmClean(page, panel)
  await expect(cleanButton(panel)).toBeDisabled()
  await expect(cleanButton(panel)).toHaveAccessibleName(/清理结果尚未确认/)
  await refreshButton(panel).click()
  await expect(refreshButton(panel)).toBeEnabled()
  await expect(cleanButton(panel)).toBeDisabled()
  expect(counts.clean).toBe(1)
})

test('lost accepted clear is reconciled without repeating the real mutation', async ({ page }) => {
  const counts = observeActions(page)
  await page.route('**/api/v1/system/cache/clean', async route => {
    const response = await route.fetch()
    expect(response.status()).toBe(202)
    await route.abort('failed')
  })
  await openSettings(page)
  const panel = page.getByTestId('cache-storage-panel')
  await expect(cleanButton(panel)).toBeEnabled()
  await confirmClean(page, panel)
  await expect(cleanButton(panel)).toHaveAccessibleName(/清理结果尚未确认/)
  await refreshButton(panel).click()
  await expect(cleanButton(panel)).toBeEnabled({ timeout: 10_000 })
  expect(counts.clean).toBe(1)
})

test('failed refresh retains measured bytes and stays retryable', async ({ page }) => {
  await openSettings(page)
  const panel = page.getByTestId('cache-storage-panel')
  await expect(panel).toContainText('0 B · 0 个文件')
  await expect(refreshButton(panel)).toBeEnabled()
  await page.route('**/api/v1/system/cache/refresh', route => route.abort('failed'))
  await refreshButton(panel).click()
  await expect(panel.getByRole('alert')).toContainText('连接失败')
  await expect(panel).toContainText('0 B · 0 个文件')
  await expect(refreshButton(panel)).toBeEnabled()
  await page.unroute('**/api/v1/system/cache/refresh')
  await refreshButton(panel).click()
  await expect(panel.getByRole('alert')).toHaveCount(0)
})

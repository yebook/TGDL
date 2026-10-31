import { expect, test, type Page } from '@playwright/test'
import type { CacheStatus } from '../src/api/types'

function snapshot(): CacheStatus {
  return { free_bytes: 32 * 1024 ** 3, total_bytes: 64 * 1024 ** 3, used_bytes: 4 * 1024 ** 3, file_count: 3, usage_state: 'ready', measured_at: '2026-10-31T14:00:00Z', scope: 'current_bot_media', maintenance: { state: 'idle', operation_id: null, can_clean: true, blocked_reason: null, message: '可清理当前 Bot 的媒体缓存。', last_result: null } }
}
async function openSettings(page: Page) {
  await page.goto('/')
  await page.getByLabel('管理员账号', { exact: true }).fill('e2e-admin')
  await page.getByLabel('密码', { exact: true }).fill('local-e2e-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('link', { name: '设置', exact: true }).click()
  await page.getByRole('button', { name: '下载与保存', exact: true }).click()
}
for (const width of [1440, 390]) {
  test(`cache usage, confirmation, asynchronous release and draft preservation at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 600 : 1000 })
    let cache = snapshot()
    let clears = 0
    let scans = 0
    await page.route('**/api/v1/system/status', async route => {
      const response = await route.fetch()
      const status = await response.json()
      await route.fulfill({ json: { ...status, local_api: { ...status.local_api, applied_mode: 'local', state: 'running', phase: 'idle' }, storage: { ...status.storage, cache } } })
    })
    await page.route('**/api/v1/system/cache/refresh', async route => { scans++; await route.fulfill({ status: 202, json: cache }) })
    await page.route('**/api/v1/system/cache/clean', async route => {
      clears++
      expect(route.request().method()).toBe('POST')
      expect(route.request().headers()['x-csrf-token']).toBeTruthy()
      cache = { ...cache, maintenance: { ...cache.maintenance, state: 'running', operation_id: 'a'.repeat(32), can_clean: false, blocked_reason: 'maintenance_running', message: '正在清理媒体缓存。' } }
      await route.fulfill({ status: 202, json: cache })
    })
    await openSettings(page)
    const panel = page.getByTestId('cache-storage-panel')
    await expect(panel).toContainText('4.0 GB · 3 个文件')
    await expect.poll(() => scans).toBe(1)
    // Edit an ordinary setting; maintenance must neither save nor discard it.
    const input = page.getByRole('spinbutton', { name: '确认有效期（分钟）', exact: true })
    await input.fill('4')
    await input.press('Tab')
    const inputValue = await input.inputValue()
    const clear = panel.getByRole('button', { name: '清理媒体缓存', exact: true })
    await clear.click()
    const dialog = page.getByRole('dialog', { name: '清理媒体缓存', exact: true })
    await expect(dialog).toContainText('已保存文件和账号数据会保留')
    await expect(dialog).toContainText('未保存设置会保留')
    await dialog.getByRole('button', { name: '取消', exact: true }).click()
    expect(clears).toBe(0)
    await clear.click()
    await dialog.getByRole('button', { name: '确认清理', exact: true }).click()
    await expect(panel.getByRole('button', { name: '正在清理', exact: true })).toBeDisabled()
    await expect(input).toHaveValue(inputValue)
    expect(clears).toBe(1)
    cache = { ...cache, used_bytes: 0, file_count: 0, maintenance: { ...cache.maintenance, state: 'succeeded', can_clean: true, blocked_reason: null, message: '媒体缓存清理完成。', last_result: { before_bytes: 4 * 1024 ** 3, after_bytes: 0, released_bytes: 4 * 1024 ** 3 } } }
    await panel.getByRole('button', { name: '刷新占用', exact: true }).click()
    await expect(panel).toContainText('上次清理实际释放 4.0 GB')
    await expect(panel).toContainText('0 B · 0 个文件')
    await expect(clear).toBeEnabled()
    await expect(input).toHaveValue(inputValue)
    await panel.scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: `../.runtime/cache-storage/released-${width}.png`, fullPage: true })
    // Status polling and section switches must not trigger another full scan.
    await page.getByRole('button', { name: '连接 Telegram', exact: true }).click()
    await page.getByRole('button', { name: '下载与保存', exact: true }).click()
    expect(scans).toBe(2)
  })
}
test('unknown clear is not replayed or unlocked by an older idle snapshot', async ({ page }) => {
  let cache = snapshot()
  let clears = 0
  await page.route('**/api/v1/system/status', async route => {
    const response = await route.fetch()
    const status = await response.json()
    await route.fulfill({ json: { ...status, storage: { ...status.storage, cache } } })
  })
  await page.route('**/api/v1/system/cache/refresh', async route => { await route.fulfill({ status: 202, json: cache }) })
  await page.route('**/api/v1/system/cache/clean', async route => { clears++; await route.abort('failed') })
  await openSettings(page)
  const panel = page.getByTestId('cache-storage-panel')
  const clear = panel.getByRole('button', { name: '清理媒体缓存', exact: true })
  await clear.click()
  await page.getByRole('dialog', { name: '清理媒体缓存', exact: true }).getByRole('button', { name: '确认清理', exact: true }).click()
  await expect(panel).toContainText('清理结果尚未确认')
  await panel.getByRole('button', { name: '刷新占用', exact: true }).click()
  await expect(clear).toBeDisabled()
  cache = { ...cache, maintenance: { ...cache.maintenance, state: 'uncertain', operation_id: 'b'.repeat(32), can_clean: false, blocked_reason: 'maintenance_uncertain', message: '请重启本地 API 确认清理状态。' } }
  await panel.getByRole('button', { name: '刷新占用', exact: true }).click()
  await expect(panel).toContainText('请重启本地 API 确认清理状态')
  await expect(clear).toBeDisabled()
  expect(clears).toBe(1)
})
test('unavailable usage stays unknown and backend blockers disable clean', async ({ page }) => {
  const cache = snapshot()
  cache.used_bytes = null; cache.file_count = null; cache.measured_at = null; cache.usage_state = 'unavailable'
  cache.maintenance = { ...cache.maintenance, can_clean: false, blocked_reason: 'pending_verification', message: '有已保存文件等待校验，暂不能清理。' }
  let clears = 0
  await page.route('**/api/v1/system/status', async route => {
    const response = await route.fetch()
    const status = await response.json()
    await route.fulfill({ json: { ...status, storage: { ...status.storage, cache } } })
  })
  await page.route('**/api/v1/system/cache/clean', async route => { clears++; await route.abort() })
  await openSettings(page)
  const panel = page.getByTestId('cache-storage-panel')
  await expect(panel).toContainText('暂不可用')
  await expect(panel).toContainText('有已保存文件等待校验')
  await expect(panel).not.toContainText('0 B')
  await expect(panel.getByRole('button', { name: '清理媒体缓存', exact: true })).toBeDisabled()
  expect(clears).toBe(0)
})
test('failed status refresh preserves measured usage and disables cleanup until resynchronized', async ({ page }) => {
  let failing = false
  const cache = snapshot()
  await page.route('**/api/v1/system/status', async route => {
    if (failing) { await route.fulfill({ status: 503, json: { error: { message: '状态暂不可用。' } } }); return }
    const response = await route.fetch()
    const status = await response.json()
    await route.fulfill({ json: { ...status, storage: { ...status.storage, cache } } })
  })
  await openSettings(page)
  const panel = page.getByTestId('cache-storage-panel')
  const clear = panel.getByRole('button', { name: '清理媒体缓存', exact: true })
  await expect(clear).toBeEnabled()
  failing = true
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(panel).toContainText('状态待更新')
  await expect(panel).toContainText('4.0 GB · 3 个文件')
  await expect(clear).toBeDisabled()
  failing = false
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(clear).toBeEnabled()
})

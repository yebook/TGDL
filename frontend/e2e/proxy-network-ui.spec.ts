import { expect, test } from '@playwright/test'

for (const width of [1440, 390]) {
  test(`proxy acknowledgement and API mode stay distinct without resetting edits at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    let ready = false
    await page.route('**/api/v1/settings', async route => {
      const response = await route.fetch()
      const value = await response.json()
      await route.fulfill({ json: { ...value, values: { ...value.values, bot_api_mode: 'local' } } })
    })
    await page.route('**/api/v1/system/status', async route => {
      const response = await route.fetch()
      const value = await response.json()
      await route.fulfill({ json: { ...value,
        proxy: { state: ready ? 'active' : 'pending', desired_revision: 9, applied_revision: ready ? 9 : null, message: '' },
        local_api: { ...value.local_api, managed: true, state: ready ? 'running' : 'starting', desired_mode: 'local', applied_mode: ready ? 'local' : 'cloud', phase: ready ? 'idle' : 'starting', message: '', can_restart: false, can_retry: false },
      } })
    })
    await page.goto('/')
    await page.getByLabel('管理员账号', { exact: true }).fill('e2e-admin')
    await page.getByLabel('密码', { exact: true }).fill('local-e2e-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.getByRole('link', { name: '设置', exact: true }).click()
    const api = page.getByTestId('local-api-status')
    await expect(api).toContainText('正在启动本地 API')
    await expect(api.locator('.local-api-modes').getByText('官方云端', { exact: true })).toBeVisible()
    await expect(page.locator('.proxy-saved-state')).toContainText('等待应用')
    const time = page.getByLabel('每日自动清理时间', { exact: true })
    await time.fill('09:05')
    ready = true
    await expect(page.locator('.proxy-saved-state .proxy-state-label')).toHaveText('已应用', { timeout: 10000 })
    await expect(time).toHaveValue('09:05')
    await expect(page.locator('.save-bar')).toContainText('有尚未保存的修改')
    await expect(api.locator('.local-api-modes').getByText('本地 API', { exact: true })).toHaveCount(2)
    await expect(page.locator('.proxy-mode-hint')).toContainText('无需额外 Compose 文件')
    await time.scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../.runtime/diagnostic-logs/settings-${width}.png`, fullPage: true })
  })
}

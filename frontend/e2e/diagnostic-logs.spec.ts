import { readFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'

for (const width of [1440, 390]) {
  test(`diagnostic log export is explicit and readable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await page.route('**/api/v1/system/status', async route => {
      const response = await route.fetch()
      const status = await response.json()
      await route.fulfill({ json: { ...status, diagnostic_log: { state: width === 390 ? 'error' : 'ready', message: width === 390 ? '日志写入异常，可尝试导出已有记录。' : '诊断日志已启用。', max_file_bytes: 10485760, backup_count: 3 } } })
    })
    let exports = 0
    const event = { timestamp: '2030-01-01T10:00:00Z', event: 'telegram_request', outcome: 'success', duration_ms: 17 }
    await page.route('**/api/v1/system/logs', async route => {
      exports++
      await new Promise(resolve => setTimeout(resolve, 350))
      await route.fulfill({ json: { events: exports === 1 ? [event] : [], truncated: exports === 1 } })
    })
    await page.goto('/')
    await page.getByLabel('管理员账号', { exact: true }).fill('e2e-admin')
    await page.getByLabel('密码', { exact: true }).fill('local-e2e-only-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.getByRole('link', { name: '系统状态', exact: true }).click()
    const panel = page.getByTestId('diagnostic-log-panel')
    await expect(panel).toContainText(width === 390 ? '记录异常' : '记录中')
    await expect(panel).toContainText('10.0 MB')
    await page.waitForResponse(response => response.url().endsWith('/system/status'))
    expect(exports).toBe(0)
    await panel.scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: `../.runtime/diagnostic-logs/export-${width}.png`, fullPage: true })
    const button = page.getByRole('button', { name: '导出诊断日志', exact: true })
    const downloadPromise = page.waitForEvent('download')
    await button.click()
    await expect(button).toBeDisabled()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/^tgdl-diagnostics-.*\.jsonl$/)
    const path = await download.path()
    expect(path).not.toBeNull()
    expect(JSON.parse((await readFile(path!, 'utf8')).trim())).toEqual(event)
    await expect(page.getByText('诊断日志已导出，部分记录因导出上限或格式异常未包含。', { exact: true })).toBeVisible()
    expect(exports).toBe(1)
    await button.click()
    await expect(page.getByText('暂无可导出的诊断日志。', { exact: true })).toBeVisible()
    expect(exports).toBe(2)
  })
}

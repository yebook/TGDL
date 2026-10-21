import { expect, test, type Page } from '@playwright/test'

const scenario = process.env.E2E_REQUEST_SAFETY
async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('管理员账号', { exact: true }).fill('e2e-admin')
  await page.getByLabel('密码', { exact: true }).fill('local-e2e-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('link', { name: '系统状态', exact: true }).click()
  await expect(page.getByTestId('bot-request-safety')).toBeVisible()
}
async function screenshot(page: Page, name: string) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
    window.scrollTo(0, 0)
  })
  await page.screenshot({ path: `../.runtime/request-safety/${name}.png`, fullPage: true })
}

test('authentication pause is visible and explicit retry uses real guards once', async ({ page }) => {
  test.skip(scenario !== 'auth')
  await login(page)
  const panel = page.getByTestId('bot-request-safety')
  await expect(panel).toContainText('认证异常，已暂停请求')
  await expect(page.getByTestId('local-api-status')).toContainText('认证异常，已暂停请求')
  await screenshot(page, 'auth-desktop')
  await page.setViewportSize({ width: 390, height: 600 })
  await screenshot(page, 'auth-mobile')
  let requests = 0
  page.on('request', request => { if (request.url().endsWith('/system/bot/retry')) requests++ })
  const response = page.waitForResponse(response => response.url().endsWith('/system/bot/retry'))
  await page.getByRole('button', { name: '重试 Bot 连接', exact: true }).click()
  await expect(page.getByRole('button', { name: '重试 Bot 连接', exact: true })).toBeDisabled()
  const result = await response
  expect(result.ok()).toBe(true)
  expect((await result.json()).state).toBe('ready')
  await expect(panel).toHaveCount(0)
  await expect(page.getByText('恢复请求已受理，请查看连接状态。', { exact: true })).toBeVisible()
  expect(requests).toBe(1)
  await page.reload()
  await expect(panel).toHaveCount(0)
})

test('server wait remains visible after reload and cannot be bypassed by retry', async ({ page }) => {
  test.skip(scenario !== 'throttled')
  await login(page)
  await expect(page.getByTestId('bot-request-safety')).toContainText('请求限流，等待恢复')
  await expect(page.getByTestId('bot-request-safety')).toContainText('最早重试时间：')
  await expect(page.getByRole('button', { name: '重试 Bot 连接', exact: true })).toHaveCount(0)
  await page.setViewportSize({ width: 390, height: 600 })
  await screenshot(page, 'wait-mobile')
  await page.reload()
  await expect(page.getByTestId('bot-request-safety')).toContainText('最早重试时间：')
  await expect(page.getByRole('button', { name: '重试 Bot 连接', exact: true })).toHaveCount(0)
})

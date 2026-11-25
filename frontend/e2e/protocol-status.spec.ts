import { expect, test } from '@playwright/test'

test.skip(process.env.E2E_MANAGED_API !== 'protocol', 'Requires the offline protocol recovery fixture')

test('one successful handshake recovers the summary via shared refresh and preserves diagnostic detail', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 700 })
  await page.goto('/')
  await page.getByLabel('管理员账号', { exact: true }).fill('e2e-admin')
  await page.getByLabel('密码', { exact: true }).fill('local-e2e-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  const api = page.getByTestId('top-api-status')
  const bot = page.getByTestId('top-bot-status')
  const refresh = page.getByRole('button', { name: '刷新状态', exact: true })
  await expect(api).toContainText('协议待确认')
  await refresh.click()
  await expect(api).toContainText('协议超时')
  await expect(bot).toContainText('上游异常')
  await expect(page.getByTestId('top-status')).toContainText('Telegram 协议复查已完成。')
  await page.getByRole('link', { name: '设置', exact: true }).click()

  const panel = page.getByTestId('network-test')
  const detect = panel.getByRole('button', { name: '检测网络连通性', exact: true })
  await expect(detect).toBeEnabled()
  const networkTest = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/system/network-test')
  const statusRefresh = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/system/status' && !new URL(response.url()).search)
  await detect.click()
  expect((await (await networkTest).json()).status_protocol_updated).toBe(true)
  expect((await (await statusRefresh).json()).local_api.telegram_protocol).toBe('partial')
  await expect(panel.getByTestId('telegram-protocol')).toContainText('1/4 握手通过')
  await expect(api).toContainText('协议可达')
  await expect(bot).toContainText('已就绪')

  await refresh.click()
  await expect(page.getByTestId('top-status')).toContainText('沿用仍有效的近期结果')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  // A previous server's empty 204 must not be shown as new probe evidence.
  await page.route('**/api/v1/system/protocol-refresh', route => route.fulfill({ status: 204 }))
  const legacyStatusRefresh = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/system/status' && !new URL(response.url()).search)
  await refresh.click()
  await legacyStatusRefresh
  await expect(page.getByTestId('top-status')).toContainText('本次未取得可用于当前 API 的新协议结果')
})

import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('管理员账号', { exact: true }).fill('e2e-admin')
  await page.getByLabel('密码', { exact: true }).fill('local-e2e-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByLabel('搜索名称或任务编号')).toBeVisible()
}
async function chooseAction(page: Page, label: string) {
  const detail = page.locator('.detail-view')
  await detail.getByRole('button', { name: '任务操作', exact: true }).click()
  await page.getByRole('menuitem', { name: label, exact: true }).click()
}

for (const width of [1440, 390]) {
  test(`任务进行中只显示任务级取消操作 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await login(page)
    const id = `cancel-acquisition-${width}`
    const apiPath = `/api/v1/tasks/${id}`
    await page.getByLabel('搜索名称或任务编号').fill(id)
    await page.getByRole('button', { name: '搜索', exact: true }).click()
    await expect(page.locator('.task-row')).toHaveCount(1)
    await page.locator('.task-row .task-name').click()
    const detail = page.locator('.detail-view')
    await expect(detail.getByRole('button', { name: '任务操作', exact: true })).toBeVisible()
    await expect(detail.locator('.cache-summary')).toHaveCount(0)
    await detail.getByRole('button', { name: '任务操作', exact: true }).click()
    await expect(page.getByRole('menuitem', { name: '取消', exact: true })).toBeVisible()
    await page.getByRole('menuitem', { name: '取消', exact: true }).click()
    const confirmation = page.locator('.el-message-box')
    await expect(confirmation).toContainText('任务将安全停止')
    await confirmation.getByRole('button', { name: '暂不操作', exact: true }).click()
    await chooseAction(page, '取消')
    const submitted = page.waitForResponse(response => new URL(response.url()).pathname === `${apiPath}/cancel`
      && response.request().method() === 'POST')
    await confirmation.getByRole('button', { name: '确认取消', exact: true }).click()
    expect((await submitted).ok()).toBe(true)
    await expect(detail.getByRole('button', { name: '任务操作', exact: true })).toHaveCount(0)
    expect((await (await page.request.get(apiPath)).json()).can_cancel).toBe(false)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })

  test(`取消清理只保留紧凑任务级反馈 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await login(page)
    const id = `cancel-unverified-${width}`
    const apiPath = `/api/v1/tasks/${id}`
    await page.getByLabel('搜索名称或任务编号').fill(`Unverified cancellation fixture ${width}`)
    await page.getByRole('button', { name: '搜索', exact: true }).click()
    await expect(page.locator('.task-row')).toHaveCount(1)
    await page.locator('.task-row .task-name').click()
    const detail = page.locator('.detail-view')
    await chooseAction(page, '取消')
    await page.locator('.el-message-box').getByRole('button', { name: '确认取消', exact: true }).click()
    await expect.poll(async () => (await (await page.request.get(apiPath)).json()).cancel_cleanup?.state).toBe('partial')
    await expect(detail.locator('.el-alert')).toHaveCount(1)
    await expect(detail.locator('.attempts')).toHaveCount(0)
    await expect(detail.locator('.item-cache')).toHaveCount(0)
    expect((await (await page.request.get(apiPath)).json()).can_retry).toBe(false)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })
}

import { expect, test, type Page } from '@playwright/test'

// Actual REST/CSRF/service/DB and disposable media. Route interception below
// only drops a real committed response; it never fabricates a successful API.
test.describe.configure({ mode: 'serial' })
const dialog = (page: Page) => page.getByRole('dialog', { name: '删除任务', exact: true })
async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('管理员账号', { exact: true }).fill('e2e-admin')
  await page.getByLabel('密码', { exact: true }).fill('local-e2e-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByLabel('搜索名称或任务编号')).toBeVisible()
}
async function search(page: Page, value: string) {
  await page.getByLabel('搜索名称或任务编号').fill(value)
  await page.getByRole('button', { name: '搜索', exact: true }).click()
  await expect(page.locator('.task-row')).toHaveCount(1)
}
async function chooseDetailAction(page: Page, label: string) {
  await page.locator('.detail-view').getByRole('button', { name: '任务操作', exact: true }).click()
  await page.getByRole('menuitem', { name: label, exact: true }).click()
}
async function filePresence(page: Page, id: string) {
  const response = await page.request.get(`/__e2e__/task-files/${id}`)
  expect(response.ok()).toBe(true)
  return (await response.json()).present
}
async function confirm(page: Page, deleteFiles = false) {
  const panel = dialog(page)
  await expect(panel.getByRole('checkbox', { name: '同时删除已下载文件' })).not.toBeChecked()
  if (deleteFiles) {
    await panel.getByRole('checkbox').focus()
    await page.keyboard.press('Space')
    await expect(panel).toContainText('文件删除后无法恢复')
  }
  await panel.getByRole('button', { name: '确认删除', exact: true }).click()
}

for (const width of [1440, 390]) {
  test(`external removal/restoration updates list/detail without changing succeeded history ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 600 : 1000 })
    await login(page)
    const id = `files-keep-${width}`
    await search(page, id)
    await page.locator('.task-name').click()
    await expect(page.locator('.detail-heading')).toContainText('已完成')
    await expect(page.locator('.media-card')).toContainText('本地文件存在')
    expect((await page.request.post(`/__e2e__/task-files/${id}/remove`)).ok()).toBe(true)
    await expect(page.locator('.task-row')).toContainText('本地文件不存在')
    await expect(page.locator('.media-card')).toContainText('本地文件不存在')
    await expect(page.locator('.detail-heading')).toContainText('已完成')
    expect((await page.request.post(`/__e2e__/task-files/${id}/restore`)).ok()).toBe(true)
    await expect(page.locator('.media-card')).toContainText('本地文件存在')
    await expect(page.locator('.task-row')).not.toContainText('本地文件不存在')
    await chooseDetailAction(page, '删除')
    await expect(dialog(page).getByRole('checkbox')).not.toBeChecked()
    await dialog(page).getByRole('button', { name: '保留任务', exact: true }).click()
    expect((await page.request.get(`/api/v1/tasks/${id}`)).status()).toBe(200)
    await chooseDetailAction(page, '删除')
    await expect.poll(() => dialog(page).evaluate(element => `${getComputedStyle(element).opacity}|${getComputedStyle(element).transform}`)).toBe('1|none')
    await page.screenshot({ path: testInfo.outputPath(`delete-confirm-${width}.png`), fullPage: true })
    await confirm(page)
    await expect(dialog(page)).not.toBeVisible()
    expect(await filePresence(page, id)).toEqual([true])
    await expect(page.locator('.el-message').last()).toContainText('文件已保留')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })

  test(`explicit file removal and shared-file retention ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 600 : 1000 })
    await login(page)
    const id = `files-delete-${width}`
    await search(page, id)
    await page.locator('.task-delete').click()
    await confirm(page, true)
    await expect(dialog(page)).not.toBeVisible()
    expect(await filePresence(page, id)).toEqual([false])
    await expect(page.locator('.el-message').last()).toContainText('已删除 1 个文件')
    const shared = `files-shared-a-${width}`
    await search(page, shared)
    await page.locator('.task-delete').click()
    await confirm(page, true)
    await expect(dialog(page)).not.toBeVisible()
    await expect(page.locator('.el-message--warning').last()).toContainText('仍被其他任务引用，已保留')
    expect(await filePresence(page, shared)).toEqual([true])
    await search(page, `files-shared-b-${width}`)
    await page.locator('.task-name').click()
    await expect(page.locator('.media-card')).toContainText('本地文件存在')
  })

  test(`batch all shared references deletes physical file once ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 600 : 1000 })
    await login(page)
    await page.getByLabel('搜索名称或任务编号').fill('files-batch-shared-')
    await page.getByRole('button', { name: '搜索', exact: true }).click()
    for (const suffix of ['c', 'd']) {
      await page.getByRole('checkbox', { name: `选择任务 files-batch-shared-${suffix}-${width}`, exact: true }).focus()
      await page.keyboard.press('Space')
    }
    await page.getByRole('button', { name: '删除所选', exact: true }).click()
    await expect(dialog(page)).toContainText('将删除 2 条任务记录')
    await confirm(page, true)
    await expect(dialog(page)).not.toBeVisible()
    await expect(page.locator('.el-message').last()).toContainText('已删除 1 个文件')
    expect(await filePresence(page, `files-batch-shared-c-${width}`)).toEqual([false])
  })

  test(`partial missing, changed and unavailable are separate local states ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 600 : 1000 })
    await login(page)
    await search(page, `files-mixed-${width}`)
    await expect(page.locator('.task-row')).toContainText('1 个本地文件不存在')
    await page.locator('.task-name').click()
    await expect(page.locator('.media-card').filter({ hasText: '本地文件不存在' })).toHaveCount(1)
    await expect(page.locator('.media-card').filter({ hasText: '本地文件存在' })).toHaveCount(1)
    await expect.poll(() => page.locator('.el-drawer').evaluate(element => `${getComputedStyle(element).opacity}|${getComputedStyle(element).transform}`)).toBe('1|none')
    await page.locator('.media-card').last().scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath(`local-files-${width}.png`), fullPage: true })
    await page.keyboard.press('Escape')
    await search(page, `files-unavailable-${width}`)
    await expect(page.locator('.task-row')).toContainText('暂时无法检查本地文件')
    await expect(page.locator('.task-row')).not.toContainText('不存在')
    await search(page, `files-changed-${width}`)
    await expect(page.locator('.task-row')).toContainText('本地文件已变化')
    await page.locator('.task-delete').click()
    await confirm(page, true)
    await expect(dialog(page)).not.toBeVisible()
    await expect(page.locator('.el-message--warning').last()).toContainText('未通过安全检查，已保留')
    expect(await filePresence(page, `files-changed-${width}`)).toEqual([true])
  })

  test(`lost committed reply uses identical operation after reload ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 600 : 1000 })
    await login(page)
    const id = `files-lost-${width}`
    const bodies: unknown[] = []
    page.on('request', request => {
      if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/tasks/delete') {
        bodies.push(request.postDataJSON())
        expect(request.headers()['x-csrf-token']).toBeTruthy()
      }
    })
    await page.route('**/api/v1/tasks/delete', async route => {
      const response = await route.fetch()
      expect(response.ok()).toBe(true)
      await route.abort('failed')
    })
    await search(page, id)
    await page.locator('.task-delete').click()
    await confirm(page, true)
    await expect(dialog(page).getByRole('alert')).toContainText('删除结果尚未确认')
    await expect(dialog(page).getByRole('checkbox')).toBeDisabled()
    await dialog(page).getByRole('button', { name: '稍后确认', exact: true }).click()
    await expect(dialog(page)).not.toBeVisible()
    await expect(page.getByRole('button', { name: '确认删除结果', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '确认删除结果', exact: true }).click()
    await expect(dialog(page).getByRole('checkbox')).toBeDisabled()
    expect(await filePresence(page, id)).toEqual([false])
    await page.unroute('**/api/v1/tasks/delete')
    // A real CSRF rejection of the retry cannot prove the earlier accepted
    // operation never committed, or clear its stable recovery identity.
    await page.route('**/api/v1/tasks/delete', route => route.continue({
      headers: { ...route.request().headers(), 'x-csrf-token': 'invalid-e2e-csrf-token' },
    }))
    const rejectedRetry = page.waitForResponse(response => response.url().endsWith('/api/v1/tasks/delete') && response.status() === 403)
    await dialog(page).getByRole('button', { name: '重试确认结果', exact: true }).click()
    await rejectedRetry
    await expect(dialog(page).getByRole('alert')).toContainText('删除结果尚未确认')
    await expect(dialog(page).getByRole('checkbox')).toBeDisabled()
    await page.unroute('**/api/v1/tasks/delete')
    await page.reload()
    await expect(dialog(page)).toContainText('上一次删除结果尚未确认')
    await dialog(page).getByRole('button', { name: '重试确认结果', exact: true }).click()
    await expect(dialog(page)).not.toBeVisible()
    expect(bodies).toHaveLength(3)
    expect(bodies[1]).toEqual(bodies[0])
    expect(bodies[2]).toEqual(bodies[0])
    expect(await filePresence(page, id)).toEqual([false])
  })
}

import { expect, test, type Page } from '@playwright/test'
import type { TaskDetail } from '../src/api/types'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('管理员账号', { exact: true }).fill('e2e-admin')
  await page.getByLabel('密码', { exact: true }).fill('local-e2e-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByLabel('搜索名称或任务编号')).toBeVisible()
}
async function open(page: Page, query: string) {
  await page.getByLabel('搜索名称或任务编号').fill(query)
  await page.getByRole('button', { name: '搜索', exact: true }).click()
  await expect(page.locator('.task-row')).toHaveCount(1)
  await page.locator('.task-name').click()
  await expect(page.locator('.detail-heading')).toBeVisible()
}
async function detail(page: Page, id: string): Promise<TaskDetail> {
  const response = await page.request.get(`/api/v1/tasks/${id}`)
  expect(response.ok()).toBe(true)
  return response.json()
}

for (const width of [1440, 390]) {
  test(`最终态缓存只显示去重后的摘要 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 700 : 1000 })
    await login(page)
    const id = `cache-complete-${width}`
    await open(page, id)
    const summary = page.locator('.cache-summary')
    await expect(summary).toBeVisible()
    await expect(page.locator('.item-cache')).toHaveCount(0)
    await expect(summary).toContainText('缓存')
    await expect(summary.locator('.cache-path')).toHaveCount(1)
    await expect(page.getByRole('button', { name: '继续保存', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '清理缓存', exact: true })).toHaveCount(0)
    const data = await detail(page, id)
    expect(data.items[0]!.cache!.path).not.toContain('offline-e2e-only-bot-token')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    const path = summary.locator('.cache-path')
    expect(await path.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  })

  test(`异常可恢复任务显示恢复和删除操作 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 700 : 1000 })
    await login(page)
    await open(page, `cache-restart-${width}`)
    const detailView = page.locator('.detail-view')
    await detailView.getByRole('button', { name: '任务操作', exact: true }).click()
    await expect(page.getByRole('menuitem', { name: '恢复', exact: true })).toBeVisible()
    await expect(page.getByRole('menuitem', { name: '删除', exact: true })).toBeVisible()
    await expect(page.locator('.item-cache')).toHaveCount(0)
    await expect(page.locator('.detail-view').getByRole('button', { name: '清理缓存', exact: true })).toHaveCount(0)
    await expect(page.locator('.detail-view').getByRole('button', { name: '继续下载', exact: true })).toHaveCount(0)
  })
}

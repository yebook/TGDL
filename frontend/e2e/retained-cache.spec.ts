import { expect, test, type Page } from '@playwright/test'
import type { TaskDetail } from '../src/api/types'

// Only the native/Telegram boundary uses synthetic data. Browser requests,
// CSRF, durable cache operations and task refreshes use the isolated real API.
test.describe.configure({ mode: 'serial' })
async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('管理员账号', { exact: true }).fill('e2e-admin')
  await page.getByLabel('密码', { exact: true }).fill('local-e2e-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByLabel('搜索名称或任务编号')).toBeVisible()
}
async function open(page: Page, id: string) {
  await page.getByLabel('搜索名称或任务编号').fill(id)
  await page.getByRole('button', { name: '搜索', exact: true }).click()
  await expect(page.locator('.task-row')).toHaveCount(1)
  await page.locator('.task-name').click()
  await expect(page.locator('.item-cache')).toBeVisible()
}
async function detail(page: Page, id: string): Promise<TaskDetail> {
  const response = await page.request.get(`/api/v1/tasks/${id}`)
  expect(response.ok()).toBe(true)
  return response.json()
}
async function physical(page: Page, id: string) {
  const response = await page.request.get(`/__e2e__/retained-cache/${id}`)
  expect(response.ok()).toBe(true)
  return response.json()
}
async function geometry(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const panel = page.locator('.item-cache').first()
  const box = await panel.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1)
  const path = panel.locator('.cache-path')
  if (await path.count()) expect(await path.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
}

for (const width of [1440, 390]) {
  test(`retained complete cache checks space then saves without downloading ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 700 : 1000 })
    await login(page)
    const id = `cache-complete-${width}`
    await open(page, id)
    const panel = page.locator('.item-cache').first()
    await expect(panel).toContainText('完整缓存已保留')
    await expect(panel).toContainText('实际占用')
    const initial = await detail(page, id)
    const initialPhysical = await physical(page, id)
    expect(initial.items[0]!.cache!.path).not.toContain('offline-e2e-only-bot-token')
    await panel.getByRole('button', { name: '继续保存', exact: true }).click()
    await expect.poll(async () => (await detail(page, id)).items[0]!.cache?.operation?.state).toBe('failed')
    await expect(page.locator('.storage-failure')).toContainText('保存前空间检查')
    await expect(page.locator('.storage-failure')).toContainText('并发预留')
    expect((await physical(page, id)).source_exists).toBe(true)
    expect((await physical(page, id)).final_exists).toBe(false)
    expect((await page.request.post(`/__e2e__/retained-cache/${id}/space`)).ok()).toBe(true)
    await expect(panel.getByRole('button', { name: '继续保存', exact: true })).toBeEnabled()
    await panel.getByRole('button', { name: '继续保存', exact: true }).click()
    await expect.poll(async () => (await detail(page, id)).items[0]!.status).toBe('succeeded')
    await expect(page.locator('.detail-heading')).toContainText('已完成')
    const saved = await physical(page, id)
    expect(saved.final_exists).toBe(true)
    expect(saved.get_file_calls).toBe(initialPhysical.get_file_calls)
    await geometry(page)
    await panel.locator('.item-cache-actions').scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath(`cache-save-${width}.png`), fullPage: true })
  })

  test(`partial download can resume and cancellation deletes its retained cache ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 700 : 1000 })
    await login(page)
    const id = `cache-partial-${width}`
    await open(page, id)
    const panel = page.locator('.item-cache').first()
    await expect(panel).toContainText('下载未完成，缓存已保留')
    await expect(panel.getByRole('button', { name: '继续保存', exact: true })).toHaveCount(0)
    await panel.getByRole('button', { name: '继续下载', exact: true }).click()
    await expect(page.locator('.detail-view').getByRole('button', { name: '取消并删除文件', exact: true })).toBeVisible()
    await page.locator('.detail-view').getByRole('button', { name: '取消并删除文件', exact: true }).click()
    const confirmation = page.locator('.el-message-box')
    await expect(confirmation).toContainText('删除关联缓存、目标临时文件和已保存文件')
    await confirmation.getByRole('button', { name: '暂不操作', exact: true }).click()
    await page.locator('.detail-view').getByRole('button', { name: '取消并删除文件', exact: true }).click()
    await confirmation.getByRole('button', { name: '确认取消并删除', exact: true }).click()
    await expect.poll(async () => (await detail(page, id)).items[0]!.status).toBe('cancelled')
    await expect.poll(async () => (await detail(page, id)).cancel_cleanup?.state).toBe('complete')
    expect((await physical(page, id)).source_exists).toBe(false)
    await expect(panel).toContainText('缓存已不存在')
    await expect(panel.getByRole('button', { name: '继续下载', exact: true })).toBeDisabled()
    await expect(panel.getByRole('button', { name: '重新下载（从头开始）', exact: true })).toBeDisabled()
    await geometry(page)
    await panel.locator('.item-cache-actions').scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath(`cache-cancel-${width}.png`), fullPage: true })
    await page.reload()
    await open(page, id)
    await expect(page.locator('.item-cache').first()).toContainText('缓存已不存在')
    expect((await physical(page, id)).source_exists).toBe(false)
  })

  test(`restart unavailable resume offers explicit new download and keeps old cache ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 700 : 1000 })
    await login(page)
    const id = `cache-restart-${width}`
    await open(page, id)
    const panel = page.locator('.item-cache').first()
    await expect(panel.getByRole('button', { name: '继续下载', exact: true })).toBeDisabled()
    await expect(panel).toContainText('重启')
    const initial = await detail(page, id)
    const cacheId = initial.items[0]!.cache!.id
    const before = await physical(page, id)
    await panel.getByRole('button', { name: '重新下载（从头开始）', exact: true }).click()
    const confirmation = page.locator('.el-message-box')
    await expect(confirmation).toContainText('旧缓存保留')
    await confirmation.getByRole('button', { name: '暂不操作', exact: true }).click()
    expect((await physical(page, id)).attempt_sequence).toBe(before.attempt_sequence)
    await panel.getByRole('button', { name: '重新下载（从头开始）', exact: true }).click()
    await confirmation.getByRole('button', { name: '确认重新下载', exact: true }).click()
    await expect.poll(async () => (await physical(page, id)).attempt_sequence).toBeGreaterThan(before.attempt_sequence)
    await expect.poll(async () => (await detail(page, id)).items[0]!.status).toBe('succeeded')
    await expect(page.locator('.detail-heading')).toContainText('已完成')
    const result = await detail(page, id)
    expect(result.items[0]!.caches!.some(cache => cache.id === cacheId && cache.state === 'retained')).toBe(true)
    expect((await physical(page, id)).source_exists).toBe(true)
    await geometry(page)
    await panel.locator('.item-cache-actions').scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath(`cache-restart-${width}.png`), fullPage: true })
  })

  test(`clean confirmation can be dismissed before submission and final files remain ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 700 : 1000 })
    await login(page)
    const id = `cache-clean-${width}`
    await open(page, id)
    const panel = page.locator('.item-cache').first()
    const before = await physical(page, id)
    await panel.getByRole('button', { name: '清理缓存', exact: true }).click()
    const confirmation = page.locator('.el-message-box')
    await confirmation.getByRole('button', { name: '暂不操作', exact: true }).click()
    expect((await physical(page, id)).source_exists).toBe(true)
    expect((await physical(page, id)).actions).toBe(before.actions)
    await panel.getByRole('button', { name: '清理缓存', exact: true }).click()
    await confirmation.getByRole('button', { name: '确认清理', exact: true }).click()
    await expect.poll(async () => (await physical(page, id)).source_exists).toBe(false)
    expect((await physical(page, id)).final_exists).toBe(before.final_exists)
    await expect(panel).toContainText('缓存已不存在')
    await page.reload()
    await open(page, id)
    await expect(page.locator('.item-cache').first()).toContainText('缓存已不存在')
  })

  test(`clean confirms exact file and reconciles a dropped committed response ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 700 : 1000 })
    await login(page)
    const id = `cache-lost-${width}`
    await open(page, id)
    const panel = page.locator('.item-cache').first()
    const before = await physical(page, id)
    const data = await detail(page, id)
    const itemId = data.items[0]!.id
    let count = 0
    const path = `**/api/v1/tasks/${id}/items/${itemId}/cache/clean`
    await page.route(path, async route => {
      count++
      const response = await route.fetch()
      expect(response.ok()).toBe(true)
      await route.abort('failed')
    })
    await panel.getByRole('button', { name: '清理缓存', exact: true }).click()
    const confirmation = page.locator('.el-message-box')
    await expect(confirmation).toContainText('提交后不能通过取消任务撤回')
    await confirmation.getByRole('button', { name: '确认清理', exact: true }).click()
    await expect.poll(async () => (await physical(page, id)).source_exists).toBe(false)
    await expect(panel).toContainText('缓存已不存在')
    expect((await physical(page, id)).final_exists).toBe(before.final_exists)
    expect(count).toBe(1)
    await geometry(page)
  })
}

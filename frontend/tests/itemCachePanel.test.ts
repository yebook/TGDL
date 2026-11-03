import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, createSSRApp, defineComponent, h, nextTick, reactive, ssrContextKey, type App } from 'vue'
import { renderToString } from 'vue/server-renderer'
import ItemCachePanel from '../src/components/ItemCachePanel.vue'
import type { ItemCache } from '../src/api/types'
import { ApiError } from '../src/api/client'

const mocks = vi.hoisted(() => ({ request: vi.fn(), message: Object.assign(vi.fn(), { info: vi.fn() }), confirm: vi.fn(), close: vi.fn() }))
vi.mock('../src/api/boundedRequest', () => ({ boundedRequest: mocks.request }))
vi.mock('element-plus', () => ({ ElMessage: mocks.message, ElMessageBox: { confirm: mocks.confirm, close: mocks.close } }))

interface Node { text: string; parent: Node | null; children: Node[]; props: Record<string, unknown> }
const node = (text = ''): Node => ({ text, parent: null, children: [], props: {} })
const renderer = createRenderer<Node, Node>({
  createElement: () => node(), createText: node, createComment: () => node(),
  setText: (target, value) => { target.text = value },
  setElementText: (target, value) => { target.text = value; target.children = [] },
  patchProp: (target, key, _old, value) => { target.props[key] = value },
  insert: (child, parent, anchor = null) => {
    if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
  },
  remove: child => { child.parent?.children.splice(child.parent.children.indexOf(child), 1); child.parent = null },
  parentNode: child => child.parent,
  nextSibling: child => child.parent?.children[child.parent.children.indexOf(child) + 1] ?? null,
})
const apps: App[] = []
afterEach(() => { apps.splice(0).forEach(app => app.unmount()); vi.resetAllMocks(); vi.unstubAllGlobals() })
function cache(overrides: Partial<ItemCache> = {}): ItemCache {
  return { id: 'cache-1', state: 'retained', completeness: 'complete', path: '/cache/[redacted]/file.bin', size_bytes: 1024,
    allocated_bytes: 4096, can_clean: true, can_save: true, can_resume_download: false, can_redownload: false,
    unavailable_reason: null, resume_unavailable_reason: null, recovery_guidance: null, operation: null, ...overrides }
}
async function markup(value: ItemCache, saved = false) {
  const app = createSSRApp(ItemCachePanel, { taskId: 'task-1', itemId: 'item-1', cache: value, saved })
  app.component('el-button', defineComponent({ setup: (_props, { slots }) => () => h('button', slots.default?.()) }))
  app.config.warnHandler = () => {}
  return renderToString(app)
}
function mount(value = cache(), kind = 'save') {
  const props = reactive({ taskId: 'task-1', itemId: 'item-1', cache: value })
  const changed = vi.fn()
  // Use the real setup/watchers; this Node host has no ElementPlus DOM runtime.
  // The public submit command is connected to the test button below.
  const component = { ...ItemCachePanel, render(_ctx: unknown, _cache: unknown, _props: unknown, setup: { act: (kind: string) => void }) {
    return h('button', { onClick: () => setup.act(kind) })
  } } as unknown as typeof ItemCachePanel
  const root = node()
  const app = renderer.createApp({ render: () => h(component, { ...props, onChanged: changed }) })
  app.config.warnHandler = () => {}
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount(root)
  apps.push(app)
  const click = () => (root.children[0]!.props.onClick as () => void)()
  return { props, click, changed }
}
async function flush() { await Promise.resolve(); await Promise.resolve(); await nextTick() }

describe('per-file retained cache actions', () => {
  it('submits cache actions over ordinary LAN HTTP without randomUUID', async () => {
    vi.stubGlobal('crypto', { getRandomValues: (target: Uint8Array) => { target.fill(171); return target } })
    mocks.request.mockResolvedValue({ items: [] })
    const { click } = mount()
    click(); await flush()
    expect(mocks.request).toHaveBeenCalledOnce()
    expect(JSON.parse(mocks.request.mock.calls[0]![1].body).operation_id).toBe('ab'.repeat(16))
  })

  it('distinguishes complete, partial and missing caches without promising partial saving', async () => {
    expect(await markup(cache())).toMatch(/完整缓存已保留[\s\S]*实际占用 4.0 KB[\s\S]*继续保存/)
    const partial = await markup(cache({ completeness: 'partial', can_save: false, can_resume_download: false,
      can_redownload: true, resume_unavailable_reason: 'Bot API 已重启，无法继续使用原下载进度。' }))
    expect(partial).toContain('下载未完成，缓存已保留')
    expect(partial).toContain('Bot API 已重启')
    expect(partial).toContain('重新下载（从头开始）')
    expect(partial).not.toContain('继续保存')
    expect(await markup(cache({ state: 'missing', completeness: 'partial' }))).toContain('缓存已不存在')
  })

  it('does not describe an already saved file as awaiting target saving', async () => {
    expect(await markup(cache(), true)).not.toContain('尚未完成目标保存')
  })

  it('offers explicit redownload when a complete source becomes unverifiable', async () => {
    const html = await markup(cache({ state: 'uncertain', can_save: false, can_clean: false, can_redownload: true }))
    expect(html).toContain('重新下载（从头开始）')
    expect(html).not.toContain('>继续保存<')
    expect(html).not.toContain('>继续下载<')
  })

  it('does not let an old idle snapshot re-enable an operation with a lost response', async () => {
    mocks.request.mockRejectedValue(new ApiError('连接失败', 0, 'network_error'))
    const { props, click } = mount()
    click(); await flush()
    expect(mocks.request).toHaveBeenCalledOnce()
    const request = mocks.request.mock.calls[0]!
    const body = JSON.parse(request[1].body)
    expect(body).toEqual({ operation_id: expect.stringMatching(/^[a-f0-9]{32}$/), cache_id: 'cache-1' })
    props.cache = { ...props.cache }
    await nextTick(); click(); await flush()
    expect(mocks.request).toHaveBeenCalledOnce()
    props.cache = { ...props.cache, operation: { id: body.operation_id, kind: 'save', state: 'failed', message: '目标盘空间不足，完整缓存保留' } }
    await nextTick()
    mocks.request.mockResolvedValue({ items: [] })
    click(); await flush()
    expect(mocks.request).toHaveBeenCalledTimes(2)
    expect(mocks.message).toHaveBeenCalledWith(expect.objectContaining({ message: '目标盘空间不足，完整缓存保留' }))
  })

  it('ignores a late old-item result when task identity changes and allows a new attempt', async () => {
    let resolve!: (value: unknown) => void
    mocks.request.mockReturnValueOnce(new Promise(done => { resolve = done }))
    const { props, click, changed } = mount()
    click(); await flush()
    props.taskId = 'task-2'
    props.itemId = 'item-2'
    props.cache = cache({ id: 'cache-2' })
    await nextTick()
    resolve({ items: [] }); await flush()
    expect(changed).not.toHaveBeenCalled()
    mocks.request.mockResolvedValue({ items: [] })
    click(); await flush()
    expect(mocks.request).toHaveBeenLastCalledWith('/tasks/task-2/items/item-2/cache/save', expect.anything())
    expect(changed).toHaveBeenCalledOnce()
  })

  it('rechecks the authoritative capability after cleaning confirmation and never submits twice', async () => {
    let confirm!: () => void
    mocks.confirm.mockReturnValue(new Promise<void>(done => { confirm = done }))
    const { props, click } = mount(cache(), 'clean')
    click(); click(); await flush()
    expect(mocks.confirm).toHaveBeenCalledOnce()
    expect(mocks.confirm.mock.calls[0]![0]).toContain('提交后不能通过取消任务撤回')
    props.cache = { ...props.cache, can_clean: false, unavailable_reason: '缓存仍由其他任务使用' }
    await nextTick()
    confirm(); await flush()
    expect(mocks.request).not.toHaveBeenCalled()
  })

  it.each(['partial', 'complete'] as const)('confirms %s cache fallback and retains the selected identity', async completeness => {
    mocks.confirm.mockResolvedValue(undefined)
    mocks.request.mockResolvedValue({ items: [] })
    const { click } = mount(cache({ completeness, state: completeness === 'complete' ? 'uncertain' : 'retained',
      can_save: false, can_redownload: true }), 'redownload')
    click(); click(); await flush()
    expect(mocks.request).toHaveBeenCalledOnce()
    expect(mocks.confirm.mock.calls[0]![0]).toContain('旧缓存保留')
    expect(mocks.request).toHaveBeenCalledWith('/tasks/task-1/items/item-1/cache/redownload', expect.objectContaining({
      body: expect.stringContaining('"confirmed":true'),
    }))
  })

  it('sends explicit cleanup acknowledgement after the irreversible deletion confirmation', async () => {
    mocks.confirm.mockResolvedValue(undefined)
    mocks.request.mockResolvedValue({ items: [] })
    const { click } = mount(cache(), 'clean')
    click(); await flush()
    expect(mocks.request).toHaveBeenCalledWith('/tasks/task-1/items/item-1/cache/clean', expect.objectContaining({
      body: expect.stringContaining('"confirmed":true'),
    }))
  })

  it('announces a queued operation failure once when polling reaches a terminal result', async () => {
    const operation = { id: 'op', kind: 'save' as const, state: 'queued' as const }
    const { props } = mount(cache({ operation, can_save: false, can_clean: false }))
    props.cache = { ...props.cache, operation: { ...operation, state: 'failed', message: '目标盘空间不足，缓存保留' } }
    await nextTick()
    props.cache = { ...props.cache, operation: { ...props.cache.operation! } }
    await nextTick()
    expect(mocks.message).toHaveBeenCalledOnce()
    expect(mocks.message).toHaveBeenCalledWith(expect.objectContaining({ type: 'error', message: '目标盘空间不足，缓存保留' }))
  })

  it('shows a shared unavailable reason only once and offers recovery for unknown outcomes', async () => {
    const message = '文件仍在使用或停止尚未确认，暂不能操作缓存。'
    const html = await markup(cache({ completeness: 'partial', state: 'uncertain', can_save: false, can_clean: false,
      unavailable_reason: message, resume_unavailable_reason: message,
      operation: { id: 'unknown', kind: 'download', state: 'uncertain', message } }))
    // Disabled-control descriptions repeat the explanation accessibly; visible
    // status paragraphs should carry it once.
    expect(html.match(/role="status"[^>]*>文件仍在使用或停止尚未确认/g)).toHaveLength(1)
    expect(html).toContain('前往连接设置')
    expect(html).toContain('重新同步状态')
  })
})

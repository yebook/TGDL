import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, createSSRApp, h, nextTick, reactive, ssrContextKey, type App } from 'vue'
import { renderToString } from 'vue/server-renderer'
import CacheStoragePanel from '../src/components/CacheStoragePanel.vue'
import type { CacheStatus } from '../src/api/types'

const messages = vi.hoisted(() => vi.fn())
vi.mock('element-plus', () => ({ ElMessage: messages, ElMessageBox: { close: vi.fn() } }))

// A small in-memory Vue host exercises real component watchers and rendering,
// without a browser DOM or an additional test dependency.
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
afterEach(() => { apps.splice(0).forEach(app => app.unmount()); vi.clearAllMocks() })

function snapshot(): CacheStatus {
  return { free_bytes: null, total_bytes: null, used_bytes: 4 * 1024**3, file_count: 3,
    usage_state: 'ready', refresh_error: null, measured_at: null, scope: 'current_bot_media',
    maintenance: { state: 'idle', operation_id: null, can_clean: true, blocked_reason: null,
      message: '可清理媒体缓存', last_result: null } }
}
function mount(status?: CacheStatus) {
  const props = reactive<{ status?: CacheStatus; active: boolean }>({ status, active: false })
  const root = node()
  // Volar models an SFC as a constructor; the actual compiled export is an
  // options object. Preserve its setup/watchers and override only SSR rendering.
  const component = { ...CacheStoragePanel, render: () => h('div') } as unknown as typeof CacheStoragePanel
  const app = renderer.createApp({ render: () => h(component, props) })
  // Element Plus components are host elements here; their widget behavior is
  // covered by real REST browser acceptance, not this presentation unit test.
  app.config.warnHandler = () => {}
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount(root)
  apps.push(app)
  return { props, root }
}
async function markup(status?: CacheStatus): Promise<string> {
  const app = createSSRApp(CacheStoragePanel, { status, active: false })
  app.config.warnHandler = () => {}
  return renderToString(app)
}

describe('simple cache presentation', () => {
  it('uses a placeholder before any measurement and omits extra state metadata', async () => {
    const result = await markup()
    expect(result).toContain('缓存占用 —')
    expect(result).not.toMatch(/0 B|未知|暂不可用|统计时间|缓存盘剩余/)
  })

  it('keeps measured bytes through explicit scan failure and ignores ordinary stale state', async () => {
    const status = snapshot()
    const { props } = mount(status)
    props.status = { ...status, usage_state: 'stale' }
    await nextTick()
    expect(await markup(props.status)).toMatch(/4.0 GB.*· 3 个文件/)
    expect(await markup(props.status)).not.toContain('失败')
    props.status = { ...status, usage_state: 'stale', refresh_error: '缓存占用刷新失败，请稍后重试' }
    await nextTick()
    expect(await markup(props.status)).toMatch(/4.0 GB.*· 3 个文件/)
    expect(await markup(props.status)).toContain('缓存占用刷新失败，请稍后重试')
    props.status = status
    await nextTick()
    expect(await markup(props.status)).not.toContain('失败')
  })

  it('announces an asynchronous cleanup failure once without adding a result panel', async () => {
    const status = snapshot()
    const { props } = mount(status)
    const operation = { ...status.maintenance, state: 'running' as const, operation_id: 'a'.repeat(32), can_clean: false }
    props.status = { ...status, maintenance: operation }
    await nextTick()
    const message = '已保存文件未通过校验，已保留缓存；请检查保存目录和文件后重试'
    props.status = { ...status, maintenance: { ...operation, state: 'failed', can_clean: true, message } }
    await nextTick()
    expect(messages).toHaveBeenCalledWith(expect.objectContaining({ type: 'error', message }))
    expect(await markup(props.status)).not.toContain(message)
    props.status = { ...props.status }
    await nextTick()
    expect(messages).toHaveBeenCalledTimes(1)
  })

  it('announces asynchronous successful cleanup once', async () => {
    const status = snapshot()
    const { props } = mount(status)
    const operation = { ...status.maintenance, state: 'running' as const, operation_id: 'b'.repeat(32), can_clean: false }
    props.status = { ...status, maintenance: operation }
    await nextTick()
    props.status = { ...status, used_bytes: 0, file_count: 0,
      maintenance: { ...operation, state: 'succeeded', message: '缓存清理完成', can_clean: true } }
    await nextTick()
    expect(messages).toHaveBeenCalledWith(expect.objectContaining({ type: 'success', message: '缓存清理完成' }))
  })

  it('waits for the finishing worker before announcing its terminal message', async () => {
    const status = snapshot()
    const { props } = mount(status)
    const operation = { ...status.maintenance, state: 'running' as const, operation_id: 'c'.repeat(32), can_clean: false }
    props.status = { ...status, maintenance: operation }
    await nextTick()
    props.status = { ...status, maintenance: { ...operation, state: 'succeeded',
      blocked_reason: 'maintenance_running', message: '正在清理缓存，暂不开始新的下载' } }
    await nextTick()
    expect(messages).not.toHaveBeenCalled()
    props.status = { ...status, maintenance: { ...operation, state: 'succeeded',
      blocked_reason: null, can_clean: true, message: '缓存清理完成' } }
    await nextTick()
    expect(messages).toHaveBeenCalledWith(expect.objectContaining({ type: 'success', message: '缓存清理完成' }))
  })
})

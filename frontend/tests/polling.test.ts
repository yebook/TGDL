import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, defineComponent } from 'vue'
import { usePolling } from '../src/composables/usePolling'

const renderer = createRenderer({
  patchProp: () => {}, insert: () => {}, remove: () => {}, createElement: () => ({}),
  createText: () => ({}), createComment: () => ({}), setText: () => {}, setElementText: () => {},
  parentNode: () => null, nextSibling: () => null,
})
function mountPolling(load: (signal: AbortSignal) => Promise<string>) {
  const documentTarget = Object.assign(new EventTarget(), { hidden: false })
  vi.stubGlobal('document', documentTarget)
  let polling!: ReturnType<typeof usePolling<string>>
  const app = renderer.createApp(defineComponent({ setup() { polling = usePolling(load); return () => null } }))
  app.mount({})
  return { polling, app, documentTarget }
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('live task polling', () => {
  it('keeps the latest filtered response when an aborted old request still resolves', async () => {
    vi.useFakeTimers()
    let oldResolve!: (value: string) => void
    const first = new Promise<string>(resolve => { oldResolve = resolve })
    const load = vi.fn().mockReturnValueOnce(first).mockResolvedValueOnce('new filter')
    const { polling, app } = mountPolling(load)
    await polling.refresh()
    oldResolve('old filter')
    await Promise.resolve()
    expect(polling.data.value).toBe('new filter')
    expect(load.mock.calls[0]![0].aborted).toBe(true)
    app.unmount()
  })
  it('retains last successful data after a network failure and stops requests after unmount', async () => {
    vi.useFakeTimers()
    const load = vi.fn().mockResolvedValueOnce('saved tasks').mockRejectedValueOnce(new Error('offline'))
    const { polling, app } = mountPolling(load)
    await Promise.resolve(); await Promise.resolve()
    await polling.refresh()
    expect(polling.data.value).toBe('saved tasks')
    expect(polling.error.value).toBe('offline')
    app.unmount()
    await vi.advanceTimersByTimeAsync(30000)
    expect(load).toHaveBeenCalledTimes(2)
  })
})

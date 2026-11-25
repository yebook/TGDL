import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, defineComponent } from 'vue'
import { type PollReason, usePolling } from '../src/composables/usePolling'

const renderer = createRenderer({
  patchProp: () => {}, insert: () => {}, remove: () => {}, createElement: () => ({}),
  createText: () => ({}), createComment: () => ({}), setText: () => {}, setElementText: () => {},
  parentNode: () => null, nextSibling: () => null,
})
function mountPolling(load: (signal: AbortSignal, reason: PollReason) => Promise<string>, interval?: (result: string) => number | undefined) {
  const documentTarget = Object.assign(new EventTarget(), { hidden: false })
  vi.stubGlobal('document', documentTarget)
  let polling!: ReturnType<typeof usePolling<string>>
  const app = renderer.createApp(defineComponent({ setup() { polling = usePolling(load, interval); return () => null } }))
  app.mount({})
  return { polling, app, documentTarget }
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('live polling', () => {
  it('distinguishes explicit refresh from mount, timed and visibility polling', async () => {
    vi.useFakeTimers()
    const load = vi.fn<(signal: AbortSignal, reason: PollReason) => Promise<string>>().mockResolvedValue('ready')
    const { polling, app, documentTarget } = mountPolling(load)
    await vi.advanceTimersByTimeAsync(3000)
    await polling.refresh()
    documentTarget.dispatchEvent(new Event('visibilitychange'))
    expect(load.mock.calls.map(([, reason]) => reason)).toEqual(['automatic', 'automatic', 'explicit', 'automatic'])
    app.unmount()
  })
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
  it('slows settled responses, refreshes immediately on demand, and resumes the live cadence', async () => {
    vi.useFakeTimers()
    const load = vi.fn().mockResolvedValueOnce('settled').mockResolvedValue('active')
    const { polling, app } = mountPolling(load, state => state === 'settled' ? 15000 : undefined)
    await vi.advanceTimersByTimeAsync(14999)
    expect(load).toHaveBeenCalledTimes(1)
    await polling.refresh()
    expect(load).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(2999)
    expect(load).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(load).toHaveBeenCalledTimes(3)
    app.unmount()
  })
  it('retries promptly after a failed refresh and refreshes again when the tab is shown', async () => {
    vi.useFakeTimers()
    const load = vi.fn().mockResolvedValueOnce('settled').mockRejectedValueOnce(new Error('offline')).mockResolvedValue('settled')
    const { polling, app, documentTarget } = mountPolling(load, () => 15000)
    await vi.advanceTimersByTimeAsync(15000)
    expect(polling.data.value).toBe('settled')
    expect(polling.error.value).toBe('offline')
    await vi.advanceTimersByTimeAsync(3000)
    expect(load).toHaveBeenCalledTimes(3)
    documentTarget.dispatchEvent(new Event('visibilitychange'))
    expect(load).toHaveBeenCalledTimes(4)
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

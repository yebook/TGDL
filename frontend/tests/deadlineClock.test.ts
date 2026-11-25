import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, defineComponent, nextTick, ref } from 'vue'
import { useDeadlineClock } from '../src/composables/useDeadlineClock'

const renderer = createRenderer({
  patchProp: () => {}, insert: () => {}, remove: () => {}, createElement: () => ({}),
  createText: () => ({}), createComment: () => ({}), setText: () => {}, setElementText: () => {},
  parentNode: () => null, nextSibling: () => null,
})
function mountClock(stepMs?: number) {
  vi.useFakeTimers()
  vi.setSystemTime(0)
  const documentTarget = Object.assign(new EventTarget(), { hidden: false })
  vi.stubGlobal('document', documentTarget)
  const deadline = ref(Number.NaN)
  const active = ref(true)
  let now!: ReturnType<typeof useDeadlineClock>
  const app = renderer.createApp(defineComponent({
    setup() {
      now = useDeadlineClock(() => deadline.value, { active: () => active.value, stepMs })
      return () => null
    },
  }))
  app.mount({})
  return { deadline, active, now, app, documentTarget }
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('visible deadline clocks', () => {
  it.each([1000, 60000])('does not tick until a %i ms countdown changes its displayed value', async stepMs => {
    const { now, deadline, app } = mountClock(stepMs)
    expect(vi.getTimerCount()).toBe(0)
    deadline.value = 250 + 2 * stepMs
    await nextTick()
    expect(vi.getTimerCount()).toBe(1)
    vi.advanceTimersByTime(249)
    expect(now.value).toBe(0)
    vi.advanceTimersByTime(1)
    expect(now.value).toBe(250)
    vi.advanceTimersByTime(stepMs - 1)
    expect(now.value).toBe(250)
    vi.advanceTimersByTime(stepMs + 1)
    expect(now.value).toBe(deadline.value)
    expect(vi.getTimerCount()).toBe(0)
    app.unmount()
  })

  it('reschedules a result expiry and wakes exactly once at the new deadline', async () => {
    const { now, deadline, app } = mountClock()
    deadline.value = 300000
    await nextTick()
    vi.advanceTimersByTime(1000)
    deadline.value = 301000
    await nextTick()
    vi.advanceTimersByTime(299999)
    expect(now.value).toBe(1000)
    expect(vi.getTimerCount()).toBe(1)
    vi.advanceTimersByTime(1)
    expect(now.value).toBe(301000)
    expect(vi.getTimerCount()).toBe(0)
    app.unmount()
  })

  it('pauses in a hidden section or tab, catches up on return, and cleans up on unmount', async () => {
    const { now, deadline, active, app, documentTarget } = mountClock(1000)
    deadline.value = 3000
    await nextTick()
    active.value = false
    await nextTick()
    expect(vi.getTimerCount()).toBe(0)
    vi.advanceTimersByTime(1100)
    active.value = true
    await nextTick()
    expect(now.value).toBe(1100)
    expect(vi.getTimerCount()).toBe(1)
    documentTarget.hidden = true
    documentTarget.dispatchEvent(new Event('visibilitychange'))
    expect(vi.getTimerCount()).toBe(0)
    vi.advanceTimersByTime(2500)
    expect(now.value).toBe(1100)
    documentTarget.hidden = false
    documentTarget.dispatchEvent(new Event('visibilitychange'))
    expect(now.value).toBe(3600)
    expect(vi.getTimerCount()).toBe(0)
    deadline.value = 5000
    await nextTick()
    expect(vi.getTimerCount()).toBe(1)
    app.unmount()
    vi.advanceTimersByTime(1000)
    documentTarget.dispatchEvent(new Event('visibilitychange'))
    expect(vi.getTimerCount()).toBe(0)
    expect(now.value).toBe(3600)
  })
})

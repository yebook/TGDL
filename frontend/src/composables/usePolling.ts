import { onMounted, onUnmounted, ref } from 'vue'
import { errorMessage, isAbort } from '../api/client'

const defaultInterval = 3000
const backgroundInterval = 15000

export type PollReason = 'automatic' | 'explicit'

/** One request at a time; new filters supersede older responses, including ignored aborts. */
export function usePolling<T>(load: (signal: AbortSignal, reason: PollReason) => Promise<T>, interval: number | ((result: T) => number | undefined) = defaultInterval) {
  const data = ref<T>()
  const loading = ref(false)
  const error = ref('')
  const updatedAt = ref<Date>()
  let timer: ReturnType<typeof setTimeout> | undefined
  let controller: AbortController | undefined
  let generation = 0
  let disposed = false

  const poll = async (reason: PollReason) => {
    clearTimeout(timer)
    controller?.abort()
    controller = new AbortController()
    const current = ++generation
    loading.value = true
    let delay = typeof interval === 'number' ? interval : defaultInterval
    try {
      const result = await load(controller.signal, reason)
      if (current !== generation || disposed) return
      if (typeof interval === 'function') delay = interval(result) ?? defaultInterval
      data.value = result
      error.value = ''
      updatedAt.value = new Date()
    } catch (cause) {
      if (current === generation && !disposed && !isAbort(cause)) error.value = errorMessage(cause)
    } finally {
      if (current === generation && !disposed) {
        loading.value = false
        timer = setTimeout(() => { void poll('automatic') }, document.hidden ? Math.max(backgroundInterval, delay) : delay)
      }
    }
  }
  const refresh = () => poll('explicit')
  const visibility = () => { if (!document.hidden) void poll('automatic') }
  onMounted(() => { void poll('automatic'); document.addEventListener('visibilitychange', visibility) })
  onUnmounted(() => {
    disposed = true; generation++; controller?.abort(); clearTimeout(timer)
    document.removeEventListener('visibilitychange', visibility)
  })
  return { data, loading, error, updatedAt, refresh }
}

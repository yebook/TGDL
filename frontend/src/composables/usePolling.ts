import { onMounted, onUnmounted, ref } from 'vue'
import { errorMessage, isAbort } from '../api/client'

/** One request at a time; new filters supersede older responses, including ignored aborts. */
export function usePolling<T>(load: (signal: AbortSignal) => Promise<T>, interval = 3000) {
  const data = ref<T>()
  const loading = ref(false)
  const error = ref('')
  const updatedAt = ref<Date>()
  let timer: ReturnType<typeof setTimeout> | undefined
  let controller: AbortController | undefined
  let generation = 0
  let disposed = false

  const refresh = async () => {
    clearTimeout(timer)
    controller?.abort()
    controller = new AbortController()
    const current = ++generation
    loading.value = true
    try {
      const result = await load(controller.signal)
      if (current !== generation || disposed) return
      data.value = result
      error.value = ''
      updatedAt.value = new Date()
    } catch (cause) {
      if (current === generation && !disposed && !isAbort(cause)) error.value = errorMessage(cause)
    } finally {
      if (current === generation && !disposed) {
        loading.value = false
        timer = setTimeout(() => { void refresh() }, document.hidden ? 15000 : interval)
      }
    }
  }
  const visibility = () => { if (!document.hidden) void refresh() }
  onMounted(() => { void refresh(); document.addEventListener('visibilitychange', visibility) })
  onUnmounted(() => {
    disposed = true; generation++; controller?.abort(); clearTimeout(timer)
    document.removeEventListener('visibilitychange', visibility)
  })
  return { data, loading, error, updatedAt, refresh }
}

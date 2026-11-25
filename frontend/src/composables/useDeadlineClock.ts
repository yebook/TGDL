import { onMounted, onUnmounted, ref, watch } from 'vue'

/** Wake only when a visible countdown changes units, or once when a deadline expires. */
export function useDeadlineClock(deadline: () => number, { active, stepMs }: { active: () => boolean; stepMs?: number }) {
  const now = ref(Date.now())
  let timer: ReturnType<typeof setTimeout> | undefined

  function update() {
    clearTimeout(timer)
    now.value = Date.now()
    const remaining = deadline() - now.value
    if (!active() || document.hidden || !Number.isFinite(remaining) || remaining <= 0) return
    const delay = stepMs ? remaining % stepMs || stepMs : remaining
    timer = setTimeout(update, Math.min(delay, 2 ** 31 - 1))
  }

  onMounted(() => {
    watch([deadline, active], update, { immediate: true })
    document.addEventListener('visibilitychange', update)
  })
  onUnmounted(() => {
    clearTimeout(timer)
    document.removeEventListener('visibilitychange', update)
  })
  return now
}

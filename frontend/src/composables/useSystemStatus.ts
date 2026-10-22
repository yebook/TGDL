import { inject, type InjectionKey, type Ref } from 'vue'
import type { SystemStatus } from '../api/types'

export interface SystemContext {
  data: Ref<SystemStatus | undefined>; error: Ref<string>; loading: Ref<boolean>
  debug: Ref<boolean>; refresh: () => Promise<void>
}
export const systemKey: InjectionKey<SystemContext> = Symbol('system-status')
export function useSystemStatus(): SystemContext {
  const context = inject(systemKey)
  if (!context) throw new Error('System status requires an authenticated workspace')
  return context
}

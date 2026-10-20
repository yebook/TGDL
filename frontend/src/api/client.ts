export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code = 'request_failed',
    public readonly requestId?: string,
    public readonly details?: unknown,
  ) { super(message) }
}

let csrfToken = ''
let expiredHandler: (() => void) | undefined
export const setCsrfToken = (value: string) => { csrfToken = value }
export const onSessionExpired = (handler: () => void) => { expiredHandler = handler }

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, { ...options, headers, credentials: 'same-origin' })
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error
    throw new ApiError('连接失败，请检查服务和网络后重试。', 0, 'network_error')
  }
  if (response.status === 204) return undefined as T
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 401 && !['/auth/login', '/auth/me', '/setup'].includes(path)) {
      setCsrfToken('')
      expiredHandler?.()
    }
    const body = payload as { error?: { message?: string; code?: string; request_id?: string; details?: unknown } } | null
    throw new ApiError(body?.error?.message ?? '请求未完成，请稍后重试。', response.status,
      body?.error?.code, body?.error?.request_id, body?.error?.details)
  }
  return payload as T
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return `${error.message}${error.requestId ? `（请求 ${error.requestId}）` : ''}`
  return error instanceof Error ? error.message : '操作失败，请重试。'
}

export function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

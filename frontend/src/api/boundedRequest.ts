import { ApiError, request } from './client'

/** Bound UI waiting without retrying mutations whose server outcome may be unknown. */
export async function boundedRequest<T>(path: string, options: RequestInit = {}, timeoutMs = 15000): Promise<T> {
  if (options.signal?.aborted) throw new DOMException('Request cancelled', 'AbortError')
  const controller = new AbortController()
  const parent = options.signal
  let timer: ReturnType<typeof setTimeout> | undefined
  let cancel = () => {}
  const deadline = new Promise<never>((_, reject) => {
    cancel = () => {
      reject(new DOMException('Request cancelled', 'AbortError'))
      controller.abort()
    }
    if (parent?.aborted) { cancel(); return }
    parent?.addEventListener('abort', cancel, { once: true })
    timer = setTimeout(() => {
      reject(new ApiError('请求超时，请重新同步状态后确认结果。', 0, 'request_timeout'))
      controller.abort()
    }, timeoutMs)
  })
  try {
    return await Promise.race([deadline, request<T>(path, { ...options, signal: controller.signal })])
  } finally {
    clearTimeout(timer)
    parent?.removeEventListener('abort', cancel)
  }
}

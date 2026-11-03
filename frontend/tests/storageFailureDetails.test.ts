import { describe, expect, it } from 'vitest'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import StorageFailureDetails from '../src/components/StorageFailureDetails.vue'
import type { StorageFailure } from '../src/api/types'

describe('storage failure detail', () => {
  it('explains the failed stage and per-device reservation without displaying internal phase values', async () => {
    const failure: StorageFailure = { phase: 'before_save', sufficient: false, volumes: [
      { location: 'target+cache', free_bytes: 1024, required_bytes: 2048, concurrent_bytes: 4096,
        reserve_bytes: 256, missing_bytes: 5376, error_code: 'NO_SPACE' },
    ] }
    const html = await renderToString(createSSRApp(StorageFailureDetails, { failure }))
    expect(html).toContain('保存前空间检查')
    expect(html).toContain('缓存与保存盘（同盘）')
    expect(html).toContain('可用 1.0 KB · 本次需要 2.0 KB')
    expect(html).toContain('并发预留 4.0 KB · 安全余量 256 B')
    expect(html).toContain('尚缺 5.3 KB')
    expect(html).not.toContain('before_save')
  })

  it('keeps unavailable capacity unknown and explains the recovery action', async () => {
    const failure: StorageFailure = { phase: 'before_download', sufficient: false, volumes: [
      { location: 'cache', free_bytes: null, required_bytes: 2048, concurrent_bytes: 0,
        reserve_bytes: 0, missing_bytes: null, error_code: 'STORAGE_UNAVAILABLE' },
    ] }
    const html = await renderToString(createSSRApp(StorageFailureDetails, { failure }))
    expect(html).toContain('缓存盘')
    expect(html).toContain('可用 未知')
    expect(html).toContain('检查存储连接与权限后重试')
    expect(html).not.toContain('尚缺')
  })
})

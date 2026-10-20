import { afterEach, describe, expect, it, vi } from 'vitest'
import { diagnosticJsonl, downloadDiagnosticLog } from '../src/api/diagnosticLog'
import type { DiagnosticLogExport } from '../src/api/types'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
describe('diagnostic log download', () => {
  it('exports independent UTF-8 JSON lines including escaped strings and null values', () => {
    const events: DiagnosticLogExport['events'] = [{ event: '恢复', elapsed_ms: 3, retry_at: null, success: true }, { event: 'line\nbreak' }]
    const lines = diagnosticJsonl(events).trimEnd().split('\n')
    expect(lines).toHaveLength(2)
    expect(lines.map(line => JSON.parse(line))).toEqual(events)
    expect(diagnosticJsonl([])).toBe('')
  })
  it('cleans up the anchor and releases the Blob URL after the browser consumes it', async () => {
    vi.useFakeTimers()
    const revokeObjectURL = vi.fn()
    const createObjectURL = vi.fn<(blob: Blob) => string>(() => 'blob:diagnostic-test')
    const click = vi.fn()
    const remove = vi.fn()
    const anchor = { href: '', download: '', click, remove }
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    vi.stubGlobal('document', { createElement: vi.fn(() => anchor), body: { appendChild: vi.fn() } })
    downloadDiagnosticLog([{ event: 'ready' }])
    expect(click).toHaveBeenCalledOnce()
    expect(remove).toHaveBeenCalledOnce()
    expect(anchor.download).toMatch(/^tgdl-diagnostics-.*\.jsonl$/)
    const blob = createObjectURL.mock.calls[0]?.[0] as Blob | undefined
    expect(blob?.type).toBe('application/x-ndjson;charset=utf-8')
    expect(await blob?.text()).toBe('{"event":"ready"}\n')
    expect(revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(30000)
    expect(revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:diagnostic-test')
  })
})

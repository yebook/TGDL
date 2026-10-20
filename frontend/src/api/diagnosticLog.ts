import type { DiagnosticLogExport } from './types'

export function diagnosticJsonl(events: DiagnosticLogExport['events']): string {
  return events.length ? `${events.map(event => JSON.stringify(event)).join('\n')}\n` : ''
}

export function downloadDiagnosticLog(events: DiagnosticLogExport['events']): void {
  const blob = new Blob([diagnosticJsonl(events)], { type: 'application/x-ndjson;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `tgdl-diagnostics-${new Date().toISOString().replace(/[:.]/g, '-')}.jsonl`
  document.body.appendChild(anchor)
  try { anchor.click() } finally {
    anchor.remove()
    // Give the browser time to consume the download before releasing its URL.
    setTimeout(() => URL.revokeObjectURL(url), 30000)
  }
}

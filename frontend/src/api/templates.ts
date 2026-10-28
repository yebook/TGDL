import { bytes } from './format'

export const defaultConfirmationTemplate = '📥 确认下载：{title}\n📁 保存到：{destination}\n⏳ 确认有效期：{expires_in_minutes} 分钟\n{files}'
export const defaultFilenameTemplate = '{name}'
export const confirmationVariables = [
  ['title', '消息标题'], ['file_count', '文件数量'], ['total_size', '总大小（未知时显示未知）'],
  ['files', '文件清单（含大小）'], ['destination', '保存目录'], ['expires_in_minutes', '确认有效期（分钟）'],
] as const
export const filenameVariables = [
  ['name', '原名主体；缺失时用消息标题，再自动命名'], ['date', '日期 YYYYMMDD'], ['time', '时间 HHMMSS'],
  ['datetime', '日期时间 YYYYMMDD_HHMMSS'], ['type', 'video 或 image'], ['index', '顺序，从 1 开始'], ['id', '短 ID'],
] as const

// A single scan substitutes only source-template variables, never braces in media values.
export function renderTemplateExample(template: string, values: Record<string, string>, filename = false, bound = true): string {
  if (!template.trim() || Array.from(template).length > (filename ? 160 : 2000)) {
    throw new Error(filename ? '文件名模板须为 1–160 个字符，且不能全为空白。' : '确认提示模板须为 1–2000 个字符，且不能全为空白。')
  }
  if (filename && /[<>:"/\\|?*\u0000-\u001f\u007f]/.test(template)) throw new Error('文件名模板不能包含路径、控制字符或 <>:"/\\|?*。')
  if (!filename && /[\u0000-\u0008\u000b-\u001f\u007f]/.test(template)) throw new Error('确认提示模板不能包含控制字符。')
  let output = ''
  let literalUnits = 0, substitutions = 0
  for (let index = 0; index < template.length;) {
    const char = template[index]!
    if ((char === '{' || char === '}') && template[index + 1] === char) {
      output += char; literalUnits++; index += 2
    } else if (char === '{') {
      const end = template.indexOf('}', index + 1)
      const key = end === -1 ? '' : template.slice(index + 1, end)
      if (!Object.hasOwn(values, key)) throw new Error('模板变量或括号无效，请使用下方列出的变量。')
      output += values[key]; substitutions++; index = end + 1
    } else if (char === '}') throw new Error('模板变量或括号无效，请使用下方列出的变量。')
    else { output += char; literalUnits++; index++ }
  }
  if (!filename && bound && output.length > 3500) {
    const budget = Math.max(1, Math.floor((3500 - literalUnits) / Math.max(substitutions, 1)))
    const bounded = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, value.length <= budget ? value : `${truncateUtf16(value, budget - 1)}…`]))
    const result = renderTemplateExample(template, bounded, false, false)
    return result.length <= 3500 ? result : `${truncateUtf16(result, 3499)}…`
  }
  return output
}

function truncateUtf16(value: string, units: number): string {
  return value.slice(0, Math.max(0, units)).replace(/[\ud800-\udbff]$/, '')
}

function fitExampleFilename(rendered: string): string {
  // Mirror backend naming for this fixed .mp4 example, including the 180-byte cap.
  let stem = rendered.toLowerCase().endsWith('.mp4') ? rendered.slice(0, -4) : rendered
  stem = (stem || 'file').replace(/[<>:"/\\|?*\u0000-\u001f\u007f]/g, '_').replace(/\.\./g, '_').replace(/^[ .]+|[ .]+$/g, '')
  if (!stem || /^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])$/i.test(stem.split('.')[0]!)) stem = `file_${stem || 'file'}`
  const encoder = new TextEncoder()
  let result = ''
  for (const char of stem) {
    if (encoder.encode(result + char).length > 176) break
    result += char
  }
  return `${result.replace(/[ .]+$/g, '') || 'file'}.mp4`
}

export function templateExamples(confirmation: string, filename: string, expiresIn: number) {
  let file = '', fileError = '', text = '', confirmationError = ''
  try {
    file = fitExampleFilename(renderTemplateExample(filename, { name: '海边日落', date: '20261020', time: '183000', datetime: '20261020_183000', type: 'video', index: '1', id: 'a1b2c3d4' }, true))
  } catch (cause) { fileError = (cause as Error).message }
  try {
    text = renderTemplateExample(confirmation, { title: '海边日落', file_count: '1', total_size: bytes(13107200), files: `${file || '海边日落.mp4'}\n📦 文件大小：${bytes(13107200)}`, destination: '2026-10-20', expires_in_minutes: String(Number((expiresIn / 60).toFixed(2))) }) + '\n任务编号：e5f6a7b80123456789abcdef01234567'
  } catch (cause) { confirmationError = (cause as Error).message }
  return { file, fileError, text, confirmationError }
}

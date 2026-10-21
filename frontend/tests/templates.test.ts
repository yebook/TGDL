import { describe, expect, it } from 'vitest'
import { defaultConfirmationTemplate, defaultFilenameTemplate, renderTemplateExample, templateExamples } from '../src/api/templates'

const identifierLine = '\n任务编号：e5f6a7b80123456789abcdef01234567'

describe('settings template examples', () => {
  it('keeps media braces literal and supports escaped braces', () => {
    expect(renderTemplateExample('{{title}}: {title}', { title: '{files}' })).toBe('{title}: {files}')
  })
  it.each(['{unknown}', '{title.name}', '{title[0]}', '{title!r}', '{title:10}', '{', '}', '{title'])('rejects unsupported syntax %s', value => {
    expect(() => renderTemplateExample(value, { title: 'sample' })).toThrow('变量或括号无效')
  })
  it.each(['../{name}', 'a\\{name}', 'a:{name}', 'a\n{name}', '{name}?'])('rejects unsafe filename template %s', value => {
    expect(() => renderTemplateExample(value, { name: 'sample' }, true)).toThrow('文件名模板不能')
  })
  it('validates length and whitespace while supporting Unicode', () => {
    expect(() => renderTemplateExample('  ', {})).toThrow('不能全为空白')
    expect(() => renderTemplateExample('a'.repeat(161), {}, true)).toThrow('1–160')
    expect(renderTemplateExample('🌅'.repeat(160), {}, true)).toHaveLength(320)
  })
  it('previews readable sizes, automatic extension and current expiry', () => {
    const sample = templateExamples(defaultConfirmationTemplate, defaultFilenameTemplate, 600)
    expect(sample.file).toBe('海边日落_a1b2c3d4.mp4')
    expect(sample.text).toContain('12.5 MB')
    expect(sample.text).toContain('确认下载：海边日落\n')
    expect(sample.text.endsWith(identifierLine)).toBe(true)
    expect(sample.text).toContain('保存到：2026-10-20\n')
    expect(sample.text).toContain('10 分钟')
    expect(sample.text).toContain(sample.file)
    expect(sample.confirmationError).toBe('')
    expect(sample.fileError).toBe('')
  })
  it.each([[1800, '30'], [30, '0.5'], [90, '1.5']])('previews the supported minute variable for %s', (seconds, minutes) => {
    expect(templateExamples('{expires_in_minutes} 分钟', '{name}', seconds).text).toBe(`${minutes} 分钟${identifierLine}`)
  })
  it('rejects removed template variables', () => {
    expect(templateExamples('{expires_in}', '{name}', 1800).confirmationError).toContain('模板变量或括号无效')
  })
  it.each([['CON', 'file_CON.mp4'], ['.', 'file_file.mp4'], ['{name}.mp4', '海边日落.mp4'], ['{name} . ', '海边日落.mp4']])('matches safe backend sample name for %s', (template, result) => {
    expect(templateExamples('{files}', template, 300).file).toBe(result)
  })
  it('preserves extension within the backend UTF-8 byte limit', () => {
    const sample = templateExamples('{files}', '🌅'.repeat(160), 300)
    expect(sample.file).toBe(`${'🌅'.repeat(44)}.mp4`)
    expect(new TextEncoder().encode(sample.file).length).toBe(180)
    expect(sample.text).toBe(`${sample.file} · 12.5 MB${identifierLine}`)
  })
  it('allows confirmation tabs/newlines but rejects other control characters', () => {
    expect(renderTemplateExample('\t{title}\n', { title: 'sample' })).toBe('\tsample\n')
    expect(() => renderTemplateExample('a\r\n', {})).toThrow('控制字符')
  })
  it('bounds expanded confirmation examples just like Bot cards', () => {
    const sample = templateExamples('{files}'.repeat(100), '🌅'.repeat(160), 300)
    expect(sample.text).toBe(`${'🌅'.repeat(17)}…`.repeat(100) + identifierLine)
    expect(sample.text.length).toBe(3500 + identifierLine.length)
    expect(sample.text).not.toContain('\ufffd')
  })
  it('keeps later variables visible and bounds non-BMP literal-only text', () => {
    expect(renderTemplateExample('{title}\n{files}', { title: '🌅'.repeat(2000), files: 'file.mp4' })).toBe(`${'🌅'.repeat(874)}…\nfile.mp4`)
    expect(renderTemplateExample('🌅'.repeat(2000), {})).toBe(`${'🌅'.repeat(1749)}…`)
  })
})

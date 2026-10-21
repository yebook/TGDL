import { readFile, readdir, stat } from 'node:fs/promises'
import { dirname, resolve, extname } from 'node:path'
import { fileURLToPath } from 'node:url'

export const docsRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
export const excluded = new Set(['README.md', 'project-requirements.md'])
export const publicPages = (await readdir(docsRoot)).filter(name => name.endsWith('.md') && !excluded.has(name))
export const markdownLinks = text => [...text.replace(/^```[^\n]*\n[\s\S]*?^```\s*$/gm, '').matchAll(/!?\[[^\]]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)/g)].map(match => match[1])

const inputs = [...publicPages.map(name => resolve(docsRoot, name)), resolve(docsRoot, 'README.md'),
  ...['README.md', 'CONTRIBUTING.md', 'SECURITY.md', 'CHANGELOG.md'].map(name => resolve(docsRoot, '..', name))]
let count = 0
for (const path of inputs) {
  const text = await readFile(path, 'utf8')
  if (/\b[A-Za-z]:[\\/]|\.runtime\/local-docker|\.trellis\//.test(text)) throw new Error(`Nonportable/internal content: ${path}`)
  if ((text.match(/^```/gm) || []).length % 2) throw new Error(`Unclosed code fence: ${path}`)
  for (const link of markdownLinks(text)) {
    if (/^(?:https?:|mailto:|#)/.test(link)) continue
    const [relative] = decodeURIComponent(link).split('#')
    const target = resolve(dirname(path), relative)
    const info = await stat(target).catch(() => null)
    if (!info) throw new Error(`Missing relative link in ${path}: ${link}`)
    if (publicPages.some(name => resolve(docsRoot, name) === path) && extname(target) === '.md' && !publicPages.some(name => resolve(docsRoot, name) === target)) {
      throw new Error(`Public page links to excluded page: ${link}`)
    }
    count++
  }
}
console.log(`Checked ${inputs.length} Markdown files and ${count} relative links; public anchors checked after build.`)

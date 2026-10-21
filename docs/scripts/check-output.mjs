import { readFile, readdir, stat } from 'node:fs/promises'
import { resolve, relative, extname } from 'node:path'
import { docsRoot, publicPages, markdownLinks } from './check-links.mjs'

const dist = resolve(docsRoot, '.vitepress/dist')
const base = process.env.DOCS_BASE || '/TGDL/'
const htmlPages = new Map()
for (const name of publicPages) htmlPages.set(name, await readFile(resolve(dist, name.replace(/\.md$/, '.html')), 'utf8'))
for (const [name, html] of htmlPages) {
  if (!html.includes(`${base}assets/`)) throw new Error(`Missing base assets: ${name}`)
  const source = await readFile(resolve(docsRoot, name), 'utf8')
  for (const link of markdownLinks(source)) {
    if (/^(?:https?:|mailto:)/.test(link) || !link.includes('#')) continue
    const [page, anchor] = decodeURIComponent(link).split('#')
    const target = page || name
    const targetHtml = htmlPages.get(target)
    if (!targetHtml || !targetHtml.includes(`id="${anchor}"`)) throw new Error(`Missing built anchor ${name}: ${link}`)
  }
}
async function walk(folder) {
  for (const name of await readdir(folder)) {
    const path = resolve(folder, name)
    if ((await stat(path)).isDirectory()) { await walk(path); continue }
    const nameFromDist = relative(dist, path).replaceAll('\\', '/')
    if (/project-requirements|(?:^|\/)(?:README|package|pnpm-lock|pnpm-workspace)|\.trellis|\.runtime|\.env/.test(nameFromDist)) throw new Error(`Unexpected output file: ${nameFromDist}`)
    if (['.html', '.js', '.json'].includes(extname(path))) {
      const text = await readFile(path, 'utf8')
      if (/\.runtime\/local-docker|\.trellis\/tasks|F:\\\\workspace|C:\\\\Users|before-update-2026/.test(text)) throw new Error(`Internal content in ${nameFromDist}`)
    }
  }
}
await walk(dist)
await stat(resolve(dist, '.nojekyll'))
console.log(`Checked ${htmlPages.size} generated public pages, anchors, base and output exclusions.`)

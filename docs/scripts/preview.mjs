// VitePress 1.x preview does not honor a host argument. Keep local preview on loopback.
import { createServer } from 'node:http'
import { createReadStream } from 'node:fs'
import { lstat } from 'node:fs/promises'
import { dirname, extname, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const args = process.argv.slice(2)
if (args.length && (args.length !== 2 || args[0] !== '--port')) throw new Error('Usage: pnpm docs:preview [--port PORT]')
const port = Number(args[1] || 4173)
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Port must be an integer from 1024 to 65535.')
const base = process.env.DOCS_BASE || '/TGDL/'
if (!/^\/(?:[A-Za-z0-9_-]+\/)*$/.test(base)) throw new Error('Invalid DOCS_BASE.')
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../.vitepress/dist')
if ((await lstat(root)).isSymbolicLink()) throw new Error('Preview output must not be a symlink.')
await lstat(resolve(root, 'index.html'))
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2' }

createServer(async (req, res) => {
  const fail = code => { res.writeHead(code); res.end() }
  if (!['GET', 'HEAD'].includes(req.method)) return fail(405)
  let pathname
  try { pathname = decodeURIComponent(new URL(req.url, 'http://127.0.0.1').pathname) } catch { return fail(400) }
  if (!pathname.startsWith(base)) return fail(404)
  const parts = pathname.slice(base.length).split('/')
  if (parts.some(part => part === '..' || part.includes('\\') || (part.startsWith('.') && part !== '.nojekyll'))) return fail(404)
  const relative = parts.join('/') + (pathname.endsWith('/') ? 'index.html' : '')
  const path = resolve(root, relative)
  if (!path.startsWith(root + sep)) return fail(404)
  try {
    let cursor = root
    for (const part of relative.split('/')) {
      cursor = resolve(cursor, part)
      if ((await lstat(cursor)).isSymbolicLink()) return fail(404)
    }
    const info = await lstat(path)
    if (!info.isFile()) return fail(404)
    res.writeHead(200, { 'content-type': mime[extname(path)] || 'application/octet-stream', 'cache-control': 'no-store' })
    if (req.method === 'HEAD') { res.end(); return }
    const stream = createReadStream(path)
    stream.on('error', () => res.destroy())
    stream.pipe(res)
  } catch { fail(404) }
}).on('error', error => {
  console.error(`Preview failed (${error.code || 'ERROR'}). Choose another --port if occupied or reserved.`)
  process.exitCode = 1
}).listen(port, '127.0.0.1', () => console.log(`Documentation preview: http://127.0.0.1:${port}${base}`))

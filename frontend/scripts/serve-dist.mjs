import { createReadStream, existsSync, statSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { extname, join, normalize, resolve } from 'node:path'

const args = new Map()
for (let index = 2; index < process.argv.length; index += 1) {
  const key = process.argv[index]
  if (key === '--' || !key.startsWith('--')) {
    continue
  }
  const value = process.argv[index + 1]
  if (value && !value.startsWith('--')) {
    args.set(key, value)
    index += 1
  } else {
    args.set(key, 'true')
  }
}

const host = args.get('--host') || '127.0.0.1'
const port = Number(args.get('--port') || 4173)
const root = resolve('dist')

const contentTypes = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.ico': 'image/x-icon',
}

if (!existsSync(join(root, 'index.html'))) {
  console.error('dist/index.html not found. Run npm run build first.')
  process.exit(1)
}

function safePath(urlPath) {
  const decoded = decodeURIComponent(urlPath.split('?')[0] || '/')
  const normalized = normalize(decoded).replace(/^(\.\.[/\\])+/, '')
  return resolve(root, normalized.slice(1))
}

function sendFile(response, filePath) {
  const type = contentTypes[extname(filePath)] || 'application/octet-stream'
  response.writeHead(200, {
    'content-type': type,
    'cache-control': 'no-store',
  })
  createReadStream(filePath).pipe(response)
}

const server = createServer(async (request, response) => {
  try {
    const requestUrl = new URL(request.url || '/', `http://${host}:${port}`)
    let filePath = safePath(requestUrl.pathname)
    if (!filePath.startsWith(root)) {
      response.writeHead(403)
      response.end('Forbidden')
      return
    }
    if (existsSync(filePath) && statSync(filePath).isDirectory()) {
      filePath = join(filePath, 'index.html')
    }
    if (existsSync(filePath) && statSync(filePath).isFile()) {
      sendFile(response, filePath)
      return
    }
    const html = await readFile(join(root, 'index.html'))
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-cache' })
    response.end(html)
  } catch (error) {
    response.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' })
    response.end(error instanceof Error ? error.message : 'Internal Server Error')
  }
})

server.listen(port, host, () => {
  console.log(`AppStation frontend served at http://${host}:${port}`)
})

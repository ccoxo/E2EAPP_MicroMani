import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir } from 'node:fs/promises'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { chromium } from 'playwright'

const port = 5174
const baseUrl = `http://127.0.0.1:${port}`

const build = spawnSync('npm', ['run', 'build'], { stdio: 'inherit' })
if (build.status !== 0) {
  throw new Error('build failed before visual check')
}

const server = spawn('node', ['scripts/serve-dist.mjs', '--host', '127.0.0.1', '--port', String(port)], {
  stdio: ['ignore', 'pipe', 'pipe'],
})

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function waitForServer() {
  for (let index = 0; index < 80; index += 1) {
    try {
      const response = await fetch(baseUrl)
      if (response.ok) return
    } catch {
      await wait(250)
    }
  }
  throw new Error('Vite dev server did not start')
}

try {
  await mkdir('tmp/screenshots', { recursive: true })
  await waitForServer()
  const chromeForTesting = join(
    homedir(),
    'Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
  )
  const browser = await chromium.launch(
    existsSync(chromeForTesting) ? { executablePath: chromeForTesting, headless: true } : { headless: true },
  )
  const viewports = [
    { name: 'desktop-1920', width: 1920, height: 1080 },
    { name: 'desktop-1440', width: 1440, height: 900 },
    { name: 'mobile-390', width: 390, height: 844 },
  ]
  const targets = [
    { name: 'dashboard', path: '/' },
    { name: 'manual', path: '/settings#manual' },
    { name: 'motion-left', path: '/settings#motion-left' },
    { name: 'motion-right', path: '/settings#motion-right' },
    { name: 'camera-global', path: '/settings#camera-global' },
    { name: 'force-left', path: '/settings#force-left' },
    { name: 'gripper-left', path: '/settings#gripper-left' },
    { name: 'gripper-right', path: '/settings#gripper-right' },
    { name: 'teleop-left', path: '/settings#teleop-left' },
    { name: 'safety', path: '/settings#safety' },
  ]

  for (const viewport of viewports) {
    for (const target of targets) {
      const page = await browser.newPage({ viewport })
      await page.goto(`${baseUrl}${target.path}`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(500)
      await page.screenshot({ path: `tmp/screenshots/${viewport.name}-${target.name}.png`, fullPage: true })
      const mainContent = await page.locator('.main-content').boundingBox()
      const chartCount = await page.locator('canvas').count()
      const bodyOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 4)
      if (!mainContent || mainContent.width < 250 || mainContent.height < 250) {
        throw new Error(`${viewport.name}/${target.name}: main content is too small`)
      }
      if (bodyOverflow) {
        throw new Error(`${viewport.name}/${target.name}: page has horizontal overflow`)
      }
      if (target.path.startsWith('/settings#') && target.path !== '/settings#manual') {
        const focusedCount = await page.locator('.hardware-config-card-focused').count()
        if (focusedCount !== 1) {
          throw new Error(`${viewport.name}/${target.name}: focused hardware card missing`)
        }
      }
      const chartExpected = target.path !== '/settings#manual'
      if (viewport.width >= 1200 && chartExpected && chartCount < 2) {
        throw new Error(`${viewport.name}/${target.name}: charts did not render`)
      }
      await page.close()
    }
  }
  await browser.close()
  console.log(`visual checks passed for ${viewports.length} viewports × ${targets.length} targets`)
} finally {
  server.kill('SIGTERM')
}

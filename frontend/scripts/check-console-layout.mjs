/*
 * 离线验证日志面板与安全控制区的布局边界，不向后端或硬件发送请求。
 * 从仓库根目录运行：node frontend/scripts/check-console-layout.mjs
 */
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { createServer } from 'vite'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const outputDirectory = join(frontendRoot, 'output/playwright/console-layout')
const origin = 'http://127.0.0.1:5187'
const viewports = [
  { width: 1920, height: 1080 },
  { width: 1440, height: 900 },
  { width: 1280, height: 720 },
  { width: 1024, height: 768 },
  { width: 390, height: 844 },
  { width: 320, height: 640 },
]
const scenarios = [
  { name: 'idle-closed', open: false, active: false },
  { name: 'idle-open', open: true, active: false },
  { name: 'locked-open', open: true, active: true },
  { name: 'locked-closed', open: false, active: true },
  { name: 'error-open', open: true, active: true, error: true },
  { name: 'error-closed', open: false, active: true, error: true },
]
const longError = `离线布局回归：急停反馈未确认，连接中断。${'请检查设备连接与实体急停状态。'.repeat(18)} transport=${'unconfirmed_'.repeat(30)}`
const report = { origin, cases: [], blockedHttp: 0, blockedWebSockets: 0 }
let browser
let server

async function settle(page) {
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise((resolveFrame) => requestAnimationFrame(() => requestAnimationFrame(resolveFrame)))
  })
}

async function injectScenario(page, scenario) {
  await page.evaluate(async ({ open, active, error, message }) => {
    const { useTelemetryStore } = await import('/src/stores/telemetry.ts')
    const { initialControlSafety } = await import('/src/utils/controlSafety.ts')
    // 停止页面自身的重连和看门狗，保证注入状态不被异步网络失败覆盖。
    useTelemetryStore.getState().stopBackend()
    useTelemetryStore.getState().stopMock()
    useTelemetryStore.setState((state) => ({
      logs: [...Array.from({ length: 40 }, (_, index) => ({
        id: 100000 + index,
        ts: Date.UTC(2026, 8, 18, 8, 0, 0) + index * 25,
        channel: index % 2 ? '[HAL]' : '[SAFETY]',
        level: index % 5 === 0 ? 'ERROR' : 'INFO',
        msg: `layout-fixture-${index} event=operation_complete operation=${index} 状态仅用于离线布局检查`,
      })), ...[
        { level: 'DEBUG', msg: 'diagnostic-debug-fixture event=background_poll' },
        { level: 'INFO', msg: 'diagnostic-info-fixture event=teleop_axis_trace axis=Roll updateRet=[Roll:0]' },
        { level: 'WARNING', msg: 'diagnostic-warning-fixture event=teleop_axis_trace axis=Roll updateRet=[Roll:0]' },
        { level: 'ERROR', msg: 'diagnostic-error-fixture event=teleop_axis_trace axis=Roll updateRet=[Roll:0]' },
      ].map((entry, index) => ({ ...entry, id: 101000 + index, ts: Date.UTC(2026, 8, 18, 8, 0, 2) + index * 25, channel: '[HAL]' }))],
      logPanelOpen: open,
      controlSafety: {
        ...initialControlSafety(),
        generation: state.controlSafety.generation + 1,
        emergencyRequested: active,
        emergencyError: error ? message : null,
      },
      frame: {
        ...state.frame,
        forceStatus: {
          ...state.frame.forceStatus,
          safety: { ...state.frame.forceStatus?.safety, latched: active, canAcknowledge: false },
        },
      },
    }))
  }, { ...scenario, message: longError })
  await settle(page)
}

async function inspectLayout(page, scenario) {
  return page.evaluate(({ open, active, error }) => {
    const issues = []
    const bounds = {}
    const epsilon = 1
    const rect = (selector, required = true) => {
      const element = document.querySelector(selector)
      if (!element) {
        if (required) issues.push(`缺少 ${selector}`)
        return null
      }
      const box = element.getBoundingClientRect()
      const result = { x: box.x, y: box.y, right: box.right, bottom: box.bottom, width: box.width, height: box.height }
      bounds[selector] = result
      if (required && (box.width <= 0 || box.height <= 0 || getComputedStyle(element).visibility === 'hidden')) {
        issues.push(`${selector} 不可见`)
      }
      return result
    }
    const contains = (outer, inner, label) => {
      if (!outer || !inner) return
      if (inner.x < outer.x - epsilon || inner.y < outer.y - epsilon
        || inner.right > outer.right + epsilon || inner.bottom > outer.bottom + epsilon) issues.push(`${label} 超出所属区域`)
    }
    const viewport = { x: 0, y: 0, right: innerWidth, bottom: innerHeight }
    const hitTarget = (selector, container) => {
      const box = rect(selector)
      if (!box) return
      contains(viewport, box, selector)
      if (container) contains(container, box, selector)
      const element = document.querySelector(selector)
      const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)
      if (!hit || !element.contains(hit)) issues.push(`${selector} 中心被其他元素遮挡：${hit?.className ?? '视口外'}`)
    }
    const panel = rect('.log-panel')
    const dock = rect('.safety-dock')
    const main = rect('.main-content')
    const toolbar = rect('.log-toolbar')
    contains(viewport, panel, '日志面板')
    contains(viewport, dock, '安全控制区')
    contains(rect('.work-area'), main, '主内容区')
    contains(main, dock, '浮动急停')
    contains(panel, toolbar, '日志工具栏')
    contains(viewport, rect('.status-bar'), '状态栏')
    if (panel && dock && Math.min(panel.right, dock.right) - Math.max(panel.x, dock.x) > epsilon
      && Math.min(panel.bottom, dock.bottom) - Math.max(panel.y, dock.y) > epsilon) issues.push('日志面板与安全控制区重叠')
    hitTarget('[aria-label="全局急停"]', dock)
    if (active) hitTarget('[aria-label="确认安全态"]', dock)
    else if (document.querySelector('[aria-label="确认安全态"]')) issues.push('未锁存时不应显示确认安全态')
    if (error) contains(dock, rect('.safety-control-error'), '急停错误信息')
    if (open) {
      const logViewport = rect('.log-viewport')
      const filters = rect('.log-filters')
      contains(panel, filters, '日志筛选区')
      contains(panel, logViewport, '日志滚动区')
      if (toolbar && logViewport && toolbar.bottom > logViewport.y + epsilon) issues.push('日志工具栏与滚动区重叠')
      if (filters && logViewport && filters.bottom > logViewport.y + epsilon) issues.push('日志筛选区与滚动区重叠')
      if (toolbar && filters && toolbar.bottom > filters.y + epsilon) issues.push('日志工具栏与筛选区重叠')
      hitTarget('[aria-label="搜索日志"]', panel)
      hitTarget('[aria-label="日志级别"]', panel)
      hitTarget('[aria-label="导出"]', panel)
      hitTarget('[aria-label="下一个错误"]', panel)
    } else {
      if (document.querySelector('.log-viewport')) issues.push('日志收起后仍显示滚动区')
      if (panel && panel.height > 44) issues.push(`日志收起高度超过44px：${panel.height}`)
    }
    if (document.documentElement.scrollWidth > innerWidth + epsilon) issues.push('页面出现水平溢出')
    return { issues, bounds }
  }, scenario)
}

async function checkLogVisibility(page, result) {
  const search = page.getByRole('textbox', { name: '搜索日志' })
  const diagnosticToggle = page.getByRole('button', { name: '显示诊断日志', exact: true })
  const checkCount = async (query, expected) => {
    await search.fill(query)
    await settle(page)
    const count = await page.locator('.log-message').count()
    if (count !== expected) result.issues.push(`搜索 ${query} 应显示 ${expected} 条，实际 ${count} 条`)
  }
  try {
    await checkCount('layout-fixture-39', 1)
    for (const query of ['diagnostic-debug-fixture', 'diagnostic-info-fixture']) await checkCount(query, 0)
    for (const query of ['diagnostic-warning-fixture', 'diagnostic-error-fixture']) await checkCount(query, 1)
    await diagnosticToggle.click()
    for (const query of ['diagnostic-debug-fixture', 'diagnostic-info-fixture']) await checkCount(query, 1)
  } finally {
    if (await diagnosticToggle.getAttribute('aria-pressed') === 'true') await diagnosticToggle.click()
    await search.fill('')
    await page.locator('.log-filters').evaluate((element) => { element.scrollTop = 0 })
    await settle(page)
  }
}

async function checkResizing(page, scenario, result, viewportName) {
  const handle = page.getByRole('separator', { name: '调整日志高度' })
  const panel = page.locator('.log-panel')
  const height = async () => (await panel.boundingBox()).height
  const checkStage = async (stage) => {
    await page.locator('.log-filters').evaluate((element) => { element.scrollTop = 0 })
    await settle(page)
    const checked = await inspectLayout(page, scenario)
    result.issues.push(...checked.issues.map((issue) => `${stage}：${issue}`))
    if (!(await checkModalAccess(page))) result.issues.push(`${stage}：模态框遮挡全局急停`)
    result.resizeStages ??= []
    result.resizeStages.push({ stage, height: await height(), min: Number(await handle.getAttribute('aria-valuemin')), max: Number(await handle.getAttribute('aria-valuemax')) })
  }
  await handle.press('Home')
  await settle(page)
  const min = Number(await handle.getAttribute('aria-valuemin'))
  if (Math.abs(await height() - min) > 1) result.issues.push('Home 未切换到最小日志高度')
  await checkStage('最小高度')
  const handleBox = await handle.boundingBox()
  const initialHeight = await height()
  const max = Number(await handle.getAttribute('aria-valuemax'))
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2)
  await page.mouse.down()
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2 - 100, { steps: 6 })
  await page.mouse.up()
  await settle(page)
  const draggedHeight = await height()
  if (max > min + 1 && draggedHeight <= initialHeight + 1) result.issues.push('向上拖动未增加日志高度')
  if (Math.abs(draggedHeight - Math.min(max, initialHeight + 100)) > 2) result.issues.push('拖动高度未按位移增长或未遵守上限')
  await checkStage('向上拖动')
  await handle.press('End')
  await settle(page)
  if (Math.abs(await height() - Number(await handle.getAttribute('aria-valuemax'))) > 1) result.issues.push('End 未切换到最大日志高度')
  await checkStage('最大高度')
  if (scenario.name === 'idle-open') await page.screenshot({ path: join(outputDirectory, `${viewportName}-idle-expanded.png`), animations: 'disabled' })
  await handle.press('ArrowDown')
  await settle(page)
  const reducedHeight = await height()
  if (Math.abs(reducedHeight - Math.max(min, max - 32)) > 1) result.issues.push('ArrowDown 未按32px调整高度')
  await handle.press('ArrowUp')
  await settle(page)
  if (Math.abs(await height() - Math.min(max, reducedHeight + 32)) > 1) result.issues.push('ArrowUp 未按32px调整高度')
  await handle.press('Home')
  await settle(page)
  const restoredHeight = await height()
  await page.getByRole('button', { name: '最大化日志', exact: true }).click()
  await checkStage('最大化按钮')
  if (Math.abs(await height() - Number(await handle.getAttribute('aria-valuemax'))) > 1) result.issues.push('最大化按钮未展开至上限')
  await page.getByRole('button', { name: '还原日志高度', exact: true }).click()
  await settle(page)
  if (Math.abs(await height() - restoredHeight) > 1) result.issues.push('还原按钮未恢复原高度')
  await checkStage('还原按钮')

  const filtersButton = page.getByRole('button', { name: '日志筛选', exact: true })
  try {
    await filtersButton.click()
    await checkStage('最小高度与展开筛选')
    await handle.press('End')
    await checkStage('最大高度与展开筛选')
    if (scenario.name === 'idle-open') await page.screenshot({ path: join(outputDirectory, `${viewportName}-filters-open.png`), animations: 'disabled' })
  } finally {
    if (await filtersButton.getAttribute('aria-expanded') === 'true') await filtersButton.click()
    await page.locator('.log-filters').evaluate((element) => { element.scrollTop = 0 })
    await settle(page)
  }
}

async function checkModalAccess(page) {
  await page.evaluate(() => {
    const mask = document.createElement('div')
    mask.id = 'console-layout-modal-fixture'
    mask.className = 'ui-modal-mask'
    mask.setAttribute('role', 'presentation')
    const dialog = document.createElement('div')
    dialog.className = 'ui-modal'
    dialog.setAttribute('role', 'dialog')
    dialog.setAttribute('aria-label', '离线布局测试弹窗')
    dialog.textContent = '离线布局测试弹窗：急停应保持可见且可点击。'
    mask.append(dialog)
    document.body.append(mask)
  })
  try {
    await settle(page)
    return await page.locator('[aria-label="全局急停"]').evaluate((button) => {
      const rect = button.getBoundingClientRect()
      const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
      return button.contains(hit)
    })
  } finally {
    await page.evaluate(() => document.getElementById('console-layout-modal-fixture')?.remove())
  }
}

try {
  await mkdir(outputDirectory, { recursive: true })
  server = await createServer({
    root: frontendRoot,
    configFile: join(frontendRoot, 'vite.config.ts'),
    server: { host: '127.0.0.1', port: 5187, strictPort: true, open: false },
  })
  await server.listen()
  const executablePath = [
    chromium.executablePath(),
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  ].find((candidate) => existsSync(candidate))
  assert.ok(executablePath, '未找到 Playwright Chromium 或已安装的 Chrome/Edge；本脚本不会自动安装浏览器。')
  browser = await chromium.launch({ executablePath, headless: true })

  for (const viewport of viewports) {
    const viewportName = `${viewport.width}x${viewport.height}`
    const context = await browser.newContext({ viewport, deviceScaleFactor: 1, reducedMotion: 'reduce', serviceWorkers: 'block' })
    context.setDefaultTimeout(10000)
    await context.route('**/*', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      // 除 Vite 静态模块请求外全部阻断，也覆盖同源 API 配置。
      if (url.origin !== origin || /^\/api(?:\/|$)/.test(url.pathname) || !['GET', 'HEAD'].includes(request.method())) {
        report.blockedHttp += 1
        await route.abort('blockedbyclient')
      } else await route.continue()
    })
    await context.routeWebSocket('**/*', (socket) => {
      // Vite 模块加载需要自己的热更新连接；仅放行本脚本启动的本地服务。
      if (new URL(socket.url()).origin === origin.replace('http:', 'ws:')) {
        socket.connectToServer()
        return
      }
      report.blockedWebSockets += 1
      socket.close({ code: 1000, reason: '离线布局测试' })
    })
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'sendBeacon', { configurable: true, value: () => false })
    })
    const page = await context.newPage()
    const pageErrors = []
    page.on('pageerror', (error) => pageErrors.push(error.stack ?? error.message))
    try {
      await page.goto(`${origin}/settings?mode=observe#force-left`, { waitUntil: 'domcontentloaded', timeout: 30000 })
      await page.locator('#force-left').waitFor({ state: 'attached', timeout: 15000 }).catch(async (error) => {
        await page.screenshot({ path: join(outputDirectory, `${viewportName}-startup-failed.png`) })
        console.error(await page.locator('body').innerText(), pageErrors.slice(0, 3))
        throw error
      })
      await page.getByRole('button', { name: '全局急停', exact: true }).waitFor()

      for (const scenario of scenarios) {
        const result = { viewport: viewportName, scenario: scenario.name, issues: [], pageErrors: [] }
        try {
          await injectScenario(page, scenario)
          Object.assign(result, await inspectLayout(page, scenario))
          // 同时滚动页面和实际内容容器，防止安全区随长设置页离开视口。
          await page.evaluate(() => {
            document.querySelector('.main-content')?.scrollTo(0, 0)
            window.scrollTo(0, 0)
          })
          await settle(page)
          const before = await page.getByRole('button', { name: '全局急停', exact: true }).boundingBox()
          await page.evaluate(() => {
            const main = document.querySelector('.main-content')
            main?.scrollTo(0, main.scrollHeight)
            window.scrollTo(0, document.documentElement.scrollHeight)
          })
          await settle(page)
          const scrolled = await inspectLayout(page, scenario)
          result.issues.push(...scrolled.issues.map((issue) => `滚动后：${issue}`))
          const after = await page.getByRole('button', { name: '全局急停', exact: true }).boundingBox()
          if (!before || !after || Math.abs(before.y - after.y) > 1) result.issues.push('滚动主页面改变了全局急停位置')
          if (!(await checkModalAccess(page))) result.issues.push('模态框遮挡全局急停')
          await page.locator('#force-left').evaluate((card) => card.scrollIntoView({ block: 'start' }))
          await settle(page)
          await page.screenshot({ path: join(outputDirectory, `${viewportName}-${scenario.name}.png`), animations: 'disabled' })
          if (scenario.name === 'idle-open') {
            if (await page.locator('.log-advanced-filters').count()) result.issues.push('日志筛选未默认折叠')
            if (await page.getByRole('button', { name: '显示诊断日志', exact: true }).getAttribute('aria-pressed') !== 'false') result.issues.push('诊断日志未默认收起')
            await checkLogVisibility(page, result)
          }
          if (scenario.open) await checkResizing(page, scenario, result, viewportName)
          result.pageErrors = [...pageErrors]
          if (pageErrors.length) result.issues.push('页面出现未捕获异常')
        } catch (error) {
          result.issues.push(error.stack ?? String(error))
          await page.screenshot({ path: join(outputDirectory, `${viewportName}-${scenario.name}-failed.png`) }).catch(() => {})
        }
        report.cases.push(result)
        console.log(`${result.issues.length ? 'FAIL' : 'PASS'} ${viewportName}/${scenario.name}${result.issues.length ? `: ${result.issues.join('；')}` : ''}`)
      }
    } finally {
      await context.close()
    }
  }
  const failures = report.cases.filter((result) => result.issues.length > 0)
  assert.equal(failures.length, 0, `${failures.length} 个布局场景失败，详见 ${join(outputDirectory, 'report.json')}`)
  console.log(`布局回归通过：${report.cases.length} 个场景；已隔离 ${report.blockedHttp} 个 HTTP 请求与 ${report.blockedWebSockets} 个 WebSocket。`)
} finally {
  await writeFile(join(outputDirectory, 'report.json'), `${JSON.stringify(report, null, 2)}\n`).catch(() => {})
  await browser?.close()
  await server?.close()
}

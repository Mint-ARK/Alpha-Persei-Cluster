/**
 * 用无头浏览器给构建产物截图，便于快速核对视觉效果。
 * 用法：npm run build && npm run shot
 */
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const page = join(root, '..', 'static_web', 'control-panel.html')
const outDir = join(root, 'scratch_shots')

if (!existsSync(page)) {
  console.error('[shot] 未找到 static_web/control-panel.html，请先执行 npm run build')
  process.exit(1)
}

const browser = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
].find((candidate) => existsSync(candidate))

if (!browser) {
  console.error('[shot] 未找到 Edge 或 Chrome')
  process.exit(1)
}

mkdirSync(outDir, { recursive: true })

const base = pathToFileURL(page).href

const panels = [
  'overview',
  'accounts',
  'heroes',
  'inventory',
  'mail',
  'gacha',
  'shop',
  'aichat',
  'settings',
  'agreement',
]

const targets = panels.map((id) => ({
  name: `${id}.png`,
  url: `${base}#${id}`,
  size: '1600,1500',
}))

console.log(`[shot] 启动无头浏览器生成 ${targets.length} 张面板实拍截图...`)

for (const target of targets) {
  const outPath = join(outDir, target.name)
  const args = [
    '--headless=new',
    '--disable-gpu',
    `--window-size=${target.size}`,
    '--hide-scrollbars',
    '--virtual-time-budget=2000',
    `--screenshot=${outPath}`,
    target.url,
  ]
  const res = spawnSync(browser, args, { stdio: 'pipe' })
  if (res.status === 0 && existsSync(outPath)) {
    console.log(`  ✓ ${target.name}`)
  } else {
    console.warn(`  ✗ ${target.name} 失败`, res.stderr?.toString() || '')
  }
}

console.log(`[shot] 截图完成，保存在：${outDir}`)

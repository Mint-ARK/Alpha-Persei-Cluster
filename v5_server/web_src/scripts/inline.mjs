/**
 * 构建后处理：把 static_web 中的 app.js 与 app.css 内联进 HTML，
 * 产出可以直接双击用浏览器打开的单文件脱机预览，并更新 GM 控制台与关于页。
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const targetDir = join(root, '..', 'static_web')
const htmlPath = join(targetDir, 'index.html')

if (!existsSync(htmlPath)) {
  console.error(`[inline] 未找到 ${htmlPath}，请先执行 vite build`)
  process.exit(1)
}

const readAsset = (name) => readFileSync(join(targetDir, name.replace(/^\.?\//, '')), 'utf8')

let html = readFileSync(htmlPath, 'utf8')

html = html.replace(/<script[^>]*\ssrc="([^"]+\.js)"[^>]*><\/script>/g, (_match, src) => {
  const code = readAsset(src).replace(/<\/script/gi, '<\\/script')
  return `<script type="module">\n${code}\n</script>`
})

html = html.replace(/<link[^>]*rel="stylesheet"[^>]*\shref="([^"]+\.css)"[^>]*>/g, (_match, href) => {
  return `<style>\n${readAsset(href)}\n</style>`
})

const outPath = join(targetDir, 'control-panel.html')
writeFileSync(outPath, html, 'utf8')

const kb = (Buffer.byteLength(html, 'utf8') / 1024).toFixed(0)
console.log(`[inline] 单文件脱机预览已生成：${outPath} (${kb} KB)`)

// 规范生成 GM 控制台标准入口（引用同级编译后的 app.js 与 app.css）
const gmConsoleHtml = `<!doctype html>
<html lang="zh-CN" data-theme="light">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="color-scheme" content="light dark" />
    <title>深空之眼 · GM 运维管理控制台</title>
    <script type="module" crossorigin src="./app.js"></script>
    <link rel="stylesheet" crossorigin href="./app.css">
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
`
writeFileSync(join(targetDir, 'gm_console.html'), gmConsoleHtml, 'utf8')

// 规范生成 关于/协议面板标准入口
const agreementTemplatePath = join(root, 'templates', 'agreement.html')
if (existsSync(agreementTemplatePath)) {
  const agreementContent = readFileSync(agreementTemplatePath, 'utf8')
  writeFileSync(join(targetDir, 'agreement.html'), agreementContent, 'utf8')
  console.log(`[inline] 协议/关于页已更新：${join(targetDir, 'agreement.html')}`)
}

console.log(`[inline] 控制台所有产物已直接就位：${targetDir}`)

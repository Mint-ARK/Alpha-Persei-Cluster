// Exercise the actual API client with bounded fetch/timer fixtures, no network.
import { buildSync } from 'esbuild'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import vm from 'node:vm'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const code = buildSync({ entryPoints: [resolve(root, 'src/lib/api.ts')], bundle: true,
  platform: 'node', format: 'cjs', write: false }).outputFiles[0].text
const timers = new Map()
const storage = new Map()
let timerId = 0
let calls = 0
let fetcher
const module = { exports: {} }
const context = {
  module, exports: module.exports, AbortController,
  console: { warn() {} },
  localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
  window: { location: { protocol: 'http:' },
    setTimeout: (fn, ms) => { assert.equal(ms, 10000); timers.set(++timerId, fn); return timerId },
    clearTimeout: id => timers.delete(id), dispatchEvent() {} },
  fetch: (...args) => { calls++; return fetcher(...args) },
}
vm.runInNewContext(code, context)
const api = module.exports
const good = { code: 0, data: { saves: [], presets: [] } }
const response = data => ({ ok: true, status: 200, json: async () => data })
let checks = 0
fetcher = async () => response(good)
assert.equal((await api.fetchSaveCatalog()).data.saves.length, 0); checks++
fetcher = async () => ({ ok: false, status: 404, statusText: 'Not Found' })
let before = calls
await assert.rejects(api.fetchSaveCatalog(), /尚未加载存档管理接口/)
assert.equal(calls - before, 1); checks++
fetcher = async () => response({ errorCode: '0', data: {} })
await assert.rejects(api.fetchSaveCatalog(), /有效的存档列表/); checks++
fetcher = async () => { throw new Error('offline') }
await assert.rejects(api.fetchSaveCatalog(), /offline/); checks++
fetcher = (_url, opts) => new Promise((_resolve, reject) => {
  opts.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true })
})
before = calls
const pending = api.fetchSaveCatalog()
assert.equal(timers.size, 1)
for (const fire of timers.values()) fire()
await assert.rejects(pending, /超时/)
assert.equal(calls - before, 1); checks++
assert.equal(timers.size, 0); checks++
fetcher = async () => response(good)
assert.equal((await api.fetchSaveCatalog()).code, 0); checks++
api.setSelectedSave('很长的中文存档名称_70级.db')
fetcher = async (url, opts) => {
  assert.equal(opts.headers['X-Save-Name'], encodeURIComponent('很长的中文存档名称_70级.db'))
  return response({ code: 0, data: {} })
}
await api.fetchOverviewStatus(); checks++
console.log(`PASS ${checks} save-list client checks; no network, timers cleared, no automatic retries`)

import assert from 'node:assert/strict'
import { buildSync } from 'esbuild'
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const code = buildSync({ entryPoints: [resolve(root, 'src/lib/avatarAssets.ts')], bundle: true,
  platform: 'node', format: 'cjs', write: false }).outputFiles[0].text
const module = { exports: {} }
vm.runInNewContext(code, { module, exports: module.exports })
const { PLAYER_AVATARS, avatarAssetPath } = module.exports
assert.equal(PLAYER_AVATARS.length, 216)
for (const item of PLAYER_AVATARS) {
  assert.equal(typeof item.id, 'number')
  const actual = avatarAssetPath(item.id)
  assert.ok(actual.endsWith('/' + item.resource))
  assert.ok(existsSync(resolve(root, '../static_web', actual.replace(/\.png$/, '.webp'))), actual)
}
assert.equal(avatarAssetPath(2110111), 'extracted_assets/avatars/1011.png')
assert.equal(avatarAssetPath(2200116), 'extracted_assets/avatars/2200008.png')
assert.equal(avatarAssetPath(1084), 'extracted_assets/avatars/1084.png')
console.log('PASS all 216 avatar ID/resource paths; alternate resource names and legacy portrait display verified')

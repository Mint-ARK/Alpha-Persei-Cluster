// Exercise real form transitions and request building; no API/database writes.
import { buildSync } from 'esbuild'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const output = buildSync({ entryPoints: [resolve(root, 'src/lib/saveForm.ts')], bundle: true,
  platform: 'node', format: 'cjs', write: false }).outputFiles[0].text
const module = { exports: {} }
vm.runInNewContext(output, { module, exports: module.exports })
const { applySavePreset, changeSaveOptions, matchSavePreset, saveFormError, saveFormPayload,
  saveNameLength, acceptUidInput, UID_MAX_DIGITS, SAVE_NAME_MAX_CHARS } = module.exports
const initial = { level: 1, skip_intro: false, skip_feature_guides: false, clear_through: null,
  noob_tasks_ready: false, support_mail: true }
const presets = [
  { key: 'fresh', options: initial },
  { key: 'skip_prologue', options: { ...initial, skip_intro: true, clear_through: 0 } },
  { key: 'quick_start', options: { ...initial, level: 70, skip_intro: true, skip_feature_guides: true,
    clear_through: 5, noob_tasks_ready: true } },
]
let checks = 0
assert.equal(matchSavePreset(initial, presets), 'fresh'); checks++
let controls = { ...initial, uid: '261026123456', nick: '保留名字' }
controls = applySavePreset(controls, presets[2].options)
assert.equal(matchSavePreset(controls, presets), 'quick_start')
assert.equal(controls.uid, '261026123456'); assert.equal(controls.nick, '保留名字'); checks++
controls = changeSaveOptions(controls, { level: 69 })
assert.equal(matchSavePreset(controls, presets), 'custom')
controls = changeSaveOptions(controls, { level: 70 })
assert.equal(matchSavePreset(controls, presets), 'quick_start'); checks++
controls = applySavePreset(controls, presets[0].options)
assert.equal(controls.clear_through, null); assert.equal(controls.skip_intro, false)
assert.equal(controls.nick, '保留名字'); checks++
controls = changeSaveOptions(controls, { clear_through: 5 })
assert.equal(controls.clear_through, null); assert.equal(controls.skip_intro, false); checks++
controls = changeSaveOptions(controls, { skip_intro: true })
controls = changeSaveOptions(controls, { clear_through: 0 })
assert.equal(matchSavePreset(controls, presets), 'skip_prologue'); checks++
controls = changeSaveOptions(controls, { support_mail: false })
assert.equal(matchSavePreset(controls, presets), 'custom')
controls = changeSaveOptions(controls, { support_mail: true })
assert.equal(matchSavePreset(controls, presets), 'skip_prologue'); checks++
controls = changeSaveOptions(controls, { skip_intro: false })
assert.equal(controls.clear_through, null)
assert.equal(matchSavePreset(controls, presets), 'fresh'); checks++
const payload = saveFormPayload('中文新档', controls, true)
assert.equal(payload.options.nick, null); assert.equal(payload.options.uid, '261026123456')
assert.equal(payload.options.clear_through, null)
assert.equal(payload.preset, undefined)
assert.equal(payload.preview, true)
assert.equal(Object.keys(payload.options).length, 8); checks++
assert.equal(saveFormError(controls), '')
for (const level of [NaN, 0, 101, 1.5]) assert.ok(saveFormError({ ...controls, level }))
assert.ok(saveFormError({ ...controls, uid: '0123' }))
assert.ok(saveFormError({ ...controls, uid: '中文' })); checks++
const beforeCustom = JSON.stringify(controls)
assert.equal(matchSavePreset(controls, presets, true), 'custom')
assert.equal(JSON.stringify(controls), beforeCustom); checks++
controls = changeSaveOptions(controls, { nick: '自定义名字' }, true)
assert.equal(matchSavePreset(controls, presets, true), 'custom')
assert.equal(saveFormPayload('中文新档', controls, false, true).options.nick, '自定义名字'); checks++
controls = changeSaveOptions(controls, { clear_through: 5 }, true)
assert.equal(controls.clear_through, 5)
assert.equal(controls.skip_intro, false)
assert.match(saveFormError(controls), /跳过501/)
assert.equal(saveFormPayload('中文新档', controls, false, true).options.clear_through, 5); checks++
controls = changeSaveOptions(controls, { skip_intro: true }, true)
assert.equal(saveFormError(controls), '')
assert.equal(controls.clear_through, 5); checks++
controls = applySavePreset(controls, presets[0].options)
assert.equal(matchSavePreset(controls, presets), 'fresh')
assert.equal(controls.nick, '自定义名字')
assert.equal(controls.clear_through, null); checks++
controls = applySavePreset(controls, presets[2].options)
assert.equal(matchSavePreset(controls, presets, true), 'custom')
assert.equal(matchSavePreset(controls, presets), 'quick_start'); checks++
assert.equal(UID_MAX_DIGITS, 12)
assert.equal(SAVE_NAME_MAX_CHARS, 32)
assert.equal(saveFormError({ ...initial, uid: '999999999999' }), '')
assert.match(saveFormError({ ...initial, uid: '1000000000000' }), /12位/); checks++
for (const name of ['中'.repeat(32), 'x'.repeat(32), '🐉'.repeat(32)]) {
  assert.equal(saveNameLength(name + '.DB'), 32)
  assert.equal(saveFormError(initial, name), '')
  assert.equal(saveFormError(initial, name + '.db'), '')
  assert.match(saveFormError(initial, name + 'x'), /32个字符/)
}
checks++
for (const value of ['', '1', '1200', '261026123456', '999999999999']) assert.equal(acceptUidInput(value), true, value)
checks++
for (const value of ['0', '0123', 'a', '12a3', '中文', '１２３', '-123', '+123', '1.2', '1e3',
  '12 3', ' 123', '123 ', '123\n', '1\t2', '1,234', '🐉', '1234567890123']) {
  assert.equal(acceptUidInput(value), false, JSON.stringify(value))
}
checks++
console.log(`PASS ${checks} form checks: bidirectional matching, dependencies, identity preservation and explicit payload`)

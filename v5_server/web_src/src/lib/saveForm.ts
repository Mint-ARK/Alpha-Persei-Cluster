import type { SaveCatalog, SaveOptions } from './api'

export const UID_MAX_DIGITS = 12
export const SAVE_NAME_MAX_CHARS = 32

export function acceptUidInput(value: string): boolean {
  return value.length <= UID_MAX_DIGITS && !/[^0-9]/.test(value)
    && (value === '' || value[0] !== '0')
}

export function saveNameLength(name: string): number {
  const stem = name.toLowerCase().endsWith('.db') ? name.slice(0, -3) : name
  return Array.from(stem).length
}

// Presets describe these controls only. Save identity stays independent.
const presetFields = ['level', 'skip_intro', 'skip_feature_guides', 'clear_through',
  'noob_tasks_ready', 'support_mail'] as const

export function changeSaveOptions(current: SaveOptions, patch: Partial<SaveOptions>, customEditing = false): SaveOptions {
  const next = { ...current, ...patch }
  if (!customEditing && !next.skip_intro) next.clear_through = null
  return next
}

export function applySavePreset(current: SaveOptions, preset: SaveOptions): SaveOptions {
  const patch = Object.fromEntries(presetFields.map(field => [field, preset[field]]))
  return changeSaveOptions(current, patch)
}

export function matchSavePreset(options: SaveOptions, presets: SaveCatalog['presets'], explicitCustom = false): string {
  if (explicitCustom) return 'custom'
  return presets.find(preset => presetFields.every(field => options[field] === preset.options[field]))?.key || 'custom'
}

export function saveFormError(options: SaveOptions, name = ''): string {
  if (saveNameLength(name) > SAVE_NAME_MAX_CHARS) return '存档名称最多32个字符（不含.db后缀）'
  if (!Number.isInteger(options.level) || options.level < 1 || options.level > 100) return '初始等级须为1—100的整数'
  if (options.uid && !acceptUidInput(options.uid)) return 'UID须为1—12位正整数，不能有前导零'
  if (options.skip_intro && (options.nick || '').trim().length > 16) return '初始昵称不能超过16个字符'
  if (options.clear_through !== null && !options.skip_intro) return '主线预通关需要跳过501首次剧情，请勾选跳过501或取消预通关'
  return ''
}

export function saveFormPayload(name: string, controls: SaveOptions, preview: boolean, customEditing = false) {
  // Explicit control values are the entire request; no preset key or hidden defaults.
  const options = controls
  return { name, preview, options: {
    ...Object.fromEntries(presetFields.map(field => [field, options[field]])),
    uid: options.uid || null,
    nick: options.skip_intro || customEditing ? options.nick || null : null,
  } as SaveOptions }
}

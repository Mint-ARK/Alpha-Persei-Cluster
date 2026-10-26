import { useState } from 'react'
import { Button, Card, Checkbox, Description, Input, Label, ListBox, NumberField, Select, TextField } from '@heroui/react'
import { Database, Plus, RefreshCw } from 'lucide-react'
import { createSave, type SaveOptions } from '../lib/api'
import { useServer } from '../lib/serverContext'
import { applySavePreset, changeSaveOptions, matchSavePreset, saveFormError, saveFormPayload,
  saveNameLength, acceptUidInput, UID_MAX_DIGITS, SAVE_NAME_MAX_CHARS } from '../lib/saveForm'

const initial: SaveOptions = { level: 1, skip_intro: false, skip_feature_guides: false,
  clear_through: null, noob_tasks_ready: false, support_mail: true }

export default function SavesPanel() {
  const { saves, selectedSave, selectSave, refreshSaves, savePresets: presets, saveCatalogLoading } = useServer()
  const [options, setOptions] = useState<SaveOptions>(initial)
  const [name, setName] = useState('')
  const [explicitCustom, setExplicitCustom] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [preview, setPreview] = useState<any>(null)
  const preset = matchSavePreset(options, presets, explicitCustom)
  const customEditing = preset === 'custom'
  const formError = saveFormError(options, name)
  const change = (patch: Partial<SaveOptions>) => { setOptions(o => changeSaveOptions(o, patch, customEditing)); setPreview(null) }
  const submit = async (isPreview: boolean) => {
    if (busy || formError) return
    setBusy(true); setMessage('')
    try {
      const res = await createSave(saveFormPayload(name, options, isPreview, customEditing))
      if (isPreview) setPreview(res.data)
      else { setMessage(`已创建 ${res.data.filename}，UID ${res.data.uid}。可在游戏选区列表进入。`); await refreshSaves() }
    } catch (e: any) { setMessage(e.message) }
    finally { setBusy(false) }
  }
  return <div className="space-y-5">
    <div className="flex items-center justify-between"><h1 className="flex items-center gap-2 text-xl font-bold"><Database />存档管理</h1>
      <Button isDisabled={saveCatalogLoading} onPress={() => refreshSaves().catch(e => setMessage(e.message))}><RefreshCw className="size-4" />刷新</Button></div>
    <p className="text-sm text-muted">选择存档后，控制面板各页与 API 配置跟随切换。进入游戏时，在选区界面选择对应存档。</p>
    {message && <p role="status" className="rounded-xl bg-accent-soft p-3 text-sm whitespace-pre-wrap">{message}</p>}
    <div className="grid gap-3 lg:grid-cols-2">
      {saves.map(save => <Card key={save.filename} className={`p-4 ${selectedSave === save.filename ? 'ring-2 ring-accent' : ''}`}>
        <div className="flex items-center justify-between gap-3"><div><h2 className="font-semibold">{save.name}</h2>
          <p className="text-sm text-muted">{save.nick} · Lv.{save.level} · UID {save.uid}</p>
          <p className="text-xs text-muted">{save.mounted ? '游戏当前挂载 · ' : ''}{save.port ? `网关 ${save.port}` : '等待选区扫描登记'}</p></div>
          <Button isDisabled={selectedSave === save.filename} onPress={() => selectSave(save.filename)}>{selectedSave === save.filename ? '管理中' : '管理此存档'}</Button></div>
      </Card>)}
    </div>
    <Card className="space-y-4 p-5"><h2 className="flex items-center gap-2 font-semibold"><Plus className="size-4" />生成新存档</h2>
      <div className="grid gap-4 md:grid-cols-2">
        <Select fullWidth selectedKey={preset} isDisabled={busy} onSelectionChange={key => {
          if (key === 'custom') { setExplicitCustom(true); return }
          const p = presets.find(row => row.key === String(key))
          if (p) { setExplicitCustom(false); setOptions(current => applySavePreset(current, p.options)); setPreview(null) }
        }}><Label>预设方案</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
          <Select.Popover><ListBox>{[...presets.map(p => ({ id: p.key, title: p.title })), { id: 'custom', title: '自定义' }].map(p =>
            <ListBox.Item key={p.id} id={p.id}>{p.title}</ListBox.Item>)}</ListBox></Select.Popover>
          <Description>{explicitCustom ? '自定义编辑中，保留当前配置；选择其他方案可恢复预设。' : '选择方案会更新下方控件；也可选择自定义，保留当前搭配自由编辑。'}</Description>
        </Select>
        <TextField fullWidth value={name} isDisabled={busy} onChange={setName}><Label>存档名称</Label><Input maxLength={(SAVE_NAME_MAX_CHARS + 3) * 2} placeholder="留空自动命名：新存档_01" />
          <Description>{saveNameLength(name)}/{SAVE_NAME_MAX_CHARS}字符，不含自动添加的.db后缀</Description>
        </TextField>
        <TextField fullWidth value={options.uid || ''} isDisabled={busy} onChange={value => {
          if (acceptUidInput(value)) change({ uid: value || null })
        }}><Label>UID</Label><Input inputMode="numeric" pattern="[0-9]*" maxLength={UID_MAX_DIGITS}
          autoComplete="off" spellCheck={false} placeholder="留空自动生成；创建后固定"
          onBeforeInput={event => {
            const text = (event.nativeEvent as InputEvent).data
            if (text && /[^0-9]/.test(text)) event.preventDefault()
          }}
          onPaste={event => {
            const input = event.currentTarget
            const text = event.clipboardData.getData('text')
            const candidate = input.value.slice(0, input.selectionStart ?? 0) + text
              + input.value.slice(input.selectionEnd ?? input.value.length)
            if (!acceptUidInput(candidate)) event.preventDefault()
          }} />
          <Description>最多{UID_MAX_DIGITS}位正整数，不能以0开头</Description>
        </TextField>
        <NumberField fullWidth minValue={1} maxValue={100} step={1} value={options.level} isDisabled={busy} onChange={level => change({ level })}>
          <Label>初始等级</Label><NumberField.Group><NumberField.DecrementButton /><NumberField.Input /><NumberField.IncrementButton /></NumberField.Group>
        </NumberField>
        <TextField fullWidth value={options.nick || ''} isDisabled={busy || (!customEditing && !options.skip_intro)} onChange={nick => change({ nick: nick || null })}><Label>初始昵称</Label><Input maxLength={16} placeholder={options.skip_intro ? '留空为管理员' : customEditing ? '可先填写，跳过501时使用' : '保留501：在剧情中取名'} />
          {!options.skip_intro && <Description>保留501剧情时，进入游戏后仍会在剧情中取名。</Description>}
        </TextField>
        <Select fullWidth selectedKey={options.clear_through === null ? 'none' : String(options.clear_through)} isDisabled={busy || (!customEditing && !options.skip_intro)} onSelectionChange={key => {
          if (key === null) return
          const n = String(key) === 'none' ? null : Number(key)
          change({ clear_through: n })
        }}><Label>主线预通关</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
          <Select.Popover><ListBox>{[{ id: 'none', title: '不预通关' }, ...[0,1,2,3,4,5].map(n => ({ id: String(n), title: n ? `至第${n}章` : '序章' }))].map(p =>
            <ListBox.Item key={p.id} id={p.id}>{p.title}</ListBox.Item>)}</ListBox></Select.Popover>
          {!options.skip_intro && <Description>{customEditing ? '预通关包含序章，生成前须同时勾选跳过501。' : '开启“跳过501首次剧情”后可设置预通关。'}</Description>}
        </Select>
      </div>
      <div className="flex flex-wrap gap-5 text-sm">
        {([['skip_intro', '跳过501首次剧情'], ['skip_feature_guides', '跳过功能引导'], ['noob_tasks_ready', '新手任务待领奖'], ['support_mail', '发放助力邮件']] as const).map(([key, label]) =>
          <Checkbox key={key} isSelected={options[key]} isDisabled={busy} onChange={selected => change({ [key]: selected })}>
            <Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control><Label>{label}</Label></Checkbox.Content>
          </Checkbox>)}
      </div>
      <p className="text-xs text-muted">等级自动匹配经验；主线预通关补首通奖励，直接入存档。API 密钥和聊天记录不复制到新档。</p>
      {formError && <p role="alert" className="text-sm text-danger">{formError}</p>}
      {preview && <p className="text-sm">预览：Lv.{preview.level}，累计经验 {preview.total_exp}，预通关 {preview.cleared_stages_count} 关，首通奖励 {preview.first_clear_rewards.length} 类，助力邮件 {preview.support_mail_count} 封。</p>}
      <div className="flex gap-3"><Button isDisabled={busy || !!formError} onPress={() => submit(true)}>预览配置</Button><Button variant="primary" isDisabled={busy || !!formError} onPress={() => submit(false)}>{busy ? '处理中…' : '创建存档'}</Button></div>
    </Card>
  </div>
}

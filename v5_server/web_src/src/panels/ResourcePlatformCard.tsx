import { Button, Card, Chip, Label, ListBox, Select } from '@heroui/react'
import type { ChannelManifestSelection, ResourceDistributionData, ResourcePlatform } from '../lib/api'

export const platformNames = { pc: 'PC · Windows', android: 'Android 官服', ios: 'iPhone / iPad' }
export const platformChannels = { pc: '9', android: '7', ios: '7' }

export default function ResourcePlatformCard({ platform, data, value, onChange, onPrepare, disabled }: {
  platform: ResourcePlatform; data: ResourceDistributionData; value: ChannelManifestSelection
  onChange: (value: ChannelManifestSelection) => void; disabled: boolean
  onPrepare: (filename: string) => void
}) {
  const row = data.platforms[platform]
  const available = data.profiles.filter(p => p.platform === platform && p.ready && !p.removed)
  const natives = available.filter(p => p.origin === 'native')
  const saved = (data.saved_downloads || []).filter(row => row.platform === platform)
  const pending = value.selected_id === 'auto' ? null : available.find(p => p.id === value.selected_id)
  const currentAppVer = value.app_version || (platform === 'pc' ? '307' : '308')
  const basePlatform = platform === 'pc' ? 'Windows' : platformNames[platform]
  const baseAppOptions = [
    { id: 'auto', label: '自动 (跟随客户端请求免更)' },
    { id: '307', label: `Build 307 (${basePlatform})` },
    { id: '308', label: `Build 308 (${basePlatform})` },
  ]
  const hasCurrentInOptions = baseAppOptions.some(o => o.id === currentAppVer)
  const appOptions = hasCurrentInOptions ? baseAppOptions : [...baseAppOptions, { id: currentAppVer, label: `Build ${currentAppVer} (自定义)` }]

  return <Card className="min-w-0 space-y-4 p-5">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold">{platformNames[platform]}</h3>
      <Chip size="sm" variant="soft">渠道 {row.channel_id}</Chip>
    </div>

    <Select aria-label={`${platformNames[platform]}分发方案`} selectedKey={value.selected_id}
      onSelectionChange={key => { if (key) onChange({ ...value, selected_id: String(key) }) }} isDisabled={disabled}>
      <Label>分发方案</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
      <Select.Popover><ListBox><ListBox.Item id="auto" textValue="自动选择原生清单">自动选择原生清单</ListBox.Item>
        {available.map(p => <ListBox.Item key={p.id} id={p.id} textValue={p.label}>{p.label}</ListBox.Item>)}
      </ListBox></Select.Popover>
    </Select>

    {saved.length > 0 && <div className="space-y-2">
      <p className="text-xs text-muted">已保存的主清单可补齐中日语索引后登记为分发方案。</p>
      {saved.map(({ filename }) => <div key={filename} className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 break-all font-mono text-xs" title={filename}>{filename.split('_').slice(1, 3).join(' / ')} · {filename.slice(-14, -6)}</span>
        <Button size="sm" variant="secondary" isDisabled={disabled} onPress={() => onPrepare(filename)}>准备分发</Button>
      </div>)}
    </div>}

    <Select aria-label={`${platformNames[platform]}底包版本`} selectedKey={currentAppVer}
      onSelectionChange={key => { if (key) onChange({ ...value, app_version: String(key) }) }} isDisabled={disabled}>
      <Label>底包版本 · 安装包管理</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
      <Select.Popover><ListBox>
        {appOptions.map(o => <ListBox.Item key={o.id} id={o.id} textValue={o.label}>{o.label}</ListBox.Item>)}
      </ListBox></Select.Popover>
    </Select>

    <Select aria-label={`${platformNames[platform]}保险方案`} selectedKey={value.fallback_id}
      onSelectionChange={key => { if (key) onChange({ ...value, fallback_id: String(key) }) }} isDisabled={disabled}>
      <Label>保险方案 · 原生BASE</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
      <Select.Popover><ListBox>{natives.map(p => <ListBox.Item key={p.id} id={p.id} textValue={p.label}>{p.label}</ListBox.Item>)}</ListBox></Select.Popover>
    </Select>

    <p className="text-xs leading-relaxed text-muted">
      {platform === 'pc'
        ? '自动：Windows本机已登记清单优先，否则使用最新原生清单。底包版本控制下发给客户端的安装包检测参数。'
        : '自动：使用本渠道最新原生清单。底包版本控制下发给客户端的安装包检测参数，设为“自动”可免除换端更新底包弹窗。'}
    </p>

    <div className="space-y-2 rounded-xl bg-surface-secondary p-3 text-xs">
      <p className="font-medium">当前生效：{row.effective.label}</p>
      <p className="text-muted">底包对齐：{row.app_version === 'auto' ? '自动跟随客户端请求' : `Build ${row.app_version || (platform === 'pc' ? '307' : '308')} 底包`}</p>
      <p className="text-muted">业务基线：Build {row.effective.base_resource_version || row.effective.resource_version} · 登录连接独立判定</p>
      <p className="break-all font-mono text-muted">{row.effective.assethash}</p>
      <p className="text-muted">{row.effective.origin === 'derived' ? `BASE：${row.effective.base_label}` : '已核验原生来源'}</p>
    </div>

    {pending && pending.id !== row.effective_id && <p className="text-xs text-accent">待应用方案：{pending.label}</p>}
    {value.app_version && value.app_version !== row.app_version && (
      <p className="text-xs text-accent">待应用底包：{value.app_version === 'auto' ? '自动跟随' : `Build ${value.app_version}`}</p>
    )}
    {row.notice && <p className="text-xs text-warning">{row.notice}</p>}
  </Card>
}

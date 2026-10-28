import { useEffect, useState } from 'react'
import { Gauge, RefreshCw, Save } from 'lucide-react'
import { Button, Card, Chip, Label, NumberField, Slider } from '@heroui/react'
import { fetchResourceDownloadSettings, saveResourceDownloadSettings,
  type ResourceDownloadSettings, type UpdateProgressData } from '../lib/api'

export default function ResourceDownloadControls({ online, progress }: {
  online: boolean; progress: UpdateProgressData | null
}) {
  const [settings, setSettings] = useState<ResourceDownloadSettings | null>(null)
  const [value, setValue] = useState(32)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const result = await fetchResourceDownloadSettings()
      if (result.code !== 0 || !result.data) throw new Error(result.msg || '读取下载设置失败')
      setSettings(result.data)
      setValue(result.data.max_concurrency)
    } catch (e: any) { setError(e.message || '读取下载设置失败') }
    finally { setLoading(false) }
  }
  useEffect(() => { if (online) void load() }, [online])
  const change = (next: number) => {
    if (Number.isFinite(next)) {
      setValue(Math.max(settings?.min_concurrency ?? 1, Math.min(settings?.max_allowed ?? 128, Math.round(next))))
      setNotice('')
    }
  }
  const apply = async () => {
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const result = await saveResourceDownloadSettings(value)
      if (result.code !== 0 || !result.data) throw new Error(result.msg || '保存下载设置失败')
      setSettings(result.data)
      setValue(result.data.max_concurrency)
      setNotice('已应用，当前任务与后续任务均使用此设置')
    } catch (e: any) { setError(e.message || '保存下载设置失败') }
    finally { setSaving(false) }
  }
  const disabled = !online || !settings || loading || saving
  const changed = settings !== null && value !== settings.max_concurrency
  const busy = progress && ['checking', 'downloading', 'cancelling', 'registering'].includes(progress.status)

  return <Card className="p-5 space-y-4 lg:col-span-2">
    <div className="flex items-start justify-between gap-3">
      <div className="flex items-center gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-2xl bg-accent/10 text-accent"><Gauge className="size-5" /></span>
        <div><h3 className="font-semibold text-foreground">主动下载并发</h3>
          <p className="mt-0.5 text-xs text-muted">控制同时处理的资源数量，适用于主动资源同步。</p></div>
      </div>
      <Button isIconOnly size="sm" variant="ghost" aria-label="刷新下载设置" onPress={load} isDisabled={!online || loading || saving}>
        <RefreshCw className={loading ? 'size-4 animate-spin' : 'size-4'} />
      </Button>
    </div>
    <div className="grid items-end gap-5 sm:grid-cols-[minmax(0,1fr)_9rem]">
      <div className="min-w-0 space-y-3">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Chip size="sm" variant="soft" color="accent">{settings ? `已应用 ${settings.max_concurrency} 路` : !online ? '服务未启动' : error ? '设置未载入' : '正在读取…'}</Chip>
          <span className="text-muted">默认 {settings?.default_concurrency ?? 32} 路</span>
          {busy && <span className="text-accent">正在处理 {progress?.active_downloads ?? 0} 项</span>}
        </div>
        <Slider aria-label="主动下载并发上限" minValue={settings?.min_concurrency ?? 1}
          maxValue={settings?.max_allowed ?? 128} step={1} value={value} isDisabled={disabled}
          onChange={next => change(typeof next === 'number' ? next : next[0])}>
          <Slider.Track><Slider.Fill /><Slider.Thumb /></Slider.Track>
        </Slider>
        <div className="flex justify-between text-[11px] tabular-nums text-muted"><span>1 路</span><span>128 路</span></div>
      </div>
      <NumberField aria-label="主动下载并发数" minValue={settings?.min_concurrency ?? 1}
        maxValue={settings?.max_allowed ?? 128} step={1} value={value} onChange={change} isDisabled={disabled}>
        <Label>并发上限</Label><NumberField.Group><NumberField.DecrementButton /><NumberField.Input /><NumberField.IncrementButton /></NumberField.Group>
      </NumberField>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-separator pt-4">
      <p className="max-w-lg text-xs leading-relaxed text-muted">游戏客户端的被动下载不受此项限制。调低后，正在处理的文件会正常完成。</p>
      <div className="flex shrink-0 gap-2">
        <Button size="sm" variant="secondary" isDisabled={disabled || value === (settings?.default_concurrency ?? 32)}
          onPress={() => change(settings?.default_concurrency ?? 32)}>恢复默认</Button>
        <Button size="sm" variant="primary" isDisabled={disabled || !changed} onPress={apply}>
          <Save className="size-3.5" />{saving ? '正在保存…' : '应用并保存'}
        </Button>
      </div>
    </div>
    {(error || settings?.warning) && <p role="alert" className="text-xs text-danger">{error || settings?.warning}</p>}
    {notice && <p role="status" className="text-xs text-success">{notice}</p>}
  </Card>
}

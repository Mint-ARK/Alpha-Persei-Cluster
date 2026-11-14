import { useEffect, useState } from 'react'
import { Gauge, RefreshCw, Save } from 'lucide-react'
import { Button, Card, Chip, Input, Label, NumberField, Slider } from '@heroui/react'
import { fetchResourceDownloadSettings, saveResourceDownloadSettings,
  type ResourceDownloadSettings, type UpdateProgressData } from '../lib/api'

export default function ResourceDownloadControls({ online, progress }: {
  online: boolean; progress: UpdateProgressData | null
}) {
  const [settings, setSettings] = useState<ResourceDownloadSettings | null>(null)
  const [value, setValue] = useState(32)
  const [bandwidth, setBandwidth] = useState('0')
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
      setBandwidth(String((result.data.max_bandwidth_kibps ?? 0) / 1024))
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
  const bandwidthKiB = Math.round(Number(bandwidth) * 1024)
  const invalidBandwidth = !bandwidth.trim() || !Number.isFinite(bandwidthKiB)
    || Number(bandwidth) < 0 || (Number(bandwidth) > 0 && bandwidthKiB === 0)
  const apply = async () => {
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const result = await saveResourceDownloadSettings(value, bandwidthKiB)
      if (result.code !== 0 || !result.data) throw new Error(result.msg || '保存下载设置失败')
      setSettings(result.data)
      setValue(result.data.max_concurrency)
      setBandwidth(String((result.data.max_bandwidth_kibps ?? 0) / 1024))
      setNotice('已应用，当前任务与后续任务均使用此设置')
    } catch (e: any) { setError(e.message || '保存下载设置失败') }
    finally { setSaving(false) }
  }
  const disabled = !online || !settings || loading || saving
  const bandwidthSupported = settings?.max_bandwidth_kibps !== undefined
  const changed = settings !== null && (value !== settings.max_concurrency || bandwidthKiB !== (settings.max_bandwidth_kibps ?? 0))
  const busy = progress && ['checking', 'downloading', 'cancelling', 'registering'].includes(progress.status)

  return <Card className="p-5 space-y-4 lg:col-span-2">
    <div className="flex items-start justify-between gap-3">
      <div className="flex items-center gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-2xl bg-accent/10 text-accent"><Gauge className="size-5" /></span>
        <h3 className="font-semibold text-foreground">主动下载设置</h3>
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
    <div className="grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_9rem]">
      <div className="space-y-1 text-xs text-muted">
        {bandwidthSupported && <Chip size="sm" variant="soft" color="accent">
          {settings?.max_bandwidth_kibps ? `已限速 ${settings.max_bandwidth_kibps / 1024} MiB/s` : '不限速'}
        </Chip>}
        <p id="download-bandwidth-help">{settings && !bandwidthSupported
          ? '服务端重启后可设置总限速。' : '所有主动下载共用这个上限。填 0 表示不限速。'}</p>
      </div>
      <div className="space-y-1">
        <Label htmlFor="download-bandwidth">总限速 (MiB/s)</Label>
        <Input id="download-bandwidth" type="number" inputMode="decimal" min={0} step="any"
          value={bandwidth} disabled={disabled || !bandwidthSupported} aria-invalid={invalidBandwidth}
          aria-describedby="download-bandwidth-help" onChange={event => { setBandwidth(event.target.value); setNotice('') }} />
      </div>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-separator pt-4">
      <p className="max-w-lg text-xs leading-relaxed text-muted">保存后立即调整下载速度。调低并发时，已开始的文件会继续下载。游戏客户端的被动下载不计入限额。</p>
      <div className="flex shrink-0 gap-2">
        <Button size="sm" variant="secondary" isDisabled={disabled || (value === (settings?.default_concurrency ?? 32) && bandwidth === '0')}
          onPress={() => { change(settings?.default_concurrency ?? 32); setBandwidth('0') }}>恢复默认</Button>
        <Button size="sm" variant="primary" isDisabled={disabled || !changed || invalidBandwidth} onPress={apply}>
          <Save className="size-3.5" />{saving ? '正在保存…' : '应用并保存'}
        </Button>
      </div>
    </div>
    {(error || settings?.warning) && <p role="alert" className="text-xs text-danger">{error || settings?.warning}</p>}
    {notice && <p role="status" className="text-xs text-success">{notice}</p>}
  </Card>
}

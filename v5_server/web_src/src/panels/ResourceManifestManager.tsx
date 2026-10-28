import { useEffect, useRef, useState, type ReactNode } from 'react'
import { FilePlus2, RotateCcw, Save, Trash2, X } from 'lucide-react'
import { Button, Card, Chip, Input, Label, ListBox, Modal, Select, TextField } from '@heroui/react'
import { Section } from '../components/kit'
import { generateManifest, previewManifest, removeManifest, restoreManifest, saveResourceDistribution,
  type ChannelManifestSelection, type ManifestGenerateInput, type ManifestPreview, type ManifestProfile,
  type ResourceDistributionData, type ResourcePlatform } from '../lib/api'
import ResourcePlatformCard, { platformNames } from './ResourcePlatformCard'

const platforms = ['pc', 'android', 'ios'] as const
const channels = { pc: '9', android: '8', ios: '7' }
type Draft = Record<ResourcePlatform, ChannelManifestSelection>
const fromSaved = (data: ResourceDistributionData): Draft => Object.fromEntries(platforms.map(p => [p, {
  selected_id: data.platforms[p].selected_id, fallback_id: data.platforms[p].fallback_id,
}])) as Draft
const equal = (a: Draft, b: Draft) => platforms.every(p => a[p].selected_id === b[p].selected_id && a[p].fallback_id === b[p].fallback_id)

export default function ResourceManifestManager({ data, onSaved, disabled, downloadControls }: {
  data: ResourceDistributionData; onSaved: (data: ResourceDistributionData) => void; disabled: boolean
  downloadControls: ReactNode
}) {
  const [draft, setDraft] = useState<Draft>(() => fromSaved(data))
  const [custom, setCustom] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [filter, setFilter] = useState<ResourcePlatform>('pc')
  const [showRemoved, setShowRemoved] = useState(false)
  const [generator, setGenerator] = useState(false)
  const [generatorPlatform, setGeneratorPlatform] = useState<ResourcePlatform>('pc')
  const [generatorBase, setGeneratorBase] = useState('')
  const [removing, setRemoving] = useState<ManifestProfile | null>(null)
  const previousData = useRef(data)
  useEffect(() => {
    const old = fromSaved(previousData.current)
    setDraft(current => Object.fromEntries(platforms.map(p => {
      const valid = current[p].selected_id === 'auto' || data.profiles.some(r => r.id === current[p].selected_id && r.ready)
      const clean = current[p].selected_id === old[p].selected_id && current[p].fallback_id === old[p].fallback_id
      return [p, clean || !valid ? fromSaved(data)[p] : current[p]]
    })) as Draft)
    previousData.current = data
  }, [data])
  const presets = (build: string): Draft => Object.fromEntries(platforms.map(p => {
    const native = data.profiles.filter(r => r.platform === p && r.origin === 'native' && r.ready)
      .sort((a, b) => Number(b.resource_version) - Number(a.resource_version) || (b.registered_at || 0) - (a.registered_at || 0))
    const fallback = data.platforms[p].latest_native_id || native[0]?.id || data.platforms[p].fallback_id
    return [p, { selected_id: build === 'auto' ? 'auto' : native.find(r => r.resource_version === build)?.id || fallback,
      fallback_id: fallback }]
  })) as Draft
  const preset = custom ? 'custom' : ['auto', '229', '311', '321'].find(p => equal(draft, presets(p))) || 'custom'
  const apply = async () => {
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await saveResourceDistribution(draft)
      onSaved(result.data); setNotice('渠道选择已应用，下次客户端版本检查生效')
    } catch (e: any) { setError(e.message || '应用失败') }
    finally { setBusy(false) }
  }
  const remove = async () => {
    if (!removing) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await removeManifest(removing.id)
      onSaved(result.data); setNotice(result.data.operation_notice || '已移入回收区'); setRemoving(null)
    } catch (e: any) { setError(e.message || '移除失败') }
    finally { setBusy(false) }
  }
  const restore = async (id: string) => {
    setBusy(true); setError(''); setNotice('')
    try { const result = await restoreManifest(id); onSaved(result.data); setNotice('副本已恢复，尚未应用') }
    catch (e: any) { setError(e.message || '恢复失败') }
    finally { setBusy(false) }
  }
  const openGenerator = (platform: ResourcePlatform, base = '') => {
    setGeneratorPlatform(platform); setGeneratorBase(base); setGenerator(true)
  }
  return <>
    <Section title="分发与下载" description="选择渠道分发预设并调整主动下载参数。">
      <div className="grid items-stretch gap-4 lg:grid-cols-3">
      <Card className="flex flex-col gap-4 p-5">
        <div><h3 className="font-semibold text-foreground">游戏分发方案</h3>
          <p className="mt-1 text-xs leading-relaxed text-muted">总控预设只填写各渠道的清单选择，应用后生效。</p></div>
        <Select className="w-full" aria-label="渠道选择预设" selectedKey={preset} isDisabled={disabled || busy}
          onSelectionChange={key => {
            if (!key) return
            if (key === 'custom') { setCustom(true); return }
            setDraft(presets(String(key))); setCustom(false); setNotice('')
          }}>
          <Label>总控预设</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
          <Select.Popover><ListBox><ListBox.Item id="auto" textValue="自动 · 各渠道原生清单">自动 · 各渠道原生清单</ListBox.Item>
            {['229', '311', '321'].map(v => <ListBox.Item id={v} key={v} textValue={`原生 Build ${v}`}>原生 Build {v}</ListBox.Item>)}
            <ListBox.Item id="custom" textValue="自定义">自定义</ListBox.Item>
          </ListBox></Select.Popover>
        </Select>
        <p className="flex-1 text-xs leading-relaxed text-muted">缺少版本的渠道使用保险方案；手动调整后自动匹配预设，也可选择自定义。</p>
        <Button className="w-full" variant="primary" onPress={apply} isDisabled={disabled || busy || equal(draft, fromSaved(data))}>
          <Save className="size-4" />{busy ? '正在处理…' : '应用渠道选择'}
        </Button>
      </Card>
      {downloadControls}
      </div>
    </Section>
    <Section title="渠道分发方案" description="各渠道独立选择清单与保险方案。">
      <div className="grid items-start gap-4 lg:grid-cols-3">{platforms.map(platform =>
        <ResourcePlatformCard key={platform} platform={platform} data={data} value={draft[platform]}
          disabled={disabled || busy} onChange={value => { setDraft(d => ({ ...d, [platform]: value })); setCustom(false); setNotice('') }} />
      )}</div>
      {error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
      {notice && <p role="status" className="mt-3 text-sm text-success">{notice}</p>}
    </Section>
    <Section title="清单库" description="原生清单作为BASE；派生副本可独立生成、应用与移除。语音沿用BASE。">
      <Card className="overflow-hidden p-4 sm:p-5">
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <Select className="w-full sm:max-w-xs" aria-label="清单库渠道" selectedKey={filter}
            onSelectionChange={key => { if (key) setFilter(key as ResourcePlatform) }}>
            <Label>平台 / 渠道</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
            <Select.Popover><ListBox>{platforms.map(p => <ListBox.Item id={p} key={p} textValue={`${platformNames[p]} · ${channels[p]}`}>{platformNames[p]} · {channels[p]}</ListBox.Item>)}</ListBox></Select.Popover>
          </Select>
          <Button variant={showRemoved ? 'secondary' : 'ghost'} onPress={() => setShowRemoved(v => !v)}>
            <RotateCcw className="size-4" />{showRemoved ? '返回清单库' : `回收区 (${data.profiles.filter(p => p.platform === filter && p.removed).length})`}
          </Button>
          <Button className="sm:ml-auto" variant="primary" isDisabled={disabled || busy} onPress={() => openGenerator(filter)}><FilePlus2 className="size-4" />生成副本</Button>
        </div>
        <div className="divide-y divide-separator">{data.profiles.filter(p => p.platform === filter && Boolean(p.removed) === showRemoved).map(profile =>
          <div key={profile.id} className="flex flex-wrap items-center justify-between gap-3 py-4">
            <div className="min-w-0 flex-1 basis-64 space-y-2">
              <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{profile.label}</span>
                <Chip size="sm" variant="soft" color={profile.origin === 'native' ? 'success' : 'accent'}>{profile.origin === 'native' ? '原生BASE' : '派生副本'}</Chip>
                {data.platforms[filter].effective_id === profile.id && <Chip size="sm" variant="soft">使用中</Chip>}
                {!profile.ready && !profile.removed && <Chip size="sm" color="warning" variant="soft">未就绪</Chip>}
              </div>
              <p className="break-all font-mono text-xs text-muted">{profile.assethash}</p>
              <p className="text-xs text-muted">{profile.origin === 'derived' ? `BASE：${profile.base_label}` : `已核验 · ${profile.version_name}`} · ID：{profile.id}</p>
              {profile.error && !profile.removed && <p className="text-xs text-danger">{profile.error}</p>}
            </div>
            {profile.removed ? <Button size="sm" variant="secondary" isDisabled={disabled || busy} onPress={() => restore(profile.id)}><RotateCcw className="size-3.5" />恢复</Button>
              : profile.origin === 'native' ? <Button size="sm" variant="secondary" isDisabled={disabled || busy || !profile.ready} onPress={() => openGenerator(filter, profile.id)}>用作BASE</Button>
              : <Button size="sm" variant="danger-soft" isDisabled={disabled || busy} onPress={() => setRemoving(profile)}><Trash2 className="size-3.5" />移除</Button>}
          </div>)}
          {!data.profiles.some(p => p.platform === filter && Boolean(p.removed) === showRemoved) && <p className="py-8 text-center text-sm text-muted">{showRemoved ? '回收区为空' : '该渠道暂无清单'}</p>}
        </div>
      </Card>
    </Section>
    <ManifestGenerator open={generator} onOpenChange={setGenerator} data={data} platform={generatorPlatform}
      baseId={generatorBase} onGenerated={onSaved} disabled={disabled} />
    <Modal.Root isOpen={Boolean(removing)} onOpenChange={open => { if (!open && !busy) setRemoving(null) }}>
      <Modal.Backdrop><Modal.Container><Modal.Dialog className="max-w-lg p-5">
        <Modal.Header><Modal.Heading>移除派生副本</Modal.Heading></Modal.Header>
        <Modal.Body className="space-y-3 py-4"><p>{removing?.label}</p><p className="text-sm text-muted">正在使用时会先切换到本渠道保险方案。原生BASE、共享语音和实际资产保留，副本可从回收区恢复。</p></Modal.Body>
        <Modal.Footer><Button variant="secondary" isDisabled={busy} onPress={() => setRemoving(null)}>取消</Button><Button variant="danger" isDisabled={busy} onPress={remove}>移入回收区</Button></Modal.Footer>
      </Modal.Dialog></Modal.Container></Modal.Backdrop>
    </Modal.Root>
  </>
}

function ManifestGenerator({ open, onOpenChange, data, platform: initialPlatform, baseId, onGenerated, disabled }: {
  open: boolean; onOpenChange: (open: boolean) => void; data: ResourceDistributionData
  platform: ResourcePlatform; baseId: string; onGenerated: (data: ResourceDistributionData) => void; disabled: boolean
}) {
  const [platform, setPlatform] = useState<ResourcePlatform>(initialPlatform)
  const [base, setBase] = useState(baseId)
  const [app, setApp] = useState('308')
  const [build, setBuild] = useState('331')
  const [label, setLabel] = useState('')
  const [preview, setPreview] = useState<ManifestPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const serial = useRef(0)
  const bases = data.profiles.filter(p => p.platform === platform && p.origin === 'native' && p.ready)
  useEffect(() => {
    if (!open) return
    setPlatform(initialPlatform)
    setBase(baseId || data.profiles.filter(p => p.platform === initialPlatform && p.origin === 'native' && p.ready)
      .sort((a, b) => Number(b.resource_version) - Number(a.resource_version))[0]?.id || '')
    setError(''); setNotice(''); setLabel('')
  }, [open, initialPlatform, baseId])
  const input: ManifestGenerateInput = { platform, channel_id: channels[platform], base_id: base,
    target_app_version: app, target_resource_version: build, label }
  useEffect(() => {
    const id = ++serial.current
    setPreview(null); setError(''); setNotice('')
    if (!open || !base || !app || !build) { setLoading(false); return }
    setLoading(true)
    const timer = setTimeout(() => {
      void previewManifest({ platform, channel_id: channels[platform], base_id: base,
        target_app_version: app, target_resource_version: build, label }).then(result => {
        if (serial.current === id) setPreview(result.data)
      }).catch(e => { if (serial.current === id) setError(e.message || '预览失败') })
        .finally(() => { if (serial.current === id) setLoading(false) })
    }, 400)
    return () => { clearTimeout(timer); if (serial.current === id) serial.current++ }
  }, [open, platform, base, app, build, label])
  const generate = async () => {
    if (!preview) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await generateManifest({ ...input, preview_token: preview.preview_token })
      onGenerated(result.data.catalogue)
      setNotice(result.data.existed ? '相同副本已存在，可在渠道分发方案中选择' : '副本已生成，尚未应用。请在渠道分发方案中选择。')
    } catch (e: any) { setError(e.message || '生成失败') }
    finally { setBusy(false) }
  }
  return <Modal.Root isOpen={open} onOpenChange={value => { if (!busy) onOpenChange(value) }}>
    <Modal.Backdrop><Modal.Container><Modal.Dialog className="max-h-[90vh] w-full max-w-2xl overflow-y-auto p-5">
      <Modal.Header className="flex flex-row items-center justify-between"><Modal.Heading>生成清单副本</Modal.Heading><Button isIconOnly variant="ghost" size="sm" aria-label="关闭生成器" isDisabled={busy} onPress={() => onOpenChange(false)}><X className="size-4" /></Button></Modal.Header>
      <Modal.Body className="space-y-4 py-4">
        <Select aria-label="生成目标渠道" selectedKey={platform} isDisabled={busy} onSelectionChange={key => {
          if (!key) return
          const p = key as ResourcePlatform; setPlatform(p)
          setBase(data.profiles.find(r => r.platform === p && r.origin === 'native' && r.ready)?.id || '')
        }}><Label>平台 / 渠道</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox>{platforms.map(p => <ListBox.Item id={p} key={p} textValue={`${platformNames[p]} · ${channels[p]}`}>{platformNames[p]} · {channels[p]}</ListBox.Item>)}</ListBox></Select.Popover></Select>
        <Select aria-label="原生BASE清单" selectedKey={base || null} isDisabled={busy} onSelectionChange={key => { if (key) setBase(String(key)) }}>
          <Label>BASE · 仅限已核验原生清单</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
          <Select.Popover><ListBox>{bases.map(p => <ListBox.Item id={p.id} key={p.id} textValue={p.label}>{p.label}</ListBox.Item>)}</ListBox></Select.Popover>
        </Select>
        <div className="grid grid-cols-2 gap-3"><TextField value={app} isDisabled={busy} onChange={v => setApp(v.replace(/[^0-9]/g, '').slice(0, 4))}><Label>目标安装包版本</Label><Input inputMode="numeric" maxLength={4} /></TextField>
          <TextField value={build} isDisabled={busy} onChange={v => setBuild(v.replace(/[^0-9]/g, '').slice(0, 4))}><Label>目标资源版本</Label><Input inputMode="numeric" maxLength={4} /></TextField></div>
        <TextField value={label} isDisabled={busy} onChange={setLabel}><Label>方案名称 · 可选</Label><Input maxLength={48} placeholder={`兼容 ${app || '…'}/${build || '…'}`} /></TextField>
        <div className="space-y-2 rounded-xl bg-surface-secondary p-4 text-xs">
          <p className="font-medium">{loading ? '正在计算最终文件名与MD5…' : '生成预览'}</p>
          {preview && <><p className="break-all font-mono">{preview.assethash}</p><p className="break-all font-mono text-muted">MD5：{preview.md5}</p>
            <p className="break-all text-muted">来源：{preview.base_assethash}</p><p className="text-muted">{preview.voice_source} · {(preview.size / 1024).toFixed(1)} KiB</p></>}
          {!preview && !loading && <p className="text-muted">请选择BASE并填写目标版本</p>}
        </div>
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        {notice && <p role="status" className="text-sm text-success">{notice}</p>}
      </Modal.Body>
      <Modal.Footer><Button variant="secondary" isDisabled={busy} onPress={() => onOpenChange(false)}>关闭</Button>
        <Button variant="primary" isDisabled={disabled || busy || loading || !preview} onPress={generate}><FilePlus2 className="size-4" />{busy ? '正在生成…' : '生成副本（不应用）'}</Button></Modal.Footer>
    </Modal.Dialog></Modal.Container></Modal.Backdrop>
  </Modal.Root>
}

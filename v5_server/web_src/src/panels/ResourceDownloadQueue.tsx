import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Pause, Play, Plus, RefreshCw, Search, Trash2, X } from 'lucide-react'
import { Button, Card, Checkbox, Chip, Label, ListBox, NumberField, ProgressBar, Select } from '@heroui/react'
import { Section } from '../components/kit'
import { checkOfficialUpdate, enqueueDownload, enqueueOfficialManifest, fetchDownloadCatalogue, fetchDownloadQueue, fetchDownloadReport, operateDownload, setDownloadQueueSettings,
  type CheckUpdateData, type DownloadKind, type DownloadQueueData, type DownloadScope, type DownloadTask, type EnqueuedDownload,
  type ResourceDistributionData, type ResourcePlatform, type VoiceLanguage } from '../lib/api'
import { platformNames } from './ResourcePlatformCard'

const terminal = new Set(['completed', 'warnings', 'failed', 'cancelled'])
const states: Record<string, string> = { queued: '排队中', waiting_passive: '等待客户端下载结束', indexing: '建立索引', ready: '等待确认下载',
  downloading: '下载中', paused: '已暂停', cancelled: '已取消', completed: '已完成', warnings: '完成 · 有未解析引用', failed: '有失败项' }
const scopes: Record<DownloadScope, string> = { required: '必须', extended: '扩展', all: '全部' }
const voiceNames: Record<VoiceLanguage, string> = { zh: '中文', ja: '日语' }
type DownloadMode = DownloadKind | 'assets-with-voice'
const size = (value = 0) => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GiB` : value >= 1024 ** 2 ? `${(value / 1024 ** 2).toFixed(1)} MiB` : `${(value / 1024).toFixed(1)} KiB`

export default function ResourceDownloadQueue({ online, distribution, queuedManifest, onCatalogueChanged }: {
  online: boolean; distribution: ResourceDistributionData | null; queuedManifest: EnqueuedDownload | null
  onCatalogueChanged: () => void
}) {
  const [queue, setQueue] = useState<DownloadQueueData | null>(null)
  const [files, setFiles] = useState<{ platform: ResourcePlatform; filename: string }[]>([])
  const [platform, setPlatform] = useState<ResourcePlatform>('pc')
  const [selected, setSelected] = useState('')
  const [mode, setMode] = useState<DownloadMode>('assets')
  const [voiceLanguages, setVoiceLanguages] = useState<VoiceLanguage[]>([])
  const [official, setOfficial] = useState<CheckUpdateData | null>(null)
  const [checking, setChecking] = useState(false)
  const [checkError, setCheckError] = useState('')
  const [scope, setScope] = useState<DownloadScope>('all')
  const [parallel, setParallel] = useState(1)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [catalogueError, setCatalogueError] = useState('')
  const [notice, setNotice] = useState('')
  const [manifestResult, setManifestResult] = useState<DownloadTask | null>(null)
  const [history, setHistory] = useState(false)
  const pending = useRef(false)
  const queueRequest = useRef(0)
  const catalogueRequest = useRef(0)
  const previousManifestStates = useRef<Map<string, string> | null>(null)
  const preferred = useRef('')
  const sourceChosen = useRef(false)
  const section = useRef<HTMLDivElement>(null)
  const externalQueue = useRef<EnqueuedDownload | null>(null)
  const kind = mode === 'assets-with-voice' ? 'assets' : mode
  const needsVoice = mode === 'voice' || mode === 'assets-with-voice'
  const officialFilename = official?.platform === platform ? official.remote_assethash : ''
  const load = async () => {
    if (!online || pending.current) return
    pending.current = true
    const id = queueRequest.current
    try {
      const result = await fetchDownloadQueue()
      if (result.code !== 0) throw new Error(result.msg)
      if (id === queueRequest.current) setQueue(result.data)
    } catch (e: any) { if (id === queueRequest.current) setError(e.message || '读取下载队列失败') }
    finally { pending.current = false }
  }
  const catalogue = async () => {
    const id = ++catalogueRequest.current
    try {
      const result = await fetchDownloadCatalogue()
      if (result.code !== 0) throw new Error(result.msg)
      if (id === catalogueRequest.current) { setFiles(result.data.files); setCatalogueError('') }
    } catch (e: any) {
      if (id === catalogueRequest.current) setCatalogueError(e.message || '读取已保存清单失败')
    }
  }
  useEffect(() => {
    if (!online) return
    void load(); void catalogue()
    const timer = setInterval(() => void load(), 1500)
    return () => clearInterval(timer)
  }, [online])
  useEffect(() => {
    if (!online || !queue) return
    const manifests = queue.tasks.filter(task => task.kind === 'manifest')
    const previous = previousManifestStates.current
    previousManifestStates.current = new Map(manifests.map(task => [task.id, task.state]))
    if (!previous) return
    const finished = manifests.filter(task => (task.state === 'completed' || task.state === 'failed')
      && previous.get(task.id) !== task.state)
    const latest = finished[finished.length - 1]
    if (latest) { setManifestResult(latest); setNotice('') }
    if (finished.length) { void catalogue(); onCatalogueChanged() }
  }, [online, queue])
  useEffect(() => { if (queue) setParallel(queue.parallel_tasks) }, [queue?.parallel_tasks])
  const profiles = distribution?.profiles.filter(p => p.platform === platform && p.ready && !p.removed) || []
  const options = [...profiles.map(p => ({ key: `p:${p.id}`, label: p.label })),
    ...files.filter(f => f.platform === platform).map(f => ({ key: `f:${f.filename}`, label: f.filename }))]
  useEffect(() => {
    if (preferred.current && options.some(o => o.key === preferred.current)) {
      setSelected(preferred.current); preferred.current = ''; sourceChosen.current=true; return
    }
    const effective = distribution?.platforms[platform].effective_id
    if (!sourceChosen.current && effective && options.some(o => o.key === `p:${effective}`)) {
      setSelected(`p:${effective}`); sourceChosen.current=true; return
    }
    if (!options.some(o => o.key === selected)) setSelected(options[0]?.key || '')
  }, [platform, distribution, files])
  const updateQueue = (data: DownloadQueueData) => {
    queueRequest.current++
    setQueue(data)
  }
  useEffect(() => {
    if (!queuedManifest || externalQueue.current === queuedManifest) return
    externalQueue.current = queuedManifest
    queueRequest.current++
    setQueue(current => {
      if (!current) return queuedManifest.queue
      if (current.tasks.some(task => task.id === queuedManifest.task_id)) return current
      const added = queuedManifest.queue.tasks.find(task => task.id === queuedManifest.task_id)
      return added ? { ...current, tasks: [...current.tasks, added] } : current
    })
    setHistory(false); setManifestResult(null); setError('')
    setNotice('清单任务已加入队列，点击「开始下载」确认')
    section.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [queuedManifest])
  const checkManifest = async () => {
    if (!online || busy || checking) return
    setChecking(true); setOfficial(null); setCheckError('')
    try {
      const response = await checkOfficialUpdate(platform)
      if (response.code !== 0 || !response.data.remote_assethash || response.data.platform !== platform) {
        throw new Error(response.code !== 0 ? response.msg : '官方接口未返回本渠道的主清单')
      }
      setOfficial(response.data)
    } catch (e: any) { setCheckError(e.message || '检查更新失败') }
    finally { setChecking(false) }
  }
  const perform = async (action: string, id?: string) => {
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await operateDownload(action, id)
      if (result.code !== 0) throw new Error(result.msg)
      updateQueue(result.data)
    } catch (e: any) { setError(e.message || '任务操作失败') }
    finally { setBusy(false) }
  }
  const add = async () => {
    if (!online || busy || checking || (kind === 'manifest' ? !officialFilename : !selected)
      || (needsVoice && !voiceLanguages.length)) return
    setBusy(true); setError(''); setNotice('')
    if (kind === 'manifest') setManifestResult(null)
    try {
      const result = kind === 'manifest'
        ? await enqueueOfficialManifest(platform, officialFilename, voiceLanguages)
        : await enqueueDownload({ platform, kind, scope, download: false, voice_languages: needsVoice ? voiceLanguages : [],
          ...(selected.startsWith('p:') ? { profile_id: selected.slice(2) } : { filename: selected.slice(2) }) })
      if (result.code !== 0) throw new Error(result.msg)
      updateQueue(result.data.queue); setHistory(false); setNotice(kind === 'manifest' ? '已加入队列，确认开始后准备清单' : '已加入分析队列，分析后等待确认下载')
    } catch (e: any) { setError(e.message || '添加任务失败') }
    finally { setBusy(false) }
  }
  const applyParallel = async () => {
    setBusy(true); setError('')
    try {
      const result = await setDownloadQueueSettings(parallel)
      if (result.code !== 0) throw new Error(result.msg)
      updateQueue(result.data); setNotice('并行任务数已保存，在途文件完成后更新分组')
    } catch (e: any) { setError(e.message || '设置失败') }
    finally { setBusy(false) }
  }
  const useManifest = (task: DownloadTask) => {
    preferred.current = `f:${task.source.filename}`
    const languages = task.for_distribution ? ['zh', 'ja'] as VoiceLanguage[] : task.voice_languages || []
    setPlatform(task.platform); setMode(languages.length ? 'assets-with-voice' : 'assets'); setSelected(`f:${task.source.filename}`)
    setOfficial(null); setCheckError('')
    setVoiceLanguages(languages)
    void catalogue(); section.current?.scrollIntoView({ behavior: 'smooth' })
  }
  const report = async (id: string) => {
    setError('')
    try {
      const result = await fetchDownloadReport(id)
      if (result.code !== 0) throw new Error(result.msg)
      const url = URL.createObjectURL(new Blob([JSON.stringify(result.data, null, 2)], { type: 'application/json' }))
      const anchor = document.createElement('a'); anchor.href=url; anchor.download=`资源下载报告_${id.slice(0, 8)}.json`
      anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e: any) { setError(e.message || '导出报告失败') }
  }
  const canToggleQueue = !!queue?.tasks.some(task => queue.paused
    ? task.state === 'paused' || task.state === 'queued'
    : ['queued', 'waiting_passive', 'indexing', 'downloading'].includes(task.state))
  return <div ref={section} className="scroll-mt-20" id="active-downloads">
    <Section title="主动下载管理" description="资源属于本台服务器共享缓存；每份配置是一条任务，切换存档不影响下载。">
      <Card className="space-y-4 p-5">
        <div className={`grid gap-4 ${kind === 'manifest' ? 'md:grid-cols-2' : 'md:grid-cols-3'}`}>
          <Select aria-label="下载渠道" selectedKey={platform} isDisabled={busy || checking} onSelectionChange={key => {
            if (key && key !== platform) {
              sourceChosen.current = false; preferred.current = ''; setSelected(''); setOfficial(null); setCheckError('')
              setPlatform(key as ResourcePlatform); void catalogue()
            }
          }}>
            <Label>渠道</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
            <Select.Popover><ListBox>{(['pc', 'android', 'ios'] as const).map(p => <ListBox.Item id={p} key={p} textValue={platformNames[p]}>{platformNames[p]}</ListBox.Item>)}</ListBox></Select.Popover>
          </Select>
          <Select aria-label="下载任务类型" selectedKey={mode} isDisabled={busy || checking} onSelectionChange={key => { if (key) setMode(key as DownloadMode) }}>
            <Label>任务类型</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
            <Select.Popover><ListBox>
              <ListBox.Item id="assets">仅下载资产</ListBox.Item>
              <ListBox.Item id="assets-with-voice">下载资产和语音</ListBox.Item>
              <ListBox.Item id="voice">仅下载语音</ListBox.Item>
              <ListBox.Item id="manifest">仅准备官方清单</ListBox.Item>
            </ListBox></Select.Popover>
          </Select>
          {kind !== 'manifest' && <div className="space-y-2"><Label>下载范围</Label><div className="flex gap-2">{(['required', 'extended', 'all'] as const).map(s =>
            <Button key={s} size="sm" variant={scope === s ? 'primary' : 'secondary'} isDisabled={busy} aria-pressed={scope === s} onPress={() => setScope(s)}>{scopes[s]}</Button>)}</div></div>}
        </div>
        {kind === 'manifest' ? <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">官方主清单</p>
              {officialFilename && <>
                <p className="text-sm text-muted">{official?.remote_app_version} / {official?.remote_version}{official?.remote_manifest_present && (official.remote_manifest_valid ? ' · 主清单已保存' : ' · 同名清单校验失败')}</p>
                <p className="break-all font-mono text-xs text-muted">{officialFilename}</p>
              </>}
            </div>
            <Button variant="secondary" isDisabled={!online || busy || checking} onPress={checkManifest}>
              <Search className="size-4" />{checking ? '正在检查…' : officialFilename ? '重新检查更新' : '检查更新'}
            </Button>
          </div>
          {checkError && <p role="alert" className="text-sm text-danger">{checkError}</p>}
        </div>
          : <Select className="w-full" aria-label="下载蓝图清单" selectedKey={selected || null} isDisabled={busy || !options.length} onSelectionChange={key => { if (key) { sourceChosen.current=true; preferred.current=''; setSelected(String(key)) } }}>
            <Label>清单方案 / 已保存文件</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
            <Select.Popover><ListBox>{options.map(o => <ListBox.Item id={o.key} key={o.key} textValue={o.label}><span className="break-all">{o.label}</span></ListBox.Item>)}</ListBox></Select.Popover>
          </Select>}
        {(kind === 'manifest' || needsVoice) && <fieldset className="space-y-2">
          <legend className="text-sm font-medium">{kind === 'manifest' ? '同时准备语音清单' : '语种'}</legend>
          <div className="flex flex-wrap gap-5">
            {(['zh', 'ja'] as const).map(lang => <Checkbox key={lang} isSelected={voiceLanguages.includes(lang)} isDisabled={busy}
              onChange={checked => setVoiceLanguages(current => checked ? [...current, lang] : current.filter(value => value !== lang))}>
              <Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control><Label>{voiceNames[lang]}</Label></Checkbox.Content>
            </Checkbox>)}
          </div>
        </fieldset>}
        {kind !== 'manifest' && <p className="text-xs leading-relaxed text-muted">必须包含延后名单以外的对象，扩展包含命中延后名单的对象，全部覆盖所选清单列出的对象。分析会校验当前目标目录的库存。</p>}
        {catalogueError && <p role="alert" className="text-sm text-danger">清单目录读取失败：{catalogueError}</p>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="primary" isDisabled={!online || busy || checking || (kind === 'manifest' ? !officialFilename : !selected) || (needsVoice && !voiceLanguages.length)} onPress={add}>
            {kind === 'manifest' ? <><Plus className="size-4" />将清单加入队列</> : <><Search className="size-4" />分析并加入队列</>}
          </Button>
        </div>
        <p className="text-right text-xs text-muted">入队后不会自动下载，确认任务后点击“开始下载”。</p>
      </Card>
      <Card className="mt-4 space-y-4 p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div><p className="text-xs text-muted">队列总下载速度</p><p className="text-2xl font-semibold tabular-nums">{size(queue?.total_speed_bps)} /s</p>
            <p className="mt-1 text-xs text-muted">当前组 {queue?.active_task_ids.length || 0} 条 · 在途 {queue?.active_files || 0} / {queue?.file_concurrency || 32} 路</p></div>
          <div className="flex flex-wrap items-end gap-2">
            <NumberField className="w-36" aria-label="并行任务数" minValue={1} maxValue={8} value={parallel} onChange={value => { if (Number.isFinite(value)) setParallel(value) }} isDisabled={busy}>
              <Label>并行任务数</Label><NumberField.Group><NumberField.DecrementButton /><NumberField.Input /><NumberField.IncrementButton /></NumberField.Group>
            </NumberField>
            <Button variant="secondary" isDisabled={!online || busy || parallel === queue?.parallel_tasks} onPress={applyParallel}>应用</Button>
            <Button variant="secondary" isDisabled={!online || busy || !canToggleQueue} onPress={() => perform(queue?.paused ? 'resume_all' : 'pause_all')}>{queue?.paused ? <Play className="size-4" /> : <Pause className="size-4" />}{queue?.paused ? '全部继续' : '全部暂停'}</Button>
            <Button isIconOnly aria-label="刷新下载队列" variant="ghost" onPress={() => { void load(); void catalogue() }} isDisabled={!online}><RefreshCw className="size-4" /></Button>
          </div>
        </div>
        {!!queue?.active_group_id && <ProgressBar aria-label="当前执行组进度" value={queue.group_stats.total_bytes ? Math.min(100,100*((queue.group_stats.cached_bytes || 0)+(queue.group_stats.done_bytes || 0))/queue.group_stats.total_bytes) : 0}>
          <Label>当前执行组 · 共享对象去重统计</Label><ProgressBar.Output /><ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track>
        </ProgressBar>}
        {queue?.gate.passive_pending && <p role="status" className="text-sm text-warning">客户端正在回源下载。主动任务等待，释放后再建立或更新索引。</p>}
        {queue?.paused && <p role="status" className="text-sm text-muted">队列已暂停。点击「全部继续」恢复调度。</p>}
        {(error || queue?.warning) && <p role="alert" className="text-sm text-danger">{error || queue?.warning}</p>}
        {notice && <p role="status" className="text-sm text-success">{notice}</p>}
        {manifestResult && <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-surface-secondary p-3 text-sm">
          <p role={manifestResult.state === 'failed' ? 'alert' : 'status'} className={`min-w-0 flex-1 break-all ${manifestResult.state === 'failed' ? 'text-danger' : 'text-success'}`}>
            {platformNames[manifestResult.platform]} · {manifestResult.message} · {manifestResult.source.filename}
          </p>
          {manifestResult.state === 'completed' && <Button size="sm" variant="secondary" isDisabled={!online || busy} onPress={() => useManifest(manifestResult)}>用此清单配置下载</Button>}
          <Button isIconOnly size="sm" variant="ghost" aria-label="关闭清单任务提示" onPress={() => setManifestResult(null)}><X className="size-4" /></Button>
        </div>}
        <div className="flex justify-between border-t border-separator pt-4"><p className="text-sm font-medium">{history ? '任务记录' : '下载队列'}</p><Button size="sm" variant="ghost" onPress={() => setHistory(v => !v)}>{history ? '返回队列' : '已完成 / 失败记录'}</Button></div>
        {(queue?.tasks || []).filter(t => terminal.has(t.state) === history).map(task => <div key={task.id} className="space-y-3 border-t border-separator pt-4">
          <div className="flex flex-wrap justify-between gap-2"><div className="min-w-0 flex-1"><p className="break-all text-sm font-medium">{task.label}</p><p className="mt-1 text-xs text-muted">{platformNames[task.platform]} · {task.kind === 'manifest' ? '清单' : `${task.kind === 'voice' ? '仅语音' : '资产'} · ${scopes[task.scope]}`}{task.voice_languages?.length ? ` · ${task.voice_languages.map(lang => voiceNames[lang]).join('、')}` : ''} · {task.id.slice(0, 8)}{task.group_id ? ` · 执行组 ${task.group_id.slice(0, 6)}` : ''}</p></div>
            <Chip size="sm" variant="soft" color={task.state === 'failed' ? 'danger' : task.state === 'warnings' ? 'warning' : 'default'}>{states[task.state] || task.state}</Chip></div>
          <ProgressBar aria-label={`${task.label}下载进度`} value={task.progress_percent}><div className="flex justify-between text-xs text-muted"><span>{task.progress_percent.toFixed(1)}%</span><ProgressBar.Output /></div><ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track></ProgressBar>
          <div className="flex flex-wrap justify-between gap-2 text-xs text-muted"><span>完成 {(task.stats.cached_files || 0) + (task.stats.done_files || 0)} / {task.stats.total_files ?? '待分析'} · 缓存 {task.stats.cached_files || 0} · 待下载 {size(task.stats.needed_bytes)}</span><span>{size(task.speed_bps)} /s · 实际接收 {size(task.wire_bytes)}</span></div>
          <p className="text-xs text-muted">{queue?.paused && task.state === 'queued' ? '等待全局队列继续' : task.message}</p>
          {task.stats.output_dir && <p className="break-all text-xs text-muted">目标目录 {task.stats.output_dir} · 可用 {size(task.stats.free_bytes)}</p>}
          {!!task.stats.unresolved_count && <p className="text-xs text-warning">{task.stats.unresolved_count} 个分组引用缺少下载规格，已单独记录。</p>}
          {!!task.stats.failed_files && <p className="text-xs text-danger">{task.stats.failed_files} 个对象失败。{task.stats.failures?.[0]?.error}</p>}
          <div className="flex flex-wrap justify-end gap-2">
            <Button size="sm" variant="ghost" onPress={() => report(task.id)}>下载报告</Button>
            {task.state === 'queued' && <><Button isIconOnly size="sm" variant="ghost" aria-label="任务上移" isDisabled={busy} onPress={() => perform('up', task.id)}><ArrowUp className="size-4" /></Button><Button isIconOnly size="sm" variant="ghost" aria-label="任务下移" isDisabled={busy} onPress={() => perform('down', task.id)}><ArrowDown className="size-4" /></Button></>}
            {['ready', 'paused'].includes(task.state) && <Button size="sm" variant="primary" isDisabled={busy} onPress={() => perform(task.state === 'ready' ? 'start' : 'resume', task.id)}><Play className="size-3.5" />{task.state === 'ready' ? '开始下载' : '继续'}</Button>}
            {!terminal.has(task.state) && !['ready', 'paused'].includes(task.state) && <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => perform('pause', task.id)}><Pause className="size-3.5" />暂停</Button>}
            {!terminal.has(task.state) && <Button size="sm" variant="danger-soft" isDisabled={busy} onPress={() => perform('cancel', task.id)}><X className="size-3.5" />取消</Button>}
            {task.state === 'failed' && <Button size="sm" variant="secondary" isDisabled={busy} onPress={() => perform('retry', task.id)}>重试失败项</Button>}
            {task.kind === 'manifest' && task.state === 'completed' && <Button size="sm" variant="primary" onPress={() => useManifest(task)}>用此清单配置下载</Button>}
            {terminal.has(task.state) && <Button size="sm" variant="ghost" isDisabled={busy} onPress={() => perform('remove', task.id)}><Trash2 className="size-3.5" />移除记录</Button>}
          </div>
        </div>)}
        {!queue?.tasks.some(t => terminal.has(t.state) === history) && <p className="py-5 text-center text-sm text-muted">{history ? '暂无任务记录' : '队列为空'}</p>}
      </Card>
    </Section>
  </div>
}

import { useState } from 'react'
import { ArrowRight, CloudDownload, RefreshCw, X } from 'lucide-react'
import { Button, Card, Chip, Label, ListBox, Modal, Select } from '@heroui/react'
import { Section } from '../components/kit'
import { checkOfficialUpdate, type CheckUpdateData, type DownloadQueueHint, type ResourcePlatform } from '../lib/api'
import { platformNames } from './ResourcePlatformCard'

export default function OfficialResourceProbe({ online, onQueue }: { online: boolean; onQueue: (hint: DownloadQueueHint) => void }) {
  const [platform, setPlatform] = useState<ResourcePlatform>('pc')
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<CheckUpdateData | null>(null)
  const [error, setError] = useState('')
  const check = async () => {
    setOpen(true); setBusy(true); setError(''); setResult(null)
    try {
      const response = await checkOfficialUpdate(platform)
      if (response.code !== 0) throw new Error(response.msg)
      setResult(response.data)
    } catch (e: any) { setError(e.message || '检测失败') }
    finally { setBusy(false) }
  }
  return <>
    <Section title="官方资源更新" description="查看所选渠道的官方版本与差异，下载统一在主动下载管理中操作。">
      <Card className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_20rem] md:items-center">
        <div className="flex items-start gap-3"><span className="grid size-11 shrink-0 place-items-center rounded-2xl bg-accent/10 text-accent"><CloudDownload className="size-5" /></span>
          <div><h3 className="font-semibold">检查官方资源</h3><p className="mt-2 text-sm text-muted">探测只读取元数据与本地缓存，不自动下载缺失清单。</p>
            <div className="mt-3 flex gap-2"><Chip size="sm" variant="soft">{platformNames[platform]}</Chip><Chip size="sm" variant="soft">{result ? `${result.remote_app_version} / ${result.remote_version}` : '尚未检测'}</Chip></div></div></div>
        <div className="space-y-3"><Select aria-label="更新检测平台" selectedKey={platform} isDisabled={busy} onSelectionChange={key => { if (key) { setPlatform(key as ResourcePlatform); setResult(null) } }}>
          <Label>检测平台 · channelId={{ pc: '9', android: '8', ios: '7' }[platform]}</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
          <Select.Popover><ListBox>{(['pc', 'android', 'ios'] as const).map(p => <ListBox.Item key={p} id={p} textValue={platformNames[p]}>{platformNames[p]}</ListBox.Item>)}</ListBox></Select.Popover>
        </Select><Button className="w-full" variant="secondary" isDisabled={!online || busy} onPress={check}><CloudDownload className="size-4" />检查更新</Button></div>
      </Card>
    </Section>
    <Modal.Root isOpen={open} onOpenChange={setOpen}><Modal.Backdrop><Modal.Container><Modal.Dialog className="flex max-h-[88vh] w-full max-w-2xl flex-col p-5">
      <Modal.Header className="flex flex-row items-start justify-between gap-3 border-b border-separator pb-3"><div><Modal.Heading>官方更新检测</Modal.Heading><p className="mt-1 text-xs text-muted">{platformNames[platform]} · channelId={{ pc: '9', android: '8', ios: '7' }[platform]}</p></div><Button isIconOnly size="sm" variant="ghost" aria-label="关闭官方检测" onPress={() => setOpen(false)}><X className="size-4" /></Button></Modal.Header>
      <Modal.Body className="min-h-0 space-y-4 overflow-y-auto py-4">
        {busy && <p className="flex items-center gap-2 py-8 text-muted"><RefreshCw className="size-4 animate-spin" />正在探测官方最新版本…</p>}
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        {result && <>
          <div className="grid gap-3 rounded-xl bg-surface-secondary p-4 sm:grid-cols-3">{[
            ['本地清单绑定版本', `${result.local_app_version || '未知'} / ${result.local_version}`, result.local_version_name],
            ['官方清单绑定版本', `${result.remote_app_version || '未知'} / ${result.remote_version}`, result.remote_version_name],
            ['官方最新安装包', result.remote_install_version || '未返回', 'install 条目'],
          ].map(([title, value, description]) => <div key={title}><p className="text-xs text-muted">{title}</p><p className="my-1 font-mono text-lg font-semibold">{value}</p><p className="text-xs text-muted">{description}</p></div>)}</div>
          <div className="space-y-3 rounded-xl bg-surface-secondary p-3 text-xs select-text"><p>请求渠道：{result.platform} · channelId={result.channel_id}</p><div><p className="text-muted">本地完整清单名</p><p className="break-all font-mono">{result.local_assethash}</p></div><div><p className="text-muted">官方完整清单名（noInstall）</p><p className="break-all font-mono">{result.remote_assethash}</p></div><p className="text-muted">{result.remote_manifest_present ? result.remote_manifest_valid ? '本渠道清单已保存。' : '已有同名文件但校验失败，准备清单任务会跳过且保留原文件。' : '本渠道尚未保存此清单，可前往下载管理准备。'}</p></div>
          {!!result.remote_candidates?.length && <div className="space-y-2"><h3 className="text-sm font-medium">官方接口返回条目</h3>{result.remote_candidates.map((row, i) => <div className="space-y-1 rounded-xl border border-separator p-3 text-xs" key={i}><p>{row.type} · {row.app_version ? `${row.app_version} / ` : ''}{row.version} · {row.version_name} · channelId={row.channel_id}</p>{row.assethash && <p className="break-all font-mono">{row.assethash}</p>}</div>)}</div>}
          {result.diff_ready === false ? <p className="text-sm text-warning">{result.diff_error}</p> : <><Chip variant="soft" size="sm" color={result.has_update ? 'accent' : 'success'}>{result.has_update ? '资源清单存在差异' : '本地与官方清单一致'}</Chip><div className="grid grid-cols-3 gap-3 text-center text-sm"><p>新增 / 移除<br /><strong>+{result.diff_summary.added_count} / -{result.diff_summary.removed_count}</strong></p><p>变更对象<br /><strong>{result.diff_summary.modified_count}</strong></p><p>差量体积<br /><strong>{result.diff_summary.total_download_mb} MB</strong></p></div><p className="text-xs text-muted">总计比对 {result.total_remote_assets.toLocaleString()} 项。清单一致时仍可在下载管理检查本地资产是否缺失。</p></>}
        </>}
      </Modal.Body>
      <Modal.Footer className="flex flex-wrap justify-between gap-2 border-t border-separator pt-3"><Button size="sm" variant="secondary" onPress={() => setOpen(false)}>关闭</Button><div className="flex flex-wrap gap-2"><Button size="sm" variant="ghost" isDisabled={busy} onPress={check}>重新检测</Button><Button size="sm" variant="primary" isDisabled={busy || !result?.remote_assethash} onPress={() => { if (result) { setOpen(false); onQueue({ platform: result.platform as ResourcePlatform, filename: result.remote_assethash, kind: result.remote_manifest_present && result.remote_manifest_valid ? 'assets' : 'manifest' }) } }}><ArrowRight className="size-4" />前往下载管理</Button></div></Modal.Footer>
    </Modal.Dialog></Modal.Container></Modal.Backdrop></Modal.Root>
  </>
}

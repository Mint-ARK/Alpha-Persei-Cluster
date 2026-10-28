import { useEffect, useState } from 'react'
import { ArrowUp, FolderOpen, HardDrive, X } from 'lucide-react'
import { Button, Input, Label, Modal, TextField } from '@heroui/react'
import { browseResourceStorage, fetchResourceStorage, saveResourceStorage,
  type ResourceStorageData, type ServerDirectoryData } from '../lib/api'

export default function ResourceStorage({ online }: { online: boolean }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<ResourceStorageData | null>(null)
  const [browser, setBrowser] = useState<ServerDirectoryData | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const browse = async (path = '', select = true) => {
    setBusy(true); setError('')
    try {
      const result = await browseResourceStorage(path)
      if (result.code !== 0) throw new Error(result.msg)
      setBrowser(result.data)
      if (select && result.data.path) setDraft(result.data.path)
    } catch (e: any) { setError(e.message || '目录读取失败') }
    finally { setBusy(false) }
  }
  useEffect(() => {
    if (!open || !online) return
    setBusy(true); setError(''); setNotice(''); setBrowser(null); setData(null)
    let current = true
    void Promise.all([fetchResourceStorage(), browseResourceStorage()]).then(([status, listing]) => {
      if (!current) return
      if (status.code !== 0 || listing.code !== 0) throw new Error(status.msg || listing.msg)
      setData(status.data); setDraft(status.data.root || status.data.recommended_root); setBrowser(listing.data)
    }).catch(e => { if (current) setError(e.message || '读取存储设置失败') })
      .finally(() => { if (current) setBusy(false) })
    return () => { current = false }
  }, [open, online])
  const save = async () => {
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await saveResourceStorage(draft)
      if (result.code !== 0) throw new Error(result.msg)
      setData(result.data); setDraft(result.data.root); setNotice('已保存并生效，新文件写入此处，旧缓存继续可读。')
    } catch (e: any) { setError(e.message || '保存存储目录失败') }
    finally { setBusy(false) }
  }
  const separator = data?.server_os === 'windows' ? '\\' : '/'
  const preview = draft.trim().replace(/[\\/]+$/, '') || (draft.trim().startsWith('/') ? '/' : '')
  return <>
    <Button variant="secondary" isDisabled={!online} onPress={() => setOpen(true)}><FolderOpen className="size-4" />资源存储位置</Button>
    <Modal.Root isOpen={open} onOpenChange={value => { if (!busy) setOpen(value) }}>
      <Modal.Backdrop><Modal.Container><Modal.Dialog className="max-h-[90vh] w-full max-w-2xl overflow-y-auto p-5">
        <Modal.Header className="flex flex-row items-center justify-between"><Modal.Heading>资源存储位置</Modal.Heading><Button isIconOnly variant="ghost" aria-label="关闭资源存储位置" isDisabled={busy} onPress={() => setOpen(false)}><X className="size-4" /></Button></Modal.Header>
        <Modal.Body className="space-y-4 py-4">
          <p className="text-sm text-muted">浏览的是服务器磁盘，文件保存在运行服务端的机器上。Windows默认推荐D盘，其次其他非系统盘；Linux可选择挂载目录。</p>
          <TextField value={draft} onChange={setDraft} isDisabled={busy || !data || data.overridden}><Label>资源根目录（支持中文与空格）</Label><Input /></TextField>
          <div className="flex flex-wrap gap-2"><Button size="sm" variant="secondary" isDisabled={busy || !draft.trim()} onPress={() => browse(draft)}>浏览此目录</Button>
            <Button size="sm" variant="secondary" isDisabled={busy || !data?.recommended_root || data.overridden} onPress={() => setDraft(data!.recommended_root)}>使用推荐位置</Button>
            <Button size="sm" variant="ghost" isDisabled={busy} onPress={() => browse('', false)}>磁盘 / 挂载位置</Button></div>
          {data && !data.recommended_root && <p className="text-sm text-warning">未找到可推荐的非系统盘，请自行选择服务器存储位置。</p>}
          {data && !data.root_selected && data.root && <p className="text-xs text-muted">当前使用自动推荐位置，保存可将它固定为以后使用的目录。</p>}
          <div className="space-y-2 rounded-xl border border-separator p-3">
            <div className="flex items-center justify-between gap-2"><p className="break-all text-xs text-muted">{browser?.path || '服务器磁盘 / 挂载位置'}</p><Button size="sm" variant="ghost" isDisabled={busy || browser?.parent == null} onPress={() => browse(browser?.parent || '', !!browser?.parent)}><ArrowUp className="size-3.5" />上一级</Button></div>
            <div className="grid max-h-48 gap-2 overflow-y-auto sm:grid-cols-2">{browser?.directories.map(entry => <Button key={entry.path} variant="secondary" className="h-auto min-h-9 justify-start whitespace-normal py-2 text-left" isDisabled={busy} onPress={() => browse(entry.path)}><FolderOpen className="size-4 shrink-0" /><span className="break-all">{entry.name}</span></Button>)}</div>
            {browser && !browser.directories.length && <p className="text-xs text-muted">此目录没有可浏览的子文件夹，可直接保存为资源根目录。</p>}
            {browser?.truncated && <p className="text-xs text-warning">目录较大，仅显示部分子目录；可直接输入完整路径。</p>}
          </div>
          <div className="space-y-1 text-xs text-muted"><p className="font-medium text-foreground">保存后自动使用</p>{['pc', 'android', 'ios'].map(p => <p className="break-all" key={p}>{preview ? `${preview === '/' ? '' : preview}${separator}${p}` : `未选择 / ${p}`}</p>)}</div>
          <p className="break-all text-xs text-muted">当前生效：{data?.root || '未选择'}{data?.free_bytes != null ? ` · 可用 ${(data.free_bytes / 1024 ** 3).toFixed(1)} GiB` : ''}</p>
          {!!data?.read_roots.length && <p className="break-all text-xs text-muted">历史缓存只读：{data.read_roots.join('；')}</p>}
          {data?.overridden && <p className="text-sm text-warning">当前由CDN环境变量指定目录，请移除覆盖并重启后修改。</p>}
          {(error || data?.warning) && <p role="alert" className="text-sm text-danger">{error || data?.warning}</p>}
          {notice && <p role="status" className="text-sm text-success">{notice}</p>}
          <p className="text-xs text-muted">切换需要传输和索引空闲；不会搬迁或删除旧资源。目录不存在时，保存会创建根目录和三个平台子目录。</p>
        </Modal.Body>
        <Modal.Footer><Button variant="secondary" isDisabled={busy} onPress={() => setOpen(false)}>关闭</Button><Button variant="primary" isDisabled={busy || !online || !data || data.overridden || !draft.trim() || (draft.trim() === data.root && data.root_selected)} onPress={save}><HardDrive className="size-4" />{busy ? '处理中…' : '保存并应用'}</Button></Modal.Footer>
      </Modal.Dialog></Modal.Container></Modal.Backdrop>
    </Modal.Root>
  </>
}

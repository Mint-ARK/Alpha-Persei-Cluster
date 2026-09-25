import { useState, useEffect, useMemo, useRef } from 'react'
import {
  AlertCircle,
  Check,
  Eye,
  Inbox,
  Mail as MailIcon,
  Minus,
  Paperclip,
  Plus,
  RefreshCw,
  Search,
  Send,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import {
  Button,
  Card,
  Chip,
  Description,
  Input,
  Label,
  Modal,
  Separator,
  TextArea,
  TextField,
  cn,
} from '@heroui/react'
import { PageHeader, Section } from '../components/kit'
import { ItemSlot } from '../components/ItemSlot'
import { useServer } from '../lib/serverContext'
import {
  fetchMailHistory,
  sendGMMail,
  isTrialSkinFilm,
  isDisplayOnlyItem,
  type GMMailRecord,
  type DBMailRecord,
} from '../lib/api'
import defaultCatalog from '../data/items_catalog.json'
import { useElementSize } from '../lib/useElementSize'

// 附件草稿条目结构（支持独立勾选状态）
interface MailDraftAttachment {
  id: number
  name: string
  rare: number
  icon_file: string
  quality_frame: string
  count: number
  selected?: boolean
}

const STORAGE_KEY_MAIL_DRAFT = 'gm_mail_draft_attachments'

function loadSavedDraft(): MailDraftAttachment[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = localStorage.getItem(STORAGE_KEY_MAIL_DRAFT)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const filtered = parsed
      .filter((it: any) => !isDisplayOnlyItem(Number(it.id), undefined, it.name))
      .map((it: any) => ({ ...it, selected: it.selected !== false }))
    if (filtered.length !== parsed.length) {
      saveDraft(filtered)
    }
    return filtered
  } catch {
    return []
  }
}

function saveDraft(draft: MailDraftAttachment[]) {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(STORAGE_KEY_MAIL_DRAFT, JSON.stringify(draft))
  } catch (e) {
    console.error('保存附件草稿失败:', e)
  }
}

// 严苛输入校验白名单（仅允许中英文、数字、日常标点符号、空白与换行）
const SAFE_TEXT_REGEX = /^[\u4e00-\u9fa5a-zA-Z0-9\s，。！？、：；“”‘’（）《》【】—…·,.!?:;'"()\[\]<>\-—\n\r]*$/

// 搜索匹配加黑加粗高亮渲染（如同搜索引擎联想建议词：匹配部分加黑强调，未匹配词弱化）
function highlightMatch(text: string, query: string) {
  const trimmed = query.trim()
  if (!trimmed) return <span>{text}</span>

  const escaped = trimmed.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = text.split(new RegExp(`(${escaped})`, 'gi'))

  return (
    <span>
      {parts.map((part, i) =>
        part.toLowerCase() === trimmed.toLowerCase() ? (
          <span
            key={i}
            className="font-black text-foreground dark:text-white bg-accent/20 dark:bg-accent/35 px-0.5 rounded shadow-xs"
          >
            {part}
          </span>
        ) : (
          <span key={i} className="text-muted-foreground">
            {part}
          </span>
        ),
      )}
    </span>
  )
}

// 快捷追加常用调试资产
const QUICK_PRESETS = [
  { id: 1, name: '移转之辉', count: 1000 },
  { id: 2, name: '精准探测凭证', count: 10 },
  { id: 3, name: '常规探测凭证', count: 10 },
  { id: 5, name: '艾因索菲币', count: 100000 },
  { id: 41710, name: '纯金誓约之戒', count: 1 },
  { id: 40101, name: '特供体力药剂', count: 5 },
  { id: 40801, name: '高级作战记录', count: 20 },
  { id: 41301, name: '源质结晶', count: 10 },
]

export default function MailPanel() {
  const { activeUid } = useServer()

  // 测量左侧卡片真实高度，实现左右卡片 100% 绝对等高与局部滚动联动
  const { ref: leftCardRef, height: leftCardHeight } = useElementSize<HTMLDivElement>()

  // 1. 公文表单状态
  const [sender, setSender] = useState('隐科组总务部')
  const [title, setTitle] = useState('【隐科组】战备物资调配')
  const [body, setBody] = useState(
    '亲爱的管理员：\n隐科组已为您调度以下补给资产，请在有效期内查收，并用于前线作战指挥。',
  )

  // 2. 快捷录入与搜索联想状态
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedCatalogItem, setSelectedCatalogItem] = useState<{
    id: number
    name: string
    rare: number
    icon_file: string
    quality_frame: string
  } | null>(null)
  const [manualCount, setManualCount] = useState('100')
  const [isDropdownOpen, setIsDropdownOpen] = useState(false)
  const searchContainerRef = useRef<HTMLDivElement>(null)

  // 3. 待发附件列表（联动草稿池）
  const [attachments, setAttachments] = useState<MailDraftAttachment[]>(loadSavedDraft)

  // 4. 提交与历史状态
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [historyTab, setHistoryTab] = useState<'audit' | 'inbox'>('audit')
  const [gmHistory, setGmHistory] = useState<GMMailRecord[]>([])
  const [dbMails, setDbMails] = useState<DBMailRecord[]>([])
  const [isDetailModalOpen, setIsDetailModalOpen] = useState(() => {
    if (typeof window === 'undefined') return false
    return (
      new URLSearchParams(window.location.search).get('openDetail') === '1' ||
      window.location.hash.includes('openDetail=1')
    )
  })
  const [selectedMail, setSelectedMail] = useState<{
    id: number | string
    title: string
    sender: string
    content: string
    sent_at_str: string
    attachments: Array<{ id: number; name?: string; number?: number }>
    statusText: string
    statusColor?: 'default' | 'accent' | 'success' | 'warning' | 'danger'
    isFromAudit: boolean
  } | null>(() => {
    if (typeof window === 'undefined') return null
    if (
      new URLSearchParams(window.location.search).get('openDetail') === '1' ||
      window.location.hash.includes('openDetail=1')
    ) {
      return {
        id: 10001,
        title: '【隐科组】战备物资调度批复',
        sender: '隐科组总务部',
        content: '亲爱的管理员：\n隐科组已为您调度以下补给物资，请在有效期内查收，并用于前线作战指挥。\n祝作战顺利！',
        sent_at_str: '2026-09-16 11:30:00',
        attachments: [
          { id: 1, name: '移转之辉', number: 1000 },
          { id: 2, name: '精确探测凭证', number: 10 },
          { id: 4, name: '特供体力药剂', number: 5 },
        ],
        statusText: '已入库',
        statusColor: 'success',
        isFromAudit: true,
      }
    }
    return null
  })

  const openAuditDetail = (row: GMMailRecord) => {
    setSelectedMail({
      id: row.mail_id || row.id,
      title: row.title,
      sender: row.sender || '隐科组总务部',
      content: row.content,
      sent_at_str: row.sent_at_str || (row.sent_at ? new Date(row.sent_at * 1000).toLocaleString('zh-CN') : '—'),
      attachments: row.attachments || [],
      statusText: row.status || '已入库',
      statusColor: 'success',
      isFromAudit: true,
    })
    setIsDetailModalOpen(true)
  }

  const openDbDetail = (mail: DBMailRecord) => {
    setSelectedMail({
      id: mail.mail_id,
      title: mail.title,
      sender: mail.sender || '隐科组总务部',
      content: mail.content,
      sent_at_str: mail.send_time ? new Date(mail.send_time * 1000).toLocaleString('zh-CN') : '—',
      attachments: mail.attachments || [],
      statusText: mail.is_claimed ? '已领取' : (mail.attachments?.length ? '未领取' : (mail.is_read ? '已读' : '未读')),
      statusColor: mail.is_claimed ? 'default' : (mail.attachments?.length ? 'warning' : 'default'),
      isFromAudit: false,
    })
    setIsDetailModalOpen(true)
  }
  const [toast, setToast] = useState<{ show: boolean; msg: string; tone: 'success' | 'danger' } | null>(null)

  // 物品字典极速倒排索引
  const catalogMap = useMemo(() => {
    const map = new Map<number, { name: string; rare: number; icon_file: string; quality_frame: string }>()
    for (const it of defaultCatalog as any[]) {
      map.set(Number(it.id), {
        name: it.name,
        rare: Number(it.rare) || 4,
        icon_file: it.icon_file || `${it.id}.png`,
        quality_frame: it.quality_frame || 'Item_purple.png',
      })
    }
    return map
  }, [])

  // 监听窗口聚焦与草稿同步事件，跨面板无缝同步草稿
  useEffect(() => {
    const handleSync = () => {
      setAttachments(loadSavedDraft())
    }
    window.addEventListener('focus', handleSync)
    window.addEventListener('storage', handleSync)
    window.addEventListener('mail-draft-updated', handleSync)
    return () => {
      window.removeEventListener('focus', handleSync)
      window.removeEventListener('storage', handleSync)
      window.removeEventListener('mail-draft-updated', handleSync)
    }
  }, [])

  // 加载历史记录
  const loadHistory = async () => {
    setLoadingHistory(true)
    try {
      const res = await fetchMailHistory(activeUid)
      if (res.code === 0 && res.data) {
        setGmHistory(res.data.history || [])
        setDbMails(res.data.db_mails || [])
      }
    } catch (e) {
      console.error('加载邮件历史失败:', e)
    } finally {
      setLoadingHistory(false)
    }
  }

  useEffect(() => {
    loadHistory()
  }, [activeUid])

  // 输入校验计算
  const senderTrim = sender.trim()
  const titleTrim = title.trim()
  const bodyTrim = body.trim()

  const senderError = useMemo(() => {
    if (!senderTrim) return '发件人不能为空'
    if (!SAFE_TEXT_REGEX.test(senderTrim)) return '包含非法字符，仅允许输入中英文、数字及常用标点符号'
    return ''
  }, [senderTrim])

  const titleError = useMemo(() => {
    if (!titleTrim) return '邮件标题不能为空'
    if (titleTrim.length > 40) return '标题长度建议不超过 40 字'
    if (!SAFE_TEXT_REGEX.test(titleTrim)) return '包含非法字符，仅允许输入中英文、数字及常用标点符号'
    return ''
  }, [titleTrim])

  const bodyError = useMemo(() => {
    if (!bodyTrim) return '邮件正文不能为空'
    if (!SAFE_TEXT_REGEX.test(bodyTrim)) return '包含非法字符，仅允许输入中英文、数字及常用标点符号'
    return ''
  }, [bodyTrim])

  const isFormValid = !senderError && !titleError && !bodyError

  // 附件操作方法
  const updateAttachments = (nextList: MailDraftAttachment[]) => {
    setAttachments(nextList)
    saveDraft(nextList)
  }

  // 点击外部自动收起联想下拉浮层
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (searchContainerRef.current && !searchContainerRef.current.contains(e.target as Node)) {
        setIsDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // 搜索建议联想池（根据输入实时匹配名称或 ID）
  const searchSuggestions = useMemo(() => {
    const q = searchTerm.trim().toLowerCase()
    if (!q) return []

    // 若当前输入框已经完整匹配了选中项的格式，不重复弹出联想
    if (
      selectedCatalogItem &&
      (`${selectedCatalogItem.name} (ID: ${selectedCatalogItem.id})` === searchTerm ||
        selectedCatalogItem.name === searchTerm ||
        String(selectedCatalogItem.id) === searchTerm)
    ) {
      return []
    }

    const isNumeric = /^\d+$/.test(q)
    const targetId = isNumeric ? parseInt(q, 10) : null
    const matches: Array<{
      id: number
      name: string
      rare: number
      icon_file: string
      quality_frame: string
      score: number
    }> = []

    for (const it of defaultCatalog as any[]) {
      const itId = Number(it.id)
      const itName = String(it.name || '')
      if (isDisplayOnlyItem(itId, Number(it.type), itName)) {
        continue
      }
      const itIdStr = String(it.id)
      const itNameLower = itName.toLowerCase()

      let score = 0
      if (itId === targetId) {
        score = 100 // ID 完全匹配
      } else if (itNameLower === q) {
        score = 90 // 名称完全匹配
      } else if (itNameLower.startsWith(q)) {
        score = 70 // 名称前缀匹配
      } else if (itNameLower.includes(q)) {
        score = 50 // 名称包含匹配
      } else if (itIdStr.includes(q)) {
        score = 30 // ID 包含匹配
      }

      if (score > 0) {
        matches.push({
          id: itId,
          name: itName,
          rare: Number(it.rare) || 4,
          icon_file: it.icon_file || `${it.id}.png`,
          quality_frame: it.quality_frame || 'Item_purple.png',
          score,
        })
      }
    }

    // 按匹配评分降序，同分优先高品质与低 ID
    matches.sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score
      if (b.rare !== a.rare) return b.rare - a.rare
      return a.id - b.id
    })

    return matches.slice(0, 20)
  }, [searchTerm, selectedCatalogItem])

  const handleSelectSuggestion = (item: {
    id: number
    name: string
    rare: number
    icon_file: string
    quality_frame: string
  }) => {
    setSelectedCatalogItem(item)
    setSearchTerm(`${item.name} (ID: ${item.id})`)
    setIsDropdownOpen(false)
  }

  const handleClearSearch = () => {
    setSearchTerm('')
    setSelectedCatalogItem(null)
    setIsDropdownOpen(false)
  }

  const handleManualAdd = () => {
    let target = selectedCatalogItem

    // 未显式点选候选项时的自适应解析
    if (!target) {
      const trimmed = searchTerm.trim()
      if (!trimmed) return

      // 1. 优先纯数字 ID 判定
      if (/^\d+$/.test(trimmed)) {
        const idNum = parseInt(trimmed, 10)
        const info = catalogMap.get(idNum)
        target = {
          id: idNum,
          name: info?.name || `道具 ${idNum}`,
          rare: info?.rare || 4,
          icon_file: info?.icon_file || `${idNum}.png`,
          quality_frame: info?.quality_frame || 'Item_purple.png',
        }
      } else if (searchSuggestions.length > 0) {
        // 2. 存在匹配结果时默认取第一项
        target = searchSuggestions[0]
      }
    }

    if (!target) return

    if (isDisplayOnlyItem(target.id, undefined, target.name)) {
      const isFilm = isTrialSkinFilm(target.id, target.name)
      setToast({
        show: true,
        msg: isFilm
          ? `物品「${target.name}」(ID: ${target.id}) 属于限时试衣底片，未实现对应协议，仅供图鉴展示，禁止通过邮件发送！`
          : `物品「${target.name}」(ID: ${target.id}) 属于刻印类物品，仅供图鉴展示，禁止通过邮件发送！`,
        tone: 'danger',
      })
      return
    }

    const count = parseInt(manualCount, 10)
    if (isNaN(count) || count <= 0) return

    const existingIndex = attachments.findIndex((a) => a.id === target.id)
    if (existingIndex >= 0) {
      const copy = [...attachments]
      copy[existingIndex].count += count
      copy[existingIndex].selected = true // 重新添加自动激活勾选
      updateAttachments(copy)
    } else {
      const item: MailDraftAttachment = {
        id: target.id,
        name: target.name,
        rare: target.rare,
        icon_file: target.icon_file,
        quality_frame: target.quality_frame,
        count,
        selected: true,
      }
      updateAttachments([...attachments, item])
    }

    setSearchTerm('')
    setSelectedCatalogItem(null)
    setManualCount('100')
    setIsDropdownOpen(false)
  }

  const handlePresetAdd = (preset: { id: number; name: string; count: number }) => {
    const info = catalogMap.get(preset.id)
    const existingIndex = attachments.findIndex((a) => a.id === preset.id)

    if (existingIndex >= 0) {
      const copy = [...attachments]
      copy[existingIndex].count += preset.count
      copy[existingIndex].selected = true
      updateAttachments(copy)
    } else {
      const item: MailDraftAttachment = {
        id: preset.id,
        name: info?.name || preset.name,
        rare: info?.rare || 4,
        icon_file: info?.icon_file || `${preset.id}.png`,
        quality_frame: info?.quality_frame || 'Item_purple.png',
        count: preset.count,
        selected: true,
      }
      updateAttachments([...attachments, item])
    }
  }

  const handleCountChange = (id: number, delta: number) => {
    const next = attachments
      .map((it) => {
        if (it.id === id) {
          const newCount = it.count + delta
          return newCount > 0 ? { ...it, count: newCount } : null
        }
        return it
      })
      .filter(Boolean) as MailDraftAttachment[]
    updateAttachments(next)
  }

  const handleDirectCountInput = (id: number, val: string) => {
    const num = parseInt(val, 10)
    if (isNaN(num) || num < 0) return
    const next = attachments
      .map((it) => {
        if (it.id === id) {
          return num > 0 ? { ...it, count: num } : null
        }
        return it
      })
      .filter(Boolean) as MailDraftAttachment[]
    updateAttachments(next)
  }

  const handleToggleSelect = (id: number) => {
    const next = attachments.map((it) =>
      it.id === id ? { ...it, selected: it.selected === false ? true : false } : it,
    )
    updateAttachments(next)
  }

  const allSelected = attachments.length > 0 && attachments.every((a) => a.selected !== false)

  const handleToggleSelectAll = () => {
    const nextState = !allSelected
    const next = attachments.map((it) => ({ ...it, selected: nextState }))
    updateAttachments(next)
  }

  const handleRemove = (id: number) => {
    updateAttachments(attachments.filter((a) => a.id !== id))
  }

  const handleClearAll = () => {
    updateAttachments([])
  }

  // 仅投递被勾选的附件
  const checkedAttachments = useMemo(
    () => attachments.filter((a) => a.selected !== false),
    [attachments],
  )
  const totalCheckedTypes = checkedAttachments.length
  const totalCheckedCount = checkedAttachments.reduce((sum, a) => sum + a.count, 0)
  const totalItemTypes = attachments.length

  const handleSend = async () => {
    if (!isFormValid || isSubmitting) return

    const forbidden = checkedAttachments.find((a) => isDisplayOnlyItem(a.id, undefined, a.name))
    if (forbidden) {
      const isFilm = isTrialSkinFilm(forbidden.id, forbidden.name)
      setToast({
        show: true,
        msg: isFilm
          ? `待发附件包含限时试衣底片「${forbidden.name}」(ID: ${forbidden.id})，系统禁止通过邮件发送！`
          : `待发附件包含刻印类物品「${forbidden.name}」(ID: ${forbidden.id})，系统禁止通过邮件发送！`,
        tone: 'danger',
      })
      return
    }

    setIsSubmitting(true)
    setToast(null)

    try {
      const payload = {
        uid: activeUid,
        sender: senderTrim,
        title: titleTrim,
        content: bodyTrim,
        attachments: checkedAttachments.map((a) => ({
          id: a.id,
          name: a.name,
          number: a.count,
        })),
      }

      const res = await sendGMMail(payload)
      if (res.code === 0) {
        const pushed = res.data?.pushed
        const pushNotice = pushed ? ' (已即时推送到在线客户端，红点已点亮)' : ''
        setToast({
          show: true,
          msg: `邮件已成功投递至 UID ${activeUid}（发送 ${totalCheckedTypes} 项附件）${pushNotice}，已在审计日志中登记！`,
          tone: 'success',
        })
        // 仅移除已勾选发送的附件，保留未勾选的项
        const remaining = attachments.filter((a) => a.selected === false)
        updateAttachments(remaining)
        loadHistory()
      } else {
        setToast({
          show: true,
          msg: res.msg || '投递失败，请检查服务端日志',
          tone: 'danger',
        })
      }
    } catch (err: any) {
      setToast({
        show: true,
        msg: err.message || '网络请求异常，未能送达',
        tone: 'danger',
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="邮件系统"
        description="支持正文严苛校验、正版品质槽位附件挂载与独立审计日志持久化。"
        actions={
          <Button
            size="sm"
            variant="ghost"
            className="gap-1.5 cursor-pointer text-muted hover:text-foreground"
            onClick={loadHistory}
            isDisabled={loadingHistory}
          >
            <RefreshCw className={cn('size-4', loadingHistory && 'animate-spin')} />
            刷新数据
          </Button>
        }
      />

      {/* 提示通知条 */}
      {toast && toast.show && (
        <div
          className={cn(
            'flex items-center justify-between rounded-xl px-4 py-3 text-sm font-medium transition-all shadow-sm',
            toast.tone === 'success'
              ? 'bg-success/15 border border-success/30 text-success'
              : 'bg-danger/15 border border-danger/30 text-danger',
          )}
        >
          <div className="flex items-center gap-2">
            {toast.tone === 'success' ? <Check className="size-4 shrink-0" /> : <AlertCircle className="size-4 shrink-0" />}
            <span>{toast.msg}</span>
          </div>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-xs cursor-pointer"
            onClick={() => setToast(null)}
          >
            关闭
          </Button>
        </div>
      )}

      {/* 主工作区分割网格：两列顶部自然对齐 */}
      <div className="grid gap-6 lg:grid-cols-12 items-start">
        {/* 左侧：邮件公文配置与快捷附件录入卡片（占 7 列） */}
        <div className="lg:col-span-7 flex flex-col">
          <Section title="邮件公文编辑" description="发件人、标题与正文严格限制输入中英文、数字及日常标点" className="flex flex-col">
            <div ref={leftCardRef}>
              <Card className="flex flex-col p-5">
                <Card.Content className="gap-4">
                {/* 发件人 */}
                <TextField fullWidth value={sender} onChange={setSender} isRequired>
                  <Label>发件人（Sender）</Label>
                  <Input placeholder="请输入发件人（例如：隐科组总务部）" />
                  {senderError ? (
                    <p className="text-xs text-danger font-medium mt-1">{senderError}</p>
                  ) : (
                    <Description>客户端公文抬头显示，支持自定义机构或部门</Description>
                  )}
                </TextField>

                {/* 邮件标题 */}
                <TextField fullWidth value={title} onChange={setTitle} isRequired>
                  <Label>邮件标题</Label>
                  <Input placeholder="请输入邮件标题" />
                  {titleError ? (
                    <p className="text-xs text-danger font-medium mt-1">{titleError}</p>
                  ) : (
                    <Description>建议不超过 30 个汉字，过长可能在客户端折行</Description>
                  )}
                </TextField>

                {/* 正文内容 */}
                <TextField fullWidth value={body} onChange={setBody} isRequired>
                  <Label>正文内容</Label>
                  <TextArea placeholder="请输入正文内容" rows={3} />
                  {bodyError ? (
                    <p className="text-xs text-danger font-medium mt-1">{bodyError}</p>
                  ) : (
                    <Description>仅限中英文、数字、常用标点与换行，严禁脚本与特殊字符</Description>
                  )}
                </TextField>

                <Separator className="my-0.5" />

                {/* 附件智能搜索与快捷录入窗口 */}
                <div className="rounded-xl border border-separator/60 bg-surface-secondary/40 p-3.5" ref={searchContainerRef}>
                  <div className="flex items-center justify-between mb-2.5">
                    <div className="flex items-center gap-2">
                      <Paperclip className="size-4 text-accent" />
                      <span className="text-xs font-semibold">附件智能检索与录入</span>
                    </div>
                    <span className="text-[11px] text-muted">支持中文全名搜索 / ID 直输</span>
                  </div>

                  {/* 搜索建议与数量录入行 */}
                  <div className="relative mb-2.5">
                    <div className="flex items-center gap-2">
                      <div className="relative flex-1 min-w-[140px]">
                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted pointer-events-none" />
                        <input
                          type="text"
                          placeholder="输入物品全名（如：探测、移转）或 ID..."
                          value={searchTerm}
                          onChange={(e) => {
                            setSearchTerm(e.target.value)
                            setSelectedCatalogItem(null)
                            setIsDropdownOpen(true)
                          }}
                          onFocus={() => {
                            if (searchTerm.trim()) setIsDropdownOpen(true)
                          }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault()
                              handleManualAdd()
                            } else if (e.key === 'Escape') {
                              setIsDropdownOpen(false)
                            }
                          }}
                          className="w-full h-8.5 rounded-lg border border-separator/80 bg-surface pl-8 pr-7 text-xs text-foreground placeholder:text-muted focus:border-accent focus:outline-none transition-colors"
                        />
                        {searchTerm && (
                          <button
                            type="button"
                            onClick={handleClearSearch}
                            className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 text-muted hover:text-foreground cursor-pointer"
                            title="清空输入"
                          >
                            <X className="size-3.5" />
                          </button>
                        )}
                      </div>

                      {/* 数量输入框 */}
                      <div className="w-24 shrink-0">
                        <input
                          type="number"
                          placeholder="数量"
                          value={manualCount}
                          onChange={(e) => setManualCount(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault()
                              handleManualAdd()
                            }
                          }}
                          className="w-full h-8.5 rounded-lg border border-separator/80 bg-surface px-3 font-mono text-xs text-foreground placeholder:text-muted focus:border-accent focus:outline-none transition-colors"
                          min={1}
                        />
                      </div>

                      {/* 装入按钮 */}
                      <Button
                        size="sm"
                        variant="secondary"
                        className="h-8.5 shrink-0 gap-1 cursor-pointer font-medium text-xs whitespace-nowrap px-3"
                        onClick={handleManualAdd}
                        isDisabled={!selectedCatalogItem && !searchTerm.trim()}
                      >
                        <Plus className="size-3.5" />
                        装入待发
                      </Button>
                    </div>

                    {/* 搜索引擎式联想匹配下拉浮层 */}
                    {isDropdownOpen && searchTerm.trim() && (
                      <div className="absolute left-0 right-0 top-full mt-1 z-50 rounded-xl border border-separator bg-surface dark:bg-[#1a1a1e] shadow-2xl overflow-hidden">
                        <div className="p-1.5 max-h-64 overflow-y-auto cp-scroll space-y-1">
                          {searchSuggestions.length === 0 ? (
                            <div className="py-4 text-center text-xs text-muted">
                              未检索到与 “<span className="font-semibold text-foreground">{searchTerm}</span>” 匹配的道具
                            </div>
                          ) : (
                            searchSuggestions.map((item) => (
                              <div
                                key={item.id}
                                onClick={() => handleSelectSuggestion(item)}
                                className="flex items-center gap-2.5 p-2 rounded-lg hover:bg-surface-secondary/80 cursor-pointer transition-colors group"
                              >
                                <div className="shrink-0 pointer-events-none">
                                  <ItemSlot
                                    id={item.id}
                                    name={item.name}
                                    rare={item.rare}
                                    iconFile={item.icon_file}
                                    qualityFrame={item.quality_frame}
                                    size="sm"
                                    showCount={false}
                                    showAddButton={false}
                                  />
                                </div>
                                <div className="min-w-0 flex-1">
                                  <div className="text-xs truncate font-medium">
                                    {highlightMatch(item.name, searchTerm)}
                                  </div>
                                  <div className="mt-0.5 flex items-center gap-2 text-[11px] font-mono text-muted">
                                    <span>ID: {highlightMatch(String(item.id), searchTerm)}</span>
                                    <span className="text-[10px] text-muted-foreground">· 品质 {item.rare}★</span>
                                  </div>
                                </div>
                                <span className="text-[11px] text-accent opacity-0 group-hover:opacity-100 font-medium transition-opacity shrink-0 pr-1">
                                  点击装选
                                </span>
                              </div>
                            ))
                          )}
                        </div>
                        {searchSuggestions.length > 0 && (
                          <div className="border-t border-separator/50 bg-surface-secondary/40 px-3 py-1 text-[10px] text-muted flex items-center justify-between">
                            <span>共找到 {searchSuggestions.length} 个相关物品</span>
                            <span>点击条目选中并填入，或直接按回车装入</span>
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* 常用预设快捷标签 */}
                  <div>
                    <div className="text-[11px] text-muted mb-1.5 flex items-center gap-1">
                      <Sparkles className="size-3 text-warning" />
                      <span>常用调试资产（点击直接装入待发清单）：</span>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {QUICK_PRESETS.map((preset) => (
                        <button
                          key={preset.id}
                          type="button"
                          onClick={() => handlePresetAdd(preset)}
                          className="inline-flex items-center gap-1 rounded-md border border-separator/80 bg-surface px-2 py-0.5 text-xs text-muted hover:border-accent hover:text-accent transition-colors cursor-pointer"
                        >
                          <span>{preset.name}</span>
                          <span className="font-mono text-[10px] text-muted-foreground">+{preset.count}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </Card.Content>
            </Card>
            </div>
          </Section>
        </div>

        {/* 右侧：待发附件清单卡片（占 5 列，采用严格局部滚动框，绝不向下平铺） */}
        <div className="lg:col-span-5 flex flex-col">
          <Section
            title="待发附件清单"
            description="勾选确认要发送的资产，未勾选者暂存保留"
            className="flex flex-col"
            actions={
              attachments.length > 0 && (
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={handleToggleSelectAll}
                    className="text-xs text-accent hover:underline cursor-pointer font-medium"
                  >
                    {allSelected ? '取消全选' : '全选'}
                  </button>
                  <span className="text-separator/60 text-xs">|</span>
                  <button
                    type="button"
                    onClick={handleClearAll}
                    className="text-xs text-muted hover:text-danger cursor-pointer flex items-center gap-1"
                  >
                    <Trash2 className="size-3" />
                    清空
                  </button>
                </div>
              )
            }
          >
            <div style={{ height: leftCardHeight > 0 ? `${leftCardHeight}px` : undefined }}>
              <Card className="h-full flex flex-col p-5">
                {/* 附件列表展示区：flex-1 min-h-0 自适应撑满高度，超出时内部局部滚动，绝不撑大卡片 */}
                <div className="flex-1 min-h-0 overflow-y-auto space-y-2 pr-1.5 cp-scroll mb-3 border border-separator/60 rounded-xl p-2 bg-surface-secondary/20">
                {attachments.length === 0 ? (
                  <div className="flex h-full flex-col items-center justify-center rounded-lg border border-dashed border-separator/80 p-6 text-center text-muted">
                    <Inbox className="size-8 opacity-40 mb-2 stroke-[1.5]" />
                    <p className="text-sm font-medium">暂无待发附件</p>
                    <p className="text-xs text-muted-foreground mt-1 max-w-[240px]">
                      在左侧智能检索框输入全名搜索或填入 ID，亦可在背包面板点击 ＋ 批量装填。
                    </p>
                  </div>
                ) : (
                  attachments.map((item) => {
                    const isChecked = item.selected !== false
                    return (
                      <div
                        key={item.id}
                        className={cn(
                          'flex items-center gap-2.5 rounded-xl border p-2 transition-all',
                          isChecked
                            ? 'border-separator/70 bg-surface-secondary/50 hover:bg-surface-secondary/80'
                            : 'border-separator/30 bg-surface-secondary/15 opacity-55 hover:opacity-80',
                        )}
                      >
                        {/* 复选勾选框 */}
                        <label className="flex items-center cursor-pointer shrink-0 pl-1">
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={() => handleToggleSelect(item.id)}
                            className="size-4 rounded border-separator/80 bg-surface accent-accent cursor-pointer"
                          />
                        </label>

                        {/* 正版品质框槽位 */}
                        <div className="shrink-0">
                          <ItemSlot
                            id={item.id}
                            name={item.name}
                            rare={item.rare}
                            iconFile={item.icon_file}
                            qualityFrame={item.quality_frame}
                            size="sm"
                            showCount={false}
                            showAddButton={false}
                          />
                        </div>

                        {/* 物品文本信息 */}
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-xs font-semibold">{item.name}</div>
                          <div className="mt-0.5 font-mono text-[11px] text-muted">
                            ID: {item.id}
                          </div>
                        </div>

                        {/* 数量微调步进器 */}
                        <div className="flex items-center gap-1 shrink-0">
                          <Button
                            size="sm"
                            variant="outline"
                            isIconOnly
                            className="size-6 border-separator/80 cursor-pointer"
                            onClick={() => handleCountChange(item.id, -10)}
                          >
                            <Minus className="size-3" />
                          </Button>

                          <input
                            type="number"
                            value={item.count}
                            onChange={(e) => handleDirectCountInput(item.id, e.target.value)}
                            className="w-14 rounded border border-separator/80 bg-surface px-1 py-0.5 text-center font-mono text-xs tabular-nums focus:outline-none focus:ring-1 focus:ring-accent"
                            min={1}
                          />

                          <Button
                            size="sm"
                            variant="outline"
                            isIconOnly
                            className="size-6 border-separator/80 cursor-pointer"
                            onClick={() => handleCountChange(item.id, 10)}
                          >
                            <Plus className="size-3" />
                          </Button>

                          <Button
                            size="sm"
                            variant="ghost"
                            isIconOnly
                            className="size-6 text-muted hover:text-danger cursor-pointer ml-0.5"
                            onClick={() => handleRemove(item.id)}
                            aria-label={`移除 ${item.name}`}
                          >
                            <Trash2 className="size-3.5" />
                          </Button>
                        </div>
                      </div>
                    )
                  })
                )}
              </div>

              <Separator className="my-2 shrink-0" />

              {/* 底部结算与投递大按钮 */}
              <div className="shrink-0 space-y-2.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted">受控目标 UID:</span>
                  <Chip size="sm" variant="soft" color="accent" className="font-mono">
                    <Chip.Label>{activeUid}</Chip.Label>
                  </Chip>
                </div>

                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted">待发资产统计:</span>
                  <span className="font-medium">
                    已勾选 <span className="text-accent font-bold">{totalCheckedTypes}</span> / {totalItemTypes} 类 · 累计{' '}
                    <span className="font-mono font-bold">{totalCheckedCount.toLocaleString('en-US')}</span> 件
                  </span>
                </div>

                <Button
                  size="md"
                  className="w-full gap-2 font-semibold cursor-pointer shadow-sm bg-accent text-accent-foreground"
                  onClick={handleSend}
                  isDisabled={!isFormValid || isSubmitting}
                >
                  <Send className={cn('size-4', isSubmitting && 'animate-spin')} />
                  {isSubmitting ? '正在写入数据库与广播...' : '确认投递邮件'}
                </Button>

                {!isFormValid && (
                  <p className="text-center text-[11px] text-danger">
                    请先修正公文表单中的校验错误后再行投递
                  </p>
                )}
              </div>
            </Card>
            </div>
          </Section>
        </div>
      </div>

      {/* 底部：发送历史记录窗口（原生高可靠表格结构，杜绝白屏崩溃） */}
      <Section
        title="邮件记录与收件箱状态"
        description="专有审计日志持久化于 v5_server/data/gm_mail_history.json，游戏信箱反映客户端真实状态"
        actions={
          <div className="flex items-center gap-2">
            <div className="flex rounded-lg bg-surface-secondary p-0.5 text-xs">
              <button
                type="button"
                onClick={() => setHistoryTab('audit')}
                className={cn(
                  'rounded-md px-2.5 py-1 font-medium transition-colors cursor-pointer',
                  historyTab === 'audit'
                    ? 'bg-surface font-semibold text-foreground shadow-xs'
                    : 'text-muted hover:text-foreground',
                )}
              >
                GM 派发审计日志 ({gmHistory.length})
              </button>
              <button
                type="button"
                onClick={() => setHistoryTab('inbox')}
                className={cn(
                  'rounded-md px-2.5 py-1 font-medium transition-colors cursor-pointer',
                  historyTab === 'inbox'
                    ? 'bg-surface font-semibold text-foreground shadow-xs'
                    : 'text-muted hover:text-foreground',
                )}
              >
                游戏收件箱 ({dbMails.length})
              </button>
            </div>

            <Button
              size="sm"
              variant="ghost"
              className="gap-1 text-xs cursor-pointer text-muted hover:text-foreground"
              onClick={loadHistory}
              isDisabled={loadingHistory}
            >
              <RefreshCw className={cn('size-3.5', loadingHistory && 'animate-spin')} />
              刷新
            </Button>
          </div>
        }
      >
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <div className="h-[380px] overflow-y-auto cp-scroll">
              <table className="w-full min-w-[760px] text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-separator/80 bg-surface-secondary/95 backdrop-blur-xs text-muted shadow-xs">
                    <th className="py-3 px-4 w-28 font-medium sticky top-0 z-10 bg-surface-secondary/95 backdrop-blur-xs border-b border-separator/80">记录/信件编号</th>
                    <th className="py-3 px-4 w-36 font-medium sticky top-0 z-10 bg-surface-secondary/95 backdrop-blur-xs border-b border-separator/80">派发时间</th>
                    <th className="py-3 px-4 w-28 font-medium sticky top-0 z-10 bg-surface-secondary/95 backdrop-blur-xs border-b border-separator/80">发件人</th>
                    <th className="py-3 px-4 min-w-[140px] font-medium sticky top-0 z-10 bg-surface-secondary/95 backdrop-blur-xs border-b border-separator/80">标题</th>
                    <th className="py-3 px-4 font-medium sticky top-0 z-10 bg-surface-secondary/95 backdrop-blur-xs border-b border-separator/80">附件资产明细</th>
                    <th className="py-3 px-4 w-24 text-end font-medium sticky top-0 z-10 bg-surface-secondary/95 backdrop-blur-xs border-b border-separator/80">状态</th>
                    <th className="py-3 px-3 w-16 text-center font-medium sticky top-0 z-10 bg-surface-secondary/95 backdrop-blur-xs border-b border-separator/80">详情</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-separator/40">
                  {historyTab === 'audit' ? (
                    gmHistory.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="text-center text-muted py-12">
                          暂无 GM 邮件派发审计记录
                        </td>
                      </tr>
                    ) : (
                      gmHistory.map((row) => (
                        <tr
                          key={`gm-${row.id || row.mail_id}`}
                          onClick={() => openAuditDetail(row)}
                          className="hover:bg-surface-secondary/50 transition-colors cursor-pointer group"
                        >
                          <td className="py-3 px-4 font-mono text-muted">
                            #{row.mail_id || row.id}
                          </td>
                          <td className="py-3 px-4 font-mono text-muted whitespace-nowrap">
                            {row.sent_at_str || (row.sent_at ? new Date(row.sent_at * 1000).toLocaleString('zh-CN') : '—')}
                          </td>
                          <td className="py-3 px-4 font-medium whitespace-nowrap">
                            {row.sender || '隐科组总务部'}
                          </td>
                          <td className="py-3 px-4 font-medium max-w-[200px] truncate group-hover:text-accent transition-colors">
                            {row.title}
                          </td>
                          <td className="py-3 px-4">
                            <div className="flex flex-wrap gap-1">
                              {row.attachments && row.attachments.length > 0 ? (
                                row.attachments.map((att, idx) => {
                                  const info = catalogMap.get(att.id)
                                  return (
                                    <Chip key={idx} size="sm" variant="soft" color="default" className="text-[10px]">
                                      <Chip.Label>
                                        {att.name || info?.name || `ID ${att.id}`} ×{att.number?.toLocaleString('en-US') ?? 0}
                                      </Chip.Label>
                                    </Chip>
                                  )
                                })
                              ) : (
                                <span className="text-muted">无附件</span>
                              )}
                            </div>
                          </td>
                          <td className="py-3 px-4 text-end">
                            <Chip size="sm" variant="soft" color="success">
                              <Chip.Label>{row.status || '已入库'}</Chip.Label>
                            </Chip>
                          </td>
                          <td className="py-3 px-3 text-center" onClick={(e) => { e.stopPropagation(); openAuditDetail(row); }}>
                            <Button
                              size="sm"
                              variant="ghost"
                              isIconOnly
                              className="size-7 text-muted group-hover:text-accent cursor-pointer"
                              aria-label="查看邮件正文详情"
                            >
                              <Eye className="size-3.5" />
                            </Button>
                          </td>
                        </tr>
                      ))
                    )
                  ) : dbMails.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="text-center text-muted py-12">
                        当前收件箱无邮件
                      </td>
                    </tr>
                  ) : (
                    dbMails.map((mail, idx) => (
                      <tr
                        key={`db-${mail.mail_id || idx}`}
                        onClick={() => openDbDetail(mail)}
                        className="hover:bg-surface-secondary/50 transition-colors cursor-pointer group"
                      >
                        <td className="py-3 px-4 font-mono text-muted">
                          #{mail.mail_id}
                        </td>
                        <td className="py-3 px-4 font-mono text-muted whitespace-nowrap">
                          {mail.send_time ? new Date(mail.send_time * 1000).toLocaleString('zh-CN') : '—'}
                        </td>
                        <td className="py-3 px-4 font-medium whitespace-nowrap">
                          {mail.sender || '隐科组总务部'}
                        </td>
                        <td className="py-3 px-4 font-medium max-w-[200px] truncate group-hover:text-accent transition-colors">
                          {mail.title}
                        </td>
                        <td className="py-3 px-4">
                          <div className="flex flex-wrap gap-1">
                            {mail.attachments && mail.attachments.length > 0 ? (
                              mail.attachments.map((att, aIdx) => {
                                const info = catalogMap.get(att.id)
                                return (
                                  <Chip key={aIdx} size="sm" variant="soft" color="default" className="text-[10px]">
                                    <Chip.Label>
                                      {att.name || info?.name || `ID ${att.id}`} ×{att.number?.toLocaleString('en-US') ?? 0}
                                    </Chip.Label>
                                  </Chip>
                                )
                              })
                            ) : (
                              <span className="text-muted">无附件</span>
                            )}
                          </div>
                        </td>
                        <td className="py-3 px-4 text-end">
                          <Chip
                            size="sm"
                            variant="soft"
                            color={mail.is_claimed ? 'default' : mail.attachments?.length ? 'warning' : 'default'}
                          >
                            <Chip.Label>
                              {mail.is_claimed ? '已领取' : mail.attachments?.length ? '未领取' : (mail.is_read ? '已读' : '未读')}
                            </Chip.Label>
                          </Chip>
                        </td>
                        <td className="py-3 px-3 text-center" onClick={(e) => { e.stopPropagation(); openDbDetail(mail); }}>
                            <Button
                              size="sm"
                              variant="ghost"
                              isIconOnly
                              className="size-7 text-muted group-hover:text-accent cursor-pointer"
                              aria-label="查看邮件正文详情"
                            >
                            <Eye className="size-3.5" />
                          </Button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </Card>

        {/* 邮件正文与详情查看模态框 (HeroUI 3 Modal) */}
        <Modal.Root isOpen={isDetailModalOpen} onOpenChange={setIsDetailModalOpen}>
          <Modal.Backdrop className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4">
            <Modal.Container className="w-full max-w-lg max-h-[85vh] flex flex-col pointer-events-none">
              <Modal.Dialog className="relative w-full pointer-events-auto bg-surface border border-separator rounded-2xl shadow-2xl p-5 flex flex-col max-h-[85vh] overflow-hidden">
                {/* 右上角绝对定位关闭按钮 */}
                <button
                  type="button"
                  className="absolute top-4 right-4 z-20 flex size-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-secondary hover:text-foreground cursor-pointer focus:outline-none"
                  aria-label="关闭"
                  onClick={() => setIsDetailModalOpen(false)}
                >
                  <X className="size-4" />
                </button>

                <Modal.Header className="pb-3 border-b border-separator shrink-0 pr-8">
                  <Modal.Heading className="text-base font-semibold text-foreground flex items-center gap-2">
                    <MailIcon className="size-5 text-accent" />
                    邮件详情 #{selectedMail?.id}
                  </Modal.Heading>
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mt-1 text-xs text-muted">
                    <span>发件人：<strong className="text-foreground">{selectedMail?.sender}</strong></span>
                    <span>·</span>
                    <span>时间：{selectedMail?.sent_at_str}</span>
                    {selectedMail?.statusText && (
                      <>
                        <span>·</span>
                        <Chip size="sm" variant="soft" color={selectedMail.statusColor || 'default'} className="h-4.5 text-[10px] px-1.5">
                          <Chip.Label>{selectedMail.statusText}</Chip.Label>
                        </Chip>
                      </>
                    )}
                  </div>
                </Modal.Header>

                <Modal.Body className="py-4 space-y-4 flex-1 overflow-y-auto cp-scroll">
                  {/* 邮件标题 */}
                  <div className="space-y-1">
                    <span className="text-[11px] font-medium text-muted">公文标题</span>
                    <div className="p-2.5 rounded-xl border border-separator/70 bg-surface-secondary/40 text-xs font-semibold text-foreground select-text">
                      {selectedMail?.title || '（无标题）'}
                    </div>
                  </div>

                  {/* 邮件正文 */}
                  <div className="space-y-1">
                    <span className="text-[11px] font-medium text-muted">公文正文全文</span>
                    <div className="p-3.5 rounded-xl border border-separator/70 bg-surface-secondary/30 text-xs font-mono text-foreground leading-relaxed whitespace-pre-wrap select-text max-h-48 overflow-y-auto cp-scroll">
                      {selectedMail?.content || '（此邮件无文字正文）'}
                    </div>
                  </div>

                  {/* 附件资产 */}
                  <div className="space-y-2">
                    <span className="text-[11px] font-medium text-muted flex items-center gap-1.5">
                      <Paperclip className="size-3.5 text-accent" />
                      附件资产明细 ({selectedMail?.attachments?.length || 0} 项)
                    </span>

                    {!selectedMail?.attachments || selectedMail.attachments.length === 0 ? (
                      <div className="rounded-xl border border-dashed border-separator/60 p-4 text-center text-xs text-muted">
                        此邮件未携带任何附件资产
                      </div>
                    ) : (
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-40 overflow-y-auto cp-scroll p-1 border border-separator/60 rounded-xl bg-surface-secondary/20">
                        {selectedMail.attachments.map((att, idx) => {
                          const info = catalogMap.get(att.id)
                          const count = att.number ?? (att as any).count ?? 0
                          return (
                            <div
                              key={idx}
                              className="flex items-center gap-2.5 p-2 rounded-xl border border-separator/60 bg-surface hover:bg-surface-secondary/60 transition-colors"
                            >
                              <div className="shrink-0 pointer-events-none">
                                <ItemSlot
                                  id={att.id}
                                  name={att.name || info?.name || `ID ${att.id}`}
                                  rare={info?.rare || 4}
                                  iconFile={info?.icon_file || `${att.id}.png`}
                                  qualityFrame={info?.quality_frame || 'Item_purple.png'}
                                  size="sm"
                                  showCount={false}
                                  showAddButton={false}
                                />
                              </div>
                              <div className="min-w-0 flex-1">
                                <div className="text-xs truncate font-medium text-foreground">
                                  {att.name || info?.name || `道具 ${att.id}`}
                                </div>
                                <div className="mt-0.5 flex items-center justify-between text-[11px] font-mono text-muted">
                                  <span>ID: {att.id}</span>
                                  <span className="text-foreground font-semibold">×{count.toLocaleString('en-US')}</span>
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                </Modal.Body>

                <Modal.Footer className="flex items-center justify-between border-t border-separator pt-3.5 shrink-0">
                  <span className="text-[11px] text-muted">
                    {selectedMail?.isFromAudit ? '来源：GM 派发专有持久化审计记录' : '来源：游戏玩家信箱 (mail 表)'}
                  </span>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="rounded-xl cursor-pointer text-xs"
                    onPress={() => setIsDetailModalOpen(false)}
                  >
                    关闭
                  </Button>
                </Modal.Footer>
              </Modal.Dialog>
            </Modal.Container>
          </Modal.Backdrop>
        </Modal.Root>
      </Section>
    </div>
  )
}



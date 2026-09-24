import { useState, useEffect, useMemo, useRef } from 'react'
import {
  Check,
  Inbox,
  LayoutGrid,
  Mail,
  Package,
  Plus,
  RefreshCw,
  Rows3,
  Table2,
  Trash2,
} from 'lucide-react'
import {
  Button,
  Card,
  Chip,
  ListBox,
  Modal,
  SearchField,
  Select,
  Separator,
  cn,
} from '@heroui/react'
import { PageHeader, PlainSwitch } from '../components/kit'
import { ItemSlot } from '../components/ItemSlot'
import { useServer } from '../lib/serverContext'
import {
  fetchInventoryItems,
  resolveAssetUrl,
  isTrialSkinFilm,
  isDisplayOnlyItem,
} from '../lib/api'
import { useElementSize } from '../lib/useElementSize'
import defaultCatalog from '../data/items_catalog.json'
import { INVENTORY_OFFLINE_SNAPSHOT } from '../data/inventory_fallback'

// 官方字典条目原始结构
interface CatalogItem {
  id: number
  name: string
  type: number
  rare: number // 1 ~ 5
  icon_file: string
  quality_frame: string
  suit_id?: number
}

// 展现层融合结构
interface InventoryDisplayItem extends CatalogItem {
  category: string
  ownedCount: number
}

// 邮件草稿箱暂存项结构
export interface MailDraftAttachment {
  id: number
  name: string
  rare: number
  icon_file: string
  quality_frame: string
  count: number
}

const STORAGE_KEY_MAIL_DRAFT = 'gm_mail_draft_attachments'

function loadSavedDraft(): MailDraftAttachment[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = localStorage.getItem(STORAGE_KEY_MAIL_DRAFT)
    if (!raw) return []
    const parsed: MailDraftAttachment[] = JSON.parse(raw)
    return Array.isArray(parsed)
      ? parsed.filter((d) => !isDisplayOnlyItem(Number(d.id), undefined, d.name))
      : []
  } catch {
    return []
  }
}

function saveDraft(draft: MailDraftAttachment[]) {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(STORAGE_KEY_MAIL_DRAFT, JSON.stringify(draft))
  } catch (e) {
    console.error('保存邮件附件草稿失败:', e)
  }
}

// 保留的历史活动素材最新款（求援通讯α/β、补给清单Ⅰ/Ⅱ、锤炼之证、限时凭证），作为素材预览展示
const EVENT_PREVIEW_TOKEN_IDS = new Set([54128, 54129, 53214, 53221, 54096, 83, 85, 69, 78])

// 官方物品类型映射分类标签（礼物纠正并合并归类，移除独立的赋能模块；试衣底片统一以培养材料/素材展示）
function getCategoryLabel(type: number, id?: number, name?: string): string {
  if (id && (isTrialSkinFilm(id, name) || EVENT_PREVIEW_TOKEN_IDS.has(id))) {
    return '培养材料'
  }
  switch (type) {
    case 1:
      return '通用货币'
    case 5:
      return '宝箱礼包'
    case 4:
    case 3:
      return '培养材料'
    case 7:
      return '刻印套装'
    case 20:
    case 26:
    case 14:
      return '个性装扮'
    case 10:
    case 6:
    default:
      return '其他道具'
  }
}

// 分类微胶囊徽章样式（方案 A 科技信息牌）
function getCategoryBadgeStyle(type: number, id?: number, name?: string): string {
  if (id && (isTrialSkinFilm(id, name) || EVENT_PREVIEW_TOKEN_IDS.has(id))) {
    return 'bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border-indigo-500/25'
  }
  switch (type) {
    case 1:
      return 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/25'
    case 5:
      return 'bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/25'
    case 4:
    case 3:
      return 'bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border-indigo-500/25'
    case 7:
      return 'bg-purple-500/10 text-purple-700 dark:text-purple-400 border-purple-500/25'
    case 20:
    case 26:
    case 14:
      return 'bg-pink-500/10 text-pink-700 dark:text-pink-400 border-pink-500/25'
    default:
      return 'bg-slate-500/10 text-slate-700 dark:text-slate-400 border-slate-500/25'
  }
}

// 品质卡片边框与悬浮光晕（5★金 / 4★紫 / 3★蓝 / 2★绿 / 1★白）
function getQualityCardBorder(rare: number): string {
  switch (rare) {
    case 5:
      return 'hover:border-amber-400/60 hover:shadow-[0_4px_16px_rgba(245,158,11,0.12)]'
    case 4:
      return 'hover:border-purple-400/60 hover:shadow-[0_4px_16px_rgba(168,85,247,0.12)]'
    case 3:
      return 'hover:border-blue-400/60 hover:shadow-[0_4px_16px_rgba(59,130,246,0.10)]'
    case 2:
      return 'hover:border-emerald-400/60 hover:shadow-[0_4px_16px_rgba(16,185,129,0.10)]'
    default:
      return 'hover:border-slate-400/40 hover:shadow-[0_4px_12px_rgba(148,163,184,0.08)]'
  }
}

const CATEGORIES = [
  '全部',
  '通用货币',
  '宝箱礼包',
  '培养材料',
  '刻印套装',
  '个性装扮',
  '其他道具',
]

const QUALITIES = [
  { id: 'all', label: '全部品质' },
  { id: '5', label: '5★ 金色' },
  { id: '4', label: '4★ 紫色' },
  { id: '3', label: '3★ 蓝色' },
  { id: '2', label: '2★ 绿色' },
  { id: '1', label: '1★ 白色' },
]

const VIEWS = [
  { id: 'grid', label: '网格卡片', icon: LayoutGrid },
  { id: 'table', label: '数据表格', icon: Table2 },
  { id: 'list', label: '紧凑列表', icon: Rows3 },
] as const

type ViewMode = (typeof VIEWS)[number]['id']

// 单页规模配置档位
const PAGE_SIZES = [
  { id: '48', label: '48', value: 48 },
  { id: '96', label: '96', value: 96 },
  { id: '240', label: '240', value: 240 },
  { id: 'all', label: '全部', value: -1 },
] as const

/* ---------------- 虚拟滚动与布局常量 ---------------- */
const VIEWPORT_HEIGHT = 600
const GRID_GAP = 12
const GRID_MIN_WIDTH = 136
const GRID_CARD_HEIGHT = 168
const GRID_PADDING = 12
const ROW_HEIGHT = { grid: GRID_CARD_HEIGHT + GRID_GAP, table: 48, list: 46 } as const
const OVERSCAN = 3

const TABLE_COLUMNS = 'minmax(0,1.8fr) 100px 120px 100px 120px 110px'
const LIST_COLUMNS = '80px minmax(0,1.8fr) 120px 90px 120px 60px'

function HeaderCell({ children, align }: { children: React.ReactNode; align?: 'end' }) {
  return (
    <div className={cn('truncate px-4 text-xs font-medium text-muted', align === 'end' && 'text-end')}>
      {children}
    </div>
  )
}

export default function InventoryPanel() {
  const { activeUid } = useServer()

  // 1. 核心数据状态（带离线 1,757 项全图鉴与 879 项真实持仓离线兜底，永不为0）
  const [catalog, setCatalog] = useState<CatalogItem[]>(defaultCatalog as CatalogItem[])
  const [ownedMap, setOwnedMap] = useState<Record<number, number>>(INVENTORY_OFFLINE_SNAPSHOT)
  const [isLoading, setIsLoading] = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)

  // 2. 筛选与视图状态
  const [view, setView] = useState<ViewMode>('grid')
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('全部')
  const [quality, setQuality] = useState('all')
  const [onlyOwned, setOnlyOwned] = useState(false)

  // 3. 数据规模控制器与分页
  const [pageSizeOption, setPageSizeOption] = useState<string>('48')
  const [page, setPage] = useState(1)

  // 4. 邮件草稿附件池
  const [mailDraft, setMailDraft] = useState<MailDraftAttachment[]>(loadSavedDraft)
  const [isDraftModalOpen, setIsDraftModalOpen] = useState(false)

  // 异步加载官方 1,757 项纯净条目字典
  useEffect(() => {
    let isMounted = true
    const catalogUrl = resolveAssetUrl('extracted_assets/items/items_catalog.json')
    fetch(catalogUrl)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then((data: CatalogItem[]) => {
        if (isMounted) {
          setCatalog(data)
        }
      })
      .catch((err) => {
        console.error('加载 items_catalog.json 异常:', err)
      })
    return () => {
      isMounted = false
    }
  }, [])

  // 异步拉取当前账号在测试库的真实持仓
  const loadInventory = async (isManual = false) => {
    if (isManual) setIsRefreshing(true)
    try {
      const res = await fetchInventoryItems(activeUid)
      if (res.code === 0 && res.data && Array.isArray(res.data.items)) {
        const map: Record<number, number> = {}
        for (const it of res.data.items) {
          map[it.id] = (map[it.id] || 0) + (it.num || 1)
        }
        setOwnedMap(map)
      }
    } catch (err) {
      console.warn('获取玩家背包持仓失败:', err)
    } finally {
      setIsLoading(false)
      if (isManual) setIsRefreshing(false)
    }
  }

  useEffect(() => {
    loadInventory()
  }, [activeUid])

  // 将 catalog 与 ownedMap O(1) 融合
  const mergedItems = useMemo<InventoryDisplayItem[]>(() => {
    return catalog.map((cat) => ({
      ...cat,
      category: getCategoryLabel(cat.type, cat.id, cat.name),
      ownedCount: ownedMap[cat.id] || 0,
    }))
  }, [catalog, ownedMap])

  // 多维筛选计算
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return mergedItems.filter((item) => {
      // 1. 仅看已拥有
      if (onlyOwned && item.ownedCount <= 0) return false

      // 2. 分类筛选
      if (category !== '全部' && item.category !== category) return false

      // 3. 品质筛选
      if (quality !== 'all' && String(item.rare) !== quality) return false

      // 4. 关键词检索 (支持 ID / 名称)
      if (q) {
        const matchId = String(item.id).includes(q)
        const matchName = item.name.toLowerCase().includes(q)
        if (!matchId && !matchName) return false
      }

      return true
    })
  }, [mergedItems, query, category, quality, onlyOwned])

  // 单页规模数值
  const activePageSize = useMemo(() => {
    const found = PAGE_SIZES.find((s) => s.id === pageSizeOption)
    return found ? found.value : 48
  }, [pageSizeOption])

  // 分页总数计算
  const totalPages =
    activePageSize === -1 ? 1 : Math.max(1, Math.ceil(filtered.length / activePageSize))

  // 当前激活的数据切片（若选择全部，则为全量 filtered；否则为当前页条目）
  const currentViewItems = useMemo(() => {
    if (activePageSize === -1) {
      return filtered
    }
    const start = (page - 1) * activePageSize
    return filtered.slice(start, start + activePageSize)
  }, [filtered, page, activePageSize])

  /* ---------------- 视口与虚拟滚动计算 ---------------- */
  const { ref: viewportRef, width: viewportWidth, height: viewportHeight } =
    useElementSize<HTMLDivElement>()
  const [scrollTop, setScrollTop] = useState(0)

  // 网格模式下列数随容器宽度自适应（增加多层健壮兜底，杜绝视口尺寸未就绪时异常跌至 2 列）
  const effectiveWidth =
    viewportWidth > 0
      ? viewportWidth
      : viewportRef.current?.clientWidth ||
        (typeof window !== 'undefined' && window.innerWidth ? window.innerWidth - 300 : 1200)

  const columns =
    view === 'grid'
      ? Math.max(
          2,
          Math.floor((effectiveWidth - GRID_PADDING * 2 + GRID_GAP) / (GRID_MIN_WIDTH + GRID_GAP)),
        )
      : 1

  const rowHeight = ROW_HEIGHT[view]
  const totalRows = Math.ceil(currentViewItems.length / columns)
  const totalHeight = totalRows * rowHeight

  // 虚拟切片计算
  const height = viewportHeight || VIEWPORT_HEIGHT
  const startRow = Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN)
  const endRow = Math.min(totalRows, Math.ceil((scrollTop + height) / rowHeight) + OVERSCAN)

  const slice = currentViewItems.slice(
    startRow * columns,
    Math.min(currentViewItems.length, endRow * columns),
  )
  const offsetY = startRow * rowHeight

  // 平滑滚动监听
  const scrollRaf = useRef(0)
  const handleScroll = () => {
    if (scrollRaf.current) return
    scrollRaf.current = requestAnimationFrame(() => {
      scrollRaf.current = 0
      setScrollTop(viewportRef.current?.scrollTop ?? 0)
    })
  }

  // 控制项变动时重置页码与滚动位置
  const controlKey = `${query}|${category}|${quality}|${onlyOwned}|${view}|${pageSizeOption}`
  useEffect(() => {
    setPage(1)
    if (viewportRef.current) viewportRef.current.scrollTop = 0
    setScrollTop(0)
  }, [controlKey])

  // 翻页时回到视口顶部
  useEffect(() => {
    if (viewportRef.current) viewportRef.current.scrollTop = 0
    setScrollTop(0)
  }, [page])

  // 统计指标
  const ownedTotalTypes = Object.keys(ownedMap).length

  // 添加/移除邮件附件草稿
  const handleToggleDraft = (item: InventoryDisplayItem) => {
    if (isDisplayOnlyItem(item.id, item.type, item.name)) {
      return
    }
    setMailDraft((prev) => {
      const exists = prev.some((d) => d.id === item.id)
      let next: MailDraftAttachment[]
      if (exists) {
        next = prev.filter((d) => d.id !== item.id)
      } else {
        next = [
          ...prev,
          {
            id: item.id,
            name: item.name,
            rare: item.rare,
            icon_file: item.icon_file,
            quality_frame: item.quality_frame,
            count: 1,
          },
        ]
      }
      saveDraft(next)
      return next
    })
  }

  const handleUpdateDraftCount = (id: number, count: number) => {
    setMailDraft((prev) => {
      const next = prev.map((d) => (d.id === id ? { ...d, count: Math.max(1, count) } : d))
      saveDraft(next)
      return next
    })
  }

  const handleClearDraft = () => {
    setMailDraft([])
    saveDraft([])
  }

  const isItemInDraft = (id: number) => mailDraft.some((d) => d.id === id)

  /* ---------------- 视图行渲染函数 ---------------- */
  const renderCard = (item: InventoryDisplayItem) => {
    const isDisplayOnly = isDisplayOnlyItem(item.id, item.type, item.name)
    const inDraft = isItemInDraft(item.id)
    const isOwned = item.ownedCount > 0
    return (
      <Card
        key={item.id}
        className={cn(
          'group relative flex flex-col items-center justify-between p-2.5 text-center transition-all duration-200 border border-separator/60 rounded-xl bg-surface hover:bg-surface-secondary/70 shadow-xs',
          getQualityCardBorder(item.rare),
          !isOwned && 'opacity-65 hover:opacity-100',
        )}
        style={{ height: GRID_CARD_HEIGHT }}
      >
        {/* 上半部：正版品质框槽位 */}
        <div className="pt-0.5">
          <ItemSlot
            id={item.id}
            name={item.name}
            rare={item.rare}
            iconFile={item.icon_file}
            qualityFrame={item.quality_frame}
            count={item.ownedCount}
            size="lg"
            showCount={isOwned}
            showAddButton={!isDisplayOnly}
            isAdded={inDraft}
            onAdd={() => handleToggleDraft(item)}
          />
        </div>

        {/* 下半部：科技信息底牌（标题居中醒目 + 规整胶囊标签） */}
        <div className="w-full min-w-0 px-0.5 pb-0.5">
          <div
            className="truncate text-xs font-semibold text-foreground group-hover:text-accent transition-colors"
            title={item.name}
          >
            {item.name}
          </div>
          <div className="mt-1.5 flex items-center justify-between gap-1 text-[10px]">
            <span className="inline-flex items-center rounded px-1.5 py-0.5 font-mono font-medium text-muted-foreground bg-muted/20 border border-separator/40 leading-none">
              #{item.id}
            </span>
            <span
              className={cn(
                'inline-flex items-center rounded px-1.5 py-0.5 font-medium border leading-none truncate max-w-[76px]',
                getCategoryBadgeStyle(item.type, item.id, item.name),
              )}
              title={item.category}
            >
              {item.category}
            </span>
          </div>
        </div>
      </Card>
    )
  }

  const renderTableRow = (item: InventoryDisplayItem) => {
    const isDisplayOnly = isDisplayOnlyItem(item.id, item.type, item.name)
    const inDraft = isItemInDraft(item.id)
    return (
      <div
        key={item.id}
        className="grid items-center border-b border-separator/40 bg-surface hover:bg-surface-secondary/60 transition-colors"
        style={{ height: rowHeight, gridTemplateColumns: TABLE_COLUMNS }}
      >
        <div className="flex min-w-0 items-center gap-2.5 px-4">
          <ItemSlot
            id={item.id}
            name={item.name}
            rare={item.rare}
            iconFile={item.icon_file}
            qualityFrame={item.quality_frame}
            size="sm"
            showCount={false}
          />
          <span className="truncate text-xs font-medium text-foreground" title={item.name}>
            {item.name}
          </span>
        </div>
        <div className="truncate px-4 font-mono text-xs text-muted">{item.id}</div>
        <div className="truncate px-4 text-xs text-muted">{item.category}</div>
        <div className="px-4">
          <Chip
            size="sm"
            variant="soft"
            color={
              item.rare === 5
                ? 'warning'
                : item.rare === 4
                ? 'accent'
                : item.rare === 3
                ? 'default'
                : 'default'
            }
          >
            <Chip.Label>{item.rare}★ 品质</Chip.Label>
          </Chip>
        </div>
        <div className="px-4 text-end font-mono text-xs font-medium tabular-nums">
          {item.ownedCount > 0 ? (
            <span className="text-foreground">{item.ownedCount.toLocaleString('en-US')}</span>
          ) : (
            <span className="text-muted/60">0</span>
          )}
        </div>
        <div className="px-4 text-end">
          {isDisplayOnly ? (
            <span className="text-[11px] text-muted-foreground/60 select-none">仅供展示</span>
          ) : (
            <Button
              size="sm"
              variant={inDraft ? 'secondary' : 'ghost'}
              className="gap-1 text-xs h-7 px-2"
              onPress={() => handleToggleDraft(item)}
            >
              {inDraft ? (
                <>
                  <Check className="size-3 text-success" />
                  <span>已存草稿</span>
                </>
              ) : (
                <>
                  <Plus className="size-3" />
                  <span>加入附件</span>
                </>
              )}
            </Button>
          )}
        </div>
      </div>
    )
  }

  const renderListRow = (item: InventoryDisplayItem) => {
    const isDisplayOnly = isDisplayOnlyItem(item.id, item.type, item.name)
    const inDraft = isItemInDraft(item.id)
    return (
      <div
        key={item.id}
        className="grid items-center border-b border-separator/40 bg-surface hover:bg-surface-secondary/60 transition-colors"
        style={{ height: rowHeight, gridTemplateColumns: LIST_COLUMNS }}
      >
        <div className="truncate px-4 font-mono text-[11px] text-muted">{item.id}</div>
        <div className="flex min-w-0 items-center gap-2.5 px-4">
          <ItemSlot
            id={item.id}
            name={item.name}
            rare={item.rare}
            iconFile={item.icon_file}
            qualityFrame={item.quality_frame}
            size="sm"
            showCount={false}
          />
          <span className="truncate text-xs font-medium text-foreground" title={item.name}>
            {item.name}
          </span>
        </div>
        <div className="truncate px-4 text-xs text-muted">{item.category}</div>
        <div className="px-4">
          <span
            className={cn(
              'inline-flex items-center gap-1 text-xs',
              item.rare === 5
                ? 'text-warning font-medium'
                : item.rare === 4
                ? 'text-accent font-medium'
                : 'text-muted',
            )}
          >
            {item.rare}★
          </span>
        </div>
        <div className="px-4 text-end font-mono text-xs tabular-nums">
          {item.ownedCount > 0 ? (
            <span className="font-semibold text-foreground">
              ×{item.ownedCount.toLocaleString('en-US')}
            </span>
          ) : (
            <span className="text-muted/40">—</span>
          )}
        </div>
        <div className="px-4 text-end">
          {isDisplayOnly ? (
            <span className="text-[11px] text-muted-foreground/60 select-none">仅供展示</span>
          ) : (
            <Button
              isIconOnly
              size="sm"
              variant={inDraft ? 'secondary' : 'ghost'}
              className="size-7"
              onPress={() => handleToggleDraft(item)}
              aria-label="加入附件"
            >
              {inDraft ? <Check className="size-3.5 text-success" /> : <Plus className="size-3.5" />}
            </Button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* 1. 顶部 Header */}
      <PageHeader
        title="资源背包 · 资产监控"
        description="直通游戏数据库真实货币、素材与刻印持仓。官方 1,757 项全图鉴覆盖，精准匹配原生品质框与图标，支持附件快捷加入邮件草稿池。"
        actions={
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant={mailDraft.length > 0 ? 'secondary' : 'ghost'}
              className="relative gap-1.5"
              onPress={() => setIsDraftModalOpen(true)}
            >
              <Mail className="size-4" />
              <span>邮件草稿箱</span>
              {mailDraft.length > 0 && (
                <span className="rounded-full bg-accent px-1.5 py-0.5 text-[10px] font-semibold text-accent-foreground leading-none">
                  {mailDraft.length}
                </span>
              )}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              className="gap-1.5"
              onPress={() => loadInventory(true)}
              isDisabled={isRefreshing}
            >
              <RefreshCw className={cn('size-4', isRefreshing && 'animate-spin')} />
              <span>刷新库存</span>
            </Button>
          </div>
        }
      />

      {/* 2. 参照 Warehouse 架构的数据规模与搜索管理卡片 */}
      <Card className="p-5 shadow-surface">
        <Card.Content className="space-y-4">
          {/* 第一行：数据指标 + 单页规模控制器 + 仅看已拥有 + 视图切换 */}
          <div className="flex flex-wrap items-center justify-between gap-4">
            {/* 核心统计指标 */}
            <div className="flex flex-wrap items-center gap-4 text-xs">
              <div className="flex items-center gap-1.5">
                <span className="text-muted">全服图鉴</span>
                <span className="font-mono font-semibold tabular-nums text-foreground">
                  {catalog.length.toLocaleString('en-US')} 项
                </span>
              </div>
              <Separator orientation="vertical" className="h-3.5" />
              <div className="flex items-center gap-1.5">
                <span className="text-muted">当前已拥有</span>
                <span className="font-mono font-semibold tabular-nums text-accent">
                  {ownedTotalTypes.toLocaleString('en-US')} 种
                </span>
              </div>
              <Separator orientation="vertical" className="h-3.5" />
              <div className="flex items-center gap-1.5">
                <span className="text-muted">筛选命中</span>
                <span className="font-mono font-semibold tabular-nums text-success">
                  {filtered.length.toLocaleString('en-US')} 条
                </span>
              </div>
            </div>

            {/* 右侧控制集群：单页规模控制器 + 仅看已拥有开关 + 三视图切换 */}
            <div className="flex flex-wrap items-center gap-3">
              {/* 单页规模控制器 */}
              <div className="flex items-center gap-1.5">
                <span className="text-xs font-medium text-muted">单页显示:</span>
                <div className="flex items-center gap-1 rounded-xl bg-default/60 p-1">
                  {PAGE_SIZES.map((option) => (
                    <Button
                      key={option.id}
                      size="sm"
                      variant={pageSizeOption === option.id ? 'secondary' : 'ghost'}
                      className={cn(
                        'h-7 px-2 text-xs font-mono tabular-nums',
                        pageSizeOption === option.id && 'shadow-surface font-semibold',
                      )}
                      onPress={() => setPageSizeOption(option.id)}
                    >
                      {option.label}
                    </Button>
                  ))}
                </div>
              </div>

              <Separator orientation="vertical" className="h-4 hidden sm:block" />

              {/* 仅看已拥有开关 */}
              <div className="flex items-center gap-2">
                <span
                  className="text-xs font-medium text-muted cursor-pointer select-none hover:text-foreground transition-colors"
                  onClick={() => setOnlyOwned(!onlyOwned)}
                >
                  仅看已拥有
                </span>
                <PlainSwitch isSelected={onlyOwned} onChange={setOnlyOwned} label="仅看已拥有" />
              </div>

              <Separator orientation="vertical" className="h-4 hidden sm:block" />

              {/* 三视图切换分段器 */}
              <div className="flex items-center gap-1 rounded-xl bg-default/60 p-1">
                {VIEWS.map((option) => (
                  <Button
                    key={option.id}
                    size="sm"
                    variant={view === option.id ? 'secondary' : 'ghost'}
                    className={cn('h-7 gap-1 px-2.5 text-xs', view === option.id && 'shadow-surface')}
                    onPress={() => setView(option.id)}
                  >
                    <option.icon className="size-3.5" />
                    <span className="hidden sm:inline">{option.label}</span>
                  </Button>
                ))}
              </div>
            </div>
          </div>

          <Separator />

          {/* 第二行：多维搜索与过滤组件 */}
          <div className="flex flex-wrap items-center gap-3">
            {/* 搜索框 */}
            <SearchField
              aria-label="检索物品名称或 ID"
              fullWidth
              className="w-full sm:w-[280px]"
              value={query}
              onChange={setQuery}
            >
              <SearchField.Group>
                <SearchField.SearchIcon />
                <SearchField.Input placeholder="检索物品名称或 ID..." />
                <SearchField.ClearButton />
              </SearchField.Group>
            </SearchField>

            {/* 分类下拉（已精简并移除“赋能模块”） */}
            <Select
              aria-label="按业务分类筛选"
              className="w-[140px]"
              selectedKey={category}
              onSelectionChange={(key) => setCategory(String(key))}
            >
              <Select.Trigger>
                <Select.Value />
                <Select.Indicator />
              </Select.Trigger>
              <Select.Popover>
                <ListBox>
                  {CATEGORIES.map((catName) => (
                    <ListBox.Item key={catName} id={catName}>
                      {catName}
                    </ListBox.Item>
                  ))}
                </ListBox>
              </Select.Popover>
            </Select>

            {/* 品质下拉 */}
            <Select
              aria-label="按品质星级筛选"
              className="w-[130px]"
              selectedKey={quality}
              onSelectionChange={(key) => setQuality(String(key))}
            >
              <Select.Trigger>
                <Select.Value />
                <Select.Indicator />
              </Select.Trigger>
              <Select.Popover>
                <ListBox>
                  {QUALITIES.map((q) => (
                    <ListBox.Item key={q.id} id={q.id}>
                      {q.label}
                    </ListBox.Item>
                  ))}
                </ListBox>
              </Select.Popover>
            </Select>

            {/* 快捷清空重置 */}
            {(query || category !== '全部' || quality !== 'all' || onlyOwned) && (
              <Button
                size="sm"
                variant="ghost"
                className="text-xs text-muted hover:text-foreground"
                onPress={() => {
                  setQuery('')
                  setCategory('全部')
                  setQuality('all')
                  setOnlyOwned(false)
                }}
              >
                重置筛选
              </Button>
            )}

            {/* 性能与挂载指标显示 */}
            <div className="ms-auto flex items-center gap-3 text-xs text-muted">
              <span className="hidden md:inline">
                视口挂载 <span className="font-mono font-medium text-foreground">{slice.length}</span> 项
                （虚拟滚动中）
              </span>
              <Separator orientation="vertical" className="h-3.5 hidden md:block" />
              <span>
                {activePageSize === -1 ? (
                  <span className="font-mono text-accent">全量直览</span>
                ) : (
                  <span>
                    第 <span className="font-mono font-semibold text-foreground">{page}</span> /{' '}
                    <span className="font-mono">{totalPages}</span> 页
                  </span>
                )}
              </span>
            </div>
          </div>
        </Card.Content>
      </Card>

      {/* 3. 物品展示主体区（虚拟滚动视口常驻） */}
      {isLoading ? (
        <div className="flex h-64 flex-col items-center justify-center gap-3">
          <RefreshCw className="size-6 animate-spin text-muted" />
          <p className="text-sm text-muted">正在加载背包资产与官方图鉴...</p>
        </div>
      ) : (
        <div className="space-y-3">
          {/* 虚拟滚动视口容器（常驻挂载，绝对不因过滤集为空而销毁重建） */}
          <div
            ref={viewportRef}
            onScroll={handleScroll}
            className="cp-scroll relative overflow-auto rounded-2xl border border-separator/50 bg-surface-secondary/40 shadow-inner"
            style={{ height: VIEWPORT_HEIGHT }}
          >
            {filtered.length === 0 ? (
              <div className="flex h-full min-h-[460px] flex-col items-center justify-center gap-2 text-center p-8">
                <Inbox className="size-8 text-muted opacity-60" />
                <p className="text-sm font-medium">未找到符合条件的物品</p>
                <p className="text-xs text-muted">可尝试清空搜索词或切换品质与分类过滤</p>
              </div>
            ) : (
              <>
                {/* 表格/列表粘性表头 */}
                {view !== 'grid' && currentViewItems.length > 0 && (
                  <div
                    className="sticky top-0 z-20 grid items-center border-b border-separator bg-surface shadow-xs"
                    style={{
                      height: 38,
                      gridTemplateColumns: view === 'table' ? TABLE_COLUMNS : LIST_COLUMNS,
                    }}
                  >
                    {view === 'table' ? (
                      <>
                        <HeaderCell>物品信息</HeaderCell>
                        <HeaderCell>ID</HeaderCell>
                        <HeaderCell>分类</HeaderCell>
                        <HeaderCell>品质</HeaderCell>
                        <HeaderCell align="end">持仓数量</HeaderCell>
                        <HeaderCell align="end">草稿附件</HeaderCell>
                      </>
                    ) : (
                      <>
                        <HeaderCell>ID</HeaderCell>
                        <HeaderCell>物品信息</HeaderCell>
                        <HeaderCell>分类</HeaderCell>
                        <HeaderCell>品质</HeaderCell>
                        <HeaderCell align="end">持仓</HeaderCell>
                        <HeaderCell align="end">附件</HeaderCell>
                      </>
                    )}
                  </div>
                )}

                {/* 虚拟画布容器 */}
                <div style={{ height: totalHeight }} className={view === 'grid' ? 'p-3' : undefined}>
                  <div
                    style={
                      view === 'grid'
                        ? {
                            transform: `translateY(${offsetY}px)`,
                            display: 'grid',
                            gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
                            gap: GRID_GAP,
                          }
                        : { transform: `translateY(${offsetY}px)` }
                    }
                  >
                    {view === 'grid' && slice.map(renderCard)}
                    {view === 'table' && slice.map(renderTableRow)}
                    {view === 'list' && slice.map(renderListRow)}
                  </div>
                </div>
              </>
            )}
          </div>

          {/* 底部翻页控制器与状态提示 */}
          <div className="flex items-center justify-between pt-1">
            <span className="text-xs text-muted">
              {filtered.length === 0 ? (
                <span>共 0 项结果</span>
              ) : activePageSize === -1 ? (
                <span>
                  已展示当前筛选的全部{' '}
                  <span className="font-mono font-medium text-foreground">
                    {filtered.length.toLocaleString('en-US')}
                  </span>{' '}
                  项，虚拟滚动已就绪
                </span>
              ) : (
                <span>
                  共 {filtered.length.toLocaleString('en-US')} 项，当前显示第{' '}
                  {(page - 1) * activePageSize + 1} -{' '}
                  {Math.min(filtered.length, page * activePageSize)} 项
                </span>
              )}
            </span>

            {filtered.length > 0 && activePageSize !== -1 && totalPages > 1 && (
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  isDisabled={page <= 1}
                  onPress={() => setPage((p) => Math.max(1, p - 1))}
                >
                  上一页
                </Button>
                <span className="font-mono text-xs tabular-nums text-foreground">
                  {page} / {totalPages}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  isDisabled={page >= totalPages}
                  onPress={() => setPage((p) => Math.min(totalPages, p + 1))}
                >
                  下一页
                </Button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 4. 邮件草稿附件箱模态弹窗（彻底修复暗屏居中问题） */}
      <Modal.Root isOpen={isDraftModalOpen} onOpenChange={setIsDraftModalOpen}>
        <Modal.Backdrop className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4">
          <Modal.Container className="w-full max-w-lg max-h-[85vh] flex flex-col pointer-events-none">
            <Modal.Dialog className="relative w-full pointer-events-auto bg-surface border border-separator rounded-2xl shadow-2xl p-5 flex flex-col max-h-[85vh] overflow-hidden">
              <Modal.Header className="flex items-center justify-between pb-3 border-b border-separator shrink-0">
                <div className="flex items-center gap-2">
                  <Mail className="size-5 text-accent" />
                  <Modal.Heading className="text-base font-semibold text-foreground">
                    邮件附件草稿池
                  </Modal.Heading>
                  {mailDraft.length > 0 && (
                    <Chip size="sm" variant="soft" color="accent">
                      <Chip.Label>{mailDraft.length} 项暂存</Chip.Label>
                    </Chip>
                  )}
                </div>
              </Modal.Header>

              <div className="py-2 text-xs text-muted border-b border-separator/50">
                此处暂存的物品将在后续「邮件系统」中作为附件统一配发。
              </div>

              <Modal.Body className="py-2 flex-1 overflow-y-auto cp-scroll">
                {mailDraft.length === 0 ? (
                  <div className="flex flex-col items-center justify-center gap-2 py-12 text-center text-muted">
                    <Package className="size-8 opacity-40" />
                    <p className="text-sm">附件池暂无物品</p>
                    <p className="text-xs">点击物品卡片右上角的 “+” 号可快捷加入</p>
                  </div>
                ) : (
                  <div className="divide-y divide-separator/60">
                    {mailDraft.map((row) => (
                      <div key={row.id} className="flex items-center gap-3 py-2.5">
                        <ItemSlot
                          id={row.id}
                          name={row.name}
                          rare={row.rare}
                          iconFile={row.icon_file}
                          qualityFrame={row.quality_frame}
                          size="sm"
                          showCount={false}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium text-foreground">
                            {row.name}
                          </div>
                          <div className="font-mono text-xs text-muted">ID {row.id}</div>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs text-muted">数量:</span>
                          <input
                            type="number"
                            min={1}
                            max={9999999}
                            value={row.count}
                            onChange={(e) =>
                              handleUpdateDraftCount(row.id, parseInt(e.target.value, 10) || 1)
                            }
                            className="w-18 rounded-md border border-separator bg-background px-2 py-1 text-center font-mono text-xs tabular-nums text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                          />
                        </div>
                        <Button
                          isIconOnly
                          size="sm"
                          variant="ghost"
                          className="text-danger hover:bg-danger/10"
                          onPress={() =>
                            setMailDraft((prev) => {
                              const next = prev.filter((d) => d.id !== row.id)
                              saveDraft(next)
                              return next
                            })
                          }
                          aria-label="移除此项"
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </Modal.Body>

              <Modal.Footer className="flex items-center justify-between pt-3 border-t border-separator shrink-0">
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-xs text-danger hover:bg-danger/10"
                  isDisabled={mailDraft.length === 0}
                  onPress={handleClearDraft}
                >
                  清空草稿
                </Button>
                <div className="flex items-center gap-2">
                  <Button size="sm" variant="ghost" onPress={() => setIsDraftModalOpen(false)}>
                    取消
                  </Button>
                  <Button size="sm" variant="secondary" onPress={() => setIsDraftModalOpen(false)}>
                    确定
                  </Button>
                </div>
              </Modal.Footer>
            </Modal.Dialog>
          </Modal.Container>
        </Modal.Backdrop>
      </Modal.Root>
    </div>
  )
}

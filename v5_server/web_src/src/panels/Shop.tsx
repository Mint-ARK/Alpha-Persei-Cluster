import { useState, useEffect, useMemo, useCallback } from 'react'
import {
  ShoppingBag,
  RotateCcw,
  Sparkles,
  Filter,
  Layers,
  LayoutGrid,
  Table as TableIcon,
  Check,
  Store,
  Tag,
  Ban,
  CheckCircle2,
} from 'lucide-react'
import {
  Button,
  Card,
  SearchField,
  cn,
} from '@heroui/react'
import { PageHeader, StatCard } from '../components/kit'
import { ItemSlot } from '../components/ItemSlot'
import { useServer } from '../lib/serverContext'
import {
  fetchShopCatalog,
  fetchShopGoods,
  fetchShopStats,
  resetShopPurchase,
  resolveAssetUrl,
  type ShopCatalogData,
  type ShopGoodsItem,
  type ShopMeta,
  type ShopStatsData,
  OFFLINE_SHOP_CATALOG_FALLBACK,
  OFFLINE_DAILY_GOODS_FALLBACK,
} from '../lib/api'

// 常用货币的图标文件映射（快速加载高清代币小图标）
const CURRENCY_ICONS: Record<number, string> = {
  1: '1.png',   // 移转之辉
  2: '2.png',   // 艾因索菲币
  3: '3.png',   // 友情点
  4: '4.png',   // 吨吨值
  5: '5.png',   // 修正者探测凭证
  19: '19.png', // 钥从探测凭证
  20: '20.png', // 精准探测凭证
  21: '21.png', // 常规探测凭证
  24: '24.png', // 换装券
  33: '33.png', // 偏移质素
  36: '36.png', // 共鸣辉芒
  38: '38.png', // 精确探测凭证
  40: '40.png', // 矩阵声望
  41: '41.png', // 残梦结晶
  42: '42.png', // 深梦核心
  43: '43.png', // 异变黑曜
  44: '44.png', // 映射仪
}

export default function ShopPanel() {
  const { activeUid } = useServer()

  // 1. 状态加载
  const [catalog, setCatalog] = useState<ShopCatalogData>(OFFLINE_SHOP_CATALOG_FALLBACK)
  const [activeGroup, setActiveGroup] = useState<string>('daily')
  const [selectedShopId, setSelectedShopId] = useState<number>(2)
  const [goods, setGoods] = useState<ShopGoodsItem[]>(OFFLINE_DAILY_GOODS_FALLBACK)
  const [stats, setStats] = useState<ShopStatsData>({
    uid: activeUid,
    total_shops: 115,
    total_goods: 3418,
    daily_refresh_times: 0,
    daily_refresh_max: 20,
    sold_out_goods_count: 0,
  })

  const [, setLoadingCatalog] = useState(false)
  const [loadingGoods, setLoadingGoods] = useState(false)

  // 2. 检索与筛选
  const [searchQuery, setSearchQuery] = useState('')
  const [filterLimitedOnly, setFilterLimitedOnly] = useState(false)
  const [filterSoldOutOnly, setFilterSoldOutOnly] = useState(false)
  const [viewMode, setViewMode] = useState<'grid' | 'table'>('grid')

  // 3. GM 操作状态与轻量 Toast 提示
  const [actionLoading, setActionLoading] = useState(false)
  const [toastMsg, setToastMsg] = useState<{ text: string; tone: 'success' | 'warning' | 'danger' } | null>(null)

  const showToast = useCallback((text: string, tone: 'success' | 'warning' | 'danger' = 'success') => {
    setToastMsg({ text, tone })
    setTimeout(() => setToastMsg(null), 3500)
  }, [])

  // 加载商店目录与综合指标
  const loadCatalogAndStats = useCallback(async () => {
    setLoadingCatalog(true)
    try {
      const [catRes, statRes] = await Promise.all([
        fetchShopCatalog(),
        fetchShopStats(activeUid),
      ])
      if (catRes.code === 0 && catRes.data) {
        setCatalog(catRes.data)
      }
      if (statRes.code === 0 && statRes.data) {
        setStats(statRes.data)
      }
    } catch (e: any) {
      console.error('加载商店目录异常:', e)
    } finally {
      setLoadingCatalog(false)
    }
  }, [activeUid])

  // 加载指定商店货架商品
  const loadGoods = useCallback(async (shopId: number) => {
    setLoadingGoods(true)
    try {
      const res = await fetchShopGoods(shopId, activeUid)
      if (res.code === 0 && res.data?.goods) {
        setGoods(res.data.goods)
      }
    } catch (e: any) {
      console.error('加载商店商品异常:', e)
    } finally {
      setLoadingGoods(false)
    }
  }, [activeUid])

  // 挂载与 UID 变动时初始化
  useEffect(() => {
    loadCatalogAndStats()
  }, [loadCatalogAndStats])

  useEffect(() => {
    loadGoods(selectedShopId)
  }, [selectedShopId, loadGoods])

  // 当前大类下属的商店清单
  const shopsInActiveGroup = useMemo(() => {
    const groupDef = catalog.groups.find((g) => g.id === activeGroup)
    if (!groupDef) return catalog.shops
    if (activeGroup === 'all') return catalog.shops
    const idSet = new Set(groupDef.shop_ids)
    return catalog.shops.filter((s) => idSet.has(s.shop_id))
  }, [catalog, activeGroup])

  // 当前选中的商店元数据
  const currentShopMeta = useMemo<ShopMeta | undefined>(() => {
    return catalog.shops.find((s) => s.shop_id === selectedShopId)
  }, [catalog.shops, selectedShopId])

  // 切换大类时，若当前商店不在大类中，自动定位到大类的第 1 个商店
  const handleGroupChange = (groupId: string) => {
    setActiveGroup(groupId)
    const groupDef = catalog.groups.find((g) => g.id === groupId)
    if (groupDef && groupDef.shop_ids.length > 0) {
      if (!groupDef.shop_ids.includes(selectedShopId)) {
        setSelectedShopId(groupDef.shop_ids[0])
      }
    }
  }

  // 商品列表过滤计算（严格过滤刻印与角色本体）
  const filteredGoods = useMemo(() => {
    return goods.filter((g) => {
      // 0. 严格过滤刻印：全星级刻印不开放展示
      const idStr = String(g.item_id)
      if (
        (g.item_id >= 400000 && g.item_id < 600000) ||
        (idStr.length === 6 && '2345'.includes(idStr[0]) && '123456'.includes(idStr[1]))
      ) {
        return false
      }
      // 1. 严格过滤角色本体（1000~1999）
      if (g.item_id >= 1000 && g.item_id < 2000) {
        return false
      }
      // 2. 搜索过滤
      if (searchQuery.trim()) {
        const q = searchQuery.trim().toLowerCase()
        const matchName = g.name.toLowerCase().includes(q)
        const matchGid = String(g.goods_id).includes(q)
        const matchIid = String(g.item_id).includes(q)
        if (!matchName && !matchGid && !matchIid) return false
      }
      // 3. 限购过滤
      if (filterLimitedOnly && g.limit_num <= 0) return false
      // 4. 售罄过滤
      if (filterSoldOutOnly && !g.is_sold_out) return false
      return true
    })
  }, [goods, searchQuery, filterLimitedOnly, filterSoldOutOnly])

  // GM 核心功能：仅刷新周期售罄商品（重置每日/每周/每月限购、每日商店与每日体力）
  const handleResetPurchase = async (targetShopId?: number) => {
    setActionLoading(true)
    const sid = targetShopId
    try {
      const res = await resetShopPurchase(activeUid, sid)
      if (res.code === 0) {
        showToast(
          res.msg ||
            (sid !== undefined
              ? `商店 #${sid} 周期限购已重置，售罄商品已全部恢复！`
              : '全服周期商品已刷新，每日商店与每日体力已恢复！'),
          'success'
        )
        await Promise.all([
          loadCatalogAndStats(),
          loadGoods(selectedShopId),
        ])
      } else {
        showToast(res.msg || '刷新周期商品失败', 'danger')
      }
    } catch (e: any) {
      showToast(`网络异常: ${e?.message || e}`, 'danger')
    } finally {
      setActionLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* 顶部 PageHeader 与专属「刷新周期商品」按钮 */}
      <PageHeader
        title="商店系统"
        description="全服 115 个官方交易区与商城货架检视、每日/每周/每月周期商品配额重置与限购管控（永久限购与换装皮肤受安全保护）。"
        actions={
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              className="gap-1.5 font-medium shadow-sm"
              isDisabled={actionLoading}
              onPress={() => handleResetPurchase(selectedShopId)}
            >
              <RotateCcw className="size-4 text-accent" />
              {actionLoading ? '正在重置...' : '刷新本商店周期商品'}
            </Button>
            <Button
              size="sm"
              className="gap-1.5 font-medium shadow-sm"
              isDisabled={actionLoading}
              onPress={() => handleResetPurchase(undefined)}
            >
              <Sparkles className="size-4 text-accent" />
              {actionLoading ? '正在刷新...' : '全服周期重置(含体力)'}
            </Button>
          </div>
        }
      />

      {/* Toast 提示横幅 */}
      {toastMsg && (
        <div
          className={cn(
            'flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs font-medium transition-all shadow-sm',
            toastMsg.tone === 'success' && 'bg-success/15 border border-success/30 text-success',
            toastMsg.tone === 'warning' && 'bg-warning/15 border border-warning/30 text-warning',
            toastMsg.tone === 'danger' && 'bg-danger/15 border border-danger/30 text-danger'
          )}
        >
          {toastMsg.tone === 'success' ? (
            <CheckCircle2 className="size-4 shrink-0" />
          ) : (
            <Ban className="size-4 shrink-0" />
          )}
          <span>{toastMsg.text}</span>
        </div>
      )}

      {/* 四大 StatCard 统计卡片 */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard
          label="在架官方商店"
          value={String(stats.total_shops || 116)}
          unit="个交易区"
          icon={Store}
          tone="accent"
        />
        <StatCard
          label="全服收录商品"
          value={String(stats.total_goods || 7250)}
          unit="项配置"
          icon={ShoppingBag}
          tone="default"
        />
        <StatCard
          label="今日每日采购刷新"
          value={`${stats.daily_refresh_times} / ${stats.daily_refresh_max}`}
          unit="次"
          icon={RotateCcw}
          tone={stats.daily_refresh_times >= 20 ? 'danger' : 'warning'}
        />
        <StatCard
          label="当前限购已售罄"
          value={String(stats.sold_out_goods_count || 0)}
          unit="项商品"
          icon={Tag}
          tone={stats.sold_out_goods_count > 0 ? 'danger' : 'success'}
        />
      </div>

      {/* 核心卡片容器：商店大类切换、子商店切换与商品检索 */}
      <Card className="p-5 space-y-5">
        {/* 1. 官方六大核心大类切换 */}
        <div className="flex flex-wrap items-center gap-2 border-b border-separator pb-3">
          {catalog.groups.map((grp) => {
            const isSelected = activeGroup === grp.id
            return (
              <button
                key={grp.id}
                onClick={() => handleGroupChange(grp.id)}
                className={cn(
                  'rounded-lg px-3.5 py-1.5 text-xs font-medium transition-colors',
                  isSelected
                    ? 'bg-accent text-accent-foreground font-semibold shadow-sm'
                    : 'text-muted hover:bg-surface-secondary hover:text-foreground'
                )}
              >
                {grp.name}
              </button>
            )
          })}
        </div>

        {/* 2. 当前大类下属子商店胶囊选择器 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs text-muted">
            <span className="font-semibold text-foreground flex items-center gap-1.5">
              <Layers className="size-3.5 text-accent" />
              当前业务区下属商店 ({shopsInActiveGroup.length})
            </span>
            {currentShopMeta && (
              <span>
                当前选中：<strong className="text-foreground">{currentShopMeta.name}</strong> (ID: {currentShopMeta.shop_id} · 共 {goods.length} 件商品)
              </span>
            )}
          </div>

          <div className="flex flex-wrap gap-2 max-h-28 overflow-y-auto cp-scroll p-1">
            {shopsInActiveGroup.map((shop) => {
              const isSelected = shop.shop_id === selectedShopId
              return (
                <button
                  key={shop.shop_id}
                  onClick={() => setSelectedShopId(shop.shop_id)}
                  className={cn(
                    'flex items-center gap-2 rounded-xl border px-3 py-1.5 text-xs font-medium transition-all',
                    isSelected
                      ? 'border-accent bg-accent/15 text-accent font-semibold shadow-sm'
                      : 'border-separator bg-surface text-muted hover:border-accent/40 hover:text-foreground'
                  )}
                >
                  <span>{shop.name}</span>
                  <span
                    className={cn(
                      'rounded-full px-1.5 py-px text-[10px] tabular-nums font-mono',
                      isSelected ? 'bg-accent text-accent-foreground' : 'bg-surface-secondary text-muted'
                    )}
                  >
                    {shop.goods_count}
                  </span>
                </button>
              )
            })}
          </div>
        </div>

        {/* 3. 搜索与控制过滤工具栏 */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-separator">
          <div className="flex flex-1 items-center gap-2 max-w-md">
            <SearchField aria-label="搜索商品" fullWidth>
              <SearchField.Group>
                <SearchField.SearchIcon />
                <SearchField.Input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="搜索商品名称 / 道具 ID / 商品 ID"
                />
                <SearchField.ClearButton onClick={() => setSearchQuery('')} />
              </SearchField.Group>
            </SearchField>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setFilterLimitedOnly((prev) => !prev)}
              className={cn(
                'flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition-colors',
                filterLimitedOnly
                  ? 'border-accent bg-accent/10 text-accent font-medium'
                  : 'border-separator text-muted hover:bg-surface-secondary'
              )}
            >
              <Filter className="size-3.5" />
              仅看有限购
            </button>

            <button
              onClick={() => setFilterSoldOutOnly((prev) => !prev)}
              className={cn(
                'flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition-colors',
                filterSoldOutOnly
                  ? 'border-danger bg-danger/10 text-danger font-medium'
                  : 'border-separator text-muted hover:bg-surface-secondary'
              )}
            >
              <Ban className="size-3.5" />
              仅看已售罄
            </button>

            <div className="flex items-center rounded-lg border border-separator p-0.5 bg-surface">
              <button
                aria-label="网格视图"
                onClick={() => setViewMode('grid')}
                className={cn(
                  'rounded-md p-1.5 transition-colors',
                  viewMode === 'grid' ? 'bg-accent text-accent-foreground shadow-sm' : 'text-muted hover:text-foreground'
                )}
              >
                <LayoutGrid className="size-3.5" />
              </button>
              <button
                aria-label="表格视图"
                onClick={() => setViewMode('table')}
                className={cn(
                  'rounded-md p-1.5 transition-colors',
                  viewMode === 'table' ? 'bg-accent text-accent-foreground shadow-sm' : 'text-muted hover:text-foreground'
                )}
              >
                <TableIcon className="size-3.5" />
              </button>
            </div>
          </div>
        </div>

        {/* 4. 货架商品展示区 */}
        {loadingGoods ? (
          <div className="py-16 text-center text-sm text-muted">
            正在拉取货架商品及已购状态...
          </div>
        ) : filteredGoods.length === 0 ? (
          <div className="py-16 text-center space-y-2">
            <ShoppingBag className="size-8 text-muted mx-auto stroke-1" />
            <div className="text-sm font-medium text-foreground">暂无符合条件的商品</div>
            <div className="text-xs text-muted">请调整搜索关键词或重置筛选条件</div>
          </div>
        ) : viewMode === 'grid' ? (
          /* 网格卡片视图 */
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3.5">
            {filteredGoods.map((item) => {
              const currencyIconUrl = CURRENCY_ICONS[item.cost_id]
                ? resolveAssetUrl(`extracted_assets/items/${CURRENCY_ICONS[item.cost_id]}`)
                : resolveAssetUrl('extracted_assets/items/1.png')

              return (
                <div
                  key={item.goods_id}
                  className={cn(
                    'group relative flex items-center gap-3.5 rounded-2xl border p-3.5 transition-all bg-surface',
                    item.is_sold_out
                      ? 'border-separator/40 opacity-75'
                      : 'border-separator hover:border-accent/40 hover:shadow-surface'
                  )}
                >
                  {/* 左侧：正版官方品质框槽位 */}
                  <ItemSlot
                    id={item.item_id}
                    name={item.name}
                    rare={item.rare}
                    iconFile={item.icon_file}
                    qualityFrame={item.quality_frame}
                    size="lg"
                    showCount={false}
                    showAddButton={false}
                  />

                  {/* 右侧：商品信息、价格与限购状态 */}
                  <div className="min-w-0 flex-1 flex flex-col justify-between h-full py-0.5">
                    <div>
                      <div className="flex items-start justify-between gap-1">
                        <span className="font-semibold text-sm truncate leading-tight" title={item.name}>
                          {item.name}
                        </span>
                        {item.discount > 0 && (
                          <span className="shrink-0 rounded bg-accent/20 px-1 py-px text-[10px] font-bold text-accent">
                            {item.discount / 10}折
                          </span>
                        )}
                      </div>
                      <div className="font-mono text-[11px] text-muted truncate mt-0.5">
                        ID: {item.goods_id} · 物资: {item.item_id}
                      </div>
                    </div>

                    {/* 售价展示 */}
                    <div className="flex items-center gap-1.5 mt-2">
                      <img
                        src={currencyIconUrl}
                        alt={item.cost_name}
                        className="size-4 object-contain shrink-0"
                        loading="lazy"
                        onError={(e) => {
                          (e.target as HTMLElement).style.display = 'none'
                        }}
                      />
                      <span className="font-mono text-sm font-semibold text-foreground tabular-nums">
                        {item.cheap_cost.toLocaleString()}
                      </span>
                      {item.cheap_cost < item.cost && (
                        <span className="font-mono text-xs text-muted line-through tabular-nums">
                          {item.cost.toLocaleString()}
                        </span>
                      )}
                      <span className="text-[11px] text-muted truncate">
                        {item.cost_name}
                      </span>
                    </div>

                    {/* 持有与库存状况 */}
                    <div className="flex items-center justify-between mt-1 text-[10px] text-muted">
                      <span>持有: <strong className="font-mono text-foreground font-medium">{(item.user_balance ?? 0).toLocaleString()}</strong></span>
                      {(item.user_item_count ?? 0) > 0 && (
                        <span>已有: <strong className="font-mono text-foreground font-medium">{item.user_item_count}</strong></span>
                      )}
                    </div>

                    {/* 限购进度状态与售罄标识 */}
                    <div className="mt-2.5 pt-2 border-t border-separator/40 flex items-center justify-between gap-2">
                      <div className="min-w-0 flex-1 text-[11px]">
                        {item.limit_num === -1 ? (
                          <span className="text-success font-medium flex items-center gap-1">
                            <Check className="size-3" />
                            不限购
                          </span>
                        ) : (
                          <div className="flex items-center gap-1 truncate">
                            <span className={item.is_sold_out ? 'text-danger font-semibold' : 'text-muted'}>
                              {item.is_sold_out ? '已售罄' : `已购 ${item.buy_times}/${item.limit_num}`}
                            </span>
                            <span className="text-[10px] text-muted font-mono">
                              ({item.refresh_cycle_label})
                            </span>
                          </div>
                        )}
                      </div>

                      {item.is_sold_out && (
                        <span className="inline-flex items-center rounded-md bg-danger/15 px-1.5 py-0.5 text-[10px] font-semibold text-danger">
                          售罄
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          /* 表格视图 */
          <div className="overflow-x-auto rounded-xl border border-separator">
            <table className="w-full text-start text-xs border-collapse">
              <thead>
                <tr className="border-b border-separator bg-surface-secondary/50 text-muted">
                  <th className="p-3 text-start font-medium">商品物资</th>
                  <th className="p-3 text-start font-medium">配置ID</th>
                  <th className="p-3 text-start font-medium">售价与代币</th>
                  <th className="p-3 text-start font-medium">限购限额</th>
                  <th className="p-3 text-start font-medium">当前已购</th>
                  <th className="p-3 text-start font-medium">刷新周期</th>
                  <th className="p-3 text-end font-medium">限购状态</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-separator">
                {filteredGoods.map((item) => {
                  const currencyIconUrl = CURRENCY_ICONS[item.cost_id]
                    ? resolveAssetUrl(`extracted_assets/items/${CURRENCY_ICONS[item.cost_id]}`)
                    : resolveAssetUrl('extracted_assets/items/1.png')

                  return (
                    <tr key={item.goods_id} className="hover:bg-surface-secondary/30 transition-colors">
                      <td className="p-3">
                        <div className="flex items-center gap-2.5">
                          <ItemSlot
                            id={item.item_id}
                            name={item.name}
                            rare={item.rare}
                            iconFile={item.icon_file}
                            qualityFrame={item.quality_frame}
                            size="sm"
                            showCount={false}
                          />
                          <div>
                            <div className="font-semibold text-foreground">{item.name}</div>
                            <div className="text-[10px] text-muted">物资 ID: {item.item_id}</div>
                          </div>
                        </div>
                      </td>
                      <td className="p-3 font-mono text-muted">{item.goods_id}</td>
                      <td className="p-3">
                        <div className="flex items-center gap-1.5">
                          <img
                            src={currencyIconUrl}
                            alt=""
                            className="size-3.5 object-contain"
                            onError={(e) => {
                              (e.target as HTMLElement).style.display = 'none'
                            }}
                          />
                          <span className="font-mono font-semibold">{item.cheap_cost}</span>
                          <span className="text-muted">{item.cost_name}</span>
                        </div>
                      </td>
                      <td className="p-3">
                        {item.limit_num === -1 ? (
                          <span className="text-success font-medium">不限购</span>
                        ) : (
                          <span className="font-mono">{item.limit_num} 件</span>
                        )}
                      </td>
                      <td className="p-3 font-mono">
                        <span className={item.is_sold_out ? 'text-danger font-semibold' : ''}>
                          {item.buy_times}
                        </span>
                      </td>
                      <td className="p-3 text-muted">{item.refresh_cycle_label}</td>
                      <td className="p-3 text-end">
                        {item.limit_num === -1 ? (
                          <span className="text-success font-medium">无限量</span>
                        ) : item.is_sold_out ? (
                          <span className="inline-flex items-center rounded-md bg-danger/15 px-2 py-0.5 text-xs font-semibold text-danger">
                            已售罄
                          </span>
                        ) : (
                          <span className="text-muted">
                            余 {item.left_num} 件
                          </span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

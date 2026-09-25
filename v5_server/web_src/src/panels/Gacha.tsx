import { useState, useEffect, useMemo } from 'react'
import {
  AlertCircle,
  CheckCircle2,
  Dices,
  KeyRound,
  Layers,
  Lock,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Swords,
  Target,
  Zap,
} from 'lucide-react'
import {
  Alert,
  Button,
  Card,
  Chip,
  Input,
  ListBox,
  SearchField,
  Select,
  Slider,
  cn,
} from '@heroui/react'
import { IconBadge, PageHeader, PlainSwitch, Section } from '../components/kit'
import { useServer } from '../lib/serverContext'
import {
  fetchGachaPools,
  toggleGachaPool,
  setGachaPity,
  applyGachaPreset,
  resolveAssetUrl,
  type GachaPoolEntry,
  type PityGroupState,
  type DrawRecordItem,
} from '../lib/api'
import expansionCatalogRaw from '../data/expansion_pools_catalog.json'
import { GACHA_HISTORY_OFFLINE_FALLBACK } from '../data/gacha_history_fallback'

// 离线静态回退目录（保障单文件离线秒开，涵盖 229 与 311 现役卡池）
const offlineCatalog: GachaPoolEntry[] = (expansionCatalogRaw as any[]).map((item) => ({
  id: item.pool_id,
  pool_id: item.pool_id,
  name: `[${item.type_label || ''}] ${item.hero_name || item.pool_name || item.pool_id}`,
  pool_name: item.pool_name || '',
  hero_name: item.hero_name || '',
  type: item.pool_group || 'hero_precision_70',
  type_label: item.type_label || '',
  enabled: [10000, 10001, 10002, 5030601, 5030301, 5020301, 5020302, 5020303, 5020601, 5000303, 4080101].includes(item.pool_id),
  active: [10000, 10001, 10002, 5030601, 5030301, 5020301, 5020302, 5020303, 5020601, 5000303, 4080101].includes(item.pool_id),
  pity_cap: item.pool_group === 'hero_precision_90' ? 90 : 70,
  activity_id: item.activity_id || 0,
  grade: item.grade || 'A',
  hero_id: item.hero_id || 0,
  order: item.order || 0,
}))

// 四大卡池分类枚举
type GachaCategory = 'precision_90' | 'precision_70' | 'standard' | 'servant'

interface CategoryMeta {
  id: GachaCategory
  seriesKey: string
  title: string
  subTag: string
  desc: string
  pityCap: number
  softPityStart: number
  softPityEnd: number
  isOpenAllocation: boolean // 是否开放个性卡池调配
  icon: typeof Dices
  tone: 'accent' | 'warning' | 'success' | 'danger'
}

const CATEGORIES: CategoryMeta[] = [
  {
    id: 'precision_90',
    seriesKey: 'hero_precision_90',
    title: '90 抽必中精准池',
    subTag: '特殊精准探测',
    desc: '90 抽大保底必出当期 UP 修正者，绝不歪卡；软保底 81~89 抽阶梯平滑爬升。',
    pityCap: 90,
    softPityStart: 81,
    softPityEnd: 89,
    isOpenAllocation: true,
    icon: Target,
    tone: 'danger',
  },
  {
    id: 'precision_70',
    seriesKey: 'hero_precision_70',
    title: '70 抽扩充探测池',
    subTag: '扩充探测',
    desc: '70 抽小保底 50% 出 UP；若歪卡触发大保底防歪锁，下次出金 100% 必出 UP。',
    pityCap: 70,
    softPityStart: 66,
    softPityEnd: 69,
    isOpenAllocation: true,
    icon: Sparkles,
    tone: 'accent',
  },
  {
    id: 'standard',
    seriesKey: 'hero_standard_70',
    title: '常驻自选角色池',
    subTag: '自选标准探测',
    desc: '客户端原生支持 33 位常驻 S 级修正者自由自选；70 抽硬保底，不歪限定。',
    pityCap: 70,
    softPityStart: 66,
    softPityEnd: 69,
    isOpenAllocation: false,
    icon: Swords,
    tone: 'warning',
  },
  {
    id: 'servant',
    seriesKey: 'weapon_servant_70',
    title: '自选钥从探测池',
    subTag: '精准钥从探测',
    desc: '客户端原生支持 6 大神系 5 星沉睡之子定向选择；4 星钥从 10 抽必中保底。',
    pityCap: 70,
    softPityStart: 66,
    softPityEnd: 69,
    isOpenAllocation: false,
    icon: KeyRound,
    tone: 'success',
  },
]

export default function GachaPanel() {
  const { activeUid, isOnline, resVersionInfo } = useServer()
  const currentVersion = resVersionInfo?.current_version || '229'
  const isVer311 = currentVersion === '311'

  // 全链路版本守卫：判定目标卡池是否属于 Build 311 独有卡池
  const isPool311Exclusive = (p: GachaPoolEntry) => {
    return Boolean(p.is_version_locked || String(p.id).startsWith('503') || p.id === 5030601 || p.id === 5030301)
  }

  // 判定当前运行环境下是否受版本锁定禁止开启
  const isPoolLocked = (p: GachaPoolEntry) => {
    return !isVer311 && isPool311Exclusive(p)
  }

  // 选中的四大卡池类型之一 (支持 localStorage 持久化记忆，杜绝刷新后分类重置导致视觉上卡池假关闭)
  const STORAGE_KEY_GACHA_CAT = 'gm_gacha_selected_cat'
  const [selectedCat, setSelectedCat] = useState<GachaCategory>(() => {
    if (typeof window !== 'undefined') {
      const hashQuery = window.location.hash.includes('?')
        ? window.location.hash.slice(window.location.hash.indexOf('?'))
        : ''
      const params = new URLSearchParams(window.location.search || hashQuery)
      const cat = params.get('gachaCat') as GachaCategory
      if (cat && ['precision_90', 'precision_70', 'standard', 'servant'].includes(cat)) {
        return cat
      }
      const saved = localStorage.getItem(STORAGE_KEY_GACHA_CAT) as GachaCategory
      if (saved && ['precision_90', 'precision_70', 'standard', 'servant'].includes(saved)) {
        return saved
      }
    }
    return 'precision_90'
  })

  const handleSelectCat = (catId: GachaCategory) => {
    setSelectedCat(catId)
    if (typeof window !== 'undefined') {
      localStorage.setItem(STORAGE_KEY_GACHA_CAT, catId)
    }
  }

  // 全量卡池列表（包含在架与未在架）
  const [pools, setPools] = useState<GachaPoolEntry[]>(offlineCatalog)
  const [loading, setLoading] = useState(false)
  const [presetLoading, setPresetLoading] = useState(false)
  const [operatingPid, setOperatingPid] = useState<number | null>(null)
  const [toastMsg, setToastMsg] = useState<{ type: 'success' | 'error' | 'warning'; text: string } | null>(null)

  // 四大保底系列真实计数状态
  const [pityStates, setPityStates] = useState<Record<string, PityGroupState>>({
    hero_precision_90: { pool_group: 'hero_precision_90', since_ssr: 12, since_sr: 3, total_draws: 82, is_up_guaranteed: 0, pity_cap: 90 },
    hero_precision_70: { pool_group: 'hero_precision_70', since_ssr: 45, since_sr: 6, total_draws: 185, is_up_guaranteed: 1, pity_cap: 70 },
    hero_standard_70: { pool_group: 'hero_standard_70', since_ssr: 28, since_sr: 2, total_draws: 128, is_up_guaranteed: 0, pity_cap: 70 },
    weapon_servant_70: { pool_group: 'weapon_servant_70', since_ssr: 0, since_sr: 0, total_draws: 40, is_up_guaranteed: 0, pity_cap: 70 },
  })

  // 抽卡历史记录（以 pool_group 分组，预置离线真实数据）
  const [historyByGroup, setHistoryByGroup] = useState<Record<string, DrawRecordItem[]>>(GACHA_HISTORY_OFFLINE_FALLBACK)

  // 垫抽微调输入框数值与防歪状态
  const [tuningPity, setTuningPity] = useState<number>(45)
  const [tuningGuaranteed, setTuningGuaranteed] = useState<boolean>(true)
  const [savingPity, setSavingPity] = useState(false)

  // 搜索与过滤状态
  const [keyword, setKeyword] = useState('')
  const [gradeFilter, setGradeFilter] = useState<'all' | 'A' | 'B' | 'C'>('all')
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all')
  const [versionFilter, setVersionFilter] = useState<'all' | '311' | '229' | 'legacy'>('all')

  // 当前分类元信息
  const curCatMeta = useMemo(() => {
    return CATEGORIES.find((c) => c.id === selectedCat) || CATEGORIES[0]
  }, [selectedCat])

  // 当前分类对应的保底状态
  const curPityState = useMemo(() => {
    return (
      pityStates[curCatMeta.seriesKey] || {
        pool_group: curCatMeta.seriesKey,
        since_ssr: 0,
        since_sr: 0,
        total_draws: 0,
        is_up_guaranteed: 0,
        pity_cap: curCatMeta.pityCap,
      }
    )
  }, [pityStates, curCatMeta])

  // 统计全局当前在架卡池总数
  const totalActivePoolsCount = useMemo(() => {
    return pools.filter((p) => p.enabled || p.active).length
  }, [pools])

  // 当前分类对应的抽卡历史记录
  const curHistoryRecords = useMemo<DrawRecordItem[]>(() => {
    return historyByGroup[curCatMeta.seriesKey] || []
  }, [historyByGroup, curCatMeta.seriesKey])

  // 统计当前分类历史的品阶分布
  const historyStats = useMemo(() => {
    let ssr = 0
    let sr = 0
    let r = 0
    for (const item of curHistoryRecords) {
      if (item.rare === 'SSR') ssr++
      else if (item.rare === 'SR') sr++
      else r++
    }
    return { ssr, sr, r, total: curHistoryRecords.length }
  }, [curHistoryRecords])

  // 切换分类时，同步垫抽微调输入框
  useEffect(() => {
    setTuningPity(curPityState.since_ssr)
    setTuningGuaranteed(curPityState.is_up_guaranteed === 1)
  }, [curCatMeta.seriesKey, curPityState.since_ssr, curPityState.is_up_guaranteed])

  // 加载服务端卡池与保底及历史信息
  const loadGachaData = async () => {
    if (!isOnline) {
      setPools(offlineCatalog)
      return
    }
    setLoading(true)
    try {
      const res = await fetchGachaPools(activeUid)
      if (res.data) {
        if (res.data.pools && res.data.pools.length > 0) {
          setPools(res.data.pools)
        }
        if (res.data.pity_states) {
          setPityStates((prev) => ({
            ...prev,
            ...res.data.pity_states,
          }))
        }
        if (res.data.history_by_group) {
          const incoming = res.data.history_by_group
          setHistoryByGroup((prev) => {
            const next = { ...prev }
            for (const [k, v] of Object.entries(incoming)) {
              if (Array.isArray(v)) {
                next[k] = v
              }
            }
            return next
          })
        }
      }
    } catch (err) {
      console.warn('[Gacha] 获取卡池信息失败:', err)
      setPools((prev) => (prev && prev.length > 0 ? prev : offlineCatalog))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadGachaData()
  }, [activeUid, isOnline])

  // 切换在架卡池状态
  const handleTogglePool = async (poolId: number, targetEnabled: boolean) => {
    // 全链路版本守卫：若当前为 229 模式，禁止启用 311 独有卡池
    if (targetEnabled) {
      const curPool = pools.find((p) => p.id === poolId)
      if (curPool && isPoolLocked(curPool)) {
        showToast('warning', '当前处于 Build 229 兼容基线模式，禁止启用 Build 311 专属卡池！')
        return
      }
    }

    // 渲染下限兜底：如果用户试图下架卡池，且当前分类下的在架卡池仅剩 1 个，予以强阻断
    if (!targetEnabled) {
      const curPool = pools.find((p) => p.id === poolId)
      const curGroup = curPool?.type || curCatMeta.seriesKey
      const activeInGroup = pools.filter(
        (p) => p.type === curGroup && (p.enabled || p.active),
      )
      if (activeInGroup.length <= 1) {
        showToast(
          'error',
          '开放卡池不可低于 1 个，客户端至少需要保留 1 个在架卡池以保障探测界面正常渲染！',
        )
        return
      }
    }

    setOperatingPid(poolId)
    // 乐观更新前端状态
    setPools((prev) =>
      prev.map((p) =>
        p.id === poolId ? { ...p, enabled: targetEnabled, active: targetEnabled } : p,
      ),
    )
    try {
      const res = await toggleGachaPool(poolId, targetEnabled)
      if (res.code === 0) {
        showToast('success', `卡池 #${poolId} 已${targetEnabled ? '成功上架' : '已下架'}并同步活动`)
      } else {
        showToast('error', res.msg || '切换卡池状态失败')
        await loadGachaData()
      }
    } catch (err: any) {
      showToast('error', `操作异常: ${err?.message || err}`)
      await loadGachaData()
    } finally {
      setOperatingPid(null)
    }
  }

  // 保存垫抽数与防歪锁
  const handleSavePity = async () => {
    setSavingPity(true)
    try {
      const targetCount = Math.max(0, Math.min(curCatMeta.pityCap, Number(tuningPity) || 0))
      const res = await setGachaPity(
        curCatMeta.seriesKey,
        targetCount,
        curCatMeta.id === 'precision_70' ? tuningGuaranteed : undefined,
        activeUid,
      )
      if (res.code === 0) {
        showToast('success', `${curCatMeta.title} 垫抽数已保存为 ${targetCount}`)
        setPityStates((prev) => ({
          ...prev,
          [curCatMeta.seriesKey]: {
            ...prev[curCatMeta.seriesKey],
            since_ssr: targetCount,
            is_up_guaranteed: tuningGuaranteed ? 1 : 0,
          },
        }))
      } else {
        showToast('error', res.msg || '保存垫抽数失败')
      }
    } catch (err: any) {
      showToast('error', `网络异常: ${err?.message || err}`)
    } finally {
      setSavingPity(false)
    }
  }

  // 应用卡池预设模板
  const handleApplyPreset = async (presetKey: string) => {
    if (!isVer311 && presetKey === 'theme44_pioneer') {
      showToast('warning', '当前处于 Build 229 兼容基线模式，禁止应用 Build 311 专属先锋卡池预设！请先在版本管理中切换至 Build 311。')
      return
    }
    setPresetLoading(true)
    try {
      const res = await applyGachaPreset(presetKey)
      if (res.code === 0) {
        showToast('success', res.msg || `已成功应用预设 [${presetKey}]`)
        await loadGachaData()
      } else {
        showToast('error', res.msg || '切换预设失败')
      }
    } catch (err: any) {
      showToast('error', `网络异常: ${err?.message || err}`)
    } finally {
      setPresetLoading(false)
    }
  }

  // Toast 提示
  const showToast = (type: 'success' | 'error' | 'warning', text: string) => {
    setToastMsg({ type, text })
    setTimeout(() => {
      setToastMsg(null)
    }, 3500)
  }

  // 当前在架所有卡池 ID 集合
  const activePoolIds = useMemo(() => {
    return pools.filter((p) => p.enabled || p.active).map((p) => p.id)
  }, [pools])

  const isPioneer311Active = activePoolIds.includes(5030601) && activePoolIds.includes(5030301)
  const isBaseline229Active = activePoolIds.includes(5020601) && activePoolIds.includes(5020301)

  // 属于当前选定分类的所有卡池
  const categoryPools = useMemo(() => {
    return pools.filter((p) => {
      if (curCatMeta.id === 'precision_90') return p.type === 'hero_precision_90'
      if (curCatMeta.id === 'precision_70') return p.type === 'hero_precision_70'
      if (curCatMeta.id === 'standard') return p.type === 'hero_standard_70'
      if (curCatMeta.id === 'servant') return p.type === 'weapon_servant_70'
      return false
    })
  }, [pools, curCatMeta.id])

  // 当前分类下的在架卡池
  const activePoolsInCat = useMemo(() => {
    return categoryPools.filter((p) => p.enabled || p.active)
  }, [categoryPools])

  // 经过关键词与多属性过滤后的卡池列表（对齐仓库检索机制）
  const filteredCatalogPools = useMemo(() => {
    const q = keyword.trim().toLowerCase()
    const words = q ? q.split(/\s+/).filter(Boolean) : []

    return categoryPools.filter((p) => {
      // 关键词切词多项检索
      if (words.length > 0) {
        const matchAll = words.every((w) => {
          const matchName = (p.name || '').toLowerCase().includes(w)
          const matchHero = (p.hero_name || '').toLowerCase().includes(w)
          const matchPool = (p.pool_name || '').toLowerCase().includes(w)
          const matchId = String(p.id).includes(w)
          return matchName || matchHero || matchPool || matchId
        })
        if (!matchAll) return false
      }

      // 版本来源过滤
      if (versionFilter === '311' && ![5030601, 5030301].includes(p.id)) return false
      if (versionFilter === '229' && ![5020301, 5020302, 5020303, 5020601].includes(p.id)) return false
      if (versionFilter === 'legacy' && [5030601, 5030301, 5020301, 5020302, 5020303, 5020601].includes(p.id)) return false

      // 安全评级过滤
      if (gradeFilter !== 'all' && p.grade !== gradeFilter) return false

      // 状态过滤
      const isActive = Boolean(p.enabled || p.active)
      if (statusFilter === 'active' && !isActive) return false
      if (statusFilter === 'inactive' && isActive) return false

      return true
    })
  }, [categoryPools, keyword, gradeFilter, statusFilter, versionFilter])

  return (
    <div className="space-y-6">
      {/* 顶部标头 */}
      <PageHeader
        title="卡池与探测调度"
        description="沙盒环境四大核心卡池体系（90抽必中、70抽扩充、常驻自选、自选钥从）调度中枢。精准监控独立保底状态机，支持实时热更上下架与抽卡历史溯源。"
        actions={
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              isIconOnly
              aria-label="刷新卡池与保底数据"
              onPress={loadGachaData}
              isDisabled={loading || presetLoading}
            >
              <RefreshCw className={cn('size-4', (loading || presetLoading) && 'animate-spin')} />
            </Button>
            <Chip size="sm" variant="soft" color="accent">
              <Chip.Label>目标账号: {activeUid}</Chip.Label>
            </Chip>
          </div>
        }
      />

      {/* 飘字提示 */}
      {toastMsg && (
        <Alert status={toastMsg.type === 'success' ? 'success' : toastMsg.type === 'warning' ? 'warning' : 'danger'}>
          <Alert.Indicator>
            {toastMsg.type === 'success' ? (
              <CheckCircle2 className="size-5" />
            ) : (
              <AlertCircle className="size-5" />
            )}
          </Alert.Indicator>
          <Alert.Content>
            <Alert.Description>{toastMsg.text}</Alert.Description>
          </Alert.Content>
        </Alert>
      )}

      {/* 双版本自适应状态感知与卡池预设调度中枢 */}
      <Section
        title="版本自适应与卡池预设方案"
        description="针对 Build 229 与 Build 311 双客户端环境调配卡池组合，支持方案切换与状态同步"
        actions={
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted">当前服务端资源:</span>
            <Chip
              size="sm"
              variant="soft"
              color={isVer311 ? 'accent' : 'success'}
            >
              <Chip.Label className="font-mono font-bold">
                Build {currentVersion}
              </Chip.Label>
            </Chip>
          </div>
        }
      >
        <Card className="p-4 space-y-3">
          {/* 版本状态与智能自适应横幅 */}
          {isVer311 && !isPioneer311Active && (
            <Alert status="warning" className="py-2.5">
              <Alert.Indicator>
                <AlertCircle className="size-4" />
              </Alert.Indicator>
              <Alert.Content>
                <Alert.Title className="text-xs font-bold">
                  检测到当前为 Build 311 资源环境，但全自选卡池未在架
                </Alert.Title>
                <Alert.Description className="text-xs">
                  311 客户端探测主界面优先展示全自选锚定（#5030601）与全自选扩充（#5030301）。可通过下方【Build 311 预设方案】或【双版本通用方案】批量上架。
                </Alert.Description>
              </Alert.Content>
            </Alert>
          )}

          {isVer311 && isPioneer311Active && (
            <Alert status="success" className="py-2">
              <Alert.Indicator>
                <CheckCircle2 className="size-4" />
              </Alert.Indicator>
              <Alert.Content>
                <Alert.Description className="text-xs">
                  <span className="font-bold">Build 311 全自选已就绪：</span>
                  已成功在架 90 抽大保底自选（#5030601）与 70 抽小保底自选（#5030301），客户端可无缝选定 26 位 S 级修正者并保底必中！
                </Alert.Description>
              </Alert.Content>
            </Alert>
          )}

          {!isVer311 && (
            <div className="text-xs text-muted flex items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-1.5">
                <ShieldCheck className="size-3.5 text-success" />
                <span>
                  当前运行于 <strong>Build 229 环境</strong>（基线卡池状态：{isBaseline229Active ? '正常在架' : '调配中'}）。开启双版本通用方案时，311 卡池受 Theme 44 边界隔离保护，229 客户端自动稳态隐藏，双端互不干扰。
                </span>
              </div>
            </div>
          )}

          {/* 主界面轮播降噪与老包体兼容性建议 */}
          {totalActivePoolsCount > 8 && (
            <Alert status="warning" className="py-2.5">
              <Alert.Indicator>
                <AlertCircle className="size-4 text-warning shrink-0" />
              </Alert.Indicator>
              <Alert.Content>
                <Alert.Title className="text-xs font-bold">主界面 Banner 轮播降噪与旧端兼容建议</Alert.Title>
                <Alert.Description className="text-xs text-muted leading-relaxed">
                  当前共有 <span className="font-bold text-foreground font-mono">{totalActivePoolsCount}</span> 个在架卡池。客户端主界面左下角广告轮播会自动读取在架卡池；
                  若运行于 <strong>229 等老版本客户端</strong>，部分历史卡池因包体内缺少对应海报图片可能会导致主界面<strong>轮播大白屏</strong>，且 311 客户端会生成较多轮播指示圆点。建议根据需求关闭非必要的历史卡池，或使用下方【Build 311 预设方案】/【Build 229 预设方案】保持界面整洁。
                </Alert.Description>
              </Alert.Content>
            </Alert>
          )}

          {/* 预设快捷操作栏 */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 pt-1">
            <button
              type="button"
              disabled={presetLoading || !isVer311}
              onClick={() => handleApplyPreset('theme44_pioneer')}
              className={cn(
                "group text-start p-3 rounded-xl border border-separator bg-surface-secondary/40 transition-all",
                !isVer311
                  ? "opacity-50 cursor-not-allowed border-dashed bg-surface-secondary/20"
                  : "hover:bg-surface hover:border-accent/50 cursor-pointer disabled:opacity-60"
              )}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-bold text-foreground group-hover:text-accent flex items-center gap-1.5">
                  <Sparkles className="size-3.5 text-accent" />
                  Build 311 预设方案
                </span>
                <Chip size="sm" variant="soft" color={isVer311 ? "accent" : "default"}>
                  <Chip.Label className="text-[10px]">{isVer311 ? "311适用" : "229锁定"}</Chip.Label>
                </Chip>
              </div>
              <div className="text-[11px] text-muted line-clamp-1">
                {isVer311 ? "全自选锚定90 + 全自选扩充70 + 常驻/钥从" : "需在版本管理中切换至 Build 311"}
              </div>
            </button>

            <button
              type="button"
              disabled={presetLoading}
              onClick={() => handleApplyPreset('all_versions_combo')}
              className="group text-start p-3 rounded-xl border border-separator bg-surface-secondary/40 hover:bg-surface hover:border-accent/50 transition-all cursor-pointer disabled:opacity-60"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-bold text-foreground group-hover:text-accent flex items-center gap-1.5">
                  <Layers className="size-3.5 text-accent" />
                  双版本通用方案
                </span>
                <Chip size="sm" variant="soft" color="accent">
                  <Chip.Label className="text-[10px]">双端兼容</Chip.Label>
                </Chip>
              </div>
              <div className="text-[11px] text-muted line-clamp-1">
                311双自选 + 229现役限定 10 卡池全开
              </div>
            </button>

            <button
              type="button"
              disabled={presetLoading}
              onClick={() => handleApplyPreset('classic_safe')}
              className="group text-start p-3 rounded-xl border border-separator bg-surface-secondary/40 hover:bg-surface hover:border-success/50 transition-all cursor-pointer disabled:opacity-60"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-bold text-foreground group-hover:text-success flex items-center gap-1.5">
                  <ShieldCheck className="size-3.5 text-success" />
                  Build 229 预设方案
                </span>
                <Chip size="sm" variant="soft" color="success">
                  <Chip.Label className="text-[10px]">229适用</Chip.Label>
                </Chip>
              </div>
              <div className="text-[11px] text-muted line-clamp-1">
                星仪90/70 + 托特70 + 诗蔻蒂70纯净基线
              </div>
            </button>

            <button
              type="button"
              disabled={presetLoading}
              onClick={() => handleApplyPreset('minimal')}
              className="group text-start p-3 rounded-xl border border-separator bg-surface-secondary/40 hover:bg-surface hover:border-separator transition-all cursor-pointer disabled:opacity-60"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-bold text-foreground flex items-center gap-1.5">
                  <Lock className="size-3.5 text-muted" />
                  最小运行集
                </span>
                <Chip size="sm" variant="soft" color="default">
                  <Chip.Label className="text-[10px]">稳态</Chip.Label>
                </Chip>
              </div>
              <div className="text-[11px] text-muted line-clamp-1">
                自选精准 + 常驻角色 + 钥从
              </div>
            </button>
          </div>
        </Card>
      </Section>

      {/* 顶部四大卡池分类导航卡 (四栏等高网格) */}
      <Section title="卡池分类中枢" description="点击切换四大卡池分类管理工作台">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {CATEGORIES.map((cat) => {
            const isSelected = selectedCat === cat.id
            const pState = pityStates[cat.seriesKey] || {
              since_ssr: 0,
              pity_cap: cat.pityCap,
              is_up_guaranteed: 0,
            }
            const actCount = pools.filter(
              (p) => p.type === cat.seriesKey && (p.enabled || p.active),
            ).length

            return (
              <button
                key={cat.id}
                type="button"
                className="block w-full text-start outline-none transition-transform active:scale-[0.99]"
                onClick={() => handleSelectCat(cat.id)}
              >
                <Card
                  className={cn(
                    'h-full p-4 transition-all duration-200 border',
                    isSelected
                      ? 'border-accent bg-surface ring-2 ring-accent shadow-md'
                      : 'border-separator bg-surface/80 hover:bg-surface hover:shadow-overlay',
                  )}
                >
                  <Card.Header className="flex-row items-start justify-between gap-2 p-0 pb-2">
                    <div className="flex items-center gap-2.5 min-w-0">
                      <IconBadge
                        icon={cat.icon}
                        tone={isSelected ? cat.tone : 'default'}
                        size="sm"
                      />
                      <div className="min-w-0">
                        <div className="text-[11px] font-mono text-muted">{cat.subTag}</div>
                        <div className="truncate text-sm font-semibold tracking-tight">
                          {cat.title}
                        </div>
                      </div>
                    </div>
                    {isSelected && (
                      <Chip size="sm" variant="soft" color="accent">
                        <Chip.Label className="text-[10px]">当前选中</Chip.Label>
                      </Chip>
                    )}
                  </Card.Header>

                  <Card.Content className="p-0 pt-2 space-y-2.5 border-t border-separator">
                    {/* 保底水位大读数 */}
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-xs text-muted">当前保底水位</span>
                      <div className="text-base font-bold tabular-nums font-mono text-foreground">
                        {pState.since_ssr}{' '}
                        <span className="text-xs font-normal text-muted">/ {cat.pityCap} 抽</span>
                      </div>
                    </div>

                    {/* 细进度条 */}
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-secondary">
                      <div
                        className={cn(
                          'h-full rounded-full transition-all duration-300',
                          pState.since_ssr >= cat.softPityStart
                            ? 'bg-warning'
                            : pState.since_ssr >= cat.pityCap
                              ? 'bg-danger'
                              : 'bg-accent',
                        )}
                        style={{
                          width: `${Math.min(100, Math.max(0, (pState.since_ssr / cat.pityCap) * 100))}%`,
                        }}
                      />
                    </div>

                    {/* 在架卡池与状态提示 */}
                    <div className="flex items-center justify-between text-[11px] text-muted pt-1">
                      <span>
                        在架卡池: <span className="font-semibold text-foreground">{actCount}</span> 个
                      </span>
                      {cat.isOpenAllocation ? (
                        <span className="text-accent font-medium">开放调配</span>
                      ) : (
                        <span className="text-muted">稳态受控</span>
                      )}
                    </div>
                  </Card.Content>
                </Card>
              </button>
            )
          })}
        </div>
      </Section>

      {/* 选定分类下的核心控制面板 (横向 1:1 等宽与纵向绝对等高) */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 lg:h-[800px] items-stretch">
        {/* 左侧 1 栏：保底状态微调 + 抽卡历史记录 (纵向等高自适应堆叠) */}
        <div className="flex flex-col gap-6 lg:h-full min-h-0">
          {/* 上半部分：保底状态微调卡片 (自适应自然内容高度，杜绝越界破框) */}
          <div className="flex-none flex flex-col">
            <Section
              title="保底状态微调"
              description={`${curCatMeta.title}独立保底状态机与动态调配`}
              actions={
                <Chip size="sm" variant="soft" color="accent">
                  <Chip.Label className="font-mono">封顶 {curCatMeta.pityCap} 抽</Chip.Label>
                </Chip>
              }
              className="flex-none flex flex-col"
            >
              <Card className="p-4 space-y-2.5 overflow-hidden">
                {/* 核心指标与垫抽进度水位 */}
                <div className="rounded-xl border border-separator bg-surface-secondary/40 p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted font-medium">垫刀进度水位</span>
                    <span className="font-mono text-xs font-bold text-accent">
                      {Math.round((curPityState.since_ssr / curCatMeta.pityCap) * 100)}%
                    </span>
                  </div>
                  <div className="flex items-baseline gap-1.5">
                    <span className="font-mono text-3xl font-extrabold tabular-nums text-foreground">
                      {curPityState.since_ssr}
                    </span>
                    <span className="font-mono text-xs text-muted">/ {curCatMeta.pityCap} 抽</span>
                  </div>

                  {/* 细进度条 */}
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-secondary">
                    <div
                      className={cn(
                        'h-full rounded-full transition-all duration-300',
                        curPityState.since_ssr >= curCatMeta.softPityStart
                          ? 'bg-warning'
                          : curPityState.since_ssr >= curCatMeta.pityCap
                            ? 'bg-danger'
                            : 'bg-accent',
                      )}
                      style={{
                        width: `${Math.min(100, Math.max(0, (curPityState.since_ssr / curCatMeta.pityCap) * 100))}%`,
                      }}
                    />
                  </div>

                  {/* 软保底说明 */}
                  <div className="text-[11px] leading-relaxed text-muted pt-0.5">
                    软保底区间为{' '}
                    <span className="font-semibold text-foreground font-mono">
                      {curCatMeta.softPityStart} ~ {curCatMeta.softPityEnd}
                    </span>{' '}
                    抽阶梯爬升。官方基准出率 1.60%，大数综合出金率收敛至 2.40%。
                  </div>

                  {/* 70 扩充池专属：大保底触发状态显示 */}
                  {curCatMeta.id === 'precision_70' && (
                    <div className="flex items-center justify-between pt-1.5 border-t border-separator text-xs">
                      <span className="text-muted flex items-center gap-1">
                        <ShieldAlert className="size-3.5 text-warning shrink-0" />
                        大保底状态
                      </span>
                      <span
                        className={cn(
                          'inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium font-mono',
                          curPityState.is_up_guaranteed === 1
                            ? 'bg-accent/15 text-accent border border-accent/30 font-semibold'
                            : 'bg-surface-secondary text-muted',
                        )}
                      >
                        {curPityState.is_up_guaranteed === 1 ? (
                          <>
                            <CheckCircle2 className="size-3 text-accent" />
                            已触发大保底 (下次出金 100% 必出 UP)
                          </>
                        ) : (
                          <>
                            <AlertCircle className="size-3 text-muted" />
                            未触发大保底 (当前 50/50 概率)
                          </>
                        )}
                      </span>
                    </div>
                  )}
                </div>

                {/* 抽数微调：可修改文本 + 滑块联动调节 */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-semibold text-foreground">
                      设置垫抽数 (0 ~ {curCatMeta.pityCap})
                    </span>
                    <span className="font-mono text-xs text-muted">
                      调节预览: <strong className="text-accent">{tuningPity}</strong> 抽
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <Slider
                      aria-label="垫抽数滑块"
                      value={tuningPity}
                      onChange={(v) => {
                        const val = typeof v === 'number' ? v : v[0]
                        setTuningPity(val)
                      }}
                      minValue={0}
                      maxValue={curCatMeta.pityCap}
                      step={1}
                      className="flex-1"
                    >
                      <Slider.Track className="h-2 rounded-full bg-surface-secondary">
                        <Slider.Fill className="bg-accent rounded-full" />
                        <Slider.Thumb className="size-4.5 rounded-full bg-accent border-2 border-surface shadow-xs ring-2 ring-accent/20 cursor-pointer focus:outline-none" />
                      </Slider.Track>
                    </Slider>

                    <Input
                      type="number"
                      min={0}
                      max={curCatMeta.pityCap}
                      value={String(tuningPity)}
                      onChange={(e) => {
                        const val = Math.max(0, Math.min(curCatMeta.pityCap, Number(e.target.value) || 0))
                        setTuningPity(val)
                      }}
                      className="w-20 font-mono font-bold text-center shrink-0"
                    />

                    <Button
                      size="sm"
                      variant="primary"
                      onPress={handleSavePity}
                      isDisabled={savingPity}
                      className="shrink-0 font-medium"
                    >
                      {savingPity ? '保存中...' : '保存落库'}
                    </Button>
                  </div>
                </div>

                {/* 70 扩充池专属：大保底防歪锁定开关 */}
                {curCatMeta.id === 'precision_70' && (
                  <div className="rounded-xl border border-separator p-2.5 space-y-1 bg-surface">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2 min-w-0">
                        <IconBadge
                          icon={tuningGuaranteed ? ShieldCheck : Lock}
                          tone={tuningGuaranteed ? 'accent' : 'default'}
                          size="sm"
                        />
                        <div>
                          <div className="text-xs font-semibold">大保底防歪锁定</div>
                          <div className="text-[11px] text-muted">
                            开启后跳过 50/50 概率，下次出金 100% 必出当期 UP 修正者
                          </div>
                        </div>
                      </div>
                      <PlainSwitch
                        isSelected={tuningGuaranteed}
                        label="大保底防歪锁定"
                        onChange={setTuningGuaranteed}
                      />
                    </div>
                  </div>
                )}

                {/* 90 抽必中池专属提示 */}
                {curCatMeta.id === 'precision_90' && (
                  <div className="rounded-xl border border-separator p-2.5 text-[11px] text-muted space-y-1">
                    <div className="font-semibold text-foreground flex items-center gap-1.5">
                      <CheckCircle2 className="size-3.5 text-accent" /> 90 抽大保底绝对防歪
                    </div>
                    <div>该卡池出金 100% 必定命中当期 UP 修正者，规则上不存在歪卡逻辑。</div>
                    {activePoolIds.includes(5030601) && (
                      <div className="text-secondary font-medium pt-1 border-t border-separator/60">
                        ★ 当前已在架 311 全自选锚定探测池（#5030601），在 311 客户端内自由自选目标，垫抽数严格计入 hero_precision_90 序列，绝不污染常驻池！
                      </div>
                    )}
                  </div>
                )}

                {/* 70 扩充池专属 311 提示 */}
                {curCatMeta.id === 'precision_70' && activePoolIds.includes(5030301) && (
                  <div className="rounded-xl border border-secondary/30 bg-secondary/5 p-2.5 text-[11px] text-muted space-y-1">
                    <div className="font-semibold text-secondary flex items-center gap-1.5">
                      <Sparkles className="size-3.5 text-secondary" /> 311 全自选扩充探测在架中
                    </div>
                    <div>已在架全自选扩充探测池（#5030301），支持 26 位 S 级修正者自由选定，抽数与防歪锁与当期限定 70 池严格共享。</div>
                  </div>
                )}

                {/* 常驻与钥从专属自选提示 */}
                {!curCatMeta.isOpenAllocation && (
                  <div className="rounded-xl border border-separator p-2.5 text-[11px] text-muted space-y-1">
                    <div className="font-semibold text-foreground flex items-center gap-1.5">
                      <ShieldCheck className="size-3.5 text-success" /> 官方原生自由自选
                    </div>
                    <div>常驻与钥从池已由游戏客户端原生完整支持自由自选，真机运行 100% 零报错。</div>
                  </div>
                )}
              </Card>
            </Section>
          </div>

          {/* 下半部分：抽卡历史记录卡片 (与上方微调卡片同高 1:1，内部畅快滚动) */}
          <div className="flex-1 min-h-0 flex flex-col">
            <Section
              title="抽卡历史记录"
              description={`${curCatMeta.title}探测明细（最近最多 100 条）`}
              actions={
                <Chip size="sm" variant="soft" color="default">
                  <Chip.Label className="font-mono">
                    记录: {curHistoryRecords.length} 条
                  </Chip.Label>
                </Chip>
              }
              className="flex-1 min-h-0 flex flex-col h-full"
            >
              <Card className="flex-1 min-h-0 h-full flex flex-col p-4 space-y-2.5">
                {/* 顶部统计摘要条 */}
                <div className="flex items-center justify-between text-xs text-muted border-b border-separator pb-2 shrink-0">
                  <div className="flex items-center gap-2.5">
                    <span className="inline-flex items-center gap-1 text-[11px] text-amber-500 font-semibold font-mono">
                      <span className="size-2 rounded-full bg-amber-500" />
                      SSR: {historyStats.ssr}
                    </span>
                    <span className="inline-flex items-center gap-1 text-[11px] text-purple-400 font-semibold font-mono">
                      <span className="size-2 rounded-full bg-purple-400" />
                      SR: {historyStats.sr}
                    </span>
                    <span className="inline-flex items-center gap-1 text-[11px] text-blue-400 font-semibold font-mono">
                      <span className="size-2 rounded-full bg-blue-400" />
                      R: {historyStats.r}
                    </span>
                  </div>
                  <span className="text-[11px] font-mono text-muted">上限 100 条</span>
                </div>

                {/* 滚动列表 */}
                <div className="flex-1 min-h-0 overflow-y-auto space-y-1.5 pr-1">
                  {curHistoryRecords.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center py-12 text-center text-sm text-muted">
                      <Dices className="size-8 opacity-40 mb-2" />
                      <div>暂无该卡池抽卡历史记录</div>
                      <div className="text-[11px] opacity-70 mt-1">在游戏内探测抽取后将自动同步至此</div>
                    </div>
                  ) : (
                    curHistoryRecords.map((rec) => {
                      const isSSR = rec.rare === 'SSR'
                      const isSR = rec.rare === 'SR'
                      const isServant =
                        String(rec.item_id).startsWith('23') ||
                        String(rec.item_id).startsWith('24') ||
                        String(rec.item_id).startsWith('25') ||
                        (Boolean(rec.img) && rec.img.includes('servants'))

                      // 仓库/展示槽位边框与底色
                      const slotToneClass = isSSR
                        ? 'border-amber-500/60 bg-gradient-to-b from-amber-500/20 via-amber-500/5 to-transparent ring-1 ring-amber-500/30'
                        : isSR
                          ? 'border-purple-500/50 bg-gradient-to-b from-purple-500/20 via-purple-500/5 to-transparent ring-1 ring-purple-500/25'
                          : 'border-blue-500/40 bg-gradient-to-b from-blue-500/15 via-blue-500/5 to-transparent'

                      return (
                        <div
                          key={rec.id}
                          className={cn(
                            'flex items-center justify-between gap-3 p-2 rounded-xl border transition-colors',
                            isSSR
                              ? 'border-amber-500/30 bg-amber-500/5 shadow-xs'
                              : isSR
                                ? 'border-purple-500/20 bg-purple-500/5'
                                : 'border-separator bg-surface hover:bg-surface-secondary/30',
                          )}
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            {/* 仓库/背包展示槽位小图标 */}
                            <div
                              className={cn(
                                'size-10 rounded-lg shrink-0 overflow-hidden border relative flex items-center justify-center shadow-xs bg-surface-secondary/50',
                                slotToneClass,
                              )}
                            >
                              <img
                                src={resolveAssetUrl(rec.img)}
                                alt={rec.item_name}
                                className={cn(
                                  'size-full drop-shadow-sm transition-transform duration-200',
                                  isServant ? 'object-contain p-0.5' : 'object-cover',
                                )}
                                onError={(e) => {
                                  // 智能兜底：钥从回退到官方3星小图标，角色回退到官方角色头像
                                  (e.target as HTMLImageElement).src = resolveAssetUrl(
                                    isServant
                                      ? 'extracted_assets/servants/icons/2310001.png'
                                      : 'extracted_assets/avatars/1084.png',
                                  )
                                }}
                              />
                            </div>
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5">
                                <span className="text-xs font-semibold text-foreground truncate">
                                  {rec.item_name}
                                </span>
                                {isServant && (
                                  <span className="text-[10px] text-muted-foreground/80 px-1 py-0.2 rounded bg-surface-secondary border border-separator/50 shrink-0 font-sans scale-90 origin-left">
                                    钥从
                                  </span>
                                )}
                              </div>
                              <div className="text-[10px] text-muted font-mono">
                                {rec.draw_time || '时间未记录'}
                              </div>
                            </div>
                          </div>

                          {/* 品阶 Badge */}
                          <div className="shrink-0 flex items-center gap-1.5">
                            {isSSR && (
                              <span className="px-1.5 py-0.5 rounded text-[10px] font-bold font-mono bg-amber-500/15 text-amber-500 border border-amber-500/30 shadow-xs">
                                SSR
                              </span>
                            )}
                            {isSR && (
                              <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold font-mono bg-purple-500/15 text-purple-400 border border-purple-500/30">
                                SR
                              </span>
                            )}
                            {!isSSR && !isSR && (
                              <span className="px-1.5 py-0.5 rounded text-[10px] font-medium font-mono bg-blue-500/10 text-blue-400 border border-blue-500/20">
                                R
                              </span>
                            )}
                          </div>
                        </div>
                      )
                    })
                  )}
                </div>
              </Card>
            </Section>
          </div>
        </div>

        {/* 右侧 1 栏：开放卡池配置（90精准/70扩充）或稳态机制（常驻/钥从） */}
        <div className="flex flex-col lg:h-full min-h-0">
          {curCatMeta.isOpenAllocation ? (
            <Section
              title="开放卡池配置"
              description={`调度 ${curCatMeta.title} 在架卡池，支持实时热更上下架（至少保留 1 个）`}
              actions={
                <div className="flex items-center gap-2">
                  <Chip
                    size="sm"
                    variant="soft"
                    color={activePoolsInCat.length > 0 ? 'accent' : 'danger'}
                  >
                    <Chip.Label>本类在架: {activePoolsInCat.length} 个</Chip.Label>
                  </Chip>
                  <Chip
                    size="sm"
                    variant="soft"
                    color={totalActivePoolsCount > 8 ? 'warning' : 'success'}
                  >
                    <Chip.Label>全服总在架: {totalActivePoolsCount} 个</Chip.Label>
                  </Chip>
                  <Chip size="sm" variant="soft" color="default">
                    <Chip.Label>编目: {categoryPools.length} 个</Chip.Label>
                  </Chip>
                </div>
              }
              className="flex-1 min-h-0 flex flex-col h-full"
            >
              <Card className="flex-1 min-h-0 h-full flex flex-col p-4 space-y-4">
                {/* 检索工具条：精简 placeholder，改用 Select 下拉选择 */}
                <div className="flex flex-wrap items-center gap-2.5 shrink-0">
                  <SearchField
                    aria-label="搜索角色或卡池名"
                    className="flex-1 min-w-[180px]"
                    value={keyword}
                    onChange={setKeyword}
                  >
                    <SearchField.Group>
                      <SearchField.SearchIcon>
                        <Search className="size-4" />
                      </SearchField.SearchIcon>
                      <SearchField.Input placeholder="搜索角色或卡池名..." />
                      <SearchField.ClearButton />
                    </SearchField.Group>
                  </SearchField>

                  {/* 安全评级 Select 下拉 */}
                  <Select
                    aria-label="按安全评级筛选"
                    className="w-[125px]"
                    selectedKey={gradeFilter}
                    onSelectionChange={(key) => setGradeFilter(String(key) as any)}
                  >
                    <Select.Trigger>
                      <Select.Value />
                      <Select.Indicator />
                    </Select.Trigger>
                    <Select.Popover>
                      <ListBox>
                        <ListBox.Item id="all">全部评级</ListBox.Item>
                        <ListBox.Item id="A">Grade A</ListBox.Item>
                        <ListBox.Item id="B">Grade B</ListBox.Item>
                        <ListBox.Item id="C">Grade C</ListBox.Item>
                      </ListBox>
                    </Select.Popover>
                  </Select>

                  {/* 上架状态 Select 下拉 */}
                  <Select
                    aria-label="按在架状态筛选"
                    className="w-[115px]"
                    selectedKey={statusFilter}
                    onSelectionChange={(key) => setStatusFilter(String(key) as any)}
                  >
                    <Select.Trigger>
                      <Select.Value />
                      <Select.Indicator />
                    </Select.Trigger>
                    <Select.Popover>
                      <ListBox>
                        <ListBox.Item id="all">全部状态</ListBox.Item>
                        <ListBox.Item id="active">仅在架</ListBox.Item>
                        <ListBox.Item id="inactive">仅未在架</ListBox.Item>
                      </ListBox>
                    </Select.Popover>
                  </Select>

                  {/* 版本来源 Select 下拉 */}
                  <Select
                    aria-label="按版本来源筛选"
                    className="w-[125px]"
                    selectedKey={versionFilter}
                    onSelectionChange={(key) => setVersionFilter(String(key) as any)}
                  >
                    <Select.Trigger>
                      <Select.Value />
                      <Select.Indicator />
                    </Select.Trigger>
                    <Select.Popover>
                      <ListBox>
                        <ListBox.Item id="all">全部版本</ListBox.Item>
                        <ListBox.Item id="311">Build 311 卡池</ListBox.Item>
                        <ListBox.Item id="229">Build 229 卡池</ListBox.Item>
                        <ListBox.Item id="legacy">历史旧池</ListBox.Item>
                      </ListBox>
                    </Select.Popover>
                  </Select>

                  {/* 快捷清空重置 */}
                  {(keyword || gradeFilter !== 'all' || statusFilter !== 'all' || versionFilter !== 'all') && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-xs text-muted hover:text-foreground h-8 px-2"
                      onPress={() => {
                        setKeyword('')
                        setGradeFilter('all')
                        setStatusFilter('all')
                        setVersionFilter('all')
                      }}
                    >
                      重置
                    </Button>
                  )}
                </div>

                {/* 检索读数 */}
                <div className="flex items-center justify-between text-xs text-muted px-0.5 flex-wrap gap-2 shrink-0">
                  <span>
                    合法编目{' '}
                    <span className="font-semibold text-foreground font-mono">
                      {categoryPools.length}
                    </span>{' '}
                    个 · 命中{' '}
                    <span className="font-semibold text-foreground font-mono">
                      {filteredCatalogPools.length}
                    </span>{' '}
                    个
                  </span>
                  <span className="text-[11px] text-muted">
                    至少保留 1 个在架卡池
                  </span>
                </div>

                {/* 卡池列表容器 (自适应撑满高度，内部滚动) */}
                <div className="flex-1 min-h-0 overflow-y-auto space-y-2 pr-1">
                  {filteredCatalogPools.length === 0 ? (
                    <div className="p-8 text-center text-sm text-muted">未找到匹配的卡池条目</div>
                  ) : (
                    filteredCatalogPools.map((p) => {
                      const isLockedByVersion = isPoolLocked(p)
                      const isAct = isLockedByVersion ? false : Boolean(p.enabled || p.active)
                      const isBusy = operatingPid === p.id
                      const is311Selectable = p.id === 5030601 || p.id === 5030301

                      return (
                        <div
                          key={p.id}
                          className={cn(
                            'flex items-center justify-between gap-3 p-3 rounded-xl border transition-colors',
                            isLockedByVersion
                              ? 'border-separator/40 bg-surface-secondary/20 opacity-60'
                              : is311Selectable && isAct
                                ? 'border-secondary/40 bg-secondary/5 shadow-xs ring-1 ring-secondary/20'
                                : isAct
                                  ? 'border-accent/30 bg-accent/5 shadow-xs'
                                  : 'border-separator bg-surface hover:bg-surface-secondary/30',
                          )}
                        >
                          <div className="min-w-0 flex items-center gap-3">
                            {/* 头像替代编号 */}
                            {p.hero_id && p.hero_id > 0 ? (
                              <img
                                src={resolveAssetUrl(`extracted_assets/avatars/${p.hero_id}.png`)}
                                alt={p.hero_name}
                                className="size-9 rounded-full object-cover border border-separator shadow-xs shrink-0 bg-surface-secondary"
                                onError={(e) => {
                                  (e.target as HTMLImageElement).src = resolveAssetUrl(
                                    'extracted_assets/avatars/1053.png',
                                  )
                                }}
                              />
                            ) : is311Selectable ? (
                              <div className="size-9 rounded-full bg-secondary/15 border border-secondary/30 flex items-center justify-center shrink-0 text-secondary shadow-xs">
                                <Sparkles className="size-4" />
                              </div>
                            ) : (
                              <div className="size-9 rounded-full bg-accent/10 border border-accent/20 flex items-center justify-center shrink-0 text-accent">
                                <Sparkles className="size-4" />
                              </div>
                            )}

                            <div className="min-w-0">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="text-sm font-semibold text-foreground truncate">
                                  {p.hero_name || p.pool_name}
                                </span>
                                {is311Selectable && (
                                  <Chip size="sm" variant="soft" color={isLockedByVersion ? "default" : "accent"}>
                                    <Chip.Label className="text-[10px] font-bold">Build 311 · 全自选</Chip.Label>
                                  </Chip>
                                )}
                                <Chip size="sm" variant="soft" color="default">
                                  <Chip.Label className="text-[10px]">{p.type_label}</Chip.Label>
                                </Chip>
                                {p.grade === 'A' && (
                                  <span className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-success font-mono">
                                    <ShieldCheck className="size-3" /> Grade A
                                  </span>
                                )}
                                {p.grade === 'B' && (
                                  <span className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-accent font-mono">
                                    Grade B
                                  </span>
                                )}
                                {p.grade === 'C' && (
                                  <span className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-warning font-mono">
                                    <ShieldAlert className="size-3" /> Grade C
                                  </span>
                                )}
                              </div>
                              {p.pool_name && (
                                <div className="text-xs text-muted truncate font-mono mt-0.5 flex items-center gap-1">
                                  <span>{p.pool_name}</span>
                                  {Boolean(p.activity_id && p.activity_id > 0) && (
                                    <>
                                      <span>·</span>
                                      <span className="inline-flex items-center gap-0.5 text-warning font-sans">
                                        <Zap className="size-3" /> 活动: {p.activity_id}
                                      </span>
                                    </>
                                  )}
                                </div>
                              )}
                              {is311Selectable && (
                                <div className={cn("text-[11px] font-medium mt-0.5 flex items-center gap-1", isLockedByVersion ? "text-muted" : "text-accent")}>
                                  <Sparkles className="size-3" />
                                  <span>
                                    {isLockedByVersion
                                      ? 'Build 311 专属全自选卡池 · 当前 Build 229 基线下已锁定禁发'
                                      : '支持 26 位 S 级修正者全自选 · 独立保底序列'}
                                  </span>
                                </div>
                              )}
                            </div>
                          </div>

                          {/* 启停上下架 Switch 开关 */}
                          <div className="shrink-0 flex items-center gap-2.5">
                            {isLockedByVersion ? (
                              <Chip size="sm" variant="soft" color="default">
                                <Chip.Label className="text-[10px] text-muted font-medium">229锁定 (需311)</Chip.Label>
                              </Chip>
                            ) : isAct ? (
                              <Chip size="sm" variant="soft" color={is311Selectable ? 'accent' : 'success'}>
                                <Chip.Label className="text-[10px] font-semibold">在架中</Chip.Label>
                              </Chip>
                            ) : (
                              <Chip size="sm" variant="soft" color="default">
                                <Chip.Label className="text-[10px] text-muted">未开放</Chip.Label>
                              </Chip>
                            )}
                            <PlainSwitch
                              isSelected={isAct}
                              label={isLockedByVersion ? `卡池 ${p.id} (229模式锁定禁用)` : `开启卡池 ${p.id}`}
                              isDisabled={isBusy || isLockedByVersion}
                              onChange={(val) => handleTogglePool(p.id, val)}
                            />
                          </div>
                        </div>
                      )
                    })
                  )}
                </div>
              </Card>
            </Section>
          ) : (
            /* 常驻与钥从稳态说明卡片同样填充 h-full */
            <Section
              title="稳态体系运行机制"
              description="官方常态化自选机制保障"
              className="flex-1 min-h-0 flex flex-col h-full"
            >
              <Card className="flex-1 min-h-0 h-full flex flex-col p-5 space-y-4 overflow-y-auto">
                <div className="flex items-start gap-3">
                  <IconBadge icon={ShieldCheck} tone="success" size="md" />
                  <div className="space-y-1">
                    <div className="text-sm font-semibold">自选体系已在游戏内全量接管</div>
                    <div className="text-xs text-muted leading-relaxed">
                      对于{curCatMeta.title}
                      ，游戏客户端原生提供了完备的自选面板交互界面。玩家进入探测主界面后，可随时切换
                      33 位常驻老 S 角色或 6 大神系沉睡之子，无需通过管理面板频繁调换卡池 ID。
                    </div>
                  </div>
                </div>

                <div className="rounded-xl border border-separator p-4 space-y-2.5 bg-surface-secondary/30">
                  <div className="text-xs font-semibold text-foreground">
                    为什么此分类无需调配历史卡池？
                  </div>
                  <ul className="text-xs text-muted space-y-1.5 list-disc list-inside leading-relaxed">
                    <li>
                      官方在游戏版本迭代中，已将早期单卡池的专属 UI Prefab
                      逐步剔除或收敛至通用自选容器；
                    </li>
                    <li>
                      下发不存在专属 Prefab 的历史旧卡池可能导致客户端在切换卡池切页时抛出空引用异常；
                    </li>
                    <li>
                      保持现役官方推荐稳态在架集合（4080101 自选精准、10001 常规、10000
                      新人，及 10002 钥从），既能满足全角色/全钥从定向获取，又能保证 100% 稳定运行。
                    </li>
                  </ul>
                </div>
              </Card>
            </Section>
          )}
        </div>
      </div>
    </div>
  )
}

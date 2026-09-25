import { useState, useEffect, useMemo } from 'react'
import {
  Users,
  Heart,
  Star,
  Award,
  ChevronRight,
  AlertCircle,
  ArrowUpAZ,
  ArrowDownZA,
  Search,
  SlidersHorizontal,
  Sparkles,
  Shield,
  ShieldOff,
  Info,
  Zap,
  Cpu,
  CheckCircle2,
  X,
  Mail,
  Plus,
  Maximize2,
  BookOpen,
} from 'lucide-react'
import {
  Avatar,
  Button,
  Card,
  Chip,
  SearchField,
} from '@heroui/react'
import { PageHeader, StatCard } from '../components/kit'
import { useServer } from '../lib/serverContext'
import {
  fetchHeroesList,
  fetchHeroDetail,
  resolveAssetUrl,
  type HeroEntry,
  type HeroStatsData,
  type HeroFullDetail,
} from '../lib/api'
import heroesCatalog from '../data/heroesCatalog.json'
import heroDetailsCatalog from '../data/heroDetailsCatalog.json'
import weaponServantsCatalog from '../data/weaponServantsCatalog.json'

interface CatalogItem {
  id: number
  name: string
  title: string
  official_name: string
  pinyin_initial: string
  full_pinyin_initials?: string
  race_id: number
  race_name: string
  race_icon: string
  element_id: number
  element_name: string
  element_icon: string
  rare: number
  unlock_star: number
  avatar: string
}

// 官方自机 84 位修正者离线预置元数据（严格对齐官方 Catalog，杜绝幻觉）
const defaultHeroesList: HeroEntry[] = (heroesCatalog as unknown as CatalogItem[]).map((cat) => ({
  id: cat.id,
  name: cat.name,
  title: cat.title,
  official_name: cat.official_name,
  custom_name: '',
  display_name: cat.official_name,
  pinyin_initial: cat.pinyin_initial || '#',
  full_pinyin_initials: cat.full_pinyin_initials || '',
  race_id: cat.race_id,
  race_name: cat.race_name,
  race_icon: cat.race_icon,
  element_id: cat.element_id,
  element_name: cat.element_name,
  element_icon: cat.element_icon,
  star: cat.unlock_star || 200,
  grade_name: cat.rare === 3 ? 'S' : cat.rare === 2 ? 'A' : 'B',
  level: 1,
  unlocked: false,
  is_favorite: false,
  is_oath: false,
  oath_level: 0,
  avatar: cat.avatar,
}))

type FilterType = 'all' | 'unlocked' | 'oath' | 'favorite'
type SortField = 'pinyin' | 'level' | 'star'
type SortOrder = 'asc' | 'desc'

// 6 大神系 5 星沉睡之子钥从定义（品质 5 星金色，官方物品图标与立绘齐全）
const RACE_SLEEPING_SERVANT: Record<number, { id: number; name: string; race_name: string; desc: string }> = {
  1: { id: 2510000, name: '星使·沉睡之子', race_name: '奥山', desc: '通过唤醒沉睡之子的真名，可以令其觉醒为奥山神系的指定一位五星专属钥从。' },
  2: { id: 2520000, name: '列王·沉睡之子', race_name: '尼罗', desc: '通过唤醒沉睡之子的真名，可以令其觉醒为尼罗神系的指定一位五星专属钥从。' },
  3: { id: 2530000, name: '式神·沉睡之子', race_name: '真樱', desc: '通过唤醒沉睡之子的真名，可以令其觉醒为真樱神系的指定一位五星专属钥从。' },
  4: { id: 2540000, name: '妖精·沉睡之子', race_name: '圣树', desc: '通过唤醒沉睡之子的真名，可以令其觉醒为圣树神系的指定一位五星专属钥从。' },
  5: { id: 2550000, name: '命者·沉睡之子', race_name: '众星', desc: '通过唤醒沉睡之子的真名，可以令其觉醒为众星神系的指定一位五星专属钥从。' },
  9: { id: 2590000, name: '方异·沉睡之子', race_name: '天垣', desc: '通过唤醒沉睡之子的真名，可以令其觉醒为天垣神系的指定一位五星专属钥从。' },
}

const SLOT_ROMAN_NUMERALS = ['Ⅰ', 'Ⅱ', 'Ⅲ', 'Ⅳ', 'Ⅴ', 'Ⅵ']

function getEquipStarStyle(star: number) {
  if (star >= 5) {
    return {
      border: 'border-amber-400',
      bg: 'bg-amber-500/10',
      badge: 'bg-amber-500 text-black',
      activeRing: 'border-warning bg-warning/10 ring-1 ring-warning/40 shadow-xs',
      hover: 'hover:border-warning/40 hover:bg-surface-secondary/40',
      tagColor: 'warning' as const,
    }
  }
  if (star === 4) {
    return {
      border: 'border-purple-400',
      bg: 'bg-purple-500/10',
      badge: 'bg-purple-500 text-white',
      activeRing: 'border-purple-400 bg-purple-500/10 ring-1 ring-purple-400/40 shadow-xs',
      hover: 'hover:border-purple-400/40 hover:bg-surface-secondary/40',
      tagColor: 'accent' as const,
    }
  }
  if (star === 3) {
    return {
      border: 'border-blue-400',
      bg: 'bg-blue-500/10',
      badge: 'bg-blue-500 text-white',
      activeRing: 'border-blue-400 bg-blue-500/10 ring-1 ring-blue-400/40 shadow-xs',
      hover: 'hover:border-blue-400/40 hover:bg-surface-secondary/40',
      tagColor: 'default' as const,
    }
  }
  return {
    border: 'border-emerald-400',
    bg: 'bg-emerald-500/10',
    badge: 'bg-emerald-500 text-white',
    activeRing: 'border-emerald-400 bg-emerald-500/10 ring-1 ring-emerald-400/40 shadow-xs',
    hover: 'hover:border-emerald-400/40 hover:bg-surface-secondary/40',
    tagColor: 'success' as const,
  }
}

const STORAGE_KEY_MAIL_DRAFT = 'gm_mail_draft_attachments'

function addAttachmentToMailDraft(item: {
  id: number
  name: string
  rare: number
  icon_file: string
  quality_frame: string
  count: number
}): boolean {
  if (typeof window === 'undefined') return false
  try {
    const raw = localStorage.getItem(STORAGE_KEY_MAIL_DRAFT)
    let draft: any[] = []
    if (raw) {
      try {
        const parsed = JSON.parse(raw)
        if (Array.isArray(parsed)) draft = parsed
      } catch {
        draft = []
      }
    }
    const idx = draft.findIndex((d) => d.id === item.id)
    if (idx >= 0) {
      draft[idx].count = (Number(draft[idx].count) || 0) + item.count
      draft[idx].selected = true
    } else {
      draft.push({
        id: item.id,
        name: item.name,
        rare: item.rare,
        icon_file: item.icon_file,
        quality_frame: item.quality_frame,
        count: item.count,
        selected: true,
      })
    }
    localStorage.setItem(STORAGE_KEY_MAIL_DRAFT, JSON.stringify(draft))
    window.dispatchEvent(new Event('mail-draft-updated'))
    window.dispatchEvent(new Event('storage'))
    return true
  } catch (e) {
    console.error('快捷追加邮件附件失败:', e)
    return false
  }
}

export default function HeroesPanel() {
  const { activeUid, isOnline } = useServer()
  const [stats, setStats] = useState<HeroStatsData | null>(null)
  const [heroes, setHeroes] = useState<HeroEntry[]>(defaultHeroesList)
  const [selectedHeroId, setSelectedHeroId] = useState<number | null>(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search)
      const h = params.get('hero')
      if (h && Number(h)) {
        return Number(h)
      }
    }
    return null
  })
  const [activeSubTab, setActiveSubTab] = useState(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search)
      const sub = params.get('sub')
      if (sub && ['base', 'skills', 'servant', 'equip', 'transition', 'chips', 'oath', 'astrolabe'].includes(sub)) {
        return sub
      }
    }
    return 'base'
  })
  const [detail, setDetail] = useState<HeroFullDetail | null>(null)
  const [, setDetailLoading] = useState(false)
  const [selectedEquipSlot, setSelectedEquipSlot] = useState<number>(1)
  const [selectedTransitionSlot, setSelectedTransitionSlot] = useState<number>(1)

  // 钥从立绘高精大图鉴赏弹窗
  const [previewServant, setPreviewServant] = useState<{
    name: string
    portrait: string
    star?: number
    desc?: string
    race_name?: string
  } | null>(null)

  // 快捷追加附件即时反馈
  const [shortcutFeedback, setShortcutFeedback] = useState<string | null>(null)

  // 监听 ESC 键快速关闭大图鉴赏
  useEffect(() => {
    if (!previewServant) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPreviewServant(null)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [previewServant])

  // 筛选与排序状态
  const [keyword, setKeyword] = useState('')
  const [filterType, setFilterType] = useState<FilterType>('all')
  const [sortField, setSortField] = useState<SortField>('pinyin')
  const [sortOrder, setSortOrder] = useState<SortOrder>('asc')

  useEffect(() => {
    let mounted = true
    if (!isOnline) {
      setStats(null)
      setHeroes(defaultHeroesList)
      return
    }

    fetchHeroesList(activeUid)
      .then((res) => {
        if (!mounted) return
        if (res.data) {
          if (res.data.heroes && res.data.heroes.length > 0) {
            setHeroes(res.data.heroes)
          }
          if (res.data.stats) {
            setStats(res.data.stats)
          }
        }
      })
      .catch((err) => {
        console.warn('[Heroes] 获取角色状态失败 (服务器或Service未就绪):', err)
        if (mounted) {
          setStats(null)
          setHeroes(defaultHeroesList)
        }
      })
    return () => {
      mounted = false
    }
  }, [activeUid, isOnline])

  // 过滤与排序计算
  const filteredHeroes = useMemo(() => {
    let list = heroes.slice()

    // 1. 快捷分类过滤
    if (filterType === 'unlocked') {
      list = list.filter((h) => h.unlocked)
    } else if (filterType === 'oath') {
      list = list.filter((h) => h.is_oath)
    } else if (filterType === 'favorite') {
      list = list.filter((h) => h.is_favorite)
    }

    // 2. 关键字搜索 (支持官方名、自定义爱称、称号、拼音首字母、全拼缩写、ID、属性、神系)
    const kw = keyword.trim().toLowerCase()
    if (kw) {
      list = list.filter((h) => {
        const matchId = String(h.id).includes(kw)
        const matchOfficial = h.official_name.toLowerCase().includes(kw)
        const matchCustom = (h.custom_name || '').toLowerCase().includes(kw)
        const matchName = h.name.toLowerCase().includes(kw)
        const matchTitle = h.title.toLowerCase().includes(kw)
        const matchPinyin = h.pinyin_initial.toLowerCase().includes(kw)
        const matchFullPinyin = (h.full_pinyin_initials || '').toLowerCase().includes(kw)
        const matchRace = h.race_name.toLowerCase().includes(kw)
        const matchElement = h.element_name.toLowerCase().includes(kw)
        return (
          matchId ||
          matchOfficial ||
          matchCustom ||
          matchName ||
          matchTitle ||
          matchPinyin ||
          matchFullPinyin ||
          matchRace ||
          matchElement
        )
      })
    }

    // 3. 多维度排序
    list.sort((a, b) => {
      let diff = 0
      if (sortField === 'level') {
        diff = a.level - b.level
      } else if (sortField === 'star') {
        diff = a.star - b.star
      } else {
        // 默认按拼音首字母
        diff = a.pinyin_initial.localeCompare(b.pinyin_initial, 'zh-CN')
      }

      if (diff === 0) {
        diff = a.id - b.id
      }

      return sortOrder === 'asc' ? diff : -diff
    })

    return list
  }, [heroes, filterType, keyword, sortField, sortOrder])

  // 当前选中的角色：优先匹配当前列表中与 selectedHeroId 吻合的项；
  // 若未手动选中（或选中角色被当前筛选/搜索过滤排除），则默认选中当前排序/筛选列表的最靠前者 (filteredHeroes[0])
  const selectedHero: HeroEntry = useMemo(() => {
    if (selectedHeroId) {
      const foundInFiltered = filteredHeroes.find((h) => h.id === selectedHeroId)
      if (foundInFiltered) return foundInFiltered
      const foundInAll = heroes.find((h) => h.id === selectedHeroId)
      if (foundInAll) return foundInAll
    }
    return filteredHeroes[0] || heroes[0] || defaultHeroesList[0]
  }, [filteredHeroes, heroes, selectedHeroId])

  // 选定角色的初始品阶 (解包 Catalog 中的 rare: 3->S, 2->A, 1->B)
  const initialGrade = useMemo(() => {
    const cat = (heroesCatalog as any[]).find((c) => c.id === selectedHero.id)
    if (cat?.rare === 3) return 'S'
    if (cat?.rare === 2) return 'A'
    if (cat?.rare === 1) return 'B'
    return 'S'
  }, [selectedHero.id])

  // 选定角色的当前品阶与小等阶 (从 hero.star 中动态解析 100~600，如 401->SS 超越一阶)
  const currentStarInfo = useMemo(() => {
    const star = selectedHero.star || 300
    const starLevel = Math.floor(star / 100) // 1=B, 2=A, 3=S, 4=SS, 5=SSS, 6=Ω
    const phase = star % 100 // 0=基础阶, 1=超越一阶, 2=超越二阶, 3=超越三阶, 4=超越四阶
    const safeLevel = Math.min(Math.max(starLevel, 1), 6)

    const gradeNames: Record<number, string> = {
      1: 'B',
      2: 'A',
      3: 'S',
      4: 'SS',
      5: 'SSS',
      6: 'Ω',
    }
    const gradeName = gradeNames[safeLevel] || 'S'
    const iconPath = `extracted_assets/items/grades/star_${safeLevel}.png`

    let phaseText = ''
    if (!selectedHero.unlocked) {
      phaseText = '未招募'
    } else if (safeLevel === 6) {
      phaseText = '超越巅峰'
    } else if (phase === 0) {
      phaseText = '基础阶'
    } else {
      const phaseNums = ['', '一', '二', '三', '四']
      phaseText = `超越${phaseNums[phase] || phase}阶`
    }

    return {
      safeLevel,
      phase,
      gradeName,
      iconPath,
      phaseText,
    }
  }, [selectedHero.star, selectedHero.unlocked])

  // 离线/服务未就绪时的兜底全量元数据生成
  const fallbackDetail = useMemo<HeroFullDetail>(() => {
    const catHeroes = (heroDetailsCatalog as any).heroes || {}
    const catalogHero = catHeroes[String(selectedHero.id)] || {}

    // 技能组
    const skills = (catalogHero.skills || []).map((s: any, idx: number) => {
      const isDodge = s.type === '闪避' || idx === 5
      return {
        id: s.id,
        name: s.name,
        type: s.type,
        icon: s.icon,
        level: 1,
        add_level: 0,
        total_level: 1,
        intensify: 0,
        is_dodge: isDodge,
      }
    })

    // 武装钥从 (离线状态默认未装配，保留官方推荐专属钥从元数据)
    const catalogServant = catalogHero.servant || null
    const servant = {
      id: 0,
      name: '未装载',
      portrait: '',
      icon: '',
      stage: 0,
      equipped: false,
      is_universal: false,
      star: 5,
      type: 0,
      race_id: 0,
      race_name: '',
      desc: '',
      effect_desc: '',
      exclusive_servant: catalogServant ? {
        id: catalogServant.id,
        name: catalogServant.name,
        portrait: catalogServant.portrait,
        stage: catalogServant.stage || 5,
      } : undefined,
    }

    // 刻印 6 槽位
    const equipPrefabs = (heroDetailsCatalog as any).equip_prefabs || {}
    const slots = [1, 2, 3, 4, 5, 6].map((pos) => {
      const prefab = Object.values(equipPrefabs).find((p: any) => p.pos === pos) as any
      return {
        pos,
        equipped: true,
        name: prefab ? prefab.suit_name : `槽位 ${pos}`,
        suit_name: prefab ? prefab.suit_name : '圣女的战旗',
        level: 60,
        star: 5,
        icon: prefab ? prefab.icon : `extracted_assets/equips/suit_1.png`,
        enchants: [
          { id: 1, name: '强攻', icon: 'extracted_assets/equipskills/icon_id1.png', level: 3 },
          { id: 2, name: '狂怒', icon: 'extracted_assets/equipskills/icon_id2.png', level: 3 },
        ],
      }
    })

    // 跃迁 6 槽位（采用游戏真实跃迁专属因子与技能：处刑者、审判官、演化颗粒、破甲兵装等）
    const slotNames = ['槽位一', '槽位二', '槽位三', '槽位四', '槽位五', '槽位六']
    const defaultTransitionConfigs = [
      {
        skills: [
          { id: 101, name: '强化因子·近战', icon: 'extracted_assets/equipskills/icon_id101.png', level: 3 },
          { id: 124, name: '审判官', icon: 'extracted_assets/equipskills/icon_id124.png', level: 3 },
        ],
      },
      {
        skills: [
          { id: 101, name: '强化因子·近战', icon: 'extracted_assets/equipskills/icon_id101.png', level: 3 },
          { id: 125, name: '处刑者', icon: 'extracted_assets/equipskills/icon_id125.png', level: 3 },
        ],
      },
      {
        skills: [
          { id: 103, name: '突变磁场', icon: 'extracted_assets/equipskills/icon_id103.png', level: 3 },
          { id: 121, name: '唤灵力场Ⅰ', icon: 'extracted_assets/equipskills/icon_id121.png', level: 3 },
        ],
      },
      {
        skills: [
          { id: 112, name: '演化颗粒Ⅲ', icon: 'extracted_assets/equipskills/icon_id112.png', level: 3 },
          { id: 113, name: '念动矢量Ⅲ', icon: 'extracted_assets/equipskills/icon_id113.png', level: 3 },
        ],
      },
      {
        skills: [
          { id: 126, name: '破甲兵装', icon: 'extracted_assets/equipskills/icon_id126.png', level: 3 },
          { id: 127, name: '以太眩击', icon: 'extracted_assets/equipskills/icon_id127.png', level: 3 },
        ],
      },
      {
        skills: [
          { id: 115, name: '演化颗粒Ⅳ', icon: 'extracted_assets/equipskills/icon_id115.png', level: 3 },
          { id: 116, name: '念动矢量Ⅳ', icon: 'extracted_assets/equipskills/icon_id116.png', level: 3 },
        ],
      },
    ]

    const transitions = [1, 2, 3, 4, 5, 6].map((slot_id) => {
      const cfg = defaultTransitionConfigs[slot_id - 1]
      const total_level = cfg.skills.reduce((sum, s) => sum + s.level, 0)
      return {
        slot_id,
        slot_name: slotNames[slot_id - 1],
        total_level,
        icon: cfg.skills[0]?.icon || '',
        skills: cfg.skills,
      }
    })

    // 芯片 (适配非固定槽位，默认 4 槽位)
    const chips = [
      {
        slot_id: 1,
        slot_name: '槽位 1',
        equipped: true,
        id: 101,
        name: '突击强化芯片',
        desc: '战术芯片，强化修正者突击效能与战术协调。',
        role_type_name: '基础模块',
        icon: 'extracted_assets/chips/101.png',
      },
      {
        slot_id: 2,
        slot_name: '槽位 2',
        equipped: true,
        id: 102,
        name: '连携运作芯片',
        desc: '战术芯片，提升修正者连携反应与奥义积累。',
        role_type_name: '连携',
        icon: 'extracted_assets/chips/102.png',
      },
      {
        slot_id: 3,
        slot_name: '槽位 3',
        equipped: false,
        id: 0,
        name: '未装配芯片',
        desc: '',
        role_type_name: '',
        icon: '',
      },
      {
        slot_id: 4,
        slot_name: '槽位 4',
        equipped: false,
        id: 0,
        name: '未装配芯片',
        desc: '',
        role_type_name: '',
        icon: '',
      },
    ]

    // 神格
    const allAstrolabes = catalogHero.astrolabes || []
    const activeAstrolabes = allAstrolabes.slice(0, 3)

    return {
      hero_id: selectedHero.id,
      name: selectedHero.official_name,
      portrait: `extracted_assets/portraits/${selectedHero.id}.png`,
      base: {
        level: selectedHero.level || 80,
        star: selectedHero.star || 300,
        grade_name: currentStarInfo.gradeName,
        phase: currentStarInfo.phase,
        phase_text: currentStarInfo.phaseText,
        weapon_level: 60,
        weapon_break: 4,
        module_level: catalogHero.module_supported ? 3 : 0,
        module_supported: catalogHero.module_supported ?? false,
        unlocked: selectedHero.unlocked,
      },
      servant,
      skills,
      equips: {
        slots,
        active_suits: selectedHero.star >= 600 ? ['官方推荐刻印 (Ω 2件套)'] : ['官方推荐刻印 (3件套)'],
        is_omega: selectedHero.star >= 600,
        suit_need: selectedHero.star >= 600 ? 2 : 3,
      },
      transitions,
      chips,
      astrolabe: {
        active: activeAstrolabes,
        all_nodes: allAstrolabes,
      },
      oath: {
        is_oath: selectedHero.is_oath,
        nick: selectedHero.custom_name || '',
        oath_level: selectedHero.is_oath ? (selectedHero.oath_level || 1) : 0,
        oath_time: 0,
        plot_progress: 4,
        plot_max: 5,
        like_level: 5,
        like_roman: 'Ⅴ',
        like_title: '好感度五级',
        like_exp: 400,
        like_exp_max: 400,
        like_total_exp: 1000,
        has_trust: false,
        trust_level: 0,
        trust_title: '未开放',
        trust_exp: 0,
        trust_exp_max: 0,
        trust_mood: 2,
        mood_name: '平静',
        mood_buff: '+10%',
      },
    }
  }, [selectedHero, currentStarInfo])

  // 查询单体修正者的全量养成数据
  useEffect(() => {
    let mounted = true
    if (!isOnline) {
      setDetail(null)
      return
    }

    setDetailLoading(true)
    fetchHeroDetail(selectedHero.id, activeUid)
      .then((res) => {
        if (!mounted) return
        if (
          res.data &&
          res.data.hero_id === selectedHero.id &&
          res.data.base &&
          res.data.equips?.slots
        ) {
          setDetail(res.data)
        } else {
          setDetail(null)
        }
      })
      .catch((err) => {
        console.warn('[Heroes] 获取修正者深度详情失败，使用离线元数据兜底:', err)
        if (mounted) setDetail(null)
      })
      .finally(() => {
        if (mounted) setDetailLoading(false)
      })

    return () => {
      mounted = false
    }
  }, [selectedHero.id, activeUid, isOnline])

  const currentDetail = useMemo(() => {
    if (
      detail &&
      detail.hero_id === selectedHero.id &&
      detail.base &&
      detail.equips?.slots &&
      detail.transitions &&
      detail.skills &&
      detail.servant &&
      detail.astrolabe &&
      detail.oath
    ) {
      return detail
    }
    return fallbackDetail
  }, [detail, selectedHero.id, fallbackDetail])

  // 选中的刻印槽位数据
  const selectedEquipData = useMemo(() => {
    const slots = currentDetail?.equips?.slots || fallbackDetail.equips.slots
    return (
      slots.find((s) => s.pos === selectedEquipSlot) ||
      slots[0] ||
      fallbackDetail.equips.slots[0]
    )
  }, [currentDetail, selectedEquipSlot, fallbackDetail])

  // 选中的跃迁槽位数据
  const selectedTransitionData = useMemo(() => {
    const transitions = currentDetail?.transitions || fallbackDetail.transitions
    return (
      transitions.find((t) => t.slot_id === selectedTransitionSlot) ||
      transitions[0] ||
      fallbackDetail.transitions[0]
    )
  }, [currentDetail, selectedTransitionSlot, fallbackDetail])

  const isReady = isOnline && stats !== null

  return (
    <div className="space-y-6">
      <PageHeader
        title="角色状态"
        description="修正者全图鉴名册、神系阵营分布、誓约羁绊与基础状态监控。"
      />

      {/* 服务未启动提示 */}
      {!isOnline && (
        <div className="flex items-center gap-2.5 rounded-xl border border-warning/30 bg-warning-soft/30 px-4 py-3 text-xs text-warning-soft-foreground">
          <AlertCircle className="size-4 shrink-0 text-warning" />
          <span className="font-medium">
            当前服务器暂未启动：控制台向下委派核心 Service 处理；请启动服务端以读取实时修正者状态。
          </span>
        </div>
      )}

      {/* 顶部四大核心状态统计面板 */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard
          label="角色数量"
          value={isReady ? String(stats.owned_count) : '当前服务器暂未启动'}
          unit={isReady ? `/ ${stats.total_official}` : undefined}
          icon={Users}
          tone={isReady ? 'accent' : 'default'}
          delta={!isReady ? '未启动' : stats.owned_count >= stats.total_official ? '全图鉴已满' : undefined}
          deltaTone={!isReady ? 'accent' : 'success'}
          deltaNote={!isReady ? '待核心Service挂载' : undefined}
        />
        <StatCard
          label="誓约角色数量"
          value={isReady ? String(stats.oath_count) : '当前服务器暂未启动'}
          unit={isReady ? '位' : undefined}
          icon={Heart}
          tone={isReady ? 'danger' : 'default'}
          delta={!isReady ? '未启动' : stats.oath_count > 0 ? `${stats.oath_count} 结缔` : '暂无誓约'}
          deltaTone={!isReady ? 'accent' : stats.oath_count > 0 ? 'danger' : 'default'}
          deltaNote={!isReady ? '待oath_service挂载' : undefined}
        />
        <StatCard
          label="收藏角色数量"
          value={isReady ? String(stats.favorite_count) : '当前服务器暂未启动'}
          unit={isReady ? '位' : undefined}
          icon={Star}
          tone={isReady ? 'warning' : 'default'}
          delta={!isReady ? '未启动' : stats.favorite_count > 0 ? `${stats.favorite_count} 特别关注` : undefined}
          deltaTone={!isReady ? 'accent' : 'warning'}
          deltaNote={!isReady ? '待hero_service挂载' : undefined}
        />
        <StatCard
          label="欧米茄角色数量"
          value={isReady ? String(stats.omega_count) : '当前服务器暂未启动'}
          unit={isReady ? '位' : undefined}
          icon={Award}
          tone={isReady ? 'success' : 'default'}
          delta={!isReady ? '未启动' : stats.omega_count > 0 ? `${stats.omega_count} 顶格巅峰` : undefined}
          deltaTone={!isReady ? 'accent' : 'success'}
          deltaNote={!isReady ? '待突破模组就绪' : undefined}
        />
      </div>

      {/* 主体两栏布局：左侧修正者长名册列表，右侧角色配置联动 */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* 左侧：搜索、筛选、排序与长滚动名册卡片列表 */}
        <div className="space-y-3 lg:col-span-5 xl:col-span-4">
          {/* 搜索与多维度筛选排序控制卡 */}
          <Card className="p-3.5 space-y-3 shadow-xs">
            {/* 搜索框 */}
            <SearchField aria-label="搜索修正者" className="w-full">
              <SearchField.Group>
                <SearchField.SearchIcon />
                <SearchField.Input
                  placeholder="搜索角色名 / 称号 / 拼音 / ID / 属性"
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                />
                <SearchField.ClearButton />
              </SearchField.Group>
            </SearchField>

            {/* 快捷过滤分类 */}
            <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5 text-xs">
              {[
                { id: 'all', label: `全部 (${heroes.length})` },
                { id: 'unlocked', label: `已拥有 (${stats?.owned_count ?? heroes.filter((h) => h.unlocked).length})` },
                { id: 'oath', label: `誓约 (${stats?.oath_count ?? heroes.filter((h) => h.is_oath).length})` },
                { id: 'favorite', label: `收藏 (${stats?.favorite_count ?? heroes.filter((h) => h.is_favorite).length})` },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setFilterType(tab.id as FilterType)}
                  className={`rounded-lg px-2.5 py-1 text-xs font-medium whitespace-nowrap transition-colors cursor-pointer ${
                    filterType === tab.id
                      ? 'bg-accent text-accent-foreground font-semibold shadow-xs'
                      : 'bg-surface-secondary/70 text-muted hover:bg-surface-secondary hover:text-foreground'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* 排序维度与升降序切换 */}
            <div className="flex items-center justify-between gap-2 pt-2 border-t border-separator/40 text-xs">
              <div className="flex items-center gap-1 text-muted">
                <SlidersHorizontal className="size-3.5 shrink-0" />
                <span className="text-[11px] shrink-0">排序:</span>
                {(['pinyin', 'level', 'star'] as const).map((field) => {
                  const labels = { pinyin: '首字母', level: '等级', star: '品阶' }
                  const active = sortField === field
                  return (
                    <button
                      key={field}
                      onClick={() => setSortField(field)}
                      className={`rounded px-1.5 py-0.5 text-xs transition-colors cursor-pointer ${
                        active
                          ? 'bg-accent/20 text-accent font-semibold'
                          : 'text-muted hover:text-foreground'
                      }`}
                    >
                      {labels[field]}
                    </button>
                  )
                })}
              </div>

              <Button
                size="sm"
                variant="outline"
                className="h-6.5 px-2 text-xs gap-1 border-separator/60"
                onClick={() => setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'))}
              >
                {sortOrder === 'asc' ? (
                  <>
                    <ArrowUpAZ className="size-3.5 text-accent" />
                    <span>升序</span>
                  </>
                ) : (
                  <>
                    <ArrowDownZA className="size-3.5 text-accent" />
                    <span>降序</span>
                  </>
                )}
              </Button>
            </div>
          </Card>

          {/* 修正者长滚动卡片列表 */}
          <div className="space-y-2 max-h-[510px] overflow-y-auto pr-1">
            {filteredHeroes.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-center text-muted border border-dashed border-separator rounded-xl">
                <Search className="size-8 opacity-40 mb-2" />
                <p className="text-sm font-medium">未找到匹配的修正者</p>
                <p className="text-xs text-muted mt-1">请尝试更换搜索关键字或重置分类筛选</p>
              </div>
            ) : (
              filteredHeroes.map((hero) => {
                const isSelected = selectedHero.id === hero.id
                return (
                  <div
                    key={hero.id}
                    onClick={() => setSelectedHeroId(hero.id)}
                    className={`group relative flex cursor-pointer items-center justify-between rounded-xl border p-2.5 transition-all ${
                      isSelected
                        ? 'border-accent bg-accent/10 shadow-sm ring-1 ring-accent/30'
                        : 'border-separator bg-surface hover:border-accent/40 hover:bg-surface-secondary/30'
                    } ${!hero.unlocked ? 'opacity-70 hover:opacity-100' : ''}`}
                  >
                    {/* 左侧头像与微章指示点 */}
                    <div className="flex items-center gap-2.5 min-w-0">
                      <div className="relative shrink-0">
                        <Avatar size="md" color="accent" className="size-11 ring-1 ring-separator/60">
                          <Avatar.Image src={resolveAssetUrl(hero.avatar)} />
                          <Avatar.Fallback>{hero.name.slice(0, 1)}</Avatar.Fallback>
                        </Avatar>
                        {hero.is_oath && (
                          <span
                            className="absolute -bottom-1 -right-1 flex size-4 items-center justify-center rounded-full bg-rose-500 shadow-xs ring-1 ring-background"
                            title={`已誓约 Lv.${hero.oath_level || 1}`}
                          >
                            <Heart className="size-2.5 fill-white text-white" />
                          </span>
                        )}
                        {hero.is_favorite && !hero.is_oath && (
                          <span
                            className="absolute -bottom-1 -right-1 flex size-4 items-center justify-center rounded-full bg-amber-500 shadow-xs ring-1 ring-background"
                            title="特别关注"
                          >
                            <Star className="size-2.5 fill-white text-white" />
                          </span>
                        )}
                      </div>

                      {/* 中间角色名（支持誓约自定义爱称）、属性图标与等级/品阶 */}
                      <div className="min-w-0">
                        {/* 第一行：名字与官方属性图标 */}
                        <div className="flex items-center gap-1.5 min-w-0">
                          {hero.is_oath && hero.custom_name ? (
                            <div className="flex items-center gap-1 min-w-0 truncate">
                              <span className="font-bold text-sm text-rose-500 truncate">
                                {hero.custom_name}
                              </span>
                              <span className="text-[11px] text-muted truncate">
                                ({hero.official_name})
                              </span>
                            </div>
                          ) : (
                            <span className="font-semibold text-sm text-foreground truncate">
                              {hero.official_name}
                            </span>
                          )}

                          {/* 官方元素属性图标 */}
                          <img
                            src={resolveAssetUrl(hero.element_icon)}
                            alt={hero.element_name}
                            title={`${hero.element_name}属性`}
                            className="size-4 shrink-0 object-contain drop-shadow-xs"
                            onError={(e) => {
                              ;(e.target as HTMLElement).style.display = 'none'
                            }}
                          />
                        </div>

                        {/* 第二行：品阶与星级（战力未入库，从卡片彻底剔除） */}
                        <div className="flex items-center gap-1.5 mt-1 text-xs">
                          {hero.unlocked ? (
                            <>
                              <span className="text-muted font-medium">Lv.{hero.level}</span>
                              <span
                                className={`rounded px-1.5 py-0.2 text-[10px] font-bold font-mono ${
                                  hero.grade_name === 'Ω'
                                    ? 'bg-success/15 text-success border border-success/30'
                                    : hero.grade_name === 'SSS'
                                    ? 'bg-amber-500/15 text-amber-500'
                                    : hero.grade_name === 'SS'
                                    ? 'bg-purple-500/15 text-purple-400'
                                    : 'bg-surface-secondary text-muted'
                                }`}
                              >
                                {hero.grade_name}
                              </span>
                            </>
                          ) : (
                            <span className="text-[11px] text-muted">
                              未招募 · 初始 {hero.grade_name}
                            </span>
                          )}
                          {hero.is_favorite && hero.is_oath && (
                            <span title="特别关注">
                              <Star className="size-3 fill-amber-400 text-amber-400 shrink-0" />
                            </span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* 卡片右侧：使用官方神系图标徽章填补大块留白 + 选中态箭头 */}
                    <div className="flex items-center gap-2 shrink-0 pl-2">
                      <div
                        className="flex size-9 items-center justify-center rounded-lg bg-zinc-800 border border-zinc-700/60 p-1.5 shadow-xs transition-transform group-hover:scale-105"
                        title={`${hero.race_name}神系`}
                      >
                        <img
                          src={resolveAssetUrl(hero.race_icon)}
                          alt={hero.race_name}
                          className="size-full object-contain opacity-90 group-hover:opacity-100 transition-opacity drop-shadow-xs"
                          onError={(e) => {
                            ;(e.target as HTMLElement).style.display = 'none'
                          }}
                        />
                      </div>
                      <ChevronRight
                        className={`size-4 text-muted transition-transform ${
                          isSelected ? 'text-accent translate-x-0.5' : ''
                        }`}
                      />
                    </div>
                  </div>
                )
              })
            )}
          </div>

          {/* 列表底部条目计数 */}
          <div className="flex items-center justify-between px-1 text-[11px] text-muted">
            <span>
              已显示 {filteredHeroes.length} / {heroes.length} 位修正者
            </span>
            {keyword && (
              <button
                onClick={() => setKeyword('')}
                className="text-accent hover:underline cursor-pointer"
              >
                清空搜索
              </button>
            )}
          </div>
        </div>

        {/* 右侧：选中角色的详细整备页签 */}
        <div className="space-y-4 lg:col-span-7 xl:col-span-8">
          <Card className="p-5 shadow-xs">
            {/* 头部：修正者身份看板、官方属性与神系徽章、当前品阶与超越阶级 */}
            <div className="flex items-center justify-between gap-4 border-b border-separator pb-4">
              <div className="flex items-center gap-4 min-w-0">
                {/* 角色标准圆形头像 */}
                <div className="relative size-16 shrink-0 rounded-full overflow-hidden ring-2 ring-separator/50 shadow-xs bg-surface-secondary">
                  <img
                    src={resolveAssetUrl(selectedHero.avatar)}
                    alt={selectedHero.name}
                    className="size-full object-cover rounded-full"
                    onError={(e) => {
                      ;(e.target as HTMLElement).style.display = 'none'
                    }}
                  />
                  {selectedHero.is_oath && (
                    <span
                      className="absolute bottom-0 right-0 flex size-4.5 items-center justify-center rounded-full bg-rose-500 ring-2 ring-surface shadow-xs"
                      title={`已誓约 Lv.${selectedHero.oath_level || 1}`}
                    >
                      <Heart className="size-2.5 fill-white text-white" />
                    </span>
                  )}
                </div>

                {/* 纵向间距自然的双行文本架构 */}
                <div className="flex flex-col justify-center gap-1.5 min-w-0">
                  {/* 第一行：角色名 + 初始品阶（去ID、去掉“品阶”二字，仅显示 S / A / B） */}
                  <div className="flex items-center gap-2.5 min-w-0">
                    {selectedHero.is_oath && selectedHero.custom_name ? (
                      <div className="flex items-center gap-1.5 min-w-0 truncate">
                        <h2 className="text-xl font-bold text-rose-500 truncate tracking-tight">
                          {selectedHero.custom_name}
                        </h2>
                        <span className="text-xs text-muted font-normal truncate">
                          ({selectedHero.official_name})
                        </span>
                        <Chip
                          size="sm"
                          variant="soft"
                          color="danger"
                          className="h-5 px-1.5 gap-1 text-[11px]"
                        >
                          <Heart className="size-2.5 fill-rose-500 text-rose-500" />
                          <Chip.Label>誓约</Chip.Label>
                        </Chip>
                      </div>
                    ) : (
                      <h2 className="text-xl font-bold text-foreground truncate tracking-tight">
                        {selectedHero.official_name}
                      </h2>
                    )}

                    {/* 初始品阶标签：去掉了“品阶”，仅显示 S / A / B */}
                    <Chip
                      size="sm"
                      variant="soft"
                      color="warning"
                      className="h-5 min-w-5 px-1.5 text-[11px] font-bold font-mono"
                    >
                      <Chip.Label>{initialGrade}</Chip.Label>
                    </Chip>

                    {selectedHero.is_favorite && (
                      <Chip
                        size="sm"
                        variant="soft"
                        color="warning"
                        className="h-5 px-1.5 gap-1 text-[11px]"
                      >
                        <Star className="size-2.5 fill-amber-400 text-amber-400" />
                        <Chip.Label>特别关注</Chip.Label>
                      </Chip>
                    )}
                  </div>

                  {/* 第二行：神系徽章 + 属性徽章，精简文本为“圣树”、“暗”，移除“称号” */}
                  <div className="flex items-center gap-2 text-xs">
                    {/* 神系徽章 */}
                    <div className="flex items-center gap-1.5 rounded-md bg-zinc-800 px-2 py-0.5 text-[11px] font-medium text-zinc-100 border border-zinc-700/60 shadow-2xs">
                      <img
                        src={resolveAssetUrl(selectedHero.race_icon)}
                        alt={selectedHero.race_name}
                        className="size-3.5 object-contain"
                      />
                      <span>{selectedHero.race_name}</span>
                    </div>

                    {/* 元素属性徽章 */}
                    <div className="flex items-center gap-1.5 rounded-md bg-surface-secondary px-2 py-0.5 text-[11px] font-medium text-foreground border border-separator/60">
                      <img
                        src={resolveAssetUrl(selectedHero.element_icon)}
                        alt={selectedHero.element_name}
                        className="size-3.5 object-contain"
                      />
                      <span>{selectedHero.element_name}</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* 右上角：当前角色品阶图标 + 小等阶文本（如超越一阶 / 未招募） */}
              <div className="flex flex-col items-center justify-center shrink-0 pl-4 min-w-[64px]">
                <div className="flex items-center justify-center h-9 px-1">
                  <img
                    src={resolveAssetUrl(currentStarInfo.iconPath)}
                    alt={currentStarInfo.gradeName}
                    className="h-8.5 object-contain drop-shadow-xs"
                    onError={(e) => {
                      ;(e.target as HTMLElement).style.display = 'none'
                    }}
                  />
                </div>
                <span className="text-[11px] font-medium text-muted mt-0.5 whitespace-nowrap">
                  {currentStarInfo.phaseText}
                </span>
              </div>
            </div>

            {/* 子页签切换：8 大核心系统专属子面板 */}
            <div className="mt-4">
              <div className="flex gap-1.5 border-b border-separator pb-2 text-sm overflow-x-auto">
                {[
                  { id: 'base', label: '基础' },
                  { id: 'skills', label: '技能' },
                  { id: 'servant', label: '钥从' },
                  { id: 'equip', label: '刻印' },
                  { id: 'transition', label: '跃迁' },
                  { id: 'chips', label: '芯片' },
                  { id: 'oath', label: '心链誓约' },
                  { id: 'astrolabe', label: '神格' },
                ].map((t) => (
                  <button
                    key={t.id}
                    onClick={() => setActiveSubTab(t.id)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors whitespace-nowrap cursor-pointer ${
                      activeSubTab === t.id
                        ? 'bg-accent text-accent-foreground font-semibold shadow-2xs'
                        : 'text-muted hover:bg-surface-secondary'
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {/* 子面板内容渲染 */}
              <div className="mt-4">
                {/* 1. 基础面板：横向两栏，左立绘右数值 */}
                {activeSubTab === 'base' && (
                  <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-start">
                    {/* 左侧角色全身立绘 */}
                    <div className="md:col-span-5 relative flex h-[410px] w-full items-center justify-center overflow-hidden rounded-xl border border-separator/50 bg-gradient-to-b from-surface-secondary/40 via-surface/15 to-surface-secondary/50 p-2 shadow-inner">
                      <img
                        src={resolveAssetUrl(currentDetail.portrait || `extracted_assets/portraits/${selectedHero.id}.png`)}
                        alt={selectedHero.official_name}
                        className="max-h-[380px] w-auto max-w-full object-contain filter drop-shadow-md select-none transition-transform duration-300 hover:scale-105"
                        onError={(e) => {
                          ;(e.target as HTMLImageElement).src = resolveAssetUrl(selectedHero.avatar)
                        }}
                      />
                      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-surface/80 to-transparent" />
                      <span className="absolute bottom-2 left-3 text-[11px] font-medium text-muted/70 tracking-wide font-mono">
                        NO.{selectedHero.id} · {selectedHero.official_name}
                      </span>
                    </div>

                    {/* 右侧数值详情 */}
                    <div className="md:col-span-7 space-y-3">
                      <div className="rounded-xl border border-separator p-3.5 space-y-2.5 bg-surface">
                        <h3 className="text-xs font-bold text-foreground flex items-center justify-between">
                          <span>修正者品阶与等阶</span>
                          <span className="font-mono text-muted">★ {currentDetail.base.star}</span>
                        </h3>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted">当前品阶</span>
                          <div className="flex items-center gap-1.5">
                            <span className="font-mono font-bold text-accent text-sm">
                              {currentStarInfo.gradeName}
                            </span>
                            <span className="text-muted">({currentStarInfo.phaseText})</span>
                          </div>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted">招募状态</span>
                          <span className="font-medium">
                            {currentDetail.base.unlocked ? (
                              <span className="text-success font-semibold">已招募同步</span>
                            ) : (
                              <span className="text-muted">未招募</span>
                            )}
                          </span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted">角色等级</span>
                          <span className="font-mono font-bold text-foreground">
                            Lv.{currentDetail.base.level} / 80
                          </span>
                        </div>
                      </div>

                      <div className="rounded-xl border border-separator p-3.5 space-y-2.5 bg-surface">
                        <h3 className="text-xs font-bold text-foreground">权钥与武器模组</h3>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted">权钥等级</span>
                          <span className="font-mono font-bold text-foreground">
                            Lv.{currentDetail.base.weapon_level} / 60
                            <span className="text-muted font-normal text-[11px] ml-1">
                              ({currentDetail.base.weapon_break} 阶突破)
                            </span>
                          </span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted">模组等级</span>
                          {currentDetail.base.module_supported ? (
                            currentDetail.base.module_level > 0 ? (
                              <span className="font-mono font-bold text-accent">
                                Lv.{currentDetail.base.module_level} / 3
                              </span>
                            ) : (
                              <span className="text-warning font-medium">
                                未激活 (Lv.0 / 3)
                              </span>
                            )
                          ) : (
                            <span className="text-muted">不支持模组</span>
                          )}
                        </div>
                      </div>

                      <div className="rounded-xl border border-separator p-3.5 space-y-2.5 bg-surface">
                        <h3 className="text-xs font-bold text-foreground">阵营与神系属性</h3>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted">所属神系</span>
                          <div className="w-14 flex items-center gap-2 font-medium justify-start shrink-0">
                            <div className="size-5 rounded bg-zinc-800 border border-zinc-700/60 flex items-center justify-center p-0.5 shrink-0 shadow-2xs">
                              <img
                                src={resolveAssetUrl(selectedHero.race_icon)}
                                alt={selectedHero.race_name}
                                className="size-full object-contain"
                              />
                            </div>
                            <span className="tracking-wide whitespace-nowrap">{selectedHero.race_name}</span>
                          </div>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted">主战属性</span>
                          <div className="w-14 flex items-center gap-2 font-medium justify-start shrink-0">
                            <div className="size-5 rounded bg-surface-secondary border border-separator/60 flex items-center justify-center p-0.5 shrink-0 shadow-2xs">
                              <img
                                src={resolveAssetUrl(selectedHero.element_icon)}
                                alt={selectedHero.element_name}
                                className="size-full object-contain"
                              />
                            </div>
                            <span className="tracking-wide whitespace-nowrap">{selectedHero.element_name}</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {/* 2. 技能面板：左立绘右技能组，官方技能图标、强化等级与属性强化等级 */}
                {activeSubTab === 'skills' && (
                  <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-start">
                    {/* 左侧角色立绘 */}
                    <div className="md:col-span-5 relative flex h-[410px] w-full items-center justify-center overflow-hidden rounded-xl border border-separator/50 bg-gradient-to-b from-surface-secondary/40 via-surface/15 to-surface-secondary/50 p-2 shadow-inner">
                      <img
                        src={resolveAssetUrl(currentDetail.portrait || `extracted_assets/portraits/${selectedHero.id}.png`)}
                        alt={selectedHero.official_name}
                        className="max-h-[380px] w-auto max-w-full object-contain filter drop-shadow-md select-none transition-transform duration-300 hover:scale-105"
                        onError={(e) => {
                          ;(e.target as HTMLImageElement).src = resolveAssetUrl(selectedHero.avatar)
                        }}
                      />
                      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-surface/80 to-transparent" />
                      <span className="absolute bottom-2 left-3 text-[11px] font-medium text-muted/70 tracking-wide font-mono">
                        NO.{selectedHero.id} · {selectedHero.official_name}
                      </span>
                    </div>

                    {/* 右侧技能组 */}
                    <div className="md:col-span-7 space-y-2 max-h-[410px] overflow-y-auto pr-1">
                      {currentDetail.skills.map((skill, idx) => {
                        const isDodge = skill.is_dodge || skill.type === '闪避' || idx === 5
                        const addLv = skill.add_level || 0
                        const totalLv = skill.total_level ?? (skill.level + addLv)
                        return (
                          <div
                            key={skill.id}
                            className="flex items-center justify-between rounded-xl border border-separator/70 bg-surface p-2.5 hover:bg-surface-secondary/40 transition-colors"
                          >
                            <div className="flex items-center gap-3 min-w-0">
                              {/* 官方技能图标 */}
                              <div className="size-11 shrink-0 rounded-lg border border-separator/80 bg-zinc-900 p-0.5 flex items-center justify-center shadow-xs overflow-hidden">
                                <img
                                  src={resolveAssetUrl(skill.icon)}
                                  alt={skill.name}
                                  className="size-full object-contain"
                                  onError={(e) => {
                                    ;(e.target as HTMLElement).style.display = 'none'
                                  }}
                                />
                              </div>
                              <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="text-xs font-bold text-foreground truncate">
                                    {skill.name}
                                  </span>
                                  <Chip
                                    size="sm"
                                    variant="soft"
                                    color={isDodge ? 'default' : (skill.type === '奥义' ? 'accent' : 'default')}
                                    className="h-4.5 px-1.5 text-[10px]"
                                  >
                                    {skill.type}
                                  </Chip>
                                </div>
                                <div className="text-[11px] text-muted font-mono mt-0.5 flex items-center gap-2">
                                  {isDodge ? (
                                    <span>
                                      强化等级:{' '}
                                      <strong className="text-foreground">Lv.1</strong>
                                      <span className="text-muted/70 ml-1.5 text-[10px] font-sans">
                                        (默认 1 级 · 不可强化)
                                      </span>
                                    </span>
                                  ) : (
                                    <>
                                      <span>
                                        强化等级:{' '}
                                        <strong className="text-foreground">
                                          Lv.{totalLv}
                                        </strong>
                                        {addLv > 0 && (
                                          <span className="text-amber-500 font-semibold ml-1 text-[10px]">
                                            ({skill.level} + {addLv})
                                          </span>
                                        )}
                                      </span>
                                      <span>·</span>
                                      <span>
                                        属性强化:{' '}
                                        <strong className={skill.intensify > 0 ? "text-accent font-bold" : "text-muted font-normal"}>
                                          +{skill.intensify}
                                        </strong>
                                      </span>
                                    </>
                                  )}
                                </div>
                              </div>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* 3. 钥从面板：展示已装载/通用/未装载武装钥从 */}
                {activeSubTab === 'servant' && (() => {
                  const isServantEquipped = Boolean(currentDetail.servant?.equipped && currentDetail.servant?.id > 0)
                  const isUniversal = Boolean(isServantEquipped && currentDetail.servant?.is_universal)
                  const hasPlusTwo = (selectedHero.star >= 500 || currentDetail.base.star >= 500)

                  // 专属钥从与持有状态解析
                  const exclusiveServant = currentDetail.servant?.exclusive_servant
                  const isEquippedExclusive = Boolean(isServantEquipped && !isUniversal && exclusiveServant?.id && currentDetail.servant.id === exclusiveServant.id)
                  const isExclusiveOwned = isEquippedExclusive || Boolean(exclusiveServant?.owned)
                  const exclusiveStage = isEquippedExclusive
                    ? (currentDetail.servant.stage || 1)
                    : (exclusiveServant?.stage ?? 0)

                  // 对应神系沉睡之子解析 (优先专属对应神系，兜底角色自身神系)
                  const heroRaceId = selectedHero.race_id || currentDetail.servant?.race_id || exclusiveServant?.race_id || 1
                  const sleepingChild = RACE_SLEEPING_SERVANT[heroRaceId] || RACE_SLEEPING_SERVANT[1]

                  // 统一渲染：官方推荐专属钥从卡片（支持点击呼出立绘大图鉴赏）
                  const renderExclusiveRecommendationCard = () => {
                    if (!exclusiveServant) return null
                    const exCatalogItem = exclusiveServant?.id ? (weaponServantsCatalog as Record<string, any>)[String(exclusiveServant.id)] : null
                    const exclusiveDesc = exclusiveServant.desc || exCatalogItem?.desc || exCatalogItem?.effect_desc || ''
                    const exclusiveRaceName = exclusiveServant.race_name || exCatalogItem?.race_name || selectedHero.race_name

                    return (
                      <div
                        onClick={() => setPreviewServant({
                          name: exclusiveServant.name,
                          portrait: exclusiveServant.portrait,
                          star: 5,
                          desc: exclusiveDesc,
                          race_name: exclusiveRaceName,
                        })}
                        className="group relative rounded-xl border border-amber-500/30 bg-gradient-to-r from-amber-500/10 via-surface-secondary/20 to-transparent p-3.5 space-y-2 cursor-pointer transition-all duration-200 hover:border-amber-500/60 hover:shadow-[0_0_15px_rgba(245,158,11,0.15)] hover:bg-amber-500/15 select-none"
                        title="点击鉴赏专属钥从高精立绘大图"
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-semibold text-amber-500 flex items-center gap-1.5">
                            <Sparkles className="size-3.5 animate-pulse" />
                            官方推荐专属钥从
                          </span>
                          <div className="flex items-center gap-1.5">
                            <Chip size="sm" variant="soft" color="warning" className="text-[10px] h-5 font-bold">
                              5星专属
                            </Chip>
                            <span className="text-[10px] text-muted flex items-center gap-0.5 opacity-70 group-hover:opacity-100 group-hover:text-amber-400 transition-colors">
                              <Maximize2 className="size-3" />
                              点击大图
                            </span>
                          </div>
                        </div>
                        <div className="flex items-center gap-3">
                          {exclusiveServant.portrait && (
                            <div className="relative size-14 shrink-0 rounded-lg overflow-hidden border border-amber-500/40 bg-amber-950/30 shadow-xs flex items-center justify-center p-1 group-hover:scale-105 transition-transform">
                              <img
                                src={resolveAssetUrl(exclusiveServant.portrait)}
                                alt={exclusiveServant.name}
                                className="max-h-full max-w-full object-contain filter drop-shadow-sm"
                                onError={(e) => {
                                  const img = e.target as HTMLImageElement
                                  if (exclusiveServant?.id) {
                                    img.src = resolveAssetUrl(`extracted_assets/servants/icons/${exclusiveServant.id}.png`)
                                  }
                                }}
                              />
                            </div>
                          )}
                          <div className="space-y-0.5 min-w-0 flex-1">
                            <div className="text-sm font-bold text-foreground group-hover:text-amber-400 transition-colors truncate">
                              {exclusiveServant.name}
                            </div>
                            {exclusiveDesc && (
                              <p className="text-[11px] text-muted line-clamp-2 leading-relaxed">
                                {exclusiveDesc}
                              </p>
                            )}
                          </div>
                        </div>
                      </div>
                    )
                  }

                  // 统一渲染：战备物资调配 / 邮件附件快捷装填卡片
                  const renderMailShortcutBlock = () => {
                    return (
                      <div className="rounded-xl border border-separator/60 bg-surface-secondary/25 p-3.5 space-y-2.5">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                            <Mail className="size-3.5 text-primary" />
                            战备物资调配
                          </span>
                          {shortcutFeedback ? (
                            <span className="text-[11px] font-bold text-emerald-400 flex items-center gap-1 animate-pulse">
                              <CheckCircle2 className="size-3" />
                              {shortcutFeedback}
                            </span>
                          ) : (
                            <span className="text-[10px] font-mono text-muted">
                              {isExclusiveOwned ? `专属持有 · ${exclusiveStage} 阶` : '专属状态 · 未持有'}
                            </span>
                          )}
                        </div>

                        {/* 三路分支判定 */}
                        {isExclusiveOwned && exclusiveStage >= 5 ? (
                          <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-2.5 flex items-center justify-between text-xs">
                            <div className="flex items-center gap-2 text-emerald-400 font-medium">
                              <CheckCircle2 className="size-4 shrink-0" />
                              <span>已契约满阶专属钥从 · 权能已达极致，无需额外补给</span>
                            </div>
                            <Chip size="sm" variant="soft" color="success" className="text-[10px] h-5 font-bold">
                              5阶巅峰
                            </Chip>
                          </div>
                        ) : isExclusiveOwned && exclusiveStage < 5 ? (
                          <div className="space-y-2">
                            <div className="flex items-center justify-between text-[11px] text-muted">
                              <span>专属钥从同调进阶需要消耗「神识凝晶」</span>
                              <span className="text-amber-400 font-medium">当前 {exclusiveStage} 阶</span>
                            </div>
                            <Button
                              size="sm"
                              variant="secondary"
                              className="w-full flex items-center justify-between bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/35 text-amber-300 font-medium text-xs h-9 px-3 rounded-lg transition-all shadow-xs"
                              onPress={() => {
                                const ok = addAttachmentToMailDraft({
                                  id: 41201,
                                  name: '神识凝晶',
                                  rare: 5,
                                  icon_file: '41201.png',
                                  quality_frame: 'Item_yellow.png',
                                  count: 30,
                                })
                                if (ok) {
                                  setShortcutFeedback('神识凝晶 × 30 已加入邮件待发！')
                                  setTimeout(() => setShortcutFeedback(null), 3000)
                                }
                              }}
                            >
                              <div className="flex items-center gap-2.5">
                                <div className="relative size-6 shrink-0 rounded overflow-hidden border border-amber-500/40 bg-amber-950/40 flex items-center justify-center">
                                  <img src={resolveAssetUrl('extracted_assets/items/41201.png')} alt="神识凝晶" className="size-5 object-contain" />
                                </div>
                                <span>快捷添附: 神识凝晶 × 30 至邮件</span>
                              </div>
                              <Plus className="size-4 shrink-0 opacity-80" />
                            </Button>
                          </div>
                        ) : (
                          <div className="space-y-2">
                            <div className="flex items-center justify-between text-[11px] text-muted">
                              <span>尚未持有专属钥从，可唤醒本神系「沉睡之子」真名契约</span>
                              <span className="text-amber-400 font-medium">{sleepingChild.race_name}神系</span>
                            </div>
                            <Button
                              size="sm"
                              variant="secondary"
                              className="w-full flex items-center justify-between bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/35 text-amber-300 font-medium text-xs h-9 px-3 rounded-lg transition-all shadow-xs"
                              onPress={() => {
                                const ok = addAttachmentToMailDraft({
                                  id: sleepingChild.id,
                                  name: sleepingChild.name,
                                  rare: 5,
                                  icon_file: `${sleepingChild.id}.png`,
                                  quality_frame: 'Item_yellow.png',
                                  count: 1,
                                })
                                if (ok) {
                                  setShortcutFeedback(`${sleepingChild.name} × 1 已加入邮件待发！`)
                                  setTimeout(() => setShortcutFeedback(null), 3000)
                                }
                              }}
                            >
                              <div className="flex items-center gap-2.5">
                                <div className="relative size-6 shrink-0 rounded overflow-hidden border border-amber-500/40 bg-amber-950/40 flex items-center justify-center">
                                  <img src={resolveAssetUrl(`extracted_assets/items/${sleepingChild.id}.png`)} alt={sleepingChild.name} className="size-5 object-contain" />
                                </div>
                                <span>快捷添附: {sleepingChild.name} × 1 至邮件</span>
                              </div>
                              <Plus className="size-4 shrink-0 opacity-80" />
                            </Button>
                          </div>
                        )}
                      </div>
                    )
                  }

                  // 分支 1: 未装载钥从状态
                  if (!isServantEquipped) {
                    return (
                      <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-stretch">
                        {/* 左侧：未装载虚线卡槽 */}
                        <div className="md:col-span-5 relative flex flex-col h-[460px] w-full items-center justify-center overflow-hidden rounded-xl border-2 border-dashed border-separator/60 bg-gradient-to-b from-surface/40 via-surface/10 to-surface-secondary/20 p-6 shadow-inner text-center">
                          <div className="relative mb-4 flex items-center justify-center">
                            <div className="absolute size-24 rounded-full bg-muted/5 animate-pulse" />
                            <div className="size-20 rounded-2xl border border-dashed border-muted/30 bg-surface-secondary/60 flex items-center justify-center shadow-inner">
                              <ShieldOff className="size-10 text-muted/50" />
                            </div>
                          </div>

                          <div className="space-y-1 z-10">
                            <div className="text-base font-bold text-foreground/80 tracking-wide">
                              未装载武装钥从
                            </div>
                            <p className="text-xs text-muted max-w-[220px] leading-relaxed mx-auto">
                              当前权钥核心槽位空置，尚未唤醒或同步任何武装钥从
                            </p>
                          </div>

                          <div className="mt-5 flex flex-wrap items-center justify-center gap-2 z-10">
                            <Chip size="sm" variant="soft" color="default" className="text-[11px] font-mono text-muted">
                              SLOT EMPTY
                            </Chip>
                            <Chip size="sm" variant="soft" color="default" className="text-[11px] text-muted">
                              0 阶无共鸣
                            </Chip>
                          </div>

                          <span className="absolute bottom-2 left-3 text-[11px] font-mono text-muted/40">
                            STATUS · UNBOUND
                          </span>
                        </div>

                        {/* 右侧：未装载提示、推荐专属钥从与快捷邮件添附 */}
                        <div className="md:col-span-7">
                          <div className="rounded-xl border border-separator p-5 bg-surface min-h-[460px] flex flex-col justify-between space-y-4">
                            <div className="space-y-3.5">
                              <div className="flex items-center justify-between border-b border-separator/40 pb-3">
                                <div>
                                  <span className="text-[11px] text-muted">权钥核心同调</span>
                                  <h3 className="text-base font-bold text-muted">
                                    未装配武装钥从
                                  </h3>
                                </div>
                                <Chip size="sm" variant="soft" color="default" className="font-mono">
                                  槽位空置
                                </Chip>
                              </div>

                              {/* 等阶展示：0 阶未激活 */}
                              <div className="flex items-center justify-between text-xs py-2 px-3 rounded-lg bg-surface-secondary/40 border border-separator/30">
                                <span className="text-muted font-medium">当前同调等阶</span>
                                <div className="flex items-center gap-1.5">
                                  <span className="font-mono font-bold text-sm text-muted">
                                    0 阶 (未激活)
                                  </span>
                                </div>
                              </div>

                              {/* 系统说明 */}
                              <div className="rounded-lg border border-separator/60 bg-surface-secondary/30 p-3 text-xs space-y-1.5">
                                <div className="font-semibold text-foreground flex items-center gap-1.5">
                                  <Info className="size-3.5 text-primary" />
                                  <span>武装钥从装载说明</span>
                                </div>
                                <p className="text-muted leading-relaxed">
                                  武装钥从是修正者权钥的核心载体。装载专属钥从或对应神系的通用钥从后，将直接提升基础神能并激活专属权能与同调技能词条。
                                </p>
                              </div>
                            </div>

                            {/* 底部：推荐专属钥从卡片与战备快捷补给 */}
                            <div className="space-y-3 pt-2">
                              {renderExclusiveRecommendationCard()}
                              {renderMailShortcutBlock()}
                            </div>
                          </div>
                        </div>
                      </div>
                    )
                  }

                  // 分支 2: 已装载 5 星通用钥从 / 沉睡之子
                  if (isUniversal) {
                    return (
                      <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-stretch">
                        {/* 左侧：通用钥从全身高清立绘（支持点击鉴赏大图） */}
                        <div
                          onClick={() => setPreviewServant({
                            name: currentDetail.servant.name,
                            portrait: currentDetail.servant.portrait,
                            star: 5,
                            desc: currentDetail.servant.effect_desc || currentDetail.servant.desc,
                            race_name: currentDetail.servant.race_name || selectedHero.race_name,
                          })}
                          className="md:col-span-5 relative flex h-[460px] w-full items-center justify-center overflow-hidden rounded-xl border border-separator/50 bg-gradient-to-b from-blue-500/10 via-surface/20 to-purple-500/10 p-2 shadow-inner cursor-pointer group select-none"
                          title="点击鉴赏通用钥从高精立绘大图"
                        >
                          {currentDetail.servant.portrait ? (
                            <img
                              src={resolveAssetUrl(currentDetail.servant.portrait)}
                              alt={currentDetail.servant.name}
                              className="max-h-[420px] w-auto max-w-full object-contain filter drop-shadow-md select-none transition-transform duration-300 group-hover:scale-105"
                              onError={(e) => {
                                const img = e.target as HTMLImageElement
                                if (currentDetail.servant.icon && img.src !== resolveAssetUrl(currentDetail.servant.icon)) {
                                  img.src = resolveAssetUrl(currentDetail.servant.icon)
                                } else {
                                  img.style.display = 'none'
                                }
                              }}
                            />
                          ) : (
                            <div className="flex flex-col items-center justify-center text-muted">
                              <Shield className="size-12 opacity-30 mb-2" />
                              <span className="text-xs">未配置钥从立绘</span>
                            </div>
                          )}
                          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-surface/80 to-transparent" />
                          <div className="absolute bottom-2 inset-x-3 flex items-center justify-between">
                            <span className="text-[11px] font-medium text-blue-400 font-mono">
                              UNIVERSAL · {currentDetail.servant.name}
                            </span>
                            <span className="text-[10px] text-blue-400/80 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                              <Maximize2 className="size-3" />
                              点击查看大图
                            </span>
                          </div>
                        </div>

                        {/* 右侧：通用钥从数据、统一推荐卡片与快捷邮件添附 */}
                        <div className="md:col-span-7">
                          <div className="rounded-xl border border-separator p-5 bg-surface min-h-[460px] flex flex-col justify-between space-y-4">
                            <div className="space-y-3.5">
                              <div className="flex items-center justify-between border-b border-separator/40 pb-3">
                                <div>
                                  <div className="flex items-center gap-2">
                                    <span className="text-[11px] text-muted">装备武装钥从</span>
                                    <Chip size="sm" variant="soft" color="accent" className="h-5 text-[10px] font-bold">
                                      5星通用钥从
                                    </Chip>
                                  </div>
                                  <h3 className="text-base font-bold text-foreground mt-0.5">
                                    {currentDetail.servant.name}
                                  </h3>
                                </div>
                                <Chip size="sm" variant="soft" color="warning" className="font-mono">
                                  ID: {currentDetail.servant.id || 'N/A'}
                                </Chip>
                              </div>

                              {/* 等阶与同调 +2 机制 */}
                              <div className="flex items-center justify-between text-xs py-1">
                                <span className="text-muted font-medium">钥从等阶</span>
                                <div className="flex items-center gap-1.5">
                                  <span className="font-mono font-bold text-base text-foreground">
                                    {currentDetail.servant.stage} 阶
                                  </span>
                                  {hasPlusTwo && (
                                    <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-bold bg-amber-500/20 text-amber-400 border border-amber-500/40 shadow-xs animate-pulse">
                                      +2
                                    </span>
                                  )}
                                </div>
                              </div>

                              {/* 神系通用共鸣机制 */}
                              <div className="rounded-lg border border-separator/60 bg-surface-secondary/30 p-3 text-xs space-y-1.5">
                                <div className="font-semibold text-foreground flex items-center gap-1.5">
                                  <Sparkles className="size-3.5 text-blue-400" />
                                  <span>神系通用权能共鸣</span>
                                </div>
                                <p className="text-muted leading-relaxed">
                                  {currentDetail.servant.effect_desc || currentDetail.servant.desc || '神系通用武装钥从，为同系修正者提供全局权能共振与属性增强。'}
                                </p>
                                {hasPlusTwo ? (
                                  <p className="text-amber-400 font-medium">
                                    ★ 当前角色已超越至 SSS/Ω 阶级，通用钥从同步享受等阶额外 <strong className="underline">+2 阶</strong> 同调加成！
                                  </p>
                                ) : (
                                  <p className="text-muted">
                                    提示：当角色超越至 SSS 阶或更高品阶时，通用钥从也将激活额外 +2 阶同调权能。
                                  </p>
                                )}
                              </div>
                            </div>

                            {/* 底部：统一推荐专属卡片（支持点击大图）与邮件快捷添附 */}
                            <div className="space-y-3 pt-2">
                              {renderExclusiveRecommendationCard()}
                              {renderMailShortcutBlock()}
                            </div>
                          </div>
                        </div>
                      </div>
                    )
                  }

                  // 分支 3: 已装载专属武装钥从
                  return (
                    <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-stretch">
                      {/* 左侧钥从立绘（支持点击鉴赏大图） */}
                      <div
                        onClick={() => setPreviewServant({
                          name: currentDetail.servant.name,
                          portrait: currentDetail.servant.portrait,
                          star: 5,
                          desc: currentDetail.servant.effect_desc || currentDetail.servant.desc,
                          race_name: currentDetail.servant.race_name || selectedHero.race_name,
                        })}
                        className="md:col-span-5 relative flex h-[460px] w-full items-center justify-center overflow-hidden rounded-xl border border-separator/50 bg-gradient-to-b from-amber-500/10 via-surface/20 to-amber-500/5 p-2 shadow-inner cursor-pointer group select-none"
                        title="点击鉴赏专属钥从高精立绘大图"
                      >
                        {currentDetail.servant.portrait ? (
                          <img
                            src={resolveAssetUrl(currentDetail.servant.portrait)}
                            alt={currentDetail.servant.name}
                            className="max-h-[420px] w-auto max-w-full object-contain filter drop-shadow-md select-none transition-transform duration-300 group-hover:scale-105"
                            onError={(e) => {
                              const img = e.target as HTMLImageElement
                              if (currentDetail.servant.icon && img.src !== resolveAssetUrl(currentDetail.servant.icon)) {
                                img.src = resolveAssetUrl(currentDetail.servant.icon)
                              } else {
                                img.style.display = 'none'
                              }
                            }}
                          />
                        ) : (
                          <div className="flex flex-col items-center justify-center text-muted">
                            <Shield className="size-12 opacity-30 mb-2" />
                            <span className="text-xs">未配置钥从立绘</span>
                          </div>
                        )}
                        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-surface/80 to-transparent" />
                        <div className="absolute bottom-2 inset-x-3 flex items-center justify-between">
                          <span className="text-[11px] font-medium text-amber-500/80 font-mono">
                            EXCLUSIVE · {currentDetail.servant.name}
                          </span>
                          <span className="text-[10px] text-amber-400/80 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                            <Maximize2 className="size-3" />
                            点击查看大图
                          </span>
                        </div>
                      </div>

                      {/* 右侧钥从详细数据 */}
                      <div className="md:col-span-7">
                        <div className="rounded-xl border border-separator p-5 bg-surface min-h-[460px] flex flex-col justify-between space-y-4">
                          <div className="space-y-3.5">
                            <div className="flex items-center justify-between border-b border-separator/40 pb-3">
                              <div>
                                <div className="flex items-center gap-2">
                                  <span className="text-[11px] text-muted">装备武装钥从</span>
                                  <Chip size="sm" variant="soft" color="warning" className="h-5 text-[10px] font-bold">
                                    5星专属
                                  </Chip>
                                </div>
                                <h3 className="text-base font-bold text-foreground mt-0.5">
                                  {currentDetail.servant.name}
                                </h3>
                              </div>
                              <Chip size="sm" variant="soft" color="warning" className="font-mono">
                                ID: {currentDetail.servant.id || 'N/A'}
                              </Chip>
                            </div>

                            {/* 等阶与同调 +2 机制 */}
                            <div className="flex items-center justify-between text-xs py-1">
                              <span className="text-muted font-medium">钥从等阶</span>
                              <div className="flex items-center gap-1.5">
                                <span className="font-mono font-bold text-base text-foreground">
                                  {currentDetail.servant.stage} 阶
                                </span>
                                {hasPlusTwo && (
                                  <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-bold bg-amber-500/20 text-amber-400 border border-amber-500/40 shadow-xs animate-pulse">
                                    +2
                                  </span>
                                )}
                              </div>
                            </div>

                            <div className="rounded-lg border border-separator/60 bg-surface-secondary/30 p-3 text-xs space-y-1.5">
                              <div className="font-semibold text-foreground flex items-center gap-1.5">
                                <Sparkles className="size-3.5 text-amber-400" />
                                <span>专属神识同调机制</span>
                              </div>
                              <p className="text-muted leading-relaxed">
                                {currentDetail.servant.effect_desc || currentDetail.servant.desc || '武装钥从直接承载专精战斗权能，强化核心技能循环与神能共鸣。'}
                              </p>
                              {hasPlusTwo ? (
                                <p className="text-amber-400 font-medium">
                                  ★ 当前角色已超越至 SSS/Ω 阶级，触发同调潜能爆发，钥从等阶获得额外 <strong className="underline">+2 阶</strong> 同调加成！
                                </p>
                              ) : (
                                <p className="text-muted">
                                  提示：当角色超越至 SSS 阶或更高品阶时，钥从将自动激活额外 +2 阶同调权能。
                                </p>
                              )}
                            </div>
                          </div>

                          {/* 底部：专属钥从状态提示或进阶材料快捷装填 */}
                          <div className="pt-2">
                            {renderMailShortcutBlock()}
                          </div>
                        </div>
                      </div>
                    </div>
                  )
                })()}

                {/* 4. 刻印面板：不展示角色立绘，顶部生效套装，纵向6槽位带金框，点击展示右侧赋能词条 */}
                {activeSubTab === 'equip' && (
                  <div className="space-y-3">
                    {/* 顶部当前生效套装效果 */}
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 rounded-xl border border-separator/60 bg-surface-secondary/40 p-3">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Sparkles className="size-4 text-warning shrink-0" />
                        <span className="text-xs font-bold text-foreground">当前生效套装效果:</span>
                        {currentDetail.equips.is_omega ? (
                          <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 border border-amber-500/30 px-2 py-0.5 text-[10px] font-bold text-amber-400">
                            ★ Ω 阶特权 · 套装门槛 -1 (2件生效 · 最多可同时激活3套)
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[10px] text-muted font-mono">
                            (同套装穿戴 3 件生效)
                          </span>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        {currentDetail.equips.active_suits.length > 0 ? (
                          currentDetail.equips.active_suits.map((suit, idx) => {
                            const isOmegaSuit = suit.includes('Ω')
                            return (
                              <Chip
                                key={idx}
                                size="sm"
                                variant="soft"
                                color={isOmegaSuit ? "warning" : "accent"}
                                className={`h-6 px-2 text-xs font-semibold ${isOmegaSuit ? 'border border-amber-500/40 text-amber-300' : ''}`}
                              >
                                {suit}
                              </Chip>
                            )
                          })
                        ) : (
                          <span className="text-xs text-muted">
                            {currentDetail.equips.is_omega
                              ? '暂无生效套装 (Ω 阶任意套装穿戴 2 件即可生效)'
                              : '暂无生效套装 (同套装需穿戴 3 件生效)'}
                          </span>
                        )}
                      </div>
                    </div>

                    {/* 6槽位卡片与右侧赋能词条 */}
                    <div className="grid grid-cols-1 md:grid-cols-12 gap-4">
                      {/* 左侧 6 槽位纵向卡片 */}
                      <div className="md:col-span-6 space-y-2 max-h-[360px] overflow-y-auto pr-1">
                        {currentDetail.equips.slots.map((slot) => {
                          const isSelected = selectedEquipSlot === slot.pos
                          const isEquipped = Boolean(slot.equipped && slot.star > 0 && slot.suit_name !== '未激活刻印')
                          const starStyle = getEquipStarStyle(slot.star)
                          const slotRoman = SLOT_ROMAN_NUMERALS[slot.pos - 1] || String(slot.pos)

                          return (
                            <div
                              key={slot.pos}
                              onClick={() => setSelectedEquipSlot(slot.pos)}
                              className={`flex items-center justify-between rounded-xl border p-2.5 transition-all cursor-pointer ${
                                isSelected
                                  ? (isEquipped ? starStyle.activeRing : 'border-separator bg-surface-secondary/50 ring-1 ring-separator shadow-xs')
                                  : (isEquipped ? `border-separator/70 bg-surface ${starStyle.hover}` : 'border-separator/40 bg-surface/40 hover:border-separator/70 hover:bg-surface-secondary/20')
                              }`}
                            >
                              <div className="flex items-center gap-3 min-w-0">
                                {isEquipped ? (
                                  /* 刻印品质框（动态品质色识别） */
                                  <div className={`relative size-12 shrink-0 rounded-lg border-2 ${starStyle.border} ${starStyle.bg} p-0.5 shadow-xs overflow-hidden flex items-center justify-center`}>
                                    <img
                                      src={resolveAssetUrl(slot.icon)}
                                      alt={slot.suit_name}
                                      className="size-full object-contain"
                                      onError={(e) => {
                                        ;(e.target as HTMLElement).style.display = 'none'
                                      }}
                                    />
                                    <span className={`absolute bottom-0 right-0 ${starStyle.badge} text-[9px] font-bold px-1 rounded-tl`}>
                                      {slot.star}★
                                    </span>
                                  </div>
                                ) : (
                                  /* 未装备槽位保护：虚线框 + 槽位罗马数字标号，绝不套用五星黄框 */
                                  <div className="relative size-12 shrink-0 rounded-lg border-2 border-dashed border-separator/60 bg-surface-secondary/30 p-0.5 flex items-center justify-center">
                                    <span className="text-xs font-mono font-bold text-muted/60">
                                      {slotRoman}
                                    </span>
                                  </div>
                                )}
                                <div className="min-w-0">
                                  <div className="flex items-center gap-1.5">
                                    <span className={`text-xs font-bold truncate ${isEquipped ? 'text-foreground' : 'text-muted'}`}>
                                      槽位 {slot.pos} · {isEquipped ? slot.suit_name : '未激活'}
                                    </span>
                                  </div>
                                  <div className="text-[11px] text-muted font-mono mt-0.5">
                                    {isEquipped ? `Lv.${slot.level} · ${slot.enchants.length} / 4 赋能` : '未装备刻印'}
                                  </div>
                                </div>
                              </div>
                              <ChevronRight className={`size-4 text-muted transition-transform ${isSelected ? 'text-warning translate-x-0.5' : ''}`} />
                            </div>
                          )
                        })}
                      </div>

                      {/* 右侧：当前选中槽位的赋能词条 */}
                      <div className="md:col-span-6 rounded-xl border border-separator p-3.5 bg-surface-secondary/20 flex flex-col justify-between">
                        <div>
                          <div className="flex items-center justify-between border-b border-separator/40 pb-2.5 mb-3">
                            <div className="flex items-center gap-2">
                              <h4 className="text-xs font-bold text-foreground">
                                槽位 {selectedEquipSlot} · 赋能词条明细
                              </h4>
                              {selectedEquipData?.equipped && selectedEquipData.star > 0 && (
                                <Chip size="sm" variant="soft" color="warning" className="h-5 px-1.5 text-[10px] font-semibold">
                                  {selectedEquipData.enchants.length} / 4 词条
                                </Chip>
                              )}
                            </div>
                            <span className="text-[11px] text-muted truncate max-w-[140px]">
                              {selectedEquipData?.equipped && selectedEquipData.star > 0 ? selectedEquipData.suit_name : '未装备刻印'}
                            </span>
                          </div>

                          {selectedEquipData?.equipped && selectedEquipData.star > 0 ? (
                            <div className="space-y-2">
                              {/* 1. 已生效赋能词条列表 */}
                              {selectedEquipData.enchants.map((enchant, idx) => (
                                <div
                                  key={idx}
                                  className="flex items-center justify-between rounded-lg border border-separator/60 bg-surface px-3 py-2 text-xs transition-colors hover:border-warning/30"
                                >
                                  <div className="flex items-center gap-2.5 min-w-0">
                                    <div className="size-7 shrink-0 rounded bg-surface-secondary border border-separator/50 flex items-center justify-center p-0.5">
                                      <img
                                        src={resolveAssetUrl(enchant.icon)}
                                        alt={enchant.name}
                                        className="size-full object-contain"
                                        onError={(e) => {
                                          ;(e.target as HTMLElement).style.display = 'none'
                                        }}
                                      />
                                    </div>
                                    <div className="flex items-center gap-2 min-w-0">
                                      <span className="font-medium text-foreground truncate">{enchant.name}</span>
                                      <span className="text-[10px] text-muted font-mono bg-surface-secondary px-1.5 py-0.5 rounded border border-separator/40">
                                        词条 {idx + 1}
                                      </span>
                                    </div>
                                  </div>
                                  <span className="font-mono font-bold text-warning shrink-0">
                                    Lv.{enchant.level}
                                  </span>
                                </div>
                              ))}

                              {/* 2. 空置未洗练词条槽位（补齐至最多 4 个） */}
                              {Array.from({ length: Math.max(0, 4 - selectedEquipData.enchants.length) }).map((_, i) => {
                                const slotNum = selectedEquipData.enchants.length + i + 1
                                return (
                                  <div
                                    key={`empty-${i}`}
                                    className="flex items-center justify-between rounded-lg border border-dashed border-separator/50 bg-surface/30 px-3 py-2 text-xs text-muted"
                                  >
                                    <div className="flex items-center gap-2.5">
                                      <div className="size-7 shrink-0 rounded border border-dashed border-separator/50 flex items-center justify-center text-[10px] text-muted/60 font-mono">
                                        {slotNum}
                                      </div>
                                      <span className="text-muted/70">赋能词条槽位 {slotNum}</span>
                                    </div>
                                    <span className="text-[10px] text-muted/50 font-mono">未洗练</span>
                                  </div>
                                )
                              })}
                            </div>
                          ) : (
                            /* 未装备刻印时的空状态 */
                            <div className="py-12 text-center flex flex-col items-center justify-center space-y-2">
                              <div className="size-10 rounded-full border border-dashed border-separator/70 bg-surface-secondary/40 flex items-center justify-center text-muted/60 mb-1">
                                <ShieldOff className="size-5" />
                              </div>
                              <div className="text-xs font-semibold text-foreground">该槽位暂未穿戴刻印</div>
                              <div className="text-[11px] text-muted max-w-[200px]">
                                穿戴刻印后，将在此展示该刻印携带的最多 4 条洗练赋能词条
                              </div>
                            </div>
                          )}
                        </div>

                        {/* 底部架构说明注释 */}
                        <div className="pt-2 text-[11px] text-muted/70 flex items-center gap-1.5 border-t border-separator/30 mt-3">
                          <Info className="size-3.5 shrink-0 text-muted/70" />
                          <span>单个刻印最高可洗练携带 4 个赋能词条（不含刻印固有词条）</span>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {/* 5. 跃迁面板：不展示角色立绘，六个槽位卡片只显示槽位一、槽位二，图标显示跃迁技能图标，卡片显示总等级，点击展示右侧技能 */}
                {activeSubTab === 'transition' && (
                  <div className="grid grid-cols-1 md:grid-cols-12 gap-4">
                    {/* 左侧 6 槽位纵向卡片 */}
                    <div className="md:col-span-6 space-y-2 max-h-[410px] overflow-y-auto pr-1">
                      {currentDetail.transitions.map((slot) => {
                        const isSelected = selectedTransitionSlot === slot.slot_id
                        return (
                          <div
                            key={slot.slot_id}
                            onClick={() => setSelectedTransitionSlot(slot.slot_id)}
                            className={`flex items-center justify-between rounded-xl border p-2.5 transition-all cursor-pointer ${
                              isSelected
                                ? 'border-accent bg-accent/10 ring-1 ring-accent/40 shadow-xs'
                                : 'border-separator/70 bg-surface hover:border-accent/40 hover:bg-surface-secondary/40'
                            }`}
                          >
                            <div className="flex items-center gap-3 min-w-0">
                              {/* 槽位装备的跃迁技能图标：左侧双图标方案，单技能单图标，空槽位占位符 */}
                              <div className="h-11 w-[68px] shrink-0 rounded-lg border border-separator/70 bg-surface-secondary/70 p-1 flex items-center justify-center">
                                {slot.skills && slot.skills.length >= 2 ? (
                                  <div className="flex items-center gap-1 size-full justify-center">
                                    <div className="w-7 h-8.5 rounded bg-surface/90 border border-separator/50 flex items-center justify-center p-0.5 overflow-hidden shadow-2xs">
                                      <img
                                        src={resolveAssetUrl(slot.skills[0].icon)}
                                        alt={slot.skills[0].name}
                                        title={slot.skills[0].name}
                                        className="size-full object-contain"
                                        onError={(e) => {
                                          ;(e.target as HTMLElement).style.display = 'none'
                                        }}
                                      />
                                    </div>
                                    <div className="w-7 h-8.5 rounded bg-surface/90 border border-separator/50 flex items-center justify-center p-0.5 overflow-hidden shadow-2xs">
                                      <img
                                        src={resolveAssetUrl(slot.skills[1].icon)}
                                        alt={slot.skills[1].name}
                                        title={slot.skills[1].name}
                                        className="size-full object-contain"
                                        onError={(e) => {
                                          ;(e.target as HTMLElement).style.display = 'none'
                                        }}
                                      />
                                    </div>
                                  </div>
                                ) : slot.skills && slot.skills.length === 1 ? (
                                  <div className="w-7 h-8.5 rounded bg-surface/90 border border-separator/50 flex items-center justify-center p-0.5 overflow-hidden shadow-2xs">
                                    <img
                                      src={resolveAssetUrl(slot.skills[0].icon)}
                                      alt={slot.skills[0].name}
                                      title={slot.skills[0].name}
                                      className="size-full object-contain"
                                      onError={(e) => {
                                        ;(e.target as HTMLElement).style.display = 'none'
                                      }}
                                    />
                                  </div>
                                ) : (
                                  <div className="w-7 h-8.5 rounded border border-dashed border-separator/50 flex items-center justify-center">
                                    <Zap className="size-4 text-muted/30" />
                                  </div>
                                )}
                              </div>
                              <div className="min-w-0">
                                {/* 文字只显示槽位一、槽位二即可 */}
                                <div className="text-xs font-bold text-foreground">
                                  {slot.slot_name}
                                </div>
                                <div className="text-[11px] text-muted font-mono mt-0.5">
                                  总等级: Lv.{slot.total_level}
                                </div>
                              </div>
                            </div>
                            <ChevronRight className={`size-4 text-muted transition-transform ${isSelected ? 'text-accent translate-x-0.5' : ''}`} />
                          </div>
                        )
                      })}
                    </div>

                    {/* 右侧：当前选中槽位的跃迁技能 */}
                    <div className="md:col-span-6 rounded-xl border border-separator p-3.5 bg-surface-secondary/20">
                      <div className="flex items-center justify-between border-b border-separator/40 pb-2 mb-3">
                        <h4 className="text-xs font-bold text-foreground">
                          {selectedTransitionData?.slot_name} · 跃迁能力强化
                        </h4>
                        <span className="text-[11px] text-accent font-mono">
                          总等级 Lv.{selectedTransitionData?.total_level || 0}
                        </span>
                      </div>
                      <div className="space-y-2">
                        {selectedTransitionData && selectedTransitionData.skills.length > 0 ? (
                          selectedTransitionData.skills.map((sk, idx) => (
                            <div
                              key={idx}
                              className="flex items-center justify-between rounded-lg border border-separator/60 bg-surface px-3 py-2 text-xs"
                            >
                              <div className="flex items-center gap-2.5">
                                <div className="w-8 h-10 shrink-0 rounded bg-surface-secondary border border-separator/50 flex items-center justify-center p-0.5 shadow-2xs">
                                  <img
                                    src={resolveAssetUrl(sk.icon)}
                                    alt={sk.name}
                                    className="size-full object-contain"
                                    onError={(e) => {
                                      ;(e.target as HTMLElement).style.display = 'none'
                                    }}
                                  />
                                </div>
                                <span className="font-medium text-foreground">{sk.name}</span>
                              </div>
                              <span className="font-mono font-bold text-accent">
                                Lv.{sk.level}
                              </span>
                            </div>
                          ))
                        ) : (
                          <div className="py-8 text-center text-xs text-muted">
                            当前槽位未配置跃迁技能
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                {/* 6. 芯片面板：左立绘右佩戴管理芯片，游戏内图标+真实名称+战术定位+效果描述，动态自适应不固定槽位 */}
                {activeSubTab === 'chips' && (() => {
                  const chipsList = currentDetail.chips || []
                  const equippedCount = chipsList.filter((c) => c.equipped ?? (c.id > 0)).length
                  const totalCount = chipsList.length

                  return (
                    <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-start">
                      {/* 左侧角色立绘 */}
                      <div className="md:col-span-5 relative flex h-[460px] w-full items-center justify-center overflow-hidden rounded-xl border border-separator/50 bg-gradient-to-b from-surface-secondary/40 via-surface/15 to-surface-secondary/50 p-2 shadow-inner">
                        <img
                          src={resolveAssetUrl(currentDetail.portrait || `extracted_assets/portraits/${selectedHero.id}.png`)}
                          alt={selectedHero.official_name}
                          className="max-h-[420px] w-auto max-w-full object-contain filter drop-shadow-md select-none transition-transform duration-300 hover:scale-105"
                          onError={(e) => {
                            ;(e.target as HTMLImageElement).src = resolveAssetUrl(selectedHero.avatar)
                          }}
                        />
                        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-surface/80 to-transparent" />
                        <span className="absolute bottom-2 left-3 text-[11px] font-medium text-muted/70 tracking-wide font-mono">
                          NO.{selectedHero.id} · {selectedHero.official_name}
                        </span>
                      </div>

                      {/* 右侧管理芯片组装 */}
                      <div className="md:col-span-7 space-y-3">
                        <div className="flex items-center justify-between rounded-xl border border-separator/60 bg-surface-secondary/30 p-3">
                          <div>
                            <div className="flex items-center gap-2 text-xs font-bold text-foreground">
                              <Cpu className="size-4 text-accent" />
                              <span>管理芯片系统 (AI 战术模块)</span>
                            </div>
                            <p className="text-[11px] text-muted mt-0.5">
                              管理芯片为修正者提供作为队友时的核心战斗决策、AI 逻辑与技能循环优化。
                            </p>
                          </div>
                          <Chip
                            size="sm"
                            variant="soft"
                            color={equippedCount > 0 ? 'success' : 'default'}
                            className="font-mono text-xs font-semibold shrink-0"
                          >
                            已装配 {equippedCount} / {totalCount} 槽位
                          </Chip>
                        </div>

                        {/* 动态自适应芯片槽位卡片网格 (适配非固定槽位数量) */}
                        <div className="max-h-[400px] overflow-y-auto pr-1">
                          {chipsList.length > 0 ? (
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                              {chipsList.map((chip) => {
                                const isEquipped = Boolean(chip.equipped ?? (chip.id > 0))

                                if (!isEquipped) {
                                  return (
                                    <div
                                      key={chip.slot_id}
                                      className="relative flex flex-col justify-center items-center rounded-xl border border-dashed border-separator/70 bg-surface/20 p-4 text-center min-h-[140px] transition-colors hover:border-separator"
                                    >
                                      <div className="size-10 rounded-full border border-dashed border-separator/80 flex items-center justify-center text-muted/40 mb-2">
                                        <Cpu className="size-5" />
                                      </div>
                                      <span className="text-xs font-semibold text-muted/70">
                                        槽位 {chip.slot_id} · 未装配芯片
                                      </span>
                                      <span className="text-[10px] text-muted/40 mt-1 font-mono tracking-wider">
                                        SLOT VACANT
                                      </span>
                                    </div>
                                  )
                                }

                                const roleTypeName = chip.role_type_name || '专属战术'

                                return (
                                  <div
                                    key={chip.slot_id}
                                    className="group relative flex flex-col justify-between rounded-xl border border-separator/80 bg-gradient-to-br from-surface via-surface to-surface-secondary/40 p-3.5 shadow-sm transition-all duration-200 hover:border-accent/40 hover:shadow-md"
                                  >
                                    <div>
                                      {/* 顶部：槽位号与定位标签 + 激活状态 */}
                                      <div className="flex items-center justify-between gap-2 mb-2.5">
                                        <div className="flex items-center gap-1.5">
                                          <span className="text-[11px] font-mono text-muted/80 font-medium">
                                            槽位 {chip.slot_id}
                                          </span>
                                          {roleTypeName && (
                                            <Chip
                                              size="sm"
                                              variant="soft"
                                              color={
                                                roleTypeName.includes('专属')
                                                  ? 'accent'
                                                  : roleTypeName.includes('终结') || roleTypeName.includes('特化')
                                                    ? 'warning'
                                                    : 'default'
                                              }
                                              className="h-4.5 text-[10px] px-1.5 font-bold"
                                            >
                                              {roleTypeName}
                                            </Chip>
                                          )}
                                        </div>
                                        <Chip
                                          size="sm"
                                          variant="soft"
                                          color="success"
                                          className="h-4.5 text-[10px] font-bold gap-1 px-1.5"
                                        >
                                          <CheckCircle2 className="size-3" />
                                          <span>已激活</span>
                                        </Chip>
                                      </div>

                                      {/* 中部：游戏内图标 + 芯片名称 + ID */}
                                      <div className="flex items-center gap-3">
                                        <div className="relative size-12 shrink-0 overflow-hidden rounded-xl border border-accent/20 bg-surface-secondary/80 p-1 flex items-center justify-center shadow-inner group-hover:border-accent/50 transition-colors">
                                          {chip.icon ? (
                                            <img
                                              src={resolveAssetUrl(chip.icon)}
                                              alt={chip.name}
                                              className="size-full object-contain filter drop-shadow select-none transition-transform duration-200 group-hover:scale-110"
                                              onError={(e) => {
                                                ;(e.target as HTMLImageElement).style.display = 'none'
                                                const fallback = (e.target as HTMLImageElement)
                                                  .nextElementSibling as HTMLElement
                                                if (fallback) fallback.style.display = 'flex'
                                              }}
                                            />
                                          ) : null}
                                          <div
                                            className="size-full items-center justify-center text-accent"
                                            style={{ display: chip.icon ? 'none' : 'flex' }}
                                          >
                                            <Cpu className="size-5" />
                                          </div>
                                        </div>
                                        <div className="min-w-0 flex-1">
                                          <div className="text-sm font-bold text-foreground tracking-tight truncate group-hover:text-accent transition-colors">
                                            {chip.name}
                                          </div>
                                          <div className="text-[10px] font-mono text-muted mt-0.5">
                                            ID: {chip.id}
                                          </div>
                                        </div>
                                      </div>
                                    </div>

                                    {/* 底部：官方战斗逻辑效果描述 */}
                                    <div className="mt-2.5 rounded-lg border border-separator/40 bg-surface-secondary/50 p-2 text-[11px] leading-relaxed text-muted-foreground whitespace-pre-line select-text font-normal">
                                      {chip.desc || '专属战术逻辑芯片，持续优化战斗行动决策。'}
                                    </div>
                                  </div>
                                )
                              })}
                            </div>
                          ) : (
                            <div className="py-12 text-center text-xs text-muted rounded-xl border border-dashed border-separator">
                              当前修正者暂未搭载管理芯片
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                })()}

                {/* 7. 心链誓约面板：左立绘右誓约档案，好感度等级、心链剧情进度、是否誓约（已誓约高亮并追加展示爱称，未誓约隐藏爱称） */}
                {activeSubTab === 'oath' && (
                  <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-start">
                    {/* 左侧角色立绘 */}
                    <div className="md:col-span-5 relative flex h-[410px] w-full items-center justify-center overflow-hidden rounded-xl border border-separator/50 bg-gradient-to-b from-surface-secondary/40 via-surface/15 to-surface-secondary/50 p-2 shadow-inner">
                      <img
                        src={resolveAssetUrl(currentDetail.portrait || `extracted_assets/portraits/${selectedHero.id}.png`)}
                        alt={selectedHero.official_name}
                        className="max-h-[380px] w-auto max-w-full object-contain filter drop-shadow-md select-none transition-transform duration-300 hover:scale-105"
                        onError={(e) => {
                          ;(e.target as HTMLImageElement).src = resolveAssetUrl(selectedHero.avatar)
                        }}
                      />
                      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-surface/80 to-transparent" />
                      <span className="absolute bottom-2 left-3 text-[11px] font-medium text-muted/70 tracking-wide font-mono">
                        NO.{selectedHero.id} · {selectedHero.official_name}
                      </span>
                    </div>

                    {/* 右侧誓约与双轨好感度档案 */}
                    <div className="md:col-span-7 space-y-3">
                      {/* 1. 心链誓约状态卡片 */}
                      <div
                        className={`rounded-xl border p-4 transition-all ${
                          currentDetail.oath.is_oath
                            ? 'border-rose-500/40 bg-rose-500/5 ring-1 ring-rose-500/30'
                            : 'border-separator/60 bg-surface'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <Heart
                              className={`size-5 ${
                                currentDetail.oath.is_oath ? 'fill-rose-500 text-rose-500' : 'text-muted'
                              }`}
                            />
                            <div>
                              <h3 className="text-sm font-bold">心链誓约仪式</h3>
                              <span className="text-[11px] text-muted">
                                {currentDetail.oath.is_oath
                                  ? `已缔结誓约 · 誓约进阶 Lv.${currentDetail.oath.oath_level || 1} / 3`
                                  : '尚未缔结誓约'}
                              </span>
                            </div>
                          </div>
                          {currentDetail.oath.is_oath ? (
                            <Chip size="sm" variant="soft" color="danger" className="gap-1 font-bold">
                              <Chip.Label>已誓约</Chip.Label>
                            </Chip>
                          ) : (
                            <Chip size="sm" variant="soft" color="default">
                              <Chip.Label>未誓约</Chip.Label>
                            </Chip>
                          )}
                        </div>

                        {/* 已誓约高亮并追加展示爱称 */}
                        {currentDetail.oath.is_oath && (
                          <div className="mt-3 rounded-lg border border-rose-500/20 bg-rose-500/10 p-2.5">
                            <span className="text-[11px] text-rose-400 font-medium">管理员专属爱称</span>
                            <div className="text-base font-extrabold text-rose-500 mt-0.5">
                              {currentDetail.oath.nick || selectedHero.custom_name || selectedHero.official_name}
                            </div>
                          </div>
                        )}
                      </div>

                      {/* 2. 一阶 · 基础档案好感 (全员通用，Lv.1 ~ Lv.5) */}
                      <div className="rounded-xl border border-separator/70 bg-surface p-4 text-xs space-y-2.5">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2 font-bold text-foreground">
                            <Sparkles className="size-4 text-pink-400" />
                            <span>一阶 · 基础档案好感</span>
                          </div>
                          <div className="flex items-center gap-1.5">
                            <span className="text-[11px] text-muted">
                              {currentDetail.oath.like_title || '好感度五级'}
                            </span>
                            <Chip size="sm" variant="soft" color="accent" className="font-bold font-mono text-[11px]">
                              <Chip.Label>
                                Lv.{currentDetail.oath.like_level || 5} · {currentDetail.oath.like_roman || 'Ⅴ'}阶
                              </Chip.Label>
                            </Chip>
                          </div>
                        </div>

                        {/* 进度条 */}
                        <div className="space-y-1">
                          <div className="flex items-center justify-between text-[11px]">
                            <span className="text-muted">累积好感度</span>
                            <span className="font-mono font-semibold text-foreground">
                              {currentDetail.oath.like_total_exp ?? 1000} / 1000 EXP
                            </span>
                          </div>
                          <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-secondary/70">
                            <div
                              className="h-full rounded-full bg-gradient-to-r from-pink-500 to-rose-400 transition-all duration-500"
                              style={{
                                width: `${Math.min(
                                  100,
                                  Math.round(((currentDetail.oath.like_total_exp ?? 1000) / 1000) * 100)
                                )}%`,
                              }}
                            />
                          </div>
                          <div className="flex justify-between text-[10px] text-muted/70 pt-0.5">
                            <span>初始 Ⅰ 阶</span>
                            <span>
                              {(currentDetail.oath.like_total_exp ?? 1000) >= 1000
                                ? '已达一阶极值 (1000/1000)'
                                : `距下一阶还需 ${Math.max(0, (currentDetail.oath.like_exp_max || 100) - (currentDetail.oath.like_exp || 0))} EXP`}
                            </span>
                            <span>满级 Ⅴ 阶</span>
                          </div>
                        </div>
                      </div>

                      {/* 3. 二阶 · 专属交心体系 (27位角色专属) */}
                      <div className="rounded-xl border border-separator/70 bg-surface p-4 text-xs space-y-2.5">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2 font-bold text-foreground">
                            <Heart className="size-4 text-amber-400" />
                            <span>二阶 · 专属交心体系</span>
                          </div>

                          {/* 状态徽章 */}
                          {!currentDetail.oath.has_trust ? (
                            <Chip size="sm" variant="soft" color="default" className="text-[11px]">
                              <Chip.Label>未实装交心</Chip.Label>
                            </Chip>
                          ) : currentDetail.oath.trust_level && currentDetail.oath.trust_level > 0 ? (
                            <div className="flex items-center gap-1.5">
                              <span className="text-[10px] text-emerald-400 font-medium bg-emerald-500/10 border border-emerald-500/20 px-1.5 py-0.5 rounded">
                                心境：{currentDetail.oath.mood_name || '平静'} ({currentDetail.oath.mood_buff || '+10%'})
                              </span>
                              <Chip size="sm" variant="secondary" color="warning" className="font-bold text-[11px]">
                                <Chip.Label>
                                  【{currentDetail.oath.trust_title || '同好'}】 Lv.{currentDetail.oath.trust_level}
                                </Chip.Label>
                              </Chip>
                            </div>
                          ) : (
                            <Chip size="sm" variant="soft" color="warning" className="font-semibold text-[11px]">
                              <Chip.Label>
                                {(currentDetail.oath.like_total_exp ?? 1000) >= 1000
                                  ? '一阶满级 · 待完成交心突破'
                                  : '交心待开启 (未解锁)'}
                              </Chip.Label>
                            </Chip>
                          )}
                        </div>

                        {/* 详细内容 */}
                        {!currentDetail.oath.has_trust ? (
                          <div className="rounded-lg bg-surface-secondary/40 border border-separator/40 p-2.5 text-muted text-[11px] leading-relaxed">
                            该修正者暂未开放专属交心系统，修正者好感极致为一阶 Ⅴ 阶满级。
                          </div>
                        ) : currentDetail.oath.trust_level && currentDetail.oath.trust_level > 0 ? (
                          <div className="space-y-1.5">
                            <div className="flex items-center justify-between text-[11px]">
                              <span className="text-muted">交心升级进度</span>
                              <span className="font-mono font-semibold text-foreground">
                                {currentDetail.oath.trust_level >= 5
                                  ? '已达最高等阶 · 至交'
                                  : `${currentDetail.oath.trust_exp || 0} / ${currentDetail.oath.trust_exp_max || 1500} EXP`}
                              </span>
                            </div>
                            <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-secondary/70">
                              <div
                                className="h-full rounded-full bg-gradient-to-r from-amber-500 to-yellow-400 transition-all duration-500"
                                style={{
                                  width: `${
                                    currentDetail.oath.trust_level >= 5
                                      ? 100
                                      : Math.min(
                                          100,
                                          Math.round(
                                            ((currentDetail.oath.trust_exp || 0) /
                                              (currentDetail.oath.trust_exp_max || 1500)) *
                                              100
                                          )
                                        )
                                  }%`,
                                }}
                              />
                            </div>
                            <div className="flex justify-between text-[10px] text-muted/70 pt-0.5">
                              <span>同好 (Lv.1)</span>
                              <span>亲友</span>
                              <span>挚友</span>
                              <span>知己</span>
                              <span>至交 (Lv.5)</span>
                            </div>
                          </div>
                        ) : (
                          <div className="rounded-lg bg-amber-500/5 border border-amber-500/20 p-2.5 text-[11px] text-amber-200/90 leading-relaxed flex items-center justify-between">
                            <span>
                              {(currentDetail.oath.like_total_exp ?? 1000) >= 1000
                                ? '该修正者已开放交心！当前一阶好感已圆满，完成专属突破任务后即可缔结【同好】。'
                                : '该修正者拥有专属交心系统，请先提升一阶好感到 Ⅴ 阶满级以开启交心。'}
                            </span>
                            {(currentDetail.oath.trust_exp || 0) > 0 && (
                              <span className="font-mono text-xs font-bold text-amber-400 whitespace-nowrap ml-2">
                                蓄积交心值: {currentDetail.oath.trust_exp}
                              </span>
                            )}
                          </div>
                        )}
                      </div>

                      {/* 4. 心链剧情进度卡片 */}
                      <div className="rounded-xl border border-separator/70 bg-surface p-4 text-xs space-y-2">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2 font-bold text-foreground">
                            <BookOpen className="size-4 text-sky-400" />
                            <span>心链剧情进度</span>
                          </div>
                          <span className="font-bold text-foreground font-mono">
                            已解锁 {currentDetail.oath.plot_progress} / {currentDetail.oath.plot_max || 5} 幕
                          </span>
                        </div>
                        <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-secondary/70">
                          <div
                            className="h-full rounded-full bg-gradient-to-r from-sky-500 to-indigo-400 transition-all duration-500"
                            style={{
                              width: `${Math.min(
                                100,
                                Math.round(
                                  (currentDetail.oath.plot_progress /
                                    (currentDetail.oath.plot_max || 5)) *
                                    100
                                )
                              )}%`,
                            }}
                          />
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {/* 8. 神格面板：左立绘右神格，动态置顶高亮当前已激活神格（3颗），下方列出全部 9 颗神格节点名称 */}
                {activeSubTab === 'astrolabe' && (
                  <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-start">
                    {/* 左侧角色立绘 */}
                    <div className="md:col-span-5 relative flex h-[410px] w-full items-center justify-center overflow-hidden rounded-xl border border-separator/50 bg-gradient-to-b from-surface-secondary/40 via-surface/15 to-surface-secondary/50 p-2 shadow-inner">
                      <img
                        src={resolveAssetUrl(currentDetail.portrait || `extracted_assets/portraits/${selectedHero.id}.png`)}
                        alt={selectedHero.official_name}
                        className="max-h-[380px] w-auto max-w-full object-contain filter drop-shadow-md select-none transition-transform duration-300 hover:scale-105"
                        onError={(e) => {
                          ;(e.target as HTMLImageElement).src = resolveAssetUrl(selectedHero.avatar)
                        }}
                      />
                      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-surface/80 to-transparent" />
                      <span className="absolute bottom-2 left-3 text-[11px] font-medium text-muted/70 tracking-wide font-mono">
                        NO.{selectedHero.id} · {selectedHero.official_name}
                      </span>
                    </div>

                    {/* 右侧神格数据 */}
                    <div className="md:col-span-7 space-y-3">
                      {/* 动态置顶高亮当前已激活神格 (3颗) */}
                      <div className="rounded-xl border border-accent/40 bg-accent/5 ring-1 ring-accent/30 p-3">
                        <div className="flex items-center gap-2 mb-2">
                          <Sparkles className="size-4 text-accent" />
                          <span className="text-xs font-bold text-foreground">当前已激活神格 (3颗)</span>
                        </div>
                        <div className="grid grid-cols-3 gap-2">
                          {currentDetail.astrolabe.active.length > 0 ? (
                            currentDetail.astrolabe.active.map((node) => (
                              <div
                                key={node.id}
                                className="flex flex-col items-center justify-center rounded-lg border border-accent/50 bg-surface p-2 text-center shadow-xs"
                              >
                                <span className="text-xs font-bold text-accent">{node.name}</span>
                                <span className="text-[10px] text-muted mt-0.5 font-mono">共鸣激活</span>
                              </div>
                            ))
                          ) : (
                            <div className="col-span-3 text-center text-xs text-muted py-2">
                              暂无激活神格
                            </div>
                          )}
                        </div>
                      </div>

                      {/* 全部 9 颗神格节点名称列表 */}
                      <div className="rounded-xl border border-separator/70 bg-surface p-3.5">
                        <h4 className="text-xs font-bold text-muted mb-2.5">全部 9 颗神格共鸣节点</h4>
                        <div className="grid grid-cols-3 gap-2">
                          {currentDetail.astrolabe.all_nodes.map((node) => {
                            const isActive = currentDetail.astrolabe.active.some((a) => a.id === node.id)
                            return (
                              <div
                                key={node.id}
                                className={`flex items-center justify-between rounded-lg border px-2.5 py-1.5 text-xs transition-colors ${
                                  isActive
                                    ? 'border-accent bg-accent/10 font-bold text-accent ring-1 ring-accent/30'
                                    : 'border-separator/60 bg-surface-secondary/30 text-muted'
                                }`}
                              >
                                <span className="truncate">{node.name}</span>
                                {isActive && <CheckCircle2 className="size-3 shrink-0 text-accent ml-1" />}
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </Card>
        </div>
      </div>

      {/* 8. 钥从立绘高精大图鉴赏弹窗 (Lightbox Modal) */}
      {previewServant && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-md p-4 animate-in fade-in duration-200"
          onClick={() => setPreviewServant(null)}
        >
          <div
            className="relative max-w-2xl w-full bg-surface border border-separator rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[92vh]"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 弹窗顶部栏 */}
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-separator/60 bg-surface-secondary/60">
              <div className="flex items-center gap-2.5">
                <Sparkles className="size-5 text-amber-500" />
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-base font-bold text-foreground">{previewServant.name}</h3>
                    <Chip size="sm" variant="soft" color="warning" className="text-[10px] h-5 font-bold">
                      5星专属
                    </Chip>
                  </div>
                  <span className="text-[11px] text-muted">
                    {previewServant.race_name ? `${previewServant.race_name}神系` : '武装钥从'} · 全身高精立绘鉴赏
                  </span>
                </div>
              </div>
              <button
                onClick={() => setPreviewServant(null)}
                className="rounded-lg p-1.5 text-muted hover:text-foreground hover:bg-surface-secondary transition-colors"
                title="关闭 (ESC)"
              >
                <X className="size-5" />
              </button>
            </div>

            {/* 立绘主体展示区 */}
            <div className="flex-1 overflow-auto p-6 flex flex-col items-center justify-center bg-gradient-to-b from-amber-500/5 via-surface/30 to-background min-h-[380px]">
              <img
                src={resolveAssetUrl(previewServant.portrait)}
                alt={previewServant.name}
                className="max-h-[520px] w-auto max-w-full object-contain filter drop-shadow-2xl select-none"
              />
              {previewServant.desc && (
                <div className="mt-4 max-w-lg text-center bg-surface-secondary/50 border border-separator/40 rounded-xl px-4 py-2.5 shadow-inner">
                  <p className="text-xs text-muted/90 leading-relaxed italic">
                    "{previewServant.desc}"
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

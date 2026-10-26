import { useState, useEffect, useMemo, useRef } from 'react'
import {
  BatteryCharging,
  Ban,
  Check,
  ChevronLeft,
  ChevronRight,
  Edit3,
  Eye,
  EyeOff,
  Heart,
  Layers,
  Monitor,
  RefreshCw,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  User,
  X,
  Copy,
  Save,
} from 'lucide-react'
import {
  Button,
  Card,
  Chip,
  Input,
  Modal,
  SearchField,
} from '@heroui/react'
import { PageHeader, PlainSwitch, Section, StatCard } from '../components/kit'
import {
  fetchAccountProfile,
  modifyAccountBasic,
  modifyAccountPeripheral,
  modifyAccountBirthday,
  resolveAssetUrl,
  DEFAULT_ACCOUNT_UID,
  fetchPaySwitch,
  setPaySwitch,
  fetchLaunchConfig,
  saveLaunchConfig,
  type AccountProfileData,
  type StickerWall,
  type PaySwitchData,
  type LaunchConfigData,
} from '../lib/api'
import { useServer } from '../lib/serverContext'
import { buildLaunchArguments, formatLaunchCommand, type LaunchShell } from '../lib/launchCommand'
import { PLAYER_AVATARS, avatarAssetPath, type AvatarItem } from '../lib/avatarAssets'
import stickersCatalog from '../data/stickersCatalog.json'

// 预置 8 大官方背景画卷完整数据，支持离线或连线模式下完整无缝翻页浏览
const DEFAULT_STICKER_WALLS: StickerWall[] = [
  {
    page_id: 4007,
    page_name: '混乱城际线',
    page_desc: '记录下某处景色的贴纸背景，可以在贴纸页面中查看展示。\n——错综复杂的秘密都市，暂得安宁的栖息之所。',
    foreground: 0,
    is_active: true,
    total_stickers: 5,
    stickers: [
      {
        id: 3053,
        name: '主题餐厅',
        desc: '记录了过往美好的贴纸，可以在贴纸页面中查看展示。\n——若是雨变成糖果，房子变成蛋糕，那么茶杯会变成朋友吗？',
        rare: 5,
        location_x: 1411,
        location_y: 2745,
        scale: 5000,
        layer: 1,
        rotate: 0,
      },
      {
        id: 3055,
        name: '梦醒之人',
        desc: '记录了过往美好的贴纸，可以在贴纸页面中查看展示。\n——小昙，我们能去更远的地方了。',
        rare: 5,
        location_x: 3013,
        location_y: 2927,
        scale: 5000,
        layer: 2,
        rotate: 0,
      },
      {
        id: 3054,
        name: '摩天轮',
        desc: '记录了过往美好的贴纸，可以在贴纸页面中查看展示。\n——摩天轮的灯映在脸上，狭小的空间里只能看见你和我，很安心。',
        rare: 5,
        location_x: 7066,
        location_y: 4816,
        scale: 5000,
        layer: 3,
        rotate: 0,
      },
      {
        id: 3052,
        name: '音像摊',
        desc: '记录了过往美好的贴纸，可以在贴纸页面中查看展示。\n——留声机像旋转木马，转起来，就会有音乐。',
        rare: 5,
        location_x: 4652,
        location_y: 1676,
        scale: 5000,
        layer: 4,
        rotate: 0,
      },
      {
        id: 3051,
        name: '露天影院',
        desc: '记录了过往美好的贴纸，可以在贴纸页面中查看展示。\n——遇到错误的时候，就倒带重来，总会抵达结局。',
        rare: 5,
        location_x: 8813,
        location_y: 1843,
        scale: 5000,
        layer: 5,
        rotate: 0,
      },
    ],
  },
  {
    page_id: 4001,
    page_name: '游乐园',
    page_desc: '记录下某处景色的贴纸背景，可以在贴纸页面中查看展示。\n——充满了欢声笑语的游乐园。',
    foreground: 0,
    is_active: false,
    total_stickers: 0,
    stickers: [],
  },
  {
    page_id: 4002,
    page_name: '虚拟战术室',
    page_desc: '记录下某处景色的贴纸背景，可以在贴纸页面中查看展示。\n——用于战术模拟与数据推演的虚拟空间。',
    foreground: 0,
    is_active: false,
    total_stickers: 0,
    stickers: [],
  },
  {
    page_id: 4003,
    page_name: '曙光夏日',
    page_desc: '记录下某处景色的贴纸背景，可以在贴纸页面中查看展示。\n——盛夏乐园，浪花环绕。',
    foreground: 0,
    is_active: false,
    total_stickers: 0,
    stickers: [],
  },
  {
    page_id: 4004,
    page_name: '弥楼衍大厅',
    page_desc: '记录下某处景色的贴纸背景，可以在贴纸页面中查看展示。\n——弥楼衍的平静一角。',
    foreground: 0,
    is_active: false,
    total_stickers: 0,
    stickers: [],
  },
  {
    page_id: 4006,
    page_name: '桥畔一隅',
    page_desc: '记录下某处景色的贴纸背景，可以在贴纸页面中查看展示。\n——画卷中的新泰镇迎来了新的客人。',
    foreground: 0,
    is_active: false,
    total_stickers: 0,
    stickers: [],
  },
  {
    page_id: 4008,
    page_name: '修正者的饭桌',
    page_desc: '记录下某处景色的贴纸背景，可以在贴纸页面中查看展示。\n——无论接下来要做什么，首先要做的事情只有一件：填饱肚子。',
    foreground: 0,
    is_active: false,
    total_stickers: 0,
    stickers: [],
  },
  {
    page_id: 4009,
    page_name: '共度良宵',
    page_desc: '记录下某处景色的贴纸背景，可以在贴纸页面中查看展示。\n——纵饮屠苏迎岁新，醉鞭策马跃青云。',
    foreground: 0,
    is_active: false,
    total_stickers: 0,
    stickers: [],
  },
]

function getStickerFlavorText(desc?: string): string {
  if (!desc) return ''
  if (desc.includes('——')) {
    const parts = desc.split('——')
    return parts[parts.length - 1].trim()
  }
  return desc.replace(/^记录了.*?展示。?\s*/, '').trim() || desc
}

export default function AccountsPanel() {
  const { activeUid, isOnline, resVersionInfo } = useServer()

  const [profile, setProfile] = useState<AccountProfileData | null>(null)
  const [profileLoading, setProfileLoading] = useState(false)
  const [actionMsg, setActionMsg] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  // 昵称与签名编辑态
  const [editingNick, setEditingNick] = useState(false)
  const [nickInput, setNickInput] = useState('')
  const [editingSign, setEditingSign] = useState(false)
  const [signInput, setSignInput] = useState('')
  const [editingBirthday, setEditingBirthday] = useState(false)
  const [birthdayMonth, setBirthdayMonth] = useState(1)
  const [birthdayDay, setBirthdayDay] = useState(1)
  const [birthdaySaving, setBirthdaySaving] = useState(false)
  const accountUid = activeUid || DEFAULT_ACCOUNT_UID
  const accountUidRef = useRef(accountUid)
  accountUidRef.current = accountUid
  const birthdayRequestId = useRef(0)
  const profileRequestId = useRef(0)
  const monthDays = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  const accountBirthday = profile?.uid === accountUid ? profile.birthday : undefined

  useEffect(() => {
    birthdayRequestId.current += 1
    setEditingBirthday(false)
    setBirthdaySaving(false)
  }, [accountUid])

  useEffect(() => () => {
    birthdayRequestId.current += 1
    profileRequestId.current += 1
  }, [])


  // 头像选择器弹窗与白名单列表
  const [isAvatarModalOpen, setIsAvatarModalOpen] = useState(false)
  const avatarsList = PLAYER_AVATARS
  const [avatarKeyword, setAvatarKeyword] = useState('')
  const [avatarSubmitting, setAvatarSubmitting] = useState(false)
  const [avatarError, setAvatarError] = useState<string | null>(null)

  // 贴纸画卷多页翻转切换状态
  const [currentWallIndex, setCurrentWallIndex] = useState(0)
  const [selectedStickerId, setSelectedStickerId] = useState<number | null>(null)
  const [hoveredStickerId, setHoveredStickerId] = useState<number | null>(null)
  const [showStickerLedger, setShowStickerLedger] = useState(false)
  const wallPageByUidRef = useRef<Map<number, number>>(new Map())

  const wallsList = useMemo<StickerWall[]>(() => {
    if (profile?.sticker_walls && profile.sticker_walls.length > 0) {
      return profile.sticker_walls
    }
    if (profile?.sticker_wall) {
      return [
        profile.sticker_wall,
        ...DEFAULT_STICKER_WALLS.filter((w) => w.page_id !== profile.sticker_wall?.page_id),
      ]
    }
    return DEFAULT_STICKER_WALLS
  }, [profile])

  const rememberedPageId = profile?.uid == null
    ? undefined
    : wallPageByUidRef.current.get(profile.uid)
  const rememberedWallIndex = rememberedPageId == null
    ? -1
    : wallsList.findIndex((wall) => wall.page_id === rememberedPageId)
  const activeWallIndex = wallsList.findIndex((wall) => wall.is_active)
  const clampedWallIndex = wallsList.length > 0
    ? Math.min(Math.max(currentWallIndex, 0), wallsList.length - 1)
    : 0
  // Resolve from page_id during render as well as in the effect. This avoids one
  // frame of the old numeric index when the server moves the active wall to front.
  const visibleWallIndex = wallsList.length > 0
    ? (rememberedWallIndex >= 0
      ? rememberedWallIndex
      : (activeWallIndex >= 0 ? activeWallIndex : clampedWallIndex))
    : 0
  const currentWall = wallsList[visibleWallIndex] || wallsList[0] || DEFAULT_STICKER_WALLS[0]
  const currentWallBackgroundMeta = (stickersCatalog.backgrounds as Record<string, { width?: number; height?: number }>)[String(currentWall.page_id)]
  const viewportWidth = Number(currentWallBackgroundMeta?.width) || 2048
  const viewportHeight = Number(currentWallBackgroundMeta?.height) || 1170

  // Profile responses can replace the wall list while the old index is still selected.
  // Preserve an explicitly browsed page for the same UID; otherwise prefer the server's
  // active page and clamp to the available list so the slider never points at a blank pane.
  useEffect(() => {
    if (profile?.uid == null || wallsList.length === 0) {
      setCurrentWallIndex(0)
      setSelectedStickerId(null)
      setHoveredStickerId(null)
      return
    }
    const nextIndex = rememberedWallIndex >= 0
      ? rememberedWallIndex
      : (activeWallIndex >= 0 ? activeWallIndex : clampedWallIndex)
    setCurrentWallIndex((previous) => previous === nextIndex ? previous : nextIndex)
    setSelectedStickerId(null)
    setHoveredStickerId(null)
  }, [profile?.uid, wallsList, currentWallIndex, rememberedWallIndex, activeWallIndex, clampedWallIndex])

  const inspectedStickerId = hoveredStickerId ?? selectedStickerId
  const inspectedSticker = useMemo(() => {
    if (!inspectedStickerId) return null
    return currentWall.stickers?.find((s: any) => s.id === inspectedStickerId) || null
  }, [currentWall, inspectedStickerId])

  const rememberWall = (index: number) => {
    const uid = profile?.uid
    const wall = wallsList[index]
    if (uid != null && wall) {
      wallPageByUidRef.current.set(uid, wall.page_id)
    }
  }

  const handlePrevWall = () => {
    setSelectedStickerId(null)
    setHoveredStickerId(null)
    const nextIndex = wallsList.length > 0
      ? (visibleWallIndex > 0 ? visibleWallIndex - 1 : wallsList.length - 1)
      : 0
    rememberWall(nextIndex)
    setCurrentWallIndex(nextIndex)
  }

  const handleNextWall = () => {
    setSelectedStickerId(null)
    setHoveredStickerId(null)
    const nextIndex = wallsList.length > 0 && visibleWallIndex < wallsList.length - 1
      ? visibleWallIndex + 1
      : 0
    rememberWall(nextIndex)
    setCurrentWallIndex(nextIndex)
  }

  // 启动参数与运行调谐配置状态
  const [launchConfig, setLaunchConfig] = useState<LaunchConfigData>({
    log_level: 'INFO',
    replay_enabled: false,
    replay_path: '',
    host_ip: '',
    https_port: 443,
    gw_port: 8102,
    game_port: 8105,
    db_path: 'account.db',
    client_assets_dir: '',
    available_replays: [],
    has_replay_material: false,
    detected_host_ip: '',
  })
  const [_launchLoading, setLaunchLoading] = useState(false)
  const [savingLaunch, setSavingLaunch] = useState(false)
  const [copiedCommand, setCopiedCommand] = useState(false)
  const [launchShell, setLaunchShell] = useState<LaunchShell>('cmd')

  const loadLaunchConfig = async () => {
    setLaunchLoading(true)
    try {
      const res = await fetchLaunchConfig()
      if (res && res.code === 0 && res.data) {
        setLaunchConfig((prev) => ({
          ...prev,
          ...res.data,
        }))
      }
    } catch {
      // 离线环境优雅降级
    } finally {
      setLaunchLoading(false)
    }
  }

  useEffect(() => {
    loadLaunchConfig()
  }, [])

  const handleSaveLaunchConfig = async () => {
    setSavingLaunch(true)
    try {
      const res = await saveLaunchConfig(launchConfig)
      if (res && res.code === 0 && res.data) {
        setLaunchConfig((prev) => ({ ...prev, ...res.data }))
        setActionMsg('启动参数配置已保存' + (res.msg ? ` (${res.msg})` : ''))
      } else {
        setActionError(res?.msg || '保存启动配置失败')
      }
    } catch (e: any) {
      setActionError(e?.message || '保存启动配置异常')
    } finally {
      setSavingLaunch(false)
    }
  }

  // 动态生成启动命令
  const generatedCommand = useMemo(() => {
    return formatLaunchCommand(buildLaunchArguments(launchConfig), launchShell)
  }, [launchConfig, launchShell])

  const handleCopyCommand = () => {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(generatedCommand).then(() => {
        setCopiedCommand(true)
        setActionMsg('启动命令已复制至剪贴板！')
        setTimeout(() => setCopiedCommand(false), 2500)
      }).catch(() => {
        setActionError('复制失败，请手动复制命令')
      })
    }
  }

  // 开放 PAY 开关状态与控制逻辑
  const [paySwitchData, setPaySwitchData] = useState<PaySwitchData | null>(null)
  const [paySwitchLoading, setPaySwitchLoading] = useState(false)

  const loadPaySwitch = async () => {
    try {
      const res = await fetchPaySwitch()
      if (res && res.code === 0 && res.data) {
        setPaySwitchData(res.data)
      }
    } catch {
      // 离线时不打断
    }
  }

  useEffect(() => {
    loadPaySwitch()
  }, [])

  const handleTogglePaySwitch = async (val: boolean) => {
    setPaySwitchLoading(true)
    try {
      const res = await setPaySwitch(val)
      if (res && res.code === 0 && res.data) {
        setPaySwitchData(res.data)
        setActionMsg(res.msg || (val ? '已开启游戏内直购 (PAY)' : '已关闭游戏内直购 (PAY)'))
      } else {
        setActionError(res?.msg || '操作失败')
      }
    } catch (e: any) {
      setActionError(e?.message || '网络请求异常')
    } finally {
      setPaySwitchLoading(false)
    }
  }

  // 严格守卫：仅在 iOS 平台 且 229 构建包下允许显示
  const isPaySwitchVisible = useMemo(() => {
    // 1. 版本必须严格为 229
    const isVer229 = (resVersionInfo?.current_version === '229')
    if (!isVer229) return false

    // 2. 平台必须为 iOS (支持 iOS 访问端 Safari/WebKit，或服务端识别到的 iOS 客户端)
    const isIOSBrowser = (
      typeof navigator !== 'undefined' &&
      (/iPad|iPhone|iPod/.test(navigator.userAgent) ||
       (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1))
    )
    const isIOSClient = (paySwitchData?.client_platform === 'ios')

    return isIOSBrowser || isIOSClient
  }, [resVersionInfo?.current_version, paySwitchData?.client_platform])

  const loadProfile = async (uid = activeUid || DEFAULT_ACCOUNT_UID) => {
    const requestId = ++profileRequestId.current
    const isCurrent = () => requestId === profileRequestId.current && accountUidRef.current === uid
    setProfileLoading(true)
    setActionError(null)
    try {
      const res = await fetchAccountProfile(uid)
      if (!isCurrent()) return
      if (res && res.code === 0 && res.data) {
        setProfile(res.data)
        setNickInput(res.data.nickname || res.data.nick || '海拉')
        setSignInput(res.data.sign || '')
        return
      }
    } catch {
      // 离线或服务未启动时走优雅降级
    } finally {
      if (isCurrent()) setProfileLoading(false)
    }
    if (!isCurrent()) return

    // 离线环境优雅降级：与 account_test.db 当前模拟库真实状态严格对齐
    setProfile({
      uid,
      nickname: '海拉',
      level: 93,
      exp: 173830,
      fatigue: 450,
      max_fatigue: 240,
      sign: '',
      avatar_id: 2200106,
      icon_frame_id: 2935,
      status: isOnline ? 'online' : 'offline',
      is_online: isOnline,
      client_device: isOnline ? 'Windows 在线' : '未连接',
      skin_count: 33,
      board_hero: { id: 1194, name: '海拉·悼亡之蝶' },
      profile_tags: [
        { id: 7019, name: '远行客' },
        { id: 7020, name: '小小领头羊' },
        { id: 7029, name: '方块规划师' },
      ],
      fatigue_detail: {
        current: 450,
        max: 240,
        is_full: true,
        seconds_to_next: 0,
        seconds_to_full: 0,
        next_point_str: '已满',
        full_recovery_str: '已完全回满',
      },
      sticker_wall: DEFAULT_STICKER_WALLS[0],
      sticker_walls: DEFAULT_STICKER_WALLS,
    })
    setNickInput('海拉')
    setSignInput('')
  }

  useEffect(() => {
    loadProfile(activeUid)
  }, [activeUid, isOnline])

  const showToast = (msg: string) => {
    setActionMsg(msg)
    setTimeout(() => setActionMsg(null), 3500)
  }

  const handleSaveNick = async () => {
    if (!nickInput.trim()) return
    const targetUid = activeUid || DEFAULT_ACCOUNT_UID
    try {
      await modifyAccountBasic({ uid: targetUid, nick: nickInput.trim() })
      setEditingNick(false)
      showToast(`昵称已成功修改为「${nickInput.trim()}」`)
      await loadProfile(targetUid)
    } catch (e: any) {
      setActionError(`修改昵称失败: ${e.message}`)
    }
  }

  const handleSaveSign = async () => {
    const targetUid = activeUid || DEFAULT_ACCOUNT_UID
    try {
      await modifyAccountPeripheral({ uid: targetUid, sign: signInput.trim() })
      setEditingSign(false)
      showToast('个性签名已更新')
      await loadProfile(targetUid)
    } catch (e: any) {
      setActionError(`修改签名失败: ${e.message}`)
    }
  }

  const handleSaveBirthday = async (month: number, day: number) => {
    const targetUid = accountUid
    const requestId = ++birthdayRequestId.current
    const isCurrent = () => requestId === birthdayRequestId.current && accountUidRef.current === targetUid
    setBirthdaySaving(true)
    setActionError(null)
    try {
      await modifyAccountBirthday({ uid: targetUid, month, day })
      if (!isCurrent()) return
      setEditingBirthday(false)
      showToast(month === 0 ? '生日已清除，重新登录游戏后可再次设置' : '生日已更新，重新登录游戏后生效')
      await loadProfile(targetUid)
    } catch (e: any) {
      if (isCurrent()) setActionError(`修改生日失败: ${e.message}`)
    } finally {
      if (isCurrent()) setBirthdaySaving(false)
    }
  }

  // 选择白名单头像并落库
  const handleSelectAvatar = async (avatarItem: AvatarItem) => {
    if (avatarSubmitting) return
    setAvatarError(null)
    setActionError(null)
    setAvatarSubmitting(true)
    const targetUid = activeUid || DEFAULT_ACCOUNT_UID
    try {
      await modifyAccountPeripheral({ uid: targetUid, avatar_id: avatarItem.id })
      setIsAvatarModalOpen(false)
      showToast(`头像已更换为「${avatarItem.name}」`)
      await loadProfile(targetUid)
    } catch (e: any) {
      setAvatarError(`修改头像失败: ${e.message}`)
      setActionError(`修改头像失败: ${e.message}`)
    } finally {
      setAvatarSubmitting(false)
    }
  }

  const openAvatarPicker = () => {
    setAvatarError(null)
    setIsAvatarModalOpen(true)
  }

  // 过滤后的白名单头像
  const filteredAvatars = useMemo(() => {
    if (!avatarKeyword.trim()) return avatarsList
    const q = avatarKeyword.toLowerCase()
    return avatarsList.filter(
      (a) => a.name.toLowerCase().includes(q) || String(a.id).includes(q)
    )
  }, [avatarsList, avatarKeyword])

  const avatarId = profile?.avatar_id || profile?.portrait || 2200106
  const frameId = profile?.icon_frame_id || profile?.icon_frame || 2935

  return (
    <div className="space-y-6">
      <PageHeader
        title="账号数据"
        description="当前单人沙盒指挥官核心画像与个性化档案。包含实时体力自然恢复对账、外观装扮与全域功能开关拓展坞。"
        actions={
          <Button
            size="sm"
            variant="secondary"
            className="gap-1.5"
            onPress={() => loadProfile()}
            isDisabled={profileLoading}
          >
            <RefreshCw className={`size-4 ${profileLoading ? 'animate-spin' : ''}`} />
            刷新数据
          </Button>
        }
      />

      {/* 快捷操作反馈提示 */}
      {actionMsg && (
        <div className="flex items-center gap-2 rounded-xl bg-success-soft px-4 py-2.5 text-sm text-success-soft-foreground animate-in fade-in">
          <Check className="size-4 shrink-0" />
          <span>{actionMsg}</span>
        </div>
      )}
      {actionError && (
        <div className="flex items-center gap-2 rounded-xl bg-danger-soft px-4 py-2.5 text-sm text-danger-soft-foreground animate-in fade-in">
          <Ban className="size-4 shrink-0" />
          <span>{actionError}</span>
        </div>
      )}

      {/* 1. 顶部长矩形卡片：真实佩戴头像/头像框 + 玩家名/个签稍向右移 + 中部佩戴称号 */}
      <Card className="p-6">
        <Card.Content className="p-0">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
            {/* 左侧：双层真实头像框 + 档案文本向右微移 */}
            <div className="flex items-center gap-5">
              {/* 复合头像展示区：内层真实头像 + 外层真实头像框 */}
              <Button isIconOnly variant="ghost"
                className="group relative size-20 shrink-0 p-0 cursor-pointer select-none"
                onPress={openAvatarPicker}
                aria-label="更换头像"
              >
                {/* 底层圆形背景与图标：图片缺失或加载中时绝不呈现空白 */}
                <div className="absolute inset-1.5 flex size-[68px] items-center justify-center rounded-full bg-surface-secondary text-muted shadow-inner border border-separator/60">
                  <User className="size-8 opacity-60" />
                </div>

                {/* 内层头像图片 */}
                <img
                  src={resolveAssetUrl(avatarAssetPath(avatarId))}
                  alt="当前头像"
                  className="absolute inset-1.5 size-[68px] rounded-full object-cover shadow-inner transition-transform group-hover:scale-95"
                  onLoad={e => { e.currentTarget.style.opacity = '1' }}
                  onError={(e) => {
                    ;(e.target as HTMLElement).style.opacity = '0'
                  }}
                />
                {/* 外层头像框贴图 */}
                <img
                  src={resolveAssetUrl(`extracted_assets/avatars/frames/${frameId}.png`)}
                  alt="当前头像框"
                  className="pointer-events-none absolute inset-0 size-full object-contain"
                  onError={(e) => {
                    ;(e.target as HTMLElement).style.opacity = '0'
                  }}
                />
                {/* 鼠标悬停换装蒙层 */}
                <div className="absolute inset-1.5 flex size-[68px] items-center justify-center rounded-full bg-black/50 opacity-0 transition-opacity group-hover:opacity-100">
                  <span className="text-[10px] font-medium text-white">更换</span>
                </div>
              </Button>

              {/* 玩家档案文本区：向右适当微调平移 */}
              <div className="space-y-2">
                {/* 昵称行与操作 */}
                <div className="flex flex-wrap items-center gap-2">
                  {editingNick ? (
                    <div className="flex items-center gap-1.5">
                      <Input
                        value={nickInput}
                        onChange={(e) => setNickInput(e.target.value)}
                        className="w-44"
                        placeholder="输入新昵称"
                      />
                      <Button size="sm" variant="secondary" onPress={handleSaveNick}>
                        保存
                      </Button>
                      <Button size="sm" variant="ghost" onPress={() => setEditingNick(false)}>
                        取消
                      </Button>
                    </div>
                  ) : (
                    <>
                      <span className="text-lg font-bold text-foreground">
                        {profile?.nickname || profile?.nick || '海拉'}
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        isIconOnly
                        aria-label="修改昵称"
                        onPress={() => setEditingNick(true)}
                      >
                        <Edit3 className="size-3.5" />
                      </Button>
                    </>
                  )}

                  <Chip size="sm" variant="soft" color="accent">
                    <Chip.Label>UID: {activeUid || DEFAULT_ACCOUNT_UID}</Chip.Label>
                  </Chip>
                  <Chip size="sm" variant="soft" color="success">
                    <Chip.Label>Lv.{profile?.level || 93}</Chip.Label>
                  </Chip>
                </div>

                {/* 个性签名行与操作 */}
                <div className="flex items-center gap-2 text-xs text-muted">
                  {editingSign ? (
                    <div className="flex items-center gap-1.5">
                      <Input
                        value={signInput}
                        onChange={(e) => setSignInput(e.target.value)}
                        className="w-72"
                        placeholder="个性签名..."
                      />
                      <Button size="sm" variant="secondary" onPress={handleSaveSign}>
                        保存
                      </Button>
                      <Button size="sm" variant="ghost" onPress={() => setEditingSign(false)}>
                        取消
                      </Button>
                    </div>
                  ) : (
                    <>
                      <span className="max-w-md truncate">
                        {profile?.sign || '暂无签名'}
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        isIconOnly
                        aria-label="修改签名"
                        onPress={() => { setSignInput(profile?.sign ?? ''); setEditingSign(true) }}
                      >
                        <Edit3 className="size-3" />
                      </Button>
                    </>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span>生日</span>
                  {editingBirthday ? (
                    <>
                      <select
                        aria-label="生日月份"
                        className="rounded-lg border border-separator bg-surface px-2 py-1 text-foreground"
                        value={birthdayMonth}
                        disabled={birthdaySaving}
                        onChange={(e) => {
                          const month = Number(e.target.value)
                          setBirthdayMonth(month)
                          setBirthdayDay((day) => Math.min(day, monthDays[month - 1]))
                        }}
                      >
                        {Array.from({ length: 12 }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1}月</option>)}
                      </select>
                      <select
                        aria-label="生日日期"
                        className="rounded-lg border border-separator bg-surface px-2 py-1 text-foreground"
                        value={birthdayDay}
                        disabled={birthdaySaving}
                        onChange={(e) => setBirthdayDay(Number(e.target.value))}
                      >
                        {Array.from({ length: monthDays[birthdayMonth - 1] }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1}日</option>)}
                      </select>
                      <Button size="sm" variant="secondary" isDisabled={birthdaySaving} onPress={() => handleSaveBirthday(birthdayMonth, birthdayDay)}>
                        {birthdaySaving ? '保存中…' : '保存生日'}
                      </Button>
                      <Button size="sm" variant="ghost" isDisabled={birthdaySaving} onPress={() => setEditingBirthday(false)}>取消</Button>
                      {(accountBirthday?.month || profile?.birthday_invalid) ? (
                        <Button size="sm" variant="ghost" isDisabled={birthdaySaving} onPress={() => handleSaveBirthday(0, 0)}>清除生日</Button>
                      ) : null}
                    </>
                  ) : (
                    <>
                      <span>{!accountBirthday ? '—' : profile?.birthday_invalid ? '需重新设置' : accountBirthday.month ? `${accountBirthday.month}月${accountBirthday.day}日` : '未设置'}</span>
                      <Button
                        size="sm" variant="ghost" isIconOnly aria-label="修改生日"
                        isDisabled={!accountBirthday || profileLoading || birthdaySaving || !isOnline}
                        onPress={() => {
                          setBirthdayMonth(accountBirthday?.month || 1)
                          setBirthdayDay(accountBirthday?.day || 1)
                          setEditingBirthday(true)
                        }}
                      >
                        <Edit3 className="size-3" />
                      </Button>
                    </>
                  )}
                </div>
              </div>
            </div>

            {/* 中部：玩家佩戴官方称号展示区（动态支持 0~3 个） */}
            <div className="flex flex-col items-start gap-1.5 rounded-2xl bg-surface-secondary/50 px-4 py-2.5 lg:items-center">
              <div className="flex items-center gap-1 text-[11px] font-semibold tracking-wider text-muted uppercase">
                <span>当前佩戴称号</span>
                <span className="font-mono text-xs text-foreground/70">
                  ({profile?.profile_tags ? Math.min(3, profile.profile_tags.length) : 0}/3)
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {profile?.profile_tags && profile.profile_tags.length > 0 ? (
                  profile.profile_tags.slice(0, 3).map((tag) => (
                    <Chip key={tag.id} size="sm" variant="soft" color="accent">
                      <Chip.Label>{tag.name}</Chip.Label>
                    </Chip>
                  ))
                ) : (
                  <span className="text-xs text-muted">暂未佩戴任何称号</span>
                )}
              </div>
            </div>

            {/* 右侧：保持克制纯净的沙盒标识与头像选择入口 */}
            <div className="flex items-center gap-3 self-end lg:self-center">
              <Button
                size="sm"
                variant="secondary"
                className="gap-1.5"
                onPress={openAvatarPicker}
              >
                <ShieldCheck className="size-4 text-accent" />
                更换头像
              </Button>
              <Chip size="sm" variant="soft" color="default">
                <Chip.Label>沙盒主控档案</Chip.Label>
              </Chip>
            </div>
          </div>
        </Card.Content>
      </Card>

      {/* 2. 下方四个小卡片：体力自然恢复倒计时、修正者换装统计、当前看板娘、GM服连接监控 */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {/* 卡片 1: 当前体力与精确恢复时间 */}
        <StatCard
          label="体力状态与恢复"
          value={`${profile?.fatigue ?? 450} / ${profile?.max_fatigue ?? 240}`}
          unit="PT"
          icon={BatteryCharging}
          tone={profile?.fatigue && profile.fatigue < (profile.max_fatigue ?? 240) ? 'warning' : 'accent'}
          delta={profile?.fatigue_detail?.is_full ? '已完全回满' : '恢复中'}
          deltaTone={profile?.fatigue_detail?.is_full ? 'success' : 'warning'}
          deltaNote={
            profile?.fatigue_detail?.is_full
              ? '已完全回满 (静止态)'
              : `下一点: ${profile?.fatigue_detail?.next_point_str || '--'} · 回满: ${profile?.fatigue_detail?.full_recovery_str || '--'}`
          }
        />

        {/* 卡片 2: 修正者换装数量（排除 1001 管理员换装） */}
        <StatCard
          label="修正者已解锁换装"
          value={String(profile?.skin_count ?? 33)}
          unit="件"
          icon={Sparkles}
          tone="warning"
          delta="纯净统计"
          deltaTone="default"
          deltaNote="已排除 1001 管理员常服等非角色换装"
        />

        {/* 卡片 3: 当前主界面看板娘 */}
        <StatCard
          label="主界面看板娘"
          value={profile?.board_hero?.name || '海拉·悼亡之蝶'}
          icon={Heart}
          tone="danger"
          delta={`ID: ${profile?.board_hero?.id || 1194}`}
          deltaTone="accent"
          deltaNote="主界面当前侍奉修正者"
        />

        {/* 卡片 4: GM服连接监控（未连接时纯蓝展示） */}
        <StatCard
          label="GM服连接状态"
          value={isOnline ? (profile?.client_device || '已连接') : '未连接'}
          icon={Monitor}
          tone={isOnline ? 'success' : 'accent'}
          delta={isOnline ? '通信正常' : '未连接'}
          deltaTone={isOnline ? 'success' : 'accent'}
          deltaNote={
            isOnline
              ? (profile?.is_online ? '单机客户端网络链路通信正常' : 'GM服务端通信正常')
              : 'GM服务端未启动或连接断开 (8105/8102)'
          }
        />
      </div>

      {/* 3. 底部联动功能区：左侧个性化贴纸画卷展示，右侧紧凑开关拓展坞 */}
      <Section
        title="个性化展示与快捷控制"
        description="玩家贴纸画册空间排版可视化展示与单人沙盒快捷运行环境控制台。"
      >
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          {/* 左侧：玩家个性化贴纸画卷展示卡片 (1:1 比例) */}
          <Card className="p-5 flex flex-col justify-between">
            <Card.Header className="pb-3 border-b border-separator/70 flex flex-col gap-1.5">
              {/* 标题栏主行：全部控件水平居中严格单行对齐 */}
              <div className="flex items-center justify-between gap-2 w-full">
                <div className="flex items-center gap-2 shrink-0">
                  <Layers className="size-4 text-accent shrink-0" />
                  <span className="text-sm font-bold text-foreground whitespace-nowrap">贴纸墙</span>
                  <Chip size="sm" variant="soft" color="accent" className="h-6.5 text-[11px] shrink-0">
                    <Chip.Label>{currentWall.page_name}</Chip.Label>
                  </Chip>
                  {currentWall.is_active && (
                    <Chip size="sm" variant="soft" color="success" className="h-6.5 text-[11px] shrink-0">
                      <Chip.Label>当前佩戴</Chip.Label>
                    </Chip>
                  )}
                </div>

                <div className="flex items-center gap-1.5 shrink-0">
                  {/* 贴纸墙翻卷切换按键组 */}
                  <div className="flex items-center rounded-lg border border-separator/70 bg-surface-secondary/50 p-0.5 h-6.5 shrink-0">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-5.5 px-1.5 text-xs text-muted hover:text-foreground gap-0.5"
                      onPress={handlePrevWall}
                    >
                      <ChevronLeft className="size-3" />
                      <span>上一卷</span>
                    </Button>
                    <span className="text-[11px] font-mono font-medium text-muted px-1.5 whitespace-nowrap">
                      {visibleWallIndex + 1}/{wallsList.length}
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-5.5 px-1.5 text-xs text-muted hover:text-foreground gap-0.5"
                      onPress={handleNextWall}
                    >
                      <span>下一卷</span>
                      <ChevronRight className="size-3" />
                    </Button>
                  </div>

                  <Chip size="sm" variant="soft" color={currentWall.stickers.length > 0 ? 'success' : 'default'} className="h-6.5 text-[11px] shrink-0">
                    <Chip.Label>{currentWall.stickers.length} 枚贴纸</Chip.Label>
                  </Chip>

                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6.5 px-2 text-xs gap-1 text-muted hover:text-foreground shrink-0"
                    onPress={() => setShowStickerLedger((prev) => !prev)}
                  >
                    {showStickerLedger ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                    <span>{showStickerLedger ? '收起' : '清单'}</span>
                  </Button>
                </div>
              </div>

              {/* 标题栏副行：画卷原案简介单行截断 */}
              <div className="text-[11px] text-muted truncate mt-0.5">
                {currentWall.page_desc || '错综复杂的秘密都市，暂得安宁的栖息之所。'}
              </div>
            </Card.Header>

            <Card.Content className="pt-4 space-y-3">
              {/* 贴纸墙展示视口：按背景目录比例完整呈现，支持多画卷平滑翻页 */}
              <div
                onClick={() => setSelectedStickerId(null)}
                className="relative group/viewport w-full rounded-xl overflow-hidden border border-separator/60 shadow-md bg-surface-secondary/20 select-none cursor-default"
                style={{ aspectRatio: `${viewportWidth} / ${viewportHeight}` }}
              >
                {/* 悬浮边缘物理大圆钮：切换至上一画卷 */}
                <button
                  type="button"
                  onClick={handlePrevWall}
                  className="absolute left-2.5 top-1/2 -translate-y-1/2 z-30 flex size-8.5 items-center justify-center rounded-full bg-black/60 text-white backdrop-blur-md border border-white/20 shadow-xl transition-all duration-200 opacity-60 hover:opacity-100 hover:scale-110 active:scale-95 cursor-pointer"
                  title="切换至上一贴纸墙"
                >
                  <ChevronLeft className="size-5" />
                </button>

                {/* 悬浮边缘物理大圆钮：切换至下一画卷 */}
                <button
                  type="button"
                  onClick={handleNextWall}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 z-30 flex size-8.5 items-center justify-center rounded-full bg-black/60 text-white backdrop-blur-md border border-white/20 shadow-xl transition-all duration-200 opacity-60 hover:opacity-100 hover:scale-110 active:scale-95 cursor-pointer"
                  title="切换至下一贴纸墙"
                >
                  <ChevronRight className="size-5" />
                </button>

                {/* 贴纸墙轮播滑块容器：完整背景比例展示，左右滑动切换各个贴纸墙 */}
                <div
                  className="flex h-full w-full transition-transform duration-300 ease-out"
                  style={{ transform: `translateX(-${visibleWallIndex * 100}%)` }}
                >
                  {wallsList.map((wall, wIdx) => (
                    <div
                      key={`${wall.page_id}-${wIdx}`}
                      className="relative h-full w-full shrink-0 select-none overflow-hidden"
                    >
                      {/* 原生游戏背景画卷：无黑边贴合容器 */}
                      <img
                        src={resolveAssetUrl(`extracted_assets/stickers/backgrounds/${wall.page_id}.png`)}
                        alt={wall.page_name}
                        className="absolute inset-0 w-full h-full object-cover select-none pointer-events-none"
                        onError={(e) => {
                          const target = e.currentTarget
                          if (target.dataset.fallbackApplied === 'true') return
                          target.dataset.fallbackApplied = 'true'
                          target.src = resolveAssetUrl('extracted_assets/stickers/backgrounds/4007.png')
                        }}
                      />

                      {/* 原生前景层：仅渲染当前资产目录中已确认的 11001。 */}
                      {Number(wall.foreground) === 11001 && (
                        <img
                          src={resolveAssetUrl('extracted_assets/stickers/foregrounds/stickerfg_l_11001.png')}
                          alt=""
                          aria-hidden="true"
                          className="absolute inset-0 z-20 h-full w-full object-cover select-none pointer-events-none"
                        />
                      )}

                      {/* 动态原生贴纸渲染层 */}
                      {(wall.stickers || []).map((stk: any, idx: number) => {
                        const stkMeta = (stickersCatalog.stickers as Record<string, any>)[String(stk.id)]
                        const leftPct = (stk.location_x / 100).toFixed(2)
                        const bottomPct = (stk.location_y / 100).toFixed(2)
                        const scaleMultiplier = (stk.scale || 5000) / 5000
                        const baseWidthPct = stkMeta?.width_pct || 20
                        const finalWidthPct = baseWidthPct * scaleMultiplier
                        const rotateDeg = ((stk.rotate || 0) / 10000) * 360
                        const isSelected = selectedStickerId === stk.id
                        const isHovered = hoveredStickerId === stk.id
                        const isInspected = inspectedStickerId === stk.id

                        return (
                          <div
                            key={`${wall.page_id}-${stk.id ?? 'unknown'}-${idx}`}
                            className="absolute group/sticker cursor-pointer select-none transition-transform duration-150"
                            style={{
                              left: `${leftPct}%`,
                              bottom: `${bottomPct}%`,
                              width: `${finalWidthPct.toFixed(2)}%`,
                              zIndex: isInspected ? 40 : (stk.layer || idx + 1),
                              transform: `translate(-50%, 50%) rotate(${rotateDeg}deg)`,
                              transformOrigin: 'center center',
                            }}
                            onMouseEnter={() => setHoveredStickerId(stk.id)}
                            onMouseLeave={() => setHoveredStickerId((prev) => (prev === stk.id ? null : prev))}
                            onClick={(e) => {
                              e.stopPropagation()
                              setSelectedStickerId(isSelected ? null : stk.id)
                            }}
                          >
                            <div className="relative w-full">
                              {/* 原生贴纸 PNG 素材 */}
                              <img
                                src={resolveAssetUrl(`extracted_assets/stickers/items/${stk.id}.png`)}
                                alt={stk.name}
                                className={`w-full h-auto transition-all duration-200 pointer-events-none ${
                                  isSelected
                                    ? 'filter drop-shadow-[0_0_14px_rgba(56,189,248,0.95)] scale-105'
                                    : isHovered
                                      ? 'filter drop-shadow-[0_0_12px_rgba(255,255,255,0.85)] scale-105'
                                      : 'drop-shadow-[0_6px_12px_rgba(0,0,0,0.55)] group-hover/sticker:scale-105'
                                }`}
                                onError={(e) => {
                                  e.currentTarget.style.display = 'none'
                                }}
                              />

                              {/* 贴纸角标 (选中或悬停时显示层级，反向旋转矫正) */}
                              {(isSelected || isHovered) && (
                                <div
                                  className={`absolute -top-2 -right-2 flex size-5 items-center justify-center rounded-full text-[10px] font-bold text-white shadow-xl ring-2 ring-white font-mono transition-all duration-150 ${
                                    isSelected
                                      ? 'bg-cyan-500 scale-110 shadow-cyan-500/50 animate-pulse'
                                      : 'bg-neutral-800/90'
                                  }`}
                                  style={{ transform: `rotate(${-rotateDeg}deg)` }}
                                >
                                  #{stk.layer}
                                </div>
                              )}
                            </div>
                          </div>
                        )
                      })}

                      {/* 若该画卷尚无贴纸，提示文字 */}
                      {(!wall.stickers || wall.stickers.length === 0) && (
                        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                          <div className="rounded-full bg-black/40 backdrop-blur-xs px-3 py-1 text-xs text-white/70 border border-white/10">
                            此画卷暂未装贴贴纸
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                {/* 贴纸画卷底部毛玻璃检视条 (固定纯白文字，轻透纯净风格) */}
                <div
                  className={`absolute bottom-2.5 inset-x-2.5 sm:inset-x-3.5 z-30 flex items-center justify-between gap-3 rounded-xl border border-white/20 bg-black/45 px-3 py-2 text-white shadow-xl backdrop-blur-md transition-all duration-200 ${
                    inspectedSticker
                      ? 'opacity-100 translate-y-0 scale-100 pointer-events-auto'
                      : 'opacity-0 translate-y-2 scale-98 pointer-events-none'
                  }`}
                  onClick={(e) => e.stopPropagation()}
                >
                  {/* 左侧：贴纸缩略图 + 贴纸名称 + 图层编号 + 坐标缩放 (固定纯白文字) */}
                  <div className="flex items-center gap-2.5 min-w-0 shrink-0">
                    {inspectedSticker && (
                      <div className="size-8 rounded-lg bg-white/10 p-0.5 flex items-center justify-center border border-white/20 shrink-0 overflow-hidden">
                        <img
                          src={resolveAssetUrl(`extracted_assets/stickers/items/${inspectedSticker.id}.png`)}
                          alt={inspectedSticker.name}
                          className="size-full object-contain drop-shadow-sm"
                          onError={(e) => {
                            e.currentTarget.style.display = 'none'
                          }}
                        />
                      </div>
                    )}
                    <div className="flex flex-col min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-bold text-white truncate max-w-[120px]">
                          {inspectedSticker?.name}
                        </span>
                        <span className="rounded bg-white/20 px-1 py-0.2 text-[9px] font-mono font-medium text-white border border-white/30">
                          #{inspectedSticker?.layer}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 text-[9px] font-mono text-white/80">
                        <span>({inspectedSticker?.location_x}, {inspectedSticker?.location_y})</span>
                        <span>{(((inspectedSticker?.scale || 5000) / 5000)).toFixed(1)}x</span>
                      </div>
                    </div>
                  </div>

                  {/* 中部：游戏内专属风味引述文案 (固定纯白文字) */}
                  {inspectedSticker?.desc ? (
                    <div className="hidden md:flex flex-1 items-center px-2 min-w-0 border-l border-white/15">
                      <p className="text-[11px] text-white/95 italic line-clamp-1 leading-snug">
                        “{getStickerFlavorText(inspectedSticker.desc)}”
                      </p>
                    </div>
                  ) : (
                    <div className="flex-1" />
                  )}

                  {/* 右侧：锁定状态指示或取消锁定按钮 (固定纯白) */}
                  <div className="flex items-center gap-1.5 shrink-0">
                    {selectedStickerId === inspectedSticker?.id ? (
                      <button
                        type="button"
                        onClick={() => setSelectedStickerId(null)}
                        className="flex items-center gap-1 rounded-md bg-white/20 hover:bg-white/30 px-2 py-0.5 text-[10px] text-white font-medium transition-colors cursor-pointer border border-white/30"
                        title="取消贴纸锁定"
                      >
                        <span>已锁定</span>
                        <X className="size-3" />
                      </button>
                    ) : (
                      <span className="text-[10px] text-white/60 hidden sm:inline font-mono">
                        点击锁定
                      </span>
                    )}
                  </div>
                </div>

                {/* 画布底部居中页码胶囊指示器 */}
                <div
                  className={`absolute bottom-2 inset-x-0 flex items-center justify-center gap-1.5 pointer-events-none z-20 transition-opacity duration-200 ${
                    inspectedSticker ? 'opacity-0' : 'opacity-100'
                  }`}
                >
                  {wallsList.map((wall, idx) => (
                    <div
                      key={`${wall.page_id}-${idx}`}
                      className={`transition-all duration-200 rounded-full ${
                        visibleWallIndex === idx
                          ? 'h-1.5 w-5 bg-white shadow-[0_0_8px_rgba(255,255,255,0.8)]'
                          : 'size-1.5 bg-white/40'
                      }`}
                    />
                  ))}
                </div>
              </div>

              {/* 贴纸图层清单明细表（可展开/折叠） */}
              {showStickerLedger && (
                <div className="rounded-xl border border-separator/70 bg-surface-secondary/40 p-3 space-y-2">
                  <div className="flex items-center justify-between text-xs font-semibold text-foreground">
                    <span className="flex items-center gap-1.5">
                      <SlidersHorizontal className="size-3.5 text-accent" />
                      <span>{currentWall.page_name} - 贴纸图层空间明细</span>
                    </span>
                    <span className="text-[11px] text-muted">
                      {currentWall.stickers.length > 0 ? '点击条目高亮贴纸' : '暂无贴纸数据'}
                    </span>
                  </div>

                  {currentWall.stickers.length > 0 ? (
                    <div className="space-y-1.5 max-h-48 overflow-y-auto cp-scroll pr-1">
                      {currentWall.stickers.map((stk: any, idx: number) => {
                        const isSelected = selectedStickerId === stk.id
                        return (
                          <div
                            key={`${currentWall.page_id}-${stk.id ?? 'unknown'}-${idx}`}
                            onClick={() => setSelectedStickerId(isSelected ? null : stk.id)}
                            className={`flex items-center justify-between rounded-lg px-2.5 py-1.5 text-xs transition-all cursor-pointer border ${
                              isSelected
                                ? 'border-cyan-500/50 bg-cyan-950/40 text-cyan-200'
                                : 'border-separator/50 bg-surface/60 text-foreground hover:bg-surface-secondary'
                            }`}
                          >
                            <div className="flex items-center gap-2">
                              <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-surface-secondary text-muted">
                                #{stk.layer}
                              </span>
                              <span className="font-medium">{stk.name}</span>
                              <span className="text-[10px] text-muted font-mono">({stk.id})</span>
                            </div>
                            <div className="flex items-center gap-3 font-mono text-[11px] text-muted">
                              <span>X: {stk.location_x}</span>
                              <span>Y: {stk.location_y}</span>
                              <span>{(((stk.scale || 5000) / 10000) * 2).toFixed(1)}x</span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  ) : (
                    <div className="py-4 text-center text-xs text-muted">
                      当前画卷暂未装贴贴纸，可切换至其他画卷浏览
                    </div>
                  )}
                </div>
              )}
            </Card.Content>
          </Card>

          {/* 右侧：服务启动参数与运行调谐中枢 (滚动框形式) */}
          <Card className="p-5 flex flex-col justify-between">
            <Card.Header className="pb-3 border-b border-separator/70 flex flex-col gap-1.5">
              <div className="flex items-center justify-between gap-2 w-full">
                <div className="flex items-center gap-2 shrink-0">
                  <Settings2 className="size-4 text-accent shrink-0" />
                  <span className="text-sm font-bold text-foreground whitespace-nowrap">服务启动参数与运行调谐</span>
                </div>

                <Chip size="sm" variant="soft" color="accent" className="h-6.5 text-[11px] shrink-0">
                  <Chip.Label>8 项调谐</Chip.Label>
                </Chip>
              </div>
              <div className="text-[11px] text-muted truncate mt-0.5">
                服务端端口、日志级别与关键路径配置，保存后热重载或写入启动参数
              </div>
            </Card.Header>

            <Card.Content className="pt-3 flex-1 flex flex-col min-h-0">
              {/* 纵向滚动框：固定高度内部流畅滚动，与左侧 16:9 画幅高度工整对齐 */}
              <div className="space-y-2.5 overflow-y-auto cp-scroll pr-1.5 max-h-[310px] flex-1">
                {/* 参数 1: 日志打印调整 */}
                <div className="rounded-xl border border-separator/70 bg-surface/60 p-3 hover:bg-surface-secondary/40 transition-colors space-y-1.5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-semibold text-foreground">日志打印级别</span>
                      <span className="inline-flex items-center px-1.5 py-0.2 rounded text-[10px] font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                        即时生效
                      </span>
                    </div>
                    <div className="flex items-center gap-1 bg-surface-secondary/80 p-0.5 rounded-lg border border-separator/50">
                      {(['INFO', 'DEBUG', 'WARN', 'ERROR'] as const).map((lvl) => (
                        <button
                          key={lvl}
                          type="button"
                          onClick={() => setLaunchConfig((prev) => ({ ...prev, log_level: lvl }))}
                          className={`px-2 py-0.5 text-[10px] font-semibold rounded-md transition-all cursor-pointer ${
                            launchConfig.log_level === lvl
                              ? 'bg-accent text-accent-foreground shadow-xs'
                              : 'text-muted hover:text-foreground'
                          }`}
                        >
                          {lvl}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="text-[11px] text-muted leading-tight">
                    控制台日志详尽程度，设为 DEBUG 输出 10xxx/14xxx/17xxx 协议负载
                  </div>
                </div>

                {/* 参数 2: 重放选择 */}
                <div className={`rounded-xl border border-separator/70 bg-surface/60 p-3 transition-colors space-y-2 ${
                  !launchConfig.has_replay_material ? 'opacity-70' : 'hover:bg-surface-secondary/40'
                }`}>
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5 pr-2">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-semibold text-foreground">全流程重放模式</span>
                        {!launchConfig.has_replay_material ? (
                          <span className="inline-flex items-center px-1.5 py-0.2 rounded text-[10px] font-medium bg-amber-500/15 text-amber-400 border border-amber-500/30">
                            无素材不可用
                          </span>
                        ) : (
                          <span className="inline-flex items-center px-1.5 py-0.2 rounded text-[10px] font-medium bg-cyan-500/15 text-cyan-400 border border-cyan-500/30">
                            检测到 {launchConfig.available_replays?.length || 0} 个素材
                          </span>
                        )}
                      </div>
                      <div className="text-[11px] text-muted leading-tight">
                        {launchConfig.has_replay_material
                          ? '重放抓包通信流量以离线模拟真实环境'
                          : '未检测到可用重放素材 (分析归档包)'}
                      </div>
                    </div>
                    <PlainSwitch
                      label="重放模式"
                      isSelected={launchConfig.replay_enabled && !!launchConfig.has_replay_material}
                      isDisabled={!launchConfig.has_replay_material}
                      onChange={(val) => {
                        setLaunchConfig((prev) => ({
                          ...prev,
                          replay_enabled: val,
                          replay_path: val && !prev.replay_path && prev.available_replays?.[0]
                            ? prev.available_replays[0].path
                            : prev.replay_path,
                        }))
                      }}
                    />
                  </div>
                  {launchConfig.has_replay_material && launchConfig.replay_enabled && (
                    <div className="pt-1">
                      <select
                        value={launchConfig.replay_path}
                        onChange={(e) => setLaunchConfig((prev) => ({ ...prev, replay_path: e.target.value }))}
                        className="w-full text-xs rounded-lg border border-separator/80 bg-surface px-2 py-1.5 text-foreground focus:outline-none focus:border-accent"
                      >
                        <option value="">请选择重放素材目录...</option>
                        {launchConfig.available_replays?.map((r) => (
                          <option key={r.path} value={r.path}>
                            {r.name} ({r.has_https ? 'HTTPS ' : ''}{r.has_tcp ? 'TCP' : ''})
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                </div>

                {/* 参数 3: 宿主机局域网 IP */}
                <div className="rounded-xl border border-separator/70 bg-surface/60 p-3 hover:bg-surface-secondary/40 transition-colors space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-foreground">宿主机局域网 IP</span>
                    {launchConfig.detected_host_ip && launchConfig.host_ip !== launchConfig.detected_host_ip && (
                      <button
                        type="button"
                        onClick={() => setLaunchConfig((prev) => ({ ...prev, host_ip: prev.detected_host_ip || '' }))}
                        className="text-[10px] text-accent hover:underline flex items-center gap-0.5 cursor-pointer"
                      >
                        填入检测 IP ({launchConfig.detected_host_ip})
                      </button>
                    )}
                  </div>
                  <input
                    type="text"
                    value={launchConfig.host_ip}
                    placeholder="例如 192.168.1.100 (留空使用自动探测)"
                    onChange={(e) => setLaunchConfig((prev) => ({ ...prev, host_ip: e.target.value }))}
                    className="w-full text-xs rounded-lg border border-separator/80 bg-surface px-2.5 py-1.5 text-foreground placeholder:text-muted/60 focus:outline-none focus:border-accent font-mono"
                  />
                  <div className="text-[11px] text-muted leading-tight">
                    下发给真机客户端的网关接入 IP，真机局域网联调时必填
                  </div>
                </div>

                {/* 参数 4: SDK 端口 (HTTPS) */}
                <div className="rounded-xl border border-separator/70 bg-surface/60 p-3 hover:bg-surface-secondary/40 transition-colors space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-foreground">SDK 端口 (HTTPS)</span>
                    <span className="text-[10px] text-muted font-mono">默认 443</span>
                  </div>
                  <input
                    type="number"
                    value={launchConfig.https_port || 443}
                    onChange={(e) => setLaunchConfig((prev) => ({ ...prev, https_port: parseInt(e.target.value) || 443 }))}
                    className="w-full text-xs rounded-lg border border-separator/80 bg-surface px-2.5 py-1.5 text-foreground focus:outline-none focus:border-accent font-mono"
                  />
                  <div className="text-[11px] text-muted leading-tight">
                    客户端 SDK 登录验证、公告及热更资源清单分发端口
                  </div>
                </div>

                {/* 参数 5: TCP 端口 (Gateway 网关) */}
                <div className="rounded-xl border border-separator/70 bg-surface/60 p-3 hover:bg-surface-secondary/40 transition-colors space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-foreground">TCP 端口 (网关)</span>
                    <span className="text-[10px] text-muted font-mono">默认 8102</span>
                  </div>
                  <input
                    type="number"
                    value={launchConfig.gw_port || 8102}
                    onChange={(e) => setLaunchConfig((prev) => ({ ...prev, gw_port: parseInt(e.target.value) || 8102 }))}
                    className="w-full text-xs rounded-lg border border-separator/80 bg-surface px-2.5 py-1.5 text-foreground focus:outline-none focus:border-accent font-mono"
                  />
                  <div className="text-[11px] text-muted leading-tight">
                    客户端握手接入与首登鉴权协议通道端口
                  </div>
                </div>

                {/* 参数 6: 主循环 TCP 端口 (Game 业务) */}
                <div className="rounded-xl border border-separator/70 bg-surface/60 p-3 hover:bg-surface-secondary/40 transition-colors space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-foreground">主循环 TCP 端口 (Game)</span>
                    <span className="text-[10px] text-muted font-mono">默认 8105</span>
                  </div>
                  <input
                    type="number"
                    value={launchConfig.game_port || 8105}
                    onChange={(e) => setLaunchConfig((prev) => ({ ...prev, game_port: parseInt(e.target.value) || 8105 }))}
                    className="w-full text-xs rounded-lg border border-separator/80 bg-surface px-2.5 py-1.5 text-foreground focus:outline-none focus:border-accent font-mono"
                  />
                  <div className="text-[11px] text-muted leading-tight">
                    游戏主业务长连接通信端口，负责场景指令与角色数据同步
                  </div>
                </div>

                {/* 参数 7: 数据库挂载路径 (DB Path) */}
                <div className="rounded-xl border border-separator/70 bg-surface/60 p-3 hover:bg-surface-secondary/40 transition-colors space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-foreground">数据库挂载路径 (DB Path)</span>
                    <span className="text-[10px] text-muted font-mono">SQLite 存储</span>
                  </div>
                  <input
                    type="text"
                    value={launchConfig.db_path}
                    placeholder="account.db"
                    onChange={(e) => setLaunchConfig((prev) => ({ ...prev, db_path: e.target.value }))}
                    className="w-full text-xs rounded-lg border border-separator/80 bg-surface px-2.5 py-1.5 text-foreground focus:outline-none focus:border-accent font-mono"
                  />
                  <div className="text-[11px] text-muted leading-tight">
                    支持中文文件名；切换存档在下次启动时生效
                  </div>
                </div>

                {/* 参数 8: 客户端资产位置 (Client Assets) */}
                <div className="rounded-xl border border-separator/70 bg-surface/60 p-3 hover:bg-surface-secondary/40 transition-colors space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-foreground">客户端资产位置 (Client Assets)</span>
                    <span className="text-[10px] text-muted">可选</span>
                  </div>
                  <input
                    type="text"
                    value={launchConfig.client_assets_dir}
                    placeholder="留空自动检索客户端 StreamingAssets 目录"
                    onChange={(e) => setLaunchConfig((prev) => ({ ...prev, client_assets_dir: e.target.value }))}
                    className="w-full text-xs rounded-lg border border-separator/80 bg-surface px-2.5 py-1.5 text-foreground placeholder:text-muted/60 focus:outline-none focus:border-accent font-mono"
                  />
                  <div className="text-[11px] text-muted leading-tight">
                    用于服务端提取热更包哈希及配表校验的客户端解包根路径
                  </div>
                </div>

                {/* 附加项: 开放 PAY (游戏内直购) — 严格限定仅在 iOS 且 229 构建包可见 */}
                {isPaySwitchVisible && (
                  <div className="flex items-center justify-between rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 hover:bg-emerald-500/15 transition-colors">
                    <div className="space-y-0.5 pr-2">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-semibold text-emerald-400">开放 PAY (游戏内直购)</span>
                        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                          iOS 229 特供
                        </span>
                      </div>
                      <div className="text-[11px] text-muted leading-tight">
                        解除 40035 充值停运并放行 openPay，开放移动端直购与月卡购买
                      </div>
                    </div>
                    <PlainSwitch
                      label="开放 PAY"
                      isSelected={!!paySwitchData?.ios_pay_enabled}
                      isDisabled={paySwitchLoading}
                      onChange={handleTogglePaySwitch}
                    />
                  </div>
                )}
              </div>
            </Card.Content>

            <div className="pt-3 px-1 border-t border-separator/70 flex items-center justify-between gap-2 mt-2">
              <div className="text-[11px] text-muted truncate">
                端口与DB配置下次启动生效
              </div>
              <div className="flex items-center gap-2">
                <select
                  aria-label="启动命令终端"
                  value={launchShell}
                  onChange={(e) => { setLaunchShell(e.target.value as LaunchShell); setCopiedCommand(false) }}
                  className="text-xs rounded-lg border border-separator bg-surface px-2 py-1 text-foreground"
                >
                  <option value="cmd">Windows CMD</option>
                  <option value="powershell">PowerShell</option>
                  <option value="posix">Linux / macOS</option>
                </select>
                <Button
                  size="sm"
                  variant="secondary"
                  className="text-xs h-7.5 px-2.5 cursor-pointer"
                  onClick={handleCopyCommand}
                >
                  {copiedCommand ? (
                    <Check className="size-3.5 text-emerald-400 mr-1" />
                  ) : (
                    <Copy className="size-3.5 mr-1" />
                  )}
                  {copiedCommand ? '已复制' : '复制启动命令'}
                </Button>
                <Button
                  size="sm"
                  className="text-xs h-7.5 px-3 font-semibold cursor-pointer bg-primary text-primary-foreground"
                  isDisabled={savingLaunch}
                  onClick={handleSaveLaunchConfig}
                >
                  <Save className="size-3.5 mr-1" />
                  保存配置
                </Button>
              </div>
            </div>
          </Card>

        </div>
      </Section>

      {/* 头像白名单安全选择器模态窗 */}
      <Modal.Root isOpen={isAvatarModalOpen} onOpenChange={setIsAvatarModalOpen}>
        <Modal.Backdrop className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-3 sm:p-6">
          <Modal.Container className="w-full !w-[min(1180px,94vw)] !max-w-[min(1180px,94vw)] flex flex-col pointer-events-none">
            <Modal.Dialog
              className="relative w-full !w-[min(1180px,94vw)] !max-w-[min(1180px,94vw)] pointer-events-auto bg-surface border border-separator rounded-2xl shadow-2xl p-5 sm:p-6 flex flex-col max-h-[88vh] overflow-hidden"
              style={{ width: 'min(1180px, 94vw)', maxWidth: 'min(1180px, 94vw)' }}
            >
              {/* 右上角绝对定位关闭按钮 */}
              <Button isIconOnly variant="ghost"
                className="absolute top-4 right-4 z-20 flex size-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-secondary hover:text-foreground cursor-pointer focus:outline-none"
                aria-label="关闭"
                onPress={() => setIsAvatarModalOpen(false)}
              >
                <X className="size-4" />
              </Button>

              <Modal.Header className="pb-3 border-b border-separator shrink-0 pr-8">
                <Modal.Heading className="text-base font-semibold text-foreground flex items-center gap-2">
                  <ShieldCheck className="size-5 text-accent" />
                  更换头像
                </Modal.Heading>
                <p className="text-xs text-muted mt-1">
                  共 {avatarsList.length} 款官方头像，可按名称或 7 位头像 ID 搜索。
                </p>
              </Modal.Header>

              {/* 搜索框 */}
              <div className="pt-3 pb-2 shrink-0">
                <SearchField aria-label="搜索头像" fullWidth>
                  <SearchField.Group>
                    <SearchField.SearchIcon />
                    <SearchField.Input
                      placeholder={`搜索头像名称或 ID（共 ${avatarsList.length} 款）`}
                      value={avatarKeyword}
                      onChange={(e) => setAvatarKeyword(e.target.value)}
                    />
                    <SearchField.ClearButton />
                  </SearchField.Group>
                </SearchField>
              </div>

              {avatarError && <p role="alert" className="mb-3 shrink-0 rounded-xl bg-danger-soft p-3 text-sm text-danger">{avatarError}</p>}
              {avatarSubmitting && <p role="status" className="mb-2 shrink-0 text-xs text-muted">正在保存头像…</p>}

              {/* 白名单头像滚动网格 */}
              <div className="flex-1 overflow-x-hidden overflow-y-auto p-1 cp-scroll min-h-0">
                {filteredAvatars.length === 0 ? (
                  <div className="py-16 text-center text-xs text-muted">
                    未找到匹配的头像
                  </div>
                ) : (
                  <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-7 2xl:grid-cols-8 gap-3">
                    {filteredAvatars.map((item) => {
                      const isCurrent = Number(avatarId) === Number(item.id)
                      return (
                        <button
                          type="button"
                          key={item.id}
                          aria-label={`${item.name}（头像ID ${item.id}）`}
                          aria-pressed={isCurrent}
                          disabled={avatarSubmitting || isCurrent}
                          onClick={() => handleSelectAvatar(item)}
                          className={`group relative w-full min-w-0 box-border flex flex-col items-center justify-between p-3 rounded-2xl border transition-all text-center select-none cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                            isCurrent
                              ? 'border-accent bg-accent/15 ring-2 ring-accent/40 shadow-sm cursor-default'
                              : 'border-separator/80 bg-surface-secondary/40 hover:border-accent/50 hover:bg-surface-secondary hover:shadow-xs'
                          } ${avatarSubmitting ? 'opacity-60 pointer-events-none' : ''}`}
                          style={{ minHeight: '148px' }}
                        >
                          {isCurrent && (
                            <span className="absolute top-2 right-2 size-4.5 rounded-full bg-accent text-white flex items-center justify-center shadow-xs z-10">
                              <Check className="size-3 stroke-[3]" />
                            </span>
                          )}

                          {/* 圆形头像容器：固定 54px，居中且大方 */}
                          <div className="relative size-14 shrink-0 rounded-full overflow-hidden border border-separator/80 bg-surface shadow-xs mt-1">
                            {/* 底层占位兜底 */}
                            <div className="absolute inset-0 flex items-center justify-center bg-surface-secondary text-[10px] font-mono text-muted">
                              {item.id}
                            </div>
                            <img
                              src={resolveAssetUrl(avatarAssetPath(item.id))}
                              alt={item.name}
                              className="relative size-full object-cover transition-transform group-hover:scale-105 duration-200"
                              onLoad={e => { e.currentTarget.style.opacity = '1' }}
                              onError={(e) => {
                                ;(e.target as HTMLElement).style.opacity = '0'
                              }}
                            />
                          </div>

                          {/* 文字区：黄金比例排版，长名自然折行，普通名单行居中 */}
                          <div className="w-full min-w-0 mt-2.5 flex-1 flex flex-col justify-center text-center px-1">
                            <div
                              className="text-xs font-medium text-foreground line-clamp-2 break-words leading-snug"
                              title={item.name}
                            >
                              {item.name || `头像 ${item.id}`}
                            </div>
                            <div className="text-[10px] font-mono text-muted tabular-nums mt-1 tracking-tight">
                              {item.id}
                            </div>
                          </div>
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>

              <div className="pt-3 border-t border-separator flex items-center justify-between shrink-0 mt-1">
                <span className="text-xs text-muted">
                  当前显示 {filteredAvatars.length} / {avatarsList.length} 款头像
                </span>
                <Button size="sm" variant="secondary" onPress={() => setIsAvatarModalOpen(false)}>
                  关闭
                </Button>
              </div>
            </Modal.Dialog>
          </Modal.Container>
        </Modal.Backdrop>
      </Modal.Root>
    </div>
  )
}

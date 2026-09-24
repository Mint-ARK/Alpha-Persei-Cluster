import { useState, useEffect, useRef, useMemo } from 'react'
import {
  AlertCircle,
  ArrowDown,
  Check,
  CheckCircle2,
  Clock,
  Copy,
  Layers,
  Maximize2,
  RefreshCw,
  Server,
  Sparkles,
  Trash2,
  UserCheck,
  Wifi,
  X,
} from 'lucide-react'
import {
  Button,
  Calendar,
  Card,
  Chip,
  Modal,
  SearchField,
  Separator,
  Switch,
} from '@heroui/react'
import { today, getLocalTimeZone, CalendarDate } from '@internationalized/date'
import { PageHeader, Section, StatCard, StatusDot, type Tone } from '../components/kit'
import { useServer } from '../lib/serverContext'
import { fetchOverviewLogs, fetchOverviewMetrics, type ModuleMountStatus } from '../lib/api'

interface MetricPoint {
  time: string
  memoryMb: number
  netKbps: number
}

// 默认 42 大核心架构服务模块挂载矩阵（当后台离线时的自愈兜底基准，涵盖 7 大系统层）
const fallbackModules: ModuleMountStatus[] = [
  // 1. 基础网络与接入网关 (8 项)
  { id: 'server_net', name: 'server_net', code: '8102 网关与客户端握手分流', status: '就绪', desc: '8102 端口 · 客户端分流与握手', tone: 'success' },
  { id: 'core', name: 'core', code: '8105 TCP 主循环与网络中枢', status: '运行中', desc: '8105 TCP · 核心网络驱动与长连接', tone: 'success' },
  { id: 'login', name: 'login', code: '登录洪流与会话管线 (LoginPipeline)', status: '已挂载', desc: '10043/10001/10011 登录洪流突发调度', tone: 'success' },
  { id: 'battle_server', name: 'battle_server', code: '6105 UDP 物理战斗服 (KCP)', status: '已就绪', desc: '20B对称无conv · 局内同步与战毕结算', tone: 'success' },
  { id: 'https_sdk', name: 'https_sdk', code: '443 HTTPS 原生 SDK 直通鉴权', status: '已就绪', desc: '免密直通/区服列表/适龄提示/公告接管', tone: 'success' },
  { id: 'cdn_proxy', name: 'cdn_proxy', code: '官方 CDN 智能穿透抓包网关', status: '本地模式', desc: '双向流式零内存代理落盘', tone: 'default' },
  { id: 'dns_server', name: 'dns_server', code: '本地轻量 UDP:53 DNS 服务', status: '本地拦截', desc: '局域网域名解析与无感劫持', tone: 'default' },
  { id: 'server_daemon', name: 'server_daemon', code: '双进程守护与崩溃自愈巡检', status: '已就绪', desc: '心跳保活/异常捕获/崩溃自愈重启机制', tone: 'success' },

  // 2. 核心中枢、编解码与响应生成 (4 项)
  { id: 'generator', name: 'generator', code: '全协议响应生成器与动态组帧引擎', status: '运行中', desc: '业务查库/动态序列化/响应帧装配调度', tone: 'success' },
  { id: 'codec', name: 'codec', code: '协议编解码中枢 (Protobuf/结构体)', status: '运行中', desc: '200+ 协议双向序列化与动态逆向组包', tone: 'success' },
  { id: 'account_db', name: 'account_db', code: 'SQLite 核心数据层 (217张业务表)', status: '就绪', desc: '每线程独立连接/事务管控/原子持久化', tone: 'success' },
  { id: 'operations', name: 'operations', code: 'CS协议操作码与业务逻辑分发', status: '已挂载', desc: '操作类注册/协议分发/逻辑落库调度', tone: 'success' },

  // 3. 修正者养成与战术系统 (6 项)
  { id: 'hero_service', name: 'hero_service', code: '修正者养成与神格重构系统', status: '已挂载', desc: '63位自机全星级/等级/突破/跃迁', tone: 'success' },
  { id: 'equip_service', name: 'equip_service', code: '刻印赋能与专属钥从同调', status: '已挂载', desc: '82套专属刻印/品阶/神系重构', tone: 'success' },
  { id: 'servant_service', name: 'servant_service', code: '钥从觉醒与沉睡之子召唤系统', status: '已挂载', desc: '沉睡之子唤醒/神系专属钥从超越', tone: 'success' },
  { id: 'chip_service', name: 'chip_service', code: '修正者神格芯片管理系统', status: '已挂载', desc: '芯片插槽/方案配置/词条自检', tone: 'success' },
  { id: 'cooperation_skill_server', name: 'cooperation_skill', code: '连携奥义与组合技能中枢', status: '已挂载', desc: '连携出场计量/熟练度进度/奥义组合', tone: 'success' },
  { id: 'team_server', name: 'team_server', code: '预设编队与助战派遣服务', status: '已挂载', desc: '编队增量记录/关卡推荐阵容持久化', tone: 'success' },

  // 4. 资产、经济与物资流转 (5 项)
  { id: 'inventory_service', name: 'inventory_service', code: '背包物资与全道具资产管理', status: '已挂载', desc: '1,757项纯净资产/自选箱/表情原子差量', tone: 'success' },
  { id: 'shop_service', name: 'shop_service', code: '采购商城与计费货架交易系统', status: '已挂载', desc: '115个官方货架/限购管控与周期重置', tone: 'success' },
  { id: 'mail_service', name: 'mail_service', code: '全服邮件与系统奖励分发', status: '已挂载', desc: '补偿/附件/全局邮件分发通道', tone: 'success' },
  { id: 'fatigue_service', name: 'fatigue_service', code: '体力自然恢复与消耗结算服务', status: '已挂载', desc: '360秒滴答递增/耐力药剂核销/溢出暂存', tone: 'success' },
  { id: 'periodic_gift_service', name: 'periodic_gift_service', code: '周期连续时间礼包与月卡', status: '已挂载', desc: '7日/14日/月卡周期订阅管理', tone: 'success' },

  // 5. 抽卡、关卡与核心玩法 (8 项)
  { id: 'draw_service', name: 'draw_service', code: '4大保底抽卡与防歪机制', status: '已挂载', desc: '199卡池全量清单/软保底线性递增', tone: 'success' },
  { id: 'stage_service', name: 'stage_service', code: '关卡章节与挑战进度预设', status: '已挂载', desc: '主线/困难/支线三表体系/关卡进程检视', tone: 'success' },
  { id: 'polyhedron_service', name: 'polyhedron_service', code: '多维变量 (肉鸽玩法) 状态机', status: '已挂载', desc: '信标选择/终端强化/记忆宝藏探索', tone: 'success' },
  { id: 'rogueteam_service', name: 'rogueteam_service', code: '虚构推演 (肉鸽小队/88xxx) 状态机', status: '已挂载', desc: '28科技树/7列分支地图生成与多维掉落', tone: 'success' },
  { id: 'weekly_challenge_service', name: 'weekly_challenge', code: '历战轮回与黑区净化周常中枢', status: '已挂载', desc: '梦境再构/黑区异变/周期积分结算', tone: 'success' },
  { id: 'autochess_service', name: 'autochess_service', code: '决斗王自走棋 PVE/PVP 对弈', status: '已挂载', desc: '自走棋对弈状态机与羁绊结算', tone: 'success' },
  { id: 'minigame_service', name: 'minigame_service', code: '常驻小游戏与活动玩法调度', status: '已挂载', desc: '浮光绎曲音乐会/鸣律探微等', tone: 'success' },
  { id: 'activity_lottery', name: 'activity_lottery', code: '限时活动扭蛋与抽奖奖池引擎', status: '已挂载', desc: '活动代币消耗/分级奖池与大奖轮换', tone: 'success' },

  // 6. 好感羁绊与后宅休闲 (5 项)
  { id: 'backhome_service', name: 'backhome_service', code: '游园街委托与后宅烹饪经营', status: '已挂载', desc: '游园街委托/全菜谱研发/好感度', tone: 'success' },
  { id: 'trust_service', name: 'trust_service', code: '修正者好感度与心境羁绊系统', status: '已挂载', desc: '信物交互/心境物语/剧情语音解锁', tone: 'success' },
  { id: 'oath_service', name: 'oath_service', code: '修正者誓约系统与专属婚书', status: '已挂载', desc: '纯爱信物交付/誓约仪式/婚书落盘', tone: 'success' },
  { id: 'peripheral_service', name: 'peripheral_service', code: '外围系统与个性化偏好配置', status: '已挂载', desc: '大厅场景/音乐切换/名片装扮', tone: 'success' },
  { id: 'archive_service', name: 'archive_service', code: '图鉴档案与剧情画廊收集系统', status: '已挂载', desc: '敌兵图鉴/插画原画/音乐唱片收藏', tone: 'success' },

  // 7. 中枢引擎与周期调度 (6 项)
  { id: 'ai_bot_service', name: 'ai_bot_service', code: 'AI 修正者大语言对话中枢', status: '已挂载', desc: '多模型大语言交互与人设拟真', tone: 'success' },
  { id: 'achievement_service', name: 'achievement_service', code: '成就动态自愈引擎中枢', status: '已挂载', desc: '478/478全成就实时驱动与弹窗', tone: 'success' },
  { id: 'event_bus', name: 'event_bus', code: '全局事件总线引擎 (EventBus)', status: '运行中', desc: 'STAGE_PASS/HERO_UP/ITEM_CHANGE核心广播', tone: 'success' },
  { id: 'task_listener', name: 'task_listener', code: '日常与周常任务驱动监听器', status: '已挂载', desc: '任务目标判定/进度更新/数据监听', tone: 'success' },
  { id: 'illustrated_listener', name: 'illustrated_listener', code: '图鉴全量解锁与动态索引体系', status: '已挂载', desc: '自机立绘/怪物/神系情报实时穿透', tone: 'success' },
  { id: 'lazy_timer', name: 'lazy_timer', code: '跨日刷新与周期惰性定时引擎', status: '运行中', desc: '自然日刷新判定/心跳脉冲/跨周重置', tone: 'success' },
]

export default function OverviewPanel() {
  const { status, isOnline, loading, refreshStatus, resVersionInfo, resVersionLoading, switchVersion } = useServer()
  const [switchingVersion, setSwitchingVersion] = useState<string | null>(null)
  const [switchNotice, setSwitchNotice] = useState<string>('')

  const handleSwitchVersion = async (targetVer: string) => {
    if (!isOnline || resVersionLoading || switchingVersion) return
    if (resVersionInfo?.current_version === targetVer) return
    setSwitchingVersion(targetVer)
    try {
      const ok = await switchVersion(targetVer)
      if (ok) {
        setSwitchNotice(`已切换为 ${targetVer} 版本 (下次客户端启动更新生效)`)
      } else {
        setSwitchNotice('切换失败，请检查服务状态')
      }
      setTimeout(() => setSwitchNotice(''), 4000)
    } finally {
      setSwitchingVersion(null)
    }
  }

  // 1. 顶部四大窗口链路与运行时长计算
  const sdkLink = !isOnline
    ? { state: 'unstarted', desc: '8102 网关 / 443 HTTPS · 未启动' }
    : status?.sdk_link || { state: 'ready', desc: '8102 网关 / 443 HTTPS' }

  const gameLink = !isOnline
    ? { state: 'unstarted', desc: '8105 TCP / 6105 UDP · 未启动' }
    : status?.game_link || { state: 'ready', desc: '8105 TCP 主循环 / 6105 UDP' }

  const getLinkTone = (state: string): Tone => {
    if (!isOnline || state === 'unstarted') return 'accent' // 蓝色 未启动
    if (state === 'connected') return 'success'             // 绿色 已连接
    if (state === 'ready') return 'warning'                 // 黄色 就绪
    return 'danger'                                         // 红色 异常
  }

  const getLinkText = (state: string): string => {
    if (!isOnline || state === 'unstarted') return '未启动' // 蓝色 未启动
    if (state === 'connected') return '已连接'
    if (state === 'ready') return '就绪'
    return '异常'
  }

  // 运行时长
  const localUptimeText = !isOnline
    ? '未启动'
    : status?.uptime?.local_formatted || status?.uptime_formatted || '0小时 0分 0秒'
  const cumulativeUptimeText = !isOnline
    ? '未启动'
    : status?.uptime?.cumulative_formatted || '86小时 40分'

  // 2. 模块挂载状态（后台未启动时统一标记为未启动，蓝色 accent）
  const displayedModules = useMemo(() => {
    const raw = status?.modules || fallbackModules
    if (!isOnline) {
      return raw.map((mod) => ({
        ...mod,
        status: '未启动',
        tone: 'accent' as Tone,
      }))
    }
    return raw
  }, [status?.modules, isOnline])

  // 3. HeroUI v3 日历签到数据
  const currentDate = useMemo(() => {
    try {
      return today(getLocalTimeZone())
    } catch {
      return new CalendarDate(2026, 9, 11)
    }
  }, [])

  const targetYear = status?.sign_in?.year ?? currentDate.year
  const targetMonth = status?.sign_in?.month ?? currentDate.month
  const targetToday = status?.sign_in?.today ?? currentDate.day

  const signedDays = useMemo(() => {
    return status?.sign_in?.signed_days || [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
  }, [status?.sign_in?.signed_days])

  const isSignedToday = status?.sign_in?.is_signed_today ?? true
  const totalSignedCount = status?.sign_in?.total_signed ?? signedDays.length

  const daysInMonth = useMemo(() => {
    return new Date(targetYear, targetMonth, 0).getDate()
  }, [targetYear, targetMonth])

  const signProgressPercent = useMemo(() => {
    return Math.min(100, Math.round((totalSignedCount / (daysInMonth || 30)) * 100))
  }, [totalSignedCount, daysInMonth])

  const milestones = [
    { days: 7, label: '7天' },
    { days: 14, label: '14天' },
    { days: 21, label: '21天' },
    { days: 28, label: '全勤' },
  ]

  // 4. 服务端实时输出流打印台（双日志合一 + PowerShell 终端式智能滚屏）
  const [logs, setLogs] = useState<string[]>([])
  const [autoScroll, setAutoScroll] = useState<boolean>(true)
  const [hasNewLogsBelow, setHasNewLogsBelow] = useState<boolean>(false)
  const [isLogModalOpen, setIsLogModalOpen] = useState<boolean>(false)
  const [allLogs, setAllLogs] = useState<string[]>([])
  const [logSearchQuery, setLogSearchQuery] = useState<string>('')
  const [copied, setCopied] = useState<boolean>(false)

  const terminalRef = useRef<HTMLDivElement>(null)
  const logOffsetRef = useRef<number>(0)
  const serverStartTimeRef = useRef<number>(0)
  const isUserAtBottomRef = useRef<boolean>(true)

  // 终端滚动事件监听：判断用户是否停留在底部，避免滚动打扰
  const handleTerminalScroll = () => {
    if (!terminalRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = terminalRef.current
    const atBottom = scrollHeight - scrollTop - clientHeight < 35
    isUserAtBottomRef.current = atBottom
    if (atBottom) {
      setHasNewLogsBelow(false)
    }
  }

  // 点击或开启滚屏时跳到底部
  const scrollToBottom = () => {
    if (terminalRef.current) {
      terminalRef.current.scrollTo({
        top: terminalRef.current.scrollHeight,
        behavior: 'smooth',
      })
      isUserAtBottomRef.current = true
      setHasNewLogsBelow(false)
    }
  }

  const handleAutoScrollToggle = (val: boolean) => {
    setAutoScroll(val)
    if (val) {
      scrollToBottom()
    }
  }

  // 初始与定时拉取日志（增量获取 + 缓冲区累积追加）
  useEffect(() => {
    let mounted = true
    if (!isOnline) {
      setLogs([])
      logOffsetRef.current = 0
      return
    }

    const loadLogs = async () => {
      try {
        const isFirst = logOffsetRef.current === 0
        // 首次加载获取最近 80 条；后续仅按游标增量拉取新日志
        const res = isFirst
          ? await fetchOverviewLogs(80)
          : await fetchOverviewLogs(200, logOffsetRef.current)

        if (!mounted || !res?.data) return

        const { logs: newLines, total, server_start_time } = res.data

        // 服务端重启检测：若启动时间变化，重置缓冲
        if (
          server_start_time &&
          serverStartTimeRef.current !== 0 &&
          server_start_time !== serverStartTimeRef.current
        ) {
          serverStartTimeRef.current = server_start_time
          setLogs(newLines || [])
          logOffsetRef.current = total || (newLines?.length ?? 0)
          return
        }
        if (server_start_time) {
          serverStartTimeRef.current = server_start_time
        }

        if (isFirst) {
          setLogs(newLines || [])
          logOffsetRef.current = total || (newLines?.length ?? 0)
        } else if (newLines && newLines.length > 0) {
          // 增量追加至现有缓冲区，保留最多 1500 条
          setLogs((prev) => {
            const merged = [...prev, ...newLines]
            return merged.length > 1500 ? merged.slice(-1500) : merged
          })
          logOffsetRef.current = total || (logOffsetRef.current + newLines.length)
        }
      } catch {
        // 静默处理网络异常
      }
    }

    loadLogs()
    const timer = setInterval(loadLogs, 2500)
    return () => {
      mounted = false
      clearInterval(timer)
    }
  }, [isOnline])

  // 当日志更新时：若用户保持在底部且开启了自动滚屏，自动保持吸底；若翻到上方，展示新日志气泡
  useEffect(() => {
    if (!terminalRef.current) return
    if (autoScroll && isUserAtBottomRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight
    } else if (!isUserAtBottomRef.current && logs.length > 0) {
      setHasNewLogsBelow(true)
    }
  }, [logs, autoScroll])

  // 打开模态窗时拉取全量日志（当前会话全部）
  const handleOpenAllLogs = async () => {
    setIsLogModalOpen(true)
    if (!isOnline) {
      setAllLogs([])
      return
    }
    try {
      const res = await fetchOverviewLogs(1000)
      if (res?.data?.logs) {
        setAllLogs(res.data.logs)
      }
    } catch {
      setAllLogs(logs)
    }
  }

  const handleCopyLogs = () => {
    const content = allLogs.join('\n')
    navigator.clipboard.writeText(content).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const handleClearTerminal = () => {
    setLogs([])
    setHasNewLogsBelow(false)
  }

  // 5. 服务器内存与网络监控（1Hz 采样，最近 15 秒真实物理工作集滑动窗口）
  const [serverPeakMem, setServerPeakMem] = useState<number>(0)

  const [metricHistory, setMetricHistory] = useState<MetricPoint[]>(() => {
    const initial: MetricPoint[] = []
    const now = Date.now()
    for (let i = 14; i >= 0; i--) {
      const t = new Date(now - i * 1000)
      const secStr = String(t.getSeconds()).padStart(2, '0')
      initial.push({
        time: secStr,
        memoryMb: 0,
        netKbps: 0,
      })
    }
    return initial
  })

  useEffect(() => {
    const interval = setInterval(async () => {
      const now = new Date()
      const secStr = String(now.getSeconds()).padStart(2, '0')

      let mem = 0
      let net = 0

      if (isOnline) {
        try {
          const res = await fetchOverviewMetrics()
          if (res?.data) {
            mem = Number(res.data.memory_mb) || 0
            net = Number(res.data.net_kbps) || 0
            if (res.data.peak_memory_mb) {
              setServerPeakMem(Number(res.data.peak_memory_mb))
            }
          }
        } catch {
          // 异常降级为 0，绝对杜绝 Math.random() 虚假伪造
          mem = 0
          net = 0
        }
      }

      setMetricHistory((prev) => {
        const next = [...prev.slice(1), { time: secStr, memoryMb: Math.round(mem * 10) / 10, netKbps: Math.round(net * 10) / 10 }]
        return next
      })
    }, 1000)

    return () => clearInterval(interval)
  }, [isOnline])

  // 5. 计算内存图表独立数据点与动态单位刻度（仅监控服务端主进程，不包含前端面板）
  const memData = useMemo(() => {
    const memValues = metricHistory.map((p) => p.memoryMb)
    const curVal = memValues[memValues.length - 1] ?? 0
    const peakVal = Math.max(...memValues, curVal, serverPeakMem)

    const maxVal = Math.max(...memValues, serverPeakMem, 10)
    const yMax = Math.max(60, Math.ceil((maxVal * 1.15) / 20) * 20)
    const yMid = yMax / 2
    const yMin = 0

    const isGb = yMax >= 1024
    const topTick = isGb ? `${(yMax / 1024).toFixed(1)} GB` : `${Math.round(yMax)} MB`
    const midTick = isGb ? `${(yMid / 1024).toFixed(1)} GB` : `${Math.round(yMid)} MB`
    const bottomTick = isGb ? '0 GB' : '0 MB'

    const curFormatted = isGb ? `${(curVal / 1024).toFixed(2)} GB` : `${curVal.toFixed(1)} MB`
    const peakFormatted = isGb ? `${(peakVal / 1024).toFixed(2)} GB` : `${peakVal.toFixed(1)} MB`

    const w = 300
    const h = 70
    const padTop = 6
    const padBottom = 6
    const plotH = h - padTop - padBottom

    const coords = metricHistory.map((p, idx) => {
      const x = (idx / 14) * w
      const ratio = Math.min(1, Math.max(0, (p.memoryMb - yMin) / (yMax - yMin)))
      const y = h - padBottom - ratio * plotH
      return { x, y }
    })

    const pointsStr = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ')
    const areaStr = `0,${h - padBottom} ${pointsStr} ${w},${h - padBottom}`
    const lastCoord = coords[coords.length - 1] || { x: w, y: h - padBottom }

    return {
      topTick,
      midTick,
      bottomTick,
      curFormatted,
      peakFormatted,
      pointsStr,
      areaStr,
      lastCoord,
    }
  }, [metricHistory, serverPeakMem])

  // 计算网络图表独立数据点与动态单位刻度（空闲无客户端时真实归零 0.0 KB/s）
  const netData = useMemo(() => {
    const netValues = metricHistory.map((p) => p.netKbps)
    const curVal = netValues[netValues.length - 1] ?? 0
    const peakVal = Math.max(...netValues, curVal)

    // 空闲时保留 10 KB/s 量程底线，图线平直贴底
    const maxVal = Math.max(...netValues, 0)
    const yMax = Math.max(10, Math.ceil((maxVal * 1.25) / 5) * 5)
    const yMid = yMax / 2
    const yMin = 0

    const isMb = yMax >= 1024
    const topTick = isMb ? `${(yMax / 1024).toFixed(1)} MB/s` : `${Math.round(yMax)} KB/s`
    const midTick = isMb ? `${(yMid / 1024).toFixed(1)} MB/s` : `${Math.round(yMid)} KB/s`
    const bottomTick = isMb ? '0 MB/s' : '0 KB/s'

    const curFormatted = isMb ? `${(curVal / 1024).toFixed(2)} MB/s` : `${curVal.toFixed(1)} KB/s`
    const peakFormatted = isMb ? `${(peakVal / 1024).toFixed(2)} MB/s` : `${peakVal.toFixed(1)} KB/s`

    const w = 300
    const h = 70
    const padTop = 6
    const padBottom = 6
    const plotH = h - padTop - padBottom

    const coords = metricHistory.map((p, idx) => {
      const x = (idx / 14) * w
      const ratio = Math.min(1, Math.max(0, (p.netKbps - yMin) / (yMax - yMin)))
      const y = h - padBottom - ratio * plotH
      return { x, y }
    })

    const pointsStr = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ')
    const areaStr = `0,${h - padBottom} ${pointsStr} ${w},${h - padBottom}`
    const lastCoord = coords[coords.length - 1] || { x: w, y: h - padBottom }

    return {
      topTick,
      midTick,
      bottomTick,
      curFormatted,
      peakFormatted,
      pointsStr,
      areaStr,
      lastCoord,
    }
  }, [metricHistory])

  // 过滤后的全量日志
  const filteredAllLogs = useMemo(() => {
    if (!logSearchQuery.trim()) return allLogs
    const q = logSearchQuery.toLowerCase()
    return allLogs.filter((line) => line.toLowerCase().includes(q))
  }, [allLogs, logSearchQuery])

  return (
    <div className="space-y-6">
      {/* 顶部标题栏 */}
      <PageHeader
        title="状态总览"
        description="深空之眼 V5 离线单机中枢驾驶舱 · 主数据库原子事务持久化中。"
        actions={
          <div className="flex items-center gap-2">
            <Chip size="sm" variant="soft" color="success">
              <Chip.Label>主数据库: account.db</Chip.Label>
            </Chip>
            <Button
              size="sm"
              variant="secondary"
              className="gap-1.5"
              onPress={() => refreshStatus()}
              isDisabled={loading}
            >
              <RefreshCw className={`size-4 ${loading ? 'animate-spin' : ''}`} />
              刷新状态
            </Button>
          </div>
        }
      />

      {/* 1. 上方四窗口 */}
      <div className="grid auto-rows-fr gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {/* 窗口 1: SDK网关链路状态 */}
        <StatCard
          label="SDK网关链路状态"
          value={getLinkText(sdkLink.state)}
          icon={Wifi}
          tone={getLinkTone(sdkLink.state)}
          delta={!isOnline ? '未启动' : '端口 8102 / 443'}
          deltaTone={getLinkTone(sdkLink.state)}
          deltaNote={!isOnline ? 'server未启动' : '协议鉴权与分流就绪'}
        />

        {/* 窗口 2: Game链路状态 */}
        <StatCard
          label="Game链路状态"
          value={getLinkText(gameLink.state)}
          icon={Server}
          tone={getLinkTone(gameLink.state)}
          delta={!isOnline ? '未启动' : '端口 8105 / 6105'}
          deltaTone={getLinkTone(gameLink.state)}
          deltaNote={!isOnline ? 'server未启动' : 'TCP主服与UDP战斗就绪'}
        />

        {/* 窗口 3: 运行时长 */}
        <StatCard
          label="运行时长"
          value={localUptimeText}
          icon={Clock}
          tone="accent"
          delta={!isOnline ? '未启动' : `累计: ${cumulativeUptimeText}`}
          deltaTone={!isOnline ? 'accent' : 'default'}
          deltaNote={!isOnline ? 'server未启动' : '本地连续运行 / 累计时长'}
        />

        {/* 窗口 4: 客户端资源版本分发与热切换 */}
        <Card className="p-5 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-foreground">客户端资源分发</span>
                <Chip
                  size="sm"
                  variant="soft"
                  color={resVersionInfo?.current_version === '311' ? 'accent' : 'success'}
                >
                  <Chip.Label>
                    {resVersionInfo?.current_version === '311' ? 'Build 311' : 'Build 229'}
                  </Chip.Label>
                </Chip>
              </div>
              <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-surface-secondary text-foreground">
                <Layers className="size-4" />
              </span>
            </div>

            {/* 版本切换分段按钮 */}
            <div className="mt-3 grid grid-cols-2 gap-1.5 rounded-xl bg-surface-secondary/70 p-1 border border-separator/40">
              <Button
                size="sm"
                variant={resVersionInfo?.current_version !== '311' ? 'secondary' : 'ghost'}
                className="text-xs h-7 gap-1 font-mono font-medium"
                isDisabled={!isOnline || resVersionLoading || switchingVersion !== null}
                onPress={() => handleSwitchVersion('229')}
              >
                {switchingVersion === '229' ? (
                   <RefreshCw className="size-3 animate-spin" />
                ) : resVersionInfo?.current_version !== '311' ? (
                  <Check className="size-3 text-success" />
                ) : null}
                Build 229
              </Button>
              <Button
                size="sm"
                variant={resVersionInfo?.current_version === '311' ? 'secondary' : 'ghost'}
                className="text-xs h-7 gap-1 font-mono font-medium"
                isDisabled={!isOnline || resVersionLoading || switchingVersion !== null}
                onPress={() => handleSwitchVersion('311')}
              >
                {switchingVersion === '311' ? (
                  <RefreshCw className="size-3 animate-spin" />
                ) : resVersionInfo?.current_version === '311' ? (
                  <Check className="size-3 text-secondary" />
                ) : null}
                Build 311
              </Button>
            </div>
          </div>

          <div className="mt-2.5">
            {switchNotice ? (
              <div className="text-[11px] font-medium text-accent truncate">{switchNotice}</div>
            ) : (
              <div className="flex items-center justify-between text-[11px] text-muted">
                <span className="truncate font-mono">
                  Hash: {resVersionInfo?.asset_hash ? resVersionInfo.asset_hash.slice(0, 10) + '...' : '1999279163...'}
                </span>
                <span className="shrink-0 text-foreground/70">iOS · PC · Android</span>
              </div>
            )}
          </div>
        </Card>
      </div>

      {/* 2 & 3. 中间行：左侧服务模块挂载矩阵，右侧本月签到日历 */}
      <div className="grid gap-4 lg:grid-cols-5">
        {/* 左侧：服务模块运行挂载状态 */}
        <Section
          title="服务模块运行挂载状态"
          description="V5 离线单机核心子系统与领域服务挂载矩阵"
          className="lg:col-span-3"
          actions={
            <Chip size="sm" variant="soft" color={isOnline ? 'success' : 'accent'}>
              <Chip.Label>{isOnline ? `全量挂载 (${displayedModules.length} 个服务)` : '未启动'}</Chip.Label>
            </Chip>
          }
        >
          <Card className="h-full p-5">
            <Card.Content className="gap-0">
              <div className="max-h-[390px] overflow-y-auto pr-1 cp-scroll">
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {displayedModules.map((mod) => (
                    <div
                      key={mod.id}
                      className="flex items-center justify-between rounded-xl border border-separator/60 bg-surface-secondary/40 p-2.5 transition-colors hover:bg-surface-secondary/80"
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        <StatusDot tone={mod.tone} pulse={isOnline && mod.tone === 'success'} />
                        <div className="min-w-0">
                          <div className="truncate font-mono text-xs font-semibold text-foreground">
                            {mod.name}
                          </div>
                          <div className="truncate text-[11px] text-muted mt-0.5">
                            {mod.code}
                          </div>
                        </div>
                      </div>
                      <Chip size="sm" variant="soft" color={mod.tone} className="shrink-0 ml-2">
                        <Chip.Label>{mod.status}</Chip.Label>
                      </Chip>
                    </div>
                  ))}
                </div>
              </div>
            </Card.Content>
          </Card>
        </Section>

        {/* 右侧：本月签到日历 */}
        <Section
          title="本月签到日历"
          description="HeroUI v3 日历 · 当月累计签到与里程碑奖励"
          className="lg:col-span-2"
          actions={
            <div className="flex items-center gap-1.5">
              <Chip size="sm" variant="soft" color={isSignedToday ? 'success' : 'warning'}>
                <Chip.Label>{isSignedToday ? '今日已签' : '今日待签'}</Chip.Label>
              </Chip>
            </div>
          }
        >
          <Card className="h-full p-4 flex flex-col justify-between gap-3">
            <div className="w-full flex justify-center">
              <Calendar
                aria-label="本月签到日历"
                defaultFocusedValue={currentDate}
                isReadOnly
                className="w-full max-w-[340px] shadow-none border-none bg-transparent"
              >
                <Calendar.Header className="px-1 py-0.5">
                  <Calendar.NavButton slot="previous" />
                  <Calendar.Heading className="text-xs font-semibold text-foreground" />
                  <Calendar.NavButton slot="next" />
                </Calendar.Header>
                <Calendar.Grid className="w-full">
                  <Calendar.GridHeader>
                    {(day) => (
                      <Calendar.HeaderCell className="text-center font-mono text-[11px] text-muted py-1 select-none">
                        {day}
                      </Calendar.HeaderCell>
                    )}
                  </Calendar.GridHeader>
                  <Calendar.GridBody>
                    {(date) => (
                      <Calendar.Cell date={date} className="h-8 p-0.5">
                        {({ formattedDate }) => {
                          const isTargetMonth = date.year === targetYear && date.month === targetMonth
                          const isSigned = isTargetMonth && signedDays.includes(date.day)
                          const isToday = isTargetMonth && date.day === targetToday

                          return (
                            <div
                              className={`relative flex size-full items-center justify-center font-mono text-xs rounded-lg transition-all ${
                                !isTargetMonth
                                  ? 'text-muted/30 select-none'
                                  : isToday
                                  ? 'font-bold ring-1.5 ring-accent bg-accent/15 text-accent shadow-xs'
                                  : isSigned
                                  ? 'font-semibold text-accent'
                                  : 'text-foreground/85 hover:bg-surface-secondary/40'
                              }`}
                            >
                              <span>{formattedDate}</span>
                              {isSigned && (
                                <span
                                  className={`absolute bottom-1 size-1 rounded-full ${
                                    isToday ? 'bg-accent' : 'bg-accent/80'
                                  }`}
                                />
                              )}
                            </div>
                          )
                        }}
                      </Calendar.Cell>
                    )}
                  </Calendar.GridBody>
                </Calendar.Grid>
              </Calendar>
            </div>

            <Separator className="opacity-50 my-1" />

            {/* 月度签到进度与里程碑徽章 */}
            <div className="space-y-2 px-1">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted">当月签到完成度</span>
                <span className="font-mono font-medium text-foreground">
                  {totalSignedCount} / {daysInMonth} 天 ({signProgressPercent}%)
                </span>
              </div>
              <div className="w-full bg-surface-secondary h-2 rounded-full overflow-hidden p-0.5">
                <div
                  className="bg-accent h-full rounded-full transition-all duration-500 shadow-xs"
                  style={{ width: `${signProgressPercent}%` }}
                />
              </div>

              {/* 里程碑阶段 */}
              <div className="grid grid-cols-4 gap-1.5 pt-1">
                {milestones.map((m) => {
                  const reached = totalSignedCount >= m.days
                  return (
                    <div
                      key={m.days}
                      className={`flex items-center justify-center gap-1 text-[11px] font-mono py-1 rounded-lg border transition-all ${
                        reached
                          ? 'bg-accent/15 text-accent border-accent/30 font-medium shadow-xs'
                          : 'bg-surface-secondary/50 text-muted/80 border-separator/40'
                      }`}
                    >
                      <Sparkles className={`size-3 ${reached ? 'text-accent' : 'text-muted/60'}`} />
                      <span>{m.label}</span>
                    </div>
                  )
                })}
              </div>
            </div>

            <Separator className="opacity-50 my-1" />

            {/* 底部签到状态指示（只读展示） */}
            <div className="flex items-center justify-between gap-2 px-1 text-xs">
              <div className="flex items-center gap-1.5 text-muted truncate">
                <UserCheck className="size-3.5 text-muted/70 shrink-0" />
                <span>UID: {status?.sign_in?.uid || '10001'}</span>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {isSignedToday ? (
                  <span className="flex items-center gap-1 text-emerald-500 dark:text-emerald-400 font-medium">
                    <CheckCircle2 className="size-3.5" />
                    <span>今日已完成签到</span>
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-amber-500 dark:text-amber-400 font-medium">
                    <AlertCircle className="size-3.5" />
                    <span>今日待签到</span>
                  </span>
                )}
              </div>
            </div>
          </Card>
        </Section>
      </div>

      {/* 5 & 6. 下方行：左侧输出流打印台，右侧服务器内存与网络监控折线图 */}
      <div className="grid gap-4 lg:grid-cols-5">
        {/* 左侧：服务端实时输出流打印台 */}
        <Section
          title="服务端实时输出流"
          description="本次运行的实时 stdout 与 CS-SC 协议交互流水"
          className="lg:col-span-3"
          actions={
            <div className="flex items-center gap-2">
              <div className="hidden items-center gap-1.5 sm:flex">
                <span className="text-xs text-muted">自动跟随</span>
                <Switch size="sm" isSelected={autoScroll} onChange={handleAutoScrollToggle} aria-label="自动跟随" />
              </div>
              <Button
                size="sm"
                variant="ghost"
                isIconOnly
                aria-label="清屏"
                onPress={handleClearTerminal}
              >
                <Trash2 className="size-3.5" />
              </Button>
              <Button
                size="sm"
                variant="secondary"
                className="gap-1 text-xs"
                onPress={handleOpenAllLogs}
              >
                <Maximize2 className="size-3.5" />
                查看全部
              </Button>
            </div>
          }
        >
          <Card className="h-full p-4 relative">
            <div
              ref={terminalRef}
              onScroll={handleTerminalScroll}
              className="h-[280px] overflow-y-auto rounded-xl border border-separator/50 bg-surface-secondary/70 p-3 font-mono text-xs text-foreground space-y-1 select-text flex flex-col"
            >
              {!isOnline ? (
                <div className="m-auto flex flex-col items-center justify-center gap-1.5 text-xs text-muted">
                  <span className="font-mono font-medium text-foreground/80">server未启动</span>
                  <span className="text-[11px] text-muted">后台未检测到服务端运行进程</span>
                </div>
              ) : logs.length === 0 ? (
                <div className="m-auto flex items-center justify-center text-xs text-muted">
                  控制台输出流暂无新数据
                </div>
              ) : (
                logs.map((logLine, index) => {
                  const isError = logLine.includes('[ERROR]') || logLine.includes('FAIL')
                  const isWarn = logLine.includes('[WARN]')
                  const isInfo = logLine.includes('[INFO]')
                  const isFrame =
                    logLine.includes('[FRAME]') ||
                    logLine.includes('CS_') ||
                    logLine.includes('SC_') ||
                    logLine.includes('cs_') ||
                    logLine.includes('sc_') ||
                    logLine.includes('[PUSH') ||
                    logLine.includes('[TCP')

                  let lineToneClass = 'text-foreground/90'
                  if (isError) lineToneClass = 'text-danger font-medium'
                  else if (isWarn) lineToneClass = 'text-warning font-medium'
                  else if (isFrame) lineToneClass = 'text-accent font-medium'
                  else if (isInfo) lineToneClass = 'text-foreground/80'

                  return (
                    <div key={index} className="flex items-start gap-2 leading-relaxed hover:bg-surface/60 px-1 rounded">
                      <span className="shrink-0 font-mono text-[10px] text-muted select-none w-7 text-end">
                        {index + 1}
                      </span>
                      <span className={`break-all ${lineToneClass}`}>{logLine}</span>
                    </div>
                  )
                })
              )}
            </div>

            {/* 向上翻阅时下方有新日志的浮动提示气泡 */}
            {hasNewLogsBelow && (
              <button
                type="button"
                onClick={scrollToBottom}
                className="absolute bottom-6 right-6 z-10 flex items-center gap-1.5 px-3 py-1 rounded-full bg-accent text-accent-foreground text-xs font-medium shadow-lg hover:brightness-110 active:scale-95 transition-all animate-bounce cursor-pointer border border-accent/40"
              >
                <span>有新日志输出</span>
                <ArrowDown className="size-3.5" />
              </button>
            )}
          </Card>
        </Section>

        {/* 右侧：服务器内存与网络监控（双独立图表 · 动态单位纵轴 · 物理真实采集） */}
        <Section
          title="服务器资源监控"
          description="Server 核心工作集物理内存 (不含面板) 与网络真实吞吐"
          className="lg:col-span-2"
        >
          <Card className="h-full p-4 flex flex-col justify-between gap-3">
            {/* 表 1: 服务器内存占用监控 (RSS 工作集) */}
            <div className="rounded-xl border border-separator/60 bg-surface-secondary/30 p-3 flex flex-col">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="size-2 rounded-full bg-accent" />
                  <div>
                    <span className="text-xs font-medium text-foreground">Server 进程物理内存 (RSS)</span>
                    <span className="block text-[10px] text-muted">Win32 工作集 · 排除面板渲染</span>
                  </div>
                </div>
                <div className="text-end">
                  <div className="font-mono text-xs font-semibold tabular-nums text-foreground">
                    {memData.curFormatted}
                  </div>
                  <div className="text-[10px] text-muted font-mono">
                    历史峰值 {memData.peakFormatted}
                  </div>
                </div>
              </div>

              {/* 矢量图表容器：左侧动态单位纵轴，右侧折线曲线（无横轴坐标） */}
              <div className="mt-2 flex items-stretch gap-2">
                {/* 动态单位纵轴 */}
                <div className="flex flex-col justify-between py-1 text-end font-mono text-[10px] text-muted shrink-0 w-12 select-none">
                  <span>{memData.topTick}</span>
                  <span>{memData.midTick}</span>
                  <span>{memData.bottomTick}</span>
                </div>

                {/* SVG 独立折线图 */}
                <div className="relative flex-1 h-[70px] rounded-lg border border-separator/40 bg-surface-secondary/50 overflow-hidden">
                  <svg
                    viewBox="0 0 300 70"
                    className="w-full h-full overflow-visible"
                    preserveAspectRatio="none"
                  >
                    <defs>
                      <linearGradient id="memGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="var(--accent, #a855f7)" stopOpacity="0.25" />
                        <stop offset="100%" stopColor="var(--accent, #a855f7)" stopOpacity="0.0" />
                      </linearGradient>
                    </defs>

                    {/* 参考刻度网格线 */}
                    <line x1="0" y1="6" x2="300" y2="6" stroke="currentColor" strokeOpacity="0.07" strokeDasharray="3 3" />
                    <line x1="0" y1="35" x2="300" y2="35" stroke="currentColor" strokeOpacity="0.07" strokeDasharray="3 3" />
                    <line x1="0" y1="64" x2="300" y2="64" stroke="currentColor" strokeOpacity="0.07" />

                    {/* 渐变填充 */}
                    <polygon points={memData.areaStr} fill="url(#memGradient)" />

                    {/* 折线 */}
                    <polyline
                      fill="none"
                      stroke="var(--accent, #a855f7)"
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      points={memData.pointsStr}
                    />

                    {/* 实时末端采样点 */}
                    <circle
                      cx={memData.lastCoord.x}
                      cy={memData.lastCoord.y}
                      r="3.5"
                      fill="var(--accent, #a855f7)"
                    />
                  </svg>
                </div>
              </div>
            </div>

            <Separator className="opacity-40" />

            {/* 表 2: 网络吞吐速率监控 */}
            <div className="rounded-xl border border-separator/60 bg-surface-secondary/30 p-3 flex flex-col">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="size-2 rounded-full bg-success" />
                  <span className="text-xs font-medium text-foreground">网络吞吐监控</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs font-semibold tabular-nums text-foreground">
                    {netData.curFormatted}
                  </span>
                  <span className="text-[10px] text-muted font-mono">
                    峰值 {netData.peakFormatted}
                  </span>
                </div>
              </div>

              {/* 矢量图表容器：左侧动态单位纵轴，右侧折线曲线（无横轴坐标） */}
              <div className="mt-2 flex items-stretch gap-2">
                {/* 动态单位纵轴 */}
                <div className="flex flex-col justify-between py-1 text-end font-mono text-[10px] text-muted shrink-0 w-12 select-none">
                  <span>{netData.topTick}</span>
                  <span>{netData.midTick}</span>
                  <span>{netData.bottomTick}</span>
                </div>

                {/* SVG 独立折线图 */}
                <div className="relative flex-1 h-[70px] rounded-lg border border-separator/40 bg-surface-secondary/50 overflow-hidden">
                  <svg
                    viewBox="0 0 300 70"
                    className="w-full h-full overflow-visible"
                    preserveAspectRatio="none"
                  >
                    <defs>
                      <linearGradient id="netGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="var(--success, #10b981)" stopOpacity="0.25" />
                        <stop offset="100%" stopColor="var(--success, #10b981)" stopOpacity="0.0" />
                      </linearGradient>
                    </defs>

                    {/* 参考刻度网格线 */}
                    <line x1="0" y1="6" x2="300" y2="6" stroke="currentColor" strokeOpacity="0.07" strokeDasharray="3 3" />
                    <line x1="0" y1="35" x2="300" y2="35" stroke="currentColor" strokeOpacity="0.07" strokeDasharray="3 3" />
                    <line x1="0" y1="64" x2="300" y2="64" stroke="currentColor" strokeOpacity="0.07" />

                    {/* 渐变填充 */}
                    <polygon points={netData.areaStr} fill="url(#netGradient)" />

                    {/* 折线 */}
                    <polyline
                      fill="none"
                      stroke="var(--success, #10b981)"
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      points={netData.pointsStr}
                    />

                    {/* 实时末端采样点 */}
                    <circle
                      cx={netData.lastCoord.x}
                      cy={netData.lastCoord.y}
                      r="3.5"
                      fill="var(--success, #10b981)"
                    />
                  </svg>
                </div>
              </div>
            </div>

            {/* 采样周期说明底栏 */}
            <div className="flex items-center justify-between text-[11px] text-muted pt-1 px-1">
              <span>采样频率 1.0 Hz · 滑动窗口最近 15 秒</span>
              <span className="font-mono text-[10px]">进程工作集与网卡吞吐物理只读</span>
            </div>
          </Card>
        </Section>
      </div>

      {/* 查看全量日志流模态框 */}
      <Modal.Root isOpen={isLogModalOpen} onOpenChange={setIsLogModalOpen}>
        <Modal.Backdrop className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4">
          <Modal.Container className="w-full max-w-4xl max-h-[85vh] flex flex-col pointer-events-none">
            <Modal.Dialog className="w-full pointer-events-auto bg-surface border border-separator rounded-2xl shadow-2xl p-5 flex flex-col max-h-[85vh] overflow-hidden">
              <Modal.Header className="flex items-center justify-between pb-3 border-b border-separator shrink-0">
                <div>
                  <Modal.Heading className="text-base font-semibold">
                    本次运行全量输出流日志
                  </Modal.Heading>
                  <div className="text-xs text-muted mt-0.5 font-mono">
                    共计 {filteredAllLogs.length} 条记录 · 包含网络握手、协议编解码与操作调度
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button size="sm" variant="secondary" className="gap-1 text-xs" onPress={handleCopyLogs}>
                    {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                    {copied ? '已复制' : '复制全量日志'}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    isIconOnly
                    aria-label="关闭"
                    onPress={() => setIsLogModalOpen(false)}
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              </Modal.Header>

              <Modal.Body className="py-4 space-y-3 flex-1 overflow-hidden flex flex-col min-h-0">
                <SearchField aria-label="搜索日志内容" className="w-full shrink-0">
                  <SearchField.Group>
                    <SearchField.SearchIcon />
                    <SearchField.Input
                      placeholder="输入关键词过滤（如 CS_、ERROR、login、account）..."
                      value={logSearchQuery}
                      onChange={(e) => setLogSearchQuery(e.target.value)}
                    />
                    <SearchField.ClearButton />
                  </SearchField.Group>
                </SearchField>

                <div className="flex-1 min-h-[300px] overflow-y-auto rounded-xl border border-separator/60 bg-surface-secondary/70 p-3 font-mono text-xs text-foreground space-y-1 select-text cp-scroll flex flex-col">
                  {!isOnline ? (
                    <div className="m-auto flex flex-col items-center justify-center gap-1.5 text-xs text-muted">
                      <span className="font-mono font-medium text-foreground/80">server未启动</span>
                      <span className="text-[11px] text-muted">后台未检测到服务端运行进程</span>
                    </div>
                  ) : filteredAllLogs.length === 0 ? (
                    <div className="m-auto flex items-center justify-center text-xs text-muted">
                      未找到匹配的日志条目
                    </div>
                  ) : (
                    filteredAllLogs.map((line, idx) => {
                      const isError = line.includes('[ERROR]') || line.includes('FAIL')
                      const isWarn = line.includes('[WARN]')
                      const isFrame = line.includes('[FRAME]') || line.includes('CS_') || line.includes('SC_')

                      let toneClass = 'text-foreground/90'
                      if (isError) toneClass = 'text-danger font-medium'
                      else if (isWarn) toneClass = 'text-warning font-medium'
                      else if (isFrame) toneClass = 'text-accent font-medium'

                      return (
                        <div key={idx} className="flex items-start gap-2.5 hover:bg-surface/70 px-1 py-0.5 rounded">
                          <span className="w-9 shrink-0 text-end text-[10px] text-muted font-mono select-none">
                            {idx + 1}
                          </span>
                          <span className={`break-all ${toneClass}`}>{line}</span>
                        </div>
                      )
                    })
                  )}
                </div>
              </Modal.Body>

              <Modal.Footer className="pt-3 border-t border-separator flex justify-end shrink-0">
                <Button size="sm" variant="secondary" onPress={() => setIsLogModalOpen(false)}>
                  关闭
                </Button>
              </Modal.Footer>
            </Modal.Dialog>
          </Modal.Container>
        </Modal.Backdrop>
      </Modal.Root>
    </div>
  )
}

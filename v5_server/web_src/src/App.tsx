import { useState, useEffect, useMemo, useRef, type ComponentType } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  Bot,
  Boxes,
  Database,
  Hexagon,
  Info,
  LayoutDashboard,
  Mail,
  Moon,
  RefreshCw,
  Settings,
  ShoppingBag,
  Sliders,
  Sparkles,
  Sun,
  Swords,
  Trophy,
  Users,
  Wrench,
} from 'lucide-react'
import { Avatar, Button, Chip, SearchField, Separator, Tabs } from '@heroui/react'
import { IconBadge, StatusDot } from './components/kit'
import { ThemeProvider, useTheme } from './lib/theme'
import AccountsPanel from './panels/Accounts'
import AgreementPanel from './panels/Agreement'
import AIChatPanel from './panels/AIChat'
import GachaPanel from './panels/Gacha'
import HeroesPanel from './panels/Heroes'
import InventoryPanel from './panels/Inventory'
import MailPanel from './panels/Mail'
import OverviewPanel from './panels/Overview'
import SettingsPanel from './panels/Settings'
import ShopPanel from './panels/Shop'
import { ErrorBoundary } from './components/ErrorBoundary'

import { ServerProvider, useServer } from './lib/serverContext'

type NavItem = {
  id: string
  label: string
  icon: LucideIcon
  Panel: ComponentType
  /** 该项是新分组的起点，渲染一条分隔线 */
  groupStart?: boolean
  badge?: string
}

const NAV: NavItem[] = [
  { id: 'overview', label: '状态总览', icon: LayoutDashboard, Panel: OverviewPanel },
  { id: 'accounts', label: '账号数据', icon: Users, Panel: AccountsPanel },
  { id: 'heroes', label: '角色状态', icon: Swords, Panel: HeroesPanel },
  { id: 'inventory', label: '资源背包', icon: Boxes, Panel: InventoryPanel },
  { id: 'mail', label: '邮件系统', icon: Mail, Panel: MailPanel, badge: '1' },
  { id: 'gacha', label: '卡池管理', icon: Sliders, Panel: GachaPanel },
  { id: 'shop', label: '商店系统', icon: ShoppingBag, Panel: ShopPanel },
  { id: 'aichat', label: 'AI修正者聊天', icon: Bot, Panel: AIChatPanel, badge: 'NEW' },
  { id: 'settings', label: '其他设置', icon: Settings, Panel: SettingsPanel, groupStart: true },
  { id: 'agreement', label: '关于', icon: Info, Panel: AgreementPanel },
]

interface TopBarProps {
  onSelectTab?: (key: string) => void
}

function TopBar({ onSelectTab }: TopBarProps) {
  const { resolved, setMode } = useTheme()
  const {
    activeUid,
    setActiveUid,
    resVersionInfo,
    resVersionLoading,
    switchVersion,
    consoleName,
    operatorName,
  } = useServer()
  const [searchQuery, setSearchQuery] = useState('')
  const [isSearchOpen, setIsSearchOpen] = useState(false)
  const searchContainerRef = useRef<HTMLDivElement>(null)

  // 全局搜索索引库
  const searchIndex = useMemo(() => {
    return [
      {
        id: 'overview',
        title: '状态总览',
        desc: '服务运行状态、网络链路、在线人数、系统指标与控制台实时输出流',
        category: '导航',
        tabId: 'overview',
        keywords: ['总览', '状态', '概览', '链路', '端口', '日志', '指标', '签到', 'overview'],
        icon: LayoutDashboard,
      },
      {
        id: 'accounts',
        title: '账号数据',
        desc: '账号档案、等级体力、修改昵称与签名、更换头像、贴纸画卷、快捷拓展坞',
        category: '导航',
        tabId: 'accounts',
        keywords: ['账号', '用户', '头像', '昵称', '签名', '体力', '贴纸', '画卷', 'admin', 'account'],
        icon: Users,
      },
      {
        id: 'heroes',
        title: '角色状态',
        desc: '全修正者图鉴、品阶神格、专属钥从同调、心链羁绊誓约',
        category: '导航',
        tabId: 'heroes',
        keywords: ['角色', '修正者', '神格', '钥从', '专属', '誓约', '心链', 'hero', 'heroes'],
        icon: Swords,
      },
      {
        id: 'inventory',
        title: '资源背包',
        desc: '资产全图鉴监控、货币素材、刻印分类、快捷添加至邮件草稿箱',
        category: '导航',
        tabId: 'inventory',
        keywords: ['背包', '道具', '资源', '刻印', '素材', '移转之辉', '草稿', 'inventory', 'item'],
        icon: Boxes,
      },
      {
        id: 'mail',
        title: '邮件系统',
        desc: 'GM 邮件拟制、物品附件多选投递、正文参数校验、投递历史审计',
        category: '导航',
        tabId: 'mail',
        keywords: ['邮件', '发邮件', '附件', 'GM', '投递', '审计', 'mail'],
        icon: Mail,
      },
      {
        id: 'gacha',
        title: '卡池管理',
        desc: '90抽必中、70抽扩充、自选钥从、大保底防歪锁定、保底抽数微调与预设方案',
        category: '导航',
        tabId: 'gacha',
        keywords: ['卡池', '抽卡', '探测', '保底', '防歪', '70抽', '90抽', '自选', 'gacha', 'draw'],
        icon: Sliders,
      },
      {
        id: 'shop',
        title: '商店系统',
        desc: '115 个官方交易区货架检视、每日/每周/每月周期商品配额重置与限购管控',
        category: '导航',
        tabId: 'shop',
        keywords: ['商店', '商城', '货架', '采购', '重置商品', '限购', '直购', 'shop', 'goods'],
        icon: ShoppingBag,
      },
      {
        id: 'aichat',
        title: 'AI修正者聊天',
        desc: 'AI 修正者智能体对话、原装人设调谐、多模型装配、节日与生日邮件自动派发',
        category: '导航',
        tabId: 'aichat',
        keywords: ['ai', '聊天', '对话', '人设', '问候', '修正者聊天', '大模型', 'aichat', 'chat'],
        icon: Bot,
      },
      {
        id: 'settings',
        title: '其他设置',
        desc: '控制台基础设置、测试库备份、服务热重载、战令与充值重置、功勋册、IDE 工具集',
        category: '导航',
        tabId: 'settings',
        keywords: ['设置', '配置', '运维', '备份', '热更', '热重载', 'settings', 'config'],
        icon: Settings,
      },
      {
        id: 'agreement',
        title: '关于',
        desc: '本项目的少量功能描述、可能的故障排障 FAQ、权限原理、数据隐私与个人声明',
        category: '导航',
        tabId: 'agreement',
        keywords: ['关于', '客服', '权限', '协议', '隐私', '安全', 'faq', '排障', '端口', '管理员', '免责', '声明', 'agreement', 'about'],
        icon: Info,
      },
      {
        id: 'action-battlepass',
        title: '战令与充值状态管理',
        desc: '检视与重设大月卡战令档位、首充双倍、新手福利与累充记录',
        category: '快捷操作',
        tabId: 'settings',
        keywords: ['战令', '大月卡', '首充', '充值', '双倍', '累充', '新手福利', 'recharge', 'battlepass'],
        icon: Sparkles,
      },
      {
        id: 'action-hot-reload',
        title: '服务配置热重载',
        desc: '免重启即时热更公告配置、卡池调度方案与商店周期商品配置',
        category: '快捷操作',
        tabId: 'settings',
        keywords: ['热更', '热重载', '重载配置', 'reload', 'hot_reload'],
        icon: RefreshCw,
      },
      {
        id: 'action-backup',
        title: '备份主数据库',
        desc: '为当前 SQLite account.db 生成带精确时间戳的快照备份',
        category: '快捷操作',
        tabId: 'settings',
        keywords: ['备份', '数据库备份', 'backup', 'db', '快照'],
        icon: Database,
      },
      {
        id: 'action-hall-of-fame',
        title: '项目构建功勋册',
        desc: '检视参与项目研发的 25 款顶尖大模型出力指数与实战任务记录',
        category: '功勋谱',
        tabId: 'settings',
        keywords: ['功勋册', '模型榜', '大模型', '排名', 'Gemini', 'DeepSeek', 'Claude', 'hall of fame'],
        icon: Trophy,
      },
      {
        id: 'action-ide-tools',
        title: '参与构建的 IDE 与工程工具链',
        desc: '检视 IDA Pro、WinDBG、AssetStudio、Unity 及 20 款工程工具环境',
        category: '功勋谱',
        tabId: 'settings',
        keywords: ['ide', '工具', 'ida', 'windbg', 'x64dbg', 'assetstudio', 'antigravity', 'vscode'],
        icon: Wrench,
      },
    ]
  }, [])

  // 动态搜索过滤逻辑
  const searchResults = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return []

    // 1. 如果是纯数字，插入优先项：切换目标受控 UID
    const dynamicItems = []
    if (/^\d{3,8}$/.test(q)) {
      const targetUid = parseInt(q, 10)
      dynamicItems.push({
        id: `uid-${targetUid}`,
        title: `切换受控目标 UID 至 ${targetUid}`,
        desc: `将控制面板当前操作人环境切换为玩家 UID: ${targetUid}`,
        category: '账号操作',
        action: () => {
          if (setActiveUid) setActiveUid(targetUid)
          if (onSelectTab) onSelectTab('accounts')
        },
        icon: Users,
      })
    }

    // 2. 匹配索引库
    const matched = searchIndex.filter((item) => {
      return (
        item.title.toLowerCase().includes(q) ||
        item.desc.toLowerCase().includes(q) ||
        item.keywords.some((k) => k.toLowerCase().includes(q))
      )
    })

    return [...dynamicItems, ...matched]
  }, [searchQuery, searchIndex, setActiveUid, onSelectTab])

  // 点击外侧自动关闭搜索下拉框
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (searchContainerRef.current && !searchContainerRef.current.contains(e.target as Node)) {
        setIsSearchOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleSelectResult = (item: (typeof searchResults)[0]) => {
    if ('action' in item && typeof item.action === 'function') {
      item.action()
    } else if ('tabId' in item && item.tabId && onSelectTab) {
      onSelectTab(item.tabId)
    }
    setSearchQuery('')
    setIsSearchOpen(false)
  }

  return (
    <header className="sticky top-0 z-40 border-b border-separator bg-background/85 backdrop-blur-xl">
      <div className="mx-auto flex h-14 w-full max-w-[1600px] items-center gap-3 px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="grid size-8 shrink-0 place-items-center rounded-2xl bg-foreground text-background">
            <Hexagon className="size-4" strokeWidth={2.5} />
          </span>
          <span className="truncate text-sm font-semibold tracking-tight">{consoleName}</span>
        </div>

        <Chip size="sm" variant="soft" color="accent">
          <Chip.Label>当前 UID: {activeUid}</Chip.Label>
        </Chip>

        <Chip
          size="sm"
          variant="soft"
          color={resVersionInfo?.current_version === '311' ? 'accent' : 'default'}
          className="cursor-pointer select-none transition-all hover:opacity-85"
          title="点击在 Build 229 与 Build 311 之间切换"
          onClick={() => {
            const next = resVersionInfo?.current_version === '311' ? '229' : '311'
            switchVersion(next)
          }}
        >
          <Chip.Label>
            资源: Build {resVersionInfo?.current_version || '229'}
            {resVersionLoading ? ' 切换中...' : ''}
          </Chip.Label>
        </Chip>

        <div className="ms-auto flex items-center gap-2">
          {/* 全局搜索功能中枢 (已接通跨面板快速导航与指令检索) */}
          <div ref={searchContainerRef} className="relative hidden w-[260px] xl:block">
            <SearchField
              aria-label="全局搜索"
              fullWidth
              value={searchQuery}
              onChange={(val) => {
                setSearchQuery(val)
                setIsSearchOpen(Boolean(val.trim()))
              }}
            >
              <SearchField.Group>
                <SearchField.SearchIcon />
                <SearchField.Input
                  placeholder="搜索面板 / 战令 / UID..."
                  onFocus={() => {
                    if (searchQuery.trim()) setIsSearchOpen(true)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && searchResults.length > 0) {
                      handleSelectResult(searchResults[0])
                    } else if (e.key === 'Escape') {
                      setIsSearchOpen(false)
                    }
                  }}
                />
                <SearchField.ClearButton onPress={() => setSearchQuery('')} />
              </SearchField.Group>
            </SearchField>

            {/* 搜索结果下拉面板 */}
            {isSearchOpen && searchResults.length > 0 && (
              <div className="absolute top-full left-0 right-0 mt-1.5 z-50 rounded-xl border border-separator/80 bg-surface shadow-xl p-1.5 space-y-1 max-h-[360px] overflow-y-auto cp-scroll">
                <div className="px-2 py-1 text-[10px] font-semibold text-muted tracking-wider uppercase flex items-center justify-between">
                  <span>匹配到 {searchResults.length} 项结果</span>
                  <span className="font-mono text-[9px]">按 Enter 直达首项</span>
                </div>
                {searchResults.map((item) => {
                  const IconComp = item.icon
                  return (
                    <div
                      key={item.id}
                      onClick={() => handleSelectResult(item)}
                      className="group flex items-start gap-2.5 p-2 rounded-lg hover:bg-surface-secondary cursor-pointer transition-colors"
                    >
                      <div className="size-7 rounded-md bg-surface-secondary group-hover:bg-accent/15 flex items-center justify-center shrink-0 border border-separator/50 group-hover:border-accent/30 transition-colors">
                        <IconComp className="size-3.5 text-muted group-hover:text-accent transition-colors" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-1.5">
                          <span className="text-xs font-semibold text-foreground group-hover:text-accent transition-colors truncate">
                            {item.title}
                          </span>
                          <span className="text-[10px] px-1.5 py-0.2 rounded bg-surface-secondary border border-separator/40 text-muted shrink-0 font-medium">
                            {item.category}
                          </span>
                        </div>
                        <p className="text-[11px] text-muted line-clamp-1 mt-0.5 leading-tight">
                          {item.desc}
                        </p>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* 浅色/深色主题切换 */}
          <Button
            aria-label={resolved === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
            isIconOnly
            size="sm"
            variant="ghost"
            onPress={() => setMode(resolved === 'dark' ? 'light' : 'dark')}
          >
            {resolved === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </Button>

          <Separator orientation="vertical" className="mx-1 h-5" />

          {/* 管理员头像展示区（免点击省事设计，受系统设置操作员标识控制） */}
          <div className="flex items-center gap-2.5 select-none" title={`操作人：${operatorName}`}>
            <Avatar size="sm" color="accent">
              <Avatar.Fallback>{operatorName ? operatorName.slice(0, 1).toUpperCase() : '管'}</Avatar.Fallback>
            </Avatar>
            <div className="hidden leading-tight sm:block">
              <div className="text-[13px] font-medium">{operatorName === 'admin' ? '系统管理员' : operatorName}</div>
              <div className="font-mono text-[11px] text-muted">{operatorName}</div>
            </div>
          </div>
        </div>
      </div>
    </header>
  )
}

function Sidebar() {
  const { isOnline, status, activeZoneName } = useServer()

  return (
    <aside className="sticky top-[4.5rem] flex w-[60px] shrink-0 flex-col gap-3 md:w-[232px]">
      <div className="flex items-center gap-2.5 rounded-2xl bg-surface p-2.5 shadow-surface md:p-3">
        <IconBadge icon={Hexagon} tone={isOnline ? 'accent' : 'default'} size="sm" />
        <div className="hidden min-w-0 flex-1 md:block">
          <div className="truncate text-[13px] font-semibold" title={activeZoneName}>
            {activeZoneName}
          </div>
          <div className="truncate font-mono text-[11px] text-muted">
            {status?.version ? `${status.version} · 本地单机` : '单机独立沙盒'}
          </div>
        </div>
      </div>

      <Tabs.ListContainer className="w-full">
        <Tabs.List aria-label="控制面板导航" className="w-full gap-0.5 p-1.5">
          {NAV.map((item) => (
            <Tabs.Tab
              key={item.id}
              id={item.id}
              className="h-9 w-full min-w-0 justify-center gap-2.5 px-2 text-start md:justify-start md:px-3"
            >
              {item.groupStart && <Tabs.Separator />}
              <Tabs.Indicator />
              <item.icon className="size-4 shrink-0" strokeWidth={2} />
              <span className="hidden min-w-0 flex-1 truncate md:block">{item.label}</span>
              {item.badge && (
                <span className="hidden shrink-0 rounded-full bg-danger px-1.5 py-px text-[10px] leading-4 font-medium tabular-nums text-danger-foreground md:block">
                  {item.badge}
                </span>
              )}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs.ListContainer>

      <div className="hidden rounded-2xl bg-surface p-3.5 shadow-surface md:block">
        <div className="flex items-center gap-2">
          <StatusDot tone={isOnline ? 'success' : 'warning'} pulse={isOnline} />
          <span className="text-xs font-medium">{isOnline ? '服务运行中' : '未连接服务'}</span>
        </div>
        <dl className="mt-2.5 space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <dt className="text-xs text-muted">版本</dt>
            <dd className="font-mono text-xs tabular-nums">{status?.version || (isOnline ? 'v5.2.1' : '—')}</dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-xs text-muted">在线玩家</dt>
            <dd className="text-xs tabular-nums">{isOnline ? (status?.online_users ?? 1) : 0}</dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-xs text-muted">运行时长</dt>
            <dd className="text-xs tabular-nums">
              {isOnline
                ? status?.uptime_formatted ||
                  (status?.uptime?.local_seconds !== undefined
                    ? `${Math.floor(status.uptime.local_seconds / 3600)}h ${Math.floor((status.uptime.local_seconds % 3600) / 60)}m`
                    : '0h 0m')
                : '—'}
            </dd>
          </div>
        </dl>
      </div>
    </aside>
  )
}

function Console() {
  const isStandaloneAgreement =
    typeof window !== 'undefined' &&
    window.location.pathname.toLowerCase().includes('agreement.html')

  if (isStandaloneAgreement) {
    return (
      <div className="min-h-screen bg-background text-foreground flex flex-col items-center px-4 py-8 sm:px-6">
        <div className="w-full max-w-[960px]">
          <ErrorBoundary fallbackTitle="关于面板异常">
            <AgreementPanel standalone />
          </ErrorBoundary>
        </div>
      </div>
    )
  }

  // 面板 id 同步到 URL hash，便于把某个页签的链接直接发给别人
  const [tab, setTab] = useState(() => {
    const raw = window.location.hash.replace('#', '')
    const id = raw.split('?')[0].split('&')[0]
    return NAV.some((item) => item.id === id) ? id : 'overview'
  })

  const selectTab = (key: string) => {
    setTab(key)
    window.history.replaceState(null, '', `#${key}`)
  }

  useEffect(() => {
    const onHashChange = () => {
      const raw = window.location.hash.replace('#', '')
      const id = raw.split('?')[0].split('&')[0]
      if (NAV.some((item) => item.id === id)) {
        setTab(id)
      }
    }
    const onNavEvent = (e: Event) => {
      const customEvent = e as CustomEvent<string>
      if (customEvent.detail && NAV.some((item) => item.id === customEvent.detail)) {
        selectTab(customEvent.detail)
      }
    }
    window.addEventListener('hashchange', onHashChange)
    window.addEventListener('nav-to-tab', onNavEvent)
    return () => {
      window.removeEventListener('hashchange', onHashChange)
      window.removeEventListener('nav-to-tab', onNavEvent)
    }
  }, [])

  return (
    <div className="min-h-screen bg-background text-foreground">
      <TopBar onSelectTab={selectTab} />

      <div className="mx-auto w-full max-w-[1600px] px-4 pt-6 pb-16 sm:px-6">
        <Tabs
          className="items-start gap-0"
          orientation="vertical"
          selectedKey={tab}
          onSelectionChange={(key) => selectTab(String(key))}
        >
          <Sidebar />

          <div className="min-w-0 flex-1 ps-4 sm:ps-6 lg:ps-8">
            {NAV.map(({ id, Panel, label }) => (
              <Tabs.Panel key={id} id={id} className="ms-0 w-full p-0">
                <ErrorBoundary fallbackTitle={`${label}面板组件异常`}>
                  <Panel />
                </ErrorBoundary>
              </Tabs.Panel>
            ))}
          </div>
        </Tabs>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <ThemeProvider>
      <ServerProvider>
        <Console />
      </ServerProvider>
    </ThemeProvider>
  )
}

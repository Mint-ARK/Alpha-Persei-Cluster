import { useState, useEffect } from 'react'
import {
  Accordion,
  Button,
  Card,
  Chip,
  Tabs,
} from '@heroui/react'
import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  ExternalLink,
  HelpCircle,
  Lock,
  Moon,
  Server,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sun,
  Terminal,
  Wifi,
} from 'lucide-react'
import { IconBadge, PageHeader, Section } from '../components/kit'
import { useTheme } from '../lib/theme'

interface AgreementPanelProps {
  standalone?: boolean
}

type SubTabKey = 'support' | 'permission' | 'privacy' | 'terms'

export default function AgreementPanel({ standalone = false }: AgreementPanelProps) {
  const { resolved, setMode } = useTheme()

  const openGMConsole = () => {
    if (typeof window !== 'undefined') {
      if (window.location.protocol.startsWith('http')) {
        window.open(`${window.location.protocol}//${window.location.host}/web/gm_console.html`, '_blank')
      } else {
        window.open('http://127.0.0.1/web/gm_console.html', '_blank')
      }
    }
  }
  // 从 URL searchParams 或 hash 自动推导初始激活的子板块
  const [activeTab, setActiveTab] = useState<SubTabKey>(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search)
      const tabParam = params.get('tab') as SubTabKey | null
      if (tabParam && ['support', 'permission', 'privacy', 'terms'].includes(tabParam)) {
        return tabParam
      }
      const hash = window.location.hash.replace('#', '')
      if (['support', 'permission', 'privacy', 'terms'].includes(hash)) {
        return hash as SubTabKey
      }
    }
    return 'support'
  })

  // 监听浏览器历史或外部 hash 切换
  useEffect(() => {
    const handleUrlChange = () => {
      const params = new URLSearchParams(window.location.search)
      const tabParam = params.get('tab') as SubTabKey | null
      if (tabParam && ['support', 'permission', 'privacy', 'terms'].includes(tabParam)) {
        setActiveTab(tabParam)
      }
    }
    window.addEventListener('popstate', handleUrlChange)
    return () => window.removeEventListener('popstate', handleUrlChange)
  }, [])

  const handleTabChange = (key: string | number) => {
    const nextTab = String(key) as SubTabKey
    setActiveTab(nextTab)
    if (typeof window !== 'undefined') {
      try {
        const url = new URL(window.location.href)
        url.searchParams.set('tab', nextTab)
        window.history.replaceState(null, '', url.toString())
      } catch {
        // 静默兼容
      }
    }
  }

  return (
    <div className="space-y-6">
      {/* 顶部标题区 */}
      <PageHeader
        title="关于"
        description="本项目的少量功能描述版"
        actions={
          standalone ? (
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onPress={() => setMode(resolved === 'dark' ? 'light' : 'dark')}
                aria-label="切换色彩主题"
              >
                {resolved === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
                <span className="hidden sm:inline">{resolved === 'dark' ? '浅色模式' : '深色模式'}</span>
              </Button>
              <Button
                variant="primary"
                size="sm"
                onPress={openGMConsole}
              >
                <ExternalLink className="size-4" />
                <span>打开 GM 控制台</span>
              </Button>
            </div>
          ) : (
            <Chip color="accent" variant="soft" size="sm">
              <ShieldCheck className="size-3.5" />
              <Chip.Label>本地自包含已生效</Chip.Label>
            </Chip>
          )
        }
      />

      {/* HeroUI v3 原生 Tabs 组件 */}
      <Tabs
        selectedKey={activeTab}
        onSelectionChange={handleTabChange}
        className="w-full"
      >
        <Tabs.ListContainer className="w-full">
          <Tabs.List aria-label="子导航" className="w-full flex-wrap sm:flex-nowrap">
            <Tabs.Tab id="support" className="flex-1 justify-center gap-2 py-2">
              <Tabs.Indicator />
              <HelpCircle className="size-4" />
              <span>关于可能的故障</span>
            </Tabs.Tab>
            <Tabs.Tab id="permission" className="flex-1 justify-center gap-2 py-2">
              <Tabs.Indicator />
              <Lock className="size-4" />
              <span>关于权限与原理</span>
            </Tabs.Tab>
            <Tabs.Tab id="privacy" className="flex-1 justify-center gap-2 py-2">
              <Tabs.Indicator />
              <Shield className="size-4" />
              <span>关于数据与隐私</span>
            </Tabs.Tab>
            <Tabs.Tab id="terms" className="flex-1 justify-center gap-2 py-2">
              <Tabs.Indicator />
              <BookOpen className="size-4" />
              <span>个人声明</span>
            </Tabs.Tab>
          </Tabs.List>
        </Tabs.ListContainer>

        {/* =========================================================================
            Tab 1: 排障指南 (support)
           ========================================================================= */}
        <Tabs.Panel id="support" className="mt-4 space-y-4">
          {/* GM 控制台直通卡片 */}
          <Card className="border border-separator bg-surface">
            <Card.Header className="flex flex-row items-center justify-between gap-4 pb-2">
              <div className="space-y-1">
                <Card.Title className="text-base font-semibold text-foreground flex items-center gap-2">
                  <IconBadge icon={Server} tone="accent" size="sm" />
                  前往控制台
                </Card.Title>
                <Card.Description className="text-xs text-muted">
                  在控制台中查看详情数据
                </Card.Description>
              </div>
              <Button
                variant="primary"
                size="sm"
                onPress={openGMConsole}
              >
                <ExternalLink className="size-4" />
                直通控制台
              </Button>
            </Card.Header>
            <Card.Content className="pt-2 text-xs text-muted leading-relaxed">
              回环服务直通路径：
              <code className="text-accent bg-surface-secondary px-1.5 py-0.5 rounded font-mono">
                {typeof window !== 'undefined' && window.location.protocol.startsWith('http')
                  ? `${window.location.protocol}//${window.location.host}/web/gm_console.html`
                  : 'http://127.0.0.1/web/gm_console.html'}
              </code>
            </Card.Content>
          </Card>

          {/* 原生 HeroUI v3 Accordion 常见排障 FAQ */}
          <Section title="高频排障常见疑问 (FAQ)" description="客户端报错、网络连接、引导卡死与局域网接入处理指南">
            <Accordion className="w-full space-y-2">
              <Accordion.Item id="faq-1" className="rounded-xl border border-separator bg-surface px-4 py-1">
                <Accordion.Heading>
                  <Accordion.Trigger className="w-full flex items-center justify-between py-3 text-sm font-medium text-foreground hover:text-accent">
                    <span>1. 游戏启动弹窗网络连接失败并附带4开头错误码</span>
                    <Accordion.Indicator />
                  </Accordion.Trigger>
                </Accordion.Heading>
                <Accordion.Panel>
                  <Accordion.Body className="text-xs text-muted leading-relaxed pb-4 pt-1 space-y-2 border-t border-separator/40">
                    <p>• <strong>证书未安装</strong>：首次在新计算机运行必须导入本地根证书 <code>sdk_ca.crt</code> 至“受信任的根证书颁发机构”；</p>
                    <p>• <strong>特权端口被占用</strong>：请核查本机的 <code>80</code> (HTTP)、<code>443</code> (HTTPS)、<code>8102</code> (网关) 与 <code>8105</code> (游戏服务) 端口是否被其他软件（如 IIS、Apache、Nginx 等）占用；</p>
                    <p>• <strong>域名劫持未生效</strong>：请确认桌面启动壳已正常接管或系统 HOSTS 文件包含 <code>127.0.0.1 open.ys4fun.com</code> 等回环映射条目。</p>
                  </Accordion.Body>
                </Accordion.Panel>
              </Accordion.Item>

              <Accordion.Item id="faq-2" className="rounded-xl border border-separator bg-surface px-4 py-1">
                <Accordion.Heading>
                  <Accordion.Trigger className="w-full flex items-center justify-between py-3 text-sm font-medium text-foreground hover:text-accent">
                    <span>2. 关于卡死</span>
                    <Accordion.Indicator />
                  </Accordion.Trigger>
                </Accordion.Heading>
                <Accordion.Panel>
                  <Accordion.Body className="text-xs text-muted leading-relaxed pb-4 pt-1 space-y-2 border-t border-separator/40">
                    <p>卡死问题发生多样，现如今已经基本杜绝了“网络连接失败”类卡死，但是不保证不出现loading遮罩卡死。</p>
                  </Accordion.Body>
                </Accordion.Panel>
              </Accordion.Item>

              <Accordion.Item id="faq-3" className="rounded-xl border border-separator bg-surface px-4 py-1">
                <Accordion.Heading>
                  <Accordion.Trigger className="w-full flex items-center justify-between py-3 text-sm font-medium text-foreground hover:text-accent">
                    <span>3. 如何调整移转之辉、体力与抽卡探测凭证？</span>
                    <Accordion.Indicator />
                  </Accordion.Trigger>
                </Accordion.Heading>
                <Accordion.Panel>
                  <Accordion.Body className="text-xs text-muted leading-relaxed pb-4 pt-1 space-y-2 border-t border-separator/40">
                    <p>控制面板不提供数据库直改，资源只能在 GM 控制台的 <strong>【资源背包】</strong> 中选中后通过 <strong>【邮件系统】</strong> 发送，不可撤销。详细常用货币可以在数据库的currency表中修改具体数据</p>
                  </Accordion.Body>
                </Accordion.Panel>
              </Accordion.Item>

              <Accordion.Item id="faq-4" className="rounded-xl border border-separator bg-surface px-4 py-1">
                <Accordion.Heading>
                  <Accordion.Trigger className="w-full flex items-center justify-between py-3 text-sm font-medium text-foreground hover:text-accent">
                    <span>4. 移动端真机（iOS / Android）如何连入当前电脑服务端？</span>
                    <Accordion.Indicator />
                  </Accordion.Trigger>
                </Accordion.Heading>
                <Accordion.Panel>
                  <Accordion.Body className="text-xs text-muted leading-relaxed pb-4 pt-1 space-y-2 border-t border-separator/40">
                    <p>确保移动端与宿主电脑处于同一个 Wi-Fi 局域网内。在手机端将连接的WIFI的DNS改为手动，DNS地址为计算机在本局域网中的IP，在手机浏览器中输入CMD打印出的描述文件安装信任证书并在iPhone中信任即可。</p>
                  </Accordion.Body>
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>
          </Section>
        </Tabs.Panel>

        {/* =========================================================================
            Tab 2: 本地权限与原理 (permission)
           ========================================================================= */}
        <Tabs.Panel id="permission" className="mt-4 space-y-4">
          <Card className="border border-separator bg-surface">
            <Card.Header>
              <Card.Title className="text-base font-semibold text-foreground flex items-center gap-2">
                <IconBadge icon={Terminal} tone="warning" size="sm" />
                为什么启动服务需要“以管理员身份运行”？
              </Card.Title>
              <Card.Description className="text-xs text-muted">
                端口特权规范与无感拦截机制说明
              </Card.Description>
            </Card.Header>
            <Card.Content className="text-xs text-muted leading-relaxed space-y-2">
              <p>《深空之眼》原版游戏客户端在启动自检与登录认证阶段，强制通过标准的 <code>80 (HTTP)</code> 与 <code>443 (HTTPS)</code> 端口发起域名校验。</p>
              <p>进行HOST劫持需要提权</p>
            </Card.Content>
          </Card>

          <Card className="border border-separator bg-surface">
            <Card.Header>
              <Card.Title className="text-base font-semibold text-foreground flex items-center gap-2">
                <IconBadge icon={Wifi} tone="accent" size="sm" />
                127.0.0.1 本地回环网络隔离保证
              </Card.Title>
              <Card.Description className="text-xs text-muted">
                网络流量封闭流转，杜绝公网外联与穿透
              </Card.Description>
            </Card.Header>
            <Card.Content className="text-xs text-muted leading-relaxed space-y-2">
              <p>单机仿真服务仅监听本地环回网络（Loopback Adapter，即 <code>127.0.0.1</code> / <code>localhost</code>）以及指定的局域网网段。</p>
              <p><strong>严正承诺：</strong>本程序不会访问外置其他网站，CDN穿透针对的是挂全局T的场景。</p>
            </Card.Content>
          </Card>

          <Card className="border border-separator bg-surface">
            <Card.Header>
              <Card.Title className="text-base font-semibold text-foreground flex items-center gap-2">
                <IconBadge icon={ShieldCheck} tone="success" size="sm" />
                自签根证书（Root CA）的安全性阐述
              </Card.Title>
              <Card.Description className="text-xs text-muted">
                单向域名沙盒约束，不干涉系统其他 HTTPS 通信
              </Card.Description>
            </Card.Header>
            <Card.Content className="text-xs text-muted leading-relaxed space-y-2">
              <p>为满足客户端原生 HTTPS 链路的 TLS 加密验证，安装包内附带了本地自签名的 <code>AetherGazer V5 Root CA</code> 根证书。</p>
              <p>该证书仅对特定的游戏私有域名（如 <code>*.ys4fun.com</code>、<code>soboten.com</code>）建立本地信任，其专用私钥完整存放在宿主服务端内。它绝不会影响系统日常对银行、微信、支付宝等公网正规站点的安全信任链。</p>
            </Card.Content>
          </Card>
        </Tabs.Panel>

        {/* =========================================================================
            Tab 3: 数据主权与隐私承诺 (privacy)
           ========================================================================= */}
        <Tabs.Panel id="privacy" className="mt-4 space-y-4">
          <Card className="border border-separator bg-surface">
            <Card.Header>
              <Card.Title className="text-base font-semibold text-foreground flex items-center gap-2">
                <IconBadge icon={CheckCircle2} tone="success" size="sm" />
                整个服务器数据源是库驱动的，除了配置文件外，其他数据都在库里，可以自己改
              </Card.Title>
              <Card.Description className="text-xs text-muted">
                理论上是一个脱机程序。
              </Card.Description>
            </Card.Header>
          </Card>

          <Card className="border border-separator bg-surface">
            <Card.Header>
              <Card.Title className="text-base font-semibold text-foreground flex items-center gap-2">
                <IconBadge icon={ShieldAlert} tone="accent" size="sm" />
                没有后台数据采集，我自己的服务器忙着重构呢，这只是纯爱好。
              </Card.Title>
              <Card.Description className="text-xs text-muted">
                原版客户端的很多请求在这里默认批准被空函数吃掉。设备标识不会外流。
              </Card.Description>
            </Card.Header>
            <Card.Content className="text-xs text-muted leading-relaxed space-y-2">
              <p>人机验证触发的鉴权程序有一套重放攻击方案，因为TOKEN空载成功已经上线，理论上不会触发。</p>
            </Card.Content>
          </Card>
        </Tabs.Panel>

        {/* =========================================================================
            Tab 4: 协议与免责声明 (terms)
           ========================================================================= */}
        <Tabs.Panel id="terms" className="mt-4 space-y-4">
          <Card className="border border-separator bg-surface">
            <Card.Header>
              <Card.Title className="text-base font-semibold text-foreground flex items-center gap-2">
                <IconBadge icon={BookOpen} tone="default" size="sm" />
                § 1. 技术研究与非商业化声明
              </Card.Title>
            </Card.Header>
            <Card.Content className="text-xs text-muted leading-relaxed space-y-2">
              <p>纯拿着玩的项目，仅用于软件学习交流，下载后于24小时内删除。不得牟利。</p>
              <p><strong>声明：</strong>严禁任何个人或组织将本程序、配套服务端及相关工具链用于任何形式的商业转售牟利、付费代理、搭建公开联运或任何破坏他人计算机信息系统之行为。</p>
            </Card.Content>
          </Card>

          <Card className="border border-separator bg-surface">
            <Card.Header>
              <Card.Title className="text-base font-semibold text-foreground flex items-center gap-2">
                <IconBadge icon={AlertCircle} tone="warning" size="sm" />
                § 2. 知识产权与版权归属声明
              </Card.Title>
            </Card.Header>
            <Card.Content className="text-xs text-muted leading-relaxed space-y-2">
              <p>《深空之眼》（Aether Gazer）游戏客户端内的一切美术立绘、3D 角色模型、音频音效、UI 界面素材、世界观设定及文案商标等合法知识产权，均全权归原开发商及发行方（勇仕网络等）所有。</p>
              <p>本程序不包含且不分发游戏客户端的核心私有资源，请广大玩家支持官方正版游戏！</p>
            </Card.Content>
          </Card>

          <Card className="border border-separator bg-surface">
            <Card.Header>
              <Card.Title className="text-base font-semibold text-foreground flex items-center gap-2">
                <IconBadge icon={ShieldCheck} tone="accent" size="sm" />
                § 3. 免责声明
              </Card.Title>
            </Card.Header>
            <Card.Content className="text-xs text-muted leading-relaxed space-y-2">
              <p>本程序仅供个人学习交流使用，不承担任何因使用本程序而产生的直接或间接损失。使用者需自行承担使用本程序可能带来的所有风险。</p>
            </Card.Content>
          </Card>

          {/* 底部印章 */}
          <div className="flex flex-wrap items-center justify-between gap-4 pt-4 border-t border-separator text-xs text-muted">
            <div className="flex items-center gap-2">
              <span className="inline-block size-2 rounded-full bg-success animate-pulse" />
              <span>你不说就证明已生效喽（Ciallo～(∠・ω&lt; )⌒☆） (PERMANENT)</span>
            </div>
            <div className="flex items-center gap-2 rounded-lg border border-accent/40 bg-accent-soft px-3 py-1 text-accent-soft-foreground font-mono text-[11px]">
              <ShieldCheck className="size-3.5" />
              HELA
            </div>
          </div>
        </Tabs.Panel>
      </Tabs>
    </div>
  )
}

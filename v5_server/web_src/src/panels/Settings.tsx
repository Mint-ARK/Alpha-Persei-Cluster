import { useState, useEffect, useCallback } from 'react'
import {
  Database,
  RotateCw,
  CreditCard,
  Award,
  AlertCircle,
  CheckCircle2,
  Sparkles,
  ShieldAlert,
  Undo2,
  Save,
  Zap,
  Globe,
} from 'lucide-react'
import {
  Button,
  Card,
  Description,
  Input,
  Label,
  ListBox,
  Select,
  TextField,
  cn,
} from '@heroui/react'
import {
  DataList,
  DataRow,
  FieldGrid,
  PageHeader,
  Section,
} from '../components/kit'
import ModelContributorsChart from '../components/ModelContributorsChart'
import IDEContributorsGrid from '../components/IDEContributorsGrid'
import { useServer } from '../lib/serverContext'
import {
  backupDatabase,
  hotReloadServer,
  fetchRechargeStatus,
  resetRechargeScope,
  getStoredApiBase,
  setStoredApiBase,
  type RechargeStatusData,
  type RechargeResetScope,
} from '../lib/api'

const densityOptions = [
  { id: 'comfortable', label: '标准' },
  { id: 'compact', label: '紧凑' },
]

export default function SettingsPanel() {
  const {
    resVersionInfo,
    activeUid,
    consoleName,
    setConsoleName,
    operatorName,
    setOperatorName,
    serverZones,
    defaultZoneId,
    setDefaultZoneId,
    saveServerZones,
  } = useServer()

  // 基础设置状态与本地持久化
  const [name, setName] = useState(consoleName)
  const [operator, setOperator] = useState(operatorName)
  const [selectedServer, setSelectedServer] = useState(defaultZoneId)
  const [density, setDensity] = useState(() => localStorage.getItem('cp_density') || 'comfortable')

  // 区服自定义名称编辑状态
  const [zoneNames, setZoneNames] = useState<Record<string, string>>({})
  const [zoneSaving, setZoneSaving] = useState(false)

  useEffect(() => {
    setName(consoleName)
  }, [consoleName])

  useEffect(() => {
    setOperator(operatorName)
  }, [operatorName])

  useEffect(() => {
    setSelectedServer(defaultZoneId)
  }, [defaultZoneId])

  useEffect(() => {
    const map: Record<string, string> = {}
    serverZones.forEach((z) => {
      map[z.serverId] = z.serverName
    })
    setZoneNames(map)
  }, [serverZones])

  // Toast 消息
  const [toastMsg, setToastMsg] = useState<{
    text: string
    tone: 'success' | 'warning' | 'danger' | 'info'
  } | null>(null)

  const showToast = (tone: 'success' | 'warning' | 'danger' | 'info', text: string) => {
    setToastMsg({ tone, text })
    setTimeout(() => {
      setToastMsg((cur) => (cur?.text === text ? null : cur))
    }, 3500)
  }

  const handleSaveSettings = () => {
    setConsoleName(name.trim() || 'V5 控制面板')
    setOperatorName(operator.trim() || 'admin')
    setDefaultZoneId(selectedServer)
    localStorage.setItem('cp_density', density)
    showToast('success', '偏好设置已成功保存并实时更新顶栏与侧边栏')
  }

  const handleResetSettings = () => {
    const defName = 'V5 控制面板'
    const defOp = 'admin'
    const defServer = '1'
    const defDensity = 'comfortable'
    setName(defName)
    setOperator(defOp)
    setSelectedServer(defServer)
    setDensity(defDensity)
    setConsoleName(defName)
    setOperatorName(defOp)
    setDefaultZoneId(defServer)
    localStorage.setItem('cp_density', defDensity)
    showToast('info', '偏好设置已恢复为初始默认值')
  }

  // 保存自定义区服名称到服务端
  const handleSaveZoneNames = async () => {
    setZoneSaving(true)
    try {
      const updatedZones = serverZones.map((z) => ({
        ...z,
        serverName: (zoneNames[z.serverId] || z.serverName).trim() || z.serverName,
      }))
      const ok = await saveServerZones({
        default_zone_id: selectedServer,
        zones: updatedZones,
      })
      if (ok) {
        showToast('success', '区服名称已成功保存至服务端，客户端选服列表已同步生效！')
      } else {
        showToast('danger', '保存区服配置失败，请确认服务端状态')
      }
    } catch (e: any) {
      showToast('danger', e?.message || '保存区服配置网络异常')
    } finally {
      setZoneSaving(false)
    }
  }

  // 服务端运维状态
  const [backupLoading, setBackupLoading] = useState(false)
  const [hotReloadLoading, setHotReloadLoading] = useState(false)
  const [lastBackupInfo, setLastBackupInfo] = useState<string | null>(null)

  const handleBackupDb = async () => {
    setBackupLoading(true)
    try {
      const res = await backupDatabase()
      if (res && res.code === 0 && res.data) {
        setLastBackupInfo(res.data.backup_filename)
        showToast('success', `数据库快照备份成功: ${res.data.backup_filename}`)
      } else {
        showToast('danger', res?.msg || '备份数据库快照失败')
      }
    } catch (e: any) {
      showToast('danger', e?.message || '备份网络请求异常')
    } finally {
      setBackupLoading(false)
    }
  }

  const handleHotReload = async () => {
    setHotReloadLoading(true)
    try {
      const res = await hotReloadServer()
      if (res && res.code === 0) {
        const mods = res.data?.reloaded_modules?.join(', ') || '核心服务'
        showToast('success', `服务配置热重载完成: [${mods}]`)
      } else {
        showToast('danger', res?.msg || '热重载服务配置失败')
      }
    } catch (e: any) {
      showToast('danger', e?.message || '热重载网络请求异常')
    } finally {
      setHotReloadLoading(false)
    }
  }

  // 战令与充值状态管理
  const [rechargeData, setRechargeData] = useState<RechargeStatusData | null>(null)
  const [rechargeLoading, setRechargeLoading] = useState(false)
  const [resettingScope, setResettingScope] = useState<string | null>(null)

  const loadRechargeStatus = useCallback(async () => {
    if (!activeUid) return
    setRechargeLoading(true)
    try {
      const res = await fetchRechargeStatus(activeUid)
      if (res && res.code === 0 && res.data) {
        setRechargeData(res.data)
      }
    } catch (e) {
      // 离线时不打断主流程
    } finally {
      setRechargeLoading(false)
    }
  }, [activeUid])

  useEffect(() => {
    loadRechargeStatus()
  }, [loadRechargeStatus])

  const handleResetRecharge = async (scope: RechargeResetScope, label: string) => {
    setResettingScope(scope)
    try {
      const res = await resetRechargeScope(scope, activeUid)
      if (res && res.code === 0) {
        showToast('success', `已成功重置【${label}】数据`)
        await loadRechargeStatus()
      } else {
        showToast('danger', res?.msg || `重置【${label}】失败`)
      }
    } catch (e: any) {
      showToast('danger', e?.message || '重置网络请求异常')
    } finally {
      setResettingScope(null)
    }
  }

  return (
    <div className="relative space-y-6">
      {/* 浮动提示通知 */}
      {toastMsg && (
        <div
          className={cn(
            'fixed top-6 right-6 z-50 flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium shadow-xl backdrop-blur-md transition-all animate-in fade-in slide-in-from-top-2',
            toastMsg.tone === 'success' && 'bg-emerald-500/15 border border-emerald-500/30 text-emerald-400',
            toastMsg.tone === 'warning' && 'bg-amber-500/15 border border-amber-500/30 text-amber-400',
            toastMsg.tone === 'danger' && 'bg-rose-500/15 border border-rose-500/30 text-rose-400',
            toastMsg.tone === 'info' && 'bg-cyan-500/15 border border-cyan-500/30 text-cyan-400'
          )}
        >
          {toastMsg.tone === 'success' && <CheckCircle2 className="size-4 shrink-0" />}
          {toastMsg.tone === 'danger' && <AlertCircle className="size-4 shrink-0" />}
          {toastMsg.tone === 'warning' && <AlertCircle className="size-4 shrink-0" />}
          {toastMsg.tone === 'info' && <Sparkles className="size-4 shrink-0" />}
          <span>{toastMsg.text}</span>
        </div>
      )}

      <PageHeader
        title="系统设置"
        description="控制台标识偏好、网关区服自定义及服务端运维工具。修改设置后将实时同步至顶栏、侧边栏及游戏客户端选服列表。"
        actions={
          <>
            <Button size="sm" variant="secondary" className="gap-1.5" onPress={handleResetSettings}>
              <Undo2 className="size-3.5" />
              恢复默认
            </Button>
            <Button size="sm" className="gap-1.5" onPress={handleSaveSettings}>
              <Save className="size-3.5" />
              保存设置
            </Button>
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-5">
        {/* 基础设置与区服自定义 (占 2 列) */}
        <Section title="基础设置与区服定义" description="控制台标识、操作员与服务端区服名称调配" className="lg:col-span-2">
          <Card className="h-full p-5">
            <Card.Content className="space-y-4">
              <FieldGrid cols={1}>
                <TextField fullWidth value={name} onChange={setName}>
                  <Label>控制台名称</Label>
                  <Input placeholder="控制台名称（同步显示于顶栏左上角）" />
                </TextField>

                <TextField fullWidth value={operator} onChange={setOperator}>
                  <Label>操作人标识</Label>
                  <Input className="font-mono" placeholder="admin（同步显示于顶栏右上角）" />
                </TextField>

                <Select
                  fullWidth
                  selectedKey={selectedServer}
                  onSelectionChange={(key) => setSelectedServer(String(key))}
                >
                  <Label>默认区服</Label>
                  <Select.Trigger>
                    <Select.Value />
                    <Select.Indicator />
                  </Select.Trigger>
                  <Select.Popover>
                    <ListBox>
                      {serverZones.map((zone) => (
                        <ListBox.Item key={zone.serverId} id={zone.serverId}>
                          {zoneNames[zone.serverId] || zone.serverName} (S{zone.serverId})
                        </ListBox.Item>
                      ))}
                    </ListBox>
                  </Select.Popover>
                </Select>

                <Select
                  fullWidth
                  selectedKey={density}
                  onSelectionChange={(key) => setDensity(String(key))}
                >
                  <Label>列表密度</Label>
                  <Select.Trigger>
                    <Select.Value />
                    <Select.Indicator />
                  </Select.Trigger>
                  <Select.Popover>
                    <ListBox>
                      {densityOptions.map((option) => (
                        <ListBox.Item key={option.id} id={option.id}>
                          {option.label}
                        </ListBox.Item>
                      ))}
                    </ListBox>
                  </Select.Popover>
                </Select>
              </FieldGrid>

              {/* 区服自定义名称行内编辑区域 */}
              <div className="rounded-xl border border-separator/80 bg-default/25 p-3 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-xs font-semibold">
                    <Globe className="size-3.5 text-accent" />
                    <span>网关区服名称自定义</span>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 gap-1 px-2 text-[11px]"
                    isDisabled={zoneSaving}
                    onPress={handleSaveZoneNames}
                  >
                    <Save className={cn('size-3', zoneSaving && 'animate-spin')} />
                    保存服名
                  </Button>
                </div>

                <div className="space-y-2">
                  {serverZones.map((zone) => (
                    <div key={zone.serverId} className="flex items-center gap-2">
                      <span className="shrink-0 font-mono text-xs text-muted w-10">S{zone.serverId}:</span>
                      <input
                        type="text"
                        value={zoneNames[zone.serverId] ?? zone.serverName}
                        onChange={(e) => {
                          const val = e.target.value
                          setZoneNames((prev) => ({ ...prev, [zone.serverId]: val }))
                        }}
                        className="flex-1 rounded-lg border border-separator/80 bg-surface px-2.5 py-1 text-xs text-foreground placeholder:text-muted focus:border-accent focus:outline-none transition-colors"
                        placeholder={`区服 ${zone.serverId} 名称`}
                      />
                    </div>
                  ))}
                </div>
                <div className="text-[11px] leading-relaxed text-muted">
                  提示：在此处自定义的区服名称将直连写入服务端并在选服接口动态返回，玩家启动游戏进入选服界面时即可看到自定义名称。
                </div>
              </div>

              <TextField
                fullWidth
                defaultValue={getStoredApiBase()}
                onChange={(e: any) => setStoredApiBase(e.target.value)}
              >
                <Label>服务端自定义 API 基础路径</Label>
                <Input className="font-mono" placeholder="留空默认同源相对路径 (自动跟随当前访问主机)" />
                <Description>仅用于控制台自身的接口请求，留空自动跟随当前页面主机与端口</Description>
              </TextField>
            </Card.Content>
          </Card>
        </Section>

        {/* 战令与充值状态管理 (占 3 列) */}
        <Section
          title="战令与充值状态管理"
          description="针对当前活跃账号的付费状态、战令等级、首充双倍及新手福利快速查看与原子重置"
          className="lg:col-span-3"
        >
          <Card className="flex h-full flex-col justify-between p-5">
            <Card.Content className="space-y-4">
              <div className="flex items-center justify-between border-b border-separator pb-3">
                <div className="flex items-center gap-2">
                  <CreditCard className="size-4 text-accent" />
                  <span className="text-sm font-semibold">目标玩家 UID:</span>
                  <span className="font-mono text-sm font-bold text-accent">{activeUid || 10001}</span>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 gap-1 px-2 text-xs"
                  isDisabled={rechargeLoading}
                  onPress={loadRechargeStatus}
                >
                  <RotateCw className={cn('size-3.5', rechargeLoading && 'animate-spin')} />
                  刷新状态
                </Button>
              </div>

              {/* 战令与充值核心状态展示卡 */}
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <div className="rounded-xl border border-separator bg-default/40 p-3">
                  <div className="text-xs text-muted">战令开通状态</div>
                  <div className="mt-1 flex items-center gap-1.5 font-semibold">
                    <Award className="size-4 text-warning" />
                    <span>
                      {rechargeData?.battlepass?.pay_level === 0
                        ? '未开通'
                        : rechargeData?.battlepass?.pay_level === 1
                        ? '普通进阶战令'
                        : rechargeData?.battlepass?.pay_level === 2
                        ? '典藏进阶战令'
                        : '未就绪'}
                    </span>
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-muted">
                    经验(ID:14): {rechargeData?.battlepass?.exp_14 ?? 0}
                  </div>
                </div>

                <div className="rounded-xl border border-separator bg-default/40 p-3">
                  <div className="text-xs text-muted">累充总金额</div>
                  <div className="mt-1 font-mono text-base font-bold text-success">
                    ¥{rechargeData?.total_recharge_yuan?.toFixed(2) ?? '0.00'}
                  </div>
                  <div className="mt-1 text-[11px] text-muted">
                    累计 {rechargeData?.total_recharge_num ?? 0} 积分 (元)
                  </div>
                </div>

                <div className="rounded-xl border border-separator bg-default/40 p-3 sm:col-span-1 col-span-2">
                  <div className="text-xs text-muted">首充双倍状态</div>
                  <div className="mt-1 flex items-center gap-1.5 font-semibold">
                    <Sparkles className="size-4 text-cyan-400" />
                    <span>已充 {rechargeData?.first_recharge_ids?.length ?? 0} 档</span>
                  </div>
                  <div className="mt-1 text-[11px] text-muted">
                    {rechargeData?.first_recharge_ids && rechargeData.first_recharge_ids.length > 0
                      ? `已消ID: ${rechargeData.first_recharge_ids.slice(0, 3).join(',')}${
                          rechargeData.first_recharge_ids.length > 3 ? '...' : ''
                        }`
                      : '各档位首充双倍均保留'}
                  </div>
                </div>
              </div>

              {/* 新手福利与货币情况 */}
              <div className="rounded-xl border border-separator/80 bg-default/20 p-3 text-xs">
                <div className="mb-2 font-medium text-muted">附加福利与货币状态:</div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 font-mono text-[11px]">
                  <div>首充领奖1: {rechargeData?.noob_welfare?.fr_first_gear ? '已领' : '未领'}</div>
                  <div>首充领奖2: {rechargeData?.noob_welfare?.fr_second_gear ? '已领' : '未领'}</div>
                  <div>18元签到天数: {rechargeData?.noob_welfare?.fr_now_sign ?? 0} 天</div>
                  <div>月卡激活标记: {rechargeData?.noob_welfare?.mc_flag ? '激活' : '未激活'}</div>
                </div>
              </div>
            </Card.Content>

            {/* 快速重置工具栏 */}
            <Card.Footer className="flex flex-wrap items-center justify-between gap-2 border-t border-separator pt-4">
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  isDisabled={resettingScope !== null}
                  onPress={() => handleResetRecharge('battlepass', '战令与领奖状态')}
                >
                  {resettingScope === 'battlepass' && <RotateCw className="size-3.5 animate-spin" />}
                  重设战令
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  isDisabled={resettingScope !== null}
                  onPress={() => handleResetRecharge('first_recharge', '首充双倍')}
                >
                  {resettingScope === 'first_recharge' && <RotateCw className="size-3.5 animate-spin" />}
                  重设首充双倍
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  isDisabled={resettingScope !== null}
                  onPress={() => handleResetRecharge('noob_welfare', '新手福利')}
                >
                  {resettingScope === 'noob_welfare' && <RotateCw className="size-3.5 animate-spin" />}
                  重设新手福利
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  isDisabled={resettingScope !== null}
                  onPress={() => handleResetRecharge('total_recharge', '累充总额')}
                >
                  {resettingScope === 'total_recharge' && <RotateCw className="size-3.5 animate-spin" />}
                  重设累充额度
                </Button>
              </div>

              <Button
                size="sm"
                variant="danger"
                className="gap-1.5"
                isDisabled={resettingScope !== null}
                onPress={() => handleResetRecharge('all', '全部付费数据')}
              >
                {resettingScope === 'all' ? (
                  <RotateCw className="size-3.5 animate-spin" />
                ) : (
                  <ShieldAlert className="size-3.5" />
                )}
                全部付费重置
              </Button>
            </Card.Footer>
          </Card>
        </Section>
      </div>

      <div className="grid gap-4 lg:grid-cols-5">
        {/* 环境摘要 (占 3 列，集成服务热重载与数据库备份) */}
        <Section title="环境摘要与服务运维" description="当前单机沙盒的运行参数、数据快照与服务配置热载" className="lg:col-span-3">
          <Card className="h-full p-5">
            <Card.Content className="gap-0">
              <DataList>
                <DataRow label="环境标识" value={<span className="font-mono text-xs">sandbox-v5 (离线单机)</span>} />
                <DataRow label="服务端版本" value={<span className="font-mono text-xs">v5.2.1-sandbox</span>} />
                <DataRow label="协议版本" value={<span className="font-mono text-xs">v5 / codec-3</span>} />
                <DataRow label="当前数据源" value={<span className="font-mono text-xs font-semibold text-success">account.db [主数据库 / 离线独占]</span>} />
                <DataRow
                  label="资源分发版本"
                  value={
                    <span className="font-mono text-xs font-semibold text-accent">
                      {resVersionInfo?.version_name || 'v3.0.7_229'} (Build {resVersionInfo?.current_version || '229'}) · 在线热切就绪
                    </span>
                  }
                />
                <DataRow label="数据库体积" value="13.9 MB · 48 张数据表" />
                <DataRow label="活跃连接" value="单机独占 (1 连接)" />
                {lastBackupInfo && (
                  <DataRow
                    label="最近快照"
                    value={<span className="font-mono text-xs text-success">{lastBackupInfo}</span>}
                  />
                )}
              </DataList>
            </Card.Content>
            <Card.Footer className="flex flex-wrap gap-2 pt-3">
              <Button
                size="sm"
                variant="secondary"
                className="gap-1.5"
                isDisabled={backupLoading}
                onPress={handleBackupDb}
              >
                {backupLoading ? <RotateCw className="size-4 animate-spin" /> : <Database className="size-4" />}
                备份数据库
              </Button>
              <Button
                size="sm"
                variant="primary"
                className="gap-1.5"
                isDisabled={hotReloadLoading}
                onPress={handleHotReload}
              >
                {hotReloadLoading ? <RotateCw className="size-4 animate-spin" /> : <Zap className="size-4" />}
                服务配置热重载
              </Button>
            </Card.Footer>
          </Card>
        </Section>

        {/* 关于 (占 2 列) */}
        <Section title="关于" description="构建与依赖信息" className="lg:col-span-2">
          <Card className="h-full p-5">
            <Card.Content className="gap-0">
              <DataList>
                <DataRow label="面板版本" value={<span className="font-mono">v2.0.0</span>} />
                <DataRow label="UI 框架" value={<span className="font-mono">HeroUI v3.2.4</span>} />
                <DataRow label="React" value={<span className="font-mono">19</span>} />
                <DataRow label="Tailwind CSS" value={<span className="font-mono">v4</span>} />
                <DataRow label="数据通道" value="CQRS 无锁只读 / WAL 事务原子持久化" />
                <DataRow label="热载支持" value="公告/抽卡/商城/GM接口热刷新" />
              </DataList>
            </Card.Content>
          </Card>
        </Section>
      </div>

      {/* 模型功勋册 */}
      <Section
        title="参与本项目构建的模型"
        description="统计了本项目中所有参与设计、问题解决、思路讨论、数据搜集、素材整理的模型贡献者，并按照贡献大小进行排名。没有他们，项目可能根本不会存在。"
      >
        <ModelContributorsChart />
      </Section>

      {/* 20款 IDE 与关键工具网格 */}
      <Section
        title="参与本项目构建的 IDE 与工程环境"
        description="记录了在项目逆向工程、协议分析、底层调试、引擎解构、原生应用构建及智能体结对编码中发挥核心作用的 20 款关键工具与工作台，排名不分先后。"
      >
        <IDEContributorsGrid />
      </Section>
    </div>
  )
}

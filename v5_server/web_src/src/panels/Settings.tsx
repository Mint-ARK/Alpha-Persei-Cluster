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
  PlainSwitch,
} from '../components/kit'
import ModelContributorsChart from '../components/ModelContributorsChart'
import IDEContributorsGrid from '../components/IDEContributorsGrid'
import { useServer } from '../lib/serverContext'
import {
  backupDatabase,
  hotReloadServer,
  fetchRechargeStatus,
  resetRechargeScope,
  updatePaidRights,
  type PaidRightsPatch,
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
    status,
    resVersionInfo,
    activeUid,
    consoleName,
    setConsoleName,
    operatorName,
    setOperatorName,
    managedSave,
  } = useServer()

  // 基础设置状态与本地持久化
  const [name, setName] = useState(consoleName)
  const [operator, setOperator] = useState(operatorName)
  const [apiBase, setApiBase] = useState(getStoredApiBase)
  const [density, setDensity] = useState(() => localStorage.getItem('cp_density') || 'comfortable')

  useEffect(() => {
    setName(consoleName)
  }, [consoleName])

  useEffect(() => {
    setOperator(operatorName)
  }, [operatorName])

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
    const address = apiBase.trim().replace(/\/+$/, '')
    if (address) {
      try {
        const url = new URL(address)
        if (!['http:', 'https:'].includes(url.protocol)) throw new Error('invalid protocol')
      } catch {
        showToast('danger', '请填写完整的http/https服务地址，或留空使用当前主机')
        return
      }
    }
    setConsoleName(name.trim() || 'V5 控制面板')
    setOperatorName(operator.trim() || 'admin')
    localStorage.setItem('cp_density', density)
    showToast('success', '偏好设置已成功保存并实时更新顶栏与侧边栏')
    if (address !== getStoredApiBase()) setStoredApiBase(address)
  }

  const handleResetSettings = () => {
    const defName = 'V5 控制面板'
    const defOp = 'admin'
    const defDensity = 'comfortable'
    setName(defName)
    setOperator(defOp)
    setApiBase(getStoredApiBase())
    setDensity(defDensity)
    setConsoleName(defName)
    setOperatorName(defOp)
    localStorage.setItem('cp_density', defDensity)
    showToast('info', '偏好设置已恢复为初始默认值')
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
  const [rightsSaving, setRightsSaving] = useState(false)

  const changeRights = async (changes: PaidRightsPatch) => {
    if (!activeUid || rightsSaving) return
    setRightsSaving(true)
    try {
      const res = await updatePaidRights(changes, activeUid)
      if (res.code !== 0) throw new Error(res.msg || '权益保存失败')
      await loadRechargeStatus()
      showToast('success', '权益状态已保存')
    } catch (e: any) {
      showToast('danger', e?.message || '权益保存失败')
    } finally {
      setRightsSaving(false)
    }
  }

  const displayDeadline = (ts: number | undefined) => ts
    ? new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false }) : '未购买'

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
        description="控制台显示偏好、当前存档工作区与服务端运维工具。"
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
        {/* 基础设置与当前存档 (占 2 列) */}
        <Section title="基础设置与存档工作区" description="控制台标识、操作员与当前管理存档" className="lg:col-span-2">
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

                <TextField fullWidth isReadOnly value={managedSave?.name || '尚未选择存档'}>
                  <Label>当前管理存档</Label><Input />
                  <Description className="break-words">{managedSave
                    ? `${managedSave.nick} · Lv.${managedSave.level} · UID ${managedSave.uid}`
                    : '请在顶栏选择存档'}</Description>
                </TextField>

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

              {/* 存档管理统一入口 */}
              <div className="rounded-xl border border-separator/80 bg-default/25 p-3 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-xs font-semibold">
                    <Database className="size-3.5 text-accent" />
                    <span>存档管理</span>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 gap-1 px-2 text-[11px]"
                    onPress={() => window.dispatchEvent(new CustomEvent('nav-to-tab', { detail: 'saves' }))}
                  >
                    打开存档管理
                  </Button>
                </div>

                <div className="text-[11px] leading-relaxed text-muted">
                  顶栏选择需要管理的存档；新建存档和生成参数请进入存档管理页。游戏内也通过选区列表选择对应存档。
                </div>
              </div>

              <TextField
                fullWidth
                value={apiBase}
                onChange={setApiBase}
              >
                <Label>控制面板服务端地址</Label>
                <Input className="font-mono" placeholder="留空默认同源相对路径 (自动跟随当前访问主机)" />
                <Description>用于面板连接游戏服务端，保存设置后应用；留空跟随当前页面地址。AI模型接口与密钥请到“AI修正者”配置。</Description>
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
                        : rechargeData?.battlepass?.pay_level === 201
                        ? '进阶合约'
                        : rechargeData?.battlepass?.pay_level === 202
                        ? '深度合约'
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
                  <div>月卡新手福利标记: {rechargeData?.noob_welfare?.mc_flag ? '已开启' : '未开启'}</div>
                </div>
              </div>

              <div className="space-y-4 rounded-xl border border-separator p-4">
                <Select fullWidth selectedKey={String(rechargeData?.battlepass?.pay_level ?? 0)}
                  isDisabled={!rechargeData || rightsSaving || rechargeLoading || resettingScope !== null}
                  onSelectionChange={(key) => {
                    const tier = Number(key)
                    if ([0, 201, 202].includes(tier)) changeRights({ pay_level: tier as 0 | 201 | 202 })
                  }}>
                  <Label>本期战令档位</Label>
                  <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
                  <Select.Popover><ListBox>
                    <ListBox.Item id="0">免费合约</ListBox.Item>
                    <ListBox.Item id="201">进阶合约</ListBox.Item>
                    <ListBox.Item id="202">深度合约</ListBox.Item>
                  </ListBox></Select.Popover>
                  <Description>每周一05:00换期，保留档位；深度赠礼每期一封，切换不会重复发。</Description>
                </Select>
                <div className="text-xs text-muted">
                  本期结束：{displayDeadline(rechargeData?.battlepass?.cycle_end)} · 深度赠礼：
                  {rechargeData?.battlepass?.premium_gift_issued ? '本期已发' : '本期待发'}
                </div>
                <div className="flex items-center justify-between gap-4 border-t border-separator pt-3">
                  <div className="min-w-0 space-y-1">
                    <div className="text-sm font-medium">恒定观测 · 长期权益</div>
                    <div className="text-xs text-muted">每日90移转之辉，关闭长期权益后已购余期仍有效。</div>
                    <div className="text-xs text-muted">
                      {rechargeData?.small_month_card?.active ? '生效中' : '未生效'} ·
                      今日{rechargeData?.small_month_card?.signed_today ? '已领取' : '未领取'}
                    </div>
                    <div className="break-words text-xs text-muted">已购到期：{displayDeadline(rechargeData?.small_month_card?.paid_until)}</div>
                  </div>
                  <PlainSwitch label="恒定观测长期权益"
                    isSelected={!!rechargeData?.small_month_card?.long_term}
                    isDisabled={!rechargeData || rightsSaving || rechargeLoading || resettingScope !== null}
                    onChange={(value) => changeRights({ small_long_term: value })} />
                </div>
                <div className="flex items-center justify-between gap-4 border-t border-separator pt-3">
                  <div className="min-w-0 space-y-1">
                    <div className="text-sm font-medium">百宝囊 · 自动续期</div>
                    <div className="text-xs text-muted">每30天自然轮换；关闭续期保留当前有效期，遗漏奖励通过邮件补发。</div>
                    <div className="text-xs text-muted">
                      {rechargeData?.big_month_card?.active ? '生效中' : '未生效'} ·
                      本期已结算{rechargeData?.big_month_card?.sign_days ?? 0}天 ·
                      今日{rechargeData?.big_month_card?.signed_today ? '已领取' : '未领取'}
                    </div>
                    <div className="break-words text-xs text-muted">本期结束：{displayDeadline(rechargeData?.big_month_card?.cycle_end)}</div>
                  </div>
                  <PlainSwitch label="百宝囊自动续期"
                    isSelected={!!rechargeData?.big_month_card?.auto_renew}
                    isDisabled={!rechargeData || rightsSaving || rechargeLoading || resettingScope !== null}
                    onChange={(value) => changeRights({ big_auto_renew: value })} />
                </div>
                <p className="text-xs leading-relaxed text-muted">周补给25,000经验；日活前三档追加4,000／5,000／6,000，周活前三档追加10,000／12,000／16,000。新增经验不占战令任务周额度。权益开关不计累充，游戏购买照常计入。</p>
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
                <DataRow label="运行模式" value="单机服务 / 多存档管理" />
                <DataRow label="服务端版本" value={<span className="font-mono text-xs">{status?.version || '未读取'}</span>} />
                <DataRow label="游戏服务端口" value={<span className="font-mono text-xs">{status?.network?.game_port || status?.game_port || '—'}</span>} />
                <DataRow label="当前数据源" value={<span className="inline-block max-w-60 break-all text-right font-mono text-xs font-semibold text-success">{managedSave?.filename || '—'}</span>} />
                <DataRow
                  label="资源分发版本"
                  value={
                    <span className="font-mono text-xs font-semibold text-accent">
                      {resVersionInfo ? `${resVersionInfo.version_name} (Build ${resVersionInfo.current_version})` : '未读取'}
                    </span>
                  }
                />
                <DataRow label="存档槽位" value={managedSave?.slot_id ?? '未登记'} />
                <DataRow label="选区网关端口" value={managedSave?.port ?? '未登记'} />
                <DataRow label="在线玩家" value={status?.online_users ?? '未读取'} />
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

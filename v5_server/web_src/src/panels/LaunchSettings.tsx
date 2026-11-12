import { useEffect, useState } from 'react'
import { Button, Card, Chip, FieldError, Input, Label, ListBox, Modal, Select, TextField } from '@heroui/react'
import { Copy, RefreshCw, RotateCcw, Save } from 'lucide-react'
import { PageHeader, PlainSwitch, Section } from '../components/kit'
import { fetchLaunchConfig, fetchPaySwitch, saveLaunchConfig, setPaySwitch,
  type LaunchConfigData, type LaunchOptions, type PaySwitchData } from '../lib/api'
import { buildLaunchArguments, formatLaunchCommand, type LaunchShell } from '../lib/launchCommand'
import { useServer } from '../lib/serverContext'

const ports = [
  ['https_port', 'SDK / HTTPS 端口', 1],
  ['http_port', 'HTTP 端口（0 关闭）', 0],
  ['gw_port', '网关端口起点', 1],
  ['game_port', '游戏 TCP 端口', 1],
  ['battle_port', '战斗 UDP 端口（0 关闭）', 0],
] as const
type Port = (typeof ports)[number][0]

function validPort(value: string, minimum: number) {
  return /^\d{1,5}$/.test(value) && Number(value) >= minimum && Number(value) <= 65535
}
const labels: Record<keyof LaunchOptions, string> = {
  https_port: 'SDK / HTTPS', http_port: 'HTTP', web_bind: 'Web 监听地址',
  gw_port: '网关起点', game_port: '游戏 TCP', battle_port: '战斗 UDP',
  host_ip: '对外发布 IPv4', dns: '内置 DNS', dns_upstream: 'DNS 上游',
  cdn_mode: 'CDN 模式', db_path: '启动存档', client_assets_dir: 'PC 客户端资源路径',
  log_level: '日志级别', replay_enabled: '全流程重放', replay_path: '重放素材',
}
const keys = Object.keys(labels) as (keyof LaunchOptions)[]
const modes = [
  ['off', '关闭'],
  ['relay', '仅穿透'],
  ['cache', '穿透并存盘'],
] as const
const shells = [['cmd', 'Windows CMD'], ['powershell', 'PowerShell'], ['posix', 'Linux / macOS']] as const

function Choice({ label, value, choices, onChange, disabled }: {
  label: string
  value: string
  choices: readonly (readonly [string, string])[]
  onChange: (value: string) => void
  disabled?: boolean
}) {
  return (
    <Select selectedKey={value || null} onSelectionChange={(key) => { if (key !== null) onChange(String(key)) }}
      isDisabled={disabled} placeholder="请选择">
      <Label>{label}</Label>
      <Select.Trigger><Select.Value className="truncate" /><Select.Indicator /></Select.Trigger>
      <Select.Popover>
        <ListBox>{choices.map(([id, text]) => <ListBox.Item key={id} id={id}>{text}</ListBox.Item>)}</ListBox>
      </Select.Popover>
    </Select>
  )
}

function PayControl() {
  const { resVersionInfo } = useServer()
  const [data, setData] = useState<PaySwitchData | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const eligible = resVersionInfo?.current_version === '229'

  useEffect(() => {
    let active = true
    if (eligible) {
      fetchPaySwitch().then(({ data }) => { if (active) setData(data) })
        .catch((cause: Error) => { if (active) setError(cause.message) })
    }
    return () => { active = false }
  }, [eligible])

  const iosBrowser = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
  if (!eligible || (!iosBrowser && data?.client_platform !== 'ios')) return null

  async function update(enabled: boolean) {
    setBusy(true)
    setError('')
    try {
      setData((await setPaySwitch(enabled)).data)
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><p className="text-sm font-medium">开放游戏内直购</p><p className="text-xs text-muted">iOS 229 · 即时生效</p></div>
      <PlainSwitch label="开放游戏内直购" isSelected={!!data?.ios_pay_enabled} isDisabled={busy || !data}
        onChange={(enabled) => void update(enabled)} />
      {error && <p className="basis-full text-xs text-danger" role="alert">{error}</p>}
    </div>
  )
}

export default function LaunchSettings() {
  const { saves } = useServer()
  const [stored, setStored] = useState<LaunchConfigData | null>(null)
  const [draft, setDraft] = useState<LaunchOptions | null>(null)
  const [portText, setPortText] = useState<Partial<Record<Port, string>>>({})
  const [upstream, setUpstream] = useState('')
  const [shell, setShell] = useState<LaunchShell>(navigator.userAgent.includes('Windows') ? 'cmd' : 'posix')
  const [busy, setBusy] = useState(true)
  const [confirm, setConfirm] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  function replace(config: LaunchConfigData) {
    if (!config.active || !Array.isArray(config.dns_upstream)) {
      throw new Error('当前内核尚未加载新版启动配置接口，请重启后再试')
    }
    setStored(config)
    setDraft(config)
    setPortText({})
    setUpstream(config.dns_upstream.join(', '))
  }

  async function load() {
    setBusy(true)
    setMessage('')
    setError('')
    try {
      replace((await fetchLaunchConfig()).data)
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => { void load() }, [])

  useEffect(() => {
    if (!stored?.restart_pending) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const deadline = Date.now() + 30000
    async function reconnect() {
      try {
        const { data } = await fetchLaunchConfig()
        if (active && !data.restart_pending) {
          replace(data)
          setMessage('内核已重新连接')
          return
        }
      } catch {
        // The listener is unavailable until startup finishes.
      }
      if (active && Date.now() < deadline) timer = setTimeout(reconnect, 1500)
    }
    timer = setTimeout(reconnect, 1500)
    return () => { active = false; clearTimeout(timer) }
  }, [stored?.restart_pending])

  function set<K extends keyof LaunchOptions>(key: K, value: LaunchOptions[K]) {
    setDraft((previous) => previous && { ...previous, [key]: value })
  }

  const portValue = (field: Port) => portText[field] ?? String(draft?.[field] ?? '')
  const portsValid = ports.every(([field, , min]) => validPort(portValue(field), min))
  const form = draft && portsValid ? {
    ...draft,
    ...Object.fromEntries(ports.map(([field]) => [field, Number(portValue(field))])),
    dns_upstream: upstream.split(/[,，\s]+/).filter(Boolean),
  } : null
  const changed = form && stored ? keys.filter((key) => JSON.stringify(form[key]) !== JSON.stringify(stored[key])) : []
  const pending = form && stored ? keys.filter((key) => JSON.stringify(form[key]) !== JSON.stringify(stored.active[key])) : []
  const command = form ? formatLaunchCommand(['--supervised', ...buildLaunchArguments(form)], shell) : ''
  const disabled = busy || !!stored?.restart_pending

  async function save(restart: boolean) {
    if (!form) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const patch = Object.fromEntries(changed.map((key) => [key, form[key]]))
      const response = await saveLaunchConfig({ ...patch, restart })
      replace(response.data)
      setMessage(restart ? '重启请求已发出。若修改了面板监听地址或端口，请从新地址访问。' : response.msg)
      setConfirm(false)
    } catch (cause) {
      setError((cause as Error).message)
      setConfirm(false)
    } finally {
      setBusy(false)
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(command)
      setMessage('启动命令已复制')
      setError('')
    } catch {
      setError('复制失败，可在命令框中手动选择并复制')
    }
  }

  const saveOptions: [string, string][] = saves.map((save) => [save.filename, save.name])
  if (draft?.db_path && !saveOptions.some(([id]) => id === draft.db_path)) {
    saveOptions.unshift([draft.db_path, draft.db_path])
  }
  const replayOptions: [string, string][] = (stored?.available_replays ?? []).map((item) => [item.path, item.name])
  if (draft?.replay_path && !replayOptions.some(([id]) => id === draft.replay_path)) {
    replayOptions.unshift([draft.replay_path, draft.replay_path])
  }

  return (
    <div className="space-y-5">
      <PageHeader title="启动配置" description="管理服务端监听、启动存档和运行选项。" />
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {message && <p role="status" className="text-sm text-success">{message}</p>}
      {draft && stored ? (
        <>
          <Section title="服务配置">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card className="p-5">
                <Card.Header className="p-0"><Card.Title>网络与监听</Card.Title></Card.Header>
                <Card.Content className="grid grid-cols-1 gap-4 px-0 pb-0 pt-4 sm:grid-cols-2">
                  <div>
                    <TextField value={draft.host_ip} onChange={(value) => set('host_ip', value)} isDisabled={disabled}>
                      <Label>对外发布 IPv4</Label><Input placeholder="留空自动检测" />
                    </TextField>
                    {stored.detected_host_ip && (
                      <Button size="sm" variant="ghost" className="mt-1" isDisabled={disabled}
                        onPress={() => set('host_ip', stored.detected_host_ip || '')}>
                        填入检测地址 {stored.detected_host_ip}
                      </Button>
                    )}
                  </div>
                  <TextField value={draft.web_bind} onChange={(value) => set('web_bind', value)} isDisabled={disabled}>
                    <Label>Web 监听地址</Label><Input placeholder="0.0.0.0" />
                  </TextField>
                  {ports.map(([field, label, min]) => (
                    <TextField key={field} value={portValue(field)} isDisabled={disabled}
                      isInvalid={!validPort(portValue(field), min)}
                      onChange={(value) => {
                        if (/^\d{0,5}$/.test(value)) setPortText((previous) => ({ ...previous, [field]: value }))
                      }}>
                      <Label>{label}</Label>
                      <Input inputMode="numeric" pattern="[0-9]*" maxLength={5} />
                      <FieldError>请输入 {min} 至 65535 的整数</FieldError>
                    </TextField>
                  ))}
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm">内置 DNS · 53 端口</span>
                    <PlainSwitch label="内置 DNS" isSelected={draft.dns} isDisabled={disabled}
                      onChange={(value) => set('dns', value)} />
                  </div>
                  <TextField className="sm:col-span-2" value={upstream} onChange={setUpstream} isDisabled={disabled || !draft.dns}>
                    <Label>DNS 上游</Label><Input placeholder="多个 IPv4 地址用逗号分隔" />
                  </TextField>
                </Card.Content>
              </Card>
              <Card className="p-5">
                <Card.Header className="p-0"><Card.Title>运行选项</Card.Title></Card.Header>
                <Card.Content className="space-y-4 px-0 pb-0 pt-4">
                  <Choice label="启动存档" value={draft.db_path} choices={saveOptions} disabled={disabled}
                    onChange={(value) => set('db_path', value)} />
                  <Choice label="CDN 模式" value={draft.cdn_mode} choices={modes} disabled={disabled}
                    onChange={(value) => set('cdn_mode', value as LaunchOptions['cdn_mode'])} />
                  <Choice label="日志级别 · 保存后即时生效" value={draft.log_level} disabled={disabled}
                    choices={(['DEBUG', 'INFO', 'WARN', 'ERROR'] as const).map((level) => [level, level])}
                    onChange={(value) => set('log_level', value as LaunchOptions['log_level'])} />
                  <TextField value={draft.client_assets_dir} onChange={(value) => set('client_assets_dir', value)} isDisabled={disabled}>
                    <Label>PC 客户端资源路径</Label><Input placeholder="留空自动探测" />
                  </TextField>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm">全流程重放</span>
                    <PlainSwitch label="全流程重放" isSelected={draft.replay_enabled}
                      isDisabled={disabled || (!stored.has_replay_material && !draft.replay_enabled)}
                      onChange={(enabled) => {
                        setDraft((previous) => previous && { ...previous, replay_enabled: enabled,
                          replay_path: previous.replay_path || replayOptions[0]?.[0] || '' })
                      }} />
                  </div>
                  {draft.replay_enabled && <Choice label="重放素材" value={draft.replay_path} choices={replayOptions}
                    disabled={disabled} onChange={(value) => set('replay_path', value)} />}
                  <PayControl />
                </Card.Content>
              </Card>
            </div>
          </Section>
          <Card className="p-5">
            <Card.Content className="space-y-4 p-0">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium">当前运行</p>
                  <p className="mt-1 text-xs text-muted break-words">
                    {stored.active.db_path} · {stored.active.web_bind}:{stored.active.https_port} · 网关 {stored.active.gw_port}
                  </p>
                </div>
                <Chip size="sm" variant="soft" color="accent">
                  <Chip.Label>{stored.supervisor === 'windows' ? '启动壳托管' : stored.supervisor === 'systemd' ? 'systemd 托管' : stored.supervisor === 'cli' ? '命令行托管' : '命令行直启'}</Chip.Label>
                </Chip>
              </div>
              {pending.length > 0 && <p className="text-xs text-muted">与当前运行值不同：{pending.map((key) => labels[key]).join('、')}</p>}
              <div className="flex flex-wrap items-end gap-2">
                <Choice label="命令格式" value={shell} choices={shells} onChange={(value) => setShell(value as LaunchShell)} />
                <Button variant="secondary" isDisabled={!command} onPress={() => void copy()}><Copy className="size-4" />复制启动命令</Button>
              </div>
              <pre tabIndex={0} className="whitespace-pre-wrap break-all rounded-xl bg-surface-secondary p-3 text-xs">{command}</pre>
            </Card.Content>
            <Card.Footer className="flex flex-wrap items-center justify-between gap-3 px-0 pb-0 pt-4">
              <Button variant="ghost" isDisabled={busy || changed.length > 0} onPress={() => void load()}>
                <RefreshCw className="size-4" />重新读取
              </Button>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" isDisabled={disabled || !changed.length} onPress={() => void save(false)}>
                  <Save className="size-4" />保存配置
                </Button>
                <Button variant="primary" isDisabled={disabled || !form || !stored.supervisor} onPress={() => setConfirm(true)}>
                  <RotateCcw className="size-4" />保存并重启
                </Button>
              </div>
            </Card.Footer>
            {!stored.supervisor && <p className="mt-2 text-xs text-muted">用上方命令启动后即可从面板重启。</p>}
          </Card>
        </>
      ) : <Button variant="secondary" isDisabled={busy} onPress={() => void load()}>{busy ? '正在读取启动配置' : '重新读取'}</Button>}
      <Modal.Root isOpen={confirm} onOpenChange={(open) => { if (!busy) setConfirm(open) }}>
        <Modal.Backdrop>
          <Modal.Container>
            <Modal.Dialog aria-label="保存并重启内核">
              <Modal.Header><Modal.Heading>保存配置并重启内核</Modal.Heading></Modal.Header>
              <Modal.Body>重启会断开当前游戏连接。使用反向代理时，请同步更新已变更的 Web 监听地址与端口。</Modal.Body>
              <Modal.Footer>
                <Button variant="secondary" isDisabled={busy} onPress={() => setConfirm(false)}>取消</Button>
                <Button variant="primary" isDisabled={busy} onPress={() => void save(true)}>确认重启</Button>
              </Modal.Footer>
            </Modal.Dialog>
          </Modal.Container>
        </Modal.Backdrop>
      </Modal.Root>
    </div>
  )
}

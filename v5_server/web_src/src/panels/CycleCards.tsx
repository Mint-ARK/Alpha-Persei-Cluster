import { useEffect, useState } from 'react'
import { Button, Card, Chip, Modal } from '@heroui/react'
import { RefreshCw, RotateCcw } from 'lucide-react'
import { Section } from '../components/kit'
import { fetchCycles, resetCycle, type CycleCardData, type CycleKind, type CycleRow } from '../lib/api'

const labels: Record<CycleKind, string> = {
  daily: '每日任务',
  weekly: '每周任务',
  seizure: '介质攫取',
  dream: '梦境再构',
  iteration: '迭代校验 · 常规',
  polyhedron: '多维变量',
  battlepass: '战令',
  mythic: '黑区净化',
}
const kinds = Object.keys(labels) as CycleKind[]
const resetDate = new Intl.DateTimeFormat('zh-CN', {
  month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit',
})
const rowHeight = 34
const visibleRows = 6
const stateLabels = { pending: '未完成', ready: '已达成', claimed: '已领取', progress: '' }

function CycleRows({ rows }: { rows: CycleRow[] }) {
  const [scrollTop, setScrollTop] = useState(0)
  const start = Math.min(Math.max(0, Math.floor(scrollTop / rowHeight) - 2), Math.max(0, rows.length - visibleRows))
  const end = start + visibleRows + 5

  return (
    <div className="overflow-y-auto overscroll-contain" onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      style={{ height: rowHeight * visibleRows }} tabIndex={0} aria-label="任务与奖励进度">
      <div role="list" className="relative" style={{ height: rowHeight * rows.length }}>
        {rows.slice(start, end).map((row, index) => (
          <div key={row.id} role="listitem" aria-setsize={rows.length} aria-posinset={start + index + 1}
            className="absolute inset-x-0 flex items-center gap-2 border-b border-separator/60 pr-2 text-xs"
            style={{ top: (start + index) * rowHeight, height: rowHeight }}>
            <span title={row.label} className="min-w-0 flex-1 truncate">{row.label}</span>
            <span className="shrink-0 tabular-nums text-muted">
              {row.current.toLocaleString()}{row.target === null ? '' : ` / ${row.target.toLocaleString()}`}
            </span>
            {row.status !== 'progress' && (
              <Chip size="sm" variant="soft" color={row.status === 'claimed' ? 'success' : row.status === 'ready' ? 'accent' : 'default'}>
                <Chip.Label>{stateLabels[row.status]}</Chip.Label>
              </Chip>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export default function CycleCards({ uid }: { uid: number }) {
  const [cards, setCards] = useState<Partial<Record<CycleKind, CycleCardData>>>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<CycleKind | null>(null)
  const [confirm, setConfirm] = useState<CycleCardData | null>(null)
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    if (!uid) {
      setLoading(false)
      return
    }
    fetchCycles(uid, undefined, controller.signal)
      .then(({ data }) => setCards(Object.fromEntries(data.map((card) => [card.kind, card]))))
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [uid])

  async function update(kind: CycleKind, card?: CycleCardData) {
    setBusy(kind)
    setError('')
    setFeedback('')
    try {
      const next = card ? (await resetCycle(uid, kind, card.token)).data : (await fetchCycles(uid, kind)).data[0]
      setCards((previous) => ({ ...previous, [kind]: next }))
      if (card) setFeedback(`${labels[kind]}已重置，下次自动刷新时间照旧。`)
    } catch (cause) {
      setError(`${labels[kind]}：${(cause as Error).message}`)
    } finally {
      setBusy(null)
      setConfirm(null)
    }
  }

  return (
    <Section title="任务与挑战周期">
      {error && <p role="alert" className="mb-3 text-sm text-danger">{error}</p>}
      {feedback && <p role="status" className="mb-3 text-sm text-success">{feedback}</p>}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kinds.map((kind) => {
          const card = cards[kind]
          return (
            <Card key={kind} className="min-w-0 p-4">
              <Card.Header className="flex flex-row items-center justify-between gap-2 p-0">
                <Card.Title className="text-sm">{labels[kind]}</Card.Title>
                <Button isIconOnly size="sm" variant="ghost" aria-label={`刷新${labels[kind]}进度`}
                  isDisabled={!uid || loading || !!busy} onPress={() => void update(kind)}>
                  <RefreshCw className={`size-4 ${busy === kind && !confirm ? 'animate-spin' : ''}`} />
                </Button>
              </Card.Header>
              <Card.Content className="px-0 py-2">
                {card?.rows.length ? <CycleRows key={card.token} rows={card.rows} /> :
                  <div className="grid place-items-center text-xs text-muted" style={{ height: rowHeight * visibleRows }}>
                    {loading ? '正在读取' : card?.error || (card ? '本期没有进度条目' : '进度暂不可用')}
                  </div>}
              </Card.Content>
              <Card.Footer className="flex flex-wrap items-center justify-between gap-2 border-t border-separator/70 px-0 pb-0 pt-3">
                <span className="text-xs text-muted">
                  {card?.reset_at ? `下次自动刷新 ${resetDate.format(card.reset_at * 1000)}` : '刷新时间未知'}
                </span>
                <Button size="sm" variant="secondary" isDisabled={!card?.token || !!busy}
                  onPress={() => card && setConfirm(card)}>
                  <RotateCcw className="size-3.5" /> 重置本期
                </Button>
              </Card.Footer>
            </Card>
          )
        })}
      </div>
      <Modal.Root isOpen={!!confirm} onOpenChange={(open) => { if (!open && !busy) setConfirm(null) }}>
        <Modal.Backdrop>
          <Modal.Container>
            <Modal.Dialog aria-label="重置本期进度">
              <Modal.Header><Modal.Heading>重置{confirm && labels[confirm.kind]}本期进度</Modal.Heading></Modal.Header>
              <Modal.Body>清空所选存档的本期进度和对应领奖记录。请先退出游戏账号。</Modal.Body>
              <Modal.Footer>
                <Button variant="secondary" isDisabled={!!busy} onPress={() => setConfirm(null)}>取消</Button>
                <Button variant="danger" isDisabled={!!busy} onPress={() => confirm && void update(confirm.kind, confirm)}>
                  {busy ? '正在重置' : '确认重置'}
                </Button>
              </Modal.Footer>
            </Modal.Dialog>
          </Modal.Container>
        </Modal.Backdrop>
      </Modal.Root>
    </Section>
  )
}

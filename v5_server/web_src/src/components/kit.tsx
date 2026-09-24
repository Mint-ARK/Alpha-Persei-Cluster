/**
 * 版面基元
 *
 * 这一层只做「排版与对齐」，不重新发明 HeroUI 已有的视觉：
 * 颜色一律走 HeroUI 语义 token（surface / muted / separator / accent-soft ...），
 * 圆角、阴影、字号沿用组件自带值。目的是让 9 个面板共享同一套间距与基线，
 * 而不是每个面板各写一套 padding。
 */
import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Card, Chip, Switch, cn } from '@heroui/react'

export type Tone = 'default' | 'accent' | 'success' | 'warning' | 'danger'

/** 语义色的「柔和」底 + 前景，用于图标块、标记块 */
export const toneSoft: Record<Tone, string> = {
  default: 'bg-default text-foreground',
  accent: 'bg-accent-soft text-accent-soft-foreground',
  success: 'bg-success-soft text-success-soft-foreground',
  warning: 'bg-warning-soft text-warning-soft-foreground',
  danger: 'bg-danger-soft text-danger-soft-foreground',
}

/** 语义色实心，用于状态点、进度条 */
export const toneSolid: Record<Tone, string> = {
  default: 'bg-muted',
  accent: 'bg-accent',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
}

/* ------------------------------------------------------------------ 图标块 / 状态点 */

export function IconBadge({
  icon: Icon,
  tone = 'default',
  size = 'md',
  className,
}: {
  icon: LucideIcon
  tone?: Tone
  size?: 'sm' | 'md'
  className?: string
}) {
  return (
    <span
      className={cn(
        'grid shrink-0 place-items-center rounded-2xl',
        size === 'sm' ? 'size-8' : 'size-9',
        toneSoft[tone],
        className,
      )}
    >
      <Icon className={size === 'sm' ? 'size-4' : 'size-[18px]'} strokeWidth={2} />
    </span>
  )
}

export function StatusDot({ tone = 'default', pulse = false }: { tone?: Tone; pulse?: boolean }) {
  return (
    <span className="relative grid size-2 shrink-0 place-items-center">
      {pulse && (
        <span
          className={cn(
            'absolute inline-flex size-2 animate-ping rounded-full opacity-60',
            toneSolid[tone],
          )}
        />
      )}
      <span className={cn('relative inline-flex size-2 rounded-full', toneSolid[tone])} />
    </span>
  )
}

/* ------------------------------------------------------------------ 页面 / 区块标题 */

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
      <div className="min-w-0 space-y-1">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">{title}</h1>
        {description && (
          <p className="max-w-[860px] text-sm leading-6 text-muted">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}

export function Section({
  title,
  description,
  actions,
  children,
  className,
}: {
  title?: string
  description?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    // flex-col + flex-1 内容区：区块作为栅格项被拉伸时，内部卡片的 h-full
    // 才会按「行高减去标题高度」计算，否则会溢出压到下一区块上
    <section className={cn('flex flex-col gap-3', className)}>
      {(title || actions) && (
        <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-1.5">
          <div className="min-w-0 space-y-0.5">
            {title && (
              <h2 className="text-[15px] font-semibold tracking-tight text-foreground">{title}</h2>
            )}
            {description && <p className="text-xs leading-5 text-muted">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className="min-h-0 flex-1 flex flex-col">{children}</div>
    </section>
  )
}

/* ------------------------------------------------------------------ 指标卡 */

export function StatCard({
  label,
  value,
  unit,
  icon,
  tone = 'default',
  delta,
  deltaTone = 'success',
  deltaNote,
}: {
  label: string
  value: string
  unit?: string
  icon: LucideIcon
  tone?: Tone
  delta?: string
  deltaTone?: Tone
  deltaNote?: string
}) {
  const isLongText = value.length > 5 && !/^[0-9,./\s%+-]+$/.test(value)

  return (
    <Card className="h-full p-5">
      <Card.Header className="flex-row items-start justify-between gap-3">
        <Card.Title className="min-w-0 truncate text-sm font-medium text-muted">{label}</Card.Title>
        <IconBadge icon={icon} tone={tone} />
      </Card.Header>
      {/* justify-end：即使上方标签换行，四张卡的数值也保持同一条基线 */}
      <Card.Content className="justify-end gap-2.5">
        <div className="flex min-h-[32px] items-baseline gap-1.5">
          {isLongText ? (
            <span className="text-sm font-medium text-muted/90 tracking-normal leading-snug">
              {value}
            </span>
          ) : (
            <span className="text-[26px] leading-none font-semibold tracking-tight tabular-nums">
              {value}
            </span>
          )}
          {unit && <span className="text-sm text-muted">{unit}</span>}
        </div>
        {(delta || deltaNote) && (
          <div className="flex min-w-0 items-center gap-2">
            {delta && (
              <Chip size="sm" variant="soft" color={deltaTone}>
                <Chip.Label>{delta}</Chip.Label>
              </Chip>
            )}
            {deltaNote && <span className="min-w-0 truncate text-xs text-muted">{deltaNote}</span>}
          </div>
        )}
      </Card.Content>
    </Card>
  )
}

/* ------------------------------------------------------------------ 键值列表 */

export function DataList({ children, className }: { children: ReactNode; className?: string }) {
  return <dl className={cn('divide-y divide-separator', className)}>{children}</dl>
}

export function DataRow({
  label,
  hint,
  value,
}: {
  label: string
  hint?: string
  value: ReactNode
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5 first:pt-0 last:pb-0">
      <dt className="min-w-0 text-sm text-muted">
        <span className="truncate">{label}</span>
        {hint && <span className="ms-1.5 text-xs opacity-70">{hint}</span>}
      </dt>
      <dd className="shrink-0 text-sm font-medium tabular-nums">{value}</dd>
    </div>
  )
}

/* ------------------------------------------------------------------ 列表行 */

/**
 * 统一的「图标 · 标题/副标题 · 元信息 · 操作」行。
 * 四个栏位的宽度与间距在所有面板里保持一致，是整页对齐感的来源。
 */
export function ListRow({
  icon,
  tone = 'default',
  title,
  description,
  meta,
  trailing,
}: {
  icon?: LucideIcon
  tone?: Tone
  title: ReactNode
  description?: ReactNode
  meta?: ReactNode
  trailing?: ReactNode
}) {
  return (
    <div className="flex items-center gap-3.5 py-3 first:pt-0 last:pb-0">
      {icon && <IconBadge icon={icon} tone={tone} size="sm" />}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-foreground">{title}</div>
        {description && (
          <div className="mt-0.5 truncate text-xs leading-5 text-muted">{description}</div>
        )}
      </div>
      {meta && (
        <div className="hidden shrink-0 text-xs tabular-nums text-muted md:block">{meta}</div>
      )}
      {trailing && <div className="flex shrink-0 items-center gap-2">{trailing}</div>}
    </div>
  )
}

/* ------------------------------------------------------------------ 表单栅格 */

const gridCols = {
  1: '',
  2: 'sm:grid-cols-2',
  3: 'sm:grid-cols-2 xl:grid-cols-3',
} as const

export function FieldGrid({
  cols = 2,
  children,
  className,
}: {
  cols?: 1 | 2 | 3
  children: ReactNode
  className?: string
}) {
  return <div className={cn('grid gap-x-5 gap-y-4', gridCols[cols], className)}>{children}</div>
}

/* ------------------------------------------------------------------ 开关 */

/** 裸开关：标签由所在行提供，避免行内出现两处文字说明 */
export function PlainSwitch({
  isSelected,
  onChange,
  label,
  isDisabled,
}: {
  isSelected: boolean
  onChange: (value: boolean) => void
  label: string
  isDisabled?: boolean
}) {
  return (
    <Switch aria-label={label} isSelected={isSelected} isDisabled={isDisabled} onChange={onChange}>
      <Switch.Content>
        <Switch.Control>
          <Switch.Thumb />
        </Switch.Control>
      </Switch.Content>
    </Switch>
  )
}

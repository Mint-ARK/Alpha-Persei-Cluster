import React, { useState } from 'react'
import { Plus, Check } from 'lucide-react'
import { resolveAssetUrl } from '../lib/api'

export interface ItemSlotProps {
  id: number
  name: string
  rare: number // 1 ~ 5
  iconFile: string
  qualityFrame: string
  count?: number
  size?: 'sm' | 'md' | 'lg' | 'xl'
  showCount?: boolean
  showAddButton?: boolean
  onAdd?: () => void
  isAdded?: boolean
  className?: string
  onClick?: () => void
}

const SIZE_MAP = {
  sm: {
    container: 'size-12 rounded-lg',
    iconSize: 'size-8',
    countText: 'text-[10px] px-1 py-0.5',
    plusBtn: 'size-4 p-0.5',
  },
  md: {
    container: 'size-16 rounded-xl',
    iconSize: 'size-11',
    countText: 'text-[11px] px-1.5 py-0.5',
    plusBtn: 'size-5 p-1',
  },
  lg: {
    container: 'size-20 rounded-xl',
    iconSize: 'size-14',
    countText: 'text-xs px-2 py-0.5',
    plusBtn: 'size-6 p-1',
  },
  xl: {
    container: 'size-24 rounded-2xl',
    iconSize: 'size-18',
    countText: 'text-xs px-2 py-1',
    plusBtn: 'size-6 p-1',
  },
}

// 品质柔和光晕映射
const GLOW_MAP: Record<number, string> = {
  5: 'hover:shadow-[0_0_12px_rgba(234,179,8,0.35)]', // 5星金色
  4: 'hover:shadow-[0_0_12px_rgba(168,85,247,0.35)]', // 4星紫色
  3: 'hover:shadow-[0_0_12px_rgba(59,130,246,0.30)]', // 3星蓝色
  2: 'hover:shadow-[0_0_12px_rgba(34,197,94,0.25)]',  // 2星绿色
  1: 'hover:shadow-[0_0_8px_rgba(148,163,184,0.20)]', // 1星白色
}

export function ItemSlot({
  id,
  name,
  rare = 5,
  iconFile,
  qualityFrame = 'Item_yellow.png',
  count,
  size = 'md',
  showCount = true,
  showAddButton = false,
  onAdd,
  isAdded = false,
  className = '',
  onClick,
}: ItemSlotProps) {
  const [failStage, setFailStage] = useState(0)
  const config = SIZE_MAP[size] || SIZE_MAP.md
  const glow = GLOW_MAP[rare] || GLOW_MAP[5]
  const frameUrl = resolveAssetUrl(`extracted_assets/items/grades/${qualityFrame || 'Item_box.png'}`)

  React.useEffect(() => {
    setFailStage(0)
  }, [id, iconFile])

  // 多级智能素材回退
  let iconUrl = ''
  if (failStage === 0) {
    let normalizedIcon = iconFile || `${id}.png`
    let iconRelPath = `extracted_assets/items/${normalizedIcon}`
    if (normalizedIcon.startsWith('extracted_assets/')) {
      iconRelPath = normalizedIcon
    } else if (
      normalizedIcon.startsWith('avatars/') ||
      normalizedIcon.startsWith('servants/') ||
      normalizedIcon.startsWith('items/')
    ) {
      iconRelPath = `extracted_assets/${normalizedIcon}`
    }
    iconUrl = resolveAssetUrl(iconRelPath)
  } else if (failStage === 1) {
    // 阶段 1：尝试以自身道具 ID 为贴图 (特别针对 11068 等角色情报碎片)
    iconUrl = resolveAssetUrl(`extracted_assets/items/${id}.png`)
  } else if (failStage === 2) {
    // 阶段 2：尝试以对应角色头像为贴图
    const heroId = id >= 10000 && id < 20000 ? id % 10000 : id
    iconUrl = resolveAssetUrl(`extracted_assets/avatars/${heroId}.png`)
  } else {
    // 阶段 3：保底灰底占位
    iconUrl = resolveAssetUrl('extracted_assets/items/grades/Item_box.png')
  }

  const handleImgError = () => {
    if (failStage < 3) {
      setFailStage((prev) => prev + 1)
    }
  }

  const handleAddClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (onAdd) {
      onAdd()
    }
  }

  return (
    <div
      onClick={onClick}
      title={`${name} (ID: ${id})`}
      className={`group relative select-none shrink-0 overflow-hidden border border-separator/40 transition-all duration-200 cursor-pointer ${config.container} ${glow} ${className}`}
    >
      {/* 1. 底层：官方正版品质框 */}
      <img
        src={frameUrl}
        alt={`Frame-${rare}`}
        className="absolute inset-0 size-full object-fill pointer-events-none"
        loading="lazy"
      />

      {/* 2. 中层：物品主体图标 */}
      <div className="absolute inset-0 flex items-center justify-center p-1">
        <img
          src={iconUrl}
          alt={name}
          onError={handleImgError}
          className={`${config.iconSize} object-contain drop-shadow-md transition-transform duration-200 group-hover:scale-105 pointer-events-none`}
          loading="lazy"
        />
      </div>

      {/* 3. 顶层右下角：持有数量角标 */}
      {showCount && count !== undefined && (
        <div
          className={`absolute bottom-0 right-0 rounded-tl-md bg-black/75 font-mono font-semibold tabular-nums text-white shadow-sm pointer-events-none backdrop-blur-[2px] leading-none ${config.countText}`}
        >
          {count > 999999 ? `${(count / 10000).toFixed(1)}w` : count.toLocaleString('en-US')}
        </div>
      )}

      {/* 4. 顶层右上角：添加到邮件附件草稿池的快捷按钮 */}
      {showAddButton && (
        <button
          type="button"
          aria-label={`添加 ${name} 到邮件附件`}
          onClick={handleAddClick}
          className={`absolute top-1 right-1 z-10 flex items-center justify-center rounded-full transition-all duration-200 ${
            isAdded
              ? 'bg-emerald-500 text-white scale-100 opacity-100 ring-1.5 ring-white/90 dark:ring-black/60 shadow-md'
              : 'bg-black/45 text-white/90 hover:bg-accent hover:text-accent-foreground opacity-0 group-hover:opacity-100 hover:scale-110 shadow-sm backdrop-blur-sm'
          } ${config.plusBtn}`}
        >
          {isAdded ? (
            <Check className="size-full stroke-[3]" />
          ) : (
            <Plus className="size-full stroke-[2.5]" />
          )}
        </button>
      )}
    </div>
  )
}

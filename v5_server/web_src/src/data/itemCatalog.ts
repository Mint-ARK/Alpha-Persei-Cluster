import items from './items_catalog.json'

export interface CatalogItem {
  id: number
  name: string
  type: number
  rare: number
  icon_file: string
  quality_frame: string
  aliases?: string[]
}

export const itemCatalog: CatalogItem[] = items
export const itemById = new Map(itemCatalog.map(item => [item.id, item]))

export const quickMailItems = [
  [1, 1000], [38, 10], [5, 10], [2, 100000],
  [41710, 1], [20003, 5], [40103, 20], [40203, 10],
].flatMap(([id, count]) => {
  const item = itemById.get(id)
  return item ? [{ ...item, name: itemName(item), count }] : []
})

export function itemName(item: CatalogItem): string {
  return item.name || `道具 #${item.id}`
}

export function refreshDraftAttachment<T extends { id: number; name: string; rare: number; icon_file: string; quality_frame: string; selected?: boolean }>(attachment: T): T {
  const id = Number(attachment.id)
  const item = itemById.get(id)
  const name = item ? itemName(item) : `道具 #${id}`
  return { ...attachment, id, name, rare: item?.rare ?? 0, icon_file: item?.icon_file ?? '',
    quality_frame: item?.quality_frame ?? 'Item_box.webp', selected: attachment.selected !== false && attachment.name === name }
}

export function itemSearchScore(item: CatalogItem, query: string): number {
  const id = String(item.id)
  if (id === query) return 100
  const names = [item.name, ...(item.aliases ?? [])].map(name => name.toLowerCase())
  if (names.includes(query)) return 90
  if (names.some(name => name.startsWith(query))) return 70
  if (names.some(name => name.includes(query))) return 50
  return id.includes(query) ? 30 : 0
}

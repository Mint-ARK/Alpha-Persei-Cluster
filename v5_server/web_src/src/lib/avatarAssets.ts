import catalog from '../data/avatarsCatalog.json'

export interface AvatarItem { id: number; name: string; resource: string }
export const PLAYER_AVATARS: AvatarItem[] = catalog.portraits
const byId = new Map(PLAYER_AVATARS.map(item => [item.id, item]))

export function avatarAssetPath(id: number): string {
  const item = byId.get(Number(id))
  // Legacy character-ID portraits remain renderable; only canonical IDs are selectable.
  return `extracted_assets/avatars/${item?.resource || `${id}.png`}`
}

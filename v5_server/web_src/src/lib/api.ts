/**
 * GM 控制台 API 统一通信客户端
 * 遵循 CQRS 读写分离架构：
 * - 读请求 (GET) 直连 GMReader 无锁只读通道
 * - 写请求 (POST) 经由领域 Service 事务安全落库与 TCP 原子帧广播
 */

export enum GMErrorCode {
  SUCCESS = 0,
  PARAM_INVALID = 10001,
  PARAM_OUT_OF_RANGE = 10002,
  USER_NOT_FOUND = 10003,
  DB_NOT_READY = 20001,
  DB_EXEC_FAILED = 20002,
  SERVICE_ERROR = 30001,
  NOT_FOUND = 40004,
}

export interface ApiResponse<T = any> {
  code: number
  msg: string
  data: T
}

export interface ModuleMountStatus {
  id: string
  name: string
  code: string
  status: string
  desc: string
  tone: 'success' | 'warning' | 'danger' | 'accent' | 'default'
}

export interface LinkStateInfo {
  state: 'ready' | 'connected' | 'error'
  port?: number
  https_port?: number
  udp_port?: number
  desc?: string
}

export interface UptimeInfo {
  local_seconds: number
  local_formatted: string
  cumulative_seconds: number
  cumulative_formatted: string
}

export interface MonthSignInData {
  year: number
  month: number
  today: number
  signed_days: number[]
  total_signed: number
  is_signed_today: boolean
  uid: number
}

export interface SystemMetricsData {
  timestamp: number
  memory_mb: number
  peak_memory_mb?: number
  net_kbps: number
}

export interface OverviewStatusData {
  server_time?: number
  sdk_link?: LinkStateInfo
  game_link?: LinkStateInfo
  uptime?: UptimeInfo
  platforms?: string[]
  platform_info?: string
  modules?: ModuleMountStatus[]
  sign_in?: MonthSignInData
  metrics?: {
    memory_mb: number
    net_kbps: number
  }
  uptime_seconds?: number
  uptime_formatted?: string
  game_port?: number
  gw_port?: number
  host_ip?: string
  default_uid?: number
  online_users?: number
  total_users?: number
  capture_enabled?: boolean
  version?: string
  db_name?: string
  server_status?: string
  core_status?: string
  network?: {
    host_ip: string
    game_port: number
    gw_port: number
    tcp_clients_count: number
  }
  stats?: {
    total_users: number
    online_users: number
    total_items: number
    total_heroes: number
    capture_enabled: boolean
  }
}

export interface ProfileTag {
  id: number
  name: string
}

export interface BoardHeroInfo {
  id: number
  name: string
  avatar?: string
}

export interface FatigueDetail {
  current: number
  max: number
  is_full: boolean
  seconds_to_next: number
  seconds_to_full: number
  next_point_str: string
  full_recovery_str: string
}

export interface StickerItem {
  id: number
  name: string
  desc?: string
  rare?: number
  location_x: number
  location_y: number
  scale: number
  layer: number
  rotate: number
}

export interface StickerWall {
  page_id: number
  page_name: string
  page_desc?: string
  foreground?: number
  is_active?: boolean
  total_stickers: number
  stickers: StickerItem[]
}

export interface AccountProfileData {
  uid: number
  nickname?: string
  nick?: string
  level: number
  exp: number
  fatigue: number
  max_fatigue?: number
  fatigue_max?: number
  sign: string
  avatar_id?: number
  portrait?: number
  icon_frame_id?: number
  icon_frame?: number
  card_bg_id?: number
  status?: string
  is_online?: boolean
  heroes_count?: number
  items_count?: number
  profile_tags?: ProfileTag[]
  board_hero?: BoardHeroInfo
  skin_count?: number
  fatigue_detail?: FatigueDetail
  client_device?: string
  sticker_wall?: StickerWall
  sticker_walls?: StickerWall[]
}

export interface HeroStatsData {
  owned_count: number
  total_official: number
  oath_count: number
  favorite_count: number
  omega_count: number
}

export interface HeroEntry {
  id: number
  name: string
  title: string
  official_name: string
  custom_name?: string
  display_name: string
  pinyin_initial: string
  full_pinyin_initials?: string
  race_id: number
  race_name: string
  race_icon: string
  element_id: number
  element_name: string
  element_icon: string
  star: number
  grade_name: string
  level: number
  unlocked: boolean
  is_favorite: boolean
  is_oath: boolean
  oath_level: number
  avatar: string
}

export interface HeroBaseDetail {
  level: number
  star: number
  grade_name: string
  phase: number
  phase_text: string
  weapon_level: number
  weapon_break: number
  module_level: number
  module_supported: boolean
  unlocked: boolean
}

export interface HeroServantDetail {
  id: number
  name: string
  portrait: string
  icon?: string
  stage: number
  equipped?: boolean
  is_universal?: boolean
  star?: number
  type?: number
  race_id?: number
  race_name?: string
  desc?: string
  effect_desc?: string
  exclusive_servant?: {
    id: number
    name: string
    portrait: string
    stage?: number
    owned?: boolean
    desc?: string
    race_id?: number
    race_name?: string
  }
}

export interface HeroSkillItem {
  id: number
  name: string
  type: string
  icon: string
  level: number
  add_level?: number
  total_level?: number
  intensify: number
  is_dodge?: boolean
}

export interface HeroEquipEnchant {
  id: number
  name: string
  icon: string
  level: number
}

export interface HeroEquipSlot {
  pos: number
  equipped: boolean
  name: string
  suit_name: string
  level: number
  star: number
  icon: string
  enchants: HeroEquipEnchant[]
}

export interface HeroEquipsData {
  slots: HeroEquipSlot[]
  active_suits: string[]
  is_omega?: boolean
  suit_need?: number
}

export interface HeroTransitionSkill {
  id: number
  name: string
  icon: string
  level: number
}

export interface HeroTransitionSlot {
  slot_id: number
  slot_name: string
  total_level: number
  icon: string
  skills: HeroTransitionSkill[]
}

export interface HeroChipItem {
  slot_id: number
  slot_name?: string
  equipped?: boolean
  id: number
  name: string
  desc?: string
  icon?: string
  picture_id?: string
  role_type_id?: number
  role_type_name?: string
  cost?: number
}

export interface HeroAstrolabeNode {
  id: number
  name: string
}

export interface HeroAstrolabeData {
  active: HeroAstrolabeNode[]
  all_nodes: HeroAstrolabeNode[]
}

export interface HeroOathDetail {
  is_oath: boolean
  nick: string
  oath_level: number
  oath_time: number
  plot_progress: number
  plot_max?: number
  // 一阶好感 (Like / 基础档案好感)
  like_level?: number
  like_roman?: string
  like_title?: string
  like_exp?: number
  like_exp_max?: number
  like_total_exp?: number
  // 二阶专属交心 (Trust)
  has_trust?: boolean
  trust_level?: number
  trust_title?: string
  trust_exp?: number
  trust_exp_max?: number
  trust_mood?: number
  mood_name?: string
  mood_rate?: number
  mood_buff?: string
}

export interface HeroFullDetail {
  hero_id: number
  name: string
  portrait: string
  base: HeroBaseDetail
  servant: HeroServantDetail
  skills: HeroSkillItem[]
  equips: HeroEquipsData
  transitions: HeroTransitionSlot[]
  chips: HeroChipItem[]
  astrolabe: HeroAstrolabeData
  oath: HeroOathDetail
}

export interface InventoryItemEntry {
  id: number
  num: number
  name?: string
  type?: number
}

export interface MailEntry {
  id: number
  title: string
  content: string
  sender?: string
  send_time?: number
  items?: Array<{ id: number; num: number }>
}

export interface GachaPoolEntry {
  id: number
  name: string
  enabled: boolean
  pity_count?: number
}

// 本地存储 key
const STORAGE_KEY_API_BASE = 'gm_api_base'
const STORAGE_KEY_ACTIVE_UID = 'gm_active_uid'

export function getStoredApiBase(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem(STORAGE_KEY_API_BASE) || ''
}

export function setStoredApiBase(url: string): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(STORAGE_KEY_API_BASE, url.trim().replace(/\/+$/, ''))
}

export const DEFAULT_ACCOUNT_UID = 2174928301

export function getActiveUid(): number {
  if (typeof window === 'undefined') return DEFAULT_ACCOUNT_UID
  const stored = localStorage.getItem(STORAGE_KEY_ACTIVE_UID)
  if (!stored || stored === '10001') {
    localStorage.setItem(STORAGE_KEY_ACTIVE_UID, String(DEFAULT_ACCOUNT_UID))
    return DEFAULT_ACCOUNT_UID
  }
  return parseInt(stored, 10) || DEFAULT_ACCOUNT_UID
}

export function setActiveUid(uid: number): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(STORAGE_KEY_ACTIVE_UID, String(uid))
}

/**
 * 转换前端静态提取资源（头像、物品等）路径：
 * 保证在 file:/// 协议和普通 http 协议下均能正确以相对路径访问。
 */
export function resolveAssetUrl(assetPath: string): string {
  let clean = assetPath.replace(/^\/+/, '')
  if (clean.startsWith('extracted_assets/')) {
    clean = clean.replace(/\.(png|jpg|jpeg)$/i, '.webp')
  }
  return `./${clean}`
}

/**
 * 计算实际请求的基础 URL 前缀
 */
export function resolveApiUrl(path: string): string {
  const customBase = getStoredApiBase()
  const cleanPath = path.startsWith('/') ? path : `/${path}`

  if (customBase) {
    return `${customBase}${cleanPath}`
  }

  // 若通过 file:/// 协议离线打开单文件控制台，默认回退到标准 80 端口直传服务
  if (typeof window !== 'undefined' && window.location.protocol === 'file:') {
    return `http://127.0.0.1:80${cleanPath}`
  }

  // 网页或开发服务器同源相对路径（由 Vite proxy 转发或 Flask 静态托管）
  return cleanPath
}

/**
 * 统一网络请求包装器
 */
export async function apiRequest<T = any>(
  path: string,
  options: RequestInit = {}
): Promise<ApiResponse<T>> {
  const url = resolveApiUrl(path)
  const defaultHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  }

  try {
    const res = await fetch(url, {
      ...options,
      headers: {
        ...defaultHeaders,
        ...(options.headers as Record<string, string>),
      },
    })

    if (!res.ok) {
      throw new Error(`HTTP Error ${res.status}: ${res.statusText}`)
    }

    const data = await res.json()
    if (data && typeof data === 'object') {
      if (typeof data.code === 'number' && data.code !== 0) {
        const error: any = new Error(data.msg || `GM 操作失败 (错误码: ${data.code})`)
        error.code = data.code
        error.data = data.data
        throw error
      }
      if (data.errorCode && data.errorCode !== '0') {
        const error: any = new Error(data.errorMsg || `请求失败 (${data.errorCode})`)
        error.code = data.errorCode
        throw error
      }
      if (data.errorMsg && data.code === undefined) {
        const error: any = new Error(data.errorMsg || '服务异常')
        throw error
      }
    }
    return data as ApiResponse<T>
  } catch (err: any) {
    console.warn(`[GM API] 请求失败 ${url}:`, err.message)
    throw err
  }
}

// ===========================================================================
// 1. 状态总览 (/api/gm/overview)
// ===========================================================================

export async function fetchOverviewStatus(): Promise<ApiResponse<OverviewStatusData>> {
  return apiRequest<OverviewStatusData>('/api/gm/overview/status')
}

export async function fetchOverviewLogs(
  limit: number = 100,
  offset?: number
): Promise<ApiResponse<{ logs: string[]; total: number; server_start_time?: number }>> {
  const query = offset !== undefined ? `limit=${limit}&offset=${offset}` : `limit=${limit}`
  return apiRequest<{ logs: string[]; total: number; server_start_time?: number }>(`/api/gm/overview/logs?${query}`)
}

export async function fetchOverviewMetrics(): Promise<ApiResponse<SystemMetricsData>> {
  return apiRequest<SystemMetricsData>('/api/gm/overview/metrics')
}

export async function executeQuickAction(
  action: string,
  params: Record<string, any> = {}
): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/overview/quick_action', {
    method: 'POST',
    body: JSON.stringify({ action, ...params }),
  })
}

// ===========================================================================
// 2. 账号数据 (/api/gm/account)
// ===========================================================================

export async function fetchAccountProfile(uid: number = getActiveUid()): Promise<ApiResponse<AccountProfileData>> {
  return apiRequest<AccountProfileData>(`/api/gm/account/profile?uid=${uid}`)
}

export async function modifyAccountBasic(payload: {
  uid?: number
  nickname?: string
  nick?: string
  level?: number
  exp?: number
}): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/account/modify_basic', {
    method: 'POST',
    body: JSON.stringify({ uid: getActiveUid(), ...payload }),
  })
}

export async function modifyAccountFatigue(payload: {
  uid?: number
  fatigue: number
}): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/account/modify_fatigue', {
    method: 'POST',
    body: JSON.stringify({ uid: getActiveUid(), ...payload }),
  })
}

export async function modifyAccountPeripheral(payload: {
  uid?: number
  sign?: string
  avatar_id?: number
  icon_frame_id?: number
}): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/account/modify_peripheral', {
    method: 'POST',
    body: JSON.stringify({ uid: getActiveUid(), ...payload }),
  })
}

// ===========================================================================
// 3. 玩家角色 (/api/gm/heroes)
// ===========================================================================

export async function fetchHeroesList(uid: number = getActiveUid()): Promise<ApiResponse<{ heroes: HeroEntry[]; total: number; stats?: HeroStatsData }>> {
  return apiRequest(`/api/gm/heroes/list?uid=${uid}`)
}

export async function fetchHeroDetail(heroId: number, uid: number = getActiveUid()): Promise<ApiResponse<HeroFullDetail>> {
  return apiRequest(`/api/gm/heroes/detail?uid=${uid}&hero_id=${heroId}`)
}

export async function unlockHero(heroId: number, uid: number = getActiveUid()): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/heroes/unlock', {
    method: 'POST',
    body: JSON.stringify({ uid, hero_id: heroId }),
  })
}

export async function graduateHero(heroId: number, uid: number = getActiveUid()): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/heroes/graduate', {
    method: 'POST',
    body: JSON.stringify({ uid, hero_id: heroId }),
  })
}

// ===========================================================================
// 4. 资源背包 (/api/gm/inventory)
// ===========================================================================

export async function fetchInventoryItems(uid: number = getActiveUid()): Promise<ApiResponse<{ items: InventoryItemEntry[]; total: number }>> {
  return apiRequest(`/api/gm/inventory/items?uid=${uid}`)
}

export async function addInventoryItem(itemId: number, num: number, uid: number = getActiveUid()): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/inventory/add_item', {
    method: 'POST',
    body: JSON.stringify({ uid, item_id: itemId, num }),
  })
}

export async function addEquipSuit(suitId: number, count: number = 1, uid: number = getActiveUid()): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/inventory/add_suit', {
    method: 'POST',
    body: JSON.stringify({ uid, suit_id: suitId, count }),
  })
}

export async function clearInventory(category?: string, uid: number = getActiveUid()): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/inventory/clear', {
    method: 'POST',
    body: JSON.stringify({ uid, category }),
  })
}

// ===========================================================================
// 5. 邮件系统 (/api/gm/mail)
// ===========================================================================

export interface GMMailAttachment {
  id: number
  name?: string
  number: number
}

export interface GMMailRecord {
  id: number
  mail_id: number
  uid: number
  sender: string
  title: string
  content: string
  attachments: GMMailAttachment[]
  attachments_count: number
  sent_at: number
  sent_at_str: string
  status: string
}

export interface DBMailRecord {
  mail_id: number
  id: number
  title: string
  sender: string
  content: string
  attachments: GMMailAttachment[]
  rewards: GMMailAttachment[]
  send_time: number
  read_flag: number
  attach_flag: number
  is_read: boolean
  is_claimed: boolean
  state: number
}

export interface MailHistoryResponse {
  uid: number
  total: number
  history: GMMailRecord[]
  db_mails: DBMailRecord[]
}

export interface SendGMMailParams {
  uid?: number
  sender?: string
  title: string
  content: string
  attachments: Array<{ id: number; name?: string; number: number }>
}

export async function fetchMailHistory(uid: number = getActiveUid()): Promise<ApiResponse<MailHistoryResponse>> {
  return apiRequest(`/api/gm/mail/history?uid=${uid}`)
}

export async function sendGMMail(payload: SendGMMailParams): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/mail/send', {
    method: 'POST',
    body: JSON.stringify({ uid: getActiveUid(), ...payload }),
  })
}

export const sendMail = sendGMMail

// ===========================================================================
// 6. 卡池管理 (/api/gm/gacha)
// ===========================================================================

export interface GachaPoolEntry {
  id: number
  pool_id: number
  name: string
  pool_name: string
  hero_name: string
  type: string
  type_label: string
  enabled: boolean
  active: boolean
  pity_cap: number
  activity_id: number
  grade: 'A' | 'B' | 'C' | string
  hero_id?: number
  order?: number
  min_version?: string
  is_version_locked?: boolean
}

export interface PityGroupState {
  pool_group: string
  since_ssr: number
  since_sr: number
  total_draws: number
  up_id?: number
  is_up_guaranteed: number
  pity_cap: number
}

export interface DrawRecordItem {
  id: number
  pool_id: number
  pool_group: string
  item_id: number
  item_name: string
  item_num: number
  rare: 'SSR' | 'SR' | 'R' | string
  type?: 'hero' | 'servant' | string
  img: string
  draw_ts: number
  draw_time: string
}

export interface GachaOverviewData {
  pools: GachaPoolEntry[]
  total: number
  active_count: number
  active_pool_ids: number[]
  pity_states: {
    hero_precision_90: PityGroupState
    hero_precision_70: PityGroupState
    hero_standard_70: PityGroupState
    weapon_servant_70: PityGroupState
    [key: string]: PityGroupState
  }
  history_by_group?: {
    hero_precision_90?: DrawRecordItem[]
    hero_precision_70?: DrawRecordItem[]
    hero_standard_70?: DrawRecordItem[]
    weapon_servant_70?: DrawRecordItem[]
    [key: string]: DrawRecordItem[] | undefined
  }
  uid: number
}

export async function fetchGachaPools(uid: number = getActiveUid()): Promise<ApiResponse<GachaOverviewData>> {
  return apiRequest(`/api/gm/gacha/pools?uid=${uid}`)
}

export async function fetchGachaHistory(
  poolGroup?: string,
  limit: number = 100,
  uid: number = getActiveUid()
): Promise<ApiResponse<{ uid: number; pool_group: string; total: number; records: DrawRecordItem[] }>> {
  const q = new URLSearchParams({ uid: String(uid), limit: String(limit) })
  if (poolGroup) q.set('group', poolGroup)
  return apiRequest(`/api/gm/gacha/history?${q.toString()}`)
}

export interface GachaPresetItem {
  name: string
  desc: string
  pool_ids: number[]
}

export async function fetchGachaPresets(): Promise<ApiResponse<Record<string, GachaPresetItem>>> {
  return apiRequest('/api/gm/gacha/presets')
}

export async function applyGachaPreset(
  presetKey: string
): Promise<ApiResponse<{ preset: string; active_pools: number[] }>> {
  return apiRequest('/api/gm/gacha/apply_preset', {
    method: 'POST',
    body: JSON.stringify({ preset_key: presetKey }),
  })
}

export async function toggleGachaPool(poolId: number, enabled: boolean): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/gacha/toggle_pool', {
    method: 'POST',
    body: JSON.stringify({ pool_id: poolId, enabled }),
  })
}

export async function setGachaPity(
  seriesOrPoolId: string | number,
  pityCount: number,
  isUpGuaranteed?: boolean | number,
  uid: number = getActiveUid()
): Promise<ApiResponse<any>> {
  const payload: any = { uid, count: pityCount }
  if (typeof seriesOrPoolId === 'number') {
    payload.pool_id = seriesOrPoolId
  } else {
    payload.series = seriesOrPoolId
  }
  if (isUpGuaranteed !== undefined) {
    payload.is_up_guaranteed = isUpGuaranteed ? 1 : 0
  }
  return apiRequest('/api/gm/gacha/set_pity', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

// ===========================================================================
// 7. 关卡状态 (/api/gm/stages)
// ===========================================================================

export async function fetchStagesStatus(uid: number = getActiveUid()): Promise<ApiResponse<any>> {
  return apiRequest(`/api/gm/stages/status?uid=${uid}`)
}

export async function unlockChapter(chapterId: number, uid: number = getActiveUid()): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/stages/unlock_chapter', {
    method: 'POST',
    body: JSON.stringify({ uid, chapter_id: chapterId }),
  })
}

export async function completeAchievements(chapterId: number, uid: number = getActiveUid()): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/stages/complete_achievements', {
    method: 'POST',
    body: JSON.stringify({ uid, chapter_id: chapterId }),
  })
}

// ===========================================================================
// 8. AI 修正者聊天 (/api/gm/aichat)
// ===========================================================================

export interface CharacterItem {
  char_id: number
  id: number
  char_name: string
  name: string
  hero_id: number
  avatar: string
  avatar_icon?: number
  icon_frame?: number
  level: number
  sign: string
  ip_location: string
  location: string
  greeting_msg: string
  system_prompt: string
  is_active: boolean
  message_count: number
  last_message: string
  last_time: number
  birthday: string
  greeting_mode: 'all' | 'holiday' | 'birthday' | 'none'
}

export interface CalendarData {
  today: {
    solar_date: string
    lunar_date: string
    solar_term: string | null
    holidays: string[]
    hero_birthdays: Array<{ hero_id: number; hero_name: string }>
    is_player_birthday: boolean
    holiday_greeting_heroes: number[]
    date?: string
    lunar_str?: string
  }
  current_month: number
  month_heroes: Array<{
    record_id: number
    hero_name: string
    birthday: string
    day: number
    organization: string
    is_today: boolean
  }>
  total_month_birthdays: number
}

export interface AIChatConfigData {
  provider: string
  model: string
  base_url: string
  api_key_masked: string
  has_api_key: boolean
  fallback_model: string
  timeout_seconds: number
  max_tokens: number
  temperature: number
  max_history_turns?: number
  system_prefix?: string
}

export interface ChatHistoryMessage {
  id: number
  char_id: number
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  model?: string
  latency_ms?: number
  is_fallback?: boolean
}

export async function fetchAIChatCharacters(uid: number = getActiveUid()): Promise<ApiResponse<{ characters: CharacterItem[] }>> {
  return apiRequest(`/api/gm/aichat/characters?uid=${uid}`)
}

export async function fetchAIChatPersonas(): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/aichat/personas')
}

export async function fetchAIChatCalendar(uid: number = getActiveUid()): Promise<ApiResponse<CalendarData>> {
  return apiRequest(`/api/gm/aichat/calendar?uid=${uid}`)
}

export async function fetchAIChatConfig(): Promise<ApiResponse<AIChatConfigData>> {
  return apiRequest('/api/gm/aichat/config')
}

export async function saveAIChatConfig(payload: Partial<AIChatConfigData> & { api_key?: string }): Promise<ApiResponse<AIChatConfigData>> {
  return apiRequest('/api/gm/aichat/config', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function testAIChatConnection(provider?: string): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/aichat/test_connection', {
    method: 'POST',
    body: JSON.stringify({ provider }),
  })
}

export async function fetchAIChatHistory(charId?: number, limit: number = 50, uid: number = getActiveUid()): Promise<ApiResponse<{ messages: ChatHistoryMessage[]; total: number; source?: string }>> {
  const query = charId ? `uid=${uid}&char_id=${charId}&limit=${limit}` : `uid=${uid}&limit=${limit}`
  return apiRequest(`/api/gm/aichat/history?${query}`)
}

export async function sendAIChatMessage(charId: number, content: string, uid: number = getActiveUid()): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/aichat/chat', {
    method: 'POST',
    body: JSON.stringify({ uid, char_id: charId, content }),
  })
}

export async function clearAIChatHistory(charId?: number, uid: number = getActiveUid()): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/aichat/clear_history', {
    method: 'POST',
    body: JSON.stringify({ uid, char_id: charId }),
  })
}

export async function updateAIChatPersona(payload: {
  char_id: number
  char_name?: string
  sign?: string
  ip_location?: string
  greeting_msg?: string
  system_prompt?: string
}): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/aichat/update_persona', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateAIChatGreetingSettings(charId: number, mode: 'all' | 'holiday' | 'birthday' | 'none', uid: number = getActiveUid()): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/aichat/greeting_settings', {
    method: 'POST',
    body: JSON.stringify({ uid, char_id: charId, mode }),
  })
}

export async function triggerAIChatGreeting(uid: number = getActiveUid(), force: boolean = false): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/aichat/trigger_greeting', {
    method: 'POST',
    body: JSON.stringify({ uid, force }),
  })
}

// ===========================================================================
// 9. 服务运维设置 (/api/gm/server)
// ===========================================================================

export async function backupDatabase(): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/server/backup_db', {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export async function toggleCapture(enabled?: boolean): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/server/toggle_capture', {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  })
}

export async function hotReloadServer(): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/server/hot_reload', {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export interface RechargeStatusData {
  uid: number
  first_recharge_ids: number[]
  total_recharge_num: number
  total_recharge_yuan: number
  total_recharge_cents?: number
  claimed_total_bonus: number[]
  time_limit_recharge_num: number
  time_limit_recharge_yuan?: number
  battlepass: {
    pay_level: number
    exp_14: number
    bp_reward_status: number
  }
  noob_welfare: {
    fr_first_gear: number
    fr_second_gear: number
    fr_now_sign: number
    mc_flag: number
  }
  currency_flower: {
    c30_ios: number
    c31_not_ios: number
    c32_free: number
    c5_total: number
    c1_diamond: number
  }
}

export type RechargeResetScope =
  | 'all'
  | 'first_recharge'
  | 'battlepass'
  | 'noob_welfare'
  | 'total_recharge'

export async function fetchRechargeStatus(
  uid: number = getActiveUid()
): Promise<ApiResponse<RechargeStatusData>> {
  return apiRequest<RechargeStatusData>(`/api/gm/recharge/status?uid=${uid}`)
}

export async function resetRechargeScope(
  scope: RechargeResetScope = 'all',
  uid: number = getActiveUid()
): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/recharge/reset', {
    method: 'POST',
    body: JSON.stringify({ uid, scope }),
  })
}

/**
 * 校验是否为刻印类物品（刻印套装、单件刻印或已废弃套装盒）
 * 规则：
 * 1. type === 7（刻印套装）
 * 2. 200000 <= id <= 600000（单件刻印与虚拟套装区间）
 * 3. 830000 <= id <= 859999（刻印套装盒区间）
 */
export function isEquipOrSigil(id: number, type?: number): boolean {
  if (type === 7) return true
  if (id >= 200000 && id <= 600000) return true
  if (id >= 830000 && id <= 859999) return true
  return false
}

/**
 * 校验是否为试衣底片类道具（空白试衣底片、各种 3天/5天/7天 限时试衣底片）
 * 规则：
 * 1. id === 30054 (空白试衣底片)
 * 2. 1000000000 <= id <= 2000000000 (官方 72 款限时试衣底片区间)
 * 3. 道具名包含 '试衣底片' 或 '底片'
 */
export function isTrialSkinFilm(id: number, name?: string): boolean {
  if (id === 30054) return true
  if (id >= 1000000000 && id <= 2000000000) return true
  if (name && (name.includes('试衣底片') || name.includes('底片'))) return true
  return false
}

/**
 * 校验是否为仅供图鉴展示、禁止邮寄的受限物品（刻印类 + 试衣底片类）
 */
export function isDisplayOnlyItem(id: number, type?: number, name?: string): boolean {
  return isEquipOrSigil(id, type) || isTrialSkinFilm(id, name)
}

// ===========================================================================
// 10. 商店系统管理 (/api/gm/shop)
// ===========================================================================

export interface ShopMeta {
  shop_id: number
  name: string
  group_name: string
  system: number
  goods_count: number
  refresh_num_limit: number
  activity_id: number
  is_permanent: boolean
}

export interface ShopGroup {
  id: string
  name: string
  shop_ids: number[]
}

export interface ShopCatalogData {
  groups: ShopGroup[]
  shops: ShopMeta[]
  total_shops: number
  total_goods: number
}

export interface ShopGoodsItem {
  goods_id: number
  item_id: number
  name: string
  rare: number
  icon_file: string
  quality_frame: string
  cost_id: number
  cost_name: string
  cost: number
  cheap_cost: number
  discount: number
  limit_num: number
  buy_times: number
  left_num: number
  is_sold_out: boolean
  refresh_cycle: number
  refresh_cycle_label: string
  next_refresh_ts?: number
  user_balance?: number
  user_item_count?: number
}

export interface ShopGoodsData {
  uid: number
  shop_id: number
  shop_name: string
  group_name: string
  refresh_num_limit: number
  goods: ShopGoodsItem[]
  total_goods: number
}

export interface ShopStatsData {
  uid: number
  total_shops: number
  total_goods: number
  daily_refresh_times: number
  daily_refresh_max: number
  sold_out_goods_count: number
}

export const OFFLINE_SHOP_CATALOG_FALLBACK: ShopCatalogData = {
  groups: [
    { id: 'daily', name: '每日采购', shop_ids: [2] },
    { id: 'trading', name: '交易中心', shop_ids: [10, 11, 12, 13, 14] },
    { id: 'voucher', name: '凭证置换', shop_ids: [20, 22, 23, 41] },
    { id: 'supply', name: '组合补给', shop_ids: [3, 4, 5, 6] },
    { id: 'skins', name: '角色换装', shop_ids: [15, 16, 17, 18, 42] },
    { id: 'all', name: '全部商店', shop_ids: [2, 10, 11, 12, 13, 14, 20, 22, 23, 41, 3, 4, 5, 6, 15, 16, 17, 18, 42] },
  ],
  shops: [
    { shop_id: 2, name: '每日采购', group_name: '每日采购', system: 0, goods_count: 13, refresh_num_limit: 20, activity_id: 0, is_permanent: true },
    { shop_id: 10, name: '辉芒商店', group_name: '交易中心', system: 1, goods_count: 14, refresh_num_limit: 0, activity_id: 0, is_permanent: true },
    { shop_id: 11, name: '合作商店', group_name: '交易中心', system: 1, goods_count: 12, refresh_num_limit: 0, activity_id: 0, is_permanent: true },
    { shop_id: 13, name: '矩阵供应', group_name: '交易中心', system: 1, goods_count: 18, refresh_num_limit: 0, activity_id: 0, is_permanent: true },
    { shop_id: 14, name: '同调轨迹', group_name: '交易中心', system: 1, goods_count: 10, refresh_num_limit: 0, activity_id: 0, is_permanent: true },
    { shop_id: 20, name: '黑区净化', group_name: '凭证置换', system: 2, goods_count: 24, refresh_num_limit: 0, activity_id: 0, is_permanent: true },
    { shop_id: 22, name: '多维变量', group_name: '凭证置换', system: 2, goods_count: 20, refresh_num_limit: 0, activity_id: 0, is_permanent: true },
    { shop_id: 23, name: '梦境再构', group_name: '凭证置换', system: 2, goods_count: 16, refresh_num_limit: 0, activity_id: 0, is_permanent: true },
    { shop_id: 41, name: '迭代校验', group_name: '凭证置换', system: 2, goods_count: 15, refresh_num_limit: 0, activity_id: 0, is_permanent: true },
    { shop_id: 4, name: '日常补给', group_name: '组合补给', system: 3, goods_count: 8, refresh_num_limit: 0, activity_id: 0, is_permanent: true },
    { shop_id: 15, name: '换装商店', group_name: '角色换装', system: 4, goods_count: 42, refresh_num_limit: 0, activity_id: 0, is_permanent: true },
  ],
  total_shops: 115,
  total_goods: 3422,
}

export const OFFLINE_DAILY_GOODS_FALLBACK: ShopGoodsItem[] = [
  { goods_id: 2001001, item_id: 40102, name: '中级作战记录', rare: 3, icon_file: '40102.png', quality_frame: 'Item_blue.png', cost_id: 2, cost_name: '艾因索菲币', cost: 800, cheap_cost: 800, discount: 0, limit_num: 3, buy_times: 0, left_num: 3, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
  { goods_id: 2001002, item_id: 40202, name: '中级源质结晶', rare: 3, icon_file: '40202.png', quality_frame: 'Item_blue.png', cost_id: 2, cost_name: '艾因索菲币', cost: 800, cheap_cost: 800, discount: 0, limit_num: 3, buy_times: 0, left_num: 3, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
  { goods_id: 2001003, item_id: 40802, name: '三星启示录', rare: 3, icon_file: '40802.png', quality_frame: 'Item_blue.png', cost_id: 2, cost_name: '艾因索菲币', cost: 800, cheap_cost: 800, discount: 0, limit_num: 3, buy_times: 0, left_num: 3, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
  { goods_id: 2001004, item_id: 40301, name: '神力因子', rare: 4, icon_file: '40301.png', quality_frame: 'Item_purple.png', cost_id: 2, cost_name: '艾因索菲币', cost: 4000, cheap_cost: 4000, discount: 0, limit_num: 1, buy_times: 0, left_num: 1, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
  { goods_id: 2001005, item_id: 40103, name: '高级作战记录', rare: 4, icon_file: '40103.png', quality_frame: 'Item_purple.png', cost_id: 1, cost_name: '移转之辉', cost: 20, cheap_cost: 20, discount: 0, limit_num: 1, buy_times: 0, left_num: 1, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
  { goods_id: 2001006, item_id: 40203, name: '高级源质结晶', rare: 4, icon_file: '40203.png', quality_frame: 'Item_purple.png', cost_id: 1, cost_name: '移转之辉', cost: 20, cheap_cost: 20, discount: 0, limit_num: 1, buy_times: 0, left_num: 1, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
  { goods_id: 2001007, item_id: 40803, name: '四星启示录', rare: 4, icon_file: '40803.png', quality_frame: 'Item_purple.png', cost_id: 1, cost_name: '移转之辉', cost: 20, cheap_cost: 20, discount: 0, limit_num: 1, buy_times: 0, left_num: 1, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
  { goods_id: 2001008, item_id: 40602, name: '中级赋能模块', rare: 3, icon_file: '40602.png', quality_frame: 'Item_blue.png', cost_id: 1, cost_name: '移转之辉', cost: 50, cheap_cost: 50, discount: 0, limit_num: 1, buy_times: 0, left_num: 1, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
  { goods_id: 2001009, item_id: 40603, name: '高级赋能模块', rare: 4, icon_file: '40603.png', quality_frame: 'Item_purple.png', cost_id: 1, cost_name: '移转之辉', cost: 30, cheap_cost: 30, discount: 0, limit_num: 1, buy_times: 0, left_num: 1, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
  { goods_id: 2001010, item_id: 40603, name: '高级赋能模块', rare: 4, icon_file: '40603.png', quality_frame: 'Item_purple.png', cost_id: 1, cost_name: '移转之辉', cost: 150, cheap_cost: 150, discount: 0, limit_num: 1, buy_times: 0, left_num: 1, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
  { goods_id: 2001011, item_id: 40401, name: '原质重构仪', rare: 4, icon_file: '40401.png', quality_frame: 'Item_purple.png', cost_id: 1, cost_name: '移转之辉', cost: 50, cheap_cost: 50, discount: 0, limit_num: 1, buy_times: 0, left_num: 1, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
  { goods_id: 2001012, item_id: 40501, name: '精密显晰因子', rare: 5, icon_file: '40501.png', quality_frame: 'Item_yellow.png', cost_id: 1, cost_name: '移转之辉', cost: 100, cheap_cost: 100, discount: 0, limit_num: 1, buy_times: 0, left_num: 1, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
  { goods_id: 2001013, item_id: 40701, name: '极品强化源质', rare: 5, icon_file: '40701.png', quality_frame: 'Item_yellow.png', cost_id: 1, cost_name: '移转之辉', cost: 200, cheap_cost: 200, discount: 0, limit_num: 1, buy_times: 0, left_num: 1, is_sold_out: false, refresh_cycle: 1, refresh_cycle_label: '每日限购' },
]

export async function fetchShopCatalog(): Promise<ApiResponse<ShopCatalogData>> {
  try {
    const res = await apiRequest<ShopCatalogData>('/api/gm/shop/catalog')
    if (res.code === 0 && res.data && res.data.shops?.length > 0) {
      return res
    }
  } catch {
    // 离线回退
  }
  return {
    code: 0,
    msg: 'success (offline fallback)',
    data: OFFLINE_SHOP_CATALOG_FALLBACK,
  }
}

export async function fetchShopGoods(shopId: number = 2, uid?: number): Promise<ApiResponse<ShopGoodsData>> {
  const query = new URLSearchParams()
  query.set('shop_id', String(shopId))
  if (uid) query.set('uid', String(uid))

  try {
    const res = await apiRequest<ShopGoodsData>(`/api/gm/shop/goods?${query.toString()}`)
    if (res.code === 0 && res.data && res.data.goods) {
      return res
    }
  } catch {
    // 离线回退
  }

  return {
    code: 0,
    msg: 'success (offline fallback)',
    data: {
      uid: uid || 10001,
      shop_id: shopId,
      shop_name: shopId === 2 ? '每日采购' : `商店 #${shopId}`,
      group_name: shopId === 2 ? '每日采购' : '常规商店',
      refresh_num_limit: shopId === 2 ? 20 : 0,
      goods: shopId === 2 ? OFFLINE_DAILY_GOODS_FALLBACK : [],
      total_goods: shopId === 2 ? OFFLINE_DAILY_GOODS_FALLBACK.length : 0,
    },
  }
}

export async function fetchShopStats(uid?: number): Promise<ApiResponse<ShopStatsData>> {
  const query = uid ? `?uid=${uid}` : ''
  try {
    const res = await apiRequest<ShopStatsData>(`/api/gm/shop/stats${query}`)
    if (res.code === 0 && res.data) {
      return res
    }
  } catch {
    // 离线回退
  }
  return {
    code: 0,
    msg: 'success (offline fallback)',
    data: {
      uid: uid || 10001,
      total_shops: 116,
      total_goods: 7250,
      daily_refresh_times: 0,
      daily_refresh_max: 20,
      sold_out_goods_count: 0,
    },
  }
}

export async function resetShopRefresh(uid: number, shopId: number = 2): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/shop/reset_refresh', {
    method: 'POST',
    body: JSON.stringify({ uid, shop_id: shopId }),
  })
}

export async function resetShopPurchase(uid: number, shopId?: number | 'all'): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/shop/reset_purchase', {
    method: 'POST',
    body: JSON.stringify({ uid, shop_id: shopId === 'all' ? undefined : shopId }),
  })
}

export async function triggerShopCycle(uid: number, cycleType: 'daily' | 'weekly' | 'monthly' = 'daily'): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/shop/trigger_cycle', {
    method: 'POST',
    body: JSON.stringify({ uid, cycle_type: cycleType }),
  })
}

export async function buyShopGoods(
  uid: number,
  shopId: number,
  goodsId: number,
  buyNum: number = 1,
  freeCost: boolean = false
): Promise<ApiResponse<any>> {
  return apiRequest('/api/gm/shop/buy_goods', {
    method: 'POST',
    body: JSON.stringify({
      uid,
      shop_id: shopId,
      goods_id: goodsId,
      buy_num: buyNum,
      free_cost: freeCost,
    }),
  })
}

// ==========================================
// 大模型 Chat 与拟真写作能力排行榜接口
// ==========================================

export interface LeaderboardItem {
  rank: number
  model: string
  provider: string
  score: number
  sub_score: string
  metric_val: string
  details?: Record<string, any>
  tags?: string[]
}

export interface LeaderboardSource {
  id: string
  name: string
  icon: string
  url: string
  metric_name: string
  description: string
  columns: string[]
  items: LeaderboardItem[]
}

export interface LeaderboardsData {
  updated_at: string
  sources: LeaderboardSource[]
}

export async function fetchAIChatLeaderboards(): Promise<ApiResponse<LeaderboardsData>> {
  return apiRequest<LeaderboardsData>('/api/gm/aichat/leaderboards')
}

export async function refreshAIChatLeaderboards(): Promise<ApiResponse<{ success: boolean; updated_sources: number; errors: string[]; data: LeaderboardsData }>> {
  return apiRequest('/api/gm/aichat/leaderboards/refresh', {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

// ==========================================
// 12. 客户端资源版本管理 (/api/gm/system/res_version)
// ==========================================

export interface ResVersionDetail {
  name: string
  asset_hash: string
  voice_list_file: string
  asset_count: number
  desc: string
}

export interface ResVersionData {
  current_version: string
  version_name: string
  asset_hash: string
  voice_list_file: string
  asset_count: number
  supported_versions: string[]
  details: Record<string, ResVersionDetail>
}

export async function fetchResVersion(): Promise<ApiResponse<ResVersionData>> {
  return apiRequest<ResVersionData>('/api/gm/system/res_version')
}

export async function switchResVersion(version: string): Promise<ApiResponse<ResVersionData>> {
  return apiRequest<ResVersionData>('/api/gm/system/res_version', {
    method: 'POST',
    body: JSON.stringify({ version }),
  })
}

// ==========================================
// 13. iOS 229 开放 PAY 开关 (/api/gm/system/pay_switch)
// ==========================================

export interface PaySwitchData {
  ios_pay_enabled: boolean
  res_version: string
  client_platform: string
  is_eligible: boolean
}

export async function fetchPaySwitch(): Promise<ApiResponse<PaySwitchData>> {
  return apiRequest<PaySwitchData>('/api/gm/system/pay_switch')
}

export async function setPaySwitch(enabled: boolean): Promise<ApiResponse<PaySwitchData>> {
  return apiRequest<PaySwitchData>('/api/gm/system/pay_switch', {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  })
}

// ==========================================
// 16. 区服列表与自定义服名管理 (/api/gm/system/server_zones)
// ==========================================

export interface ServerZoneItem {
  serverId: string
  serverName: string
  env?: string
  newServerFlag?: number
  maintain?: boolean
  maintainReason?: string
}

export interface ServerZonesData {
  default_zone_id: string
  zones: ServerZoneItem[]
}

export async function fetchServerZones(): Promise<ApiResponse<ServerZonesData>> {
  return apiRequest<ServerZonesData>('/api/gm/system/server_zones')
}

export async function updateServerZones(
  data: ServerZonesData
): Promise<ApiResponse<ServerZonesData>> {
  return apiRequest<ServerZonesData>('/api/gm/system/server_zones', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

// ==========================================
// 17. 启动参数与运行调谐配置 (/api/gm/system/launch_config)
// ==========================================

export interface ReplayMaterialItem {
  name: string
  path: string
  has_https: boolean
  has_tcp: boolean
}

export interface LaunchConfigData {
  log_level: 'INFO' | 'DEBUG' | 'WARN' | 'ERROR'
  replay_enabled: boolean
  replay_path: string
  host_ip: string
  https_port: number
  gw_port: number
  game_port: number
  db_path: string
  client_assets_dir: string
  available_replays?: ReplayMaterialItem[]
  has_replay_material?: boolean
  detected_host_ip?: string
}

export async function fetchLaunchConfig(): Promise<ApiResponse<LaunchConfigData>> {
  return apiRequest<LaunchConfigData>('/api/gm/system/launch_config')
}

export async function saveLaunchConfig(
  data: Partial<LaunchConfigData>
): Promise<ApiResponse<LaunchConfigData>> {
  return apiRequest<LaunchConfigData>('/api/gm/system/launch_config', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function regenerateCert(): Promise<ApiResponse<{ status: string; ca_crt: string; ca_cer: string; mobileconfig: string }>> {
  return apiRequest<{ status: string; ca_crt: string; ca_cer: string; mobileconfig: string }>('/api/gm/system/regenerate_cert', {
    method: 'POST',
  })
}


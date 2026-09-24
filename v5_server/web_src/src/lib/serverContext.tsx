import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import {
  fetchOverviewStatus,
  fetchResVersion,
  switchResVersion,
  getActiveUid,
  setActiveUid as saveActiveUid,
  fetchServerZones,
  updateServerZones,
  type OverviewStatusData,
  type ResVersionData,
  type ServerZoneItem,
  type ServerZonesData,
} from './api'

interface ServerContextValue {
  status: OverviewStatusData | null
  loading: boolean
  isOnline: boolean
  lastChecked: Date | null
  activeUid: number
  setActiveUid: (uid: number) => void
  refreshStatus: () => Promise<void>
  resVersionInfo: ResVersionData | null
  resVersionLoading: boolean
  switchVersion: (version: string) => Promise<boolean>

  // 控制台与操作员全局状态
  consoleName: string
  setConsoleName: (name: string) => void
  operatorName: string
  setOperatorName: (name: string) => void

  // 区服与工作区全局状态
  serverZones: ServerZoneItem[]
  defaultZoneId: string
  activeZoneName: string
  setDefaultZoneId: (id: string) => void
  refreshServerZones: () => Promise<void>
  saveServerZones: (data: ServerZonesData) => Promise<boolean>
}

const ServerContext = createContext<ServerContextValue | undefined>(undefined)

const DEFAULT_ZONES: ServerZoneItem[] = [
  { serverId: '1', serverName: '艾因索菲', env: 'prod', newServerFlag: 1 },
  { serverId: '2', serverName: '蒂卡拉', env: 'prod', newServerFlag: 0 },
]

export function ServerProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<OverviewStatusData | null>(null)
  const [resVersionInfo, setResVersionInfo] = useState<ResVersionData | null>(null)
  const [resVersionLoading, setResVersionLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [isOnline, setIsOnline] = useState(() => {
    if (typeof window !== 'undefined') {
      return window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    }
    return false
  })
  const [lastChecked, setLastChecked] = useState<Date | null>(null)
  const [activeUid, setActiveUidState] = useState<number>(() => getActiveUid())

  // 控制台标识与操作员
  const [consoleName, setConsoleNameState] = useState<string>(() => {
    return localStorage.getItem('cp_console_name') || 'V5 控制面板'
  })
  const [operatorName, setOperatorNameState] = useState<string>(() => {
    return localStorage.getItem('cp_operator') || 'admin'
  })

  const setConsoleName = (name: string) => {
    localStorage.setItem('cp_console_name', name)
    setConsoleNameState(name)
  }

  const setOperatorName = (op: string) => {
    localStorage.setItem('cp_operator', op)
    setOperatorNameState(op)
  }

  // 区服列表与当前激活区服
  const [serverZones, setServerZones] = useState<ServerZoneItem[]>(DEFAULT_ZONES)
  const [defaultZoneId, setDefaultZoneIdState] = useState<string>(() => {
    return localStorage.getItem('cp_server_id') || '1'
  })

  const setDefaultZoneId = (id: string) => {
    localStorage.setItem('cp_server_id', id)
    setDefaultZoneIdState(id)
  }

  const currentZone = serverZones.find((z) => z.serverId === defaultZoneId) || serverZones[0]
  const activeZoneName = currentZone
    ? `${currentZone.serverName} (S${currentZone.serverId})`
    : '艾因索菲 (S1)'

  const setActiveUid = (uid: number) => {
    saveActiveUid(uid)
    setActiveUidState(uid)
  }

  const refreshServerZones = async () => {
    try {
      const res = await fetchServerZones()
      if (res && res.code === 0 && res.data) {
        if (res.data.zones && res.data.zones.length > 0) {
          setServerZones(res.data.zones)
        }
        if (res.data.default_zone_id && !localStorage.getItem('cp_server_id')) {
          setDefaultZoneIdState(res.data.default_zone_id)
        }
      }
    } catch (e) {
      // 离线时不阻断主流程
    }
  }

  const saveServerZones = async (data: ServerZonesData): Promise<boolean> => {
    try {
      const res = await updateServerZones(data)
      if (res && res.code === 0) {
        if (res.data?.zones) {
          setServerZones(res.data.zones)
        }
        if (res.data?.default_zone_id) {
          setDefaultZoneId(res.data.default_zone_id)
        }
        return true
      }
      return false
    } catch (e) {
      return false
    }
  }

  const refreshResVersion = async () => {
    try {
      const res = await fetchResVersion()
      if (res && res.code === 0 && res.data) {
        setResVersionInfo(res.data)
      }
    } catch (e) {
      // 离线时不打断主流程
    }
  }

  const switchVersion = async (targetVer: string): Promise<boolean> => {
    setResVersionLoading(true)
    try {
      const res = await switchResVersion(targetVer)
      if (res && res.code === 0 && res.data) {
        setResVersionInfo(res.data)
        return true
      }
      return false
    } catch (e) {
      return false
    } finally {
      setResVersionLoading(false)
    }
  }

  const refreshStatus = async () => {
    setLoading(true)
    try {
      const res = await fetchOverviewStatus()
      if (res && res.code === 0) {
        setStatus(res.data)
        setIsOnline(true)
        if (
          res.data.default_uid &&
          (!localStorage.getItem('gm_active_uid') || localStorage.getItem('gm_active_uid') === '10001')
        ) {
          setActiveUid(res.data.default_uid)
        }
      } else {
        setIsOnline(false)
      }
      await refreshResVersion()
      await refreshServerZones()
    } catch (e) {
      setIsOnline(false)
    } finally {
      setLoading(false)
      setLastChecked(new Date())
    }
  }

  useEffect(() => {
    refreshStatus()
    const timer = setInterval(refreshStatus, 15000)
    return () => clearInterval(timer)
  }, [])

  return (
    <ServerContext.Provider
      value={{
        status,
        loading,
        isOnline,
        lastChecked,
        activeUid,
        setActiveUid,
        refreshStatus,
        resVersionInfo,
        resVersionLoading,
        switchVersion,
        consoleName,
        setConsoleName,
        operatorName,
        setOperatorName,
        serverZones,
        defaultZoneId,
        activeZoneName,
        setDefaultZoneId,
        refreshServerZones,
        saveServerZones,
      }}
    >
      {children}
    </ServerContext.Provider>
  )
}

export function useServer() {
  const ctx = useContext(ServerContext)
  if (!ctx) {
    throw new Error('useServer must be used within ServerProvider')
  }
  return ctx
}

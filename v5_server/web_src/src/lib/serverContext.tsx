import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { resolveMountedUid } from './mountedSave'
import {
  checkAuthSession,
  loginAdmin as apiLoginAdmin,
  logoutAdmin as apiLogoutAdmin,
  type AuthSessionData,
  type AuthLoginResult,
  fetchOverviewStatus,
  fetchResVersion,
  switchResVersion,
  getActiveUid,
  setActiveUid as saveActiveUid,
  fetchSaveCatalog, getSelectedSave, setSelectedSave, type SaveEntry, type SaveCatalog, getStoredApiBase,
  type OverviewStatusData,
  type ResVersionData,
} from './api'

interface ServerContextValue {
  saves: SaveEntry[]
  selectedSave: string
  selectSave: (filename: string) => void
  refreshSaves: () => Promise<void>
  saveCatalogLoading: boolean
  saveCatalogError: string
  saveCatalogLoaded: boolean
  savePresets: SaveCatalog['presets']
  managedSave: SaveEntry | null
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

  // 管理密码会话认证
  authEnabled: boolean
  isAuthenticated: boolean
  authLoading: boolean
  authExpiresAt: number | null
  loginAdmin: (password: string) => Promise<AuthLoginResult>
  logoutAdmin: () => Promise<void>
  checkAuth: () => Promise<AuthSessionData>
}

const ServerContext = createContext<ServerContextValue | undefined>(undefined)

export function ServerProvider({ children }: { children: ReactNode }) {
  const [saves, setSaves] = useState<SaveEntry[]>([])
  const [savePresets, setSavePresets] = useState<SaveCatalog['presets']>([])
  const [saveCatalogLoading, setSaveCatalogLoading] = useState(true)
  const [saveCatalogError, setSaveCatalogError] = useState('')
  const [saveCatalogLoaded, setSaveCatalogLoaded] = useState(false)
  const catalogRequest = useRef(0)
  const [selectedSave, setSelectedSaveState] = useState(getSelectedSave)
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

  // 管理密码公网安全会话状态
  const [authEnabled, setAuthEnabled] = useState(false)
  const [isAuthenticated, setIsAuthenticated] = useState(true)
  const [authLoading, setAuthLoading] = useState(true)
  const [authExpiresAt, setAuthExpiresAt] = useState<number | null>(null)

  const checkAuth = async (): Promise<AuthSessionData> => {
    setAuthLoading(true)
    try {
      const session = await checkAuthSession()
      setAuthEnabled(session.enabled)
      setIsAuthenticated(session.enabled ? session.authenticated : true)
      setAuthExpiresAt(session.expires_at || null)
      return session
    } catch {
      setAuthEnabled(false)
      setIsAuthenticated(true)
      return { enabled: false, authenticated: true }
    } finally {
      setAuthLoading(false)
    }
  }

  const loginAdmin = async (password: string): Promise<AuthLoginResult> => {
    const res = await apiLoginAdmin(password)
    if (res.ok) {
      setIsAuthenticated(true)
      setAuthExpiresAt(res.expires_at || null)
      void refreshSaves().catch(() => undefined)
      void refreshStatus().catch(() => undefined)
    }
    return res
  }

  const logoutAdmin = async () => {
    await apiLogoutAdmin()
    setIsAuthenticated(false)
    setAuthExpiresAt(null)
  }

  useEffect(() => {
    const onUnauthorized = () => {
      if (authEnabled) {
        setIsAuthenticated(false)
      }
    }
    window.addEventListener('admin-auth-unauthorized', onUnauthorized)
    return () => window.removeEventListener('admin-auth-unauthorized', onUnauthorized)
  }, [authEnabled])

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

  const managedSave = saves.find(save => save.filename === selectedSave) || null

  const setActiveUid = (uid: number) => {
    saveActiveUid(uid)
    setActiveUidState(uid)
  }

  const selectSave = (filename: string) => {
    const save = saves.find(row => row.filename === filename)
    if (!save) return
    setSelectedSave(filename)
    setSelectedSaveState(filename)
    setActiveUid(save.uid)
    setStatus(null)
  }

  const refreshSaves = async () => {
    const id = ++catalogRequest.current
    const base = getStoredApiBase()
    setSaveCatalogLoading(true)
    setSaveCatalogError('')
    try {
      const res = await fetchSaveCatalog()
      if (id !== catalogRequest.current || base !== getStoredApiBase()) return
      setSaves(res.data.saves)
      setSavePresets(res.data.presets)
      setSaveCatalogLoaded(true)
      const selected = getSelectedSave()
      const current = res.data.saves.find(row => row.filename === selected)
      const fallback = current || res.data.saves.find(row => row.mounted) || res.data.saves[0]
      setSelectedSave(fallback?.filename || '')
      setSelectedSaveState(fallback?.filename || '')
      if (fallback) setActiveUid(fallback.uid)
    } catch (e: any) {
      if (id === catalogRequest.current && base === getStoredApiBase()) {
        setSaveCatalogError(e.message || '读取存档列表失败，请重试')
      }
      throw e
    } finally {
      if (id === catalogRequest.current && base === getStoredApiBase()) setSaveCatalogLoading(false)
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
    const requestedSave = getSelectedSave()
    setLoading(true)
    try {
      const res = await fetchOverviewStatus()
      if (requestedSave !== getSelectedSave()) return
      if (res && res.code === 0) {
        setStatus(res.data)
        setIsOnline(true)
        const mountedUid = resolveMountedUid(res.data, localStorage.getItem('gm_active_uid'))
        if (mountedUid !== null) setActiveUid(mountedUid)
      } else {
        setIsOnline(false)
      }
      await refreshResVersion()
    } catch (e) {
      setIsOnline(false)
    } finally {
      setLoading(false)
      setLastChecked(new Date())
    }
  }

  useEffect(() => {
    checkAuth().then((session) => {
      if (!session.enabled || session.authenticated) {
        void refreshSaves().catch(() => undefined)
        void refreshStatus()
      }
    })
    const timer = setInterval(() => {
      if (!authEnabled || isAuthenticated) {
        void refreshStatus()
      }
    }, 15000)
    return () => clearInterval(timer)
  }, [authEnabled, isAuthenticated])

  useEffect(() => { if (selectedSave) void refreshStatus() }, [selectedSave])

  useEffect(() => {
    const reconnect = () => {
      setSaves([])
      setSavePresets([])
      setSaveCatalogLoaded(false)
      setStatus(null)
      void refreshSaves().catch(() => undefined)
      void refreshStatus()
    }
    window.addEventListener('gm-api-base-changed', reconnect)
    return () => window.removeEventListener('gm-api-base-changed', reconnect)
  }, [])

  return (
    <ServerContext.Provider
      value={{
        saves, selectedSave, selectSave, refreshSaves,
        saveCatalogLoading, saveCatalogError, saveCatalogLoaded, savePresets,
        managedSave,
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
        authEnabled,
        isAuthenticated,
        authLoading,
        authExpiresAt,
        loginAdmin,
        logoutAdmin,
        checkAuth,
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

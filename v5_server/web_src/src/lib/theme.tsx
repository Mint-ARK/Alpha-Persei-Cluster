import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

export type ThemeMode = 'light' | 'dark' | 'system'

type ThemeContextValue = {
  mode: ThemeMode
  setMode: (mode: ThemeMode) => void
  resolved: 'light' | 'dark'
}

const ThemeContext = createContext<ThemeContextValue>({
  mode: 'light',
  setMode: () => {},
  resolved: 'light',
})

const QUERY = '(prefers-color-scheme: dark)'

/**
 * HeroUI v3 的深色主题由根节点的 data-theme 驱动（也支持 .dark 类）。
 * 这里同时维护 light / dark / system 三态，system 跟随系统偏好。
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  // 支持 ?theme=dark 或 #hash?theme=dark 直接以深色打开，方便截图与对照检查
  const [mode, setMode] = useState<ThemeMode>(() => {
    const searchVal = typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('theme') : null
    const hashVal = typeof window !== 'undefined' && window.location.hash.includes('theme=dark') ? 'dark' : null
    const value = searchVal || hashVal
    return value === 'dark' || value === 'light' || value === 'system' ? value : 'light'
  })
  const [systemDark, setSystemDark] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(QUERY).matches,
  )

  useEffect(() => {
    const media = window.matchMedia(QUERY)
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  const resolved: 'light' | 'dark' = mode === 'system' ? (systemDark ? 'dark' : 'light') : mode

  useEffect(() => {
    document.documentElement.dataset.theme = resolved
  }, [resolved])

  const value = useMemo(() => ({ mode, setMode, resolved }), [mode, resolved])

  return <ThemeContext value={value}>{children}</ThemeContext>
}

export function useTheme() {
  return useContext(ThemeContext)
}

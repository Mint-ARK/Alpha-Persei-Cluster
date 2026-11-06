import { useState, useEffect, useRef } from 'react'
import { ShieldCheck, Lock, Key, Eye, EyeOff, AlertCircle, Clock, Moon, Sun } from 'lucide-react'
import { Button, Card, Chip, Input, Label, TextField, cn } from '@heroui/react'
import { IconBadge } from './kit'
import { useServer } from '../lib/serverContext'
import { useTheme } from '../lib/theme'

export default function AdminLockScreen() {
  const { loginAdmin } = useServer()
  const { resolved, setMode } = useTheme()
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [errorMsg, setErrorMsg] = useState('')
  const [cooldownSeconds, setCooldownSeconds] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // 429 频率限制倒计时逻辑
  useEffect(() => {
    if (cooldownSeconds <= 0) return
    const timer = setInterval(() => {
      setCooldownSeconds((prev) => {
        if (prev <= 1) {
          setErrorMsg('')
          return 0
        }
        return prev - 1
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [cooldownSeconds])

  const handleUnlock = async (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    if (!password || loading || cooldownSeconds > 0) return

    setLoading(true)
    setErrorMsg('')

    try {
      const res = await loginAdmin(password)
      if (!res.ok) {
        if (res.retryAfter) {
          setCooldownSeconds(res.retryAfter)
          setErrorMsg(`验证失败过于频繁，已触发防暴力破解保护。请等待 ${res.retryAfter} 秒后再试。`)
        } else if (res.error === 'invalid_password') {
          setErrorMsg('管理员密码错误，请核对后重试。')
        } else if (res.error === 'origin_rejected' || res.error === 'admin_https_origin_required') {
          setErrorMsg('请求来源 (Origin/HTTPS) 与服务器配置不匹配，请通过合法的 HTTPS 域名访问。')
        } else if (res.error === 'auth_configuration_invalid') {
          setErrorMsg('服务端认证凭据配置异常或损坏，请联系运维人员检查。')
        } else {
          setErrorMsg(res.error || '登录验证失败，请稍后重试。')
        }
      }
    } catch (err: any) {
      setErrorMsg(err.message || '网络连接失败，请检查服务是否正常启动。')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative flex min-h-screen w-full flex-col items-center justify-center bg-background px-4 py-8 text-foreground select-none">
      {/* 右上角主题切换小控件 */}
      <div className="absolute top-5 right-5">
        <Button
          aria-label={resolved === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
          isIconOnly
          size="sm"
          variant="ghost"
          onPress={() => setMode(resolved === 'dark' ? 'light' : 'dark')}
        >
          {resolved === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </Button>
      </div>

      {/* WinUI 3 极简磁贴 / 亚克力锁屏面板 */}
      <div className="relative w-full max-w-[420px]">
        {/* 顶部微渐变流光强调线 */}
        <div className="absolute -top-px inset-x-8 h-1 rounded-t-full bg-gradient-to-r from-transparent via-accent to-transparent opacity-80" />

        <Card className="rounded-3xl border border-separator/80 bg-surface/90 p-8 shadow-2xl backdrop-blur-xl">
          {/* 标头徽标与标题 */}
          <div className="flex flex-col items-center text-center space-y-3">
            <div className="relative">
              <IconBadge icon={ShieldCheck} tone="accent" size="md" className="size-14 rounded-2xl shadow-sm" />
              <div className="absolute -bottom-1 -right-1 grid size-5 place-items-center rounded-full bg-surface border border-separator text-accent shadow-xs">
                <Lock className="size-3" strokeWidth={2.5} />
              </div>
            </div>

            <div className="space-y-1">
              <h1 className="text-xl font-semibold tracking-tight text-foreground">
                深空之眼 · GM 控制台
              </h1>
              <p className="text-xs text-muted">
                公网安全凭据验证 · WinUI 3 极简锁屏
              </p>
            </div>

            <Chip size="sm" variant="soft" color="accent" className="font-mono text-[11px] px-2">
              SESSION SHIELD ACTIVE
            </Chip>
          </div>

          {/* 表单输入区 */}
          <form onSubmit={handleUnlock} className="mt-6 space-y-4">
            <div className="space-y-1.5">
              <TextField
                className="w-full"
                isDisabled={loading || cooldownSeconds > 0}
                value={password}
                onChange={setPassword}
              >
                <div className="flex items-center justify-between">
                  <Label className="text-xs font-medium text-foreground/80">控制台管理密码</Label>
                  <span className="text-[11px] text-muted">至少 12 位</span>
                </div>
                <div className="relative flex items-center">
                  <Input
                    ref={inputRef}
                    type={showPassword ? 'text' : 'password'}
                    placeholder="请输入管理员密码..."
                    className="pr-10 font-mono text-sm"
                    autoComplete="current-password"
                  />
                  <Button
                    type="button"
                    isIconOnly
                    size="sm"
                    variant="ghost"
                    className="absolute right-1 size-7 text-muted hover:text-foreground"
                    onPress={() => setShowPassword(!showPassword)}
                  >
                    {showPassword ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                  </Button>
                </div>
              </TextField>
            </div>

            {/* 错误提示栏 */}
            {errorMsg && (
              <div
                role="alert"
                className={cn(
                  'flex items-start gap-2.5 rounded-2xl p-3 text-xs leading-5 animate-in fade-in slide-in-from-top-1',
                  cooldownSeconds > 0
                    ? 'bg-warning-soft text-warning-soft-foreground border border-warning/20'
                    : 'bg-danger-soft text-danger-soft-foreground border border-danger/20'
                )}
              >
                {cooldownSeconds > 0 ? (
                  <Clock className="size-4 shrink-0 mt-0.5" />
                ) : (
                  <AlertCircle className="size-4 shrink-0 mt-0.5" />
                )}
                <div className="flex-1 font-medium">{errorMsg}</div>
              </div>
            )}

            {/* 解锁提交按钮 */}
            <Button
              type="submit"
              variant="primary"
              className="w-full h-10 rounded-2xl font-medium tracking-wide shadow-sm"
              isDisabled={!password.trim() || loading || cooldownSeconds > 0}
            >
              {loading ? (
                '正在验证安全凭据…'
              ) : cooldownSeconds > 0 ? (
                `防爆破保护中 (${cooldownSeconds}s)`
              ) : (
                <span className="flex items-center justify-center gap-1.5">
                  <Key className="size-4" />
                  解锁控制台
                </span>
              )}
            </Button>
          </form>

          {/* 底部安全机制说明 */}
          <div className="mt-6 pt-4 border-t border-separator text-center space-y-1">
            <p className="text-[11px] text-muted leading-relaxed">
              受 PBKDF2-SHA256 (600,000轮) 强安全摘要与会话 Cookie 保护
            </p>
            <p className="text-[10px] text-muted/70">
              防止公网扫描与 API Key 泄露 · 敏感资产隔离
            </p>
          </div>
        </Card>
      </div>
    </div>
  )
}

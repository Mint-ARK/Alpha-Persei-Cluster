import type { LaunchConfigData } from './api'

export type LaunchShell = 'cmd' | 'powershell' | 'posix'

export function buildLaunchArguments(config: LaunchConfigData): string[] {
  const args: string[] = []
  const add = (name: string, value: string) => args.push(name, value.trim())
  if (config.host_ip?.trim()) add('--host-ip', config.host_ip)
  if (config.https_port && config.https_port !== 443) add('--https-port', String(config.https_port))
  if (config.gw_port && config.gw_port !== 8102) add('--gw-port', String(config.gw_port))
  if (config.game_port && config.game_port !== 8105) add('--game-port', String(config.game_port))
  if (config.db_path?.trim() && config.db_path.trim() !== 'account.db') add('--db', config.db_path)
  if (config.log_level && config.log_level !== 'INFO') add('--log-level', config.log_level)
  if (config.client_assets_dir?.trim()) add('--client-assets-dir', config.client_assets_dir)
  if (config.replay_enabled && config.replay_path?.trim()) add('--replay', config.replay_path)
  return args
}

const quotePosix = (value: string) => "'" + value.replace(/'/g, "'\"'\"'") + "'"
const quotePowerShell = (value: string) => "'" + value.replace(/'/g, "''") + "'"
const quoteWindows = (value: string) => '"' + value.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\+)$/, '$1$1') + '"'

export function formatLaunchCommand(args: string[], shell: LaunchShell): string {
  if (shell === 'posix') return 'python3 -X utf8 main.py' + args.map((arg) => ' ' + quotePosix(arg)).join('')
  const prefix = shell === 'powershell'
    ? '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); $OutputEncoding = [Console]::OutputEncoding; '
    : ''

  // CMD expands %variables% even inside quotes; delayed expansion also consumes !.
  // Transfer such values as UTF-8 JSON so the shell only sees an ASCII payload.
  // This also avoids Windows PowerShell 5's native quoting loss for embedded ".
  const transport = shell === 'cmd' ? /[%!"\r\n]/ : /["\r\n]|\\$/
  if (args.some((arg) => transport.test(arg))) {
    const bytes = new TextEncoder().encode(JSON.stringify(['main.py', ...args]))
    const payload = btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(''))
    const code = `import base64,json,subprocess,sys;sys.exit(subprocess.call([sys.executable,'-X','utf8',*json.loads(base64.b64decode('${payload}'))]))`
    return prefix + 'python -X utf8 -c ' + (shell === 'cmd' ? quoteWindows(code) : quotePowerShell(code))
  }
  const quote = shell === 'cmd' ? quoteWindows : quotePowerShell
  return prefix + 'python -X utf8 main.py' + args.map((arg) => ' ' + quote(arg)).join('')
}

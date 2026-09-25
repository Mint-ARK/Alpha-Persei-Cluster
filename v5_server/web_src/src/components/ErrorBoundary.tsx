import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertCircle, RefreshCw } from 'lucide-react'
import { Button, Card } from '@heroui/react'

interface Props {
  children: ReactNode
  fallbackTitle?: string
}

interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('[ErrorBoundary caught error]:', error, errorInfo)
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex h-full min-h-[400px] w-full items-center justify-center p-6">
          <Card className="max-w-lg border border-danger/30 bg-danger-50/10 p-6 backdrop-blur-md">
            <div className="flex items-start gap-4">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-danger/10 text-danger">
                <AlertCircle className="h-5 w-5" />
              </div>
              <div className="flex-1 space-y-2">
                <h3 className="text-base font-semibold text-foreground">
                  {this.props.fallbackTitle || '面板组件渲染异常'}
                </h3>
                <p className="text-xs text-muted">
                  捕获到未处理的渲染异常，已自动阻止全页面白屏崩溃：
                </p>
                <div className="max-h-32 overflow-auto rounded-lg bg-black/40 p-2 font-mono text-[11px] text-danger-300">
                  {this.state.error?.message || '未知异常'}
                </div>
                <div className="pt-2 flex gap-3">
                  <Button
                    size="sm"
                    variant="secondary"
                    className="gap-1.5"
                    onPress={this.handleReset}
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    重置当前视图
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onPress={() => window.location.reload()}
                  >
                    刷新页面
                  </Button>
                </div>
              </div>
            </div>
          </Card>
        </div>
      )
    }

    return this.props.children
  }
}

import { useState } from 'react'
import { Card, Chip } from '@heroui/react'
import { Trophy, Info } from 'lucide-react'
import { ProviderLogo } from '../panels/AIChat'

interface ContributorItem {
  rank: number
  name: string
  line1: string
  line2: string
  vendor: 'google' | 'deepseek' | 'anthropic' | 'openai' | 'zhipu' | 'moonshot' | 'tencent'
  vendorName: string
  score: number 
  barColor: string
  textColor: string
  achievement: string // 攻坚贡献实录
}

// 严格按照用户实战评估排名录入的 25 款顶尖功勋模型
// 拆分为 line1 + line2 两行紧凑排版，与 AA 榜视觉标准 1:1 对齐
const CONTRIBUTORS: ContributorItem[] = [
  {
    rank: 1,
    name: 'Gemini 3.8 Flash high',
    line1: 'Gemini 3.8',
    line2: 'Flash (high)',
    vendor: 'google',
    vendorName: 'Google',
    score: 50,
    barColor: '#22C55E', // 翠绿
    textColor: '#ffffff',
    achievement: '与 Gemini 3.7 Flash 共同完成了大部分的第五代服务器架构搭建和设计。',
  },
  {
    rank: 2,
    name: 'DeepSeek V4 Flash 0731 high',
    line1: 'DeepSeek V4',
    line2: 'Flash 0731 (high)',
    vendor: 'deepseek',
    vendorName: 'DeepSeek',
    score: 49,
    barColor: '#2563EB', // 科技深蓝
    textColor: '#ffffff',
    achievement: '完成了绝大部分的前期协议分析、内容提取、C# 逆向分析。',
  },
  {
    rank: 3,
    name: 'Gemini 3.7 Flash high',
    line1: 'Gemini 3.7',
    line2: 'Flash (high)',
    vendor: 'google',
    vendorName: 'Google',
    score: 48,
    barColor: '#16A34A',
    textColor: '#ffffff',
    achievement: '与 Gemini 3.8 Flash 共同完成了大部分的第五代服务器架构搭建和设计。',
  },
  {
    rank: 4,
    name: 'Gemini 3.6 Flash high',
    line1: 'Gemini 3.6',
    line2: 'Flash (high)',
    vendor: 'google',
    vendorName: 'Google',
    score: 47,
    barColor: '#15803D',
    textColor: '#ffffff',
    achievement: '与 Gemini 3.7/3.8 Flash 协同推进第五代服务器架构搭建和核心设计。',
  },
  {
    rank: 5,
    name: 'DeepSeek V4 Pro Preview high',
    line1: 'DeepSeek V4 Pro',
    line2: 'Preview (high)',
    vendor: 'deepseek',
    vendorName: 'DeepSeek',
    score: 46,
    barColor: '#1D4ED8',
    textColor: '#ffffff',
    achievement: '完成了最开始的前期试探任务与基础架构摸底。',
  },
  {
    rank: 6,
    name: 'Claude Opus 5 high Max',
    line1: 'Claude Opus 5',
    line2: 'high (Max)',
    vendor: 'anthropic',
    vendorName: 'Anthropic',
    score: 45,
    barColor: '#C26E53', // 经典红赭色
    textColor: '#ffffff',
    achievement: '一次中期审查、登录系统 BUG 修复和前端面板的母版构建。',
  },
  {
    rank: 7,
    name: 'GLM 5.3 MAX',
    line1: 'GLM-5.3',
    line2: '(MAX)',
    vendor: 'zhipu',
    vendorName: '智谱 AI',
    score: 44,
    barColor: '#EA580C', // 暖橙
    textColor: '#ffffff',
    achievement: '中期 BUG 审查、BS 重建、资源报告提取。',
  },
  {
    rank: 8,
    name: 'GLM 5.3 Flash MAX',
    line1: 'GLM-5.3-Flash',
    line2: '(MAX)',
    vendor: 'zhipu',
    vendorName: '智谱 AI',
    score: 43,
    barColor: '#F97316',
    textColor: '#ffffff',
    achievement: '中期 BUG 审查和资源提取。',
  },
  {
    rank: 9,
    name: 'ChatGPT 5.6 Sol Light Middle high xhigh',
    line1: 'ChatGPT 5.6 Sol',
    line2: 'Light (xhigh)',
    vendor: 'openai',
    vendorName: 'OpenAI',
    score: 42,
    barColor: '#27272A', // 炭黑
    textColor: '#ffffff',
    achievement: '综合审计、建议、引导、仓库评定。',
  },
  {
    rank: 10,
    name: 'ChatGPT 6 Astra light',
    line1: 'ChatGPT 6',
    line2: 'Astra (light)',
    vendor: 'openai',
    vendorName: 'OpenAI',
    score: 41,
    barColor: '#3F3F46',
    textColor: '#ffffff',
    achievement: '收尾审核、建议、引导。',
  },
  {
    rank: 11,
    name: 'GLM 5.2 MAX',
    line1: 'GLM-5.2',
    line2: '(MAX)',
    vendor: 'zhipu',
    vendorName: '智谱 AI',
    score: 40,
    barColor: '#FB923C',
    textColor: '#ffffff',
    achievement: '中期 BUG 处理与少量协议分析。',
  },
  {
    rank: 12,
    name: 'ChatGPT 5.6 Terra Middle high',
    line1: 'ChatGPT 5.6 Terra',
    line2: 'Middle (high)',
    vendor: 'openai',
    vendorName: 'OpenAI',
    score: 39,
    barColor: '#52525B',
    textColor: '#ffffff',
    achievement: '早期少量的 BUG 分析与建议引导。',
  },
  {
    rank: 13,
    name: 'ChatGPT 5.6 Luna Max',
    line1: 'ChatGPT 5.6 Luna',
    line2: '(Max)',
    vendor: 'openai',
    vendorName: 'OpenAI',
    score: 38,
    barColor: '#71717A',
    textColor: '#ffffff',
    achievement: '完成了早期的代码回查工作。',
  },
  {
    rank: 14,
    name: 'Claude Opus 4.8 thinking',
    line1: 'Claude Opus 4.8',
    line2: '(thinking)',
    vendor: 'anthropic',
    vendorName: 'Anthropic',
    score: 37,
    barColor: '#D97757',
    textColor: '#ffffff',
    achievement: '进行资产探查与资源边界摸排。',
  },
  {
    rank: 15,
    name: 'Gemini 3.1 Pro high',
    line1: 'Gemini 3.1 Pro',
    line2: '(high)',
    vendor: 'google',
    vendorName: 'Google',
    score: 36,
    barColor: '#059669',
    textColor: '#ffffff',
    achievement: '接力 Gemini 3.6 Flash 进行阶段性架构推进。',
  },
  {
    rank: 16,
    name: 'Gemini 3.5 Flash high',
    line1: 'Gemini 3.5 Flash',
    line2: '(high)',
    vendor: 'google',
    vendorName: 'Google',
    score: 35,
    barColor: '#0D9488',
    textColor: '#ffffff',
    achievement: '早期网页上的建议、网络搜索与数据收集。',
  },
  {
    rank: 17,
    name: 'DeepSeek V4 Flash 0831灰度测试 max',
    line1: 'DeepSeek V4 Flash',
    line2: '0831灰度 (max)',
    vendor: 'deepseek',
    vendorName: 'DeepSeek',
    score: 34,
    barColor: '#3B82F6',
    textColor: '#ffffff',
    achievement: '中期代码全局审查和 BUG 修复。',
  },
  {
    rank: 18,
    name: 'DeepSeek V4.1 Flash high',
    line1: 'DeepSeek V4.1',
    line2: 'Flash (high)',
    vendor: 'deepseek',
    vendorName: 'DeepSeek',
    score: 33,
    barColor: '#60A5FA',
    textColor: '#ffffff',
    achievement: '后期定向专项任务攻坚。',
  },
  {
    rank: 19,
    name: 'ChatGPT 5.5',
    line1: 'ChatGPT 5.5',
    line2: '(base)',
    vendor: 'openai',
    vendorName: 'OpenAI',
    score: 32,
    barColor: '#A1A1AA',
    textColor: '#18181B',
    achievement: '早期重放攻击思路下的部分推进指引。',
  },
  {
    rank: 20,
    name: 'DeepSeek V4 Pro 0813 high',
    line1: 'DeepSeek V4 Pro',
    line2: '0813 (high)',
    vendor: 'deepseek',
    vendorName: 'DeepSeek',
    score: 31,
    barColor: '#93C5FD',
    textColor: '#1E3A8A',
    achievement: '接替 0731 的少量工作（实测效果不佳）。',
  },
  {
    rank: 21,
    name: 'Claude Opus 4.6 thinking',
    line1: 'Claude Opus 4.6',
    line2: '(thinking)',
    vendor: 'anthropic',
    vendorName: 'Anthropic',
    score: 30,
    barColor: '#EA580C',
    textColor: '#ffffff',
    achievement: '阶段性接替 Gemini 3.6 / 3.7 / 3.8 的过渡工作。',
  },
  {
    rank: 22,
    name: 'Claude Sonnet 5 thinking',
    line1: 'Claude Sonnet 5',
    line2: '(thinking)',
    vendor: 'anthropic',
    vendorName: 'Anthropic',
    score: 29,
    barColor: '#E8A593',
    textColor: '#18181B',
    achievement: '少量技术问题搜索与专项参考。',
  },
  {
    rank: 23,
    name: 'KIMI K3 MAX',
    line1: 'Kimi K3',
    line2: '(MAX)',
    vendor: 'moonshot',
    vendorName: 'Moonshot',
    score: 28,
    barColor: '#06B6D4',
    textColor: '#ffffff',
    achievement: '简要的代码扫描与竞技场中的网页设计思路贡献。',
  },
  {
    rank: 24,
    name: 'Hy 4 Preview',
    line1: 'Hy 4',
    line2: '(Preview)',
    vendor: 'tencent',
    vendorName: '腾讯混元',
    score: 27,
    barColor: '#6366F1',
    textColor: '#ffffff',
    achievement: '简单的索引检查与轻量校验。',
  },
  {
    rank: 25,
    name: 'DeepSeek V4 Flash Preview high',
    line1: 'DeepSeek V4 Flash',
    line2: 'Preview (high)',
    vendor: 'deepseek',
    vendorName: 'DeepSeek',
    score: 26,
    barColor: '#BFDBFE',
    textColor: '#1E293B',
    achievement: '辅助 V4 Pro Preview 进行小数据处理调用。',
  },
]

export default function ModelContributorsChart() {
  const [selectedModel, setSelectedModel] = useState<ContributorItem>(CONTRIBUTORS[0])
  const maxScore = 60

  return (
    <Card className="p-6 border border-separator/60 bg-surface shadow-sm space-y-6">
      {/* 顶部标题区：包含 AA 榜风格右上角徽章 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-separator pb-4">
        <div>
          <div className="flex items-center gap-2">
            <Trophy className="size-5 text-amber-500" />
            <h3 className="text-base font-bold text-foreground tracking-tight">项目构建参与者 (Model Contributors Hall)</h3>
            <Chip size="sm" variant="soft" color="accent">
              <Chip.Label className="font-mono text-xs">25 Models</Chip.Label>
            </Chip>
          </div>
          <p className="text-xs text-muted mt-1 leading-relaxed">
            本项目全链路逆向、双端协议适配、WinUI 3 启动中枢及 Web 控制台的构建参与模型参与者，依贡献和使用排名。
          </p>
        </div>
      </div>

      {/* 交互探针高亮详情卡片 */}
      <div className="p-4 rounded-xl border border-separator/60 bg-surface-secondary/30 transition-all">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5">
          <div className="flex items-center gap-2.5 flex-wrap">
            <span
              className="inline-flex items-center justify-center size-6 rounded-md font-mono text-xs font-bold text-white shadow-xs"
              style={{ backgroundColor: selectedModel.barColor }}
            >
              #{selectedModel.rank}
            </span>
            <span className="font-bold text-foreground text-sm font-sans tracking-tight">
              {selectedModel.name}
            </span>
            <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-surface border border-separator/60 text-xs text-foreground/80">
              <ProviderLogo
                model={selectedModel.name}
                provider={selectedModel.vendor}
                className="size-3.5 object-contain"
              />
              <span className="font-medium text-[11px]">{selectedModel.vendorName}</span>
            </div>
          </div>
          <div className="flex items-center gap-2 self-start sm:self-auto">
            <span className="text-xs text-muted font-sans">出力指数:</span>
            <span
              className="font-mono font-bold text-sm"
              style={{ color: selectedModel.barColor }}
            >
              {selectedModel.score}%
            </span>
          </div>
        </div>

        <div className="mt-2.5 flex items-start gap-1.5 text-xs text-muted leading-relaxed">
          <Info className="size-3.5 text-muted/70 shrink-0 mt-0.5" />
          <span>{selectedModel.achievement}</span>
        </div>
      </div>

      {/* 核心柱状图容器 (支持宽幅平滑横向滚动，确保 25 根柱子排列整齐) */}
      <div className="relative overflow-x-auto pb-4 pt-1">
        <div className="min-w-[1360px] select-none py-2 pl-12 pr-6">
          {/* 图表主区域：高 230px，带细虚线参考线与 Y 轴刻度 */}
          <div className="relative h-[230px]">
            {/* 6 条标准水平虚线参考线 (对应 50, 40, 30, 20, 10, 0) */}
            <div className="absolute inset-0 pointer-events-none flex flex-col justify-between pt-2 pb-0">
              <div className="w-full flex items-center gap-2">
                <span className="text-[10px] font-mono text-muted/50 w-5 text-right">50</span>
                <div className="flex-1 border-b border-dashed border-separator/35" />
              </div>
              <div className="w-full flex items-center gap-2">
                <span className="text-[10px] font-mono text-muted/50 w-5 text-right">40</span>
                <div className="flex-1 border-b border-dashed border-separator/35" />
              </div>
              <div className="w-full flex items-center gap-2">
                <span className="text-[10px] font-mono text-muted/50 w-5 text-right">30</span>
                <div className="flex-1 border-b border-dashed border-separator/35" />
              </div>
              <div className="w-full flex items-center gap-2">
                <span className="text-[10px] font-mono text-muted/50 w-5 text-right">20</span>
                <div className="flex-1 border-b border-dashed border-separator/35" />
              </div>
              <div className="w-full flex items-center gap-2">
                <span className="text-[10px] font-mono text-muted/50 w-5 text-right">10</span>
                <div className="flex-1 border-b border-dashed border-separator/35" />
              </div>
              <div className="w-full flex items-center gap-2">
                <span className="text-[10px] font-mono text-muted/50 w-5 text-right">0</span>
                <div className="flex-1 border-b border-separator/60" />
              </div>
            </div>

            {/* 25 根柱子主体 (严格靠底对齐) */}
            <div className="relative h-full flex items-end justify-between pl-7">
              {CONTRIBUTORS.map((item) => {
                const heightPercent = (item.score / maxScore) * 100
                const isSelected = selectedModel.rank === item.rank

                return (
                  <div
                    key={item.name}
                    onClick={() => setSelectedModel(item)}
                    className="group relative flex flex-col items-center justify-end h-full w-[46px] cursor-pointer"
                  >
                    <div
                      className={`w-[34px] rounded-t-md transition-all duration-200 flex flex-col items-center justify-start pt-1.5 ${
                        isSelected
                          ? 'ring-2 ring-accent ring-offset-2 ring-offset-surface scale-[1.03] shadow-md z-10'
                          : 'hover:opacity-90 hover:scale-[1.02]'
                      }`}
                      style={{
                        height: `${heightPercent}%`,
                        backgroundColor: item.barColor,
                      }}
                    >
                      {/* 柱体内靠上的白色加粗数值读数 */}
                      <span
                        className="text-xs font-bold font-mono select-none drop-shadow-xs"
                        style={{ color: item.textColor }}
                      >
                        {item.score}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* 柱子底座与原装厂商 Logo 图标行 (使用项目中统一原装官方品牌资产) */}
          <div className="flex items-center justify-between pl-7 pt-2.5 pb-1">
            {CONTRIBUTORS.map((item) => {
              const isSelected = selectedModel.rank === item.rank
              return (
                <div
                  key={`logo-${item.name}`}
                  onClick={() => setSelectedModel(item)}
                  className="w-[46px] flex items-center justify-center cursor-pointer transition-transform hover:scale-110"
                  title={`${item.vendorName}: ${item.name}`}
                >
                  <ProviderLogo
                    model={item.name}
                    provider={item.vendor}
                    className={`size-5 shrink-0 object-contain transition-all ${
                      isSelected ? 'scale-110 drop-shadow-xs' : 'opacity-85 hover:opacity-100'
                    }`}
                  />
                </div>
              )
            })}
          </div>

          {/* 模型名称文本行：向左下倾斜 50°，以右上角为轴锚定在 Logo 正下方，绝不上窜遮挡柱子 */}
          <div className="flex items-start justify-between pl-7 h-[130px] pt-1.5 overflow-visible">
            {CONTRIBUTORS.map((item) => {
              const isSelected = selectedModel.rank === item.rank
              return (
                <div
                  key={`name-${item.name}`}
                  onClick={() => setSelectedModel(item)}
                  className="w-[46px] relative flex justify-center cursor-pointer"
                >
                  <div
                    className="absolute top-1 right-[23px] origin-top-right transition-transform"
                    style={{ transform: 'rotate(-50deg)' }}
                  >
                    <div className="text-right whitespace-nowrap leading-[1.25]">
                      <div
                        className={`text-[11px] font-semibold font-sans tracking-tight transition-colors ${
                          isSelected
                            ? 'text-accent font-bold underline decoration-accent/60 underline-offset-2'
                            : 'text-foreground/90 hover:text-foreground'
                        }`}
                      >
                        {item.line1}
                      </div>
                      {item.line2 && (
                        <div
                          className={`text-[10px] font-medium font-sans tracking-tight transition-colors ${
                            isSelected ? 'text-accent/90' : 'text-muted hover:text-muted-foreground'
                          }`}
                        >
                          {item.line2}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </Card>
  )
}

import { useState } from 'react'
import { Card, Chip } from '@heroui/react'
import {
  Binary,
  Bug,
  Cpu,
  Activity,
  Boxes,
  Box,
  Network,
  Code2,
  FileCode,
  Search,
  Workflow,
  Terminal,
  Sparkles,
  Laptop,
  FolderGit2,
  Wrench,
  Bot,
} from 'lucide-react'

export interface ToolContributorItem {
  name: string
  category: 'reverse' | 'engine' | 'agent'
  categoryLabel: string
  role: string
  icon: typeof Terminal
  tagColor: string
  iconColor: string
}

const TOOLS: ToolContributorItem[] = [
  {
    name: 'IDA 9 PRO',
    category: 'reverse',
    categoryLabel: '逆向分析',
    role: '反汇编逆向工程、静态二进制分析与函数逻辑结构还原。',
    icon: Binary,
    tagColor: 'text-emerald-500 bg-emerald-500/10 border-emerald-500/20',
    iconColor: 'text-emerald-500',
  },
  {
    name: 'X64 DBG',
    category: 'reverse',
    categoryLabel: '动态调试',
    role: 'x64 汇编指令动态单步跟踪、内存断点拦截与寄存器状态检视。',
    icon: Bug,
    tagColor: 'text-amber-500 bg-amber-500/10 border-amber-500/20',
    iconColor: 'text-amber-500',
  },
  {
    name: 'WinDBG',
    category: 'reverse',
    categoryLabel: '系统调试',
    role: 'Windows 内存转储分析、崩溃 Dump 探查与内核级异常捕获。',
    icon: Cpu,
    tagColor: 'text-blue-500 bg-blue-500/10 border-blue-500/20',
    iconColor: 'text-blue-500',
  },
  {
    name: 'Windows 内存诊断工具',
    category: 'reverse',
    categoryLabel: '系统诊断',
    role: '物理内存完整性压测、排除硬件随机比特翻转与异常稳定性排查。',
    icon: Activity,
    tagColor: 'text-rose-500 bg-rose-500/10 border-rose-500/20',
    iconColor: 'text-rose-500',
  },
  {
    name: 'AssetStudio',
    category: 'engine',
    categoryLabel: '资源解构',
    role: 'Unity 资产资源解包、Sprite 图集与 TextAsset 资源结构导出。',
    icon: Boxes,
    tagColor: 'text-purple-500 bg-purple-500/10 border-purple-500/20',
    iconColor: 'text-purple-500',
  },
  {
    name: 'Unity Engine',
    category: 'engine',
    categoryLabel: '游戏引擎',
    role: '客户端底层运行时行为对照、渲染管线与资源生命周期检视。',
    icon: Box,
    tagColor: 'text-zinc-400 bg-zinc-500/10 border-zinc-500/20',
    iconColor: 'text-zinc-400',
  },
  {
    name: 'WireShark',
    category: 'reverse',
    categoryLabel: '网络嗅探',
    role: '全协议数据封包实时抓取、TCP/UDP 流分析与握手特征剖析。',
    icon: Network,
    tagColor: 'text-cyan-500 bg-cyan-500/10 border-cyan-500/20',
    iconColor: 'text-cyan-500',
  },
  {
    name: 'Visual Studio',
    category: 'engine',
    categoryLabel: '原生开发',
    role: 'WinUI 3 启动管理中枢工程构建、C# 原生编译与依赖总成。',
    icon: Code2,
    tagColor: 'text-indigo-500 bg-indigo-500/10 border-indigo-500/20',
    iconColor: 'text-indigo-500',
  },
  {
    name: 'Visual Studio Code',
    category: 'engine',
    categoryLabel: '全栈开发',
    role: 'Web 管理控制面板、Python 服务端核心及全套运维脚本主工作台。',
    icon: FileCode,
    tagColor: 'text-sky-500 bg-sky-500/10 border-sky-500/20',
    iconColor: 'text-sky-500',
  },
  {
    name: 'HxD HEX Editor',
    category: 'reverse',
    categoryLabel: '十六进制分析',
    role: '二进制数据十六进制检视、协议魔数比对与原始字节补丁修改。',
    icon: Search,
    tagColor: 'text-teal-500 bg-teal-500/10 border-teal-500/20',
    iconColor: 'text-teal-500',
  },
  {
    name: 'Claude Code Switch',
    category: 'agent',
    categoryLabel: 'Agent 调度',
    role: '多智能体工作流上下文无缝调度、多模型提示词切换与流水线编排。',
    icon: Workflow,
    tagColor: 'text-orange-500 bg-orange-500/10 border-orange-500/20',
    iconColor: 'text-orange-500',
  },
  {
    name: 'Claude Code',
    category: 'agent',
    categoryLabel: '终端智能体',
    role: 'Anthropic 官方终端智能体开发环境，长时序架构推演与代码重构。',
    icon: Terminal,
    tagColor: 'text-amber-600 bg-amber-600/10 border-amber-600/20',
    iconColor: 'text-amber-600',
  },
  {
    name: 'Codex',
    category: 'agent',
    categoryLabel: '代码推理',
    role: '代码生成与上下文推理环境，辅助类型系统与业务逻辑实现。',
    icon: Sparkles,
    tagColor: 'text-emerald-500 bg-emerald-500/10 border-emerald-500/20',
    iconColor: 'text-emerald-500',
  },
  {
    name: 'Zcode',
    category: 'agent',
    categoryLabel: '编码辅助',
    role: '高阶智能编码辅助工作台，工程级规范审查与结构化代码补全。',
    icon: Laptop,
    tagColor: 'text-violet-500 bg-violet-500/10 border-violet-500/20',
    iconColor: 'text-violet-500',
  },
  {
    name: 'DeepSeek harness',
    category: 'agent',
    categoryLabel: '推理基架',
    role: 'DeepSeek 原型推导与测试基架，批量协议号验证与自动化测试。',
    icon: Cpu,
    tagColor: 'text-blue-600 bg-blue-600/10 border-blue-600/20',
    iconColor: 'text-blue-600',
  },
  {
    name: 'Reasonix',
    category: 'agent',
    categoryLabel: '逻辑推演',
    role: '长链复杂逻辑推演与架构算法演练场，排查深层状态机时序冲突。',
    icon: Workflow,
    tagColor: 'text-fuchsia-500 bg-fuchsia-500/10 border-fuchsia-500/20',
    iconColor: 'text-fuchsia-500',
  },
  {
    name: 'Gemini CLI',
    category: 'agent',
    categoryLabel: '终端自动化',
    role: 'Google 官方终端自动化工作流工具，自动化脚本协作与多模态分析。',
    icon: Terminal,
    tagColor: 'text-green-500 bg-green-500/10 border-green-500/20',
    iconColor: 'text-green-500',
  },
  {
    name: 'Antigravity',
    category: 'agent',
    categoryLabel: '自主工程中枢',
    role: 'Google DeepMind 智能体结对编程平台，全自主工程实现与总线管理。',
    icon: Bot,
    tagColor: 'text-purple-600 bg-purple-600/10 border-purple-600/20',
    iconColor: 'text-purple-600',
  },
  {
    name: 'OpenCode',
    category: 'agent',
    categoryLabel: '智能编码',
    role: '开源智能编码工作流中枢，跨文件全局检索与语义上下文增强。',
    icon: FolderGit2,
    tagColor: 'text-teal-500 bg-teal-500/10 border-teal-500/20',
    iconColor: 'text-teal-500',
  },
  {
    name: 'CodeBuddy',
    category: 'agent',
    categoryLabel: '结对辅助',
    role: '智能结对编程辅助中枢，实时协同代码校验与架构健壮性巡检。',
    icon: Wrench,
    tagColor: 'text-sky-500 bg-sky-500/10 border-sky-500/20',
    iconColor: 'text-sky-500',
  },
]

type FilterType = 'all' | 'reverse' | 'engine' | 'agent'

export default function IDEContributorsGrid() {
  const [filter, setFilter] = useState<FilterType>('all')

  const filteredTools = TOOLS.filter((tool) => {
    if (filter === 'all') return true
    return tool.category === filter
  })

  return (
    <Card className="p-6 border border-separator/60 bg-surface shadow-sm space-y-6">
      {/* 顶部标题栏与筛选器 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-separator pb-4">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <Wrench className="size-5 text-accent" />
            <h3 className="text-base font-bold text-foreground tracking-tight">
              参与构建的 IDE 与工程工具链 (IDEs & Engineering Toolchains)
            </h3>
            <Chip size="sm" variant="soft" color="accent">
              <Chip.Label className="font-mono text-xs">20 Tools</Chip.Label>
            </Chip>
            <span className="px-2 py-0.5 rounded-full text-[11px] font-medium bg-surface-secondary border border-separator text-muted">
              排名不分先后
            </span>
          </div>
          <p className="text-xs text-muted mt-1 leading-relaxed">
            本项目从底层协议逆向、汇编断点调试、引擎资产解包、WinUI 原生构建到多智能体自主结对开发所依仗的核心工具环境。
          </p>
        </div>

        {/* 分类筛选标签 */}
        <div className="flex items-center gap-1.5 p-1 rounded-xl bg-surface-secondary/70 border border-separator/50 self-start sm:self-auto shrink-0">
          <button
            onClick={() => setFilter('all')}
            className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
              filter === 'all'
                ? 'bg-surface text-foreground font-bold shadow-xs border border-separator/60'
                : 'text-muted hover:text-foreground'
            }`}
          >
            全部 ({TOOLS.length})
          </button>
          <button
            onClick={() => setFilter('reverse')}
            className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
              filter === 'reverse'
                ? 'bg-surface text-foreground font-bold shadow-xs border border-separator/60'
                : 'text-muted hover:text-foreground'
            }`}
          >
            逆向与调试 (6)
          </button>
          <button
            onClick={() => setFilter('engine')}
            className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
              filter === 'engine'
                ? 'bg-surface text-foreground font-bold shadow-xs border border-separator/60'
                : 'text-muted hover:text-foreground'
            }`}
          >
            应用与引擎 (4)
          </button>
          <button
            onClick={() => setFilter('agent')}
            className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
              filter === 'agent'
                ? 'bg-surface text-foreground font-bold shadow-xs border border-separator/60'
                : 'text-muted hover:text-foreground'
            }`}
          >
            智能体工作台 (10)
          </button>
        </div>
      </div>

      {/* 20 款工具卡片矩阵 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3.5">
        {filteredTools.map((tool) => {
          const IconComponent = tool.icon
          return (
            <div
              key={tool.name}
              className="group relative flex flex-col justify-between p-3.5 rounded-xl border border-separator/60 bg-surface-secondary/25 hover:bg-surface-secondary/70 transition-all duration-200 hover:border-accent/40 hover:shadow-xs"
            >
              <div>
                <div className="flex items-center justify-between gap-2 mb-2">
                  <div
                    className={`size-8 rounded-lg flex items-center justify-center bg-surface border border-separator/60 transition-transform group-hover:scale-105 shadow-xs`}
                  >
                    <IconComponent className={`size-4 ${tool.iconColor}`} />
                  </div>
                  <span
                    className={`text-[10px] font-medium px-2 py-0.5 rounded-md border ${tool.tagColor}`}
                  >
                    {tool.categoryLabel}
                  </span>
                </div>

                <div className="font-bold text-foreground text-sm font-sans tracking-tight line-clamp-1 mb-1 group-hover:text-accent transition-colors">
                  {tool.name}
                </div>

                <p className="text-[11px] text-muted leading-relaxed line-clamp-3">
                  {tool.role}
                </p>
              </div>

              <div className="mt-3 pt-2 border-t border-separator/40 flex items-center justify-between text-[10px] text-muted/80">
                <span className="font-mono">BUILD TOOL</span>
                <span className="text-emerald-500 font-medium">✓ 已认证</span>
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

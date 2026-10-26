# V5 GM 控制面板前端（唯一权威源码）

基于 **HeroUI v3 + React 19 + Tailwind CSS v4** 的 V5 GM 控制面板主前端工程。
页面通过 `src/lib/api.ts` 接入 GM API；服务不可用时，个别面板会使用静态数据保持可预览状态。

## 源码与部署边界

- `control_panel_v2/` 是控制面板页面、主题和 API 接入的唯一权威源码。
- `control_panel/` 是 OPUS5 示例样板，仅作视觉参考，不在其中实现当前业务。
- `v5_server/gm_console_src/` 是旧的示例/过渡工程，不与 V2 并行维护业务页面。
- `v5_server/static_web/` 是 V2 构建产物的部署目标，不是第三份源码。
- 正确流程为：修改 V2 → `npm run check` → 构建 V2 → 发布构建产物 → 验证 `/web/gm_console.html`。

---

## 运行

```bash
cd control_panel_v2
npm install
npm run dev          # http://localhost:5273
```

构建：

```bash
npm run build        # 产出 dist/，并额外生成 dist/control-panel.html
npm run check        # 只做 TypeScript 类型检查
npm run shot         # 用无头 Edge/Chrome 给 9 个面板批量截图到 dist/shots/
```

`dist/control-panel.html` 是把 JS/CSS 全部内联后的**单文件**，可以直接双击用浏览器打开，
方便把视觉稿发给别人看，不需要起服务。

URL 参数：

- `#accounts` / `#gacha` …… 直接打开某个面板（页签切换会同步写回 hash）
- `?theme=dark` 以深色主题打开，`?theme=system` 跟随系统

---

## 设计说明

### 为什么是「真 HeroUI」而不是仿样式

页面直接依赖 `@heroui/react@3.2.4` 与 `@heroui/styles@3.2.4`，
用的是 `Card / Tabs / Table / Select / Slider / Switch / Alert / ProgressBar / Chip / SearchField`
等真实组件，而不是用 Tailwind 手搓外观。所以圆角、阴影、焦点环、悬停过渡、深色主题
都与 heroui.com 上的组件展示一致。

### 色彩：只用语义 token

全站不出现 `slate-200`、`blue-600` 这类固定色，一律走 HeroUI 的语义变量：

| 用途 | token |
| --- | --- |
| 页面底色 / 文字 | `bg-background` `text-foreground` |
| 卡片 / 次级面 | `bg-surface` `bg-surface-secondary` |
| 次要文字 | `text-muted` |
| 分隔线 | `border-separator` `divide-separator` |
| 主色 / 状态色 | `accent` `success` `warning` `danger`（含 `-soft` 柔和变体） |

好处是切换深色主题时不需要写任何 `dark:` 变体——右上角的月亮图标或「系统设置 → 外观」
切换 `data-theme` 即可整站生效。

### 字体

HeroUI v3 自身不覆写字体，组件继承 Tailwind v4 的 `--font-sans`（系统字体栈），
这正是官网组件展示区的观感来源。`src/styles.css` 沿用同一条链路，只补齐中文字形：

- iOS / macOS → SF Pro + **苹方 PingFang SC**
- Windows → Segoe UI + **微软雅黑**

关键点：中文字形必须排在 generic `sans-serif` **之前**，否则 Windows 上会回退到宋体。
`index.html` 同时声明了 `lang="zh-CN"`。

### 字号层级

刻意压到 5 级，避免控制台里出现七八种字号：

| 级别 | 尺寸 | 用途 |
| --- | --- | --- |
| 26px / semibold | `text-[26px]` | 指标卡数值 |
| 20px / semibold | `text-xl` | 页面标题 |
| 15px / semibold | `text-[15px]` | 区块标题 |
| 14px | `text-sm` | 正文、表格单元格、卡片标题（HeroUI 默认） |
| 12px | `text-xs` | 辅助说明、表头、元信息（HeroUI 表头默认） |

所有数字列都加了 `tabular-nums`，位数不同也能右对齐成一条竖线。

### 对齐规则

这三条是整页「不散」的来源，都收敛在 `src/components/kit.tsx` 里：

1. **`ListRow` / 列表行** —— 图标 32px、标题区 `flex-1`、元信息与操作区固定宽度右对齐。
   服务健康度、活动开关、附件清单、待发清单全部复用同一套栏宽。
2. **`StatCard`** —— 内容区用 `justify-end`，即使某张卡的标签换行，四张卡的数值仍在同一基线。
   外层栅格用 `auto-rows-fr` 保证等高。
3. **`DataList` / `DataRow`** —— 左标签 `text-muted`、右值 `font-medium tabular-nums`，
   行间用 `divide-separator` 分隔，首尾行不留多余内边距。

间距上只用 4 的倍数：卡片内 `p-5`、栅格 `gap-4`、区块之间 `space-y-6`、行内 `gap-3.5`。

### 布局

```
┌─────────────────────────────────────────────────────┐
│ 顶栏  logo · 环境标记          搜索 · 通知 · 主题 · 头像 │
├──────────┬──────────────────────────────────────────┤
│ 工作区卡片 │  页面标题 + 描述 + 右上操作                │
│ ──────── │  ────────────────────────────────────    │
│ 纵向选项卡 │  区块（标题 + 描述 + 操作）                │
│  · 总览   │    卡片栅格 / 表格 / 表单                 │
│  · 监控   │                                          │
│  ─────   │                                          │
│  · 账号   │                                          │
│  ...     │                                          │
│ ──────── │                                          │
│ 运行状态卡 │                                          │
└──────────┴──────────────────────────────────────────┘
```

左侧是 HeroUI 的 `Tabs orientation="vertical"`：灰底容器 + 选中项白色胶囊，
即组件库自带的 segmented 观感。分组不是自己画的标题，而是用官方的 `Tabs.Separator`
在每组第一项上方生成细线——放在 `TabList` 里的任意 `div` 会破坏 React Aria 的 collection。

窄屏（< 768px）侧栏收成 60px 图标条，内容区栅格自动降列。

---

## 目录

```
control_panel/
├─ index.html               lang="zh-CN"，避免中文回退宋体
├─ vite.config.ts
├─ scripts/inline.mjs       构建后把 JS/CSS 内联成单文件
├─ scripts/shot.mjs         无头浏览器批量截图
└─ src/
   ├─ main.tsx
   ├─ styles.css            字体链路 + 主题基座（唯一的全局 CSS）
   ├─ App.tsx               顶栏 / 侧栏 / Tabs 外壳
   ├─ lib/theme.tsx         light · dark · system 三态主题
   ├─ components/kit.tsx    版面基元（只管排版对齐，不重造视觉）
   ├─ lib/useElementSize.ts ResizeObserver 尺寸订阅（网格列数 / 虚拟窗口用）
   ├─ data/mock.ts          静态样例数据
   ├─ data/warehouse.ts     仓库压力数据生成器（确定性伪随机）
   └─ panels/               10 个面板
      ├─ Overview.tsx       总览看板：指标卡 · 服务健康度 · 资源水位 · 审计流水
      ├─ Realtime.tsx       实时监控：告警 · 节点负载 · 日志流 · 接口分布
      ├─ Accounts.tsx       账号管理：检索工具条 · 数据表 · 分页
      ├─ Inventory.tsx      资源背包：发放表单 · 待发清单 · 物品配置表
      ├─ Warehouse.tsx      仓库物品：上万条数据的密集展示与性能校验
      ├─ Mail.tsx           邮件派发：编辑器 · 附件 · 客户端预览 · 发送历史
      ├─ Gacha.tsx          卡池保底：预设卡 · 滑块 · 开关组 · 生效摘要
      ├─ Activities.tsx     活动开关：开关列表 · 后续排期
      ├─ Server.tsx         服务运维：进程控制 · 运行参数 · 危险操作区
      └─ Settings.tsx       系统设置：表单栅格 · 主题分段控件 · 关于
```

---

## 仓库物品面板（容量校验）

这个面板不是用来展示真实仓库内容的，它的目的是**压测这套视觉在上万条数据下还成不成立**。

三种视图共用同一套窗口化实现（按固定行高算可视区间 + `translateY` 偏移），所以网格、表格、
紧凑列表可以用同一组读数横向对比：

| 视图 | 行高 | 20,000 条时挂载 | DOM 节点 | 首帧 |
| --- | --- | --- | --- | --- |
| 网格卡片 | 124px（6 列） | 48 | 722 | ~12 ms |
| 数据表格 | 46px | 16 | 265 | ~17 ms |
| 紧凑列表 | 34px | 20 | 148 | ~16 ms |

把「虚拟滚动」关掉即可看到对照组：同样的网格视图，**5,000** 条（已被上限拦下）就要
75,002 个 DOM 节点、235 ms 首帧——数据量只有四分之一，代价却是两个数量级。

面板里四张指标卡是实时测出来的，不是写死的：

- **数据规模** —— 生成耗时（`performance.now()` 包住生成器）与筛选耗时（包住 `filter`）
- **实际挂载条目** —— 当前切片长度及其占命中总量的比例
- **视口 DOM 节点** —— `getElementsByTagName('*').length`，只在控制项变化时采样
- **首帧渲染 / 滚动帧率** —— render 期间打点、`useLayoutEffect` 读出；帧率在滚动时用
  `requestAnimationFrame` 采样，停止滚动 500ms 后自动歇下来

数据由 `mulberry32` + 固定种子生成，同一规模每次结果完全一致，便于反复对比和截图；
数量刻意跨 1~5 位，用来检验 `tabular-nums` 下的右对齐。

URL 可直接指定校验配置：`?wh=table&whSize=20000&whVirtual=0#warehouse`

---

## 改数据 / 加面板

- 改文案与数值：`src/data/mock.ts`
- 加一个面板：在 `src/panels/` 建文件，然后在 `src/App.tsx` 的 `NAV` 数组里加一项
  （`groupStart: true` 会在它上方生成分组线）。

# JEV Trading Desk V1.0 — 组件清单（Component Manifest）

> TL;DR
> - 六页面共用一套统一数据层（`api/client.ts` + `api/useJev.ts`）、一套展示组件（`components/*`）、一个外壳（`App.tsx` + `components/Shell.tsx`）。
> - 源码共 14 个文件、约 1605 行，全部位于 `src/trading/`。
> - 设计 token 集中在 `styles.css`，统一深色高密度主题，绝不引入第二套样式。
> - 入口双轨：`index.html`（既有命令中心，不改）/ `trading.html`（新交易终端，`#/command|workbench|universe|strategy|account|team` hash 路由）。

---

## [1] 文件清单

| 文件 | 行数 | 职责 |
|---|---|---|
| `src/trading/main.tsx` | — | React 19 根：挂载 `TradingApp` 到 `#trading-root` |
| `src/trading/App.tsx` | — | 外壳 + hash 路由（刷新可恢复）+ `?audit=open` 深链打开审计抽屉 |
| `src/trading/api/client.ts` | — | **唯一** `/api/jev` 客户端：`jevFetch`（15s 超时/Abort）、`placePaperOrder`、`cancelPaperOrder`、`makeIds`、`labelClass`、类型 |
| `src/trading/api/useJev.ts` | — | `useJevQuery<T>`：loading/stale/error/unreachable/lastFetched/refetch 状态机 |
| `src/trading/components/Shell.tsx` | — | `Sidebar`（6 导航）、`TopBar`（每页 meta 标签 + 时钟）、`AuditDrawer`（合规红线/溯源）、`NAV`/`PageId` |
| `src/trading/components/common.tsx` | — | `StatusPill` `Panel` `Metric` `Banner` `StateView`（诚实 loading/error/empty）`SourceFooter` `DataTable` `fmt` `fmtUsd` |
| `src/trading/components/icons.tsx` | — | 内联 SVG 图标：`IconCommand/Chart/Globe/Flask/Wallet/Team/Shield/Bolt/Book` |
| `src/trading/pages/AICommandCenter.tsx` | — | 页①：事实 Banner、30 天时钟、工程门禁、七工作线、数据审计、真实行情快照、Provider BLOCKED |
| `src/trading/pages/TradingWorkbench.tsx` | — | 页②：`KlineChart`(lightweight-charts) `OrderBook` `OrderForm`(仅服务端) `AccountAndOrders` `trades`；币种/周期切换 |
| `src/trading/pages/MarketUniverse3D.tsx` | — | 页③：Three.js 立方体（24h 涨跌幅高度/颜色编码）+ OrbitControls；"态势感知非资金流向" |
| `src/trading/pages/StrategyArena.tsx` | — | 页④：诚实 NO_DATA；真实 K 线基础数作"样本基础非收益" |
| `src/trading/pages/PaperAccount.tsx` | — | 页⑤：真实账户/持仓/委托/事件/回放；权益曲线仅快照存在时显示；撤单 |
| `src/trading/pages/AIExpertTeam.tsx` | — | 页⑥：六角色 workflow 全 BLOCKED；命名差异展示；真实审计日志 |
| `src/trading/styles.css` | — | 设计 token + 布局（232px 侧栏 + 主区）+ panel/pill/table/form/orderbook/3D/drawer |

> 行数列出于交付时由 `wc -l` 统计（合计约 1605 行）。

---

## [2] 组件依赖关系

```
main.tsx
 └─ App.tsx  (hash 路由 + refresh restore + ?audit=open)
     ├─ Shell.tsx  → Sidebar / TopBar / AuditDrawer
     ├─ common.tsx → StatusPill / Panel / Metric / Banner / StateView / SourceFooter / DataTable
     ├─ icons.tsx  → 内联 SVG
     └─ pages/*
         ├─ api/client.ts   (jevFetch / 下单 / 撤单)
         └─ api/useJev.ts   (useJevQuery 轮询状态机)
```

**铁律**：任何页面只能通过 `api/client.ts` 的 `jevFetch`/`useJevQuery` 取数；不存在页面级静态 JSON、不存在页面级直连引擎、不存在客户端定价/记账/撮合。

---

## [3] 设计 Token（来自 6 张参考图，统一应用于全部页面）

| Token | 值 | 用途 |
|---|---|---|
| `--bg` | `#070B12` | 页面底 |
| `--surface` | `#0D141F` | 面板底 |
| `--surface-alt` | `#111B29` | 二级面板 |
| `--border` | `#1B2A3B` | 分隔线 |
| `--text` | `#E4EDF7` | 主文字 |
| `--muted` | `#7C8DA3` | 次文字 |
| `--up` | `#22D3A6` | 涨（红/绿按中国习惯此处 up=绿） |
| `--down` | `#FF5A6A` | 跌（红） |
| `--accent` | `#3B82F6` | 强调 |
| `--amber` | `#F5B544` | BLOCKED/警示 |

字体：等宽数字（`ui-monospace, monospace`），紧凑高密度栅格。

---

## [4] 诚实状态渲染（无伪造）

`StateView` 三态：
1. `loading && data===undefined` → 旋转器 + "正在从统一适配层读取真实数据…"
2. `unreachable || error` → "无法从适配层获取数据" + 真实错误（如 `NOT_FOUND`/`MARKET_UNAVAILABLE`）
3. `emptyText` 命中（空数组/空对象）→ 显式说明"不是故障，而是该数据尚未产生"

页面绝不把 `BACKFILL`/`NO_DATA`/`BLOCKED` 渲染成 LIVE；`SourceFooter` 始终展示 `来源/通道/标签/生成UTC/HTTP`，供主理人逐页审计溯源。

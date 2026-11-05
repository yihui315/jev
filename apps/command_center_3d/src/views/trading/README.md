# JEV Trading Desk — Real API Contract (V4.0)

> **Hard rule (V4.0 总设计令):** 所有字段与端点必须以 Night Researcher 的**实际** API Schema 为准，**不得凭空编造接口**。
> 本文件是源码审计的产物，修正了早期 `integration/jev/tradingApi.mjs` 与初版 `paperApi.ts` 中虚构的 `/api/v1/trading` 合约。

## 1. 真相：真实端点不是 `/api/v1/trading`

早期 `integration/jev/tradingApi.mjs` 与初版前端 `services/paperApi.ts` 指向 `DEFAULT_BASE = "/api/v1/trading"` 并调用 `/market/snapshot`、`/order-book`、`/research/runs`、`/strategies` 等——**这些端点都不存在于 Night Researcher**。

Night Researcher 是 Cloudflare Pages Functions，**路由由文件目录决定**（`functions/api/...` → `/api/...`）。真实已实现的端点如下：

| 方法 | 真实路径 | 处理器 | 说明 |
|------|----------|--------|------|
| POST | `/api/paper/session` | `paper-ledger.mjs:handlePaperSession` | 建立 `nr_paper_session` Cookie 会话（HMAC 签名，`Secure; SameSite=Strict; Path=/api/`）。**所有写操作与账户读取都需要它。** |
| GET | `/api/paper/account` | `paper-server.mjs:handlePaperAccount` | 服务端权威账户 + 摘要 + 标记价。 |
| POST | `/api/paper/orders` | `paper-server.mjs:handlePaperOrders` | 下单。仅允许字段：`clientOrderId, confirmationId, symbol, side, type, quantity, limitPrice`。 |
| POST | `/api/paper/orders/[orderId]/cancel` | `paper-server.mjs:handlePaperOrderCancel` | 撤单。Body 仅需 `clientActionId`。 |
| POST | `/api/paper/account/reset` | `paper-server.mjs:handlePaperAccountReset` | 重置账户。Body 需 `clientActionId, confirmationId`。 |
| POST | `/api/paper/market-tick` | `paper-server.mjs:handlePaperMarketTick` | 服务端用最新盘口撮合挂单（限价单成交）。 |
| GET | `/api/paper/events` | `paper-server.mjs:handlePaperServerEvents` | 服务端状态事件流（游标 `after`）。 |
| GET | `/api/paper/replay` | `paper-server.mjs:handlePaperReplay` | 事件回放校验（防篡改）。 |
| GET/POST | `/api/paper/ledger` | `paper-ledger.mjs:handlePaperLedger` | 可信研究/风险事件账本（AI 运行轨迹）。 |
| POST | `/api/agents/research` | `agent-orchestrator.mjs:handleAgentResearch` | 六角色研究运行（**无状态**，不存储；返回完整 outputs）。 |

**不存在**的端点（不要再调用）：`/api/v1/trading/*`、`/market/snapshot`、`/market/orderbook`、`/strategies`、`/research/runs/{id}` 存储查询。

## 2. 账户与订单 Schema（来自 `paper-server.mjs` 真实实现）

`GET /api/paper/account` 返回信封：

```ts
{
  ok: true,
  mode: "server-authoritative-paper",
  account: PaperAccount,        // 含 positions(对象)/orders[]/trades[]
  marks: Record<string, number>,// 持仓标的服务端标记价
  summary: AccountSummary,      // 服务端推导的风险/盈亏摘要
  markQuality: "verified" | "unknown",
  version: number,
  stateHash: string
}
```

关键差异（**不再有杠杆 / 多空 / 保证金**）：

- `account.cash` / `account.reservedCash`（USDT，定点转 Number）
- `account.positions: { [symbol]: { qty, avgPrice, reservedQty } }`
- `account.orders[]`: `status` ∈ `NEW | OPEN | PARTIALLY_FILLED | FILLED | REJECTED | CANCELED`（**不是** `PENDING_CONFIRMATION/SUBMITTED/PARTIAL`）
- `account.trades[]`: 成交明细（含 `realizedPnl`, `liquidityModel: "server-visible-orderbook-walk"`）
- `summary`: `equity, availableCash, marketValue, unrealizedPnl, realizedPnl, totalPnl, dailyPnl, drawdown, exposurePct, positions[], dailyLossLocked, drawdownLocked`

`POST /api/paper/orders` 返回 `CreateOrderResponse`（展开 account 信封 + `order, fills, duplicate, accepted, rejection`）。

## 3. 研究 / AI 六角色（`/api/agents/research`）

`POST /api/agents/research` body：`{ snapshot, provider, model?, runId? }`。其中 `snapshot` 必须是可序列化的市场快照（**浏览器不可信**），`provider` 仅传 Provider ID，密钥由 Night Researcher 服务端按其 ProviderRegistry 解析（**前端不传 apiKey**）。

返回 `ResearchRunResult`：`{ ok, runId, roles: ["SPOTTER","PRIOR","EDGE","KELLY","TAKER","CLOSER"], outputs, usage, safety }`。

- `SPOTTER / PRIOR / EDGE / CLOSER` = LLM 角色（在 `outputs.<ROLE>` 中）。
- `KELLY / TAKER` = **确定性服务端闸门**（非 LLM）。研究运行中 `KELLY = computeFractionalKelly(null)` → **恒为 BLOCKED**；`TAKER` 基于被 BLOCK 的 KELLY → 同样 BLOCKED。这是设计使然：**只有经服务端校验的回测/OOS 指标才能解锁 Kelly**。前端必须如实展示，不得伪造“已放行”。
- `safety.autoExecution === false` 永远成立；TAKER 提案 `requiresManualConfirmation: true`，**绝不绕过人工确认**。

**无**策略库存储端点：页面 04（策略竞赛）与 06（AI 团队）以研究运行为唯一真实数据源；Backtest/OOS 指标若无服务端校验来源，必须标注“用户自提供、未服务端校验”，**不得冒充真实放行**。

## 4. 行情数据来源（诚实标注）

Night Researcher **没有** candles/orderbook 代理端点。其 paper 引擎自身从 `https://data-api.binance.vision/api/v3` 取 depth/ticker/exchangeInfo。因此前端行情走**同一权威公开源**：

- 报价：`/ticker/price?symbols=[...]`
- K 线：`/klines?symbol=&interval=&limit=`
- 盘口：`/depth?symbol=&limit=`
- 过滤器：`/exchangeInfo?symbol=`

`services/marketData.ts` 封装上述调用，并在 UI 中标注来源（`Binance public REST ...`）。**这不是 Mock，是引擎使用的同一上游；绝不伪造资金流或盘口。**

## 5. Mock 模式

`services/paperApi.ts` 导出 `USE_MOCK`（默认 `true`）。Mock 客户端用 `mockData.ts` 合成**现货-only、无杠杆**的数据，字段形状与真实信封一致。翻转为 `false` 即绑定真实 Night Researcher Paper API，**页面无需改动**。

## 6. 部署须知（真实集成阶段）

`/api/paper/*` 与 `/api/agents/*` 的写操作强制**同源**（`isSameOrigin`）。因此 JEV 必须通过网关或开发代理将这些路径以**同源**方式转发到 Night Researcher（且转发时 Origin 须对齐目标源）。本地开发可在 `vite.config.ts` 的 `server.proxy` 中配置。Mock 模式下不需要后端。

## 7. 验收对照（V4.0 第十条）

- [x] 6 页面可作为 JEV 原生路由运行（`App.tsx` / `navigation.ts` 已存在）
- [x] Provider Center 复用（研究调用仅传 providerId，密钥走 ProviderRegistry/DPAPI）
- [ ] Trading API 已接入真实 Schema（本文件为契约；`USE_MOCK=false` 切换无页面改动）
- [x] 账户/订单/持仓服务端权威（不落 localStorage 为权威源）
- [x] 3D 关系可追溯（**页面 03 · 3D 市场宇宙** 已建成：相关性由实时 K 线收益序列计算，连线明确标注为相关性而非资金流向；无装饰粒子、无假资金流；WebGL2 不可用时回退 2D 相关性矩阵表）
- [x] 6 角色可审计（页面 06，基于研究运行 outputs）
- [x] 策略 OOS 闸门（页面 04，KELLY/TAKER 确定性闸门如实展示 BLOCKED）
- [ ] E2E 通过（Playwright）
- [ ] 部署预览（生产发布保持人工闸门）

## 8. V4.0 视觉系统（机构级 · 已落地）

- 设计令牌作用域化：在 `src/index.css` 新增 `.jev-trading` 作用域，仅对交易子系统重新声明令牌——深海军蓝近黑背景、深灰蓝分层面板、冷青强调色、盈利绿 / 亏损红 / 风险琥珀。**JEV 全局令牌与既有模块零改动**。
- 交易页面经 `components/PageContainer` 接入 `jev-trading` 类；3D 页面根节点加 `jev-trading is-fullbleed`。
- Tailwind 颜色令牌补 `<alpha-value>`，使 `bg-status-danger/10` 等透明度修饰符在全站（含既有交易页）真正生效。
- 交易导航采用 V4.0 六页面命名：01 指挥中心 / 02 交易工作站 / 03 市场宇宙 / 04 策略竞技场 / 05 模拟账户 / 06 专家团队（原 trading-audit 作为补充审计项保留）。
- 真实集成准备：`vite.config.ts` 的 `server.proxy` / `preview.proxy` 已配置 `/api/paper`、`/api/agents` 同源转发（由 `VITE_API_PROXY_TARGET` 驱动，默认 8787；Mock 模式下不产生 `/api/*` 请求，故无影响）。

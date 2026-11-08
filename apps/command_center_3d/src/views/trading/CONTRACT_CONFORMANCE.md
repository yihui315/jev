# API 契约一致性核验（CONTRACT CONFORMANCE）

> 本文档逐字段比对 `src/views/trading/services/*` 与 Night Researcher 真实后端源码。
> 核验日期：2026-11-05。源码位置：`D:/数据分析/night_researcher/functions/_lib/*.mjs` 与 `functions/api/*`。
> **核验方式：读取真实后端实现（非推断、非伪造）。** 真实接口 E2E 因后端可启动性问题暂未跑通，见末尾「阻塞项」。

## 1. 真实路由表（与伪造的 `/api/v1/trading` 无关）

| 前端调用 | 真实路由 | 处理函数 | 源码 |
| --- | --- | --- | --- |
| `ensureSession()` | `POST /api/paper/session` | `handlePaperSession` | `paper-ledger.mjs:111+` / `functions/api/paper/session.js` |
| `getAccount()` | `GET /api/paper/account` | `handlePaperAccount` | `paper-server.mjs:706` |
| `createOrder()` | `POST /api/paper/orders` | `handlePaperOrders` | `paper-server.mjs:717` |
| `cancelOrder()` | `POST /api/paper/orders/:id/cancel` | `handlePaperCancelOrder` | `paper-server.mjs:931+` |
| `resetAccount()` | `POST /api/paper/account/reset` | `handlePaperAccountReset` | `paper-server.mjs` |
| `getServerEvents()` | `GET /api/paper/events?after=&limit=` | `handlePaperServerEvents` | `paper-server.mjs:1004` |
| `createResearchRun()` | `POST /api/agents/research` | `handleAgentResearch` | `agent-orchestrator.mjs:208` |
| 账本/回放 | `GET/POST /api/paper/ledger`、`GET /api/paper/replay` | `handlePaperLedger`/`handlePaperReplay` | `paper-ledger.mjs` / `paper-server.mjs:1027` |

会话认证：`nr_paper_session` Cookie（HMAC 签名，`PAPER_LEDGER_SECRET`），前端 `credentials:"same-origin"` 自动携带。

## 2. 账户响应（`AccountResponse` ↔ `paper-server.mjs:703`）

```ts
// 服务端 accountResponse() 真实返回：
{ ok: true, mode: "server-authoritative-paper", account, marks, summary, markQuality, version, stateHash }
```
- `markQuality`: `"verified" | "unknown"`（`paper-server.mjs:696/699`）✅ 与前端枚举一致。
- `AccountSummary`（服务端 `summarize()`，`paper-server.mjs:175-188`）字段逐一对应：
  `equity, cash, availableCash, reservedCash, marketValue, unrealizedPnl, realizedPnl, totalPnl, dailyPnl, drawdown, exposurePct, positions[], dailyLossLocked, drawdownLocked` ✅
- `account.positions: Record<symbol,{qty,avgPrice,reservedQty}>`（现货，无杠杆/无多空）✅ 与 `RawPosition` 一致。

## 3. 下单（`OrderProposal` / `CreateOrderResponse` ↔ `paper-server.mjs:401-420, 806-813`）

**服务端白名单字段（`validateInput`，行 402）与前端完全一致：**
`clientOrderId, confirmationId, symbol, side, type, quantity, limitPrice`
> 服务端明确拒绝任何额外字段（行 403："`订单请求包含不允许的字段；盘口、账户和成交数据必须由服务端读取`"）。前端 `RealPaperApiClient.createOrder` 仅转发这 7 个字段 ✅。

- `ALLOWED_SYMBOLS` = `BTCUSDT | ETHUSDT | ORDIUSDT | 1000SATSUSDT`（`paper-server.mjs:7`）✅ 与 `types.Symbol` 一致。
- `clientOrderId` / `confirmationId` 正则：`^[a-zA-Z0-9._:-]{8,100}$`（行 406-407）✅ 前端生成逻辑需满足；`OrderProposal` 注释已标注。
- `POST /orders` 成功（201）真实返回（行 806-813）：
  `{ ...accountResponse, order, fills, duplicate: false, accepted: order.status!=="REJECTED", rejection: <{code,reason}|null> }`
  被风控拒绝时返回 **409**，`rejection = { code, reason }` ✅ 与 `CreateOrderResponse` 一致。
- 幂等：同一 `clientOrderId` 命中 → 返回 `{...accountResponse, duplicate: true, order, orderId, orderStatus, accepted, rejection}`（行 739-744）✅ `CreateOrderResponse` 已含可选 `orderId/orderStatus`。

## 4. 撤单（`CancelOrderResponse` ↔ `paper-server.mjs:946-963`）

真实返回：`{ ...accountResponse, status: "CANCELED" | "NOT_OPEN" | "NOT_FOUND", order }`；
订单不存在 → `404 { ok:false, code:"ORDER_NOT_FOUND", status:"NOT_FOUND" }`。
前端 `RealPaperApiClient.cancelOrder` 在 `!res.ok` 时抛 `PaperApiError`，不在成功分支解析 ✅。

## 5. 六角色研究（`ResearchRunResult` ↔ `agent-orchestrator.mjs:208-391`）

- 输入：`{ snapshot, provider, model, runId }`（行 226-229）；`snapshot` ≤ 12000 字符（行 227），否则 400。
  前端 `createResearchRun` 仅传这四个字段，`researchRunId` 不转发 ✅。
- 输出（行 376-391）：`{ ok, runId, snapshotHash, promptVersion, provider, requestedModel, startedAt, completedAt, durationMs, runMode:"research-only", roles:[6], outputs, usage, safety }` ✅ 与 `ResearchRunResult` 一致。
- `safety.autoExecution` 恒为 `false`（行 361）；`liveTrading:false`、`paperOrderCreated:false`（行 362-363）✅ 前端 `AgentCenter` 已显式警示，TAKER 不得绕过服务端风控。
- **KELLY / TAKER 在纯研究模式下确定性 BLOCKED**：`outputs.KELLY = computeFractionalKelly(null)`（行 344），`outputs.TAKER = buildTakerPlan(...)`（行 345）。这与前端 mock（KELLY/TAKER BLOCKED）行为一致 ✅。

## 6. 服务端事件（`ServerEvent` / `ServerEventsResponse` ↔ `paper-server.mjs:1004-1021`）

真实返回（行 1016-1021）：
```ts
{ ok: true,
  events: [{ seq, eventId, commandId, eventType, schemaVersion, payload, createdAt }],
  nextAfter: events.length ? events[events.length-1].seq : after }
```
✅ 与 `ServerEvent` / `ServerEventsResponse` 完全一致（含 `commandId` / `schemaVersion`）。
事件类型见 `types.ServerEventType`（`paper.server.account.*` / `paper.server.order.*`）。

## 7. 行情数据源（诚实、权威、同源）

Night Researcher 引擎自身也读取 `https://data-api.binance.vision/api/v3`（`paper-server.mjs:241-270`：`depth / exchangeInfo / ticker/price / klines`）。
前端 `marketData.ts` 使用**同一公开源**直连（浏览器侧），并在 UI 标注来源：
`ticker/price`（价格）、`klines`（OHLCV）、`depth`（盘口）。**绝不伪造行情**。
> 已现场验证：curl 命中真实端点返回 BTCUSDT `lastPrice=80691.02`、`+4.80%`（见 `marketData.live.test.ts` 的 LIVE_MARKET=1 用例）。

## 8. 服务端风控（前端仅做预检，服务端为权威）

服务端在 `validateAgainstMarket`（`paper-server.mjs:422-452`）强制：
单笔 ≤ 净值 10%（`maxOrderPct`）、总敞口 ≤ 净值 40%（`maxExposurePct`）、日亏熔断 / 回撤熔断暂停买入、可用 USDT 校验、现货不支持裸卖空。
前端 `lib/finance.ts` 仅做允许标的 / 正数量 / 限价单价的轻量预检，所有风控以服务端为准（不重复、不绕过）。

## 9. 阻塞项（真实接口 E2E 未跑通的原因 — 诚实记录）

1. **Wrangler 绑定名不匹配（后端配置 Bug）：** 代码读取 `env.PAPER_DB`（`paper-ledger.mjs:89,102` 等多处），但 `night_researcher/wrangler.toml` 声明 `binding = "DB"`。本地/测试 `wrangler dev` 会因 `env.PAPER_DB` 为 undefined 而 503。需将绑定名改为 `PAPER_DB`（或在 `[env.*]` / `[vars]` 映射）。**属后端/基础设施修复，前端不越权修改 night_researcher 仓库。**
2. **`PAPER_LEDGER_SECRET` 缺失：** 签名密钥要求 ≥32 字符字符串（`paper-ledger.mjs:92`），当前 dev 配置无 `.dev.vars` / secret。需由后端提供。
3. **`pages_build_output_dir = "dist"` 当前不存在：** `wrangler dev`（Pages）需要 `dist`；需先 `npm run build` 生成，或使用 `--assets` 指定。
4. **策略 / 历史研究缺口（功能缺口，非 Bug）：** 后端**无独立 K 线代理**（前端走 Binance 直连，已诚实处理）；**无历史研究运行 GET 接口**（仅有状态的 `POST /agents/research`，结果不持久化查询）；**无策略存储 / 版本表**。因此 V4.1 的「策略回测 / OOS / 历史研究审计」在真实后端侧尚缺数据底座，前端以 `server-events` 代用审计，并明确标注 OOS 闸门待后端指标接入。

## 10. 结论

- **契约层：前端 `types.ts` / `paperApi.ts` 与真实后端逐项一致，已核验（0 偏差）。**
- **行情层：Binance 公开源真实可达并已验证。**
- **账户/订单/研究/事件 E2E：契约已对齐，但受上述后端可启动性阻塞，尚未跑通真实 HTTP E2E —— 不冒充通过。**

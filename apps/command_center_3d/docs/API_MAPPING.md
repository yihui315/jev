# JEV Trading Desk V1.0 — 统一后端适配层接口映射

> TL;DR
> - 所有前端页面**只**通过 `/api/jev/*` 统一适配层取数；没有任何页面使用静态 JSON 或直连引擎。
> - 适配层（`server/trading_api.mjs`，`127.0.0.1:4791`）是冻结权威 Paper 引擎（原地 import，**未修改**）与已核验公开行情 REST 主机的薄封装。
> - 每个响应都携带溯源元数据 `meta.{source, channel, label, generated_utc, http_status}`，前端据此诚实标注 `LIVE / BACKFILL / BLOCKED / NO_DATA / UNAVAILABLE / VERIFIED`。
> - WebSocket 在本系统内 **BLOCKED**，行情通道恒为 `REST_POLLING`，绝不伪装成 WS LIVE。

---

## [1] 适配层契约（不可绕过）

| 项 | 值 |
|---|---|
| 入口文件 | `apps/command_center_3d/server/trading_api.mjs` |
| 监听 | `127.0.0.1:4791`（仅本机） |
| 上游引擎 | `D:/数据分析/.workbuddy/v43-worktrees/nr-trusted/functions/_lib/{paper-server,paper-ledger}.mjs`（原地 import，`engine_modified=false`） |
| 市场主机 | `data-api.binance.vision`（公开 REST，**非 WebSocket**） |
| 会话 Cookie | `nr_paper_session`，`HttpOnly; Secure; SameSite=Strict; Path=/api/` |
| 行情通道 | `REST_POLLING` |
| 禁止项 | 无 Provider 密钥 / 无真实交易所订单 / 无真实资金 / 无伪造 AI 推断·策略收益·OOS·成交 / 无第二套账本 |

前端基址常量：`API_BASE = "/api/jev"`（`src/trading/api/client.ts`）。
**修复记录**：早期 `API_BASE + "market/klines"` 拼接出 `/api/jevmarket/klines`（缺斜杠）导致全部路由 `UNKNOWN_ROUTE → NOT_FOUND`；已在 `jevFetch` 内做 base/rel 归一化，保证恰好一个 `/`。

---

## [2] 路由总表（前端 → 适配层 → 真实来源）

来源标签说明：
- `LIVE` = 实时真实数据（行情为公开 REST 实时；账户/订单为服务端权威引擎实时态）
- `BACKFILL` = 回填/历史证据数据
- `BLOCKED` = 硬约束阻断，无数据
- `NO_DATA` = 接口可用但当前无真实样本（如 Equity 快照为空、策略 OOS）
- `VERIFIED` = 本地只读证据/巡检结论

| # | 前端调用路径 | 方法 | 适配层处理 | 真实来源 | 标签 |
|---|---|---|---|---|---|
| 1 | `system/status` | GET | `systemStatus()`（读 `runtime/gate_runs` 目录） | 本地只读 | VERIFIED |
| 2 | `providers/status` | GET | `providersStatus()` | 本地只读 | **BLOCKED** |
| 3 | `agents/status` | GET | `agentsStatus()` | 本地只读 | **BLOCKED** |
| 4 | `strategies/status` | GET | `strategiesStatus()` | 本地只读 | **NO_DATA** |
| 5 | `datasource/audit` | GET | `dataSourceAudit()`（V4.6 证据 JSON） | 本地只读 | VERIFIED |
| 6 | `market/quality` | GET | `evidence(v46_market_quality_latest.json)` | 本地回填证据 | BACKFILL |
| 7 | `reference/design` | GET | `referenceDesign()`（6 张参考图设计 token） | 本地只读 | VERIFIED |
| 8 | `market/ticker?symbols=[...]` | GET | 转发 `ticker/24hr` | Binance Vision REST | LIVE |
| 9 | `market/klines?symbol&interval&limit` | GET | 转发 `klines`（limit 10–1000） | Binance Vision REST | LIVE |
| 10 | `market/depth?symbol&limit` | GET | 转发 `depth`（limit∈{5,10,20,50,100,500,1000}） | Binance Vision REST | LIVE |
| 11 | `market/trades?symbol&limit` | GET | 转发 `trades`（limit 1–1000） | Binance Vision REST | LIVE |
| 12 | `market/exchangeInfo?symbol` | GET | 转发 `exchangeInfo` | Binance Vision REST | LIVE |
| 13 | `paper/audit` | GET | `paperAudit()`（SQLite 投影） | 权威引擎 SQLite（只读） | LIVE |
| 14 | `paper/equity` | GET | 查 `sqlite_master`；表缺失/空 → 空数组 `equity_curve_synthesized:false` | 权威引擎 SQLite | LIVE / **NO_DATA** |
| 15 | `paper/orders` | GET | `handlePaperAccount` 投影 orders/trades/version/stateHash | 权威引擎 | LIVE |
| 16 | `paper/session` | POST | `ledger.handlePaperSession`（签发 HttpOnly cookie） | 权威引擎 | LIVE |
| 17 | `paper/account` | GET/POST | `engine.handlePaperAccount` | 权威引擎 | LIVE |
| 18 | `paper/orders` | POST | `engine.handlePaperOrders`（**真实撮合**） | 权威引擎 | LIVE |
| 19 | `paper/events` | GET | `engine.handlePaperServerEvents` | 权威引擎 | LIVE |
| 20 | `paper/replay` | GET | `engine.handlePaperReplay` | 权威引擎 | LIVE |
| 21 | `paper/market/tick` | GET | `engine.handlePaperMarketTick` | 权威引擎 | LIVE |
| 22 | `paper/orders/{id}/cancel` | POST | `engine.handlePaperOrderCancel` | 权威引擎 | LIVE |
| — | 其它任意路径 | — | 返回 `{ok:false, error:"NOT_FOUND", code:"UNKNOWN_ROUTE"}` 404 | — | — |

> 表 8–12 的 `market/*` 走 `fetchVerified()`（带哈希链/超时），失败时返回 `{ok:false, code:"MARKET_UNAVAILABLE", label:"UNAVAILABLE"}` 502，**不会**伪装成 LIVE。

---

## [3] 前端页面 → 接口依赖

| 页面 | 依赖接口（路径） | 关键诚实标注 |
|---|---|---|
| ① AI 交易指挥中心 | `system/status`, `datasource/audit`, `market/quality`, `reference/design`, `providers/status`, `agents/status`, `strategies/status`, `health` | 事实 Banner：14 笔成交来自确定性 Driver，非 AI 六角色；Provider/WebSocket 标 BLOCKED |
| ② 专业交易工作台 | `market/klines`, `market/depth`, `market/ticker`, `market/trades`, `paper/session`(POST), `paper/account`, `paper/orders`, `paper/events` | K 线/盘口/逐笔=真实 REST LIVE；下单仅经服务端引擎 |
| ③ 3D 市场宇宙 | `market/ticker`, `market/24hr`(经 ticker), `system/status` | 明确"态势感知非资金流向"；BNB/SOL 仅采集 |
| ④ 策略竞赛实验室 | `strategies/status`, `market/klines`(样本基础) | 诚实 NO_DATA：不展示绩效/排名/晋级 |
| ⑤ 模拟交易账户 | `paper/account`, `paper/orders`, `paper/events`, `paper/equity`, `paper/session`(POST) | 权益曲线仅在快照存在时显示，否则 NO_DATA；撤单按钮 |
| ⑥ AI 专家团队 | `agents/status`, `datasource/audit` | 六角色 workflow 全部 BLOCKED（provider/model/tokens/decision 未知） |

---

## [4] Vite 代理（dev / preview）

`vite.config.ts`：

| 前端前缀 | 目标 | 说明 |
|---|---|---|
| `/api/jev` | `http://127.0.0.1:4791` | 唯一后端适配层（本系统） |
| `/api/paper` | `http://127.0.0.1:8787`（NR dev，默认） | 既有 Night Researcher API（本系统不用） |
| `/api/agents` | `http://127.0.0.1:8787` | 既有 API（本系统不用） |

前端**永远**只请求 `/api/jev/*`，不触碰 `/api/paper`、`/api/agents`。

---

## [5] 硬约束红线（验收口径）

1. 行情通道恒为 `REST_POLLING`；`meta.websocket_note` 在审计抽屉中明确 "WebSocket BLOCKED → REST fallback"。
2. `paper/equity` 在 `paper_equity_snapshots` 表缺失/为空时返回 `equity_curve_synthesized:false` + 空数组，**绝不**生成合成曲线。
3. 引擎 `engine_modified=false`：适配层只 import、只路由，不重写撮合/记账/会话逻辑。
4. 下单字段白名单：`clientOrderId, confirmationId, symbol, side, type, quantity, limitPrice`；MARKET 不得带 `limitPrice`，LIMIT 必须带且 >0。
5. 允许交易标的：`BTCUSDT, ETHUSDT, ORDIUSDT, 1000SATSUSDT`；`BNBUSDT, SOLUSDT` 仅采集、不可交易。

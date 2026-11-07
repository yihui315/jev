# JEV Trading Desk V1.0 — 验收报告（Acceptance Report）

> TL;DR
> - 六页面全部通过真实数据渲染验收：**0 个 NOT_FOUND**，真实行情（Binance Vision REST）、真实账户/订单（服务端权威引擎）均落地。
> - 后端 22 条 `/api/jev/*` 路由实测：行情/行情质量/系统状态/审计/参考设计 200；Paper 生命周期（session→account→下单→撮合→撤单）端到端真实通过。
> - 修复 1 个阻断性缺陷：`API_BASE` 与路径拼接缺斜杠导致全部路由 `UNKNOWN_ROUTE → NOT_FOUND`（详见 §7）。
> - 验收工具：系统 Chrome 无头（`--headless=new --virtual-time-budget`）替代 Playwright（沙箱离线无法安装）。截图见 `e2e/artifacts/*.png`。
> - 明确 BLOCKED：AI Provider 推断、WebSocket、策略 OOS、BNB/SOL 交易、30 天 Day1/30、系统级调度、GitHub/Cloudflare 权限。

---

## [1] 验收环境与工具

| 项 | 值 |
|---|---|
| 适配层 | `127.0.0.1:4791`（`server/trading_api.mjs`） |
| 前端 | `127.0.0.1:4781`（`vite dev`，代理 `/api/jev` → 4791） |
| 入口 | `http://127.0.0.1:4781/trading.html#/<page>` |
| 浏览器验收 | `/c/Program Files/Google/Chrome/Application/chrome.exe --headless=new --no-sandbox --disable-gpu --virtual-time-budget=22000 --screenshot/--dump-dom` |
| 为何不用 Playwright | 沙箱离线，`pip install playwright` 超时、bundled Chromium 缺失；改用量产系统 Chrome 无头，等价覆盖"浏览/渲染/刷新/失败反馈"。 |

---

## [2] 后端路由验收（curl 经 Vite 代理 4781）

| 路径 | 结果 |
|---|---|
| `health` | 200 |
| `system/status` `datasource/audit` `market/quality` `reference/design` | 200 |
| `providers/status` `agents/status` `strategies/status` | 200（分别标 BLOCKED / BLOCKED / NO_DATA） |
| `market/klines`(limit=200) `market/depth` `market/ticker`(JSON symbols) `market/trades` `market/exchangeInfo` | 200（真实 Binance Vision 数据） |
| `paper/session`(POST) | 200，签发 HttpOnly+Secure+SameSite=Strict cookie |
| `paper/account` `paper/orders`(GET 投影) `paper/events` `paper/equity` `paper/audit` | 200 |

> `paper/account`、`paper/orders`(GET) 在未建会话时返回 401（符合冻结引擎契约）；前端自动 POST `paper/session` 建会话后正常。

---

## [3] Paper 引擎端到端一致性（真实撮合，非伪造）

步骤（经适配层，curl + cookie jar）：

1. **建会话** `POST /api/jev/paper/session` → `session:"created"`，cookie `nr_paper_session` 为 `HttpOnly`。
2. **下单** `POST /api/jev/paper/orders`：`MARKET BUY BTCUSDT 0.0002`。
3. **账户态**（下单后）：
   - `cash: 10000 → 9984.80445764`
   - `positions: { BTCUSDT: { qty:0.0002, avgPrice:75977.7118 } }`
   - `orders:1, trades:1`
   - `equity:9999.98481964, marketValue:15.180362, unrealizedPnl:-0.01518036, realizedPnl:0`
   - 订单 `status=FILLED, filledQty=0.0002, avgFillPrice=75901.81`
4. **订单投影** `GET /api/jev/paper/orders` 回显同一 `clientOrderId`（=下单提交值），账户与订单投影一致。

结论：现金扣减、持仓生成、成交记录、订单状态在**服务端权威引擎**内自洽，前端仅转发、不定价、不记账。

---

## [4] 前端六页面渲染验收（Chrome 无头 DOM dump）

| 页面 | 检查项 | 结果 |
|---|---|---|
| ① 指挥中心 | VERIFIED/BLOCKED 标签、30 天时钟(1/30)、工作线、数据审计、审计抽屉(WebSocket BLOCKED) | ✅ 渲染（截图 `01_command.png` / `07_audit_command.png`） |
| ② 工作台 | K 线/盘口/逐笔=真实 REST；账户 $10,000；**0 NOT_FOUND** | ✅ 渲染（截图 `02_workbench.png`，DOM 验证 48 行盘口真实价） |
| ③ 3D 宇宙 | Three.js 立方体、OrbitControls、"态势感知非资金流向" | ✅ 渲染（截图 `03_universe.png`） |
| ④ 策略实验室 | 诚实 NO_DATA，无绩效/排名 | ✅ 渲染（截图 `04_strategy.png`） |
| ⑤ 模拟账户 | 真实账户/持仓/委托/事件；权益曲线 NO_DATA（表空）；撤单 | ✅ 渲染（截图 `05_account.png`） |
| ⑥ 专家团队 | 六角色 workflow 全 BLOCKED | ✅ 渲染（截图 `06_team.png`） |

**NOT_FOUND 统计**：修复前六页面 DOM 关键字 `NOT_FOUND` 出现 6 次（每面板一次）；修复后 **0 次**。

---

## [5] 路由刷新恢复 + 审计深链

- Hash 路由（`#/command|workbench|universe|strategy|account|team`）：刷新后 `readRoute()` 从 `location.hash` 还原页面，无需重渲染首屏。
- 审计抽屉深链：`trading.html?audit=open#/command` 自动打开右侧审计抽屉（截图 `07_audit_command.png` 验证：列出 `market_rest=LIVE / market_websocket=BLOCKED / provider_inference=BLOCKED / strategy_oss=NO_DATA / supervisor=VERIFIED` 等合规红线）。

---

## [6] API 失败反馈（诚实路径）

- 适配层未知路由 → `{ok:false, error:"NOT_FOUND", code:"UNKNOWN_ROUTE"}` 404；行情上游失败 → `{ok:false, code:"MARKET_UNAVAILABLE", label:"UNAVAILABLE"}` 502。
- 前端 `StateView` 把上述错误如实质渲染为"无法从适配层获取数据 + 真实错误码"，**不**伪装成 LIVE/加载中。
- 表单下单被引擎拒绝时，提示"拒绝：<r.error|r.code>（服务端返回，非客户端）"。

---

## [7] 已修复缺陷

| 缺陷 | 现象 | 根因 | 修复 |
|---|---|---|---|
| **全部接口 NOT_FOUND** | 六页面所有面板 `UNKNOWN_ROUTE → NOT_FOUND`，0 真实数据 | `API_BASE="/api/jev"` 拼接无前导斜杠路径 `"market/klines"` → `/api/jevmarket/klines`（浏览器实际请求，curl 因带前导 `/` 而正常，掩盖缺陷） | `jevFetch` 内 `base/rel` 归一化，保证恰好一个 `/`（`client.ts`） |
| 适配层 `require("node:fs")` | 启动崩溃（ESM 无 `require`） | `systemStatus()` 误用 CommonJS | 改用已 import 的 `readdirSync` |
| `paperAudit` 未 await | `readSession` 可能 undefined | 异步未等待 | 改为 `async` + `await ledger.readSession` |
| `paper/equity` 表缺失 | `no such table: paper_equity_snapshots` | 冻结引擎前 3 迁移不含该表 | 查 `sqlite_master`，缺失返回 `table_present:false` + 空数组 + `equity_curve_synthesized:false` |
| Server 多 `Set-Cookie` 合并 | 会话 cookie 丢失 | `headers.forEach` 合并多值 | 改用 `getSetCookie()` 数组转发 |

---

## [8] 明确 BLOCKED / NO_DATA（不伪装）

| 项 | 状态 | 说明 |
|---|---|---|
| AI Provider 六角色推断 | BLOCKED | 0 凭据；前端全部标 BLOCKED，不编造决策 |
| WebSocket 实时行情 | BLOCKED | `ws → 404; stream.binance.com:9443 → timeout`；回退 REST_POLLING，绝不标 WS LIVE |
| 策略 OOS 收益/排名 | NO_DATA | 无真实样本；页面不展示绩效/晋级 |
| BNB/SOL 交易 | 仅采集 | 引擎白名单不含；页面标注"仅采集不可交易" |
| 30 天 Day 1/30 | 进行中 | 时钟显示 1/30，非完成 |
| 系统级调度 | BLOCKED | 环境权限 `0x80070005` |
| GitHub / Cloudflare 发布 | 未授权 | 不合并生产 PR、不发布 |

---

## [9] 验收结论

JEV Trading Desk V1.0 六页面在**统一后端适配层 `/api/jev/*`** 下，真实渲染行情与账户数据，Paper 下单端到端经服务端权威引擎真实撮合且自洽；所有来源诚实标注 LIVE/BACKFILL/BLOCKED/NO_DATA/UNAVAILABLE/VERIFIED，无 Mock 冒充、无静态 JSON 冒充实时、无伪造 AI/成交。修复阻断性路由缺陷后，六页面 NOT_FOUND 归零。BLOCKED 项均已显式声明，未越红线。

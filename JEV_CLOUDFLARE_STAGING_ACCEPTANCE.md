# JEV · Cloudflare Staging 验收报告（Stage 1 + Stage 2）

> 生成：2026-11-08 UTC｜分支 `feat/jev-cloudflare-staging`｜Stage 1 远端 HEAD `05d3ec8`｜Stage 2 代码提交 **`bb0eb10`**
> 最终部署：**`f2157f65`** → https://f2157f65.jev-cloud-staging.pages.dev ｜生产别名 https://jev-cloud-staging.pages.dev
> 红线：`ENABLE_REAL_TRADING=false`｜冻结引擎与 D1 账本零修改｜无真实资金/真实交易所下单｜无合成成交、AI 推断、30 天记录｜不晋级生产

---

## 0. TL;DR — 门禁结论

**NO-GO：PR #4 保持 Draft，不合并、不晋级生产。**

| # | 用户指令验收项 | 实测结论 | 判定 |
|---|---|---|---|
| [1] | 解决 Binance 403，优先测试 Cloudflare 合规可访问的真实行情路径 | Worker 边缘实测 20 个目标；`api.binance.us` 200 且 Binance v3 格式兼容；已产生真实成交 | **VERIFIED** ✅ |
| [2] | 保持冻结撮合引擎与 D1 账本不变 | `git diff -- server/engine/` 为空 + sha256 证据；仅改网关 | **VERIFIED** ✅ |
| [3] | 完成真实行情校验 | 引擎确切 URL 复放通过（depth/exchangeInfo/ticker） | **VERIFIED** ✅ |
| [4] | OPEN → CANCELLED | `OPEN` → `CANCELED`，`cancelledQty=0.01000000` | **VERIFIED** ✅ |
| [5] | FILLED → POSITION | 4 笔真实盘口成交 → `BTCUSDT 0.01 @76110.770605` | **VERIFIED** ✅ |
| [6] | 权益快照 | `paper_equity_snapshots` **0 行**，`equity_curve_synthesized:false` | **NO_DATA** ❌ |
| [7] | 风控拒绝 | 3/3 正确拒绝，伪造成交数 **0** | **VERIFIED** ✅ |
| [8] | 账户隔离 / 重启持久化 | counts 与明细均 session-scoped；旧 cookie 跨部署有效 | **VERIFIED** ✅ |
| [9] | Cloudflare 实际部署 CI | GitHub Actions 未在实跑中确认（需 5 个 Secrets） | **UNVERIFIED** ❌ |

按 Release Gate「任一 blocker → NO-GO」：[6][9] 未通过，**不晋级**。这与「测试全部通过前 PR #4 保持 Draft」的指令一致。

---

## 1. Stage 2 — Binance 403 根因与合规真实行情路径

### 1.1 真实 Worker 边缘出口探测（非沙箱推断）

新增只读端点 `GET /api/jev/market/probe`：20 个**硬编码**目标、需 Basic auth、无用户输入派生 URL（无 SSRF）、并行真实 HTTP、返回 status/latency/body 摘录。部署 `d4c02d37`（12 目标）与 `a36ac837`（+8 个引擎确切 URL）。

| 目标（Worker 边缘实测） | HTTP | 结论 |
|---|---|---|
| `data-api.binance.vision`（引擎硬编码） | **403** | 被封（原始症状） |
| `api.binance.com` / `api1-4.binance.com` | **451** | `Service unavailable from a restricted location` |
| `api.binance.vision` | 530 / CF 1016 | origin 解析失败 |
| `data.binance.com` | 403 | 被封 |
| **`api.binance.us`** | **200** | `{"symbol":"BTCUSDT","price":"75943.99..."}` — **Binance API v3 格式兼容** |
| Coinbase Exchange / Kraken | 200 | 格式不兼容，未采用 |
| CoinGecko | 429 | 限流，未采用 |

**引擎确切 URL 在可达 host 上复放**（`a36ac837`）：

| 引擎请求 | HTTP | 说明 |
|---|---|---|
| `ticker/price?symbols=[BTC,ETH,ORDI,1000SATS]`（原样） | **400** | `-1121 Invalid symbol.` |
| `BTCUSDT` / `ETHUSDT` 单标的 | 200 | 真实价 |
| `ORDIUSDT` / `1000SATSUSDT` | 400 | Binance.US 未上市 |
| `depth?symbol=BTCUSDT&limit=1000` | 200 | `lastUpdateId=3408934029`, bids 539 / asks 1000 |
| `exchangeInfo` BTC / ETH | 200 | `status=TRADING`，filters 完整 |

> 补充：沙箱出口访问 `api.binance.us` 全部 `000`（代理白名单缺失），而 Worker 边缘为 200 —— 因此**必须在 Worker 内测量**，沙箱结论不可外推。

### 1.2 解决方案：引擎原生 `env.__fetch` 注入（零改动冻结边界）

冻结引擎自带官方注入点（`paper-server.mjs:88`）：

```js
function dbFetch(env) {
  return typeof env.__fetch === "function" ? env.__fetch : fetch;
}
```

网关 `onRequest` 注入 `__fetch` → `marketFetcher`，仅做两件事：
1. 把 `data-api.binance.vision` 请求重指向 `api.binance.us`；
2. 把多标的 `ticker/price?symbols=[...]` 过滤为真实存在的 `BTCUSDT/ETHUSDT`。

**上游响应体原样透传**：不缓存、不合成价格、不重新定价、不缓存标记。任何上游失败照常冒泡给引擎，由引擎诚实拒单。这不是 monkey-patch 全局 `fetch`，是 per-request 的 `env` 注入。

**零改动证明**（`bb0eb10` 提交前）：

| 检查 | 结果 |
|---|---|
| `git diff --stat HEAD -- apps/command_center_3d/server/engine/` | **空**（10 个 `_lib` 文件 + 5 个迁移 + D1 账本全未改） |
| `paper-server.mjs` sha256 | `f9ac6e142446ef2aa8deb928f2b129eb2d42f4227d1bc22469191087312e4d74` |
| `paper-ledger.mjs` sha256 | `e7bd6e612b14e0ca7d5834a681ac87f482999c7c0e78e459eef42019a3446cc1` |
| `decimal.mjs` sha256 | `5b32d4bd9e03ed57f7cd2cf04f645ef53b9d44baa05d23f2e5a5a42ad8ae6e4d` |
| `health.engine_modified` | `false`；`real_money=false`；`market_fetch_injected=true` |
| 本地 gate `cf-pages-gate.check.mjs` | **6/6 PASS** |

### 1.3 诚实边界（不得含糊）

- **venue 不同**：`api.binance.us` 是 **Binance.US 现货**，不是 Binance.com 全球盘。`health` / `datasource/audit` / 文档均显式标注 `market_venue`、`engine_original_market_host`、`market_transport`。
- **原 403 未消失**：`dataSourceAudit` 保留 `market_rest_original_host = BLOCKED / http 403` 记录作为证据；`market_rest = VERIFIED` 仅指新路径。
- **标的缩水**：`ORDIUSDT` / `1000SATSUSDT` 订单被 `MARKET_HTTP_400` 拒绝，`fills=0`，**绝不伪造成交**。
- **未引入第二账本/行情系统**：仅传输层改写，无新定价、无新凭证、无爬虫/CAPTCHA 绕过。

---

## 2. Stage 2 — 完整闭环验收（最终构建 `f2157f65` 实测）

| # | 验收项 | 真实响应 | 判定 |
|---|---|---|---|
| [A] | 匿名拒绝 / 认证可用 | `health` 无 auth → **401**；带 Basic → **200** | ✅ |
| [B] | 真实行情 | `market/ticker` → `[BTCUSDT 76011.72, ETHUSDT 2240.97]`，`source_host=api.binance.us` | ✅ |
| [C] | **MARKET FILLED** | `status=FILLED`, `accepted=true`, **4 笔成交**：`76033.13 / 76033.27 / 76033.35 / 76037.32`（真实盘口档位） | ✅ |
| [D] | **FILLED → POSITION** | `positions={"BTCUSDT":{"qty":0.01,"avgPrice":76110.770605}}`；`trades=4` | ✅ |
| [E] | **OPEN → CANCELLED** | LIMIT `40000.00` → `OPEN`；cancel → `CANCELED`, `cancelledQty=0.01000000` | ✅ |
| [F] | 实时权益（真实 mark） | `summary.equity=9999.00949395`, `markQuality=verified`, `marks.BTCUSDT=76000.01` | ✅ |
| [G] | **风控拒绝 1** | 5 BTC 超单笔 10% 上限 → `REJECTED / 超过单笔风险上限（账户净值的 10%）` | ✅ |
| [H] | **风控拒绝 2** | `ORDIUSDT` → `REJECTED / MARKET_HTTP_400`，`fills=0`（诚实拒绝） | ✅ |
| [I] | **风控拒绝 3** | 非法 `clientOrderId="x1"` → `INVALID_ORDER / clientOrderId 无效或缺失` | ✅ |
| [J] | 事件链 | 15 条：`account.snapshot:6 / order.filled:4 / order.created:2 / order.rejected:2 / order.cancelled:1` | ✅ |
| [K] | 账户隔离 | ALPHA `counts={orders:4,fills:4,events:15}`；BETA `counts={orders:0,fills:0,events:0}`，明细 `orders=[]` | ✅ |
| [L] | 重启持久化 | `acc17563` 签发的 cookie 在 `f2157f65` 上仍 `session_valid=true`；D1 全局 `sessions:12 / accounts:8` 跨部署累计 | ✅ |
| [M] | **权益快照表** | `{"snapshots":[],"count":0,"table_present":true,"equity_curve_synthesized":false}` | **NO_DATA** ❌ |

> 注 [F]：请求体 `initialCash:"100000"` 被引擎忽略 —— 冻结引擎 `CFG.initialCash="10000"` 是常量（`paper-server.mjs:9`），故本金 10000、`equity≈9999`。这是既有引擎设计，未修改，如实记录。

> 注 [K]：本会话修复了隔离瑕疵 —— 修复前 `paper/audit` 的 `counts` 是全局表基数（BETA 能看到 `orders:7`）。现在 `counts` 全部 session-scoped，全局基数移入显式标注的 `admin_global_counts`（不含订单明细，需 Basic auth）。

---

## 3. 未通过项 / 阻塞（诚实披露，无伪造）

### 3.1 权益快照表 NO_DATA — **blocker** ❌
`paper_equity_snapshots` 0 行。根因：**冻结引擎不写该表**（`equity-snapshot.mjs` 是独立的 V45 collector 组件，未部署到 Cloudflare）。已存在的 `paper.server.account.snapshot` 事件（6 条）与实时 `summary.equity` 是引擎原生真实计算，**不得冒充权益快照表**。
按硬约束「无第二账本 / 无合成数据」，我**没有**在网关伪造快照写入。要关闭此项需独立评估部署 V45 collector（超出本阶段、且必须保持冻结引擎不变）。

### 3.2 Cloudflare 实际部署 CI 未确认 — **UNVERIFIED** ❌
`.github/workflows/jev-cloudflare-staging.yml` 已适配真实后端，但 GitHub Actions 未在实跑中确认；需要仓库 Secrets：`CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_API_TOKEN`、`JEV_PREVIEW_USER`、`JEV_PREVIEW_PASSWORD`、`JEV_PAPER_LEDGER_SECRET`。你独立核验到的 `Docker Runtime Gate = SUCCESS` 与本 Pages CI 不是同一条流水线。

### 3.3 venue 与标的限制
非 blocker，但必须持续披露：Binance.US ≠ 全球盘；仅 BTC/ETH 可交易；ORDI/1000SATS 拒单。若业务要求全球盘全覆盖，需要另一条合规接入路径（不得改冻结引擎）。

### 3.4 其他诚实状态端点（Stage 1 延续，仍未通过）
`providers/status=BLOCKED`（无真实 AI 推断）、`strategies/status=NO_DATA`（无真实 OOS 样本）、`thirty_day=UNVERIFIED`、`websocket=BLOCKED`。**0 真实发布 / 0 真实 AI 推断 / 0 条 30 天记录** —— 均未伪造。

### 3.5 主指挥台 `index.html` 不在范围
`src/lib/apiBase.ts` 指向 `http://127.0.0.1`（本地 Node 后端，未部署 Cloudflare）。本报告范围為六页面 Trading Desk（`trading.html`）。

---

## 4. 回滚证据

| 项 | 值 |
|---|---|
| 部署链（本阶段） | `5e275b6b`(Stage 1 prod) → `d4c02d37`(探测1) → `a36ac837`(探测2) → `acc17563`(行情修复+闭环) → **`f2157f65`(最终)** |
| 代码提交链 | `05d3ec8` → **`bb0eb10`**（Stage 2 代码）→ 本报告提交 |
| 回滚方式 [代码] | `git revert bb0eb10` 或从 `05d3ec8` 重新部署；不触碰 D1 |
| 回滚方式 [部署] | `wrangler pages deployment list --project-name jev-cloud-staging` 后按 Cloudflare 控制台回滚到指定部署；或 `wrangler pages deploy dist --project-name jev-cloud-staging --branch=...` 部署旧构建 |
| 数据 | D1 `jev-paper-staging` 未删除、未清空；回滚不影响账本（跨部署已验证持久） |
| 红线回滚 | `ENABLE_REAL_TRADING != false` 时网关首行 500 RED_LINE，无交易面 |

---

## 5. Stage 1 结论（保留，简表）

| 项 | 结论 |
|---|---|
| Cloudflare 连接 | `wrangler login --device` 已认证（`xall315@gmail.com`），CLI 4.158.0 |
| 资源 | Pages `jev-cloud-staging`；Function `functions/api/jev/[[path]].js` + `_middleware.js`；D1 `jev-paper-staging`（19 表 / 5 迁移） |
| 六页面 | `trading.html` → `trading-CRkNXX15.js`，`command/workbench/universe/strategy/account/team`，含预览登录注入 |
| 认证 | 中间件 + 网关双层 Basic；匿名 401 |
| 冻结引擎 | `server/engine/_lib/` 逐字节 vendored，sha256 一致 |
| PR | #4 Draft，HEAD `05d3ec8` → 本阶段新提交待推送；base `release/jcc-v1.3-final`，Release 分支未修改 |

---

## 6. 后续动作清单

| # | 动作 | 类型 | 负责 | 状态 |
|---|---|---|---|---|
| 1 | 宿主机 `git push -u origin feat/jev-cloudflare-staging`（本地已领先远端 2 个提交，快进无需 force） | **用户操作** | 你 | 待办 ⛔ |
| 2 | 保持 PR #4 为 Draft，不合并、不晋级 Release | **用户操作 / 门禁** | 你 | 进行中（NO-GO） |
| 3 | 配置 5 个 GitHub Actions Secrets 并实跑一次 Cloudflare 部署 CI，取得 UNVERIFIED → VERIFIED | **用户操作** | 你 | 待办 |
| 4 | 评估部署 V45 equity collector（关闭 §3.1 blocker）；不得改冻结引擎、不得合成快照 | **待决策** | 你拍板 | 待办 |
| 5 | 浏览器实测六页面登录与数据拉取 | **用户操作** | 你 | 待办 |
| 6 | 若业务要求全球盘/ORDI/1000SATS，另立合规行情接入方案 | **待决策** | 你拍板 | 待办 |

---

### 附录 A：可复现命令（凭据从 Pages 项目环境变量读取，勿写入代码）

```bash
BASE=https://f2157f65.jev-cloud-staging.pages.dev
AUTH="Basic $(printf 'jev_staging_admin:<JEV_PREVIEW_PASSWORD>' | base64 -w0)"

curl -i "$BASE/api/jev/health"                                   # 401 匿名
curl -H "Authorization: $AUTH" "$BASE/api/jev/health"            # 200 + 注入/venue 字段
curl -H "Authorization: $AUTH" "$BASE/api/jev/market/probe"      # 真实边缘可达性证据
curl -H "Authorization: $AUTH" "$BASE/api/jev/datasource/audit"  # VERIFIED + 原 403 记录
curl -H "Authorization: $AUTH" "$BASE/api/jev/system/status"     # 诚实 limits
# 下单 / 撤单（需先 POST /api/jev/paper/session 取签名 cookie）
```

### 附录 B：原始证据文件（本地，未入库）

`D:/cf-deploy/` 下：`probe.json`、`probe2.json`（边缘探测）、`e2e_market.json` / `e2e_limit.json` / `e2e_cancel.json` / `e2e_account.json` / `e2e_risk1.json` / `e2e_risk2.json` / `e2e_events.json`（`acc17563` 闭环）、`final_order.json` / `final_limit.json` / `f_alpha.json` / `f_beta.json`（`f2157f65` 最终复测）。
这些原始响应含会话标识，按「送外部判断前脱敏」原则**未提交进仓库**；上表为脱敏后的结论与数值。

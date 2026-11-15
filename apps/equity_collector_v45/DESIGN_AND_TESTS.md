# V45 Equity Collector — 设计、Schema 与测试报告

> 角色：P2 独立开发/测试（equity-builder）
> 日期：2026-11-09
> 独占目录：`D:/jev-v15-wt/v15-dev/`
> 结论标签约定：**VERIFIED**（本环境真实执行并验证）/ **UNVERIFIED**（未验证，不得写成 VERIFIED）/ **BLOCKED**（环境限制导致无法执行）

---

## 0. 环境与边界（VERIFIED）

| 项 | 值 |
| --- | --- |
| OS | Windows 11 Pro |
| Node | v22.22.2 (`node --version`) |
| Python | 3.13.14 |
| 代理 | `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:52498` |
| 本地 DB | `node:sqlite` 内存 D1 shim（**无任何云端 D1 连接**） |
| 云端写操作 | **零**（未创建/撤销任何 Token，未 git commit/push，未 wrangler deploy） |

**只读参考文件（未修改任何字节，T5 用 sha256 复核）**：
`D:/jev-cloud-staging/apps/command_center_3d/server/engine/_lib/equity-snapshot.mjs`、`decimal.mjs`、`migrations/0005_equity_snapshots.sql`、`functions/api/jev/[[path]].js`、`migrations/0001` / `0002`。
**禁止触碰**：`D:/jev`、`D:/jev-cloud-staging`、`D:/jev-v15-wt/feat`、`D:/jev-v15-wt/audit`、`.workbuddy` —— 全程只读，未写。

---

## 1. INPUT（任务与硬约束，摘要）

实现「V45 Equity Collector」独立 Scheduled Worker，为 `paper_equity_snapshots` 产生真实、可审计的权益快照；先在隔离测试环境完成全部验证，不得接入 `jev-paper-staging`、不得碰真实资金/生产账户。

硬约束逐条落实：
1. **冻结引擎零修改** —— `server/engine/_lib/**`、`paper-server.mjs`、migrations 字节不变；仅把 2 个纯模块**字节级复制**到 `src/vendor/` 复用（T5/T7 验证）。
2. **只读账户账本** —— Collector 只读 `paper_sessions` / `paper_accounts`，只写 `paper_equity_snapshots` + `v45_snapshot_provenance`（T5 写日志 spy 证明）。
3. **ENABLE_REAL_TRADING=false 语义** —— 红线检查，非 false 直接抛错零写入（T6）。
4. **行情失效一律 UNKNOWN** —— 抛错/503/缺标的/非 200/价格非正/STALE 全部 `mark_quality='unknown'` + `data_quality='UNKNOWN'`，**严禁虚构价格、严禁用上一次好价回填**（T3）。ORDIUSDT / 1000SATSUSDT 在 Binance.US 未上市，诚实 UNKNOWN。
5. **不覆盖 Night Researcher 生产 D1 / 不连 jev-paper-staging** —— 本地内存 SQLite shim。
6. **不打印/不落盘任何凭据或 Token** —— T7 静态审计 + wrangler example 无 secret（无任何凭据）。

---

## 2. 设计

### 2.1 目录与所有权（VERIFIED）

```
v15-dev/
├── schema.sql                  # 冻结兼容 snapshot 表 + 新 provenance 表 + 最小 ledger 表
├── wrangler.toml.example       # D1 binding = EQ_DB，明确「隔离测试库」禁用提示
├── DESIGN_AND_TESTS.md         # 本文
├── src/                        # 独立 Worker 源码（自包含，可部署）
│   ├── collector.mjs           # export default { scheduled, fetch } + runCycle()
│   ├── market.mjs              # api.binance.us 行情抓取 + provenance 原始数据
│   ├── compute.mjs             # 复用冻结纯函数计算权益 + 确定性 id
│   ├── provenance.mjs          # v45_snapshot_provenance 行构造 + INSERT OR IGNORE
│   ├── replay.mjs              # 纯函数历史回放（重算 peak/drawdown）
│   ├── hash.mjs                # 确定性 snapshot_id / engine_sha256 / hashRow
│   ├── constants.mjs           # 行情源 / 白名单 / 阈值 / 版本常量
│   ├── engine-anchor.mjs       # 冻结引擎 sha256 审计锚点
│   └── vendor/                 # 冻结纯模块字节级副本（只读复用，未编辑）
│       ├── decimal.mjs
│       └── equity-snapshot.mjs
├── tests/                      # 本地隔离测试（含 d1-shim / harness / run-all）
└── evidence/                   # 真实 stdout / stderr / exitCode / RESULT_JSON
```

### 2.2 Worker 入口（VERIFIED，T5）

- `scheduled(controller, env, ctx)`：`cycleKey = "cron:" + new Date(controller.scheduledTime).toISOString()`，`nowMs = scheduledTime`，调用 `runCycle()`。同一 cron tick 被重放时 `cycleKey` 相同 → 幂等。
- `fetch(request, env, ctx)`：受控测试入口。默认 `V45_TEST_TRIGGER !== "true"` → **403**（生产禁用）；路径必须为 `/__v45/collect`；从 query 读 `cycle_key` / `now_ms`。T5 实测 403 / 200 / 404。
- `runCycle(env, options)` 为共享核心，`db` / `fetcher` / `nowMs` / `cycleKey` 均可注入，便于确定性测试。

### 2.3 行情源与 fail-closed（VERIFIED，T0/T3/T7）

- **主源硬编码 `https://api.binance.us/api/v3`**（V1.4 在 CF edge 实测可达）。`data-api.binance.vision` **禁止作主源**：本会话 T0 实测它在本地返回真实 200 价格，但 (a) V1.4 记录其 CF edge WAF 403，(b) 它**错误地列出** ORDIUSDT/1000SATSUSDT（Binance.US 未上市），会污染 UNKNOWN 判定。
- **Venue 白名单** `VENUE_SUPPORTED_SYMBOLS = ["BTCUSDT","ETHUSDT"]`。持仓中任何 symbol 不在白名单 → 视为本 venue 无报价，绝不换 venue 回填。真实 WebFetch 探测：`ORDIUSDT` / `1000SATSUSDT` 在 api.binance.us 均返回 `{"code":-1121,"msg":"Invalid symbol."}`；含二者的多 symbol 查询整体失败 → 故必须过滤白名单后再查。
- 每次周期做 2 个只读请求：`/api/v3/time`（价格时间戳 + clock skew）与 `/api/v3/ticker/price?symbols=[白名单持仓]`。
- **失败判定**：fetch 抛错 / 非 2xx / 非 JSON / 价格 ≤ 0 / 缺标的 / `freshnessVerdict !== "FRESH"`（>60s STALE）→ `usable=false` → 喂给计算的 `marks={}` → 冻结引擎走 missing-mark 分支（equity=0、market_value=0、marks_json=`{}`），`mark_quality='unknown'`、`data_quality='UNKNOWN'`。**每次调用无状态，不缓存、不回填上一次好价**。
- **新鲜度**：`price_ts` 取 `/time` 的 serverTime（回退 `Date` 响应头，再回退本地时间）；`freshness_ms = |nowMs - priceTsMs|`；`clock_skew_ms = |nowMs - serverTime|`；`>60s => STALE`（复用 V1.4 `classifyFreshness` 语义）。STALE 按更严格的 fail-closed 解释也判 UNKNOWN。

> **诚实声明（UNVERIFIED / BLOCKED）**：本地代理对 `api.binance.us:443` 的 CONNECT 隧道超时（T0：http=0、curl exit=35），而代理本身存活（api.github.com=200）。因此**本进程无法真实 live 抓取主源**；生产 CF edge 的 live 抓取属 **UNVERIFIED**。T0 另通过 WebFetch（不同网络路径）真实探测到主源有效价格与 Invalid-symbol 事实，作为主源配置有效性的旁证。所有 T1–T6 的 VERIFIED 结论均基于**受控注入的 Binance 格式行情**（基准价 74951.77 / 2143.35 来自真实 api.binance.us 探测，cycle 间变化为明确标注的受控测试输入），**不声称是实时行情**。

### 2.4 确定性幂等（VERIFIED，T2）

```
snapshot_id = "v45eq_" + sha256(JSON({
  v:1, session_id, generation, cycle_key, account_version, state_hash }))
```
只含 5 个稳定字段，**不含价格 / marks / equity / 时间戳**（T7 静态验证）。两张表均 `INSERT OR IGNORE`（T7 验证实际生成 SQL 前缀）。重复运行同一 `cycle_key`：行不增、任何字段不变（T2 用 `hashRow` 逐次比对，并额外用「同 cycle 不同价格」证明不覆盖、不回填）。

### 2.5 权益计算（复用冻结纯函数，VERIFIED，T5）

`src/compute.mjs` 调用 `src/vendor/equity-snapshot.mjs` 的纯函数 `computeEquitySnapshot()` 与 `equityInsertStatement()`（**字节级副本**，T5 用 `Buffer.compare` 与冻结原件比对一致）。数值公式完全沿用冻结引擎的 1e8 定点（`decimal.mjs`）：`cash` / `market_value = Σ qty×mark` / `unrealized_pnl = Σ(value − qty×avgPrice)` / `equity = cash + market_value` / `realized_pnl`。

**明确不调用**冻结模块的 `fetchMarks()` / `collectEquitySnapshots()` / `buildEquityRiskBasis()`（它们含被禁 host 或非确定性写入）—— T7 静态审计验证 `src/` 可执行代码中无这些调用、无 `data-api.binance.vision` 可执行引用。行情完全由 `src/market.mjs` 提供。

### 2.6 峰值 / 回撤：按 (session_id, generation) 独立（VERIFIED，T4）

`historyPeak = MAX(equity) FROM paper_equity_snapshots WHERE session_id=? AND generation=? AND mark_quality='verified'` —— **仅 VERIFIED 快照**。
`peak = max(initialCash, historyPeak, equity)`；`drawdown = (peak − equity)/peak`（1e8 定点比率）。绝不跨账户求和或互相抵消。T4 用「盈利账户权益 421502.5 >> 亏损账户峰值 174951.77」证明：若错误地取全局 MAX/求和会明显抬高亏损账户峰值，实测亏损账户峰值保持独立不变。

### 2.7 v45_snapshot_provenance（新表，不改冻结表，VERIFIED）

每条快照一行，外键 `snapshot_id → paper_equity_snapshots`。字段覆盖需求：
`source_host`、`endpoint`、`source_url`、`http_status`、`payload_sha256`（原始响应 sha256）、`price_ts`、`fetched_at`、`freshness_ms`、`freshness_verdict`(FRESH/STALE/UNKNOWN)、`clock_skew_ms`、`data_quality`(VERIFIED/UNKNOWN)、`collector_version`、`cycle_key`、`engine_sha256`。
额外审计依据：`formula_version`、`input_state_hash`、`input_marks_json`、`initial_cash_fp`、`mark_quality`、`missing_marks_json`、`time_probe_ok`、`error_code`、`latency_ms`。

### 2.8 replay() 纯函数（VERIFIED，T1/T3/T5）

`replay(rows, {initialCashFp})`：按 `created_at`（`snapshot_id` 稳定 tiebreak）排序；`runningPeak` 以 initialCash 起；对每条 VERIFIED 行 `runningPeak = max(runningPeak, equity)`，`recomputedPeak = runningPeak`，`recomputedDd = (peak−equity)/peak`；对 UNKNOWN 行 `peak=0, dd=0` 且**不更新 runningPeak**。逐条返回 `stored vs recomputed` 与 `match`，汇总 `allMatch`。公式版本 `v45-equity-fp/1`。

**公式假设（VERIFIED，已标注）**：测试账户 `state_json` 不含 `equityPeak` 字段，故 `statePeak = initialCash`；若真实引擎写入 `account.equityPeak`，replay 的 seed 需相应取该值（属 UNVERIFIED 的对接细节，见 §8）。

### 2.9 有效 Session fail-closed（VERIFIED，T6）

```sql
SELECT a.* FROM paper_accounts a
JOIN paper_sessions s ON s.session_id = a.session_id
WHERE s.revoked_at IS NULL AND s.expires_at > ?   -- ? = nowIso
```
无匹配行 → 零账户 → **零快照写入、连行情请求都不发**（T6 记录 `fetcherCalls=0`）。过期 / 撤销 / 伪造 session_id / 空库均实测 0 写入。

### 2.10 红线与只读保证（VERIFIED，T5/T6/T7）

- `assertNoRealTrading()`：`ENABLE_REAL_TRADING` 非 false 语义 → 抛 `ENABLE_REAL_TRADING_MUST_BE_FALSE`。
- Collector 源码无任何 `INSERT/UPDATE/DELETE` 针对 `paper_accounts` / `paper_server_events` / `paper_orders` / `paper_fills`（T7 静态 + T5 写日志 spy）。
- T5：账户表前后整表 hash 一致；写日志仅含 `paper_equity_snapshots` 与 `v45_snapshot_provenance`（6 次写）。

---

## 3. Schema（`schema.sql`，VERIFIED）

- **`paper_equity_snapshots`**：与冻结 `0005_equity_snapshots.sql` **字段/类型/约束/索引逐项兼容**（`snapshot_id` PK、`session_id`、`generation`、`account_version`、`state_hash`、`equity`/`cash`/`market_value`/`unrealized_pnl`/`realized_pnl`/`equity_peak`/`drawdown` 1e8 定点 INTEGER、`mark_quality CHECK('verified','unknown')`、`marks_json`、`source`、`created_at`、FK→`paper_sessions`）。**未改动冻结表定义**。
- **`v45_snapshot_provenance`（新建）**：`snapshot_id` PK + FK，列见 §2.7；索引 `(session_id, cycle_key)`、`(data_quality, fetched_at)`。
- **最小 ledger 表**（仅测试夹具）：`paper_sessions`、`paper_accounts`、`paper_orders`、`paper_server_events`（与 0001/0002 兼容），仅用于 setup 与 T5 零写入 spy。
- **`wrangler.toml.example`**：`main = src/collector.mjs`、`nodejs_compat`、`[[d1_databases]] binding = "EQ_DB"`、`database_name = "jev-v45-equity-ISOLATED-TEST"`，并写明 **「DO NOT point this binding at jev-paper-staging / Night Researcher production D1」**、`ENABLE_REAL_TRADING = "false"`、`V45_TEST_TRIGGER = "false"`、`[triggers] crons = ["*/5 * * * *"]`。

---

## 4. 冻结引擎审计锚点（VERIFIED，T5 重新读盘复核）

| 文件 | sha256 |
| --- | --- |
| `_lib/decimal.mjs` | `5b32d4bd9e03ed57f7cd2cf04f645ef53b9d44baa05d23f2e5a5a42ad8ae6e4d` |
| `_lib/equity-snapshot.mjs` | `d1c784f8e1508db7ffb9f75f0ed87064f9072ce30e4a2e35d5dcc6698b513e74` |
| `_lib/paper-server.mjs` | `f9ac6e142446ef2aa8deb928f2b129eb2d42f4227d1bc22469191087312e4d74` |
| `migrations/0005_equity_snapshots.sql` | `7d3f5f48b9e9c7c06817e5b329865388df48eec394ae1be2e4a95c816c95bbaa` |

`engine_sha256 = sha256(canonical(ENGINE_ANCHORS)) = 8c1f93f84f6ba4e48a01e6ec9d1be7bba7e050f5d6a265851d5ad2d9f15e267f`，写入每条 provenance。T5 四个锚点全部 `match=true`，`src/vendor/*` 与冻结原件 `Buffer.compare=0`，证明冻结引擎字节未变、vendor 副本可信。

---

## 5. 测试矩阵与逐项结论

执行命令：`node tests/run-all.mjs`（runner 以子进程真实执行每个测试，捕获 stdout/stderr/exitCode，写入 evidence）。

| 组 | 对应任务要求 | 脚本 | exit | verdict | 断言 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |
| **T0** | 附加：真实网络/行情探测 + 主源有效性旁证 | `t0_market_probe.mjs` | 0 | **PASS** | 6/6 | `evidence/T0_market_probe.json` |
| **T1** | ① 多次采集 3+ cycle，权益/峰值/回撤随行情变化且可复算 | `t1_multi_cycle.mjs` | 0 | **PASS** | 14/14 | `evidence/T1_multi_cycle.json` |
| **T2** | ② 重复运行同一 cycle 连跑 3 次，行数不变、全字段 hash 一致 | `t2_idempotency.mjs` | 0 | **PASS** | 8/8 | `evidence/T2_idempotency.json` |
| **T3** | ③ 断线恢复：抛错/503/缺标的→UNKNOWN 无虚构价；恢复→VERIFIED 且峰值只用 VERIFIED | `t3_outage_recovery.mjs` | 0 | **PASS** | 11/11 | `evidence/T3_outage_recovery.json` |
| **T4** | ④ 账户隔离：两 session 一亏一盈，drawdown 独立、亏损账户峰值不被抬高 | `t4_account_isolation.mjs` | 0 | **PASS** | 14/14 | `evidence/T4_account_isolation.json` |
| **T5** | ⑤ 历史回放逐条一致 + 冻结引擎表零写入；另测 scheduled/fetch 入口、engine hash | `t5_replay_readonly.mjs` | 0 | **PASS** | 20/20 | `evidence/T5_replay_readonly.json` |
| **T6** | ⑥ 过期/伪造 session fail-closed 零写入（+ 真实交易红线） | `t6_session_failclosed.mjs` | 0 | **PASS** | 8/8 | `evidence/T6_session_failclosed.json` |
| **T7** | 附加：静态合规审计（无被禁源调用、无冻结表写、无凭据打印、幂等 SQL） | `t7_static_audit.mjs` | 0 | **PASS** | 14/14 | `evidence/T7_static_audit.json` |
| — | 汇总 | `evidence/summary.json` | 0 | **OVERALL=PASS** | **95/95** | `evidence/summary.json` |

**逐项 PASS/FAIL/BLOCKED**：主测试 ①–⑥ 全部 **PASS**（无 FAIL、无 BLOCKED）。T0/T7 附加证据 **PASS**。
**BLOCKED（环境级，非测试失败）**：本地代理 → `api.binance.us` 的 live 行情抓取（T0 记录 `http=0 / curl exit=35`）。该项使「生产 live 抓取」保持 **UNVERIFIED**，见 §7。

### 关键实测数据（摘自 evidence，均为 VERIFIED）

- **T1**（1e8 定点整数）：equity `[24990354000000, 25739871700000, 23491318600000, 21992283200000]`；drawdown `[0, 0, 8735681, 14559468]`（≈0% / 0% / 8.74% / 14.56%）；`replay.allMatch=true`。期望值用同一 decimal 原语独立复算，逐项相等。
- **T2**：3 次运行 `written = [1,0,0]`；`snapshot_id` 3 次相同 `v45eq_2abb0f88…`；`snap_hash` / `prov_hash` 3 次完全相同；第 4 次**改用半价**同 cycle 仍 `written=0`、hash 不变（无回填）。
- **T3**：`c1_throw`/`c2_503`/`c3_missing`/`c4_nonpositive` 全部 `eq=0, marks={}, data_quality=UNKNOWN, error_code 诚实`；`c2_503.http_status=503`；恢复 `c5_recover` 的 `equity_peak=17495177000000`（= 唯一 VERIFIED 的 c0 equity），`dd=2830363`，证明峰值只用 VERIFIED；ORDI/SATS 账户全程 `NO_VENUE_QUOTABLE_SYMBOLS` UNKNOWN。
- **T4**：亏损账户 peak 恒 `17495177000000`、dd `0→8568278→17136556`；盈利账户 equity 高达 `42150250000000`（远大于亏损账户峰值）但 dd 恒 0；两账户 peak 互不污染；各自 `replay.allMatch=true`。
- **T5**：4 个冻结引擎锚点 `match=true`；账户表 before/after hash 相同；写日志仅 2 张允许表（6 次写）；`replay.allMatch=true`；`scheduled` written=1；`fetch` 403(默认)/200(启用)/404。
- **T6**：空库/仅 session/过期/撤销/伪造 5 例均 `snap=0, prov=0, fetcherCalls=0`；`ENABLE_REAL_TRADING="true"` 抛 `ENABLE_REAL_TRADING_MUST_BE_FALSE` 且 0 写入；有效 session 对照写 1 条 VERIFIED。

---

## 6. 证据路径（VERIFIED）

```
D:/jev-v15-wt/v15-dev/evidence/
├── summary.json               # 总览 overall=PASS, 95/95
├── T0_market_probe.json       # 真实 curl 探测 stdout/stderr/exitCode + WebFetch 实测记录
├── T1_multi_cycle.json
├── T2_idempotency.json
├── T3_outage_recovery.json
├── T4_account_isolation.json
├── T5_replay_readonly.json
├── T6_session_failclosed.json
└── T7_static_audit.json
```
每个 JSON 含 `exitCode`、`verdict`、完整 `stdout`/`stderr`、`result`（逐条 `checks` + `extra` 明细）、`capturedAt`、`node` 版本。**无凭据/Token 内容**。

---

## 7. VERIFIED / UNVERIFIED / BLOCKED 结论清单

### VERIFIED（本地隔离环境，真实执行）
- 6 组主测试全部 PASS（95/95 断言），证据落盘、exitCode 真实。
- 确定性幂等、账户隔离、峰值仅用 VERIFIED、UNKNOWN 诚实无虚构/无回填、历史回放逐条一致。
- 冻结引擎 4 个文件字节未变、vendor 副本字节一致、Collector 对冻结账本表零写入。
- 有效 session fail-closed、`ENABLE_REAL_TRADING` 红线、scheduled/fetch 两个入口、provenance 字段完整性、engine_sha256 审计链。
- 静态合规：无被禁源可执行引用、无冻结表写、无凭据/日志打印、写仅 `INSERT OR IGNORE`、id 仅 5 个稳定字段。
- 主源 `api.binance.us` 配置有效性（通过 WebFetch 真实探测：BTC/ETH 200 有价、ORDI/SATS `Invalid symbol`、多 symbol 含未上市整体失败、`/time` 正常）。

### UNVERIFIED（明确未验证，**不得写成 VERIFIED**）
- **生产 Cloudflare Worker edge 对 `api.binance.us` 的真实 live 行情抓取**（本地代理不可达，T0 标 BLOCKED）。需主理人在部署后用 `/__v45/collect` 或 cron 实测 VERIFIED 快照的 `http_status=200` / `payload_sha256` / `price_ts`。
- **云端隔离 D1 的创建、`wrangler deploy`、cron 真实调度**：本任务禁止任何云端写操作，未执行。
- **与真实 `jev-paper-staging` 只读账户数据的端到端联调**：禁止连接，未执行；`paper_accounts.state_json` 的真实字段（尤其是否含 `equityPeak`）未在生产数据上核对。
- **真实 D1 与 `node:sqlite` shim 的语义差异**（`INSERT OR IGNORE`、并发、D1 `batch`/`run` 返回体）：本地 shim 已按 D1 形状实现，但真实 D1 未验证。
- **Binance.US 白名单的长期有效性**：若上市标的变更需更新 `VENUE_SUPPORTED_SYMBOLS`。

### BLOCKED（环境限制）
- 本地 `HTTP_PROXY` 到 `api.binance.us:443` 的 CONNECT 隧道超时（T0：`http=0`、`curl exit=35`），代理本身存活（api.github.com=200）。因此所有 T1–T6 的 VERIFIED 行情为**受控注入**（基准价真实、变化为标注的测试夹具），非实时 feed。

---

## 8. 风险（RISKS）

| # | 风险 | 影响 | 缓解 / 标签 |
| --- | --- | --- | --- |
| R1 | 本地无法 live 抓取主源，生产 live 未验证 | 部署后可能出现 `UNKNOWN`（若 edge 也不可达） | 主源已由 WebFetch 旁证有效；**部署后 owner 必须实测** → UNVERIFIED |
| R2 | 受控注入行情 vs 实时行情被误读 | 「随真实行情变化」被过度解读 | 全文/evidence 明确标注基准价真实、cycle 变化为受控测试输入；不声称实时 → VERIFIED(计算)/UNVERIFIED(live) |
| R3 | Binance.US 白名单硬编码 | 新上市标的被误判 UNKNOWN | fail-closed 偏保守（宁可 UNKNOWN 不虚构）；变更需更新常量 → 已知取舍 |
| R4 | STALE 采用更严格解释（也判 UNKNOWN） | 高峰值可能少计 | 与「严禁虚构/回填」一致；provenance 保留 `freshness_verdict=STALE` 可追溯 |
| R5 | replay 公式假设账户 `state_json` 无 `equityPeak` | 真实引擎若写入该字段，seed 需调整 | `formula_version` 已记录；对接真实数据时需核对 → UNVERIFIED |
| R6 | `src/vendor/equity-snapshot.mjs` 字节副本含被禁 host 字符串 | 审查疑虑 | 仅 import 纯函数 `computeEquitySnapshot`/`equityInsertStatement`；`fetchMarks`/`collectEquitySnapshots` 从未调用（T7 静态验证） |
| R7 | `node:sqlite` shim ≠ 真实 D1 | 部署后 SQL/并发行为差异 | schema 用标准 SQLite/D1 兼容语法；真实 D1 联调由 owner 执行 → UNVERIFIED |
| R8 | `clock_skew_ms` 依赖 `/api/v3/time` | time 探针失败时 `clock_skew_ms=null` | `time_probe_ok=0` 诚实标注，`price_ts` 回退 `Date` 头/本地时间 |
| R9 | 无真实账户/资金数据 | 数值为测试夹具 | 符合「不得碰真实资金/生产账户」约束；真实性由 owner 联调补齐 → UNVERIFIED |

---

## 9. DECISION 与 HANDOFF

**DECISION：GO WITH CONDITIONS**（本地隔离环境全部功能验证通过；生产 live 行情抓取与云端隔离 D1 部署为 UNVERIFIED，需主理人执行后方可进入生产）。

**HANDOFF — 下一步 owner（主理人）动作**：
1. 创建**云端隔离测试 D1**（`jev-v45-equity-ISOLATED-TEST`），填 `wrangler.toml.example` 的 `database_id`；**严禁指向 `jev-paper-staging` 或 Night Researcher 生产 D1**。
2. 用 `schema.sql` 初始化该隔离库（`paper_equity_snapshots` 与冻结 schema 兼容；若该库已由冻结迁移建表，仅需追加 `v45_snapshot_provenance`）。
3. 部署独立 Worker（`main=src/collector.mjs`，`EQ_DB` binding，`ENABLE_REAL_TRADING="false"`，生产保持 `V45_TEST_TRIGGER="false"`）。
4. 在 CF edge 实测 `api.binance.us` live 抓取：触发一次周期，确认 `mark_quality='verified'`、`data_quality='VERIFIED'`、`http_status=200`、`payload_sha256` 非空、`freshness_verdict=FRESH`；ORDI/SATS 仍为 UNKNOWN。→ 补齐 R1 / UNVERIFIED。
5. 用**只读**方式对接真实 `paper_accounts.state_json`，核对 `equityPeak` 字段是否存在（R5），并核对真实 D1 的 `INSERT OR IGNORE` 语义（R7）。
6. 生产启用前再次确认：Collector 对 `paper_accounts` / `paper_server_events` 零写入、无凭据落盘、`ENABLE_REAL_TRADING=false`。

**本角色未执行**：任何云端写操作、Token 创建/撤销、git commit/push、对冻结/禁止目录的任何修改。

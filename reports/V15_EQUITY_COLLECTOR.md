# JEV V1.5 · P2 — V45 Equity Collector 设计、隔离部署与 D1 校验

**结论：P2 = GO WITH CONDITIONS（隔离测试环境全部通过）；接入 `jev-paper-staging` = NO-GO（仅评估，不执行）。**
**所有快照均在隔离测试 D1 上产生；账户为明确标注的 TEST FIXTURE，不代表真实交易收益。**

## 1. 隔离边界（先隔离、后评估）

| 项 | 值 |
|---|---|
| 独立 Scheduled Worker | `jev-v45-equity-collector-isolated`（`https://jev-v45-equity-collector-isolated.xall315.workers.dev`） |
| 隔离测试 D1 | `jev-v45-equity-isolated-test` / `cf5cf033-3796-4de1-b295-93f35a8ce82e` |
| D1 binding | `EQ_DB`（`wrangler.isolated-test.toml`），**只指向上述隔离库** |
| 创建所用凭据 | P0 产生的**最小权限 Token**（5 权限组） |
| cron | `0 0 1 1 *`（保留 Scheduled Worker 契约；隔离测试期间用年度 tick 避免自动周期干扰确定性测试） |
| 测试写入入口 | 测试期 `V45_TEST_TRIGGER=true`；验收后 redeploy 为 `false`，复测 `/__v45/collect` → **403 TEST_TRIGGER_DISABLED** |
| `ENABLE_REAL_TRADING` | `"false"`（Worker var 强约束，`assertNoRealTrading()` 非 false 即抛 `RED_LINE`） |
| **未触碰** | `jev-paper-staging` D1（`55b690ec…`）、Night Researcher 生产 D1、真实资金/正式生产账户 |

隔离库由主理人用新 Token 创建（`POST /d1/database` 200），并在该库上实测
`CREATE TABLE / INSERT / SELECT` 3/3 http 200 success（`reports/evidence/equity/v15_isolated_d1.json`）。

## 2. 设计要点（对照需求逐条）

| 需求 | 实现 | 证据 |
|---|---|---|
| 关联有效 Paper Session 与账户版本 | `paper_accounts a JOIN paper_sessions s`，`revoked_at IS NULL AND expires_at > now`；快照带 `account_version`、`state_hash` | `src/collector.mjs` `runCycle()`；T6 无有效 session 零写入 |
| 服务器权威账本计算现金/市值/总权益 | **复用冻结引擎纯函数** `computeEquitySnapshot()`（`src/vendor/equity-snapshot.mjs` 为 `_lib/equity-snapshot.mjs` 字节副本），1e8 定点；`cash / market_value / unrealized_pnl / realized_pnl / equity` | `src/compute.mjs`；`deployed_source_sha256.json` |
| 价格来源、时间戳、新鲜度、数据质量 | `v45_snapshot_provenance`：`source_host`、`endpoint`、`source_url`、`http_status`、`payload_sha256`、`price_ts`、`fetched_at`、`freshness_ms`、`freshness_verdict`、`clock_skew_ms`、`data_quality`、`error_code`、`collector_version`、`engine_sha256`、`formula_version`、`input_state_hash` | `schema.sql`；D1 回读 `v15_equity_d1_replay.json` |
| 同周期幂等 | `snapshot_id = sha256(session_id, generation, cycle_key, account_version, state_hash)`（**不含价格/时间**）+ 两表 `INSERT OR IGNORE` | C1 连跑 3 次 `written=0` |
| 历史峰值 / 回撤 / 计算依据 | 按 **(session_id, generation)** 用 `MAX(equity) WHERE mark_quality='verified'`，现金底线 `initialCash`；`drawdown` 1e8 定点；`marks_json` + `input_state_hash` + `formula_version` 留痕 | D1 回放 12/12 逐条一致 |
| 行情失效标 UNKNOWN、不虚构价格 | 场所白名单仅 `BTCUSDT`/`ETHUSDT`；非 2xx / 超时 / 缺标的 / STALE → `marks={}`、`mark_quality='unknown'`、`data_quality='UNKNOWN'`、`equity=0`、禁止任何兜底或上次好价 | 云侧 3 行 UNKNOWN；T3 断线恢复 |
| 不改冻结引擎订单状态/回放链 | 只读 `paper_accounts`；`paper_server_events` / `paper_orders` / `paper_fills` 零写入；`state_hash` 不变 | D1 回读：accounts 4 行 state_hash 不变、events 0、orders 0 |
| 不覆盖 Night Researcher 生产 D1 | 只绑隔离库 | `v15_security_recheck.json`（Night Researcher 无 D1 绑定） |

**冻结引擎字节证明**：`server/engine/` 15 文件在 `ce8d570` / feat / audit / 集成 HEAD 四处
tree sha256 全为 `aec5d1b7cc26db50736d355e7ef14e066fae3f2c2a376d94637c9a977008a9ad`。
Collector 只 import/vendor 纯函数副本，**未编辑任何冻结文件**。

## 3. 隔离测试 D1 的夹具（诚实标注）

隔离库写入 4 个 session，ID 全部带 `v45-FIXTURE-` 前缀，仅用于验证计算与隔离逻辑：

| session | 持仓 | 用途 |
|---|---|---|
| `v45-FIXTURE-alpha` | 0.015 BTCUSDT @ 70000, cash 9000 | 真实 BTC 盘口估值 / VERIFIED |
| `v45-FIXTURE-beta` | 1.2 ETHUSDT @ 3000, cash 5000 | 真实 ETH 估值 / 回撤 |
| `v45-FIXTURE-ordi` | 20 ORDIUSDT | 未上市 → UNKNOWN / 禁止虚构价格 |
| `v45-FIXTURE-cash` | 无持仓, cash 10000 | 只需 `/api/v3/time` 的 VERIFIED 路径 |

**这些是测试夹具，不是真实交易记录，不得用于任何收益/绩效宣称。价格全部来自真实
`api.binance.us` HTTP 200 响应（带 payload_sha256）。**

## 4. 云端隔离部署实测（主理人执行）

部署命令产物：`Uploaded jev-v45-equity-collector-isolated`、`Deployed ... triggers`、
Version ID `8dacc193-e57d-4caf-b6c9-20096d3abd62`（测试期），收敛后 `35c43bb2-9cba-4c59-8798-55598ed9f24b`。
部署源码 10 个文件的 sha256 记录在 `apps/equity_collector_v45/deployed_source_sha256.json`。

### 4.1 多周期与重复运行（真实 HTTP 200）

| 调用 | `cycle_key` | http | `written` | VERIFIED | UNKNOWN | failures |
|---|---|---|---|---|---|---|
| 1 | `v45-cloud-C1` | 200 | **4** | 3 | 1 | 0 |
| 2–4 | `v45-cloud-C1`（重复 3 次） | 200 | **0 / 0 / 0** | 3 | 1 | 0 |
| 5 | `v45-cloud-C2` | 200 | 4 | 3 | 1 | 0 |
| 6 | `v45-cloud-C3` | 200 | 4 | 3 | 1 | 0 |

**幂等结论**：同一 `cycle_key` 重复运行不新增行、不改动已有字段；确定性 `snapshot_id` 生效。

### 4.2 真实行情与 UNKNOWN

- VERIFIED 行 6 条带 `http_status=200`、`source_url=https://api.binance.us/api/v3/ticker/price?symbols=...`、
  `payload_sha256=9135549106033cd12413d8b9aab726a8d9f98657d3ab63e17de2fe9fa4f49f42`（示例）、
  `freshness_verdict=FRESH`、`clock_skew_ms` 数百 ms（< 60000ms 阈值）。
- alpha C1：`cash=900000000000`、`market_value=112597260000`、`equity=1012597260000`（即 10125.9726），
  对应真实 BTC 价约 75064.84 USDT × 0.015。
- beta C1：`equity=757365200000`、`equity_peak=1000000000000`、`drawdown=24263480`
  （1e8 定点 → **24.26348%**），由真实 ETH 盘口价与夹具成本算出。
- UNKNOWN 行 3 条（ORDIUSDT）：`error_code=NO_VENUE_QUOTABLE_SYMBOLS`、
  `missing_marks_json=["ORDIUSDT"]`、`marks_json={}`、`equity=0`、`equity_peak=0`、`drawdown=0` —
  **没有虚构价格，没有用上次好价回填，且 UNKNOWN 不参与峰值**。

### 4.3 D1 权益快照与回撤校验

| 查询 | 结果 |
|---|---|
| `paper_equity_snapshots` 行数 | **12** |
| `v45_snapshot_provenance` 行数 | **12** |
| `mark_quality` 分布 | `verified` 9 / `unknown` 3 |
| 每 session | 3 行、3 个不同 snapshot_id |
| 每 cycle | 4 行、4 个不同 snapshot_id |
| 峰值 / 回撤回放 | **12/12 条 peak 逐条一致、12/12 条 drawdown 逐条一致 → PASS** |
| 账户隔离 | alpha peak `1012597260000` / dd 0；beta peak `1000000000000` / dd `24263480`；cash peak `1e12` / dd 0；ordi peak 0 / dd 0 — **亏损账户峰值未被盈利账户抬高，未跨账户抵消** |
| 冻结账本零写入 | `paper_accounts` 4 行且 `state_hash` 与夹具注入值一致；`paper_server_events` 0；`paper_orders` 0 |
| Staging D1 隔离 | `55b690ec…` 的 `paper_equity_snapshots` 仍 **0 行** |
| Night Researcher | production/preview 均无 D1 绑定 |

回放脚本与逐条结果：`reports/evidence/equity/v15_equity_d1_replay.json`；
周期响应与 D1 查询原始结果：`reports/evidence/equity/v15_cloud_collector_evidence.json`。

> 诚实限制：云端 3 个周期在 ~10 秒内完成，真实盘口价格在该窗口内未变化，
> 因此云端未展示「权益随行情变化」。该场景由本地注入真实响应的 T1 多周期测试覆盖
> （`apps/equity_collector_v45/DESIGN_AND_TESTS.md`）。云端证据的价值在于
> **真实 D1 写入 + 真实 HTTP 溯源 + 幂等 + 隔离 + 回放**。

## 5. 本地隔离测试（真实执行，8/8 PASS）

`node tests/run-all.mjs`（Node v22.22.2，`node:sqlite` 内存 D1 shim，**零云端连接**），
最终 `evidence/summary.json`：`overall=PASS`、`8/8`、`0 FAIL`，共 **95 条断言全通过**
（`2026-11-09T04:44:03Z`）。逐项：

| ID | 测试 | exit | 断言 | 结论 |
|---|---|---|---|---|
| T0 | 行情源探测（主源/禁用源/本地上下文） | 0 | 6/6 | PASS |
| T1 | 多次采集（权益随真实响应变化、峰值/回撤可复算） | 0 | 14/14 | PASS |
| T2 | 重复运行（同 cycle 连跑 3 次，行数与内容不变） | 0 | 8/8 | PASS |
| T3 | 断线恢复（fetch 抛错 / 503 / 缺标的 → UNKNOWN；恢复后 VERIFIED 且峰值只用 VERIFIED） | 0 | 11/11 | PASS |
| T4 | 账户隔离（一亏一盈，peak/drawdown 独立、不跨账户抵消） | 0 | 14/14 | PASS |
| T5 | 历史回放 + 只读证明（冻结文件 sha256 复核、accounts/events/orders 零写入） | 0 | 20/20 | PASS |
| T6 | 过期/伪造 session fail-closed（零快照写入） | 0 | 8/8 | PASS |
| T7 | 静态审计（禁用源、禁用函数调用、INSERT OR IGNORE、确定性 id、RED_LINE、无凭据） | 0 | 14/14 | PASS |

> T7 首次运行为 FAIL（4 项），根因是**检查器把注释文本与 vendor 冻结副本当成代码引用**。
> 按主理人要求以「修检查器、不删断言」处理：剥离注释后扫描、只匹配真实调用表达式、
> 用运行时 `snapshotInsertStatement()` 产出的 SQL 验证 `INSERT OR IGNORE`。
> **未删除或放宽任何断言，未把 FAIL 改写为 PASS**；复跑 `04:44:03Z` 8/8 PASS。

脚本：`apps/equity_collector_v45/tests/`；逐项证据：`reports/evidence/equity/local/`；
设计与完整测试矩阵：`apps/equity_collector_v45/DESIGN_AND_TESTS.md`。

## 6. 接入 `jev-paper-staging` 的评估（未执行）

| 条件 | 状态 |
|---|---|
| 隔离环境全部测试通过 | 见 §5 |
| 真实 Paper Session 与账户版本可关联 | 需把 `EQ_DB` 指向 `55b690ec…` 并验证 `paper_sessions`/`paper_accounts` 真实 schema 一致 |
| 生产 D1 写入授权 | **未申请、未执行** |
| 行情源在目标运行时 VERIFIED | 是（B 上下文） |
| ORDI / 1000SATS BLOCKED 保持 | 是 |
| 30 天真实研究样本 | 仍 0 |
| `ENABLE_REAL_TRADING=false` | 是 |

**决策：暂不接入。** 接入前需要独立评审、把 cron 与 `V45_TEST_TRIGGER` 固化为生产配置、
并确认 `paper_equity_snapshots` 从 0 行变为真实数据不会对现有风控门禁造成误判。
**未接入真实资金或正式生产账户。**

## 7. DECISION

**P2 = GO WITH CONDITIONS**（隔离测试环境通过；云端隔离 D1 已产生 12 条真实可审计快照、
回放 12/12 一致）。**接入现有 Staging = NO-GO / 待独立评审。**

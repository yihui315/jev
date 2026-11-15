# JEV V1.6 — 市场来源 / 价格单位 / Fail-Closed 设计说明

**状态**：工程师实现完成（本地隔离验证 VERIFIED）；云端 D1 / Worker / E2E 由主理人另行处理（UNVERIFIED）。
**基线**：`bfb320603367b2e8ec7b01f96864312f63ad2e6a`
**工作区**：`D:/jev-v16-wt/v16`，分支 `feature/v16-market-provenance-hardening`
**实现位置**：原地加固 `apps/equity_collector_v45/`（非新建平行 app）

---

## 1. 目标

V1.5 的 provenance 只回答「价格来自哪个 host」，无法回答「来自哪个市场 / 哪个价格单位 / 时间戳来源是否可信」。V1.6 通过 M1–M4 把市场身份、价格单位契约、强制 fail-closed 校验与分区 peak 查询固化进代码与 schema，核心目的是：**任何跨市场、单位不符、来源不可证明的报价都不得进入 VERIFIED 权益或回撤计算**。

本轮不接 Staging / 真实资金、不改冻结 `paper_equity_snapshots` DDL、不改 `server/engine/` 15 文件、不改 `src/hash.mjs` 的 `deterministicSnapshotId`（H2/H5）。

---

## 2. 架构分层

| 层 | 文件 | 职责 |
|---|---|---|
| M1 schema | `migrations/0001_v45_base.sql`、`0002_market_provenance.sql`、`schema.sql` | 版本化迁移，8 个新列 + CHECK + 分区键 + 索引；历史来源不可证明 → UNKNOWN |
| M2 契约 | `src/market-contract.mjs` | 市场 / 价格单位常量标签、`SYMBOL_CONTRACTS`、`buildMarkContracts()` |
| M3 守卫 | `src/market-guard.mjs` | 7 类 fail-closed 纯函数、错误码、`resolveNetworkContext()`、`classifyMarketFailure()`、`evaluateCycle()` |
| M3 分区 | `src/partition.mjs` | `selectHistoryPeak()` —— 只按 `(exchange,market)` 且来源可证明的行算 peak |
| M3 接线 | `src/market.mjs`、`src/provenance.mjs`、`src/collector.mjs`、`src/constants.mjs` | 显式 `priceTsSource`、8 列落库、`runCycle` 真实调用 guard、降级写 UNKNOWN 行、替换旧 peak 查询 |
| M4 测试 | `tests/v16/*` | 8 组独立证据 + `reports/evidence/v16/`；V1.5 旧 8 组逐字节不变重跑 |

---

## 3. 关键设计决策（含约束冲突的处理）

### 3.1 版本号双常量（H1 与「升级到 1.6.0」的冲突）
V1.5 的 `T5_replay_readonly` 逐字节冻结了断言 `p.collector_version === "v45-equity-collector/1.0.0"`（H1：实现必须迁就旧断言）。若把 `COLLECTOR_VERSION` 直接改成 1.6.0 并让 `buildProvenance` 引用它，T5 必然 FAIL。
**处理**：`constants.mjs` 同时导出
- `COLLECTOR_VERSION = "v45-equity-collector/1.6.0"`（运行时构建版本，出现在 `runCycle` 的 `summary.collectorVersion`，V1.6 测试断言它）；
- `PROVENANCE_COLLECTOR_VERSION = "v45-equity-collector/1.0.0"`（写入冻结 `v45_snapshot_provenance.collector_version` 列的 V1.5 审计标签，T5 依赖）。

两者刻意不合并：`collector_version` 列是 V1.5 冻结的审计 schema 标签，与运行时构建版本分离。**这是 H1 铁律下「实现迁就旧断言」的必然结果，不是伪造**——V1.5 的 95 条断言逐字节未动且全部重跑通过。

### 3.2 显式 `priceTsSource`（契约 §3.1）
`market.mjs` 把原三路兜底改为显式记录来源：`/time` 成功 → `exchange_serverTime`；HTTP `Date` 头解析成功 → `http_date`；其余 → `received_at`。`received_at` 由 guard 规则 3 拒绝，绝不产生 VERIFIED。V1.5 的 `controlledFetcher` 在正常路径都提供 `/time`，故旧断言不受影响。

### 3.3 确定性 `network_context`（契约 §3.2）
`resolveNetworkContext()`：`env.MARKET_NETWORK_CONTEXT` 显式值优先（生产边缘为 `cloudflare_pages_edge_runtime_prod`）；否则走注入 fetcher（`env.__fetch` / `options.fetcher`）→ `local_test_shim`；否则 `UNKNOWN`（fail-closed）。**不使用 `Date.now()` / 随机数 / 代理推断**，保证 `T2 all_fields_identical_across_3_runs` 与 V1.6 QA-T2-1 的整行 hash 恒定。

### 3.4 失败是「降级写 UNKNOWN 行」，不是抛异常跳过（契约 §3.3 / H8）
`runCycle` 对每个 session 真实调用 `evaluateCycle()`：
- VERIFIED 且 guard 全过 → 保持 V1.5 全部返回字段与行为，8 新列写真实身份（`exchange=Binance.US`、`market=spot`、`failure_class=NONE` 等）。
- 任一 guard 失败 或 市场层本就失败 → 降级为 `marks={}`、`markQuality='unknown'`、`dataQuality='UNKNOWN'`、`equity=0`，写 `errorCode=<具体码>`、`failureClass=<具体类>`，**仍写 provenance 行**保留全部身份字段与原始证据（`mark_contracts_json` 保留注入的逐 symbol 记录）。不伪造价格、不跳过审计、不吞错误。
- 市场层已失败的周期保留其诚实 `errorCode`（如 `MARKET_HTTP_503`），`failure_class` 由 `classifyMarketFailure()` 结构化（TRANSPORT / HTTP_ERROR / VENUE_BUSINESS / STALE / PAYLOAD_UNVERIFIED / UNKNOWN）；guard 主动拒绝的 VERIFIED 周期用 guard 的 `errorCode` / `failureClass`。这样 T3 的 `honest_error_codes` 断言逐字保持。

### 3.5 M2 价格单位契约
- 常量是**身份标签，不含主机名**：`EXCHANGE_US="Binance.US"`、`EXCHANGE_GLOBAL="Binance Global spot"`、`MARKET_SPOT="spot"`。
- `1000SATSUSDT`：`unit_multiplier=1000`、`base_asset="1000SATS"`、`price_unit="USDT per 1 (1000SATS) unit = 1000 SATS"`、`status=BLOCKED`，归属 Global（仅识别，绝不用于报价）。`ORDIUSDT` 在 US 下 `status=BLOCKED / NOT_LISTED_ON_BINANCE_US`。
- `buildMarkContracts({market, marks, networkContext})` 产出**以 symbol 为键的对象**（契约 §2），每个值带完整逐 symbol 记录（17 字段）。有价周期形如 `{"BTCUSDT": {...}}`，UNKNOWN 行为 `{}`，**绝不是 `[]`**（早期中间态曾误用数组，已修正）。内部用 `Object.create(null)` 构造，避免恶意 ticker symbol（如 `__proto__`）造成原型污染，`JSON.stringify` 仍输出普通 JSON 对象。落到 `mark_contracts_json` 列（不是子表）；冻结表列 `marks_json` 保持原纯价格 map 字符串格式（T1/T2 依赖）。QA-T1-1 新增断言显式校验「非数组对象 + 键为 symbol + 值含全部契约字段」。
- 1000SATS 与 SATSUSDT 不得并列；`unit_multiplier` 与契约不符 → `UNIT_CONTRACT_MISMATCH`。

### 3.6 M3 分区 peak（核心，契约 §3.4）
`src/partition.mjs:selectHistoryPeak()` 的 SQL 逐字采用契约给的语句：`JOIN provenance`，带 `p.exchange=? AND p.market=?` 分区，并排除 `HISTORY_UNPROVEN`（`failure_class='NONE'`）、`received_at` / `UNKNOWN` 时间戳源（`price_ts_source IN (...)`）、`UNKNOWN` 上下文（`network_context <> 'UNKNOWN'`）。`collector.mjs` 的旧 `SELECT MAX(equity) ... WHERE mark_quality='verified'` **已替换为该函数**。跨市场行、历史不可证明行都不参与 peak / drawdown。

### 3.7 `snapshot_id` 与 QA-M5（契约 §3.5）
`hash.mjs` 的 `deterministicSnapshotId` 代码块**未改动**（H5）。

> **QA-M5 由预防性拒绝满足，而非扩 ID；若未来审批跨市场组合估值模型，必须同步重做 ID 与分区。**

因为 V1.6 禁止第二市场进入序列（`MARKET_MIXING_REJECTED` + 分区 peak），`INSERT OR IGNORE` 不会被两个市场互吞；因此无需把 exchange/market 扩进 ID。若将来允许跨市场组合估值，这一前提不再成立，ID 与分区必须一起重做。

### 3.8 禁止字面量与静态合规（H4/H6）
`src/` 顶层可执行代码中不出现 `data-api.binance.vision` 字面量（仅 allowlist `api.binance.us`）；Global host 注入的反例字面量只存在于 `tests/v16/` 测试文件（T7 只扫描 `src/`）。`src/` 无 `console.*`、无凭据字面量；所有写入仍是 `INSERT OR IGNORE INTO`。T7 的 14 条静态断言全部重跑通过。

### 3.9 测试期受控故障注入入口（云端 E2E 负例，**不是生产功能**）

主理人需在全新隔离 Worker（`V45_TEST_TRIGGER="true"`，验收后 redeploy 为 false）上真实触发「断线恢复与数据不可用」「错误市场注入」三个负例。为此 `src/collector.mjs` 的 `fetch()` 入口支持一个**受控** `fault` query 参数：

| 参数 | 注入的 fetcher 行为 | 预期结果 |
|---|---|---|
| `/__v45/collect?cycle_key=...&fault=transport` | 任何 URL 都 `throw new TypeError("fetch failed (simulated outage)")` | `data_quality=UNKNOWN`、`failure_class=TRANSPORT`、仍写 provenance、equity=0 |
| `&fault=http503` | `/api/v3/ticker/price` 返回 503（`/api/v3/time` 正常） | `failure_class=HTTP_ERROR`、`error_code=MARKET_HTTP_503`、仍写 provenance |
| `&fault=global_host` | ticker item 带 `exchange: "Binance Global spot"`（身份标签，非主机名） | §3 guard 规则 6 / market 层以 `MARKET_SOURCE_NOT_VERIFIED` 拒绝，产出 UNKNOWN 行且仍写 provenance（H8） |
| 无 `fault` | 走 V1.5 原有路径（`env.__fetch` 或 `globalThis.fetch`） | 行为与 V1.5 完全一致 |

**边界与性质（必须按此理解，不得误解为生产功能）**：
1. 该分支整体位于 `if (!env || env.V45_TEST_TRIGGER !== "true") return 403` **之后**；`V45_TEST_TRIGGER !== "true"` 时先返回 403，`fault` 根本不会被读取。
2. `scheduled()`（cron 生产路径）**绝不读取 `fault`**，也不经过该 `fetch()` 分支。
3. `global_host` 只使用身份标签 `"Binance Global spot"`，`src/` 顶层可执行代码不出现 Global 主机名（H4/T7 仍成立）。
4. 这是**测试期受控入口**：验收完成后 `V45_TEST_TRIGGER` 置回 `false`，生产路径不可达，`fault` 参数在正式 Worker 上被 403 挡掉、完全无效果。**它不是可交付的生产能力，也不改变生产周期的任何行为。**
5. `tests/v16/v16_t3_failure_classes.mjs` 对三个 fault 各有真实运行断言（走真实 `fetch()` 入口，不是静态检查），另含 403 前置门、无 fault 正常路径、`scheduled` 不读 fault、无禁主机字面量、UNKNOWN 行 `mark_contracts_json` 为 `{}` 而非 `[]` 的断言。

---

## 4. 强制反例（不得靠单一来源自然通过）

- **反例①**：同一周期注入 `BTCUSDT`(US) + `1000SATSUSDT`(Global) 两个市场的报价 → `buildMarkContracts` 产出两条不同 `(exchange,market)` → guard 规则 2 → `MARKET_MIXING_REJECTED`，降级写 UNKNOWN 行，`mark_contracts_json` 保留两市场原始证据。
- **反例②**：DB 预置一条 Global VERIFIED **高权益**行（`equity=99000000000000`，远大于正常 US 峰值约 `1.82e13`），再跑 US 周期 → `priorMarketKeys` 检测到不同市场 → 周期被拒绝且 US `selectHistoryPeak` 不返回该值，Global 行只在其自身分区可见。
- 哨兵值被刻意抬到高于 US 峰值；**变异验证**（去掉分区条件的旧式查询）会返回 `99000000000000`（被污染），而正确实现返回 `18244694700000`。见 `reports/evidence/v16/mutation_check.json`。因此若分区逻辑退化，M2/M7/NEG2 的 peak 断言必然 FAIL，而非自然通过。

---

## 5. 门禁状态（沿用契约 §5）

- 接入正式 Staging D1 = **NO-GO**；生产晋级 / 自动交易 = **NO-GO**；V1.5 = GO 不变。
- `ENABLE_REAL_TRADING_MUST_BE_FALSE` 红线字面量保留；不接 `jev-paper-staging` / Night Researcher / 真实资金；不写 Token 明文；不 force push；**未执行 git commit / push**（主理人统一提交）。
- 本轮所有验证均为**本地隔离内存 D1 shim**（`node:sqlite`）；云端 D1 / Worker / E2E = UNVERIFIED，由主理人处理。
- `V45_TEST_TRIGGER` 仅为云端 E2E 期间的临时开关；**正式验收后必须 redeploy 为 `false`**，届时 §3.9 的 `fault` 入口在生产路径不可达（403 前置）。冒烟部署的中间态数据不计入任何验收数字。

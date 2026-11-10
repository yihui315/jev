# MARKET_MIXING_GUARD_CHECKLIST — 独立 QA 复核清单

| 项 | 值 |
| --- | --- |
| 角色 | market-auditor（P3，独立 QA 复核） |
| 性质 | **只读复核 + 建议**；不修改 `D:/jev-v15-wt/v15-dev/` 下任何文件；不认领 #6–#11 |
| 复核对象 | `D:/jev-v15-wt/v15-dev/schema.sql`（115 行）、`wrangler.toml.example`、`src/{constants,market,collector,provenance}.mjs`、`tests/run-all.mjs`、`tests/t0~t7_*.mjs`、`evidence/summary.json` |
| 依据 | team-lead 硬规则「不同交易市场行情不得无标识混用」+ `MARKET_COVERAGE.md` 三上下文结论 |
| 复核时点证据 | `v15-dev/evidence/summary.json`：8/8 PASS（T0–T7），`generatedAt=2026-11-09T04:44:03.504Z`，Node v22.22.2 |
| 判定口径 | `PASS` / `GAP`（存在缺口，需建议）/ `N/A`；本清单不替主理人做接入或改判决策 |
| 关联交付 | `D:/jev-v15-wt/v15-market/MARKET_COVERAGE.md`、`evidence/EDGE_CONTEXT.json` |

> **一句话结论**：Equity Collector 在「单一 Binance.US 源、fail-closed、不回写、幂等」方面实现扎实；但 **`v45_snapshot_provenance` 缺少 `exchange` / `market` 等市场标识字段**，现有测试即使全 PASS 也**不能证明「不得混用」规则被满足**。最小充分补字段见第 6.3 节。

---

## 0. 复核范围与边界

- 只读：未执行 `v15-dev` 下任何写操作、未跑其测试（避免污染 equity-builder 的 evidence）、未修改 schema / 配置 / 源码 / 测试。
- 未重跑 P3 的 34 请求探测；未写云端；未触碰凭据。
- 本清单中的「应断言什么」是**给 equity-builder 评估的 QA 建议**，不是本角色提交的代码变更。
- 术语：`A1`=本地沙箱 proxy 52498、`A2`=本地沙箱 proxy 10808、`B`=Cloudflare 边缘运行时；三者是不同出站网络上下文，连通性结论互不推翻（见 `MARKET_COVERAGE.md` 第 2 节 / 附录 A）。

---

## 1. 每条价格必须携带的字段（硬性字段契约）

**规则**：任何进入 `marks_json` / 权益计算 / peak / drawdown 的价格，其所在快照的 provenance 必须可回答「哪个交易所、哪个市场、哪个交易对、什么单位、什么时刻、从哪个端点、有多新鲜、是否可信」。缺任一字段，该价格不得入仓、不得参与计算。

| 字段 | 类型 | 必填 | 校验规则 | `v45_snapshot_provenance` 现状 |
| --- | --- | --- | --- | --- |
| `exchange` | string | 是 | 值域 `Binance.US` / `Binance Global spot`；与 `source_host` 一致 | **GAP 缺失**（仅 `source_host` 隐含） |
| `market` | string | 是 | 值域 `spot` / `perp` | **GAP 缺失** |
| `symbol` | string | 是 | 逐 symbol，不可仅藏在 JSON 文本里 | **GAP 缺失**（只在 `input_marks_json` / `missing_marks_json` 文本内） |
| `base_asset` | string | 是 | 含倍数语义，如 `1000SATS` | **GAP 缺失** |
| `quote_asset` | string | 是 | 如 `USDT` / `USDC` | **GAP 缺失** |
| `price_unit` | string | 是 | 如 `USDT per 1 base_asset unit` | **GAP 缺失** |
| `price_ts` | int(ms) 或 ISO | 是 | 须与 `ts_source` 成对 | `price_ts TEXT`（有，但 ISO 文本、无来源标注） |
| `book_ts` | int(ms) | 盘口记录是 | 与 `book_ts_source` 成对 | **GAP 缺失**（collector 未取 bookTicker） |
| `ts_source` | enum | 是 | `exchange_serverTime` / `trade_time` / `http_date` / `received_at` | **GAP 缺失**（见 6.2 关键风险） |
| `source_endpoint` | string | 是 | 完整 URL，**必须含 host** | `source_url` 含 host ✓；`endpoint` 不含 host（两列并存易误用） |
| `fetch_ts` | int(ms) 或 ISO | 是 | 本地接收时刻 | `fetched_at TEXT`（有） |
| `freshness` | int(ms) + verdict | 是 | 阈值 `60000ms`；`FRESH`/`STALE`/`UNKNOWN` | `freshness_ms` + `freshness_verdict`（有） |
| `data_quality` | enum | 是 | `VERIFIED` / `UNKNOWN`；`UNKNOWN` 不得入计算 | `data_quality`（有） |
| `http_status` | int | 是 | 传输失败为 `NULL` | `http_status`（有） |
| `payload_sha256` | string | 是 | 原始响应体 sha256 | `payload_sha256`（有） |
| `clock_skew_ms` | int | 建议 | `>=60000` 判 STALE | `clock_skew_ms`（有） |
| `network_context` | string | 是 | 采集上下文（第一等证据字段） | **GAP 缺失** |

**合规记录示例（一条价格的最小可审计形态）**：
```json
{
  "network_context": "cloudflare_pages_edge_runtime_prod",
  "exchange": "Binance.US", "market": "spot",
  "symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT",
  "price": "74937.11000000", "price_unit": "USDT per 1 base_asset unit",
  "unit_multiplier": 1,
  "price_ts": 1794198260935, "ts_source": "exchange_serverTime",
  "book_ts": null, "book_ts_source": null,
  "source_endpoint": "https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT",
  "fetch_ts": 1794198260973, "freshness_ms": 384, "freshness_verdict": "FRESH",
  "data_quality": "VERIFIED", "http_status": 200,
  "payload_sha256": "63ea381f9f35483cc9ea449c0e382130c1abb06d5868084cf6cadf676518b9ec",
  "clock_skew_ms": 38
}
```

---

## 2. 1000SATS 单位倍数校验点（必须显式化）

**事实（P3 实测）**：`data-api.binance.vision` 上 `1000SATSUSDT` 的 `baseAsset="1000SATS"`，即 **1 个 base_asset 单位 = 1000 SATS**；其价格 `0.00000841 USDT / 1 (1000SATS)` 与按 1 SATS 计价的 `SATSUSDT` 相差 **1000 倍**。`api.binance.us` 上该 symbol 返回 `-1121 Invalid symbol`（未上市）。

| 编号 | 可执行校验点 | 当前状态 |
| --- | --- | --- |
| QA-U1 | 每条含 `1000SATS` 的价格必须带 `base_asset="1000SATS"` 与 `unit_multiplier=1000`（或等价 `price_unit` 文本），**禁止**只凭 symbol 字符串推断 | GAP（schema 无相关列；collector 白名单规避但未显式表达） |
| QA-U2 | 单位换算必须在入库前完成且可重放：`price_fp = D( quoted_price ) * unit_multiplier` 的逆运算能还原原始报价 | GAP |
| QA-U3 | 断言 `1000SATSUSDT` 与任何 `SATSUSDT` 价格**不**出现在同一 `(exchange,market,symbol)` 序列；若两者并存，`unit_multiplier` 必须不同且被校验 | GAP |
| QA-U4 | 断言 1000 倍误差哨兵：构造一笔 `qty=1, price=0.00000841` 的持仓，权益计算必须按 `1000SATS` 单位语义，不得按 1 SATS 计（否则市值差 1000 倍） | GAP（现有 T4 仅测 BTCUSDT） |
| QA-U5 | 断言 ORDIUSDT / 1000SATSUSDT 在 `exchange="Binance.US"` 下 `error_code` 为**业务码类**（`NO_VENUE_QUOTABLE_SYMBOLS` / `-1121`），且 `marks_json` 中不出现来自 `Binance Global spot` 的价格 | 部分（T3 `unlisted_symbols_*` 覆盖，但未带市场标识断言） |

> 注意：`constants.mjs` 已把 `1000SATSUSDT` 列入 `VENUE_UNSUPPORTED_SYMBOLS`，**风险被规避但未被显式建模**。一旦主理人决定接入 Global 候选来源，QA-U1~U4 立即成为必测项。

---

## 3. 「不得拼接 / 不得互为兜底」断言

> **禁止与 `Binance.US` 价格混用或拼接成同一条权益曲线。**

| 编号 | 层级 | 可执行断言 |
| --- | --- | --- |
| QA-M1 | Schema | `v45_snapshot_provenance` 必须有 `exchange` + `market` 两列，且在 `(session_id, generation, exchange, market)` 上可分区；缺列则整条规则不可判定 → 直接判 GAP |
| QA-M2 | 查询 | 任何 peak / drawdown / replay 查询必须带 `WHERE exchange = ? AND market = ?`；静态审计应断言源码中不存在跨 `(exchange,market)` 的 `MAX(equity)` 聚合 |
| QA-M3 | 写入 | 断言 `marks_json` 中所有 symbol 来自同一 `source_host`；响应体若含其它 host 的价格 → 整周期 `UNKNOWN`，不得部分采纳 |
| QA-M4 | 兜底 | 断言不存在 `catch { fallback to data-api.binance.vision }`、不存在「上一好价」回写、不存在 `last-good-price` 缓存；T3 的 `throw/http503/missing/nonpositive` 四场景已覆盖，但需补一条：`fetcher` 返回 **Global 域响应**时必须被 `MARKET_SOURCE_NOT_VERIFIED` 拒绝（`market.mjs:42` 的 host allowlist 应被显式测试） |
| QA-M5 | 快照 id | `snapshot_id` 决定性输入不得包含易变价格，但**若同一 cycle 允许两个市场**，必须纳入 `(exchange, market)` 或采用独立序列，否则 `INSERT OR IGNORE` 会让两个市场互相吞掉（当前单市场下不构成问题，但接入候选源前必须断言） |
| QA-M6 | 序列 | 断言同一 `(session_id, generation)` 的权益曲线上所有 VERIFIED 行 `(exchange,market)` 恒定；出现第二个值即判失败 |
| QA-M7 | 标注 | 断言 `data-api.binance.vision` 的价格即便写入，也**必须**带 `exchange="Binance Global spot"` 且不与 `Binance.US` 行参与同一 peak 链 |

---

## 4. 三类失败区分规则

**必须区分，不得互相混淆，更不得把业务码写成网络不可用。**

| 类别 | 判定特征 | `provenance` 应有表现 | 当前 `error_code` 映射 | 处理 |
| --- | --- | --- | --- | --- |
| **① 传输失败** | 无 HTTP 响应 / 超时 / CONNECT 失败 / DNS / TLS | `http_status IS NULL`、`payload_sha256 IS NULL`、`ts_source` 不得为 `exchange_serverTime` | `MARKET_<ErrorName>`（如 `MARKET_TimeoutError`）；T3 `c1_throw` | `data_quality=UNKNOWN`、`marks={}`、`equity=0`；标注 `network_context`，**不得**泛化为交易所不可用/未上市 |
| **② 非 2xx HTTP** | 有响应、`http_status >= 400` | `http_status` 有值、通常有响应体、`freshness_verdict=UNKNOWN` | `MARKET_HTTP_<status>`（如 `MARKET_HTTP_503`）；T3 `c2_503` | 同上；与 ① 的区别是 `http_status` 非 NULL |
| **③ 交易所业务码** | HTTP 400 + `{"code":-1121,"msg":"Invalid symbol."}` 等明确业务错误 | `http_status` 可为 400（若实际发请求）或 NULL（若被白名单提前过滤）；`failure_class=VENUE_BUSINESS`；symbol 判 `BLOCKED` | 当前实现：白名单 `VENUE_SUPPORTED_SYMBOLS` 提前过滤 → `NO_VENUE_QUOTABLE_SYMBOLS`（T3 `unlisted_symbols_no_venue_quotable`）；T0 WebFetch 记录真实 `-1121` | **单独归类为「未上市 / BLOCKED」**，**不**写成 `UNAVAILABLE`、**不**从另一市场取价填充 |

**GAP**：`error_code` 是自由文本前缀，三类失败没有结构化分类列。建议新增 `failure_class TEXT CHECK (failure_class IN ('TRANSPORT','HTTP_ERROR','VENUE_BUSINESS','STALE','NONE'))`，并让 QA-T3-x 断言之。

**补充规则（来自 `MARKET_COVERAGE.md` 第 8 节）**：行情新鲜度阈值 `60000ms`；`clock_skew >= 60s` 判 `STALE`；非 2xx / 超时判 `UNAVAILABLE`；`-1121` 单独归类。`market.mjs:32` 的 `classifyFreshness` 已实现 `UNKNOWN/STALE/FRESH` 三态，语义一致 ✓。

---

## 5. 对 Equity Collector 测试的逐组「应断言什么」

> **范围说明**：任务书称「6 组测试」；`tests/run-all.mjs:17-26` 实际定义 **T0–T7 共 8 组**，`evidence/summary.json` 记录 8/8 PASS。核心行为组为 **T1–T6（6 组）**，T0 为行情源探测、T7 为静态审计。为避免遗漏，本清单按实际 8 组逐项给出；每组先列现有断言摘要，再给**应补充的可执行检查项**（编号 QA-Tn-x）。所有建议均为「QA 应断言什么」，交 equity-builder 评估，不由本角色改动测试。

### T0 `market_probe`（行情源探测）
现有：`proxy_is_alive` / `primary_source_probes_recorded` / `primary_source_unreachable_from_local_proxy` / `data_host_reachable_locally` / `data_host_wrongly_lists_unlisted_symbols` / `data_host_time_reachable`（6/6 PASS）。
应补：
- **QA-T0-1**：每条 probe 记录必须带 `network_context`；`primary_source_unreachable_from_local_proxy` 的判定**必须限定**为 `network_context='local_sandbox_proxy_52498'`，断言结果对象里不得出现全局 `local_live_primary="BLOCKED"` 作为对交易所的定性（当前 `t0:76` 有此字段，建议限定语境）。
- **QA-T0-2**：补第二代理 10808 探测，断言 `api.binance.us` 在该路径 `http_status=200`，从而证明「本地不可达」是代理路径属性（P3 `PROXY_10808_CONTROL.json` 已提供该事实）。
- **QA-T0-3**：`data_host_wrongly_lists_unlisted_symbols` 建议改语义为 `data_host_lists_symbols_not_on_binance_us`（Global 合法上市，非「错误列出」），并断言响应体含 ORDIUSDT/1000SATSUSDT 时**不**写入任何 `marks_json`。
- **QA-T0-4**：`production_edge_live` 可由主理人 04:24:20Z 边缘证据（`VERIFIED/200/FRESH/skew 38ms`）更新，断言该证据带 `PROVIDED / 本会话未独立复现` 标注，不与本地探测混为同一 verdict。
- **QA-T0-5**：断言 webfetch 观测里的 `-1121`（400）被归为「③ 业务码」，与 curl `http=000` 的「① 传输失败」分别记录。

### T1 `multi_cycle`（多周期 / provenance 完整性）
现有 14/14 PASS，含 `provenance_row_count` / `provenance_complete_fields`。
应补：
- **QA-T1-1**（关键）：`provenance_complete_fields`（`t1:95-101`）当前只断言 `engine_sha256 / collector_version / cycle_key / data_quality / freshness_verdict / http_status / payload_sha256 / source_host / endpoint / price_ts / fetched_at / clock_skew_ms / error_code`，**未覆盖** `exchange / market / symbol / quote_asset / price_unit / book_ts / ts_source / network_context`。应扩展为断言这些列全部非空且值合法 —— 否则即使 PASS 也不能证明不得混用规则成立。
- **QA-T1-2**：断言 4 行 `(exchange, market)` 完全相同且 `source_url` 的 host 与 `exchange` 一致；断言 `source_url LIKE 'https://' || source_host || '/%'`。
- **QA-T1-3**：重算当次 `/api/v3/ticker/price` 响应体 sha256，断言等于 `payload_sha256`（当前只断言非空）。
- **QA-T1-4**：断言 `freshness_verdict='FRESH'` 仅在 `http_status=200` ∧ `freshness_ms<=60000` ∧ `clock_skew_ms<60000` 时成立；`price_ts` 与 `fetched_at` 可换算且差值自洽。
- **QA-T1-5**：断言 `input_marks_json` 每个 symbol 携带 `unit_multiplier`（BTCUSDT=1），且不含其它 host 的 symbol。

### T2 `idempotency`（幂等）
现有 8/8 PASS，含 `all_fields_identical_across_3_runs` / `different_price_same_cycle_does_not_rewrite` / `no_price_backfill_into_stored_row`。
应补：
- **QA-T2-1**：`all_fields_identical_across_3_runs` 必须把新增的 `exchange / market / price_ts_source / network_context / failure_class` 纳入比较列 —— 加列后若不同步更新该断言，新列被不同值重写将测不出。
- **QA-T2-2**：扩展 `different_price_same_cycle_does_not_rewrite` 为 `different_market_same_cycle_does_not_rewrite`：第二次用 `exchange="Binance Global spot"` 的价格重放同一 cycle，断言 `INSERT OR IGNORE` **不覆盖**原 US 行，且最终只有一行。
- **QA-T2-3**：断言 `snapshot_id` 在单市场下不含 `exchange/market` 仍能稳定；若未来允许双市场，断言 id 生成纳入 `(exchange,market)` 或采用独立序列（见 QA-M5）。

### T3 `outage_recovery`（故障恢复 / 三类失败核心）
现有 11/11 PASS，含 `honest_error_codes` / `unlisted_symbols_no_venue_quotable` / `recover_peak_uses_verified_only`。
应补：
- **QA-T3-1**（关键，对应第 4 节）：断言三类失败结构化区分 —— `c1_throw`: `http_status IS NULL` ∧ `failure_class='TRANSPORT'` ∧ `payload_sha256 IS NULL`；`c2_503`: `http_status=503` ∧ `failure_class='HTTP_ERROR'`；`unlisted`: `failure_class='VENUE_BUSINESS'` ∧ `error_code='NO_VENUE_QUOTABLE_SYMBOLS'` ∧ **不**等于 TRANSPORT/HTTP_ERROR。
- **QA-T3-2**：断言三类失败均 `mark_quality='unknown'` ∧ `data_quality='UNKNOWN'` ∧ `marks_json='{}'` ∧ `equity=0`，且 **ORDI/1000SATS 不从 Global 取价填充**（无跨市场兜底）。
- **QA-T3-3**：断言 outage 行的 `price_ts` 不来自 `exchange_serverTime`（`ts_source` 应为 `received_at`/NULL），避免把本地时钟伪装成交易所时刻。
- **QA-T3-4**：断言 `c5_recover` 的 `(exchange,market,network_context)` 与 `c0_ok` 相同 —— 恢复不得切换到另一市场。
- **QA-T3-5**：断言注入一个 host 为 `data-api.binance.vision` 的 fetch 响应时，`rawFetch` 抛 `MARKET_SOURCE_NOT_VERIFIED` 且该周期 `UNKNOWN`（直接测 `market.mjs:42` allowlist，对应 QA-M4）。

### T4 `account_isolation`（账户隔离）
现有 14/14 PASS。
应补：
- **QA-T4-1**：断言两账户所有 provenance 行的 `(exchange, market, network_context)` 恒定相同 —— 账户隔离不得伴随市场切换。
- **QA-T4-2**：断言 `loss_account` 的 peak 既不被 `profit_account` 抬高，也不被「另一市场注入价格」抬高（注入 Global 响应应被拒绝）。
- **QA-T4-3**：断言 `source_host='api.binance.us'` 且 `source_url` 前缀恒为 `https://api.binance.us/`。
- **QA-T4-4**：断言 replay 输入按 `(session_id, exchange, market)` 分区（当前 replay 只按 session/generation）。
- **QA-T4-5**：补一个含 `1000SATSUSDT` 的账户（当前仅 BTCUSDT），断言 QA-U4 的 1000 倍哨兵。

### T5 `replay_readonly`（回放只读 / 冻结兼容）
现有 20/20 PASS，含 `frozen_unchanged_*` / `zero_writes_to_frozen_ledger_tables` / `engine_sha256_recorded_and_matches` / `fetch_trigger_*`。
应补：
- **QA-T5-1**：断言 replay 按 `(exchange, market)` 分组，跨市场行不参与同一 peak/drawdown 链。
- **QA-T5-2**：若新增 provenance 列或子表，断言 `engine_sha256` / `formula_version` 计算范围不含这些列（或明确记录范围变化），保证冻结兼容声明仍成立。
- **QA-T5-3**：若采用 6.3 的 M2 子表方案，`only_allowed_tables_written` 白名单必须同步包含子表，且 T5 零写入断言覆盖它。
- **QA-T5-4**：断言 `fetch_trigger_enabled_works` 路径下 `network_context` 显式标记为本地测试上下文，不与生产边缘上下文混淆。

### T6 `session_failclosed`（会话失败关闭）
现有 8/8 PASS，含 `fail_closed_cases_never_call_market` / `real_trading_true_red_line_aborts` / `valid_control_writes_one_verified` / `no_fabricated_price_in_any_fail_closed_case`。
应补：
- **QA-T6-1**：断言 fail-closed 场景不仅零写入、不调用 market，且**不退化**到另一市场 / 缓存 / 上一好价（与 QA-M4 呼应）。
- **QA-T6-2**：断言 `valid_control` 那一行 provenance 必填字段齐全（`exchange/market/price_ts_source/network_context`），不因 fail-closed 而留空。
- **QA-T6-3**：断言 `ENABLE_REAL_TRADING=true` 红线时零写入且 `data_quality` 不出现 `VERIFIED`。

### T7 `static_audit`（静态审计）
现有 14/14 PASS，含 `no_forbidden_host_in_own_src` / `primary_host_is_binance_us` / `deterministic_id_uses_only_5_stable_fields` / `no_credential_references_in_src` / `wrangler_binding_is_EQ_DB`。
应补：
- **QA-T7-1**：断言 `src/` 不存在 `data-api.binance.vision` / `api.binance.com` 字面量，且 `ALLOWED_ENDPOINTS` 与 `MARKET_HOST` 一致。
- **QA-T7-2**（关键）：静态比对 `schema.sql` 的 `v45_snapshot_provenance` 列集合与本清单第 1 节字段契约，输出缺失列清单；缺失任一必填列即判 GAP。
- **QA-T7-3**：断言 frozen 表 `source` 默认值 `'binance-public-ticker'` **不被当作市场标识**使用，市场标识由 provenance 的 `exchange/market` 承担。
- **QA-T7-4**：断言无任何代码路径把 `data-api.binance.vision` 的响应写入 `marks_json`（静态扫描 + T0/T3 运行时双重）。

---

## 6. schema provenance 完整性复核（选项 1）

### 6.1 逐字段核对（对照 `schema.sql:80-109` 的 `v45_snapshot_provenance` 27 列）

| 要求字段 | schema 列 | 覆盖 | 备注 |
| --- | --- | --- | --- |
| `exchange` | — | **缺失** | 仅 `source_host TEXT -- api.binance.us` 隐含，无结构化值域 |
| `market` | — | **缺失** | `constants.mjs:18` 有 `MARKET_VENUE="Binance.US spot (...)"` 但 `buildProvenance` 未落库 |
| `symbol` | — | **缺失** | 只在 `input_marks_json` / `missing_marks_json`（JSON 文本）中，无列、无索引、无约束 |
| `quote_asset` | — | **缺失** | |
| `price_unit` | — | **缺失** | |
| `price_ts` | `price_ts TEXT` | 有 | ISO-8601 文本；与 `freshness_ms`(int ms) 单位混用，审计需转换 |
| `book_ts` | — | **缺失** | collector 未取 `bookTicker`，无盘口时刻 |
| `ts_source` | — | **缺失** | 见 6.2 关键风险 |
| `source_endpoint` | `endpoint TEXT` + `source_url TEXT` | 部分 | `source_url` **含 host** ✓（= `MARKET_BASE + endpoint`，`market.mjs:85-88`）；`endpoint` 不含 host，两列并存易误用 |
| `fetch_ts` | `fetched_at TEXT` | 有 | |
| `freshness` | `freshness_ms` + `freshness_verdict` | 有 | `CHECK IN ('FRESH','STALE','UNKNOWN')` ✓ |
| `data_quality` | `data_quality TEXT` | 有 | `CHECK IN ('VERIFIED','UNKNOWN')` ✓ |
| `http_status` | `http_status INTEGER` | 有 | 注释明确「null on network failure」✓ |
| `payload_sha256` | `payload_sha256 TEXT` | 有 | |
| `clock_skew_ms` | `clock_skew_ms INTEGER` | 有 | |

覆盖统计：**15 项要求中 8 项有、7 项缺失、1 项部分**。另有 `snapshot_id / session_id / generation / cycle_key / collector_version / engine_sha256 / formula_version / input_state_hash / input_marks_json / initial_cash_fp / source_host / error_code / time_probe_ok / latency_ms / mark_quality / missing_marks_json / created_at` 等列，设计良好（幂等、引擎锚定、fail-closed 审计）。

### 6.1.1 字段缺口清单（V1.5 `BLOCKED / 待补字段`）

> 按 team-lead 收敛要求，对 `v45_snapshot_provenance` 的字段缺口给出**明确、非 FAIL、非已满足**的定性。以下 7 项是 team-lead 点名的独立字段缺口，全部记为 **V1.5 `BLOCKED / 待补字段`**。

| # | 缺口字段 | 当前表状态 | V1.5 定性 |
| --- | --- | --- | --- |
| 1 | `exchange` | 无独立列（仅 `source_host` 隐含 `api.binance.us`） | **BLOCKED / 待补** |
| 2 | `market` | 无独立列（`spot`/`perp` 未显式） | **BLOCKED / 待补** |
| 3 | `base_asset` | 无 | **BLOCKED / 待补** |
| 4 | `quote_asset` | 无 | **BLOCKED / 待补** |
| 5 | `price_unit` | 无 | **BLOCKED / 待补** |
| 6 | `network_context` | 无 | **BLOCKED / 待补** |
| 7 | `book_ts` | 无（collector 未取 `bookTicker`） | **BLOCKED / 待补** |

**当前表已有、不得写成缺失**：`source_host` / `endpoint` / `source_url` / `price_ts` / `fetched_at` / `freshness_ms` / `freshness_verdict` / `clock_skew_ms` / `data_quality` / `http_status` / `payload_sha256`（另有 `snapshot_id` / `session_id` / `generation` / `cycle_key` / `collector_version` / `engine_sha256` / `formula_version` / `input_state_hash` / `input_marks_json` / `initial_cash_fp` / `error_code` / `time_probe_ok` / `latency_ms` / `mark_quality` / `missing_marks_json` / `created_at`）。

**同属缺口、供一并评估（附加待补，非 team-lead 点名 7 项）**：`symbol`（仅存于 `input_marks_json` / `missing_marks_json` 文本，无列/索引/约束）、`ts_source`（`price_ts` 三路兜底落库后不可审计，见 6.2-3）、`book_ts_source`、`failure_class`（三类失败仅靠 `error_code` 字符串前缀）、`unit_multiplier`（1000SATS 倍数未显式化）、`venue`（`constants.mjs:18` 的 `MARKET_VENUE` 未被 `provenance.mjs:40-47` 的 COLUMNS 落库）。

**定性口径（三条必须同时成立）**：
1. 上表 7 项记为 **V1.5 `BLOCKED / 待补字段`**：含义是「为满足『不得混用』规则，这些字段必须补齐后，合规才可判定」。
2. **不是自动判 `FAIL`**：现有 `schema.sql` 与 8/8 测试 PASS，是「单一 `api.binance.us` 主源、fail-closed、不回写、ORDI/1000SATS 诚实标 UNKNOWN」语境下的有效交付；equity-builder 已确认主源自始至终唯一、从未接入 `data-api.binance.vision`、无需回退任何内容。缺字段不等于当前实现失败。
3. **也不能写成已满足**：`source_host` / `source_url` / `MARKET_VENUE` 等不能替代独立 `exchange` + `market` 组合键；现有测试 PASS 不能证明「不得混用」规则被满足。补字段与同步更新 T1/T2 断言列集合属**后续增强方向**，是否纳入 V1.5 范围由主理人决定。

### 6.2 缺失或易被误用的字段（重点）

1. **`exchange` / `market` 明确字段缺失**（最关键）。`source_host` 是运行时字符串，`MARKET_HOST` 硬编码单一源；一旦接入第二市场，`source_host` 成为唯一区分手段，没有 `(exchange, market)` 组合键，无法在 SQL 层禁止跨市场聚合 → **「不得混用」规则目前不可判定**。
2. **`MARKET_VENUE` 语义被丢弃**：`constants.mjs:18` 已定义 `"Binance.US spot (official public REST v3)"`，`market.mjs:146` 也把它放进返回对象的 `venue`，但 `provenance.mjs:40-47` 的 `COLUMNS` **不含 `venue`** → 已有的市场语义未落库。这是成本最低的修复点。
3. **`price_ts` 来源不可审计**（关键）：`market.mjs:115-118` 的 `priceTsMs` 有三条兜底路径 —— `serverTimeMs`（/time）→ `tickerDateHeader`（HTTP Date）→ `nowMs`（**本地时钟**）。落库后只剩一个 `price_ts` 值，`time_probe_ok` 是 0/1 布尔，**无法区分 `http_date` 与 `received_at`**。当 /time 失败且无 Date 头时，本地时间会被当作价格时间戳写入，违反「真实成交与盘口时间戳」要求。
4. **`source_url` 含 host ✓，但 `endpoint` 不含 host**：两列并存时若下游只用 `endpoint` 拼 URL 会丢失网络上下文；建议统一以 `source_url` 为 `source_endpoint`，或加 `CHECK (source_url LIKE 'https://' || source_host || '/%')`。
5. **1000SATS 单位倍数未显式**：schema 无 `base_asset` / `price_unit` / `unit_multiplier`；当前靠 `VENUE_UNSUPPORTED_SYMBOLS` 白名单规避（`constants.mjs:30`），风险被绕开但未被建模（见第 2 节）。
6. `error_code` 为自由文本，三类失败靠字符串前缀区分（6.2 之上第 4 节），脆弱；建议 `failure_class`。
7. frozen 表 `paper_equity_snapshots.source DEFAULT 'binance-public-ticker'` 是模糊标签，不含市场标识；因该表冻结不可改，**必须由 provenance 表承担市场标识**。
8. `price_ts` / `fetched_at` 为 TEXT，`freshness_ms` / `clock_skew_ms` / `latency_ms` 为 INTEGER，时间单位混用，跨字段校验需先转换。

### 6.3 「为满足不得混用规则，最少需要补哪些字段」——精确建议

> 前提：**不修改冻结表 `paper_equity_snapshots`**（`schema.sql:52-70` 逐字段兼容 0005），所有新增列只加在 `v45_snapshot_provenance`。

**M1 — 最小充分集（4 个新列 + 1 条 payload 约定，建议优先评估）**

| # | 新增列 | 定义（SQLite / D1 兼容） | 解决什么 |
| --- | --- | --- | --- |
| 1 | `exchange TEXT NOT NULL` | `CHECK (exchange IN ('Binance.US','Binance Global spot'))` | 市场标识（主体） |
| 2 | `market TEXT NOT NULL` | `CHECK (market IN ('spot','perp'))` | 与 1 组成 `(exchange, market)` 分区键，使 QA-M1/M2/M6 可判定 |
| 3 | `price_ts_source TEXT NOT NULL` | `CHECK (price_ts_source IN ('exchange_serverTime','trade_time','http_date','received_at'))` | 修复 6.2-3，使 `price_ts` 可审计、禁止本地时钟冒充 |
| 4 | `network_context TEXT NOT NULL` | 如 `cloudflare_pages_edge_runtime_prod` / `local_sandbox_proxy_52498` | 区分连通性结论适用域，避免本地/边缘互推 |

**+ 1 条 payload 约定（不必加列）**：在 `input_marks_json` / `marks_json` 的每个 symbol 值中携带 `base_asset`、`quote_asset`、`price_unit`、`unit_multiplier`（`1000SATSUSDT=1000`，其余 `=1`），并让 T1/T4 断言其存在与取值（QA-U1、QA-T1-5）。

> M1 的局限：一个快照可能含多个 symbol、且各 symbol 的 `price_ts`/`book_ts` 理论上不同，父表列只能表达快照级值。若后续接入第二市场或多 symbol 倍数不同，需升级到 M2。

**M2 — 推荐完整方案（若主理人决定接入候选来源）**

新增逐 symbol 子表（一行一 symbol），父表降级为快照级：
```sql
CREATE TABLE IF NOT EXISTS v45_mark_provenance (
  snapshot_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  base_asset TEXT NOT NULL,
  quote_asset TEXT NOT NULL,
  price TEXT NOT NULL,                 -- 原始报价字符串，保精度
  price_unit TEXT NOT NULL,
  unit_multiplier INTEGER NOT NULL DEFAULT 1,
  price_ts TEXT NOT NULL,
  price_ts_source TEXT NOT NULL,
  book_ts TEXT,
  book_ts_source TEXT,
  ts_source TEXT NOT NULL,             -- 与 price_ts_source 对齐的通用枚举
  source_endpoint TEXT NOT NULL,       -- 完整 URL，含 host
  fetch_ts TEXT NOT NULL,
  freshness_ms INTEGER NOT NULL,
  freshness_verdict TEXT NOT NULL,
  data_quality TEXT NOT NULL,
  http_status INTEGER,
  payload_sha256 TEXT,
  exchange TEXT NOT NULL,
  market TEXT NOT NULL,
  network_context TEXT NOT NULL,
  PRIMARY KEY (snapshot_id, symbol),
  FOREIGN KEY(snapshot_id) REFERENCES v45_snapshot_provenance(snapshot_id)
);
CREATE INDEX IF NOT EXISTS idx_v45_mark_series
  ON v45_mark_provenance(exchange, market, symbol, fetch_ts);
```
并在父表保留 `exchange` / `market` / `network_context`（用于快照级分区），使「同一 `(session_id, generation, exchange, market)` 序列」成为可查询约束。

**另建议（非「最少」但低成本）**：把 `constants.MARKET_VENUE` 落到 `venue` 列；新增 `failure_class`（第 4 节）；给 `source_url` 加 host 一致性 CHECK；`error_code` 保留原文不动以兼容现有测试。

### 6.4 `wrangler.toml.example` 复核

| 检查 | 结果 |
| --- | --- |
| `binding = "EQ_DB"`、隔离库名 `jev-v45-equity-ISOLATED-TEST` | PASS（T7 `wrangler_binding_is_EQ_DB` / `wrangler_marks_isolated_db` 覆盖） |
| `ENABLE_REAL_TRADING="false"`、`V45_TEST_TRIGGER="false"` | PASS |
| 无凭据 / Token | PASS（T7 `no_credential_references_in_src` 覆盖） |
| 第 42–45 行明确「主源 HARDCODED Binance.US、data-api.binance.vision FORBIDDEN as primary、NEVER fall back」 | PASS，与硬规则一致 |
| 措辞精度 | 建议：`"it wrongly lists ORDIUSDT/1000SATSUSDT"` 宜改为「它们不属于 Binance.US 市场，而在 Binance Global spot 合法上市」—— Global 数据本身无误，风险在于**跨市场冒充**，避免把候选源误判为「错误数据源」 |
| `network_context` / 部署目标运行时标识 | GAP：config 未显式声明 `network_context`，建议补一个变量（如 `MARKET_NETWORK_CONTEXT="cloudflare_pages_edge_runtime_prod"`）供 provenance 落库 |

---

## 7. QA 判定与移交

| 维度 | 判定 |
| --- | --- |
| 单源硬编码 / host allowlist / fail-closed / 不回写 / 幂等 / 账户隔离 / 冻结兼容 | **PASS**（8/8 测试已验证） |
| 三类失败的**行为**区分（不伪造、不兜底、`UNKNOWN` 隔离） | **PASS**（T3），但**结构化分类**为 GAP |
| 「不得混用」规则的可判定性 | **GAP** —— 缺 `exchange`/`market` 组合键，规则无法在 schema/SQL 层判定 |
| `price_ts` 可审计性 | **GAP** —— 缺 `ts_source`，三路兜底落库后不可分辨 |
| 1000SATS 单位显式化 | **GAP**（当前靠白名单规避） |
| 现有测试对硬规则的覆盖 | **GAP** —— `provenance_complete_fields` 未断言市场标识字段；即使全 PASS 也不能证明合规 |

**判定口径限定（对齐 team-lead 收敛要求）**：上表所有 `GAP` 一律按 **V1.5 `BLOCKED / 待补字段`** 理解（见 6.1.1）——即那 7 个独立字段补齐后才可判定「不得混用」合规。**不是自动判 `FAIL`**（现有 8/8 PASS 与单主源交付在当前语境下有效，equity-builder 已确认无需回退），**也不能写成已满足**（`source_host` / `source_url` / `MARKET_VENUE` 不能替代独立 `exchange` + `market` 组合键）。是否纳入 V1.5 范围、由谁实现，决策权归主理人。

**移交建议（交 team-lead 转 equity-builder 评估，本角色不改动其文件）**：
1. 优先评估 M1 的 4 列 + payload 约定（改动小、能立即让「不得混用」可判定）。
2. 同步更新 T1 `provenance_complete_fields` 与 T2 `all_fields_identical_across_3_runs` 的断言列集合（QA-T1-1 / QA-T2-1），否则加列后测试形同虚设。
3. 补 QA-T3-1（三类失败结构化）与 QA-T3-5（host allowlist 拒绝 Global 响应）两条运行时断言。
4. T0 的 `local_live_primary="BLOCKED"` 与 `production_edge_live="UNVERIFIED"` 建议按 `MARKET_COVERAGE.md` 附录 A 更新：限定为本地上下文、并纳入主理人边缘 VERIFIED 证据（QA-T0-1 / QA-T0-4）。
5. 是否接入 Binance Global 候选来源、是否新增 M2 子表，**决策权归主理人**；本清单只给字段与断言建议。

---

## 8. 复核边界声明

- 本清单为**只读复核**：未修改 `D:/jev-v15-wt/v15-dev/` 下任何文件，未运行其测试，未写云端，未触碰凭据。
- 未修改 V1.4 报告；未把 UNVERIFIED 写成 VERIFIED；未重跑 P3 已完成探测。
- 所有「应断言什么」为 QA 建议，最终是否采纳、由谁实现、何时实现由 team-lead / 主理人决定。

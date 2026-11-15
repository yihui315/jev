# JEV V1.6 — Cloud E2E Report（M5 隔离云端验收 · 轨道 A）

- 报告人：主理人（Owner）。执行者：主理人本人（云端部署、D1 迁移、周期采集、验证、入口关闭）。
- 日期：2026-11-09（UTC 时间戳以各证据 JSON 的 `at_utc` 为准）。
- 分支：`feature/v16-market-provenance-hardening`（基线 `bfb3206`，交付物尚未 commit，由本报告与 QA 阶段 2 一并入链）。
- 证据根目录：`reports/evidence/v16/cloud/`（12 份 JSON，全部机器生成、零手填）。

## TL;DR

| 项 | 结果 |
|---|---|
| 隔离环境 | 全新 D1 `jev-v45-equity-v16-isolated-test`（`997aa8fd-e0c3-4b99-a0c2-b9022f6be5f2`）+ 全新 Worker `jev-v45-equity-collector-v16`；**不复用 V1.5 的 `cf5cf033…` 库** |
| 迁移 | 0001（10 语句，与基线 schema.sql 逐字节一致）+ 0002（9 语句，v2 表 + UNKNOWN 回填 + legacy 保留）→ 35 列，12/12 检查 PASS |
| 采集 | 10 个周期全部 HTTP 200：C1–C3 正常（幂等重跑×2 written=0）+ 三类故障注入 + 恢复周期 + 混市场对抗周期 |
| 结果 | **34 行快照 + 34 行 provenance**（11 VERIFIED / 23 UNKNOWN） |
| 验证 | **24/24 检查 PASS**；D1 回放 **10/10** peak + drawdown 一致 |
| 入口 | `V45_TEST_TRIGGER` 已 redeploy 为 `false`，采集入口 403 `TEST_TRIGGER_DISABLED`（含 fault 路径） |
| 硬门禁 | V1.5 隔离库 12 行快照未触碰；Staging D1 equity rows 仍 0；`ENABLE_REAL_TRADING=false` |

## 1. 隔离边界

- V1.6 隔离 D1：`997aa8fd-e0c3-4b99-a0c2-b9022f6be5f2`（最小权限 Token 创建，读写探针 3/3 PASS 后清空探针表）。
- V1.6 隔离 Worker：`jev-v45-equity-collector-v16`（workers.dev），cron 故意设为年度 tick（`0 0 1 1 *`），不干扰确定性 E2E 周期。
- V1.5 隔离 D1 `cf5cf033-…` 与其 Worker `jev-v45-equity-collector-isolated` **全程只读核验、未做任何写操作**。
- 正式 E2E 前执行 `reset`：丢弃冒烟周期 `V16-SMOKE-DISCARD`（非验收数据），13 条 DROP 全部成功；系统表 `_cf_KV`/`sqlite_sequence` 按设计排除在判定外（校验器口径修正，未删除断言）。

## 2. 迁移与 Schema（证据：`v16_cloud_schema.json`、`v16_migration_offline_dryrun.json`）

1. 离线 SQLite 演练先行：0001+0002 共 19 条语句全部可执行；预置 legacy 行回填为 `UNKNOWN / HISTORY_UNPROVEN`（未伪装真实来源）。
2. 真实 D1 应用：0001 十条 + 0002 九条全部成功；最终 `v45_snapshot_provenance` **35 列**；`v45_snapshot_provenance_v1_legacy` 保留未 DROP；3 个索引齐全。
3. `schema.sql`（V1.6 最终态）与迁移结果列集合完全一致；冻结的 `paper_equity_snapshots` DDL 与 0001 逐字节一致（775 字节）。

## 3. 采集周期（证据：`v16_cloud_collect_evidence.json`）

| 周期 | 注入 | HTTP | written | verified | unknown | 说明 |
|---|---|---|---|---|---|---|
| v16-cloud-C1 | — | 200 | 4 | 2 | 2 | 首个正常周期 |
| v16-cloud-C1（重跑×2） | — | 200 | **0** | 2 | 2 | 幂等：同 cycle_key 连跑 3 次仅首写 |
| v16-cloud-C2 | — | 200 | 4 | 2 | 2 | 第二周期 |
| v16-cloud-C3 | — | 200 | 4 | 2 | 2 | 第三周期 |
| v16-cloud-C4-outage | `transport` | 200 | 4 | 0 | 4 | 传输层故障 fail-closed |
| v16-cloud-C5-http | `http503` | 200 | 4 | 0 | 4 | HTTP 非法状态 fail-closed |
| v16-cloud-C6-global | `global_host` | 200 | 4 | 0 | 4 | 未授权 Global 市场注入被拒绝 |
| v16-cloud-C7-recover | — | 200 | 4 | 2 | 2 | 故障后恢复正常 VERIFIED |
| v16-cloud-C8-mixed | DB 预置 Global 高权益行 | 200 | 5 | 2 | 3 | 对抗性混市场周期 |

- 4 个 FIXTURE 账户明确标注 `v45-FIXTURE-*`（非真实交易结果）。
- 正式周期**不传固定 `now_ms`**，使用边缘真实时钟——冒烟阶段曾用历史时间戳触发真实 STALE fail-closed（`freshness_ms≈6.2e6 > 60s`），已作为非验收证据归档（`v16_smoke_stale_evidence.json`），证明 STALE 判定真实生效。
- **统计口径（QA 阶段 2 裁定）**：11 条 VERIFIED = **10 条采集器真实 US 输出**（C1×1+C2+C3+C7，各 2-3 verified）+ **1 条 C8 手工预置的对抗哨兵行**（`v16_FIXTURE_ADVERSARIAL`，仅用于验证峰值不被污染，禁止计入采集器成绩）。
- 正式 E2E 后（QA2-D1 修复）已用最终源码（含 guard 规则 8）redeploy，`deploy_ok` 采用结构化判定（wrangler exit 0 + API readback 200）；入口复查仍 403 `TEST_TRIGGER_DISABLED`（`v16_entry_closed.json` 含修复前后两次探针）。E2E 期间云端周期全部使用已解析的 `network_context=cloudflare_pages_edge_runtime_prod`，不受规则 8 影响。

## 4. 验证结果（证据：`v16_cloud_e2e_evidence.json`，24/24 PASS）

关键断言（全部 PASS）：

- `rows_written`：34。
- `verified_rows_all_binance_us_spot`：全部 verified 行 `exchange='Binance.US' AND market='spot'`。
- `verified_price_ts_source_not_local_clock`：全部为 `exchange_serverTime`（本地接收时间不冒充交易所时间）。
- `network_context_not_unknown`：全部 `cloudflare_pages_edge_runtime_prod`。
- `failure_class_none_on_verified`、`partition_key_self_consistent`、`source_url_host_matches_exchange`：全部通过。
- `mark_contracts_complete_per_symbol`：逐 symbol 完整（symbol 键对象，非数组）；`1000SATSUSDT` 契约 `unit_multiplier=1000`（状态 BLOCKED，无价格参与计算）。
- `unknown_rows_have_no_fabricated_equity`：23 行 UNKNOWN 全部不虚构可信权益（无持仓行允许现金等价，判据按持仓精确区分）。
- `mixed_market_peak_not_contaminated`：C8 中 US 周期新行 `equity=0`、`MARKET_MIXING_REJECTED`；预置 Global VERIFIED 高权益行（9.9e13 哨兵）**未污染** verified 峰值（max_verified_peak=24,989,122,000,000）。
- 传输/HTTP/Global 三类故障全部正确分类（TRANSPORT / HTTP_ERROR / MARKET_SOURCE_NOT_VERIFIED），且**仍写 provenance 审计行**。

## 5. D1 回放（证据：`v16_equity_d1_replay.json`）

- 10 行参与回放（4 个 FIXTURE 账户逐周期 verified 序列）。
- peak 逐行重算 **10/10 一致**；drawdown 逐行重算 **10/10 一致**（1e8 定点、半向上舍入，与冻结引擎公式一致）。
- `paper_accounts.state_hash` 不变；`paper_server_events` / `paper_orders` 0 行（采集器零副作用）。

## 6. 入口关闭与部署状态（证据：`v16_worker_deploy.json`、`v16_entry_closed.json`）

- E2E 完成后 redeploy `V45_TEST_TRIGGER=false`。
- 探针实测：`/__v45/collect?…` → **403 `TEST_TRIGGER_DISABLED`**；带 `fault=transport` 同样 403（故障注入入口在生产不可达）。
- 部署源码 sha256 记录：`apps/equity_collector_v45/deployed_source_sha256.json`（部署与最终源码一致）。

## 7. 硬门禁复核（证据：`v16_hard_gates.json`）

| 门禁 | 状态 |
|---|---|
| V1.5 隔离库快照 | 12 行（9 verified / 3 unknown）、provenance 12 行——**未触碰** |
| Staging D1 `55b690ec…` equity rows | 仍 **0** |
| Night Researcher 生产 | 无 D1 绑定、零改动 |
| `ENABLE_REAL_TRADING` | `"false"`（构造性证明沿用 V1.5） |
| 冻结引擎 15 文件 tree sha256 | `aec5d1b7cc26…` 不变 |
| PR #5 | 未修改（`bfb3206`，draft，open） |

## 8. 残留风险与未验证项

1. ORDIUSDT / 1000SATSUSDT 在 Binance.US 仍 BLOCKED（`-1121` 可复现）。
2. Binance Global spot 接入仍 NO-GO / CONDITIONAL（禁止混用已由 guard 强制，但候选源未接入）。
3. `paper_equity_snapshots` 在 Staging D1 仍 0 行——接入评审门禁未解除（本报告不构成解除依据）。
4. 部署为 workers.dev 隔离 Worker；接入 `jev-paper-staging` 仍是独立的、需单独授权的决策。
5. QA 阶段 2 独立复核进行中——本报告的所有数字为**主理人实测 VERIFIED**，最终 signoff 以独立 QA 结论为前置。

## 9. 证据索引

`reports/evidence/v16/cloud/`：`v16_cloud_reset.json`、`v16_cloud_schema.json`、`v16_cloud_fixtures.json`、`v16_cloud_collect_evidence.json`、`v16_cloud_e2e_evidence.json`、`v16_equity_d1_replay.json`、`v16_worker_deploy.json`、`v16_entry_closed.json`、`v16_isolated_d1.json`、`v16_migration_offline_dryrun.json`、`v16_smoke_stale_evidence.json`、`v16_hard_gates.json`。

—— 本报告不含任何 Token / 凭据明文（敏感串扫描 0 命中）。

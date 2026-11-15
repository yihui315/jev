# JEV V1.6 — Schema 版本化迁移说明（0001 → 0002 → 最终态）

**验证状态**：本地隔离内存 D1 shim，VERIFIED；云端 D1 应用 = UNVERIFIED（主理人处理）。

---

## 1. 文件布局（契约 §1.1）

| 文件 | 内容 | 约束 |
|---|---|---|
| `migrations/0001_v45_base.sql` | `git show bfb3206:apps/equity_collector_v45/schema.sql` **逐字节复制**，4943 字节 | 永不修改；由 QA-T7-2 用独立 git 读比对 |
| `migrations/0002_market_provenance.sql` | v1 → v2 版本化迁移 | 见 §3 |
| `schema.sql` | V1.6 **最终态**（= 0001 前缀 + V1.6 provenance 块），5518 字节 | `harness.openDb()` 只读这一个文件，必须是最终态 |

`schema.sql` 的 `paper_equity_snapshots` DDL 块与 0001 **逐字节一致**（生成方式：以 `0001` 的 Buffer 为前缀，按字节切在 `-- ---- NEW: V45 provenance` 标记处，再拼接 V1.6 provenance 块；前缀未经过任何字符串重写）。QA-T7-2 用 Buffer 比较证明（775 字节块相等）。

---

## 2. v2 新增 8 列（契约 §1.2，只加在 `v45_snapshot_provenance`）

| 列 | 定义 |
|---|---|
| `exchange` | `TEXT NOT NULL CHECK (exchange IN ('Binance.US','Binance Global spot','UNKNOWN'))` |
| `market` | `TEXT NOT NULL CHECK (market IN ('spot','perp','UNKNOWN'))` |
| `price_ts_source` | `TEXT NOT NULL CHECK (price_ts_source IN ('exchange_serverTime','trade_time','http_date','received_at','UNKNOWN'))` |
| `network_context` | `TEXT NOT NULL CHECK (length(network_context) > 0)` |
| `venue` | `TEXT NOT NULL`（承接 `MARKET_VENUE`，修复 V1.5「语义被丢弃」） |
| `failure_class` | `TEXT NOT NULL CHECK (... IN ('NONE','TRANSPORT','HTTP_ERROR','VENUE_BUSINESS','STALE','MARKET_MIXING','HISTORY_UNPROVEN','UNIT_MISMATCH','PAYLOAD_UNVERIFIED','UNKNOWN'))` |
| `mark_contracts_json` | `TEXT NOT NULL DEFAULT '{}'`（M2 逐 symbol 完整契约，列不是子表） |
| `market_partition_key` | `TEXT NOT NULL CHECK (market_partition_key = exchange \|\| '\|' \|\| market)`（自洽约束） |

最终表共 **35 列**（V1.5 的 27 + 8）。新增分区索引：
`idx_v45_prov_partition (session_id, generation, exchange, market, data_quality)`，并重建原有 `idx_v45_prov_session_cycle`、`idx_v45_prov_quality`。

---

## 3. 0002 迁移语义（契约 §1.3）

按顺序 5 步（`D1Shim`/`node:sqlite` 多语句 `exec` 真实执行过）：

1. `CREATE TABLE v45_snapshot_provenance_v2 (...)` —— 含全部 35 列、所有 CHECK、分区键自洽约束、FK。
2. `INSERT OR IGNORE INTO ..._v2 SELECT ... FROM v45_snapshot_provenance` —— 对 8 个新列**显式**写：
   `exchange='UNKNOWN'`、`market='UNKNOWN'`、`price_ts_source='UNKNOWN'`、`network_context='UNKNOWN'`、
   `venue='UNKNOWN'`、`failure_class='HISTORY_UNPROVEN'`、`mark_contracts_json='{}'`、
   `market_partition_key='UNKNOWN|UNKNOWN'`。
   **不得**用 `Binance.US` / `spot` / `exchange_serverTime` 之类默认值伪装历史来源。
3. `ALTER TABLE v45_snapshot_provenance RENAME TO v45_snapshot_provenance_v1_legacy` —— **保留历史证据，不 DROP**。
4. `ALTER TABLE v45_snapshot_provenance_v2 RENAME TO v45_snapshot_provenance`。
5. 重建索引：RENAME 后原两索引仍挂在 legacy 表上，故 `DROP INDEX IF EXISTS` 后按同名重建到规范表，再加 `idx_v45_prov_partition`。

> `0002` 全程无 `DROP TABLE`；legacy 表及其原始 27 列数据逐字节保留。

`market_partition_key='UNKNOWN|UNKNOWN'` 满足自洽 CHECK（`'UNKNOWN' || '|' || 'UNKNOWN'`）。

---

## 4. 历史行不得污染 peak（契约 §1.4）

迁移后的历史行虽保留 `data_quality='VERIFIED'`（V1.5 原值），但：
- `failure_class='HISTORY_UNPROVEN'` → 被 `selectHistoryPeak` 的 `failure_class='NONE'` 排除；
- `price_ts_source='UNKNOWN'` → 被 `price_ts_source IN (...)` 排除；
- `network_context='UNKNOWN'` → 被 `network_context <> 'UNKNOWN'` 排除；
- `exchange='UNKNOWN'` / `market='UNKNOWN'` → 与任何真实 `(exchange,market)` 分区都不匹配。

因此无论查询 US 分区还是 UNKNOWN 分区，历史行都**不计入 peak**。`v16_migration.mjs` 真实断言 `history_row_excluded_from_peak`（`pUS=null`、`pUnk=null`）。

---

## 5. 独立 smoke 验证（真实命令与输出）

命令：`node D:/jev-v16-wt/_verify_schema.cjs`（离树辅助脚本，不进仓库）

真实 stdout 摘要：
```
A_final_col_count= 35
A_new_cols= exchange|market|price_ts_source|network_context|venue|failure_class|mark_contracts_json|market_partition_key
A_peak_sql_compiles=OK
A_valid_row_insert=OK
A_partition_check=REJECTED (true)
A_exchange_check=REJECTED
B_legacy_exists= true
B_legacy_row_preserved= true
B_final_col_count= 35
B_backfill= UNKNOWN|UNKNOWN|UNKNOWN|UNKNOWN|UNKNOWN|HISTORY_UNPROVEN|{}|UNKNOWN|UNKNOWN
B_indexes= idx_v45_prov_partition|idx_v45_prov_quality|idx_v45_prov_session_cycle
```

结论（VERIFIED）：
- 最终 schema 35 列、8 新列名字正确；`HISTORY_PEAK_SQL` 能编译。
- 分区键 CHECK 与 exchange 值域 CHECK 真实拒绝非法行。
- 0001 → 0002 后 legacy 表存在、历史行保留、回填全为 `UNKNOWN/HISTORY_UNPROVEN`、3 个索引齐全。

生成校验（`node D:/jev-v16-wt/_gen_v16_schema.cjs`）：
```
0001 bytes           : 4943
schema.sql bytes     : 5518
0002 bytes           : 4907
prefix byte-identical: true
snapshots DDL equal  : true
snapshots DDL bytes  : 775
```

---

## 6. 与冻结表的边界

`paper_equity_snapshots` 的字段 / 约束 / 默认值**零改动**（H2），其 DDL 块在 `schema.sql` 中与 `0001` 逐字节一致。`server/engine/` 15 文件零改动。`0001` 由 `git show bfb3206` 生成，经 QA-T7-2 独立 git 读比对为逐字节一致。

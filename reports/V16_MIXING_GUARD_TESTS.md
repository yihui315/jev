# JEV V1.6 — 混合守卫 / 迁移 / 单元测试真实结果

> 本文件内容来自**真实执行**的 stdout 与 evidence JSON，未预填、未把 FAIL 写成 PASS。所有命令在 `D:/jev-v16-wt/v16` 下执行，Node `v22.22.2`，`node:sqlite` 内存 D1 shim。

---

## 1. 执行的命令（真实）

```
node apps/equity_collector_v45/tests/run-all.mjs        # V1.5 旧 8 组回归（逐字节不变）
node apps/equity_collector_v45/tests/v16/run-all.mjs    # V1.6 新 8 组
```

另执行离树辅助脚本用于 schema smoke 与变异验证：
`node D:/jev-v16-wt/_verify_schema.cjs`、`node D:/jev-v16-wt/_gen_v16_schema.cjs`、`node D:/jev-v16-wt/_mutation_check.mjs`。

---

## 2. V1.5 基线回归（加固后源码上重跑）

`node apps/equity_collector_v45/tests/run-all.mjs` → `exit code = 0`

```
[T0] exit=0 verdict=PASS   [T1] exit=0 verdict=PASS
[T2] exit=0 verdict=PASS   [T3] exit=0 verdict=PASS
[T4] exit=0 verdict=PASS   [T5] exit=0 verdict=PASS
[T6] exit=0 verdict=PASS   [T7] exit=0 verdict=PASS
OVERALL=PASS {"total":8,"pass":8,"fail":0}
```

**断言总数 = 95**（T0=6, T1=14, T2=8, T3=11, T4=14, T5=20, T6=8, T7=14），与基线一致，未删 / 未放宽任何旧断言。T7 的 14 条静态断言（含 `no_forbidden_host_in_own_src`、`all_writes_are_insert_or_ignore`、`deterministic_id_uses_only_5_stable_fields`、`real_trading_red_line_enforced`、`no_console_logging_in_src`、`no_credential_references_in_src`）全部 `true`。

旧测试文件逐字节自证（`git show bfb3206:<path>` blob 的 sha256 与工作区文件 sha256 全部一致，11/11 文件 `match=YES`）：见 §8。

---

## 3. V1.6 新套件最终结果

`node apps/equity_collector_v45/tests/v16/run-all.mjs` → `exit code = 0`

```
[V16_T1]  exit=0 verdict=PASS checks=12
[V16_T2]  exit=0 verdict=PASS checks=10
[V16_T3]  exit=0 verdict=PASS checks=17
[V16_T7]  exit=0 verdict=PASS checks=15
[V16_M]   exit=0 verdict=PASS checks=11
[V16_U]   exit=0 verdict=PASS checks=14
[V16_MIG] exit=0 verdict=PASS checks=11
[V16_NEG] exit=0 verdict=PASS checks=11
OVERALL=PASS {"total":8,"pass":8,"fail":0} assertions={"total":101,"passed":101,"failed":0}
```

**8/8 PASS，101 条断言全部通过（101 passed / 0 failed），exit code 0。**

> 相比上一版 92 条，增量 +9 来自主理人两条补充要求：① `mark_contracts_json` 由数组改为 symbol-keyed 对象，T1 新增 1 条「非数组对象」断言；② 云端 E2E 受控故障注入入口，T3 新增 8 条 fault 相关真实运行断言。旧断言总数未删减、未放宽。

### 逐组 PASS/FAIL + 断言数 + exit code

| 组 | 文件 | 覆盖 | 断言数 | PASS | FAIL | exit |
|---|---|---|---|---|---|---|
| V16_T1 | `v16_t1_field_contract.mjs` | QA-T1-1：8 新列非空 / 值域 / 分区键自洽 / source_url host 一致 / **mark_contracts_json 为非数组对象 + 键为 symbol + 值含全部契约字段** | 12 | 12 | 0 | 0 |
| V16_T2 | `v16_t2_idempotency_fields.mjs` | QA-T2-1：含新列的整行 hash 3 次一致 / 改价不回写 | 10 | 10 | 0 | 0 |
| V16_T3 | `v16_t3_failure_classes.mjs` | QA-T3-1（TRANSPORT/HTTP_ERROR/VENUE_BUSINESS 结构化）、QA-T3-5（Global host 注入）、**TEST-ONLY 三 fault 注入 + 403 门 + 无 fault 正常路径 + 静态隔离** | 17 | 17 | 0 | 0 |
| V16_T7 | `v16_t7_schema_contract.mjs` | QA-T7-2：列集合=FIELD_CONTRACT / 8 列 CHECK / snapshots DDL 逐字节 / 0001 逐字节基线 | 15 | 15 | 0 | 0 |
| V16_M | `v16_m1_m7_mixing.mjs` | QA-M1~M7：分区键 / peak 只按 (exchange,market) / 无 fallback / id 稳定 / 序列恒定 / 跨市场不聚合 | 11 | 11 | 0 | 0 |
| V16_U | `v16_u1_u5_units.mjs` | QA-U1~U5：1000SATS unit=1000 / 逆运算 / 不并列 / 1000 倍哨兵 / BLOCKED 业务码 | 14 | 14 | 0 | 0 |
| V16_MIG | `v16_migration.mjs` | 0001→0002：历史列全 UNKNOWN/HISTORY_UNPROVEN / legacy 保留 / 历史不计入 peak | 11 | 11 | 0 | 0 |
| V16_NEG | `v16_negative_mixed_cycle.mjs` | **强制反例** ① US+Global 同周期 ② 预置 Global 高权益跑 US 周期 | 11 | 11 | 0 | 0 |
| **合计** | | | **101** | **101** | **0** | **0** |

每组输出 `checks:[{name,pass,detail}]`，runner 的仓库根 `reports/evidence/v16/summary.json` 汇总每组 PASS/FAIL 与每条断言名（字段 `{generatedAt,node,overall,counts,assertions,results:[{id,file,label,exitCode,verdict,evidence,checks:[{name,pass}]}]}`）；证据落在 `reports/evidence/v16/`：8 个 `<ID>_<label>.json` runner 记录（含真实 stdout/stderr/exitCode 与 RESULT_JSON 解析结果）+ 8 个 `group_v16_*.json` 测试自写文件（CI 大小写不冲突）+ `summary.json` + `mutation_check.json` + 队友的 `independent_verify.json`。V1.5 旧 runner 的 `apps/equity_collector_v45/evidence/` 输出路径保持原样，未改动。

---

## 4. 真实过程中的一次 FAIL 与修复（如实记录）

哨兵值强化后的**第一次** V1.6 重跑，真实 stdout：

```
[V16_NEG] exit=1 verdict=FAIL checks=11
OVERALL=FAIL {"total":8,"pass":7,"fail":1} assertions={"total":92,"passed":91,"failed":1}
EXIT_CODE=1
```

- **失败断言**：`NEG2_global_sentinel_exceeds_normal_us_equity_scale`
- **失败 detail**：`{"globalEq":99000000000000,"note":"... sentinel is an order of magnitude larger"}`
- **真实原因**：这是我为该组**新写**的「哨兵有效性」断言，把阈值笔误写成 `GLOBAL_EQ > 100000000000000`（1e14），而哨兵值是 `99000000000000`（9.9e13），`9.9e13 < 1e14` → 断言不成立。**属于测试断言与哨兵值不一致的笔误，与被测实现（collector / guard / partition）无关**；其余 10 条断言（含两个强制反例）仍全部 PASS。
- **修复**：把阈值下调为 `50000000000000`（5e13），它仍约为正常 US 权益（~1.8e13）的 3 倍，保持「若 Global 行泄漏进 US peak，MAX 必然被抬高、断言必然 FAIL」的区分度——**不是放宽到无意义**。修改点仅 `tests/v16/v16_negative_mixed_cycle.mjs` 一条断言阈值，未改任何实现、未删任何断言、未把 FAIL 改成 PASS。
- **修复后重跑**：`8/8 PASS`，`92/92`，`exit 0`（当时结果；**后续按主理人补充要求增量补了 9 条断言**，最终态见 §3 的 `101/101`）。该 FAIL 属于历史真实记录，未删除、未改写为 PASS。

---

## 5. 测试强度缺陷的发现与修正（避免「自然通过」）

初版 `v16_m1_m7_mixing.mjs` / `v16_negative_mixed_cycle.mjs` 把预置 Global 行写成 `GLOBAL_EQ=9000000000`。核验时发现 US 历史峰值实际是 `18244694700000`（1e8 定点），**9e9 远小于 US 峰值**——即便分区逻辑有 bug 把 Global 行算进 US peak，`MAX` 仍取更大的 US 值，断言会「自然通过」，违反契约「不得靠单一来源让测试自然通过」。

修正：
- `GLOBAL_EQ` 抬到 `99000000000000`（9.9e13，> 正常 US 权益一个数量级）；
- M7 的 3 条不可证明哨兵行抬到 `80000000000000 / 80000000000001 / 80000000000002`（均 > usPeak）；
- 新增断言 `M2_global_sentinel_exceeds_us_peak_so_leak_is_visible`、`M7_sentinels_exceed_us_peak_so_leak_is_visible`、`NEG2_global_sentinel_exceeds_normal_us_equity_scale`，显式自证哨兵高于峰值。

### 变异验证实证（真实）

命令 `node D:/jev-v16-wt/_mutation_check.mjs`，stdout：
```
{
  "usPeak": 18244694700000,
  "globalSentinel": 99000000000000,
  "selectHistoryPeak_US": 18244694700000,
  "mutant_no_partition_peak": 99000000000000,
  "correct_not_polluted": true,
  "mutant_is_polluted": true,
  "conclusion": "If partition regressed, M2/M7/NEG2 peak assertions would FAIL (sentinel exceeds usPeak)"
}
```
即：故意去掉 `(exchange,market)` 分区条件的旧式查询返回被污染的 `99000000000000`，而真实 `selectHistoryPeak` 返回 `18244694700000`。两者不同，**证明若实现退化，M2/M7/NEG2 的 peak 断言必然 FAIL**。证据：`reports/evidence/v16/mutation_check.json`。

---

## 6. 强制反例真实 detail（来自 evidence JSON）

反例①（同周期注入 US + Global 两个市场报价）：
```
NEG1_us_plus_global_quotes_rejected : true
  detail: {"verified":0,"unknown":1,"provRows":1,"errorCode":"MARKET_MIXING_REJECTED"}
NEG1_original_two_market_evidence_preserved : true
  detail: mark_contracts_json 含 2 条记录，其中 exchange="Binance Global spot"(1000SATSUSDT) 与 exchange="Binance.US"(BTCUSDT)
NEG1_still_writes_unknown_provenance : true
NEG1_no_fabricated_price : true  (equity=0, marks_json="{}")
```

反例②（预置 Global VERIFIED 高权益行后跑 US 周期）：
```
NEG2_us_cycle_rejected_by_prior_global_market : true
  detail: {"verified":0,"unknown":1,"errorCode":"MARKET_MIXING_REJECTED"}
NEG2_us_snapshot_peak_not_polluted_by_global : true
  detail: {"rows":1,"peak":0,"globalEq":99000000000000}
NEG2_selectHistoryPeak_US_excludes_global_row : true   (peakUS=null)
NEG2_global_row_isolated_in_its_own_partition : true  (peakGlobal=99000000000000)
```

其他关键真实值：
- T3 三类结构化：`TRANSPORT`(errorCode=`MARKET_TypeError`)、`HTTP_ERROR`(`MARKET_HTTP_503`)、`VENUE_BUSINESS`(`MISSING_OR_UNLISTED_SYMBOL`)，均 PASS；Global host 注入 `api.binance.com` → `MARKET_SOURCE_NOT_VERIFIED`。
- 迁移：`exchange/market/price_ts_source/network_context/venue` 全 `UNKNOWN`，`failure_class=HISTORY_UNPROVEN`，`market_partition_key=UNKNOWN|UNKNOWN`，legacy 表保留，历史行不计入 peak（`pUS=null`、`pUnk=null`）。
- 单位：`1000SATSUSDT.unit_multiplier=1000`、逆运算还原 `0.000008`、SATS 并列 / 错误 multiplier → `UNIT_CONTRACT_MISMATCH`。
- M1-M7 分区：`peakUS=18244694700000`、`peakGlobal=99000000000000`、3 条高权益不可证明哨兵（8e13）被排除。

### 补充 A：`mark_contracts_json` 对象化（真实回读）

修正前中间态 VERIFIED / UNKNOWN 行落库为 `"[]"`（数组），违反契约 §2「以 symbol 为键的对象」。修正后真实回读：

- VERIFIED 行：`{"BTCUSDT": {"exchange":"Binance.US", ..., "unit_multiplier":1, "data_quality":"VERIFIED"}}` —— 非数组对象、键即 symbol、值含全部 17 字段。
- NEG1 混合行：`{"1000SATSUSDT":{...exchange:"Binance Global spot"...},"BTCUSDT":{...exchange:"Binance.US"...}}` —— 两市场原始证据按 symbol 索引保留。
- UNKNOWN 行（3 个 fault / STALE / VENUE_BUSINESS）：`"{}"`，**不是 `"[]"`**。

T1 新增断言 `mark_contracts_json_is_symbol_keyed_object_not_array` 与改写后的 `mark_contracts_json_complete_per_symbol` 均真实 PASS；U1-U5、NEG1 的数组形态断言同步改为对象形态（未删断言）。

### 补充 B：TEST-ONLY 受控故障注入（真实运行 detail）

`tests/v16/v16_t3_failure_classes.mjs` 通过真实 `fetchEntrypoint(new Request(...), env)` 驱动 `fetch()` 入口（不是 `runCycle` 直调、不是静态检查），`V45_TEST_TRIGGER="true"`，每个 fault 独立 DB：

```
FAULT_transport_injected_outage_is_unknown_transport_row : true
  {"status":200,"prov":1,"dq":"UNKNOWN","fc":"TRANSPORT","errorCode":"MARKET_TypeError"}
FAULT_http503_injected_is_unknown_http_error_row : true
  {"status":200,"fc":"HTTP_ERROR","errorCode":"MARKET_HTTP_503"}
FAULT_global_host_rejected_as_unverified_market : true
  {"status":200,"errorCode":"MARKET_SOURCE_NOT_VERIFIED","dq":"UNKNOWN","markContracts":"{}"}
FAULT_trigger_false_returns_403_before_fault_handling : true   (status=403)
FAULT_no_fault_path_matches_v15_verified_behavior : true       (dq=VERIFIED, fc=NONE)
FAULT_unknown_rows_mark_contracts_json_is_object_not_array : true  (["{}","{}","{}"])
FAULT_scheduled_never_reads_fault_static : true
FAULT_src_uses_identity_label_no_forbidden_global_host : true
```

`global_host` 的 ticker item 只带身份标签 `"Binance Global spot"`（无 Global 主机名），被 §3 guard 规则 6 / market 层以 `MARKET_SOURCE_NOT_VERIFIED` 拒绝，产出 UNKNOWN 行且**仍写 provenance**（H8）。`scheduled()` 不读 `fault`；`V45_TEST_TRIGGER !== "true"` 时 403 前置。**这是测试期受控入口，验收后 `V45_TEST_TRIGGER` 置 false，生产路径不可达，不得描述为生产功能**（详见 `V16_MARKET_PROVENANCE_DESIGN.md` §3.9）。

---

## 7. 结论

- V1.5 回归：**8/8 PASS，95 断言，exit 0**（VERIFIED）——旧断言逐字节未动，11/11 文件 sha256 与 `bfb3206` 一致。
- V1.6 新套件：**8/8 PASS，101 断言（101 passed / 0 failed），exit 0**（VERIFIED）——含强制反例、变异验证、`mark_contracts_json` 对象化、TEST-ONLY 三 fault 注入。
- CI 门禁脚本 `python3 scripts/v16_independent_verify.py`（full 模式）在当前态 **9/9 PASS、exit 0**（已备份并恢复 QA 的 `independent_verify.json`，未覆盖队友产出）。
- 过程中真实出现过 1 次 FAIL（新写断言阈值笔误），已如实记录并按「只改实现侧测试断言、不改被测实现、不删断言」原则修复，修复后重跑真绿。
- 以上均为**本地隔离内存 D1**结果；云端 D1 / Worker / E2E = UNVERIFIED（主理人处理）。门禁：Staging D1 接入 / 生产晋级 / 自动交易 = NO-GO；V1.5 = GO 不变。`V45_TEST_TRIGGER` 验收后须置回 false。

---

## 8. 旧测试文件 sha256 逐字节自证（H1）

命令：`git -C D:/jev-v16-wt/v16 show bfb3206:apps/equity_collector_v45/<path> | sha256sum` 与 `sha256sum <worktree path>` 对比。11/11 文件 `match=YES`：

| 文件 | baseline = worktree sha256 |
|---|---|
| `tests/t0_market_probe.mjs` | `4cb109252901550eb7867651c879a94421031158e828b110afbddf101dd59459` |
| `tests/t1_multi_cycle.mjs` | `b3619f37afef0379afe329dd6b144f98793d48ed46dd4134ef2495fc21ff6529` |
| `tests/t2_idempotency.mjs` | `70fb01b36d983a6e6b2c41aefb30cb74414682b2cc50a14ef857f636954b082b` |
| `tests/t3_outage_recovery.mjs` | `98d50ef45e4764d35ecd463ff801e9afd0196ecede76508e449353514965674e` |
| `tests/t4_account_isolation.mjs` | `e115294a878f52d2f65f4333502ad8cf386ba1597f5af030b8cc588e51533cd2` |
| `tests/t5_replay_readonly.mjs` | `f0145e39fa382e7223115e1d926be69c3330fec22297b0e2e4f6dbe175cee5eb` |
| `tests/t6_session_failclosed.mjs` | `9542db9038fedf9221450be0433a71344332e050145c9677caa18ee05a416d9c` |
| `tests/t7_static_audit.mjs` | `bdc9e215fc5a2cefaac569161d53ce5c8b73025e222131c3d3f74c2b71bf08f3` |
| `tests/run-all.mjs` | `d4f250dd6d31bab3bc5618621c6afa29da58f7b48a92ee096d647265d5e25601` |
| `tests/lib/harness.mjs` | `9ab2cfbe51046a2ca0747dbab610be78e58f970e6107a8f295a69945056a2c87` |
| `tests/lib/d1-shim.mjs` | `b0b4002df12954b376f6e07385615ef8312cc2556efc1c5cf874b62aba50c53c` |

`core.autocrlf=true`，但 `git show` blob 与工作区文件逐字节一致（均 LF）。`tests/v16/*` 为新增文件，不触碰上述锚定文件。

---

## 9. 附录：QA2-D1 缺陷修复（guard 规则 8 · fail-closed 补强）

- **缺陷来源**：独立 QA 阶段 2（QA2-D1，中等严重）：`resolveNetworkContext()` 返回 `UNKNOWN` 时（既非注入 fetcher 也无 `MARKET_NETWORK_CONTEXT`），guard 未拒绝，`evaluateCycle.ok=true` + `market.dataQuality='VERIFIED'` → 写出 `data_quality='VERIFIED'` 且 `network_context='UNKNOWN'` 的行（fail-open）。QA 用隔离 DB 插入 11 行并直接调用 `runCycle` 复现，非推断。
- **修复**（加法式，不改既有 7 规则、不改 V1.5 任何断言）：
  - `src/market-guard.mjs`：`ERROR_CODES` 新增 `MARKET_CONTEXT_UNRESOLVED`；新增 `validateNetworkContext()`（非字符串/空/`UNKNOWN` → FAIL，failureClass=UNKNOWN）；`evaluateCycle` 在 `market_identity` 之后插入 `network_context` 规则（规则 8）。
  - `tests/v16/v16_t3_failure_classes.mjs`：新增 3 条回归断言——`QA2_D1_unknown_network_context_rejected`、`QA2_D1_missing_network_context_rejected`（undefined 输入）、`QA2_D1_resolved_network_context_passes_rule8`（合法上下文仍通过）。
- **修复后真实结果（主理人独立重跑）**：V1.5 **8/8 / 95 断言 / exit 0（零影响）**；V1.6 **8/8 / 104 断言（104 passed / 0 failed）/ exit 0**（V16_T3 组 17→20）。断言只增不减。
- **云端一致性**：E2E 全部周期使用已解析上下文 `cloudflare_pages_edge_runtime_prod`，不受规则 8 影响；修复后已用最终源码 redeploy（`deploy_ok` 结构化判定），入口复查仍 403（见 `reports/evidence/v16/cloud/v16_entry_closed.json`）。
- **QA 复核**：本修复由主理人实施，须由独立 QA 复核签认后方可进入最终 signoff（不自称绿）。

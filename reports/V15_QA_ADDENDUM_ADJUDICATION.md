# JEV V1.5 增补 A1 — 独立 QA「市场混用防护」清单的补录与裁决

| 项 | 值 |
| --- | --- |
| 性质 | 对**已推送** `V15_FINAL_SIGNOFF.md`（提交 `887ac54`）的**增补裁决**；不改写其 §1–§9 原文 |
| 触发 | `market-auditor` 的只读复核清单在最终 Signoff 推送后落地，此前**未入仓、未进入 PR #5 证据链** |
| 清单 owner | `market-auditor`（P3 独立 QA）：只读复核，未修改 `v15-dev` 任何文件、未跑其测试、未写云端、未触碰凭据 |
| 裁决 owner | 主理人 / 交付总监（本轮） |
| 清单入仓路径 | `reports/V15_QA_MIXING_GUARD_CHECKLIST.md`（逐字节复制，341 行 / 33,119 字节） |
| 清单 sha256 | `236589723c6d83fe6dbc713138d8288c16cb0ee72ab0467566860b3527d6ca57` |
| 机器可读证据 | `reports/evidence/V15_QA_ADDENDUM_EVIDENCE.json` |

## TL;DR

| 项 | 裁决 |
| --- | --- |
| **主决策 GO / NO-GO** | **不变**：V1.5 安全治理与 CI 加固 = GO；接入 `jev-paper-staging` = NO-GO；生产晋级 = NO-GO |
| **P3 精度修正** | 由「GO（核查完成）」拆为两维：**市场覆盖核查 = GO**；**「不得无标识混用」合规判定 = UNVERIFIED / NO-GO** |
| **BLOCKED 计数** | **9 → 10**。新增 #10 是 #2 的**派生项，不是独立新缺陷**，单列只为让门禁可判定，不虚增问题数 |
| **现有 8/8 PASS、95 断言** | **有效但不充分**：既**不判 FAIL**、不回退，也**不能写成已满足合规**；不删、不放宽、不重写任何断言 |
| **是否本轮补 schema 字段** | **否**。登记为「接入 Staging 前必须清零」的前置门禁，见 §5；理由见 §4 |
| **是否触发新 CI** | **否**。纯文档 / 证据增补，`[skip ci]`；最新 CI Run 仍为 `42563795444` |
| **是否改冻结引擎 / 隔离库 / 已验收测试** | **全部否** |

> 一句话：上一版 Signoff 没有撒谎（§8 #2 已登记 7 个字段缺口），但**证据链缺了那份独立 QA 复核**，且 P3 的 `GO` 容易被读成「混用规则已达标」。本增补补齐证据并收紧措辞，**不改主决策**。

---

## 1. 时间线与证据链（可复现）

| # | 时刻（+08:00） | 事件 | 证据 |
| --- | --- | --- | --- |
| 1 | 12:50:40 | `reports/V15_FINAL_SIGNOFF.md` 落盘 | 文件 mtime |
| 2 | 12:51:00 | 提交 `[skip ci] V1.5: final signoff` → `887ac545dbbe6ffb4470d5ab0b5080a505bc3cf6` | `git log -1 --format=%cI` |
| 3 | 12:51 后 | 推送；PR #5 `head.sha = 887ac54…`，`draft=true`、`state=open` | GitHub `GET /pulls/5` → 200 |
| 4 | **12:54:48** | `MARKET_MIXING_GUARD_CHECKLIST.md` 才在 `D:/jev-v15-wt/v15-market/` 生成（**晚 3m48s**） | 文件 mtime |
| 5 | 本增补 | 清单原样入仓 + 本裁决 + 机器证据 + Signoff §10 增补 + PR #5 正文同步 | 本文件 |

**结论**：Signoff §1–§9 是在清单**不存在于证据链**时形成的。按「零伪造 / 不追改历史结论」原则，不重写该快照，而以 §10 增补方式显式修正精度。

---

## 2. 逐项裁决（QA 清单 → 主理人定性）

### 2.1 团队点名的 7 个字段缺口（`MARKET_MIXING_GUARD_CHECKLIST.md` §6.1.1）

| # | 字段 | 现状 | 裁决 | 归属 |
| --- | --- | --- | --- | --- |
| 1 | `exchange` | 无独立列，仅 `source_host` 隐含 `api.binance.us` | **BLOCKED / 待补字段** | 原 §8 #2 |
| 2 | `market` | 无独立列，`spot` / `perp` 未显式 | **BLOCKED / 待补字段** | 原 §8 #2 |
| 3 | `base_asset` | 无 | **BLOCKED / 待补字段** | 原 §8 #2 |
| 4 | `quote_asset` | 无 | **BLOCKED / 待补字段** | 原 §8 #2 |
| 5 | `price_unit` | 无 | **BLOCKED / 待补字段** | 原 §8 #2 |
| 6 | `network_context` | 无 | **BLOCKED / 待补字段** | 原 §8 #2 |
| 7 | `book_ts` | 无（collector 未取 `bookTicker`） | **BLOCKED / 待补字段** | 原 §8 #2 |

**当前表已有、不得写成缺失**（QA §6.1.1 明确）：`source_host` / `endpoint` / `source_url` / `price_ts` / `fetched_at` / `freshness_ms` / `freshness_verdict` / `clock_skew_ms` / `data_quality` / `http_status` / `payload_sha256` / `mark_quality` / `error_code` / `engine_sha256` / `cycle_key` 等。

### 2.2 QA 追加的 6 个同源缺口（不新增 BLOCKED 计数）

| 字段 | 风险 | 裁决 |
| --- | --- | --- |
| `symbol` | 只在 `input_marks_json` / `missing_marks_json` 文本内，无列 / 索引 / 约束 | 并入 §8 #2 同源待补 |
| `ts_source`（`price_ts_source`） | `market.mjs:115-118` 三路兜底（`/time` → HTTP Date → **本地时钟**）落库后不可分辨，本地时钟可能冒充交易所时刻 | 并入 §8 #2，**优先级最高**（M1 第 3 列） |
| `book_ts_source` | 无盘口时刻来源标注 | 并入 §8 #2 |
| `failure_class` | 传输失败 / 非 2xx / 交易所业务码三类仅靠 `error_code` 字符串前缀区分 | 并入 §8 #2（低成本） |
| `unit_multiplier` | `1000SATSUSDT` = 1000 倍单位语义靠 `VENUE_UNSUPPORTED_SYMBOLS` 白名单规避，**风险被绕开但未被建模** | 并入 §8 #2；接入 Global 前必测（QA-U1~U5） |
| `venue` | `constants.mjs:18` 的 `MARKET_VENUE="Binance.US spot (official public REST v3)"` 未被 `provenance.mjs` 落库 | 并入 §8 #2（成本最低修复点） |

### 2.3 规则可判定性（新增 §8 #10）

| 维度 | QA 判定 | 主理人裁决 |
| --- | --- | --- |
| 单源硬编码 / host allowlist / fail-closed / 不回写 / 幂等 / 账户隔离 / 冻结兼容 | **PASS**（8/8、95 断言 + 云端 12 行隔离 D1） | **VERIFIED**，保持 |
| 三类失败的**行为**区分（不伪造、不兜底、`UNKNOWN` 隔离） | **PASS**（T3） | **VERIFIED**，保持 |
| 三类失败的**结构化分类** | GAP | BLOCKED（并入 #2 `failure_class`） |
| `price_ts` 可审计性 | GAP | BLOCKED（并入 #2 `ts_source`） |
| 1000SATS 单位显式化 | GAP | BLOCKED（并入 #2 `unit_multiplier`） |
| **「不得混用」规则的可判定性** | **GAP**：缺 `(exchange, market)` 组合键，SQL 层无法禁止跨市场聚合 | **UNVERIFIED / NO-GO** → 登记为 **§8 #10**（#2 的派生项） |
| **现有测试对硬规则的覆盖** | **GAP**：`provenance_complete_fields` 未断言市场标识字段 | **UNVERIFIED**：8/8 全 PASS **不能证明**合规 |

> 关键口径：`source_host` / `source_url` / `MARKET_VENUE` **不能替代**独立 `exchange` + `market` 组合键（QA §6.1.1 第 3 条、§6.2 第 1 条）。当前实现只有单一 `api.binance.us` 主源、从未接入 `data-api.binance.vision`、无需回退任何内容 —— 这是**事实上的单市场**，但不是**可证明的单市场**。

### 2.4 P3 报告措辞（QA §6.4 / §5 T0）

| QA 建议 | 裁决 |
| --- | --- |
| `wrangler.toml.example` 中 `"wrongly lists ORDIUSDT/1000SATSUSDT"` 宜改为「它们不属于 Binance.US、但在 Binance Global spot 合法上市」 | **采纳为措辞建议**；本轮不改配置文件（无需改行为，避免无谓重跑），登记在案 |
| `local_live_primary="BLOCKED"` 必须限定为 `network_context='local_sandbox_proxy_52498'` | **已在 `V15_MARKET_COVERAGE.md` / `V15_MARKET_EDGE_CONTEXT.md` 三上下文模型中体现**；QA-T0-1 作为后续断言建议保留 |
| `production_edge_live` 需带「主理人提供 / 未独立复现」标注 | **已有**（边缘证据由主理人采集，`V15_MARKET_EDGE_CONTEXT.md` 明确 owner 与语境），保持 |

---

## 3. 三态口径（严禁混淆）

| 态 | 含义 | 本轮适用 |
| --- | --- | --- |
| `VERIFIED` | 有独立可复现证据支撑 | P0 轮换、P1 差异回归、P2 行为层 8/8 + 云端 12 行、P3 三上下文行情核查 |
| `UNVERIFIED` | **规则目前不可判定**，既非通过也非失败 | 「不得混用不同交易市场行情」合规判定 |
| `BLOCKED / 待补字段` | 需补齐字段与断言后可再判定，非自动 FAIL | 7 个必补字段 + 6 个同源缺口 |

**禁止**：把 8/8 PASS 写成合规达标；把字段缺口写成实现 FAIL 并回退已验收成果；把本地 A1 传输失败泛化为交易所不可用或未上市；把 Binance Global spot 价格与 Binance.US 拼成同一权益曲线。

---

## 4. 本轮明确不做的事（及理由）

| 不做 | 理由 |
| --- | --- |
| 不改 `v45_snapshot_provenance` schema、不改 collector 源码 | 加列必须**同步**扩展 T1 `provenance_complete_fields` 与 T2 `all_fields_identical_across_3_runs` 的断言列集合，否则 QA 原文所说「加列后测试形同虚设」。这属于**新开发 + 新测试周期**，不是文档补充 |
| 不删 / 不放宽 / 不重写任何已通过的断言 | 沿用 T7 事件的处理原则「修检查器、不删断言」；本轮不制造「改完就自称绿」 |
| 不重复执行已通过的测试 | 用户硬要求「不重复执行已通过的所有测试」；本增补零代码变更，故零重跑 |
| 不改冻结表 `paper_equity_snapshots` / 冻结引擎 15 文件 | 硬门禁；新增列只能加在 `v45_snapshot_provenance` |
| 不接入 Binance Global spot 候选来源 | P3 已判 CONDITIONAL / NO-GO；接入前 QA-U1~U5、QA-M1~M7 全部成为必测项 |
| 不接入 `jev-paper-staging`、不碰真实资金 | P2 已判 NO-GO / 仅评估 |
| 不重写 Signoff §1–§9、不 `amend`、不 force push | 保留完整历史与证据链，以 §10 增补方式修正 |
| 不触发新 CI 并把它计入 PASS | 纯文档 / 证据，`[skip ci]`；最新 CI 仍为 `42563795444` |

---

## 5. 前置门禁：接入 Staging 前必须清零（M1 最小充分集）

> 前提：**冻结表 `paper_equity_snapshots` 不可改**，以下只加在 `v45_snapshot_provenance`（或在 M2 用逐 symbol 子表）。

| # | 新增列 | 定义 | 解决 |
| --- | --- | --- | --- |
| 1 | `exchange TEXT NOT NULL` | `CHECK (exchange IN ('Binance.US','Binance Global spot'))` | 市场标识主体，与 `source_host` 一致性须断言 |
| 2 | `market TEXT NOT NULL` | `CHECK (market IN ('spot','perp'))` | 与 1 组成 `(exchange, market)` 分区键，使 QA-M1/M2/M6 可判定 |
| 3 | `price_ts_source TEXT NOT NULL` | `CHECK IN ('exchange_serverTime','trade_time','http_date','received_at')` | 禁止本地时钟冒充交易所时刻 |
| 4 | `network_context TEXT NOT NULL` | 如 `cloudflare_pages_edge_runtime_prod` / `local_sandbox_proxy_52498` | 连通性结论适用域，避免本地 / 边缘互推 |

**+ payload 约定（不加列）**：`input_marks_json` / `marks_json` 每个 symbol 携带 `base_asset` / `quote_asset` / `price_unit` / `unit_multiplier`（`1000SATSUSDT=1000`，其余 `=1`）。

**同步必做（否则加列无效）**：QA-T1-1 扩展断言列集合、QA-T2-1 把新列纳入三次重跑比较、QA-T3-1 三类失败结构化、QA-T3-5 host allowlist 拒绝 Global 响应、QA-T7-2 静态比对 schema 列集合与字段契约。

**升级条件**：若主理人决定接入候选来源或多 symbol 单位不同，按 QA §6.3 的 M2 逐 symbol 子表 `v45_mark_provenance` 实施，并把 `only_allowed_tables_written` 白名单同步覆盖子表（QA-T5-3）。

**范围裁决**：M1 / M2 **不纳入 V1.5 已签收范围**，登记为 **V1.6 或「接入 Staging 评审」的前置门禁**。理由：P2 接入本轮已判 NO-GO，无时间压力；且未经独立 QA 复验就改代码会构成自证绿。是否提前启动由主理人决定。

---

## 6. 对已推送 Signoff 的精度修正表（§1–§9 原文保留）

| 位置 | 原文 | 修正后（以本增补为准） |
| --- | --- | --- |
| TL;DR「P3 真实市场覆盖」 | `GO（核查完成）`；新增 Global 来源接入 `NO-GO / CONDITIONAL` | **市场覆盖核查 = GO（不变）**；**「不得无标识混用」合规判定 = UNVERIFIED / NO-GO（新增）** |
| TL;DR 统计行 | `PASS 24 / FAIL 0 / BLOCKED 9` | `PASS 24 / FAIL 0 / **BLOCKED 10**`（**PASS 数不增加**；新增项为派生门禁，非新完成项） |
| §1 交付物清单 | 未列 QA 清单 | 增加 `reports/V15_QA_MIXING_GUARD_CHECKLIST.md`、本裁决、`reports/evidence/V15_QA_ADDENDUM_EVIDENCE.json` |
| §8 #2 owner | 「独立 QA 建议；V1.5 记为待补 schema，接入前必须补齐」 | 精确到 `reports/V15_QA_MIXING_GUARD_CHECKLIST.md` §6.1.1（sha256 `23658972…`）；含 7 必补 + 6 同源缺口 |
| §8 新增 #10 | — | **HIGH**：「不得混用」规则当前**不可判定**（8/8 PASS 不能证明合规）；#2 派生项；清零条件见本文件 §5 |
| §9「GO WITH CONDITIONS」条件 | 补齐 #2 溯源字段、完成 #3 接入评审、保持 #4/#5 BLOCKED | 追加：**#10 合规可判定性必须随 #2 一并复验，且由独立 QA 复核** |
| §9 生产晋级 | NO-GO（blocker 未清零 #1–#9） | NO-GO（**#1–#10** 未清零），结论不变 |

---

## 7. 决策权与移交

1. **本增补只补证据与措辞**，不改变任何已验收代码、测试、D1 数据、Token、Secret、CI 结论。
2. §5 的 M1 是否提前到本轮实施、M2 是否随 Global 来源一起评审、以及 Global 来源是否接入 —— **决策权归主理人**；本裁决给出推荐（登记为前置门禁，不提前实施）与理由。
3. 若主理人决定立即实施 M1，须作为一个**独立变更**走：新分支 → schema 迁移 → 新增断言（不删旧断言）→ 隔离库复验 → 独立 QA 复核 → 新证据；**不得**把结果回填进 V1.5 已签收的 8/8 / 12 行 / 95 断言口径。
4. 生产晋级仍为 **NO-GO**：#1–#10 未清零，30 天真实研究样本仍 0，Staging D1 权益表仍 0 行。

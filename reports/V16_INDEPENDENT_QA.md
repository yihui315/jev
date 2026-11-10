# JEV V1.6 — 独立 QA 报告（阶段 1，含 `--mode` 接口修订）

| 项 | 值 |
|---|---|
| 角色 | 独立 QA（owner：验收层，只读） |
| 工作区 | `D:/jev-v16-wt/v16` |
| 基线提交 | `bfb320603367b2e8ec7b01f96864312f63ad2e6a`（简写 `bfb3206`） |
| 校验脚本 | `scripts/v16_independent_verify.py`（Python 3，仅标准库，支持 `--mode baseline\|full`） |
| 证据 JSON | `reports/evidence/v16/independent_verify.json`（仓库根） |
| QA 报告 | `reports/V16_INDEPENDENT_QA.md`（仓库根） |
| 本阶段性质 | 基线完整性 + 静态契约检查的**中间态快照**，不是 V1.6 最终验收 |

---

## 1. 验收方法与边界

- 我是**独立 QA**，隶属验收层，**只读**。不实现业务代码，不修改 `apps/equity_collector_v45/` 下的任何实现文件、源码、schema 或测试，**不替工程师签字**。`git status` 中该目录下的改动均为工程师并行产出，非我所为。
- 阶段 1 **不运行**两套测试套件（V1.5 `tests/run-all.mjs` / V1.6 `tests/v16/run-all.mjs`）：源码正在并行变动，此时跑出的测试结果无效，禁止记录为证据。本阶段**未产生任何测试 PASS 数字**。
- 不 `git commit` / `push`，不碰 PR #5，不接云端，不写凭据明文；敏感串命中只记录「文件 / 行号 / 模式名」。
- 只在自己的三个文件内工作：`scripts/v16_independent_verify.py`、`reports/V16_INDEPENDENT_QA.md`、`reports/evidence/v16/independent_verify.json`，不与工程师争抢同一文件。
- **脚本接口（按 team-lead 追加约定修订）**：
  - `--mode baseline`：只跑 **4 项**（旧测试逐字节 sha256 / 旧断言总数 95 / 冻结引擎 tree sha256 / 冻结 DDL 对基线一致），**不依赖任何 V1.6 新文件**，**不得出现 PENDING**；全 PASS → `exit 0`，有 FAIL → `exit 1`。
  - `--mode full`（默认，无参数时等价）：跑契约全部 **9 项**；全 PASS → `0`，有 FAIL → `1`，仅 PENDING 无 FAIL → `2`。
  - 仓库根由 `Path(__file__).resolve().parents[1]` 推导，**无硬编码盘符**，Linux / GitHub Actions runner 可用；所有 git 调用 `git -C <root>`。
  - **浅克隆降级**：`git cat-file -e bfb3206^{commit}` 失败时，依赖 `git show bfb3206:<path>` 的检查一律标 `FAIL` 并写明原因，**绝不标 PASS**；baseline 模式此时 4 项一律 FAIL。
  - 9 项各自独立 `try/except`；三态 `PASS` / `FAIL` / `PENDING`（PENDING = 依赖的交付物尚未存在或尚未改造，**未验证，不是通过**）。
- **检查 2 判定口径（按 team-lead 裁定修订）**：`95` 是运行时 `RESULT_JSON` 的 checks 总数，不是静态 `R.check(` 数。主判据读 V1.5 运行时 evidence `summary.json`（`overall=='PASS'`、`counts={total:8,pass:8,fail:0}`、`sum(len(results[].checks))==95`）；辅判据为静态 `R.check(` 计数与 `bfb3206` 基线逐文件相等（配合检查 1 的逐字节 sha256，证明断言既没被删也没被放宽）。**两个判据都满足才 PASS，任一不满足标 FAIL 并在 detail 写清差异**；不再把「与 95 不等，属口径差异」写成 PASS。
- **检查 4 基线兼容**：baseline 模式两方必需（当前 `schema.sql` 的 `paper_equity_snapshots` 块 vs `bfb3206:schema.sql` 同名块），`0001_v45_base.sql` 为可选第三方；若存在，额外验证其**全文逐字节等于**基线 `schema.sql` 全文并做三方比对。full 模式要求三方齐全。
- **重要**：第 2 节的所有 PASS 都是**脚本本次运行时刻的静态/字节级快照结论**，其中检查 9 只验证「文件是否存在」，**不含任何测试执行**。工程师交付完成、源码稳定、两套测试套件产出独立证据链之后，必须由主理人重新指派阶段 2 并**重跑**，方可形成验收结论。阶段 1 的**任何** PASS、`exit code = 0` 都**不构成** V1.6 实现验收，也不是签字放行。

---

## 2. 阶段 1 基线完整性实测结果

### 2.1 运行记录（真实运行，两种模式）

```
$ python scripts/v16_independent_verify.py --mode baseline
=== JEV V1.6 独立校验（mode=baseline）===
[PASS] 1. V1.5 旧测试逐字节不变（H1）
       11/11 个旧测试文件与基线逐字节一致
[PASS] 2. 旧断言总数 = 95（H3，运行时 evidence + 静态计数双判据）
       主判据运行时 evidence=通过（来源 apps/equity_collector_v45/evidence/summary.json，断言 95，overall=PASS）；辅判据静态 R.check( 92 vs 基线 92=相等；两个判据都满足 -> PASS
[PASS] 3. 冻结引擎零改动（H2，tree sha256 + 15 文件）
       15 文件, tree_sha256=aec5d1b7cc26… 与锚点一致
[PASS] 4. 冻结权益快照表 DDL 对基线逐字节一致（H2）
       三方 DDL 逐字节一致; 0001 全文==基线 schema.sql 全文=True
合计: PASS=4  FAIL=0  PENDING=0
退出码: 0
JSON: D:/jev-v16-wt/v16/reports/evidence/v16/independent_verify.json
BASELINE_EXIT=0
```

```
$ python scripts/v16_independent_verify.py --mode full
=== JEV V1.6 独立校验（mode=full）===
[PASS] 1. V1.5 旧测试逐字节不变（H1）
       11/11 个旧测试文件与基线逐字节一致
[PASS] 2. 旧断言总数 = 95（H3，运行时 evidence + 静态计数双判据）
       主判据运行时 evidence=通过（来源 apps/equity_collector_v45/evidence/summary.json，断言 95，overall=PASS）；辅判据静态 R.check( 92 vs 基线 92=相等；两个判据都满足 -> PASS
[PASS] 3. 冻结引擎零改动（H2，tree sha256 + 15 文件）
       15 文件, tree_sha256=aec5d1b7cc26… 与锚点一致
[PASS] 4. 冻结权益快照表 DDL 对基线逐字节一致（H2）
       三方 DDL 逐字节一致; 0001 全文==基线 schema.sql 全文=True
[PASS] 5. v2 新列与 CHECK/NOT NULL 约束存在（§1.2）
       8 列存在=True, 全部 NOT NULL=True, 关键列 CHECK=True
[PASS] 6. V1.6 新增/修改文件敏感串扫描（H7）
       扫描 30 个 V1.6 新增/修改文件, 命中 0 处
[PASS] 7. src 顶层禁止 Global 主机名（H4）
       可执行代码中 data-api.binance.vision=未出现; "api.binance.us" 字面量=存在
[PASS] 8. 无 console / 无凭据引用 / INSERT OR IGNORE（H6）
       console=无, 凭据引用=无, INSERT OR IGNORE INTO x2, 裸 INSERT INTO 写=0
[PASS] 9. 契约 §4.2 新测试文件齐全（§4.2）
       9 个新测试文件在 apps/equity_collector_v45/tests/v16 下齐全
合计: PASS=9  FAIL=0  PENDING=0
退出码: 0
JSON: D:/jev-v16-wt/v16/reports/evidence/v16/independent_verify.json
FULL_EXIT=0
```

- **默认无参数**另跑一次，输出与 `--mode full` 完全一致（`mode=full`、9 项 PASS、`exit 0`）。
- **exit code**：`--mode baseline` = **0**；`--mode full` = **0**；默认 = **0**。
- **JSON 路径**：`reports/evidence/v16/independent_verify.json`（仓库根），`summary = {PASS:9, FAIL:0, PENDING:0, exit_code:0}`，含 `mode` / `root_derivation` / `baseline_object_available` / `exit_code_semantics` 字段。脚本可重复执行，阶段 1 期间已被多次运行刷新，故本报告**不写死 `generatedAt`**；逐项 detail 以该 JSON 为准。脚本经 `ast.parse` 校验通过，报告中不含硬编码盘符（Linux CI 适用）。
- **运行历史（可证实）**：首轮脚本无 `--mode`；按 team-lead 追加约定与 3 个真问题修订后，复跑两模式均达上述结果。状态演变（早期 `PENDING=1/exit=2` → `PASS=9/exit=0`）源于工程师并行补齐 `tests/v16/` 等文件，**不是 QA 完成更多验证**。
- **增量交付后复跑（可证实）**：v16-engineer 完成增量交付（`mark_contracts_json` 对象化、受控故障注入、evidence 命名改口径）后，独立 QA 再次真实重跑两模式（`generatedAt=2026-11-09T06:40:53Z`）：`--mode baseline` 4 项全 PASS / `exit 0`，`--mode full` 9 项全 PASS / `exit 0`；检查 6 扫描集合由 30 变为 46、仍 0 命中。2.1 的 stdout 块为 `14:36` 那次运行原文，故数字为 30；最新逐项 detail 以 JSON 为准。此复跑**仅为静态/字节级校验脚本的常规重跑**，不运行测试套件、不构成阶段 2 独立验收；工程师自述的 V1.6 `8/8 / 101 断言 / exit 0` 等结果**未经 QA 独立复核**，属工程师旁证。

### 2.2 合计

| 模式 | PASS | FAIL | PENDING | exit code |
|---|---|---|---|---|
| `--mode baseline` | **4** | **0** | **0**（禁止出现） | **0** |
| `--mode full`（默认） | **9** | **0** | **0** | **0** |

### 2.3 逐项实测（基于 `--mode full`，只陈述脚本真实跑出的结果）

| # | 检查 | 状态 | 实测结论 |
|---|---|---|---|
| 1 | V1.5 旧测试逐字节不变（H1） | PASS | 11/11 个文件（8 个 `t0..t7_*.mjs` + `run-all.mjs` + `lib/d1-shim.mjs` + `lib/harness.mjs`）用 `git show bfb3206:<path>` 与当前文件逐字节比 sha256，全部相等。 |
| 2 | 旧断言总数 = 95（H3） | PASS | **主判据**：本轮 `apps/equity_collector_v45/evidence/summary.json` → `overall=PASS`、`counts={total:8,pass:8,fail:0}`、`sum(len(results[].checks))=95`（分组 6/14/8/11/14/20/8/14）。**辅判据**：静态 `R.check(` 当前 92 == 基线 92，逐文件相等（6/14/8/11/14/17/8/14）。两个判据都满足 → PASS。差额 3 条来自 t5 的 `for` 循环（1 个静态调用按 4 个 `ENGINE_ANCHOR_PATHS` key 动态生成 4 条断言）。 |
| 3 | 冻结引擎零改动（H2） | PASS | 15 文件；`tree_sha256 = aec5d1b7cc26db50736d355e7ef14e066fae3f2c2a376d94637c9a977008a9ad`，与锚点一致。 |
| 4 | 冻结快照表 DDL 对基线一致（H2） | PASS | `0001_v45_base.sql` / 当前 `schema.sql` / `bfb3206:schema.sql` 三方的 `CREATE TABLE ... paper_equity_snapshots (...);` 块 sha256 均为 `dab2d796fee3301776f4206cff3810ee73268e8f82fcf677da9bc5c0b8105b45`；且 `0001` **全文逐字节等于**基线 `schema.sql` 全文（4943 字节）。 |
| 5 | v2 新列与约束（§1.2） | PASS | 8 个新列全部存在且全部 `NOT NULL`；`exchange / market / price_ts_source / network_context / failure_class / market_partition_key` 6 个关键列带 `CHECK`。 |
| 6 | 敏感串扫描（H7） | PASS | 扫描当次运行集合内全部 V1.6 新增/修改文件（数量见 JSON `v16_changed_files`，随工程师并行产出而变；2.1 记录为 30，增量交付后复跑为 46），`ghp_` / `github_pat_` / Cloudflare token 赋值 / `PAPER_LEDGER_SECRET` / `api_key =` 等 6 类模式命中 **0** 处；QA 自身 3 个产出已排除，工程师的 V1.6 evidence JSON 已纳入扫描。 |
| 7 | src 顶层禁止 Global 主机名（H4） | PASS | 剥离注释后顶层 `src/*.mjs`（不含 `vendor/`）可执行代码中**未出现** `data-api.binance.vision`；`"api.binance.us"` 字面量**存在**。 |
| 8 | 无 console / 无凭据 / INSERT OR IGNORE（H6） | PASS | 剥离注释后无 `console.(log\|info\|warn\|error)(`、无凭据引用；`INSERT OR IGNORE INTO` 出现 2 处，无裸 `INSERT INTO <表>` 写语句。 |
| 9 | 契约 §4.2 新测试文件齐全 | PASS（**仅文件存在性**） | 8 组 `v16_*.mjs` + `run-all.mjs` 共 9 个文件全部出现在 `apps/equity_collector_v45/tests/v16/`。**此 PASS 只代表 9 个 `.mjs` 文件存在，不代表测试被执行、不代表断言通过、不代表 evidence JSON 已产出、不代表独立证据链成立。** |

### 2.4 必须随结果一并阅读的边界说明（可证实，非结论预填）

1. **两模式全 PASS / `exit=0` 不是 V1.6 验收通过**：它们只是脚本本次运行时刻的静态/字节级结论。本阶段**未运行**任何测试套件，**未产生任何测试 PASS 数字**；契约 §4.2 要求的每组 `checks:[{name,pass,detail}]` 与 `reports/evidence/v16/` evidence JSON 是否真实产出、断言是否通过，本阶段**一概未验证**。
2. **检查 9 的 PASS 极易被误读**：只数文件是否存在。9 个文件由工程师并行创建，内容可能仍在变动；文件存在 ≠ 测试通过 ≠ 反例被拒绝 ≠ 无自证绿。
3. **源码在并行变动**：阶段 1 期间脚本结果从 `PENDING=1 / exit=2` 演变为 `PASS=9 / exit=0`，仅因工程师补齐了文件。任意单次快照都不具验收效力，阶段 2 必须重跑。
4. **检查 2 口径已按裁定修订**：`95` 为运行时 checks 总数；旧实现曾把「静态 92 与 95 不等属口径差异」写成 PASS（弱断言），已改为运行时 evidence 主判据 + 静态计数辅判据双判据，任一不满足即 FAIL。当前两判据均满足。
5. **CI 回落语义**：`--mode baseline` 在 CI 第一步运行时，本轮 `apps/equity_collector_v45/evidence/summary.json` 可能尚未生成，脚本回落读 V1.5 归档 `reports/evidence/equity/local/summary.json`（被 git 跟踪，同为 8/8 PASS、95 断言）。归档只能证明基线断言数量/措辞，**不能证明本轮源码的运行时结果**；本轮运行时结果由 CI 的 V1.5 套件步骤及阶段 2 独立重跑验证。脚本在 detail 中以 `runtime_evidence_is_current_run=false` 与 `runtime_evidence_caveat` 明确标注。
6. **检查 3 路径发现**：契约/任务的 `server/engine/` 为简写；git pathspec 相对 repo 根，真实冻结引擎在 `apps/command_center_3d/server/engine/`（15 文件）。脚本按真实路径、`ls-tree` 原始行用 `\n` 连接（末尾不带换行）算 sha256，命中锚点。
7. **检查 5 校验边界**：仅验证「列存在 + `NOT NULL` + `CHECK` 关键字」，**未**核对 CHECK 值域内容、`DEFAULT`、`0002` 回填语义、迁移 5 步、legacy 表保留、历史行不计入 peak；均属阶段 2 待验。
8. **检查 6 扫描对象**：`git diff bfb3206..HEAD` + 暂存 diff + 未跟踪文件，仅精确排除 QA 自身 3 个文件；命中 0 仅表示本次集合内未发现所列模式，最终交付集阶段 2 需重扫。
9. **浅克隆降级（已单元验证）**：模拟 `bfb3206^{commit}` 不可用时，依赖基线的 5 项检查全部返回 FAIL 并写明原因；`--mode baseline` 的 `main()` 输出 4 项全 FAIL、无 PENDING、`exit 1`，**绝不误放绿灯**。
10. **CI 工作流与指令不一致（需 team-lead 处理，我未改该文件）**：当前 `.github/workflows/v16-market-provenance.yml` 第 37 行调用 `python3 scripts/v16_independent_verify.py`（**无 `--mode`**，等价 full），且第 36 行注释写 "It accepts no mode flag by design"。若 CI 运行时 V1.6 交付物未全部提交，full 模式会产生 PENDING → `exit 2` → 门禁转红。请按约定改为 `--mode baseline`，或确认 full 运行时所有 V1.6 文件已提交。
11. **`independent_verify.json` 为共享文件**：阶段 1 已被独立 QA 与队友多次重跑刷新（含本次两模式 + 单元验证），引用时须以 JSON 的 `generatedAt` / `mode` 对齐。

---

## 3. 待工程师交付后待验项

> 本节**只列阶段 2 要做的事**，不预填任何结论、不预填任何 PASS 数字。交付物文件已出现在工作区，但**以下验证尚未进行，均为未验证**事项。

- **独立重跑两套套件**：在加固后的源码上独立执行 V1.5 `tests/run-all.mjs` 与 V1.6 `tests/v16/run-all.mjs`，核对是否达到契约要求的 V1.5 `8/8 PASS` 与 `95` 断言，并确认证据是本轮新产生（不得复制 V1.5 旧证据冒充）。
- **逐条核对新断言名**：核对 `checks[].name` 与契约 §4.2 覆盖项一一对应，确认无删断言、无改条件、无跳过失败场景；并核对 V1.6 新套件当前自述的 **101 条断言**（较先前 +9：+1 对象形态断言、+8 受控 fault 断言；fault 断言加在已有 T3 组、新套件仍为 8 组）、`summary.json` 字段结构及 evidence 命名口径（runner `reports/evidence/v16/<ID>_<label>.json`、测试自写 `group_v16_*.json`）。
- **核对 7 类 fail-closed 错误码**：核对契约 §3 的 7 类条件与对应 `errorCode` / `failure_class` 映射是否真实生效。
- **核对反例确实被拒绝**：核对 `v16_negative_mixed_cycle.mjs` 的两个强制反例（同周期 US + Global 双市场注入 → `MARKET_MIXING_REJECTED`；DB 预置 Global VERIFIED 高权益行 → 被拒绝且 peak 不被污染）确实被拒绝，而非靠单一来源自然通过。
- **核对 0002 回填为 UNKNOWN**：历史行 8 个新列全部 `UNKNOWN` / `HISTORY_UNPROVEN`，legacy 表保留不 DROP，历史行不计入 peak；不得用 `Binance.US` / `spot` / `exchange_serverTime` 回填。
- **核对 peak 分区 SQL**：核对 `src/partition.mjs` 的 `selectHistoryPeak` 与契约 §3.4 SQL 一致，且 `collector.mjs` 旧的 `SELECT MAX(equity) ... WHERE mark_quality='verified'` 已被替换。
- **核对无自证绿**：核对 runner 从真实子进程 stdout / exit code 采集 `RESULT_JSON`，不合成结果、不吞失败。
- **复核 `mark_contracts_json` 落库内容**（team-lead 冒烟发现曾落成 `"[]"`）：核对是否为以 symbol 为键的对象、逐 symbol 完整契约，且 `marks_json` 仍为冻结的纯价格 map 字符串格式。
- **复核生产 `MARKET_NETWORK_CONTEXT` 配置**（工程师同步）：核对生产边缘是否显式配置为 `cloudflare_pages_edge_runtime_prod`；漏配会按设计让所有周期降级 UNKNOWN（设计行为，非 bug，但影响生产可用性）。
- **核对 CI 工作流 mode 一致性**：核对 `.github/workflows/v16-market-provenance.yml` 是否按约定使用 `--mode baseline`（见 2.4 第 10 条）。
- **独立 QA 补充待验（供主理人裁定）**：① 核对 `0001_v45_base.sql` 全文逐字节等于 `bfb3206:apps/equity_collector_v45/schema.sql`（阶段 1 已静态验证通过，阶段 2 复核）；② 核对 8 个新列 `CHECK` 值域与 `DEFAULT` 的精确内容；③ 在工程师最终交付集上以 `--mode baseline` 与 `--mode full` 各重跑一次并复核所有状态演变。

---

**阶段 1 结论边界**：以上仅为独立 QA 阶段 1 的脚本与实测记录。无论阶段 1 早期的 `PENDING=1` 还是当前的 `PASS=9 / exit 0`（或 `baseline` 的 `PASS=4 / exit 0`），**都禁止误读为 V1.6 验收通过**：本阶段未运行任何测试、未核对任何断言、未建立独立证据链。阶段 2 必须由主理人在工程师交付完成、源码稳定后**重新指派**，由 QA 独立重跑两套测试套件并逐条核对上述待验项，方可形成验收结论；在此之前不得签字放行。

---

## 4. 阶段 2 独立验收实测结果（轨道 A：Market Provenance Hardening）

> 本节由**接任的独立 QA（v16-qa2）**撰写。第 1–3 节为前任 QA 的阶段 1 记录，**未改写**。本节所有数字都标注来源：**VERIFIED = 我本人本次亲自执行/亲自查询**；**UNVERIFIED = 采信他人产出，不构成我签字的依据**。

### 4.0 审计边界与现场快照

| 项 | 值 |
|---|---|
| 审计时刻 | 2026-11-09T13:13:44Z（本地 21:13:44） |
| 工作区 / 分支 | `D:/jev-v16-wt/v16` / `feature/v16-market-provenance-hardening` |
| HEAD | `bfb320603367b2e8ec7b01f96864312f63ad2e6a`（= 基线，**未提交变更即待验收交付物**） |
| 验收范围 | `apps/equity_collector_v45/**` 及其证据（轨道 A）。轨道 B（`apps/command_center_3d/server/ai/`、`docs/`、`reports/V16_AI_*`）**不在本次验收范围，我未改动其任何文件** |
| 我的写入 | 仅 `reports/V16_INDEPENDENT_QA.md`（本节）与 `reports/evidence/v16/qa_phase2_independent.json` |
| 禁止动作执行情况 | 未 `git commit/push`、未改实现/测试/schema、未碰 PR #5、未写凭据明文、云端仅 SELECT/GET、未部署 |
| 证据还原 | 跑测试会覆写 evidence，**已先备份、取证后逐字节恢复**：`reports/evidence/v16/summary.json` 回到 `12:47:00.665Z`，`apps/equity_collector_v45/evidence/summary.json` 回到 `12:46:56.814Z`，`independent_verify.json` 回到原 sha256 |

**证据文件**：`reports/evidence/v16/qa_phase2_independent.json`（本文所有实测数字的机器可读版本）。

### 4.1 清单 1 — 两套套件独立重跑（VERIFIED）

| 套件 | 命令 | 我的 exit code | 组结果 | 断言 | 我的运行时间戳 | 原证据时间戳 |
|---|---|---|---|---|---|---|
| V1.5 | `node apps/equity_collector_v45/tests/run-all.mjs` | **0** | **8/8 PASS** | **95**（6/14/8/11/14/20/8/14） | `2026-11-09T13:02:35.663Z` | `12:46:56.814Z` |
| V1.6 | `node apps/equity_collector_v45/tests/v16/run-all.mjs` | **0** | **8/8 PASS** | **101**（101 passed / 0 failed） | `2026-11-09T13:03:01.025Z` | `12:47:00.665Z` |

- 时间戳晚于原证据且相隔约 16 分钟，**证明是本轮新跑，不是复制旧 evidence**；V1.6 的 8 个子进程 `exitCode` 全为 0、`verdict` 全为 PASS（逐组核对 `reports/evidence/v16/V16_*.json` 的 `exitCode` 字段）。
- 结论：契约 §4.1 / H3 的「8/8 + 95」与 V1.6 的「8/8 + 101」在**加固后源码**上成立，**VERIFIED**。

### 4.2 清单 2 — 101 断言逐组核对（VERIFIED）

| 组 | 断言数 | 覆盖要点 |
|---|---|---|
| V16_T1 | 12 | 8 新列非空/值域、分区键自洽、`source_url` host 与 exchange 一致、**对象形态**（`mark_contracts_json_is_symbol_keyed_object_not_array`、`..._complete_per_symbol`） |
| V16_T2 | 10 | 含新列整行 hash 3 次一致、`network_context` 跨 3 次恒定、改价不回写 |
| V16_T3 | 17 | 3 类结构化失败 + Global host 注入 + **8 条受控 fault 断言** |
| V16_T7 | 15 | 列集合 = `FIELD_CONTRACT`、8 列 CHECK、`0001` 逐字节基线 |
| V16_M | 11 | 分区键、peak 只按 `(exchange,market)`、无 fallback、id/序列稳定 |
| V16_U | 14 | 1000SATS `unit_multiplier=1000`、逆运算、SATS 不并列、BLOCKED |
| V16_MIG | 11 | 历史列全 UNKNOWN/HISTORY_UNPROVEN、legacy 保留、历史不计 peak |
| V16_NEG | 11 | 两个强制反例 |
| **合计** | **101** | 与 `summary.json` 的 `assertions.total=101,passed=101,failed=0` 一致 |

**8 条受控 fault 断言（逐条核对 `v16_t3_failure_classes.mjs` 源码 + evidence detail，VERIFIED）**：
`FAULT_transport_injected_outage_is_unknown_transport_row`、`FAULT_http503_injected_is_unknown_http_error_row`、`FAULT_global_host_rejected_as_unverified_market`（以上 3 条为**真实调用** `fetchEntrypoint(new Request(...), env)`，每个 fault 独立 DB）、`FAULT_trigger_false_returns_403_before_fault_handling`、`FAULT_no_fault_path_matches_v15_verified_behavior`、`FAULT_unknown_rows_mark_contracts_json_is_object_not_array`、`FAULT_scheduled_never_reads_fault_static`、`FAULT_src_uses_identity_label_no_forbidden_global_host`。

- 断言 detail 抽查为真实测量值（非布尔占位）：T1 对象断言 detail 是整段 `mark_contracts_json`；T2 是 3 个相同 sha256；NEG2 是 `peakUS=null / peakGlobal=99000000000000`。
- 观察：`FAULT_global_host_rejected_as_unverified_market` 接受 3 个错误码中的任一（`some(...)`），略宽；本例实际抛出 `MARKET_SOURCE_NOT_VERIFIED`，不影响判定。

### 4.3 清单 3 — 7 类 fail-closed 错误码（VERIFIED）

- 源码 `src/market-guard.mjs:11-19` 的 7 个错误码与契约 §3 表格**逐字一致（7/7）**，`failure_class` 映射亦一致。
- 我另写 13 例 `evaluateCycle` 输入（临时脚本，不落仓库）做**可达性实证**：健康输入 `ok=true`；identity 缺失 / host 不符 → `MARKET_IDENTITY_INVALID`；双市场或 prior 市场 → `MARKET_MIXING_REJECTED`；`received_at` / `UNKNOWN` 时戳 → `PRICE_TS_SOURCE_UNVERIFIED`；单位不符 → `UNIT_CONTRACT_MISMATCH`；STALE / 503 / 无 payload sha → `MARKET_STALE_OR_PAYLOAD_UNVERIFIED`；fallback → `MARKET_SOURCE_NOT_VERIFIED`；历史不可证明 → `HISTORY_PROVENANCE_UNPROVEN`。**13/13 符合预期，7 个码全部命中**，非「定义但不可达」。
- `collector.mjs:115` 真实调用 guard，失败走降级分支（`collector.mjs:147-160`），仍写 provenance（H8）。

### 4.4 清单 4 — 反例确证（VERIFIED，含云端口径说明）

- **本地反例①**（同周期双市场）：注入 `BTCUSDT`(US) + `1000SATSUSDT`(Global) → `verified=0 / unknown=1 / error_code=MARKET_MIXING_REJECTED`，`mark_contracts_json` 保留 2 个市场的原始证据。**VERIFIED**。
- **本地反例②**（哨兵）：预置 Global `equity=99000000000000`（9.9e13）后跑 US 周期 → `MARKET_MIXING_REJECTED`，US peak=`null`，Global 分区 peak=`99000000000000`。**VERIFIED**。
- **云端反例②**：隔离 D1 的 `v16-FIXTURE-mixed` 会话——预置 Global VERIFIED 哨兵行后跑 US 周期 → `MARKET_MIXING_REJECTED`、`equity=0`、`marks_json={}`。**VERIFIED（我直接 SQL 查询）**。
- **口径提醒**：云端只证明了反例②；**反例①（同一周期内两个市场）目前只有本地证据**，云端 C6-global fault 行的 `mark_contracts_json` 只含 `BTCUSDT`（该路径是用 payload 声明外部市场，不是注入第二个 symbol）。不得把云端这一行当作「双市场对象证据」。
- `mutation_check.json`（`usPeak=18244694700000`、无分区变体=`99000000000000`）说明若实现退化断言必 FAIL：**UNVERIFIED（采信工程师）**，但与我读到的 SQL/断言阈值互相印证。

### 4.5 清单 5 — 0002 迁移语义（VERIFIED，我自己离线演练）

方法：QA 自建 `python3 sqlite3` 内存库（`PRAGMA foreign_keys=ON`）→ 执行 `0001` → 插入 1 条 V1.5 历史行 → 执行 `0002`。

| 项 | 实测 |
|---|---|
| 活跃表列数 | **35**（27 + 8） |
| legacy 表 | `v45_snapshot_provenance_v1_legacy` 存在，**27 列**，1 行原样保留（未 DROP） |
| 历史行回填 | `exchange/market/price_ts_source/network_context/venue = UNKNOWN`、`failure_class=HISTORY_UNPROVEN`、`mark_contracts_json={}`、`market_partition_key=UNKNOWN|UNKNOWN`；`data_quality` 保留原 `VERIFIED` |
| 是否伪装 | 未见 `Binance.US` / `spot` / `exchange_serverTime` 回填 |
| 索引 | `idx_v45_prov_session_cycle`、`idx_v45_prov_quality`、`idx_v45_prov_partition` 均重建 |
| 历史行与 peak | 同一行 `mark_quality=verified` 但被分区 SQL 排除（返回 `null`）；旧式无分区 `MAX` 返回 `500000000000` → 排除确有其效 |

补充：`0001` 全文 == `bfb3206:schema.sql` 全文 = **True**，`paper_equity_snapshots` 三方 DDL 逐字节一致（独立校验脚本检查 4 PASS，VERIFIED）。

### 4.6 清单 6 — peak 分区 SQL（VERIFIED）

`src/partition.mjs:15-23` 的 `HISTORY_PEAK_SQL` 含 `p.exchange = ? AND p.market = ?`，并排除 `failure_class <> 'NONE'`（含 `HISTORY_UNPROVEN`）、`price_ts_source` 非 `received_at/UNKNOWN`、`network_context = 'UNKNOWN'`，与契约 §3.4 SQL 语义一致。`collector.mjs:165` 已用 `selectHistoryPeak` 替换旧的 `SELECT MAX(equity) ... WHERE mark_quality='verified'`（旧语句仅存在于冻结 vendor 引擎，未被调用）。

### 4.7 清单 7 — 云端隔离 D1 只读核验（VERIFIED）

只读查询 `997aa8fd-e0c3-4b99-a0c2-b9022f6be5f2`（`L.d1`，仅 SELECT）：

| 事实 | 实测 |
|---|---|
| 快照 / provenance | **34 / 34**（一一对应） |
| 构成 | 10 条周期产出的 US VERIFIED + 23 条 UNKNOWN + **1 条手工预置的对抗日志行** |
| 10 条 US VERIFIED | 全部 `exchange=Binance.US`、`market=spot`、`price_ts_source=exchange_serverTime`、`network_context=cloudflare_pages_edge_runtime_prod`、`failure_class=NONE`、`market_partition_key=Binance.US|spot` |
| 第 11 条 verified | `v16-FIXTURE-mixed` 会话的**手工预置**行：`exchange=Binance Global spot`、`equity=999999999999999`、`network_context=v16_FIXTURE_ADVERSARIAL` → **不是 collector 产出** |
| 混市场周期 | `cycle_key=v16-cloud-C8-mixed`：`error_code=MARKET_MIXING_REJECTED`、`failure_class=MARKET_MIXING`、`data_quality=UNKNOWN`、**`equity=0`**、`marks_json={}` |
| 峰值隔离 | US 分区 `MAX=24989122000000`；Global 分区 `MAX=999999999999999`；**US peak 未被哨兵污染** |
| 分区键自洽 | `market_partition_key <> exchange||'|'||market` 的行数 = **0** |
| schema | 活跃 provenance **35 列**，legacy 表存在（0 行，全新库），3 个索引齐全 |
| 与 `cloud/` JSON 一致性 | `v16_cloud_e2e_evidence.json`：`rows=34 / verified=10 / unknown=23`（其 verified 明确排除 `cycle_key` 以 `v16-FIXTURE` 开头的行）→ **10+23+1=34，口径一致** |

- **必须显式说明的两点**：① `34` 是 **V1.6 隔离库**的行数，`12` 是 **V1.5 库**的行数，两者**不得混入同一统计口径**；② V1.6 库里「11 条 verified」中有 1 条是人工预置的对抗日志，真实 US 产出只有 10 条。
- 云端 24 项 E2E 检查 24/24 PASS：**UNVERIFIED（采信主理人脚本输出）**；其中可通过 SQL 复核的行级事实已由我独立 VERIFIED。
- 意外一致的强证据：`v16_worker_deploy.json` 中 13 个源文件 sha256 与当前工作区**逐字节一致（13/13，我本地重算比对）** → 云端结果确实对应本次审计的源码版本。**VERIFIED**。

### 4.8 清单 8 — 入口关闭（VERIFIED）

| 探针 | 结果 |
|---|---|
| `GET /__v45/collect?cycle_key=QA-PHASE2-PROBE` | **403** `{"ok":false,"error":"TEST_TRIGGER_DISABLED"}` |
| `GET /__v45/collect?cycle_key=QA-PHASE2-PROBE2&fault=transport` | **403** 同码（fault 参数不可达） |
| `GET /health`、`GET /` | 亦 403 同码 —— 该 Worker 只实现 `/__v45/collect` 一个入口，**没有健康检查路由**（不是故障，属实现选择，需在运维文档中写明） |

代码层面：`collector.mjs:257` 的 403 门在解析 pathname / fault **之前**；`scheduled()` 不读 `fault`。

### 4.9 清单 9 — V1.5 库完整性（VERIFIED）

只读查询 `cf5cf033-3796-4de1-b295-93f35a8ce82e`：快照 **12**、provenance **12**、`verified=9 / unknown=3`、provenance **27 列**（**无** `exchange` / `market_partition_key` → 未被执行 0002 迁移）、`created_at` 全落在 `04:38:56Z ~ 04:39:42Z`（V1.5 时期）。与本轮取证前一致，**未被触碰**。

### 4.10 清单 10 — 无自证绿（VERIFIED）

`reports/V16_MIXING_GUARD_TESTS.md` §4 如实保留了 1 次真实 FAIL（`NEG2_global_sentinel_exceeds_normal_us_equity_scale`，阈值笔误 1e14 > 哨兵 9.9e13，当时 7/8 组、91/92 断言、exit 1），修复仅下调阈值为 5e13（仍为正常 US 权益 ~1.8e13 的 2.7 倍），**未删断言、未改被测实现**。§5 还记录了「初版哨兵 9e9 小于 US 峰值会导致自然通过」的测试强度缺陷及修正。runner 用 `spawnSync` 采真实子进程 stdout/stderr/exitCode，无 `RESULT_JSON` 时判 `PASS_NO_RESULT_JSON`（不计入 PASS）。**均属实**。

### 4.11 清单 11 — 独立校验脚本重跑（PARTIAL PASS，如实记录）

```
$ python scripts/v16_independent_verify.py --mode full
[PASS] 1,2,3,4,5,7,8,9
[FAIL] 6. V1.6 新增/修改文件敏感串扫描（H7）
       扫描 75 个 V1.6 新增/修改文件, 命中 2 处
合计: PASS=8  FAIL=1  PENDING=0 ; 退出码 1
```

- **FAIL 归因**：2 处命中都在 `apps/command_center_3d/server/ai/provider-adapter.mjs`（第 205、235 行）——**轨道 B（另一位工程师）并行新增的文件**，模式 `api_key_assignment`。两处均为局部变量的 camelCase 标识符赋值（`apiKey` 取自 `options`），**不是凭据字面量**；属正则 `\bapi[_-]?key\s*=` 对标识符的误命中。
- **轨道 A 定向重扫**（74 个文件）：0 处凭据命中；唯一命中是 `bfb3206` 基线冻结文件 `tests/t7_static_audit.mjs:87` 自身的扫描模式名，非新增泄露。
- 历史运行（`generatedAt=2026-11-09T06:40:53Z`，集合 46 文件）为 9/9、exit 0 —— 本次 FAIL 是轨道 B 新文件引入，**不是轨道 A 回归**，但**也不能写成 9/9 PASS**。
- 已按要求 `cp 备份 → 运行 → cp 恢复`，`independent_verify.json` 恢复为原字节（sha256 `2e5018c0cb16be38…`）。

**复跑（轨道 B 修复后，2026-11-09T13:18:50Z）**：v16-ai-engineer 收到前述误命中通报后，把其代码中的 `apiKey` 标识符改名为 `credential`（`provider-adapter.mjs` / `runtime.mjs` / 相关测试与文档），**未改我的脚本、未改轨道 A 任何文件**（`git status -- apps/equity_collector_v45` 逐项核对无新增变化）。我再次重跑：

```
$ python scripts/v16_independent_verify.py --mode full
[PASS] 1..9（检查 6：扫描 90 个 V1.6 新增/修改文件, 命中 0 处）
合计: PASS=9  FAIL=0  PENDING=0 ; 退出码 0
```

即 **9/9、exit 0（VERIFIED，我亲跑）**；本次结果同样已留档（运行副本 sha256 记录在我的证据 JSON 中），运行后仍把共享文件 `independent_verify.json` 恢复原字节，避免与队友抢写。**结论修正**：清单 11 的首次 8/9 是轨道 B 文件引入的误命中，修复后门禁已转绿；但「CI 用 `--mode baseline` 还是 full」的裁定问题仍然存在（见 QA2-D3）。

### 4.12 缺陷与观察项登记（只上报，未改实现）

| ID | 级别 | 标题 | 要点 |
|---|---|---|---|
| **QA2-D1** | **中（fail-open，条件触发）** | `network_context='UNKNOWN'` 仍可写出 `data_quality='VERIFIED'` 行，违反契约 §3.2「UNKNOWN → fail-closed」 | 我自写探针：`env` 无 `MARKET_NETWORK_CONTEXT` 且不注入 fetcher（模拟生产漏配），`globalThis.fetch` 返回正常 `/time`+ticker → `resolveNetworkContext` = `UNKNOWN`，`runCycle` 仍写 `mark_quality=verified / data_quality=VERIFIED / network_context=UNKNOWN / equity=17495177000000`。根因：`evaluateCycle` 收了 `input.networkContext` 但 7 条规则都没用。峰值被 SQL 兜住（`network_context <> 'UNKNOWN'`），**peak/drawdown 不被污染**；但该行对外标 VERIFIED、`summary.verified` 虚高，且生产漏配时会「静默全 VERIFIED + 峰值恒 null」。当前云端 10 条 US 行均为 `cloudflare_pages_edge_runtime_prod`，**未触发**。建议：guard 增加「network_context 缺失/UNKNOWN → 拒绝」并补 1 条断言（现有 101 条无此覆盖）。 |
| QA2-D2 | 低 | `v16_worker_deploy.json` 的 `deploy_ok=false` 自相矛盾 | 同文件 `wrangler_exit=0`、`script_readback_http=200`，且部署确实生效（13/13 源文件 sha256 与工作区一致、入口 403、E2E 成功）。根因是脚本用 stdout 关键字判定，wrangler 4.x 本次输出未命中关键字。若门禁读该字段会把成功部署判失败。 |
| QA2-D3 | 低（**部分已缓解**） | CI 门禁口径 | `.github/workflows/v16-market-provenance.yml:38` 仍以**无参（full）**调用脚本。原先检查 6 会误命中 camelCase 标识符导致 exit=1，**该误命中已由轨道 B 改名消除，13:18:50Z 复跑 full 模式 9/9 / exit 0**；仅剩「CI 到底用 `--mode baseline` 还是 full」的裁定问题（阶段 1 已提出，仍未裁定，不再阻塞门禁）。 |
| QA2-O1 | 观察 | 规则 7 运行期未接线 | `collector.mjs:130` 以 `historyProvenance: []` 调用，guard 规则 7 永不触发；`HISTORY_UNPROVEN` 的实际排除**完全依赖 partition SQL**。当前语义正确，建议文档写明或后续接线。 |
| QA2-O2 | 观察 | 契约 H8 措辞不精确 | H8 写「equity=0」，但无持仓账户的合法 cash-only 估值下 `equity == cash`、`market_value=0`、`marks_json={}`（云端 8 条 `v16-FIXTURE-cash` UNKNOWN 行即如此）。E2E 已用显式规则区分，建议澄清契约措辞。 |
| QA2-O3 | 观察 | runner 未交叉校验 exitCode | `tests/v16/run-all.mjs:45` 只采信子进程自报 `verdict`；建议改为 `verdict==PASS && exitCode==0` 才计 PASS（当前 8 组两者一致，无实际影响）。 |

---

## 5. 阶段 2 结论（轨道 A）

### 5.1 DECISION：**GO WITH CONDITIONS**

轨道 A 的**代码、schema、迁移、测试体系、云端证据链**在本次独立复核中全部成立：两套套件真跑 8/8（95 / 101 断言，exit 0）、7 类错误码逐字一致且可触发、反例真实生效、迁移语义正确、peak 分区有效、云端 34 行与源码版本一一对应、入口已关闭、V1.5 库零触碰。**没有发现任何会污染 peak/drawdown 的缺陷**。

但存在 1 个中等缺陷（QA2-D1，属契约 §3.2 偏离，条件触发）与 3 个交付/门禁问题，故不能无条件放行。

### 5.2 放行条件

1. **C1（合并主线或开启 cron 前必须完成）**：修复 QA2-D1（`network_context` 为 `UNKNOWN` 时 fail-closed）并补 1 条测试；或由主理人书面裁定接受该风险并写入签收。
2. **C2**：修正 QA2-D2 的部署判定并重出 `v16_worker_deploy.json`，或在签收中注明该字段不可信。
3. **C3**：裁定 CI 使用 `--mode baseline` 还是 `full`（**已不阻塞**：检查 6 的误命中已由轨道 B 改名消除，13:18:50Z 复跑 full 模式 9/9 / exit 0；仅剩口径裁定）。
4. **C4**：最终签收必须写明「隔离 D1 的 11 条 verified 中有 1 条是人工预置的对抗日志行，真实 US 产出为 10 条」；建议验收后销毁该隔离库。

### 5.3 数字口径与验证状态（禁止混淆）

| 数字 | 值 | 状态 | 口径 |
|---|---|---|---|
| V1.5 套件 | 8/8、95 断言、exit 0 | **VERIFIED**（我亲跑） | 本地内存 D1 |
| V1.6 套件 | 8/8、101 断言、exit 0 | **VERIFIED**（我亲跑） | 本地内存 D1 |
| V1.6 错误码 | 7/7 逐字一致 + 13 例可达性 13/13 | **VERIFIED**（我亲测） | 单元级 |
| 迁移演练 | 35 列 / legacy 保留 / 历史行全 UNKNOWN / 不计 peak | **VERIFIED**（我自建 sqlite3） | 离线 |
| 云端 V1.6 隔离库 | 34 快照 / 34 provenance（10 US VERIFIED + 23 UNKNOWN + 1 手工对抗行） | **VERIFIED**（我亲查 SQL） | **V1.6 隔离库，独立口径** |
| 云端 E2E 24 项检查 | 24/24 PASS | **UNVERIFIED**（采信主理人） | 同上 |
| 入口关闭 | 403 `TEST_TRIGGER_DISABLED`（含 fault 探针） | **VERIFIED**（我亲测 HTTPS） | 生产边缘 |
| 部署源码一致性 | 13/13 源文件 sha256 与工作区一致 | **VERIFIED**（我本地重算） | — |
| V1.5 库 | 12 快照（9 verified / 3 unknown）、12 provenance、27 列 | **VERIFIED**（我亲查 SQL） | **V1.5 库，独立口径，不与 34 合并** |
| 独立校验脚本（首跑 13:07Z） | 8 PASS / 1 FAIL / exit 1 | **VERIFIED**（我亲跑，已恢复原文件） | FAIL 归因轨道 B 文件误命中 |
| 独立校验脚本（复跑 13:18:50Z） | **9 PASS / 0 FAIL / exit 0**（90 文件 0 命中） | **VERIFIED**（我亲跑，已恢复原文件） | 轨道 B 改名消除误命中后 |
| 变异实验 `mutation_check.json` | 变体污染 / 真实现不污染 | **UNVERIFIED**（采信工程师） | — |

### 5.4 结论边界

- 本结论只覆盖**轨道 A**，不覆盖轨道 B（AI 轨道）的任何交付。
- 「GO WITH CONDITIONS」指**代码正确性可接受、附带必修项**，**不等于**原契约 §5 的放行门禁：**接入正式 Staging D1 = NO-GO、生产晋级 / 自动交易 = NO-GO、V1.5 = GO 不变**，这三条**未因本次验收而改变**。
- 若 C1 被判定为必须修复后才能合并，则本次结论应降级为 **NO-GO（仅在合并动作上）**；功能与证据链本身无需重做。

---

## 6. 阶段 2.5 — QA2-D1 修复增量复核

> 本节由**第三任独立 QA（v16-qa3）**撰写。第 1–3 节为前任 QA 阶段 1 记录、第 4–5 节为 v16-qa2 阶段 2 记录，均**未改写**。本节是**增量复核**：只复核主理人实施的 QA2-D1 修复（guard 规则 8 + 3 条回归断言）及其关联声明（C2 redeploy、报告口径），**不重做全量验收**，且**不采信主理人自述**——所有 PASS 均为本人在本会话亲自执行的实测；本人无法亲自执行的项明确标注，绝不写成 VERIFIED。

### 6.0 复核边界与现场快照

| 项 | 值 |
|---|---|
| 复核时刻 | 2026-11-09T13:40Z（本地 21:40） |
| 工作区 / 分支 / HEAD | `D:/jev-v16-wt/v16` / `feature/v16-market-provenance-hardening` / `bfb3206`（未提交交付物） |
| 我的写入 | 仅本节 + `reports/evidence/v16/qa_d1fix_verification.json` |
| 证据处理 | 跑套件前先备份 `reports/evidence/v16`（21 文件）与 `apps/equity_collector_v45/evidence`（20 文件）到仓库外 `D:/jev-v16-wt/_qa3_backup`，取证后**逐字节恢复**（restored_files=41、mismatch=0；恢复后 v16 summary `generatedAt=13:20:17.712Z`、v15 summary `13:20:13.757Z`，与跑前一致） |
| 禁止动作执行情况 | 未改任何实现/测试/schema/既有报告内容（仅追加本节）、未 git 操作、未碰 PR、未写凭据、未部署 |
| 机器可读证据 | `reports/evidence/v16/qa_d1fix_verification.json`（12 项 check 逐项 detail） |

### 6.1 清单 1 — 两套套件独立重跑（VERIFIED，我亲跑）

| 套件 | 命令 | 我的 exit code | 结果 | 我的运行时间戳 |
|---|---|---|---|---|
| V1.5 | `node apps/equity_collector_v45/tests/run-all.mjs` | **0** | **8/8 PASS，95 checks**（T0..T7 全 exit=0） | `13:25:11.293Z` |
| V1.6 | `node apps/equity_collector_v45/tests/v16/run-all.mjs` | **0** | **8/8 PASS，104 断言**（104 passed / 0 failed；分组 12/10/**20**/15/11/14/11/11） | `13:25:25.693Z` |

时间戳晚于磁盘上既有证据（13:20，主理人修复后重跑），证明是**本轮新跑**。V16_T3 由 17 → 20，与「+3 条 QA2_D1 断言」的声明一致；断言**只增不减**（其余 7 组断言数与前任 QA 阶段 2 核对的 101 条版完全一致）。Node `v22.22.2`。

### 6.2 清单 2 — D1 场景独立复现（VERIFIED，关键项，不采信自述）

方法：仓库外临时探针 `D:/jev-v16-wt/_qa3_d1_probe.mjs`（不落仓库），用 `tests/lib/harness.mjs` 的 `openDb/makeState/insertSession/insertAccount` 建隔离内存库，**直驱真实 `runCycle`**：env 只有 `{EQ_DB, ENABLE_REAL_TRADING:'false'}`——**既不设 `MARKET_NETWORK_CONTEXT` 也不注入 fetcher**（`globalThis.fetch` 提供正常 `/time` + ticker），与前任 QA 发现缺陷时的复现方法一致。`node _qa3_d1_probe.mjs` → `exit 0`，13/13 全 PASS：

| 探针 | 实测 |
|---|---|
| **P1 负例（原 D1 场景）** | 写出 1 快照 + 1 provenance；`data_quality='UNKNOWN'`、`mark_quality='unknown'`、**`error_code='MARKET_CONTEXT_UNRESOLVED'`**、`failure_class='UNKNOWN'`、**`equity=0`、`marks_json='{}'`**、`network_context='UNKNOWN'`；summary `verified=0 / unknown=1`。**前任 QA 用完全相同输入曾写出 `VERIFIED` + `equity=17495177000000`（fail-open），现在同一输入 fail-closed 且仍写 provenance（H8）** |
| **P2 正例** | `MARKET_NETWORK_CONTEXT='local_test_shim'` → `VERIFIED`、`network_context='local_test_shim'`、`equity=17495177000000`（与修复前 fail-open 的值相同——同价格、现在合法 VERIFIED）、`error_code=null`。规则 8 不伤合法路径 |
| **P3 非恒真证明** | 用导出的 7 个既有 validator 本地重建「修复前的 7 规则评估」，对同一 UNKNOWN-ctx 输入返回 **ok=true**（7/7 全过）→ 新断言确实区分修复前后行为，非恒真 |

### 6.3 清单 3 — 修复为加法式、既有行为未破坏（VERIFIED）

- **源码通读**（`src/market-guard.mjs`）：`ERROR_CODES`（:11-23）中 7 个既有错误码字符串**逐字未动**，`MARKET_CONTEXT_UNRESOLVED` 为带注释的**追加**；`validateNetworkContext` 为**新函数**（:57-63）；7 个既有 validator（:66/:102/:110/:129/:146/:166/:184）逻辑与 failure_class 映射与前任 QA 阶段 2 核对过的契约一致；`evaluateCycle` 只在 `market_identity` 之后插入**一条** `network_context` 规则（:221），位于 `market_mixing` 之前。
- **可达性实证**（我自写 13 例 `evaluateCycle` 探针，13/13）：健康输入 `ok=true` 且 8 项 check 全过（含 `network_context` 通过）；7 个旧码各自触发且 failureClass 不变（IDENTITY→UNKNOWN、MIXING→MARKET_MIXING、TS→UNKNOWN、UNIT→UNIT_MISMATCH、STALE→STALE、503/无sha→PAYLOAD_UNVERIFIED、fallback/foreign→UNKNOWN、history→HISTORY_UNPROVEN）；规则 8 触发 `MARKET_CONTEXT_UNRESOLVED`/`UNKNOWN`。
- **旁证**：前任 QA 报告引用的 `collector.mjs` 行号锚点（guard 调用 :115、降级分支 :147-160、`selectHistoryPeak` :165）**逐行仍吻合** → 本次修复未触碰 collector.mjs。
- **旧测试基线抽查**（`git show bfb3206:<path> | sha256sum` 对比工作区，超额查 4 个）：`t0_market_probe.mjs`（4cb10925…）、`t3_outage_recovery.mjs`（98d50ef4…）、`lib/harness.mjs`（9ab2cfbe…）、`run-all.mjs`（d4f250dd…）**4/4 与基线及 §8 锚点一致**。

### 6.4 清单 4 — 3 条新断言真实性（VERIFIED）

`tests/v16/v16_t3_failure_classes.mjs:85-121`：三条断言分别校验 UNKNOWN 输入 → `MARKET_CONTEXT_UNRESOLVED` + `failureClass='UNKNOWN'`、undefined 输入 → 同码、合法上下文 → `network_context` check 通过。**在我本轮重跑中真实执行且 PASS**，detail 为真实测量值（`firstFail='MARKET_CONTEXT_UNRESOLVED'`、`ctxCheck={code:'MARKET_CONTEXT_UNRESOLVED',failureClass:'UNKNOWN'}` / `{code:null,failureClass:'NONE'}`），非布尔占位、非恒真（P3 证明）。
**观察**：这 3 条断言是 `evaluateCycle` 直调（单元级），未端到端驱动 `runCycle`；端到端 fail-closed 由**我的仓库外探针**（6.2 P1）补证。建议后续把一条端到端断言纳入套件（观察项，不阻塞）。

### 6.5 清单 5 — 冻结区（VERIFIED）

`git status --short -- apps/command_center_3d/server/engine/` 输出为**空**；15 文件 tree sha256 复算 = `aec5d1b7cc26db50736d355e7ef14e066fae3f2c2a376d94637c9a977008a9ad`，与锚点一致。

### 6.6 清单 6 — 报告口径与 C2 关联声明（VERIFIED / 一项环境受限）

- **口径**：`reports/V16_CLOUD_E2E.md` 明确写有「**统计口径（QA 阶段 2 裁定）：11 条 VERIFIED = 10 条采集器真实 US 输出 + 1 条 C8 手工预置的对抗哨兵行（`v16_FIXTURE_ADVERSARIAL`，禁止计入采集器成绩）**」。口径存在且无歧义。
- **C2（证据级 VERIFIED）**：`v16_worker_deploy.json`（13:21:53Z）`deploy_ok=true`，判定方法为结构化（`wrangler exit 0` + `独立 Cloudflare API script readback HTTP 200`，明确标注 QA2-D2 fix、取代关键字嗅探）；`trigger_at_deploy='false'`。我**本地重算**部署 JSON 的 13 个源文件 sha256 与当前工作区**逐字节一致（13/13）**，含修复后的 `market-guard.mjs`（3f3ea23d…）与 `collector.mjs`（78165718…）→ redeploy 确实用最终源码。`v16_entry_closed.json`（13:22:23Z）记录 3 次探针均 403 `TEST_TRIGGER_DISABLED`，其一显式标注「after QA2-D1 guard rule 8 fix redeploy」。`wrangler.toml` 显式配置 `MARKET_NETWORK_CONTEXT='cloudflare_pages_edge_runtime_prod'`（生产漏配风险已缓解）。
- **入口 403 现场亲测（本轮受限，如实登记）**：本 QA 会话网络出口无法抵达 workers.dev（本地代理 CONNECT 502、免代理直连超时 exit 28、WebFetch 亦失败）——**环境限制，非产品缺陷**。修复后 403 状态依据：前任 QA 修复前的亲测（13:13Z，VERIFIED）+ `v16_entry_closed.json`（主理人证据）+ 13/13 源码一致 + readback 200。**本人不对修复后的现场 403 出具 VERIFIED**，建议最终签收引用该 JSON 或从不受限主机复核。

### 6.7 缺陷登记（本次增量复核）

**无新缺陷。** 观察项 2 条（不阻塞）：
1. QA2-O4：3 条新断言为单元级直调，端到端 runCycle 复现目前只有我的仓库外探针证据（6.4）。
2. QA2-O5：修复后「生产漏配 + 无注入 fetcher」将**按设计**让所有周期静默降级 UNKNOWN（`MARKET_CONTEXT_UNRESOLVED`），运维监控应对该错误码的连续出现设告警。

### 6.8 SIGN-OFF 与总 DECISION

**对 QA2-D1 修复的签认：PASS（SIGN-OFF）。**

依据：修复经我用与原缺陷发现**完全相同的方法**端到端复现（同一输入从 fail-open 变为 fail-closed 且仍写 provenance）、正例不受影响、非恒真性独立证明、7 码 7 规则逐字/行为双核未动、两套套件我亲跑 8/8（95 / 104 断言、exit 0）、冻结区零改动、旧测试基线逐字节一致、云端部署源码 13/13 一致。

**阶段 2 放行条件清算**：

| 条件 | 状态 | 依据 |
|---|---|---|
| C1（修复 QA2-D1 + 补测试） | **清零** | 本节 6.2 / 6.4（VERIFIED，我亲测） |
| C2（deploy_ok 判定修正 + 重出证据） | **清零** | 6.6：结构化判定 + 13/13 源码一致 + redeploy 证据（证据级 VERIFIED；现场 403 亲测受网络所限，已如实登记） |
| C3（CI mode 裁定） | **不阻塞**（前任已记录：误命中已消除，full 模式 9/9 exit 0） | 前任 QA §4.11 / §5.2 |
| C4（签收写明 10+1 口径） | **已满足于报告文本**，签收时照引 | 6.6 口径抽查 |

**总 DECISION：GO**（较阶段 2 的 GO WITH CONDITIONS 升级）。理由：唯一必修缺陷 QA2-D1 已修复并经独立复核签认，无新缺陷；C1–C4 全部清零或满足。
**结论边界不变**：本 GO 仅覆盖轨道 A 已审计范围；**接入正式 Staging D1 = NO-GO、生产晋级 / 自动交易 = NO-GO、V1.5 = GO 不变**；建议验收后销毁隔离 D1（沿用 C4 建议）；`V45_TEST_TRIGGER` 保持 false。

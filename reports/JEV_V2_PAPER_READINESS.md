# JEV V2.0 — Paper 基线就绪度（独立 QA 执行证据）

> 作者：independent-qa（结论全部来自本人实跑，不采信任何自述）
> 仓库 HEAD：`fa7740f2ab8be973b8407ed68d5e3514e034a2e3`（分支 `feature/v16-p0-gap-closure`，基线 `ebc3fcc`）
> 实测环境：Windows 11 / Node v22.22.2。会话开始时三处 `node_modules` 全缺；**中途 `apps/command_center_3d/node_modules` 由他人安装（156 项，含 tsc 5.6.3 / vitest / vite），我方随即复跑了类型检查、单测、构建、YAML 解析**（见 §2.3）。`apps/equity_collector_v45` **无 package.json、无依赖**，其测试是纯 node 脚本，缺 `node_modules` 不影响执行。
> 性质：**就绪度评估，不是放行裁定。** 本文件不签发「CI PASS」「生产 GO」「可上真实资金」。

---

## 1. 一句话结论

**Paper 模式的「行为正确性」有扎实的本地证据；「证据的可审计性」有 2 个已知缺口（F-18 哈希不可复现、F-19 锚只写不读）；工程构建（tsc / vitest / build / YAML）在依赖就绪后本地已全部实测通过，但**仍需远端 CI 在干净环境复跑一次**才算签收。**

| 维度 | 判定 | 依据等级 |
|---|---|---|
| Paper 行为正确性（零触网、幂等、不回填、成本双口径不合并） | 基本就绪 | 7 套件 94 断言 + V1.5 95 + V1.6 104 + AI Contract 120，全绿 |
| 风控「不可降级」 | 就绪 | 静态逐条核 + 2 种动态故障注入，产物目录 0 文件 |
| 证据可复现（跨环境） | **不就绪** | F-18：整行哈希含墙钟 `latency_ms` |
| 证据不可篡改（对外不可否认） | **不就绪** | F-19：anchor 只写不读，整本自洽重算可被接受 |
| 类型检查 `tsc --noEmit` | **PASS** | 依赖就绪后实测 exit 0、零输出（§2.3） |
| `npx vitest run` | **工作树 PASS / 提交态 FAIL** | 工作树（含未提交 exclude 补丁）22 文件 217 passed exit 0；HEAD 提交态 `2 failed｜22 passed (24)` exit 1。见 §2.3 |
| 正式构建 `npm run build` | **PASS** | vite 构建成功，exit 0（§2.3） |
| YAML 语法解析 | **PASS** | `yaml` 真实解析器，5 个工作流全部 PARSE OK（§2.3） |
| 真实 venue 费率滑点 | `NOT_MEASURED` | 代码侧仍 `venue_verified: null` |
| 远端 CI 签收 | `NOT_VERIFIED` | 分支未推送 |

---

## 2. 执行证据（本次实跑，逐套件）

| # | 命令 | exit | 断言 | 判定依据（末尾关键行） |
|---|---|---|---|---|
| 1 | `node apps/command_center_3d/server/ai/tests/day14-p0-contracts.mjs` | 0 | 7/0 | `SUMMARY P0-F CONTRACTS ...: 7 pass, 0 fail` |
| 2 | `node apps/command_center_3d/server/ai/tests/day14-p0-runner.mjs` | 0 | 24/0 | `SUMMARY P0-F RUNNER ...: 24 pass, 0 fail` |
| 3 | `node apps/equity_collector_v45/tests/market-data.test.mjs` | 0 | 10/0 | `PASS=10 FAIL=0` / `RESULT=PASS` |
| 4 | `node apps/command_center_3d/server/ai/evidence/tests/inference-evidence.test.mjs` | 0 | 17/0 | `PASS=17 FAIL=0` |
| 5 | `node apps/command_center_3d/scripts/day14-research/p3-contracts.mjs` | 0 | 12/0 | `PASS=12 FAIL=0 TOTAL=12` |
| 6 | `node apps/command_center_3d/scripts/supervisor/supervisor.test.mjs` | 0 | 14/0 | `RESULT PASS=14 FAIL=0` |
| 7 | `node apps/command_center_3d/scripts/check-no-fake-live.mjs` | 0 | 10/0 | `CORE_ASSERTIONS=7 EXTRA_ASSERTIONS=3 TOTAL=10 exit=0` |

聚合套件：

| 命令 | exit | 关键行 |
|---|---|---|
| `node apps/equity_collector_v45/tests/run-all.mjs` | 0 | `OVERALL=PASS {"total":8,"pass":8,"fail":0}`；断言合计 **95**（与 CI 硬校验值一致） |
| `node apps/equity_collector_v45/tests/v16/run-all.mjs` | 0 | `assertions={"total":104,"passed":104,"failed":0}` |
| `node apps/command_center_3d/server/ai/tests/run-all.mjs` | 0 | `TOTAL checks=120 passed=120 failed=0` |
| `node apps/command_center_3d/server/ai/tests/e2e_real_engine.mjs`（单独） | 0 | `E2E checks=37 passed=37 failed=0` |

**合计：7 套件 94 断言 + V1.5 95 + V1.6 104 + AI 120 + E2E 37，全部失败 0，未放宽、未删除任何断言。**

### 2.1 风控不可降级（用户点名项）— VERIFIED

- **静态**：`day14-runner.mjs` 顶层 `await import("../server/ai/risk-gate.mjs")` 的 catch **只有一句 throw**（`FATAL: formal risk-gate import failed — fail-closed termination`），无 warn / 无 continue / 无替身；随后 `if (typeof formalRiskGate !== "function") throw`；`runDay14()` 入口再拦 `typeof riskCheckImpl !== "function"`。全文 12 处 `catch` 逐条核对，**无任何一处能在 catch 后产出 approved 结论**；`grep -i fallback` 的 6 处命中全是注释或 `fallbackUsed:false` / `autoFallback:false` 之类的**结果字段与自检项文案**。
- **动态**（未改任何被测文件，用 Node ESM loader hook 在 resolve 阶段让 `risk-gate.mjs` 解析失败）：基线正常出 11 个产物文件；注入失败后 **exit 1 + uncaught FATAL + 运行目录根本没创建（0 文件）**，不存在「先写一半 approved 再发现风控没了」的窗口。`riskCheckImpl=null` 与伪造对象 `{approved:true, code:"RISK_APPROVED"}` 同样被入口守卫拦死。

### 2.2 日志链篡改与恢复 — 部分 VERIFIED（见 §6 F-19）

实验全部在仓库外的副本上做，基线由本人 `--new-run` 现跑：

| 实验 | 续跑 exit | 结果 |
|---|---|---|
| 无篡改重启 | 0 | 恢复至 cycle 2，链校验通过 |
| 改内容、保留原 `_hash` | 1 | `journal risk.jsonl HASH TAMPER at line 0: content hash mismatch` ✅ |
| 删除末条 | 1 | `RECOVERY_CYCLE_MISMATCH: risk has 0 records but state.cycle=1` ✅ |
| 改第 2 条 `_prev` 为全 0 | 1 | `journal decisions.jsonl CHAIN BROKEN at line 1` ✅ |
| 改内容 + 整本 `_hash` 自洽重算 | **0** | ❌ 未检出，继续写 cycle 4（见 F-19） |

---

### 2.3 依赖就绪后的补充实测（tsc / vitest / build / YAML）

`apps/command_center_3d/node_modules` 就绪后，本人立刻重跑了原先标为 `NOT_MEASURED` 的四项，全部以 **`working-directory: apps/command_center_3d`（与 `ci.yml` 一致）** 执行：

| 命令 | exit | 实测结果 |
|---|---|---|
| `./node_modules/.bin/tsc --noEmit` | **0** | 零输出（无任何 TS 诊断） |
| `npx vitest run` | **0** | `Test Files 22 passed (22)` / `Tests 217 passed | 3 skipped (220)` |
| `npx vitest run --reporter=json --outputFile=…`（与 CI 完全同参） | **0** | JSON：`77/77` suites passed、`217 passed / 0 failed / 3 pending`、`success=true` |
| `npm run build`（vite） | **0** | `✓ built in 6.71s`，仅 chunk >900kB 的非致命警告 |
| `yaml` 解析 `.github/workflows/*.yml`（5 个文件） | **0** | 全部 `PARSE OK`，无 error |

**同时纠正一个广为流传的误判（"`vitest run` 确定性红、CI 的 build-and-test 必红"）：**

从**仓库根目录**执行 `vitest run --root .` 确实会 exit 1（3 个文件报 `No test suite found`：`scripts/supervisor/supervisor.test.mjs`、`server/ai/evidence/tests/inference-evidence.test.mjs`、`apps/equity_collector_v45/tests/market-data.test.mjs`）。但这是**工作目录造成的假象**，不是仓库缺陷：

- 排除规则写在 `apps/command_center_3d/vite.config.ts` 的 `test.exclude` 里，**已显式排除前两个**（配置里自带注释说明这是 `node` 脚本被默认 glob `**/*.test.mjs` 误收的问题），并用 `...configDefaults.exclude` 保留了 vitest 默认排除项；
- `ci.yml` 的 `build-and-test` job 设置了 `defaults.run.working-directory: apps/command_center_3d`，因此 CI 实际命中的正是带 exclude 的配置 → **实测 exit 0**；
- 从根目录跑时读不到 `apps/command_center_3d/vite.config.ts`，才导致 3 个文件被误收。

**结论（经一次自我更正后的准确表述）：**
- 在**工作树**（含 devops 尚未提交的 `vite.config.ts` 补丁）下，`vitest run` = **exit 0 / 22 文件 / 217 passed**；
- 在 **HEAD 提交态**（`fa7740f`，`test.exclude` 尚不存在）下，同样工作目录跑 = **exit 1 / `2 failed | 22 passed (24)`**。

也就是说：**CI 跑的是提交态，而提交态仍会红——修复只存在于工作树，尚未 commit。** 详见 `reports/JEV_V2_CI_VERIFICATION.md` §2。

> 更正记录：本人最初据工作树实测得出「CI 的 vitest 步骤是绿的」，该结论**只对工作树成立、对提交态不成立**，已在此更正。教训：任何"CI 会/不会绿"的判断，必须用 `git show HEAD:<path>` 的提交态复现，不能拿工作树当基准。

---

## 3. ⚠️ 套件入口陷阱（本次最有价值的发现，值班必读）

### 3.1 事实（本人逐个实跑复核，已精确定位范围）

| 目录 | 单独 `node <file>` 的行为 | 是否安全 |
|---|---|---|
| `apps/equity_collector_v45/tests/t*.mjs`（V1.5） | 正常执行，输出 6–8KB，**含 `RESULT_JSON`** | ✅ 安全 |
| `apps/equity_collector_v45/tests/v16/v16_*.mjs`（V1.6） | 正常执行，输出 3–6KB，**含 `RESULT_JSON`** | ✅ 安全 |
| `apps/command_center_3d/server/ai/tests/t1_schema … t9_gateway_routes`（9 个） | **不执行任何断言**，输出仅 167 字节且全是 `ExperimentalWarning: SQLite`，exit 0 | ❌ **假绿** |

> 更正：主理人先前把该陷阱归到 `apps/equity_collector_v45/tests/t1_multi_cycle.mjs`。本人在该路径实测：**exit 0、输出 6745 字节、含 `RESULT_JSON`、断言正常执行**——它是安全的。真正假绿的只有 `server/ai/tests/` 下那 9 个只导出 `run(suite)`、没有 `isMainModule` 入口的文件。

### 3.2 可操作判别规则（值班者直接照做）

单看 exit code 是**无效**的。判定一次测试「真的跑了」，必须**同时**满足三条：

1. **看断言数**：输出里必须出现可解析的断言计数（`PASS=n FAIL=n` / `checks=n` / `SUMMARY ... n pass, n fail` / `TOTAL=n`），且 **n > 0**。
2. **看结论产物**：必须存在 `RESULT_JSON:` 行，或对应的 evidence JSON 被写出并被打印路径。
3. **看末尾汇总行**：真套件都会打印一行 SUMMARY/RESULT；缺这一行就说明进程是空跑退出。

**判定式**：
- exit=0 且 断言数>0 且 有 RESULT/SUMMARY → **真绿**
- exit=0 但 断言数=0 或 无 RESULT/SUMMARY → **假绿（视为失败，不得计入通过项）**
- exit≠0 → 失败

**红线**：`server/ai/tests/t1…t9` 这 9 个文件**在任何 CI gate、任何验收清单里都不得单独出现**；它们只能通过 `run-all.mjs` 聚合执行。若有人把它们加成独立 gate，会得到一个恒 exit 0、恒 0 断言的假门。

---

## 4. F-18：证据哈希不可复现（根因已定，VERIFIED）

### 4.1 现象
同一份代码、同一组固定输入，`v16_t2_idempotency_fields.mjs` 产出的 `fullHash` 在不同执行上下文里取两个不同值（`2fa0e3aa…` / `af384d33…`），`strippedHash` 同步变化。

### 4.2 根因
`v45_snapshot_provenance` 行的 `latency_ms` 是 fetch 封装里的真实墙钟值（`Date.now() - t0`），被写进行里；`hashRow()` 对**整行所有列**做 canonical JSON 后 sha256，**无列级排除**。于是「测出来的耗时」进入了「内容等价性哈希」。

链路：`src/market.mjs`（`latencyMs: Date.now() - t0`）→ `src/provenance.mjs`（`latency_ms: market.latencyMs`）→ `src/hash.mjs`（`hashRow` 覆盖全列）→ 测试把它写进 evidence JSON。

### 4.2b 逐字段证据：两种哈希的行，**35 列里只有 1 列不同**

把「产出 `2fa0e3aa…` 的那次」与「产出 `af384d33…` 的那次」的整行 35 列全部 dump 出来逐字段比对：

```
A hash = 2fa0e3aa94d3…     B hash = af384d33…      列数 A/B = 35 / 35
差异字段数 = 1
  latency_ms: A=1   B=0
```

**`snapshot_id`、`session_id`、`created_at`、`fetched_at`、`payload_sha256`、`mark_contracts_json`、`network_context` 全部逐字相同。**

这条证据直接**排除**了「快照 id / 自增序列 / 建库状态 / DB 文件状态」这类假设：两次都是全新内存库（`D1Shim.fromSchemaFile`），`snapshot_id` 都是 `v45eq_4e459637…`，完全相同；唯一的差就是**那一次 fetch 恰好花了 1ms 还是 0ms**。

### 4.3 判别实验（按主理人要求补做，结论推翻了「稳定」的观感）

连续**单独**跑 3 次同一测试，实测 fullHash：

```
run1 = 2fa0e3aa94d3b52c092ecd78d76c6ac32154b0a2d565a0bacfb233684ab2fd81   (latency_ms=1)
run2 = af384d3367d49b5f16f10ac8a77b38ef2a898e6f34d9f368d658650d65c117e2   (latency_ms=0)
run3 = 2fa0e3aa94d3b52c092ecd78d76c6ac32154b0a2d565a0bacfb233684ab2fd81   (latency_ms=1)
```

**3 次不都相同**（2fa / af3 / 2fa）→ 按判别规则，这**不是**隐藏状态污染，**就是 `latency_ms` 的墙钟效应**。先前观测到的「稳定停在 af3」是**热的连续运行恰好都落在 0ms 档**，属巧合，不是确定性。

补充回归验证：取同一行数据，只改 `latency_ms` 重算哈希，可精确复现两个观测值：

```
latency_ms=0 -> af384d33…   (= B/C)
latency_ms=1 -> 2fa0e3aa…   (= A)
latency_ms=2 -> 2c433d88…
latency_ms=5 -> 37bdd219…
```

### 4.4 为什么「进程内 3 次一致」没被打破
`latency_ms` 是进程级的执行速度属性：同一进程内的 3 次循环处在同一热态，3 个值同时落在 0 档或同时落在 1 档，因此 `full_provenance_row_hash_identical_across_3_runs` 恒 PASS。**该断言只证明「同进程内稳定」，不证明跨进程稳定**——一个原因同时解释了两个现象，无需引入第二个隐藏因素。

压力验证：12 进程并发跑探针 → `2fa0e3aa` × 8、`af384d33` × 4；10 个 CPU 压力进程下连跑 4 次测试 → `2fa0/af38/af38/af38`，且每次进程内 3 值仍一致、verdict 仍 PASS。**哈希在变、测试不变红。**

### 4.5 F-18 最终定级

- **性质**：`NOT_REPRODUCIBLE`（跨执行上下文不可复现）。**已排除**「隐藏状态污染」——无持久化依赖，纯墙钟效应。
- **严重度：Medium**。理由：
  - 不阻塞构建/测试/CI（当前 CI 只比对 `counts.fail===0`，不比哈希，所以不会红）；
  - 但**阻断任何基于哈希的等价性校验或 attestation**：本地 vs CI、CI run1 vs run2 都可能不同；CI 上传的 evidence artifact 里带着这个哈希，下游一旦拿它比对必然误判为「篡改/版本漂移」。
- **可用替代口径**：`snapHash`（`paper_equity_snapshots` 行哈希）**不含 `latency_ms`**，实测跨进程恒为 `62a52d8c…`，是目前唯一可跨环境比对的证据哈希。
- **修复方向（未改代码）**：把 `latency_ms` 移出等价性哈希；或测试注入固定 `latencyMs`；或在文档/校验脚本中显式声明「`fullHash` 不可跨环境比对」。

---

## 5. F-19：日志链外部锚只写不读（medium，本轮不修）

`day14-anchor.json` 含 `root` + 5 本账 `journalTips` + `freezeHash` + `runId`，设计意图是外部锚定；但全仓 `grep journalTips|day14-anchor` 只命中**写入处**与一条 `existsSync()` 存在性自检，**恢复路径从不读回比对**。anchor 自己的 `note` 也写着「external anchor = commit SHA (not pushed)」。

后果：`makeJournal()` 只校验「账本内部自洽」（`_prev` 链接 + 单行 hash 重算）。攻击者按同一算法重算整本账，内部即自洽，**恢复流程静默接受**（实测 exit 0 并继续写 cycle 4）。

**结论：`hashChainVerification` 当前提供的是「链内自洽性」，不是「对外不可否认性」。** 因此「日志链篡改检测」这一项**不得标 VERIFIED**，正确表述是：**内容篡改 / 断链 / 截断可检出；整本自洽重算不可检出。**

按主理人裁定，本轮不修（改恢复路径属 P0 冻结模块之外的新实现，且用户明确不扩功能）。

---

## 6. 环境与未测项（UNKNOWN ≠ 0）

| 项 | 状态 | 原因 |
|---|---|---|
| `npx tsc --noEmit` | **已测（PASS，exit 0）** | 依赖就绪后复跑，见 §2.3 |
| `npx vitest run` | **已测（PASS，exit 0，217 passed）** | 同上；须在 `apps/command_center_3d` 下执行 |
| `npm run build` | **已测（PASS，exit 0）** | 同上 |
| YAML 语法级解析 | **已测（PASS，5/5 PARSE OK）** | 用装好的 `yaml` 包真实解析 |
| CI 历史 run 读取 | `NOT_MEASURED` | `gh` 不可用 |
| 远端 CI 真实签收 | `NOT_VERIFIED` | 分支未推送；本地依赖是**他人在同一工作区安装**的，非干净 `npm ci`，不能替代 CI |
| 真实 venue 费率/滑点 | `NOT_MEASURED` | 代码侧仍为 `venue_verified: null` |
| 测试是否可能因 F-18 变红 | `NOT_OBSERVED` | 12 并发 + 10 压力进程共 13 次运行均 PASS、进程内 3 值一致；不作「会变红」的断言 |

**凭据**：工作区存在本地未跟踪的凭据文件（被 `.gitignore` 忽略、`git ls-files` 确认未入库），已移交安全审计，本文件不记录任何内容。

---

## 7. 明确不能从本次结果推出的结论

- ❌ 「CI 已通过」——分支未推送，无 Run ID；本地依赖是他人在同一工作区安装的，**不等于干净 `npm ci` 的 CI 环境**。
- ❌ 「`vitest run` 在 CI 里是绿的」——**只对工作树成立**。HEAD 提交态缺少 `test.exclude`，在 CI 工作目录下实测 `2 failed | 22 passed (24)`、exit 1。修复（`vite.config.ts` + `ci.yml` 两个文件）**目前仍是未提交改动**，不推送就等于没修。详见 §2.3。
- ❌ 「可上真实资金」——`allowBuySell=false` 恒成立，真实成交路径未解锁；venue 成本为 `null`。
- ❌ 「成本已对账」——只证明两个口径**没有合并**，不证明任何一个口径**是对的**。
- ❌ 「证据不可篡改 / 可跨环境复现」——F-18 与 F-19 两个缺口未关闭。
- ❌ 「本地全套绿 = CI 全绿」——7 个套件里只有 5 个进了 CI（`supervisor.test.mjs`、`check-no-fake-live.mjs` 未被任何工作流引用，24 条断言在 CI 永不执行）。

---

## 8. 若要推进到下一阶段，本人的最小条件

1. 由 CI 真实执行 `tsc --noEmit` / `vitest run` / `build`，用真实结果替换 `NOT_MEASURED`。
2. 关闭 F-18（哈希口径）与 F-19（anchor 回读），或把两者显式写入台账并禁止对外宣称「可复现/不可篡改」。
3. 把 `supervisor.test.mjs`、`check-no-fake-live.mjs` 接入 CI gate（当前 24 条断言无 CI 保护）。
4. 值班/验收一律执行 §3.2 的三条判定式，禁止只看 exit code。
5. 以上未完成前，Paper 结论只能表述为：**「本地行为验证通过；工程签收待 CI 补签；可审计性存在 2 个已知缺口。」**

> 本文件不构成签发。签发权在具备 CI 实跑结果与缺陷处置记录的环节。

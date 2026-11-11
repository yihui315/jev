# JEV_PRODUCTION_READINESS.md — V1.6 / MASTER PLAN V2.0 生产就绪度（索引型）

> 性质：**索引 + 摘要 + 指向权威源**。本文不构成第二个真相源，也不是上线授权书。
> 权威源：
> - `reports/JEV_FINAL_MASTER_STATUS.json`（提交时点唯一冻结快照；简称 **MASTER_STATUS**）
> - `reports/JEV_GAP_CLOSURE.md`（门禁台账；简称 **GAP_CLOSURE**）
> - `reports/JEV_INDEPENDENT_QA.md`（独立 QA；简称 **QA**）
> - `reports/JEV_REAL_TRADING_SECURITY.md`（安全只读复核；简称 **SECURITY**）
>
> 快照：分支 `feature/v16-p0-gap-closure`，commit `5834c3745843baa33b82e04a746fa8691684b2f6`，**未 push**（MASTER_STATUS `snapshotPoint`）。
> 引用纪律：锚点 = 文件名 + 章节号；sha256 / 行数 / 裸行号仅为版本快照。口径纪律：`UNKNOWN ≠ 0`。

---

## 0. 结论先行

> # 生产 GO = **NO**　｜　真实资金 GO = **NO**

一句话：本轮把 P0 的六项缺陷清零、把 P1–P7 的本地骨架落地并跑通 7 套件 94 断言，但**类型检查、CI、真实数据、真实时间窗口四类证据全部缺失**，因此既不构成生产就绪，更不构成真实资金授权。

补充三条不可省略的限定：

- `enableRealTrading=false`、`allowBuySell=false` **维持不解除**；真实资金运营须**用户单独明确授权**，本文件任何内容都不构成该授权（MASTER_STATUS `hardDeclarations`）。
- 本轮 `realOrdersPlaced:0` / `realAccountsCreated:0` / `realFundsInjected:0` / `realInferenceCalls:0`，且 `allResultsProvenance:"SYNTHETIC_FIXTURE"`。
- 三方 gate（实现 / 独立 QA / 安全）**均无 NO-GO，但均为 `GO WITH CONDITIONS`**，且仅限各自范围（GAP_CLOSURE §4.5）。「条件式 GO」不是 PASS，更不是生产 GO。

---

## 1. 就绪度矩阵（十阶段，直接映射 MASTER_STATUS）

| 阶段 | 内容 | 状态 | 一句判定 | 阻断原因 |
|---|---|---|---|---|
| **P0** | 六项缺陷清零 + 独立 QA | `CLOSED_WITH_CONDITIONS` | 工程门禁通过，可进入 P1 工程实现 | 结转条件 C2/C3（QA）与 C2–C6+C8（Security）；R6 `UNVERIFIED` |
| **P1** | 正式数据层 | `IMPLEMENTED_LOCAL_PASS` | 9 文件落地，本地 10/10 | 未落库（`db_persisted:false`）、`ai_market_contexts` 未建、真实行情未接 |
| **P2** | AI 多模型决策系统 | `IMPLEMENTED_LOCAL_PASS` | 4 文件落地，本地 17/17 | `providerId:"mock"` 恒定、零真实推理、Provider 清单 `DECLARED_NOT_WIRED` |
| **P3** | 策略研究（4 组对照 / 回测 / Walk-forward / OOS） | `SKELETON_ONLY_INSUFFICIENT_DATA` | 骨架 + 契约，结论 `INSUFFICIENT_DATA`，无排名 | F-14 / F-15 未修；仓库无 `isReal:true` 样本 |
| **P4** | 成本风险收益统一核算 | `IMPLEMENTED_LOCAL_PASS` | 双口径分列，机器可检未合并 | `venue_verified` 为 `null`（`NOT_MEASURED`） |
| **P5** | 7 天 Shadow + 30 天 Paper + 100 笔 / 30 笔 OOS | `BLOCKED` | 未实施，不伪造 | 真实时间窗口物理上不存在 |
| **P6** | 24/7 Supervisor | `IMPLEMENTED_LOCAL_PASS` | 10 文件，本地 14/14（含变异证明） | `DAEMON_BINDING = DECLARED_NOT_WIRED`，无守护进程、无 scheduler |
| **P7** | 前端驾驶舱 | `IMPLEMENTED_LOCAL_PASS` | 16 处硬编码 LIVE 清零，10/10 静态断言 | `TRADING_DESK_MOUNT = DECLARED_NOT_WIRED`；`TYPECHECK UNVERIFIED`；无浏览器 E2E |
| **P8** | 策略晋级评审 | `BLOCKED` | 未签字（`signed:false`） | 需市场观察样本自然形成 |
| **P9** | 实盘准备（仅设计与验证） | `DESIGN_ONLY` | 未实现（`implemented:false`） | 禁止创建实盘账户 / 注入资金 / 真实买卖 |

**读法**：这张表里**没有任何一格是「生产就绪」**。最多只到「本地实现通过」。

---

## 2. `IMPLEMENTED_LOCAL_PASS` 的精确含义

> `IMPLEMENTED_LOCAL_PASS` **只表示「本地 `node` 直跑通过」**。
> 它**不等于 CI 通过**，**不等于生产就绪**，**不等于真实数据验证**。
> （GAP_CLOSURE §2 口径纪律原文；MASTER_STATUS 全篇同此口径。）

展开成四条可核对的事实：

1. **运行方式**：由主理人在本地 `node` 直跑 7 个 `.mjs` 套件，全部 `exit 0`，合计 94 断言 PASS / 0 FAIL（MASTER_STATUS `verification`）。
2. **未经过 CI**：没有本地 Actions runner，没有 Run ID，CI 结论只能是 `CONFIG-CHANGE / UNVERIFIED`。
3. **未经过类型检查**：`node_modules` 缺失，`npx tsc --noEmit` 从未执行，`TYPECHECK = UNVERIFIED`。
4. **未接触真实数据**：所有结果为 `SYNTHETIC_FIXTURE`；`realMarketSamplesUsed:0`。

**禁止的误读**：不得把该状态写成 PASS、写成「已完成」、写成「可上线」，也不得在对外材料中省略 `LOCAL` 一词。

---

## 3. 三类硬缺口（本轮无法用代码补齐的部分）

### 3.1 `TYPECHECK UNVERIFIED`

- 事实：仓库根与 `apps/command_center_3d` 下均无 `node_modules`；`npx tsc --noEmit` **从未执行**（MASTER_STATUS `environment.nodeModules`）。
- 后果：P7 全部 TSX 改动（`ProvenanceBadge.tsx`、`TradingDesk.tsx`、`PageContainer.tsx`、四个 pages）**没有经过编译器校验**。静态断言脚本 `check-no-fake-live.mjs` 是文本结构自检，不是类型检查。
- 禁止：不得声称「类型安全」「编译通过」。

### 3.2 `CI = CONFIG-CHANGE / UNVERIFIED`

- 事实：`ci.yml` 新增 gap-closure gate step（5 条 `test -f` 严格失败 + 5 条 `node` 调用 + `set -e`，无 `continue-on-error`），`build-and-test` 的 checkout 加 `fetch-depth: 0`（裁定 C）；`v16-market-provenance.yml` 的 `on.push.branches` 仅追加两个分支名（MASTER_STATUS `ciChanges`）。
- 边界：**改动未经真实 runner 验证**；`ciRunId: null`；YAML 未经解析器校验（环境中 pyyaml / ruby-yaml / yq / js-yaml 均不可用）。
- 禁止：不得宣称 CI PASS、不得编造 Run ID、不得把「配置已改」写成「门禁已生效」（R-6）。
- 历史 Run ID（`42715875236` 等）属于基线提交 `e28e230` 的**旧记录**，与本分支无关，不得挪用来支撑本轮结论（GAP_CLOSURE §5）。

### 3.3 `npx vitest run` 从未执行

- 同一根因：`node_modules` 缺失。故 P1/P2/P3/P4/P6/P7 的测试虽然以 `node X.mjs` 形式跑通，**项目自带的 vitest 通道从未被验证**。
- 这意味着：即使未来 CI 跑起来，`npx vitest run` 这一步仍可能出现**本轮从未观测到**的失败。

> 三者叠加的结论：当前证据只能支撑「**本地单点可跑**」，不能支撑「**可重复、可自动化、可托管**」。

---

## 4. 阻塞项：F-14 / F-15（P3 解锁硬前置）

| 编号 | 等级 | 事实 | 为什么阻塞 P3 |
|---|---|---|---|
| **F-14** | `blocker-for-P3` | `LEGACY_FREEZE_EXPECTED` 声明的 `maxNotionalPerTrade:200` / `maxTrades:6` / `maxExposurePct:0.2` / `dailyLossBreachPct:1` / `drawdownBreachPct:2` 除定义与导出外**无使用点**；实际生效的是 `DEFAULT_RISK_LIMITS.maxNotionalUsd:1000`；`s.riskBreach` 恒为 `false`，`RISK_BREACH` 暂停恒不触发 | 冻结配置声明的风控口径（单笔 ≤200 USDT / 总敞口 ≤20% / 日亏损 1% / 回撤 2%）**在代码里没有落地**，且与 1000 冲突。带着未生效的风控去跑真实样本，等于用错误的风控假设做策略研究 |
| **F-15** | `blocker-for-P3` | `riskCheckImpl` 为可注入参数，调用方可替换整个风控门 | 默认链路安全且被 `RED_LINES.allowBuySell=false` 阻断真实成交，但 P3 解锁 `allowBuySell` 后，注入面就是绕过正式门的通道 |

- 两项均 `fixed:false`（MASTER_STATUS `defects.registeredNotFixed`、`phases[P3].gatingPrerequisites`）。
- 报告来源：SECURITY §10.3（新增残留风险）与 §10.4 C3/C4；GAP_CLOSURE §4.0.3（主理人独立核验）。
- **P0 语境下无实际后果**（不下单），因此它们是「P3 前置」而非「P0 blocker」——但绝不能因为 P0 通过就默认它们不需要处理。

---

## 5. P5 / P8 / P9 为什么不能提前签字

| 阶段 | 要求的真实条件 | 当前状态 | 为什么不能提前签字 |
|---|---|---|---|
| **P5** | 7 天 Shadow + 30 天 Paper + **100 笔有效交易** + **30 笔 OOS** | `BLOCKED`，`fabricated:false` | 真实时间窗口与真实样本**物理上不存在**。提前签字＝伪造时间序列 |
| **P8** | 策略晋级评审 | `BLOCKED`，`signed:false` | 晋级评审的输入是 P5 的自然样本；输入不存在时任何签字都是空转 |
| **P9** | 实盘准备（仅设计与验证） | `DESIGN_ONLY`，`implemented:false` | 明确禁止：不创建实盘账户、不注入资金、不执行真实买卖 |

配套事实：

- P3 的 walk-forward 已如实标注 `SYNTHETIC_OOS_DRILL`，`oosPass:0` / `verified:0`，`ranking:null`。**`verified:0` 必须读作「零真实样本通过」，不是「已验证通过」**（GAP_CLOSURE §4.8 裁定 B 的背景）。
- 依赖真实样本/时间窗口的 13 份交付文件（如 `JEV_PAPER_30D_PROGRESS` / `JEV_OOS_STRATEGY_REPORT` / `JEV_PROFITABILITY_SCORECARD` / `JEV_GRADUATION_DECISION`）**本轮一律不生成空壳**，避免伪造 PASS（GAP_CLOSURE §2）。

---

## 6. 安全侧剩余条件（不得平均掉）

安全 DECISION = `GO WITH CONDITIONS（仅限 Security 范围）`；**C1 与 C7 已满足**，剩余如下（SECURITY §10.4 / §10.5.1）：

| 条件 | 对应缺陷 | 状态 | 要求 |
|---|---|---|---|
| **C2** | F-05 独立凭据域缺失（high） | 未闭环 | 真实运行前建立独立凭据域，不复用 `cf_*` / `github_pat_*` 等管理凭据，并提供最小权限 / 提币禁用证据；在此之前 P9 保持 DESIGN ONLY |
| **C3** | F-15 `riskCheckImpl` 注入面 | 未闭环 | P3 解锁前锁定不可注入，或在 `orderable` 判定前对 `gate.code` / provenance 做二次独立校验 |
| **C4** | F-14 风控口径 200 vs 1000、`riskBreach` 恒 false | 未闭环（主理人重点跟踪） | P3 前统一口径，补齐日亏损 / 回撤 / 持仓 / 最大交易数 breach 判定 |
| **C5** | F-07 能力面 | 未闭环 | P3 前收敛冻结引擎 handler 的 `dbFetch` → 全局 fetch → `data-api.binance.vision`（FORBIDDEN 主机），并证明历史真实外呼脚本不可达 |
| **C6** | F-09 残留（low） | 未闭环 | anchor / summary / recovery / state 在 `runs/<runId>/` 内仍覆盖写；如需同 run 全量锚点留存，改 append-only 或按 runId+序号分文件 |
| **C8** | 不越权边界 | 边界保留 | 安全不替 QA 签字、不评价 QA 测试质量、P0 整体门禁由主理人整合 |
| **R6** | 本轮实际外部调用 = 0 | `UNVERIFIED`（保持） | 静态只读审计无法证明「全仓库任意进程运行期零外呼」。C1 的关闭含义是「他方书面确认已中转到位」，**不是**把 R6 升级为 VERIFIED；三条证据彼此独立、不互相替代、不平均 |

另需并列保留的事实：

- **F-05** 在 P0 语境下不构成 blocker（P0 不接触真实凭据/资金），但在「进入真实运行 / P3 解锁」语境下**仍是 blocker**。
- 凭据现状：凭据库键名仅 `cf_*` / `github_pat_*` / `jev_paper_ledger_secret` / `jev_preview_*` / `r2_*` / `x_*`，**无 LLM Provider 键**；历史 MiniMax M3 真实调用的凭据存储位置为 `UNVERIFIED`（`CRED-MINIMAX`，GAP_CLOSURE §1.1）。
- 安全侧 **P9 的 12 项实盘控制清单：0 项已实现 / 5 项部分 / 7 项未实现**（SECURITY §5）。这 12 项全部是「实盘晋级前须逐项补齐」的前置。
- 安全报告第一轮的 `NO-GO` 原文完整保留（SECURITY §0–§9），那是「实现未落盘时点」的历史结论，**不得当作当前结论引用**（GAP_CLOSURE §4.0.2）。

---

## 7. 未闭环缺陷登记（一律不清零）

| 编号 | 等级 | 主题 | 状态 |
|---|---|---|---|
| F-05 | high | 独立凭据域缺失 | 未修 |
| F-07 | medium | 能力面未收敛（真实外呼能力仍在仓库） | 未修 |
| F-09 | low | 残留覆盖写（限 `runs/<runId>/` 内） | 未修 |
| F-14 | `blocker-for-P3` | 风控口径双源、`riskBreach` 恒 false | 未修 |
| F-15 | `blocker-for-P3` | `riskCheckImpl` 注入面未锁定 | 未修 |
| F-16 | medium | `reports/evidence/v16/*.json` 跨次运行不可复现（时间戳 24 处 + node PID 10 处；归一化后语义零变化但字节不可复现） | 未修（需改 v16 证据 harness，超出 P0–P7 范围） |
| F-17 | low | `day14-p0-runner.mjs` 硬编码基准提交 `ebc3fcc`；`fetch-depth: 0` 只消除浅克隆缺对象一种失败模式 | 未修（`day14-p0-lib.mjs` 属 QA 独占文件，改动需 QA 书面同意） |
| R6 | medium | 静态安全结论 `UNVERIFIED` | 保持 |
| CRED-MINIMAX | medium | 历史 MiniMax 凭据位置 `UNVERIFIED` | 未查清 |

另外两条**流程性**风险，与代码无关但会影响门禁可信度：

- **F-16 的连锁后果**：任何一次重跑 `tests/v16/run-all.mjs` 都会弄脏工作树，使「tree clean」类门禁失去判定意义。
- **DEV-06（引用锚点漂移）**：本仓已发生因并行编辑导致裸行号失效的事故。**本文与同批文档一律以「文件名 + 章节号」为锚点。**

---

## 8. 解除就绪的前置清单（可勾选）

> 以下每一条都是**必要条件**，逐条完成后才可重新评估就绪度；任何一条未完成，生产 GO 与真实资金 GO 均维持 NO。

### 8.1 工程证据类

- [ ] 补齐 `node_modules`（仓库根与 `apps/command_center_3d`），执行 `npx tsc --noEmit` 并记录 exit code 与输出摘要 → 解除 `TYPECHECK UNVERIFIED`
- [ ] 在补齐依赖后执行 `npx vitest run`，记录实际结果（本轮从未执行，结果不可预判）
- [ ] 接入真实 GitHub Actions runner，触发 `ci.yml` 与 `v16-market-provenance.yml`，取得真实 Run ID 与结论 → 才可把 `CONFIG-CHANGE / UNVERIFIED` 升级
- [ ] 在真实 runner 上确认 `fetch-depth: 0` 生效、B1 不再假红；若失败须重开裁定 C
- [ ] 复跑 7 套件并核对 94 断言与冻结锚点五项 sha256 未漂移（任何 runner 内容改动都触发全量重跑，QA C2）

### 8.2 缺陷修复类

- [ ] 修 **F-14**：统一 `RISK_LIMITS` 与 `maxNotionalPerTrade` 口径（200 vs 1000），让 `riskBreach` 可被真实触发，补齐日亏损 / 回撤 / 持仓 / 最大交易数判定
- [ ] 修 **F-15**：锁定 `riskCheckImpl` 注入面，或对 `gate.code` / provenance 做二次独立校验
- [ ] 收敛 **F-07** 能力面：冻结引擎 handler 的全局 fetch 与 `data-api.binance.vision` 主机的不可达性证明
- [ ] 取得独立 QA 对 **F-17** 的**书面同意**后再改 `day14-p0-lib.mjs`（QA 独占文件）
- [ ] 处理 **F-16**：改 v16 证据 harness 的 stderr 采集方式，使 `reports/evidence/v16/*.json` 字节可复现
- [ ] 建立**独立凭据域**（F-05），不复用 `cf_*` / `github_pat_*` 管理凭据；补齐最小权限 + 提币禁用证据
- [ ] 查清历史 MiniMax 凭据存储位置（`CRED-MINIMAX`），把 `UNVERIFIED` 转为可核实结论

### 8.3 真实运行前置类

- [ ] 完成 **P5** 的真实时间窗口（7 天 Shadow + 30 天 Paper），取得 **100 笔有效交易** 与 **30 笔 OOS**；数据不足时保持 `BLOCKED`，不得用合成样本顶替
- [ ] 完成 **P8** 策略晋级评审（输入必须是 P5 的自然样本，`signed:false` 期间不得签字）
- [ ] `venue_verified` 费率/滑点由 `NOT_MEASURED` 转为实测值（当前 `null`，禁止回填 `engine_conservative` 数值）
- [ ] P9 的 12 项实盘控制清单逐项补齐（当前 0 已实现 / 5 部分 / 7 未实现）
- [ ] **真实资金上线必须由用户单独明确授权**——本清单全部勾选也不自动构成授权

### 8.4 治理类

- [ ] 保持 `enableRealTrading=false` / `allowBuySell=false` 不解除，直到 8.1–8.3 全部完成
- [ ] 本分支保持未 push / PR #6 保持 Draft，除非获得明确的推送授权
- [ ] 新增结论一律写入**新文件**并锚定章节号，不回改 `reports/**` 旧结论（MASTER_STATUS 的不可回改条款）

---

## 9. 时间与责任边界（防止被误读为承诺）

- 本文**不给出**任何完成时间、工期或里程碑承诺。真实时间窗口（7 天 / 30 天）是物理条件，不是排期问题。
- 本文**不给出**任何性能指标、吞吐、延迟、胜率、收益率数字——这些在本轮**均未测量**，写任何数值都是编造。
- 三方 gate 的「条件式 GO」只描述各自范围内的代码边界；**门禁整合权在 team-lead**，安全与 QA 均不越权签字（SECURITY §10.4 C8、QA §9）。

---

## 10. 溯源说明

- §0 结论：MASTER_STATUS `hardDeclarations`（`productionGo:false` / `realMoneyGo:false` / `ciPass:false`）+ GAP_CLOSURE §0 / §4.5「明确不解除 / 不宣布」。
- §1 矩阵：MASTER_STATUS `phases[*].status` 逐项照抄，判定语综合 `phases[*].boundaries` 与 GAP_CLOSURE §2。
- §2 语义：GAP_CLOSURE §2 口径纪律原文 + MASTER_STATUS `verification` / `environment`。
- §3 三类缺口：MASTER_STATUS `environment.nodeModules` / `hardDeclarations.typecheckStatus` / `ciChanges`。
- §4 F-14/F-15：MASTER_STATUS `defects.registeredNotFixed` 与 `phases[P3].gatingPrerequisites`；GAP_CLOSURE §4.0.3；SECURITY §10.3、§10.4 C3/C4。
- §5 P5/P8/P9：MASTER_STATUS `phases[P5/P8/P9]`；GAP_CLOSURE §2 表注与 §4.7 R-7。
- §6 安全条件：SECURITY §10.4（C1–C8）、§10.5.1（第三轮 DECISION）、§5（P9 12 项清单）、§1.2（凭据键名）；C1 关闭表述见 GAP_CLOSURE §4.5 结转条件与 QA §6A。
- §7 缺陷登记：MASTER_STATUS `defects.registeredNotFixed` + `processDeviations`（DEV-06）。
- §8 清单：由上述各项直接展开；其中「CI 真实 runner」来自 MASTER_STATUS `nextActions`，「F-17 需 QA 书面同意」来自 MASTER_STATUS `defects.registeredNotFixed[F-17]`。

**留空项**：无。所有未测得的数据（真实费率、CI Run ID、类型检查结果、真实资金成本、真实样本收益）均标注为 `NOT_MEASURED` / `UNVERIFIED`，未以 0 或任何估计值填充。

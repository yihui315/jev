# JEV_GAP_CLOSURE.md — MASTER PLAN V2.0 / P0 缺陷清零门禁

> Generated: 2026-11-11 (GMT+8) ｜ 主线: `yihui315/jev` @ `C:/Users/Administrator/WorkBuddy/jev-v16`
> 独立功能分支: `feature/v16-p0-gap-closure` ｜ 基线: `ebc3fcc` ｜ PR #6: **Draft（未动）**
> 阶段门禁: **P0 未全 PASS 前不晋级 P1**；真实资金自动交易全程禁止
> 纪律: `enableRealTrading=false` / `allowBuySell=false` / 不触网 / 不调付费模型 / 不提交订单 / 不写生产 DB / 未 push / 未碰 main·Release

---

## 0. TL;DR（主理人结论）

- 承接主线经主理人裁定为 **V1.6 交易运营**（非 `D:\jev` 控制平面）；本分支与 `D:\jev` 无交叉。
- 主理人**直接读远端源码**核对的 6 个缺陷**全部属实**，逐行证据见 §1；其中 3 项比原描述更严重。
- 已正式建队一次（`jev-v16-p0-gap-closure`），预先声明 ownership，三成员并行：实现 / 独立 QA / 安全只读复核。
- **P0 判定：CLOSED WITH CONDITIONS**（工程门禁通过，见 §4.5）；三方 gate 均为 `GO WITH CONDITIONS`，无 NO-GO、P0 语境内无 blocker。**仍不宣布 V1.6 生产 GO，真实资金运营 NO-GO。**
- **P5/P8 含真实时间窗口（7 天 Shadow / 30 天 Paper / 100 笔有效交易 / 30 笔 OOS），本轮物理上不可完成，一律 BLOCKED**，不伪造、不提前签字。

---

## 1. 主理人只读审计基线（VERIFIED：实读 `day14-runner.mjs` @ `ebc3fcc`）

| # | 缺陷 | 逐行证据 | 判定 |
|---|------|---------|------|
| 1 | 非 DRY 模式未接真实行情与模型 | L164-169 `if(!dry) return {kind:"unverified",reason:"real-path-gated-in-prep"}`；L219 注释「would call MiniMax」；L242 `status: dry?"SHADOW_NO_CALL":"CALL_OK"` 写死非真调；L239 执行路径为空注释；L111-113 导入的 `fetchMarket`/`evaluateCycle`/`frozenEngine` 声明后未使用 | ✅ 属实 |
| 2 | 正式风控导入失败回退简化门 | L117-122 catch 仅 `console.warn`；L125-133 `fallbackRiskGate` 只查 `notional`/`exposure` 两项且 HOLD 直接 `approved:true`；L225-231 在 formal 缺失时改走它 | ✅ 属实且**更严重**：可交易路径仍有放行能力，缺日亏损/回撤/持仓/新鲜度/白名单/过期校验 |
| 3 | 重启后哈希链从 GENESIS 重开 | L71 `let prev="GENESIS"` 为 `makeJournal` 闭包变量，进程重启归零；`appendFileSync` 追加旧文件却把 `_prev` 写成 GENESIS | ✅ 属实，断点恢复必断链 |
| 4 | 费用/异常计数未完整持久化 | L92-95 state 有 `costUsd`/`providerFails`，但 L190 `ctx` 每轮**新建** `{costUsd:0,...}`，L100 `evaluatePause` 读 ctx，L248 只 `s.calls++`、**从不回写 `s.costUsd`** | ✅ 属实：预算/连败/风险状态跨重启归零 |
| 5 | 权益日志填写固定本金与 MATCH | L245-246 `equity=virtualCapitalUsdt` 固定 10000、`position:0`、PnL=0、`reconcile:"MATCH (...)"` 全硬编码；L113 导入的 `frozenEngine` **从未调用** | ✅ 属实 |
| 6 | QA PASS 主要检查文件存在 | L279-290 `independentQA` = `existsSync` + 非空 + 两条常量断言；无链校验/重放/故障注入/账户对账 | ✅ 属实 |
| + | 配置名冒充运行结果 | `config.freeze.json` L3 `realMarket:true`，实跑 L173 `kind:"synthetic", isReal:false, venue:"...dry-synth"` | ✅ 属实，计入 P0-E |

### 1.1 主理人增量发现（原表之外，均 VERIFIED）

| 发现 | 证据 | 影响 |
|------|------|------|
| **口径差主要在滑点而非费率** | `config.freeze.json` `slippagePct: 0.015`（1.5%/边）。200 USDT 买卖各一次：引擎手续费 0.40 + 滑点 **6.00** = 6.40 USDT；手续费口径差仅 0.32 USDT | 两套核算若只统一费率、不标注滑点来源，「保守口径」将掩盖约 19 倍偏差，盈利排行榜仍会选错策略 |
| **行情层无盘口能力** | `apps/equity_collector_v45/src/market.mjs` 仅 `/api/v3/time` + `/api/v3/ticker/price`（L69/L91）；全文件无 `depth`/`lastUpdateId`/`bid`/`ask`/`spread` | §3 的 Bid/Ask/Depth/Spread/订单簿序列号/盘口深度**当前全部缺失**，P1 是新建而非接线 |
| **新分支 CI 覆盖缺口** | `.github/workflows/v16-market-provenance.yml` L8 push 触发**硬编码** `feature/v16-market-provenance-hardening`；`ci.yml` L5 匹配 `feature/**` | 本分支只跑 `ci.yml`，**V1.5 95 + V1.6 104 断言专有门禁不触发**。§12 纪律缺陷，属 DevOps ownership，主理人不越权修改 |
| **凭据域现状（键名，非值）** | `jev-v14.dpapi.json` 键仅：`cf_*` / `github_pat_*` / `jev_paper_ledger_secret` / `jev_preview_*` / `r2_*` / `x_*` | 该文件中**无 LLM Provider 键**；历史 MiniMax M3 真实调用的凭据存储位置为 **UNVERIFIED**，需另查。§9 独立凭据域要求当前未满足 |

### 1.2 外部事实核验

- Binance.US 费率：**COMMUNITY-VERIFIED**（多来源一致：自 2026-04-22 Advanced spot 0% maker / 0.02% taker，BNB/USD 例外；**One Click Buy/Sell 不适用**，内嵌 spread；部分州不可开户）。未取官网一手 fee page，故不标 VERIFIED。**不据此修改冻结引擎**。
- §2 主理人 6 条判断：**VERIFIED**（本地源码逐行）。
- 历史 DAY-1.3 逐周期原始数据未持久化：**BLOCKED / 不可回溯**，禁止从汇总报告反推。

### 1.3 增量发现 #5 / #6（主理人本轮实读，VERIFIED）

| # | 发现 | 证据 | 影响 |
|---|------|------|------|
| **#5 仓库实为双栈** | `apps/` 下含 `command_center_3d`、`control_ui`、`equity_collector_v45`；`packages/` 下含 `jcc_core`（`server.py` 121601 B）、`supervisor`、`operator_skill`、`mcp_facade`、`observability` | V1.6 交易运营主线 = `apps/command_center_3d` + `apps/equity_collector_v45`；JCC 控制平面栈 = `packages/*` + `apps/control_ui`。主理人裁定「不套用外部 `D:\jev` 控制平面 SOP」**不等于**禁止复用本仓库内 `packages/supervisor` / `apps/control_ui` 资产；P6/P7 应优先盘点「接线 / 复用」而非一律新建 |
| **#6 P3/P4 编号撞名陷阱** | `docs/P3_P4_SPEC.md` L13–21 把 P3/P4 定义为 JCC 缺陷 `CR-H5` / `CR-H6`（`/api/health` 的 `control_token_configured`、非 ASCII token 的 `hmac.compare_digest` TypeError），实现位置 `packages/jcc_core/server.py`；`docs/FINAL_STOP_CONDITIONS.md` 是 JCC `PROJECT_COMPLETE` / A8 pin 验收体系 | 与 MASTER PLAN V2.0 的 P3「策略研究体系」/ P4「成本风险收益双口径」**完全无关**，只是编号撞名。两者均不得作为本轮 P0–P9 门禁或 P3/P4 规格依据。已向 p3-research / p6-research 下发纠偏，防止「假委托」到错误工程 |

---

## 2. 阶段门禁矩阵（P0 未全 PASS 前不晋级）

| 阶段 | 内容 | 本轮状态 | 依据 |
|------|------|---------|------|
| **P0** | 六项缺陷清零 + 独立 QA | **CLOSED WITH CONDITIONS**（工程门禁通过；**≠ 生产 GO**） | §4.5 三方 gate 整合 + §4.8.1 主理人 7 套件复跑 |
| P1 | 正式数据层（Snapshot/Bar/OrderBook/Quality/Replay/Guard） | **IMPLEMENTED / LOCAL PASS** — 9 文件落地，`market-data.test.mjs` PASS=10 exit 0；**DB 未持久化（`db_persisted:false`）、`ai_market_contexts` 表 `DECLARED_NOT_WIRED`** | §4.8.1 #3 |
| P2 | AI 多模型决策系统 | **IMPLEMENTED / LOCAL PASS** — 4 文件落地，17/17 exit 0；**`providerId:"mock"` 恒、零真实推理、零真实 API 费用；真实 Provider 调用仍禁止** | §4.8.1 #4；§4.8 裁定 A |
| P3 | 策略研究（4 组对照 / 回测 / Walk-forward / OOS） | **SKELETON ONLY — 整体 `INSUFFICIENT_DATA`**。D 臂（AI 决策）因仓库无 `isReal:true` 样本 → `NO_DATA`，**无排名**；walk-forward `oos_pass:0` / `verified:0` / 标签 `SYNTHETIC_OOS_DRILL`。F-14 / F-15 仍是**解锁硬前置** | §4.8.1 #5 |
| P4 | 成本风险收益统一核算 | **IMPLEMENTED / LOCAL PASS** — 双口径 `engine_conservative`（COMMUNITY-VERIFIED）/ `venue_verified`（`null` + `NOT_MEASURED`），`assertNoMerge()` 已机器可检；费率契约 `FEE_CONTRACT_MATCHES_FROZEN_ENGINE` + `provenance:"SYNTHETIC_FIXTURE"` | §4.8 裁定 B |
| P5 | 7 天 Shadow + 30 天 Paper + 100 笔 / 30 笔 OOS | **BLOCKED — 真实时间窗口**，本轮物理上不可完成，不得伪造 | 主理人 §7/§14 明示「P8 不能提前签字」 |
| P6 | 24/7 Supervisor | **IMPLEMENTED / LOCAL PASS** — 10 文件，14/14 exit 0，变异测试证明有牙（篡改 `DAEMON_BINDING` → FAIL=1）；**`DAEMON_BINDING = "DECLARED_NOT_WIRED"`，无守护进程、无 scheduler** | §4.8.1 #6 |
| P7 | 前端驾驶舱 | **IMPLEMENTED / LOCAL PASS** — 16 处硬编码 `label="LIVE"` 清零、`ProvenanceBadge` 三态 fail-closed、`check-no-fake-live.mjs` 10/10 exit 0；**TradingDesk 挂载 `DECLARED_NOT_WIRED`；`TYPECHECK UNVERIFIED`（`node_modules` 缺失）** | §4.8.1 #7 |
| P8 | 策略晋级评审 | **BLOCKED — 需市场观察样本自然形成**，不得提前签字 | 主理人 §10/§14 |
| P9 | 实盘准备（仅设计与验证） | **DESIGN ONLY / 禁止实施**：不创建实盘账户、不注入资金、不执行真实买卖 | 主理人 §0/§11 |

> **口径纪律（贯穿全表）**：`IMPLEMENTED / LOCAL PASS` 仅表示「本地 `node` 直跑通过」，**不等于 CI 通过、不等于生产就绪、不等于真实数据验证**。所有真实/合成行情、真实/Mock AI、Shadow 决策/模拟成交、已实现/未实现利润、实际 API 费用必须分别标注，不得互相顶替。`UNKNOWN ≠ 0`：无样本一律写 `NOT_MEASURED` / `—`，禁止 `?? 0`。

> §13 要求的 13 份交付文件中，依赖真实样本/时间窗口者（`JEV_PAPER_30D_PROGRESS` / `JEV_OOS_STRATEGY_REPORT` / `JEV_PROFITABILITY_SCORECARD` / `JEV_GRADUATION_DECISION` 等）**本轮不生成空壳**，避免伪造 PASS。

---

## 3. 团队与 Ownership（正式建队一次）

| 成员 | 角色 | 独占写范围 | 不参与 |
|------|------|-----------|--------|
| `trading-engineer` | Trading Engineer | `scripts/day14-runner.mjs` + 新增 `scripts/day14-*.mjs` + `reports/day14-artifacts/` | QA / 安全 / 正式风控源码 / 冻结引擎源码 |
| `independent-qa` | Independent QA | `server/ai/tests/day14-p0-*.mjs` + `reports/JEV_INDEPENDENT_QA.md` | 任何实现代码 |
| `security-auditor` | Security Auditor | `reports/JEV_REAL_TRADING_SECURITY.md`（仅此一文件） | 任何源码 / 凭据内容 |
| `team-lead` | 主理人 | 本文件 + `JEV_FINAL_MASTER_STATUS.json` + 阶段门禁判定 | 不实现核心代码、不代写成员结论、不替 QA/安全签字 |

同一文件不并发写；成员回传必须符合 `jcc-handoff-protocol` 7 段（INPUT/EVIDENCE/CHANGES/TESTS/RISKS/DECISION/HANDOFF），主理人不得删 blocker、不得把 UNVERIFIED 改写成 VERIFIED、不得平均掉结论。

---

## 4. 成员结论与门禁判定

> **PENDING**：三成员并行执行中。本主理人**不预写 PASS、不代写成员产出、不替任何 gate 签字**。收到 7 段回传后按下表逐项整合，任何一项 NO-GO → P0 整体 NO-GO。

| Gate | Owner | DECISION | 关键证据 | 状态 |
|------|-------|----------|---------|------|
| P0 实现（A–E） | `trading-engineer` | **GO WITH CONDITIONS** | 14/14 本地反例 exit 0；真实 egress/付费模型/venue 实测/独立 QA/安全复核/外部锚定均标 BLOCKED-by-scope | 已回传（**实现方自测，不作验收依据**） |
| P0-F 独立 QA（首轮 @ `415cdd36`） | `independent-qa` | **GO WITH CONDITIONS → 升级 NEEDS REWORK** | 25 项：24 PASS / **1 FAIL** / 0 BLOCKED；含 B1 基线证伪与 M3 变异证明 | 已回传，**F1 未关闭** |
| P0-F 独立 QA（终局 @ `002653fe`） | `independent-qa` | — | 要求原样重跑全部 25 项、保留首轮 FAIL 历史 | **PENDING** |
| P0 安全复核（首轮） | `security-auditor` | **NO-GO** | F-01/F-02 fail-open VERIFIED、F-04 新实现未落盘 UNVERIFIED、F-05 独立凭据域未满足 | 已回传，**阻断性** |
| P0 安全复核（R1–R6 重复核 @ `002653fe`） | `security-auditor` | **GO WITH CONDITIONS** | R1–R5 VERIFIED 闭环（逐行实读 + t4a/t7 产物）；F-01/02/03/04/07/08/09/13 闭环；C1–C8 条件未齐 | 已回传，**仅 Security 范围** |
| **P0 整体门禁** | `team-lead` | **待判定** | 三方均 GO 且 F1 关闭方可晋级 P1 | **PENDING** |

### 4.0.2 安全第二轮结论与未闭环条件（不得平均掉）

- 首轮 **NO-GO** 的三根因：F-01/F-02（fail-open）经 R1/R2/R3 实读闭环；F-04（未落盘）因实现落盘并实读最终 829 行而解除；F-05（§9 独立凭据域）在 P0「不接触真实凭据/资金」语境下不构成 P0 blocker，但在 **P3 解锁语境下仍是 blocker**。
- 更新后 Security = **GO WITH CONDITIONS**，条件 C1–C8 中 **C1（R6 待 QA 书面确认）与 C8（QA 未回传）仍未满足**。R6 保持 **UNVERIFIED**：静态只读不能证明「无进程发起过外部 HTTP」，若发现任一真实 egress/付费模型调用，安全结论**立即回退 NO-GO**。
- 边界：该结论**仅表示 P0 Security 代码边界复核通过**；≠ P0 整体 PASS、≠ 晋级 P1、≠ 解除 P9 DESIGN ONLY、≠ 替 QA 签字。
- 首轮 §0–§9 的 NO-GO 是「实现未落盘时点」的历史结论，报告原文完整保留不删；不得当作当前结论引用，也不得据此认为安全仍持 NO-GO。

### 4.0.3 新增发现 F-14 / F-15（主理人独立核验，P3 解锁前置）

| 发现 | 主理人核验证据 | 影响 |
|------|---------------|------|
| **F-14（medium，但直接违背 §6）** | `CFG` L66-70 定义 `maxNotionalPerTrade:200 / maxTrades:6 / maxExposurePct:0.2 / dailyLossBreachPct:1 / drawdownBreachPct:2`；全文件 grep 显示这些字段**除定义/导出外无使用点**；`s.riskBreach` 恒 false（无置 true 处）→ `RISK_BREACH` 暂停（L297）恒不触发；实际生效仅 `RISK_LIMITS` 的 `maxNotionalUsd:1000` | 主理人 §6 明文「单笔不超过 200 USDT / 总敞口 ≤20% / 日亏损 1% / 回撤 2%」**在代码中未落地**，且 200 与 1000 口径冲突。P0 不下单故无实际后果，**P3 解锁前必须统一口径并补齐 breach 判定** |
| **F-15（medium）** | L555 `riskCheckImpl = formalRiskGate` 可注入；默认安全、被红线阻断真实成交，但调用方可替换整个风控门 | P3 解锁前须锁定注入面或二次校验 `gate.code`/provenance；本轮作为 QA 故障注入钩子保留 |
| F-07 能力面（medium） | 冻结引擎 `paper-server.mjs` 服务端 handler 含 `dbFetch`→全局 fetch→`data-api.binance.vision`（FORBIDDEN 主机）；但 runner 用 `__test` 纯内存函数，本轮不触发 | 历史脚本能力面仍在，P3 前须收敛 |
| F-09 残留（low） | anchor/summary/recovery/state 仍覆盖写，但均在 `runs/<runId>/` 内，不跨 run 静默重写 | 知悉即可 |

### 4.0 F1 硬门禁裁定（主理人）

- **F1 内容**：provider 超时 / 抛错 / 解析失败 / 无有效意图时，decisions.jsonl 记 `decisionClass:"HOLD"`、`reason:"provider-no-intent"`，把「无模型决策」混进 HOLD 计数。
- **裁定：硬门禁，不接受「按设计可接受」豁免**。依据 MASTER PLAN V2.0 §4/P2 明文：「输出非法、超时或解析失败时记录 `NO_ACTION`，不得改写成模型主动 HOLD」。QA 首轮 GO WITH CONDITIONS 按其自身规则升级为 **NEEDS REWORK 直到 F1 修复并经独立重验**。
- **缓解事实如实保留**：F1 下 `orderable=false`、无 `RISK_APPROVED`、provider 日志有 `PROVIDER_ERROR`/`PARSE_FAILED` 可区分 → 属**分类语义/审计证据污染**，非 fail-closed 失效、非资金安全漏洞。但不得因此豁免。
- **修复已落盘**（L640-659、L696）：`intentValid` / `providerFailed` → `NO_ACTION`、`PROVIDER_FAILED_FAILCLOSED`、`execution="NONE"`、`failClosedReason`。
- **关闭 F1 的唯一合法路径**：独立 QA 在最终冻结指纹上**原样重跑**全部 25 项（不改断言、不删用例），并保留首轮 24/1 记录作为历史证据。实现方 17/17 自测与我的实读核实**都不能**关闭 F1。

### 4.0.1 F1 修复范围（实现方 7 段补充 + 主理人独立核验）

- 实现方声明的改动仅 3 处：runner L640-685（verified 分支重构为 `intentValid`/`providerFailed` + NO_ACTION 分支 + 原逻辑原样搬入 else）、L696-697（纯附加 `failClosedReason`）、verify 脚本 L250-288（新增 T9a/T9b/T9c）。未改 `!acq.verified` 分支、`RISK_LIMITS`、`RED_LINES`、`buildRiskContext`、`executeIntent`、`reconcile`、`makeJournal`、`verifyConfigFreeze`、runId/anchor/state/累计逻辑。聚合导出 L803 是 F1 插入 22 行导致的行号后移、内容未变（415cdd36 时为 L781）——该口径差已核对。
- **主理人独立核验（VERIFIED，不采信自述）**：最终指纹 `002653fe…` 未再漂移；全文件 `approved:\s*true` **无匹配**；`executeIntent` 唯一调用点 L675；`orderable` 仅在 L658 置 false 与 L668 由 `gate.code === RISK_APPROVED` 判定；三处 `riskCheckImpl` 全传冻结的 `RISK_LIMITS`；`RED_LINES` 硬编码 false。结论：F1 修复未引入新的 approved 通路或下单路径。
- 实现方 17/17 自测 exit 0 属**被验方自证**，仅作核对用，不作验收依据；其亦如实说明「未单独重跑主 CLI 8，路径由 T1b 的 spawnSync CLI 覆盖」，未冒充。
- 实现方**接受 DEV-03 登记、不抗辩**，承诺绝对冻结 / idle / 不 commit / 不直接响应其他成员，任何改动先经主理人书面授权。其提出的时间线说明（F1 修复发生在收到冻结裁定之前）作为客观事实记录，但不抵消「改变被验对象」与「绕过中转」两点。

> **零自证绿**：实现方的 `day14-verify.mjs` 14 项属被验方自证，只作为用例设计参考。P0-F 必须由不参与开发的独立 QA 自行实现并运行反例集。

### 4.1 版本锚点（主理人实测复核，已冻结）

**权威最终锚点（主理人实测，已绝对冻结；以下覆盖此前所有旧指纹）**

| 文件 | sha256 | 大小 |
|------|--------|------|
| `apps/command_center_3d/scripts/day14-runner.mjs` | `002653fed0d48e3cd20c95a397400d93830192ca87f60a8361a88b3aa3726d4a` | 829 行 / 39606 B |
| `apps/command_center_3d/scripts/day14-verify.mjs` | `7b6fd5391ecd71dd036ecafd1ac2629a531c021352b535d23e4359dad0ebfbca` | 17 项自测 |
| `reports/day14-artifacts/config.freeze.json`（旧，只读） | `d4edce64c48bf15ab6803ce9ea08a164404d84642c61065d74550d7983208658` | 未改 |

版本抖动链（全部实测）：`37158 B` → `415cdd36…`（807 行，新增聚合导出）→ `002653fe…`（829 行，F1 修复）。**QA 首轮 24 PASS/1 FAIL 与安全首轮 NO-GO 均基于旧指纹，须按最终锚点重做**。实现方已被明令不得再改任何文件。

- HEAD 仍 `ebc3fcc`，**未 commit**。主理人裁定：三方 gate 齐备前维持不 commit，避免改变并行成员的 diff 基线；本地提交在 P0 判定后由主理人统一执行（PR-buffer 纪律）。
- 冻结源码零越界：`git diff --name-only HEAD` 对 `risk-gate.mjs` / `decision-schema.mjs` / 冻结引擎 / 旧 freeze 均为空（VERIFIED）。
- 新链位于 `reports/day14-artifacts/runs/run-2026-11-10T22-49-35-352Z-edc0e551/`，根目录旧产物保留不动。

### 4.2 流程偏差登记（主理人）

- **DEV-01（medium，流程）**：`independent-qa` 曾直接向 `trading-engineer` 请求新增聚合导出，违反「成员之间禁止直接传话，跨成员信息须由主理人中转」。影响可控：导出行（runner L781 `export {...}`）为纯增量、不改运行行为、实现方 14/14 复验 PASS，主理人认可保留并已要求后续一律经中转。已向双方下发纠正。
- **DEV-02（low，版本一致性）**：实现方在主理人触发安全 R1–R6 复核后又改过一次文件（37158→38203 B）。已实测新指纹并把新锚点转给安全成员，要求以最新版本重读，避免证据链对不上旧版本。
- **DEV-03（high，证据链 / 绕过裁定）**：`independent-qa` 把 F1 缺陷**直接**发给 `trading-engineer`，实现方在未获主理人授权、且已被明令「版本冻结、更改须经我转发」的情况下，直接修改被验对象（415cdd36→002653fe），并各自声称已直接同步给安全成员。后果：QA 的 24/1 结论与安全 R1–R6 复核所依据的指纹全部失效，须按最终锚点重做；且「缺陷→修复」闭环绕过了主理人对 F1 硬门禁的裁定权。修法方向正确、予以保留，但流程不认可。已向三方下发：绝对冻结 `002653fe…`、后续一律经主理人中转、F1 只能由独立 QA 在新指纹原样重跑关闭。
- **DEV-04（medium，流程）**：`security-auditor` 在第二轮回传中称「F-14/F-15 建议转 trading-engineer 知悉（我另发一份精简协作消息）」，即**再次绕过主理人中转**直接联系实现方。已下发纠正：F-14/F-15 由主理人登记并统一在 P3 解锁阶段处理，成员不得自行向实现方派活。
  - **证据补实**：`trading-engineer` 后续正式报备「被动收到该直连消息」，内容为 Security GO WITH CONDITIONS / R1–R5 VERIFIED / F-14、F-15 两个 medium（安全方自标 P3 前置、非 P0 必修）/ 锚定 `002653fe…`。实现方按裁定**未回复安全、未改任何文件、未 commit、保持 idle**，仅向主理人报备输入端存在未经中转的直连。故 DEV-04 确认为「实际发生且已抵达」，但**未造成新的版本漂移或结论污染**；实现方本次处置符合裁定。
  - **安全方如实报告并接受登记**：`security-auditor` 承认上一轮向实现方直发过 1 条消息（内容为 R1–R5 闭环确认 / V2→V3 fail-closed 加强 / F-14、F-15 标 P3 前置 / 版本三变提醒 / F-05 维持 DESIGN ONLY / R6 保持 UNVERIFIED / 不改任何源码）。消息**未输出凭据、未要求改代码、未替 QA 签字、未改变结论**；精确系统时钟无法读出，安全方明确「以消息投递记录为准，不编造时间」（该诚实标注予以保留）。已承诺此后不再向任何成员直发消息。DEV-04 闭环：违规确认、无衍生污染、双方均接受登记、纠正已生效。
- **流程偏差合计 4 起**，均如实登记、未掩盖；主理人未因此删除任何 FAIL、未把任何 UNVERIFIED 改写成 VERIFIED、未替任何 gate 签字。DEV-03/DEV-04 反映多成员并行时「跨成员信息必须由主理人中转」这条铁律被反复突破，已在三方下发统一纠正；若后续再发生，应暂停对应成员而非仅登记。

### 4.3 F-12 外部调用确认

- `trading-engineer` 书面声明：真实外部 HTTP 0 / 付费模型·LLM 调用 0 / 交易所或任意订单提交 0 / 生产 DB 写入 0，合计 **0**。依据：runner 内无 URL / 无 `globalThis.fetch` / 未 import `provider-adapter.mjs`、`ai-core.mjs`；`fetchImpl` 默认纯内存 fixture；provider 日志 `FIXTURE_NO_LLM_CALL`、`costUsd=0`；快照 `isReal:false / dataMode:"fixture"`；唯一子进程为本地只读 `git rev-parse HEAD`。
- 静态审计不可证「无进程发起过 HTTP」，故安全侧该结论按**他方书面确认**处理，仍保留 UNVERIFIED 边界；`independent-qa` 需独立给出 QA 侧计数（两条独立证据，不互相替代）。

### 4.4 主理人独立复核（不采信任何成员自评，VERIFIED）

本轮主理人**亲自实跑**独立 QA 的两套测试（非采信报告文字），结果：

| 命令 | exit | 实测结果 |
|---|---|---|
| `node apps/command_center_3d/server/ai/tests/day14-p0-contracts.mjs` | 0 | `7 pass, 0 fail`。含 T8-engine（非默认账户 `equity:16600`，证明 10000 / MATCH 非通用结论）、T3-ref（内容篡改与 `_prev` 断链均被检出）、T2c（简化门放行而正式门拒绝的反例，证伪「替换风控」） |
| `node apps/command_center_3d/server/ai/tests/day14-p0-runner.mjs` | 0 | `24 pass, 0 fail`。含 R1③（非白名单 BUY → `RISK_SYMBOL_NOT_ALLOWED` / `approved:false`）、R1-cross（`riskCheckImpl:null` → fail-closed throw、0 日志 0 放行）、R6（8 个产物文件全存在且非空、旧 `independentQA` 会放行，但链被篡改仍拒绝续跑）、双口径未合并、F-12 外呼 = 0 |

- 合计 **31 PASS / 0 FAIL**，与 `reports/JEV_INDEPENDENT_QA.md` 一致。复跑后 `git status --short` 与复跑前逐项相同，**未污染工作树**。
- F-12 动态计数实测输出：`child processes spawned (sandboxed local node) = 33`、`P0-QA-NONET-ATTEMPT hits across ALL child stderr = 0`、`parent globalThis.fetch is the NONET guard = true`、守卫安装 2 处。
- 三条 F-12 证据**彼此独立、不互相替代、不平均**：① trading-engineer 书面声明（§4.3）；② QA 侧运行时动态计数（本节实跑 + 报告 §6，仅覆盖 QA 进程树）；③ security-auditor 静态只读审计**保持 UNVERIFIED**（方法论边界，主理人明确禁止其改写为 VERIFIED）。
- **证据边界**：本复核只证明「测试在最终指纹 `002653fe…` 上确实跑出上述结果」，**不**证明真实行情 / 真实 AI / 真实资金路径可用；不解除任何红线，不宣布 P0 整体 PASS 或生产 GO。

### 4.5 P0 整体门禁整合裁定（主理人）

**裁定：`P0 = CLOSED WITH CONDITIONS`（P0 工程门禁通过，允许进入 P1 工程实现）。不等于 V1.6 生产 GO，不等于真实资金 GO。**

#### 三方 gate 原始结论（互不覆盖、互不平均、不替任何一方签字）

| 角色 | DECISION | 范围 | 依据 |
|---|---|---|---|
| `trading-engineer` | `GO WITH CONDITIONS` | 实现方自评，**不作验收依据** | P0 A–E 落盘、17/17 自测 |
| `independent-qa` | **`GO WITH CONDITIONS`**（C1 已关闭；剩余 C2 / C3） | 仅 QA 范围 | 最终指纹 `002653fe…` 上 31/31 PASS、0 FAIL、0 BLOCKED；首轮 24/1 历史保留；B1 基线证伪 + M3 变异证明测试有牙；F1 由独立重跑关闭 |
| `security-auditor` | **`GO WITH CONDITIONS`**（C1 / C7 已满足；剩余 C2–C6 + C8 边界；R6 仍 UNVERIFIED） | 仅 Security 范围 | R1–R5 VERIFIED；R6 静态只读 UNVERIFIED；首轮 NO-GO 原文保留、F 项 blocker 不删 |
| `team-lead` | **独立复跑 31 PASS / 0 FAIL**（contracts 7/7、runner 24/24，均 exit 0） | 主理人一手证据，非采信自评 | §4.4 |

- 三方**均无 NO-GO、P0 语境内无 blocker**；条件均为「版本冻结 / 流程边界 / P3 解锁前置 / P9 DESIGN ONLY」，**不是 P0 代码缺陷**。
- 实现方 17/17 自测**未采信为验收依据**；旧 `independentQA()` 文件存在式检查已被 R6 反例证伪（8 个产物全存在且非空、旧检查会放行，但链被篡改仍必须拒绝续跑）。

#### 六缺陷逐项闭环

| # | 缺陷 | 闭环证据 |
|---|---|---|
| 1 | 非 DRY 未接行情与模型 | P0-A 五路依赖注入（`fetchImpl` / `providerImpl` / `engineImpl` / `riskCheckImpl` / `nowMs`），默认 fixture、dry=true；`fixture-verified` / `isReal:false` 明确非 LIVE |
| 2 | 正式风控导入失败回退简化门 | P0-B 顶层 import 失败即 `throw`；`fallbackRiskGate` 放行能力删除；非 HOLD 构造完整 verified context 交 `riskCheck`；`RISK_LIMITS` 冻结；T2c / R1-cross 反例证明不可替换 |
| 3 | 重启哈希链从 GENESIS 重开 | P0-C 启动全量校验 `_prev` 链 + 重算 `_hash`、恢复末尾哈希；T4 双进程真实重启、T3-ref 篡改与断链均被检出 |
| 4 | 费用/异常计数未持久化 | P0-C state 原子写 + 累计预算 / 调用次数 / 失败计数 / 风险状态跨重启恢复 |
| 5 | 权益固定 10000 + MATCH | P0-D `engineImpl.summarize(account, marksUsd)` 与独立 harness 账本对账，输出 `delta` / `basis` / `tolerance`；T8-engine 实测非默认账户 `equity:16600`，证明 10000/MATCH 非通用结论；篡改现金触发 MISMATCH 自动暂停 |
| 6 | QA PASS 只查文件存在 | P0-F 独立 QA 自写 31 项反例 / 故障注入 / 链校验 / 重放 / 独立对账；R6 证明「文件全存在」不构成通过 |
| + | `realMarket:true` 冒充运行结果 | P0-E 新 `runId` + `runs/<runId>/` + 不可变 `run.json`，清除误导字段；旧 `config.freeze.json` 只读、永不重写 |

#### 随 P0 结转的条件（不是 P0 代码缺陷，但不得丢失）

| 来源 | 条件 | 归属 |
|---|---|---|
| QA C2 | `day14-runner.mjs` 内容不得再改动；改动即原样重跑两套测试并核对 `IMPL runnerSha` | 全程 |
| QA C3 / Security R6 | 三条 F-12 证据彼此独立、不平均；R6 静态结论保持 **UNVERIFIED** | 全程 |
| Security C2 / F-05 | 独立凭据域未落地；历史 MiniMax 凭据位置 UNVERIFIED | P9 DESIGN ONLY |
| Security C3 / F-15 | `riskCheckImpl` 注入面须锁定或二次校验 gate code / provenance | **P3 解锁硬前置** |
| Security C4 / F-14 | `CFG` 200/6/0.2/1/2 未生效、`riskBreach` 恒 false、实际 `maxNotionalUsd:1000`，违背 §6 单笔 ≤200 USDT | **P3 解锁硬前置** |
| Security C5 / C6 | F-07 能力面、F-09 残留（low） | P3 / 运维 |
| §1.1 | CI 覆盖缺口：`v16-market-provenance.yml` 的 push 触发硬编码 `feature/v16-market-provenance-hardening` | DevOps ownership |
| §1.1 / P1 勘察 | 行情**采集层**无 `depth` / `klines`；但冻结引擎 `fetchMarketContext()`、前端 `fetchOrderBook` / `fetchCandles`、`T03_depth.json` / `T03_klines.json` 已是盘口/Bar 消费方 → P1 是「2 新建 + 4 接线」，非 6 项全新建 | P1 |

#### 明确不解除 / 不宣布

- `enableRealTrading=false`、`allowBuySell=false` **不解除**；真实资金运营 **NO-GO**，须用户单独明确授权。
- **不宣布 V1.6 生产 GO**；不把 `GO WITH CONDITIONS` 写成 PASS，不把 UNVERIFIED 写成 VERIFIED。
- P5（7 天 Shadow / 30 天 Paper / 100 笔有效交易 / 30 笔 OOS）与 P8 晋级评审仍为 **BLOCKED — 真实时间窗口与样本不可伪造**；P9 仅 DESIGN ONLY。
- PR #6 保持 Draft；本地提交由主理人统一执行，不 push、不碰 main/Release。

### 4.6 流程偏差 DEV-05 / DEV-06（主理人自纠，如实登记）

- **DEV-05（medium，主理人侧 / 证据链）**：主理人就同一 C1 事项先后发出两条口径不一致的消息（一条要求「给出更新后 QA DECISION、剩余条件仅 C2/C3」，另一条要求「不要改 §9 DECISION」），导致 `independent-qa` 的 DECISION 出现三次演变：`GO WITH CONDITIONS` → `GO（无条件）` → `GO WITH CONDITIONS（C1 关闭，剩余 C2/C3）`。其 397 行版 §9 已**全程留痕**，非伪造、未删断言、未把 FAIL 变绿；最终采用更保守的 `GO WITH CONDITIONS` 作为权威结论。
- **DEV-06（low，引用漂移）**：主理人在 `security-auditor` 写入证据引用之后，又向本文件追加 §1.3 / §4.4，使 `JEV_GAP_CLOSURE.md` §4.3 由 L153-156 移至 L160-163；同期 QA 报告由 359 行增至 397 行，F-12 行号由 L15/L93/L297-299/L301-304 移至 L16/L95/L299-301/L303-305。已下发纠偏：安全报告改用**「文件 sha256 + 章节号」为权威锚点**，裸行号仅标注为版本快照。
- **DEV-07（low，跨成员直连）**：`p1-research` 在回传中自述「已同步关键结论给 `p2-research` 避免重复勘察」。只读勘察、未改文件、未污染结论，但违反「跨成员信息须经主理人中转」。已记录，后续统一由主理人转达。
- 累计流程偏差 **7 起**（DEV-01～DEV-07），其中 **2 起为主理人自身**，不因身份豁免登记。主理人未删任何 FAIL、未把任何 UNVERIFIED 改写为 VERIFIED、未替任何成员签字、未平均任何角色结论。

### 4.7 P1–P7 实现边界裁定（主理人，交付前锁定）

| # | 裁定 | 依据 |
|---|---|---|
| **R-1** | **`day14-runner.mjs` 绝对冻结**：P1–P7 一律不得修改、不得为其新增导出、不得复制其内部代码。任何改动都会使 QA C2 失效、31/31 结论失效并须全量重跑 | §4.5 QA C2；§4.6 DEV-02 / DEV-03 教训 |
| **R-2** | **P1 / P2 纯增量**：只新建文件，不改任何 tracked 源码。`ALLOWED_ENDPOINTS` 扩容、`providerImpl` 契约扩展、journal 字段扩展**一律推迟**到 P5 激活 / P3 解冻；真实 egress 本轮未获授权 | MASTER PLAN §0 授权边界；§1.1 凭据域 UNVERIFIED |
| **R-3** | **P2 的 8 类溯源字段落在独立 evidence 层**，不进 `decision-schema.mjs` 的 14 字段白名单（AI 不得自报 provenance，溯源由服务器写）；不建 `0007` D1 迁移（本轮不碰数据库）。P2 勘察建议的「修改 runner」方案**不予采纳** | p2 勘察 §6.1 / §6.2；冻结区零改动红线 |
| **R-4** | **推理成本三口径分列、严禁合并**：预算口径 `state.costUsd` / 实测 `actualInferenceCost` / 估算 `estCostUsd` 各自独立；`UNVERIFIED` 分量不得参与合计；未实测写 `amount:null` + `provenance:"UNVERIFIED"`，**绝不回填预算值** | `COST_MODELS` 双口径纪律平移；`agent-orchestrator.mjs:269/345-349` 把 4 个 LLM 角色 usage 累加成单个对象是反面教材 |
| **R-5** | **P3 / P4 不得伪造样本**：历史真实行情原始数据不可回溯，回测只能基于明确标注 `isReal:false` 的 fixture；四组对照结论一律标 `FIXTURE_ONLY / NOT_OOS_VALIDATED` | §1.2；主理人 §5 / §14 |
| **R-6** | **CI 配置改动只能标 `CONFIG-CHANGE / UNVERIFIED`**（无本地 Actions runner），严禁宣称 CI PASS 或编造 Run ID | §4.5 CI 缺口 |
| **R-7** | **P5 / P8 / P9 不实施**：7 天 Shadow / 30 天 Paper / 100 笔有效交易 / 30 笔 OOS 为真实时间窗口；P9 仅 DESIGN ONLY，不创建实盘账户、不注入资金、不执行真实买卖 | 主理人 §7 / §10 / §11 / §14 |

---

### 4.8 三项挂起裁定的最终结论（主理人，提交前锁定）

实现成员曾三次提请裁定，迟迟未决会成为提交时的隐性负债。本节给出终局结论，**每一项都以主理人一手实测为据，不采信成员自述**。

#### 裁定 A：`rawResponseBytes` → `rawResponseBase64`（输出侧改名，契约基数不变）

| 项 | 内容 |
|---|---|
| **问题** | 该字段实际存的是**原始响应字节的 base64 完整编码**，但字段名 `rawResponseBytes` 读起来像「字节数整数」。下游极易写出 `if (rec.rawResponseBytes > 0)` 这类把 base64 串当数字用的代码 |
| **裁定** | **A = 改名，不扩字段**。输出侧（证据记录顶层字段）改名 `rawResponseBase64`；**输入侧**（`providerResult.rawResponseBytes`，provider 交给构造器的原始字节）**保持原名**。**不新增** `rawResponseByteLength`——那会把顶层字段 19→20，破坏已冻结的字段基数 |
| **边界理由** | 该模块尚未提交，此刻改名零成本；提交后改名即需数据迁移。最小-invasive 原则在此不适用于「命名陷阱」——留着一个已知会被误读的名字，比多改 7 行更贵 |
| **落点** | `inference-evidence.mjs` 输出侧 7 处（返回键 + `verifyRawResponseHash()` + `decodeRawResponse()`）；残留的 2 处 `rawResponseBytes` 全在输入侧（L239 JSDoc / L282 `providerResult.`） |
| **新增断言** | ① 内加两条：`typeof r.rawResponseBase64 === "string"`、`Buffer.from(r.rawResponseBase64,"base64").length > 0`。目的不是加覆盖率，是让「把 base64 当字节数」在类型断言层面就离谱 |
| **主理人实测** | 残留检查：`rawResponseBytes` 仅剩 2 处输入侧 ✅；`rawResponseBase64` 出现 7 处 ✅；`inference-evidence.mjs` `f092e628…` / 424 行、测试 `6fc18164…` / 517 行；`index.mjs`（`238a4b39…`）与 `multi-model-provider.mjs`（`e26223d1…`）指纹**未变**，证明零越界；实跑 **PASS=17 FAIL=0 exit=0** |

#### 裁定 B：`VERIFIED_AGAINST_FROZEN_ENGINE` → `FEE_CONTRACT_MATCHES_FROZEN_ENGINE`

| 项 | 内容 |
|---|---|
| **问题** | 本仓「VERIFIED」是**保留的 provenance 词**（`walk-forward.mjs` 用 `verified:0` 表示「零真实样本通过」，门禁矩阵用它表示数据真实性核验）。而 `assertEngineFeeContract()` 返回的是**契约算术一致性自测状态**。两者共用一词，会在 grep 式审计中撞车，也会给「fixture 不得标 VERIFIED」的纪律开出例外 |
| **裁定** | **改**。状态词去掉保留词，并**新增结构化字段** `provenance: "SYNTHETIC_FIXTURE"`——原实现只把 SYNTHETIC 塞在 `note` 自由文本里，下游可忽略；结构化后不可忽略 |
| **落点** | `fee-wrap.mjs:83-84`、`p3-contracts.mjs:50-51`（新增 provenance 断言）。`ENGINE_FEE_RATE` / `computeFee` / `computeFeeNumber` 的**数值与算法一字未动** |
| **主理人实测** | 全仓 `grep -rn VERIFIED_AGAINST_FROZEN_ENGINE apps/` = **0 处** ✅；`ENGINE_FEE_RATE = __test.CFG.feeRate` 仍取自冻结引擎 ✅；`cost-models.mjs` `b32d10e4…` **未变**（双口径未合并）✅；`fee-wrap.mjs` `be291e16…`、`p3-contracts.mjs` `7945ee76…`；实跑 **PASS=12 FAIL=0 TOTAL=12 exit=0** |
| **注** | `p3-contracts.mjs:67` 仍保留 `provenance:"VERIFIED"` 字样，那是**反例 fixture**（用于证明「声称 VERIFIED 的 `venue_verified` 报告必须被拒」），保留正确，不得误删 |

#### 裁定 C：`ci.yml` 必须加 `fetch-depth: 0`

| 项 | 内容 |
|---|---|
| **问题** | `day14-p0-lib.mjs:322-327` 的 `runnerSourceAt(ref)` 在 `git show` 失败时**抛错**，不静默跳过；`day14-p0-runner.mjs:73-74` 的 B1 可证伪性用例硬引用 `runnerSourceAt("ebc3fcc")`。`ci.yml:25` 的 `actions/checkout@v4` 默认 `fetch-depth: 1`，在 push 到 `feature/v16-p0-gap-closure` 时对象库无 `ebc3fcc` |
| **结论** | 不加则 **B1 抛错 → `build-and-test` 稳定假红**，不是「偶发风险」，是确定性失败 |
| **裁定** | **必须加，且只加在 `build-and-test` job**（`real-data-probe` 不跑该测试，加深度纯属浪费）。「不改既有 step」纪律的本意是防悄悄改变既有测试判定；加深 clone 深度**不改变任何既有测试的判定**，只让新 gate 可跑，故按**必要修复**处理，不按「扩大授权」处理 |
| **主理人实测** | `ci.yml` 148 行（原 139 + 9）、sha256 `39ce5d0d…`；`fetch-depth: 0` 仅 L34 一处，位于 `build-and-test`（L18）内，`real-data-probe`（L97）未沾 ✅；全文件 **TAB=0**；缩进结构 `steps item(6) → with:(8) → fetch-depth:(10)`，与既有 `setup-node` 的同级缩进逐位同构 ✅；`check-no-fake-live.mjs` 复跑 10/10 exit 0（行号位移未打破 Y2）|
| **验证边界** | **无本地 Actions runner，`fetch-depth: 0` 的修复效果未经真实 runner 验证**。CI 结论仍按 R-6 标 `CONFIG-CHANGE / UNVERIFIED`，**不宣称 CI PASS、不编造 Run ID** |

#### 4.8.1 主理人第四方一手复验（提交前最后一次全量）

不采信任何成员自评。裁定 A/B/C 全部落地后，重跑全部 7 个套件：

| # | 套件 | 结果 | exit |
|---|---|---|---|
| 1 | `server/ai/tests/day14-p0-contracts.mjs` | 7 pass, 0 fail | 0 |
| 2 | `server/ai/tests/day14-p0-runner.mjs` | 24 pass, 0 fail | 0 |
| 3 | `equity_collector_v45/tests/market-data.test.mjs` | PASS=10 FAIL=0 | 0 |
| 4 | `server/ai/evidence/tests/inference-evidence.test.mjs` | PASS=17 FAIL=0 | 0 |
| 5 | `scripts/day14-research/p3-contracts.mjs` | PASS=12 FAIL=0 TOTAL=12 | 0 |
| 6 | `scripts/supervisor/supervisor.test.mjs` | PASS=14 FAIL=0 | 0 |
| 7 | `scripts/check-no-fake-live.mjs` | CORE=7 EXTRA=3 TOTAL=10 | 0 |

**合计 94 项断言 PASS / 0 FAIL / 7 套件 exit 全 0。**

十个冻结/权威锚点在提交前终检，**零漂移**：

```
002653fed0d48e3cd20c95a397400d93830192ca87f60a8361a88b3aa3726d4a  day14-runner.mjs
5797ba8cd6308e11c75c53121f151402e872ec1f1551780eb6953d60c992278f  risk-gate.mjs
328b7a9b140069d354e9b06a08d85dca79ed47f3394acb8fd43c031be3184ba1  decision-schema.mjs
f9ac6e142446ef2aa8deb928f2b129eb2d42f4227d1bc22469191087312e4d74  engine/_lib/paper-server.mjs
d4edce64c48bf15ab6803ce9ea08a164404d84642c61065d74550d7983208658  day14-artifacts/config.freeze.json
b32d10e41d9cef71534580252e48acbbc29ab583bc77f43238155aaafa8e5c2c  day14-research/cost-models.mjs
f092e6286192220e9b5b6ba123ef29fb6b14849224e254d42cedd7dfdac35a24  ai/evidence/inference-evidence.mjs
be291e16d83634e7434ec1c8a24ed9f6c16dcf87c68fcf28e02c879647463adf  day14-research/fee-wrap.mjs
7945ee764e46c77294f6f14924de1050740a148f6f91b3d0fa6cfb9d92a27737  day14-research/p3-contracts.mjs
39ce5d0d7961df209deb8e043a1dc307225f6fc62d6bedcc72304bf26d36a7fc  .github/workflows/ci.yml
```

#### 4.8.2 本轮新增两项缺陷（主理人独立发现，均不在 P0–P7 交付范围，登记不清零）

**F-16（medium）｜`reports/evidence/v16/*.json` 跨次运行不可复现**

P3P4 回归重跑 `tests/v16/run-all.mjs` 后，17 个证据 JSON 变 ` M`。常规解释是「只有时间戳变了」，但主理人对归一化后的 diff 做了严格对称性检验，发现**不止时间戳**：

- 时间戳：24 处（8 组 `startedAt`/`finishedAt`/`capturedAt`）
- **`stderr` 内嵌 node 进程 PID：10 处**（如 `(node:10816) ExperimentalWarning: SQLite ...`）

归一化时间戳与 PID 后，`+`/`-` 行完全成对（零不对称），且**含 `verdict`/`pass`/`detail`/`checks` 的变更行为 0**——即**语义零变化**，但**文件字节不可复现**。后果：任何一次重跑都会永久弄脏工作树，使「tree clean」类门禁失去意义。修复需改 v16 证据 harness 的 stderr 采集方式，**不在 P0–P7 范围**，本轮只登记。

**F-17（low）｜`day14-p0-runner.mjs` 硬编码基准提交 `ebc3fcc`**

B1 可证伪性用例硬引用 `runnerSourceAt("ebc3fcc")`。裁定 C 的 `fetch-depth: 0` 只消除了「浅克隆缺对象」这一个失败模式；若将来 rebase / squash / 换 base 使 `ebc3fcc` 不可达，B1 仍会抛错。更稳的做法是取 `HEAD~N` 或把基准版本落成 fixture 文件。`day14-p0-lib.mjs` 属 QA 独占文件，改动需 QA 书面同意，本轮不动。

---

### 4.9 DEV-08：主理人跨业务线概念污染（主理人自身，medium）

写 `docs/JEV_REAL_TRADING_SECURITY.md` 时，主理人向撰写成员下发的口径中包含一条「`ReferenceInput.url` 仅记录、不触网（禁止爬虫 / CAPTCHA bypass）」，并把它列为三大安全底线之一。

**该字段在本仓不存在。** 全仓检索（`*.md` / `*.mjs` / `*.ts` / `*.tsx` / `*.py` / `*.json`，含 `reports/JEV_REAL_TRADING_SECURITY.md` 全文）`ReferenceInput` / `reference_input` 命中 **0 处**。它来自主理人经手的另一条业务线（短视频增长系统）的真实约束，被误抄进 JEV。

| 项 | 内容 |
|---|---|
| **性质** | 概念污染 / 幻影字段。不是引用漂移（DEV-06），是**凭空引入了一个不属于本项目的事实** |
| **发现者** | 文档撰写成员。其无法取得出处时**拒绝编造章节号**并如实上报，处理正确 |
| **错误处置方式** | 初稿曾保留为「待补证据 / `NOT_FOUND_IN_AUTHORITY_SOURCE`」占位 |
| **正确处置** | **整条删除**。保留「待补位」会让读者以为 JEV 真有这条要求、只是缺证据，从而去追一个不存在的东西。**误置的事实应当撤销，不应当降级保留** |
| **连带修正** | 索引表该行改为删除线 + 「已撤销」；底线由「三条」改回「两条」（仅 `allowBuySell=false` 未解除、真实资金 NO-GO，二者均有代码与报告双重支撑） |
| **教训** | 与 DEV-06 同源：**没有逐字证据的引用，宁可缺，不可编。** 主理人下发的口径本身也必须先自证存在性，不能因为「我记得有」就写进 brief |

---

## 5. 必报字段

- **基线 SHA**：`ebc3fcc`｜**独立分支**：`feature/v16-p0-gap-closure`｜**PR #6**：Draft，未动、未 merge、未 force。
- **本地统一提交**：见 `reports/JEV_FINAL_MASTER_STATUS.json`（提交时点唯一冻结快照）。**未 push、未碰 main/Release、未 force**。
- **CI**：本轮未推送，无新 Run ID；历史 `e28e230` 4 workflow 全 SUCCESS（`42715875236` / `42715875188` / `42715868543` / `42715868572`）。
  - **CI 覆盖缺口已闭合（CONFIG-CHANGE / UNVERIFIED）**：`v16-market-provenance.yml` 的 `on.push.branches` **只追加** `feature/v16-p0-gap-closure` 与 `feature/v16-**`（原条目与既有 steps 一字未动）；`ci.yml` 新增「P0/P1/P2/P3P4 gap-closure gate」step，5 条 `test -f ... || exit 1` 严格失败 + 5 条 `node` 调用 + `set -e`，**无 `continue-on-error`**；`build-and-test` 的 checkout 加 `fetch-depth: 0`（见 §4.8 裁定 C）。
  - **边界**：**无本地 Actions runner，以上改动一律未经真实 runner 验证**，不宣称 CI PASS、不编造 Run ID（R-6）。
- **风控门禁**：`allowBuySell=false` / `ENABLE_REAL_TRADING=false` **维持不解除**；正式 `server/ai/risk-gate.mjs` 为唯一权威门，**P0-B fail-closed 已 VERIFIED 闭环**（R1/R2/R3 + T2c + R1-cross）。
- **真实资金**：**NO-GO**，全程未接触；P9 仅 DESIGN ONLY。
- **P0 判定**：`CLOSED WITH CONDITIONS`（§4.5）；三方 gate 均为 `GO WITH CONDITIONS`（无 NO-GO、P0 语境内无 blocker）；主理人独立复跑 31 PASS / 0 FAIL。**不宣布 V1.6 生产 GO**。
- **提交前一手复验**：**7 套件 / 94 项断言 PASS / 0 FAIL / exit 全 0**（§4.8.1）。
- **本轮三项裁定**：A `rawResponseBytes`→`rawResponseBase64`（输出侧改名，契约基数 19 不变）；B `VERIFIED_AGAINST_FROZEN_ENGINE`→`FEE_CONTRACT_MATCHES_FROZEN_ENGINE` + 结构化 `provenance:"SYNTHETIC_FIXTURE"`；C `ci.yml` 加 `fetch-depth: 0`（不加则 B1 稳定假红）。三项均以主理人一手实测为据（§4.8）。
- **成功项**：主线裁定、6 缺陷逐行实证 + 全部闭环、8 项增量发现（含双栈 / P3P4 编号撞名 / F-16 证据不可复现 / F-17 硬编码基准）、正式建队 + ownership、独立分支隔离、独立 QA 31/31 反例实证、主理人一手复跑、三方 gate 整合、C1/F1/C7 关闭、CI 覆盖缺口闭合、P1–P7 五路实现落地。
- **失败项**：本轮 0 FAIL。历史首轮独立 QA 曾有 **1 FAIL = F1**（provider 失败被改写为 HOLD），已修复并由独立 QA 在最终指纹上原样重跑关闭；首轮 24/1 记录保留未删、未改断言变绿。
- **阻塞项 / 未闭环**：
  - **P5 / P8 / P9** = BLOCKED / DESIGN ONLY（真实时间窗口与真实样本不存在，不伪造）。
  - **P3 解锁硬前置 F-14 / F-15 未修**（`LEGACY_FREEZE_EXPECTED` 死配置 vs 实际 `maxNotionalUsd:1000`、`s.riskBreach` 恒 false、`riskCheckImpl` 注入面）。
  - F-05 独立凭据域、F-07 能力面、F-09 残留、R6 静态 UNVERIFIED、历史 MiniMax 凭据位置 UNVERIFIED。
  - **F-16** 证据 JSON 跨次不可复现（时间戳 + node PID）；**F-17** `ebc3fcc` 硬编码基准。
  - P6 `DECLARED_NOT_WIRED`、P2 `MODEL_ID_AUTHORITY` / `ai_market_contexts` 表、TradingDesk 挂载 —— **均只声明未接线**，不得读作已启用。
  - `node_modules` 缺失致 `TYPECHECK UNVERIFIED`。
  - **真实资金运营 NO-GO，全程未接触。**

---

*纪律确认：本文件所有结论来自本地只读源码/命令实证或明确标注的证据等级；未伪造任何数据、未触碰冻结引擎与正式风控源码、未改旧 config.freeze.json、未 push、未碰 main/Release、无真实资金流动、凭据仅列键名未输出任何值。*

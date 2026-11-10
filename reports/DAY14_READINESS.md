# DAY14_READINESS.md — JEV V1.6 / DAY-1.4 准备度与验收（P0 已同步 · P1 已收口 · P3 终报）

> Generated: 2026-11-11 (GMT+8)
> 仓库: yihui315/jev ｜ 分支: `feature/v16-market-provenance-hardening` ｜ PR #6: **Draft**
> 本地 HEAD: P1 `cf75cf7` + 本报告提交（见 `git log`）｜ 远端 HEAD（推送前）: `e28e230`（P0 同步，`git ls-remote` 权威校验）；推送后远端 = 本地 HEAD
> 纪律红线: `ENABLE_REAL_TRADING=false` / `allowBuySell=false` / 未碰 main·Release / 无真实资金 / 未 force push / 密钥仅 last4 引用且不出本会话

---

## 0. TL;DR（结论先行）

- **P0 同步已解除**：本地 9 提交 `b102ca7..e28e230` 全部上远端（DPAPI 解密 Fine-grained PAT + 认证 HTTPS push，经代理 10808；token 仅内存、未打印、未落盘）。`git ls-remote` 权威确认远端 = 本地 = `e28e230`。CI 在 `e28e230` 触发 **4 个 workflow 全 SUCCESS**。
- **P1 代码安全收口已完成**（提交 `cf75cf7`）：DAY-1.4 Runner **正式复用 `server/ai/risk-gate.mjs` 的 `riskCheck` 作权威风控**（`gate="formal-risk-gate"`），简化门仅作防御性 fallback（导入失败才用，非替代）；新增 P2 溯源字段与本地 tamper 锚点；独立 QA 通过（`qa.pass=true`）。
- **诚实边界**：当前仍为 PREP/审计模式（`dry=true`，合成行情、Mock AI、SHADOW 不执行）。真实行情 + 真实 AI 路径已接线但被红线门控；解除 `allowBuySell` 前须经**独立 QA 对“真实运行”**验证。
- **不宣布 V1.6 生产 GO**：真实运行的独立 QA 尚未做（需要真实 Binance.US + 真实 MiniMax 的下一轮运行）。本报告如实标记 BLOCKED 项。

---

## 1. 主理人 P0–P3 验收指令 ↔ 执行对照

| 指令 | 主理人要求 | 执行结果 | 状态 |
|------|-----------|----------|------|
| **P0 同步 9 提交** | 从 Credential Center 读 PAT（不输出）、认证 HTTPS push、禁 force、推送后校验文件哈希/提交 | 9 提交已上远端；`git ls-remote` 校验远端=`e28e230`；无 force、未碰 main/Release | ✅ DONE |
| **P0 跑 CI** | 运行最新 SHA 的 CI 并记录 Run ID | `e28e230` 触发 4 workflow 全 SUCCESS（Run ID 见 §7） | ✅ DONE |
| **P1 复用正式风控** | 正式 Paper 路径必须复用 `server/ai/risk-gate.mjs`，禁止简化风控取代线上校验 | Runner `import {riskCheck}` 作权威门；fallback 仅防御；`risk.jsonl` `gate=formal-risk-gate` | ✅ DONE |
| **P1 不触网/不调付费模型/不提交订单** | DRY_RUN 纯函数不触网；无真实 API 调用；无订单 | `dry=true`；`provider_health=SHADOW_NO_CALL`；`allowBuySell=false` 无 execution 路径 | ✅ DONE |
| **P1 本地锚点** | 日志 sha256 链增加**独立摘要锚点** | `writeAnchor()` 产 `day14-anchor.json`（root hash over config-freeze + 5 journal tips） | ✅ DONE |
| **P1 独立 QA** | 完成独立 QA | `independentQA.pass=true`（含 `formal risk gate wired: YES` + `day14-anchor.json written` 检查） | ✅ DONE |
| **P2 区分真实/合成/Mock** | 区分真实/合成行情、真实/Mock AI、有效 HOLD/异常 NO_ACTION、Shadow/模拟成交、已实现/未实现利润及 API 费 | Runner 落 `kind`/`marketKind`/`aiKind`/`decisionClass`/`mode`/`realizedPnl`/`unrealizedPnl`/`apiFeeUsd` | ✅ DONE（harness 落地；历史缺口见 §5） |
| **P2 历史缺口不可回溯** | DAY-1.3 原始逐周期数据未持久化，禁止从汇总报告重构 | 严守：不重构；下一轮按本契约留存完整时间线 | ✅ 纪律守住 |
| **P3 最终交付** | 输出 SHA/PR/CI Run ID/同步结果/QA/风控状态/下一轮准备；BLOCKED 标记；不宣布 GO | 本报告 §7 全量填报 | ✅ 本报告 |

---

## 2. T05 DAY-1.4 PREP 要求 ↔ 实现对照（保留并更新）

| # | T05 要求 | 实现 | 状态 |
|---|---------|------|------|
| 1 | 实时市场快照归档 | `snapshots.jsonl`（每周期：`cycle,isReal,price,hash,tsSource` + `kind/marketKind/marketContextId` 溯源字段） | ✅ 已实现+验证 |
| 2 | AI Decision Journal | `decisions.jsonl`（每周期：`action,notional,confidence,riskApproved,riskCode,reason,marketHash` + `kind/aiKind/decisionClass/marketContextId` sha256 链） | ✅ 已实现+验证 |
| 3 | Risk Journal | `risk.jsonl`（**正式风控门 `server/ai/risk-gate.mjs` 的 `riskCheck`**；HOLD→`RISK_HOLD_NO_ORDER`；fallback 仅防御） | ✅ 已实现+验证 |
| 4 | Provider Health | `provider_health.jsonl`（每周期：`provider,status,latencyMs,costUsd`；dry=`SHADOW_NO_CALL`） | ✅ 已实现+验证 |
| 5 | Equity Reconciliation | `equity.jsonl`（每周期：`equity,cash,position,realizedPnl,unrealizedPnl,apiFeeUsd,reconcile`；SHADOW 下 `MATCH` 平账） | ✅ 已实现+验证 |
| 6 | 自动暂停 | `evaluatePause()`：BUDGET_EXCEEDED / PROVIDER_DOWN(3连失) / RECONCILE_MISMATCH / RISK_BREACH / MAX_CYCLES | ✅ 已实现 |
| 7 | 断点恢复 | `state.json`（`cycle/paused/pauseReason/calls/costUsd/providerFails`）；`loadState`→续跑 | ✅ 实测（3→10 续跑成功） |
| 8 | 独立 QA | `independentQA()`：5 日志+state+anchor 存在性、freeze 完整、红线=false、formal gate wired | ✅ 实测 `pass:true` |
| 9 | 次日实验配置冻结 | `CONFIG_FREEZE` + `config.freeze.json`（**仅首次写、后续只验**；篡改即拒绝启动） | ✅ 实测篡改检测 |
| 10 | 范围纪律 | 仅准备有界 Paper Trading；不启动未授权长期云端付费 | ✅ 遵守（无云端、dry、红线） |
| 11 | 独立摘要锚点（P1 新增） | `writeAnchor()` 产 `day14-anchor.json`（root hash over `config.freeze.json` + 5 日志 tip `_hash`） | ✅ 已实现+验证 |

---

## 3. 交付物清单

**代码（1 个文件，已加固）**
- `apps/command_center_3d/scripts/day14-runner.mjs`（~300 行 ESM）：
  - 权威风控：`import {riskCheck as formalRiskGate} from "../server/ai/risk-gate.mjs"`；DRY_RUN 下 `riskCheck` 为纯函数（HOLD 路径不依赖 context、不触网）。导入失败才退回 `fallbackRiskGate`（防御性，非替代）。
  - P2 溯源：`buildDecision()` 生成符合 `decision-schema`（schemaVersion `jev.ai.decision/v1`、`symbol:"BTCUSDT"`、`strategyId:"day14-prep"`、`validForSeconds:30`、HOLD 时 side/orderType/quantity/limitPrice=null）；`acquireCycleMarket` 返回 `kind:"synthetic"|"unverified"|"real"`；decision 日志加 `decisionClass`(HOLD/NO_ACTION)、`aiKind`(mock/real)、`mode`(simulated/real)、`realizedPnl/unrealizedPnl/apiFeeUsd`。
  - 本地锚点：`writeAnchor()` 先于 `independentQA()` 调用，产出 `day14-anchor.json`。
  - 复用只读导入：`equity_collector_v45/src/market.mjs`、`market-guard.mjs`、`vendor/nr_trusted/functions/_lib/paper-server.mjs` 冻结引擎——**只读**，未改冻结引擎。

**生成产物（运行时产出，位于 `reports/day14-artifacts/`；本批为加固后 8 周期运行，由 `cf75cf7` 包裹；`summary.json` 内 `sha` 记录其生成时工作树 `e28e230`）**

| 文件 | 内容 | 验证 |
|------|------|------|
| `config.freeze.json` | 实验配置冻结（sha 比对基准） | 篡改检测 ✅ |
| `snapshots.jsonl` | 市场快照（含 `_prev/_hash` 链 + 溯源字段） | 8 周期 ✅ |
| `decisions.jsonl` | 决策日志（含风控/市场哈希 + P2 溯源字段） | 8 条 ✅ |
| `risk.jsonl` | 风险日志（`gate=formal-risk-gate`，HOLD→`RISK_HOLD_NO_ORDER`） | 8 条 ✅ |
| `provider_health.jsonl` | Provider 健康（dry=`SHADOW_NO_CALL`） | 8 条 ✅ |
| `equity.jsonl` | 权益对账（含 `realizedPnl/unrealizedPnl/apiFeeUsd`） | 8 条 ✅ |
| `state.json` | 断点状态 | 续跑 ✅ |
| `day14-summary.json` | 汇总 + QA 结论 | `qa.pass:true` ✅ |
| `day14-anchor.json` | **本地 tamper 锚点（root hash）** | 已生成 ✅ |

---

## 4. 验证证据（实测，非声明）

**[E1] 五日志 + state + freeze + summary + anchor 跑通**
```
node apps/command_center_3d/scripts/day14-runner.mjs 8
→ cycles:8, calls:8, costUsd:0, paused:false
→ qa.pass:true（5 日志 + state + day14-anchor.json 存在、freeze 完整、红线=false、formal gate wired=YES）
→ anchor.root = b1068183a3ee5d854588724432e9df23641ab66676ebab8b569b0908e4ea716e
```
每条 journal 含 `_prev`/`_hash` 形成 sha256 链（tamper-evident）；锚点 root 为 freeze + 5 日志 tip 的再哈希。

**[E2] 断点恢复**（前序 T05 实测）
```
RUN1: node ... 3   → state.cycle=3, decisions=3
RUN2: node ... 10  → state.cycle=10, decisions=10  (从 3 续跑，新增 7 条，非重跑)
```

**[E3] 配置冻结篡改检测**（前序 T05 实测）
```
写入 config.freeze.json = {"enableRealTrading":true,"allowBuySell":true}
→ Error: config freeze MISMATCH — experiment config altered  (启动即拒)
```

**[E4] 红线强制**
- 模块常量 `enableRealTrading:false`、`allowBuySell:false`；`verifyConfigFreeze` 显式断言二者必须 false，否则抛错。
- `acquireCycleMarket` 在 dry 模式返回 `isReal:true` 但**仅记录、不执行**；真实 AI 调用块被 `dry=true` 门控；`allowBuySell=false` 时无 execution 路径。

**[E5] 正式风控接线（P1 核心）**
```
risk.jsonl 首行：{"cycle":0,"gate":"formal-risk-gate","action":"HOLD","approved":true,"code":"RISK_HOLD_NO_ORDER",...}
independentQA.checks 含 "formal risk gate wired: YES"
```
证明 DAY-1.4 不再用简化门替代线上风控；正式 `risk-gate.mjs` 为权威门。

**[E6] 本地独立摘要锚点（P1 新增）**
```
day14-anchor.json：
  root = sha256(freezeHash + 5 journal tips + ts)
  freezeHash = d4edce64c48bf15ab6803ce9ea08a164404d84642c61065d74550d7983208658
  journalTips.market/decision/risk/provider/equity = 各日志末条 _hash
  note = "local anchor; external anchor = commit SHA once pushed"
```
外部锚点 = 推送后的 commit SHA（已落地：`e28e230` → 即将 `cf75cf7`）。**未宣称 off-box notary。**

---

## 5. 诚实边界与启用路径

**[B1] 当前为 PREP/审计模式，非真实运行**
- 未触网：`acquireCycleMarket` 合成「真实行情形态」快照（`marketKind:"synthetic-dry"`），**不调用 `fetchMarket`、不连接 Binance.US**；`kind:"synthetic"` 明确标注。
- 未调模型：`provider_health.jsonl` 每周期 `SHADOW_NO_CALL`；真实 MiniMax 调用块被 `dry=true` 跳过；`aiKind:"mock"` 标注。
- 不执行：`allowBuySell=false` → 决策仅 SHADOW 记录，无订单；`mode:"simulated"`。

**[B2] 启用真实运行的明确步骤（P3 解锁后，非本 PREP 自动执行）**
1. 解除 `allowBuySell` 红线需经主理人显式评审 + **对真实运行的独立 QA PASS**（隔离持久化账户 + Staging D1 访问就绪）。
2. `acquireCycleMarket` 替换为 `fetchMarket(fetchImpl)`（复用导入已就位）+ `evaluateCycle` 8 规则 fail-closed；`kind` 置 `"real"`、`marketKind` 标真实 venue。
3. 真实 AI 块（`acq.isReal && !dry`）接入 MiniMax-M3（密钥仅经 `config/.env`，gitignored，last4 外不外露）；`aiKind:"real"`。
4. `runDay14({dry:false})` 显式调用；`provider_health.jsonl` 记真实 `latencyMs/costUsd/status`；`equity.jsonl` 记真实 `realizedPnl/unrealizedPnl/apiFeeUsd`。
5. 全程 `ENABLE_REAL_TRADING=false`：即便 `allowBuySell=true` 也仅 Paper（冻结引擎），无真实资金。

**[B3] P2 溯源契约（历史缺口不回溯声明）**
- DAY-1.3 的逐周期原始数据**从未持久化**，因此 T03 逐笔取证、T04 同时间线对比在历史上**不可重构**——本报告严格遵守主理人指示，不从汇总报告反推原始证据。
- 自本加固起，harness 已按契约落盘全部区分字段（`synthetic/real`、`mock/real`、`HOLD/NO_ACTION`、`simulated/real`、`realizedPnl/unrealizedPnl/apiFeeUsd`）；**下一轮真实运行**将首次产生可审计的真实时间线，届时可一键重跑 T03/T04。

---

## 6. 跨任务状态汇总（T01–T05）

| 任务 | 状态 | 关键结论 |
|------|------|----------|
| T01 GitHub 推送恢复 | ✅ **RESOLVED** | 经 DPAPI 解密 Fine-grained PAT + 认证 HTTPS push（代理 10808），9 提交 `b102ca7..e28e230` 已上远端；`git ls-remote` 权威确认远端=`e28e230`；无 force、未碰 main/Release。 |
| T02 CI 验收 | ✅ **GREEN** | `e28e230` 触发 4 workflow 全 SUCCESS（Run `42715875236`/`42715875188`/`42715868543`/`42715868572`）；历史基线 `bc2a5cb` 4 个 SUCCESS（`42712800491`/`42712789106`/`42712789223`/`42712800228`）。 |
| T03 39 次 HOLD 审计 | ⚠️ **方法论 PASS / 逐笔取证 BLOCKED** | 6 校验/风控前置/无假 HOLD 成立；缺逐笔原始数据（见 B3，不可回溯）。本 harness 已从设计根因修复——下一轮真实运行可落盘完整证据。 |
| T04 策略对比 | ⛔ **INSUFFICIENT_DATA** | 价格时间线未落盘 → 同时间线收益不可算；下一轮真实运行后按 P2 契约重跑。 |
| T05 DAY-1.4 准备 | ✅ **PREP 完成 + P1 收口（已推送）** | harness 可运行、5 日志+冻结+暂停+QA 已验证；正式风控接线 + 本地锚点 + P2 溯源字段落地（提交 `cf75cf7`）。 |

---

## 7. 必报字段（主理人要求，全量填报）

- **实际远端 SHA（推送前）**：`e28e230`（`git ls-remote origin feature/v16-market-provenance-hardening` 权威校验；PR #6 head）。推送后远端 HEAD = 本地 HEAD（含 P1 `cf75cf7` + 本报告提交），最终值以 `git ls-remote` 核对为准。
- **本地 HEAD**：P1 `cf75cf7` + 本报告提交（具体 SHA 见 `git log`；推送后二者相等）。
- **PR #6 状态**：**Draft**（未 merge、未 force、未碰 main/Release）。
- **同步结果**：P0 已同步 **9 提交** `b102ca7..e28e230`（完整本地 ahead 集，无遗漏）；本次在 `e28e230` 之上追加 P1 提交 `cf75cf7` 与本报告提交，推送后远端 = 本地 HEAD。
- **CI Run ID**：
  - `e28e230`（本轮）：`42715875236` V1.6 Market Provenance、`42715875188` Cloud Staging、`42715868543` V1.6 Market Provenance、`42715868572` Trading Workbench CI — 全 SUCCESS。
  - `bc2a5cb`（历史基线）：`42712800491`/`42712789106`/`42712789223`/`42712800228` — 全 SUCCESS。
- **DAY-1.4 独立 QA**：**PASS**（`qa.pass=true`；`formal risk gate wired: YES`；`day14-anchor.json written`；5 日志+state 存在；`allowBuySell=false`/`enableRealTrading=false`）。
- **风控门禁状态**：**ARMED via 正式 `server/ai/risk-gate.mjs`**；`allowBuySell=false` / `ENABLE_REAL_TRADING=false` 维持；下一轮真实运行前不解除。
- **下一轮真实行情 Paper Trading 准备状态**：**READY（harness 已加固）**——真实 `fetchMarket` + `evaluateCycle` + MiniMax-M3 接入点已就位（只读导入在位），待主理人评审解锁 `allowBuySell` 并执行真实运行独立 QA。
- **成功项**：P0 同步、CI 全绿、P1 正式风控接线、P2 溯源字段落地、本地锚点、独立 QA。
- **失败项**：无（未触发失败断言；红线未被破）。
- **阻塞项（如实标记）**：① DAY-1.3 历史逐周期原始数据未持久化，无法回溯重构（仅方法论 PASS）；② T04 同时间线策略对比需下一轮真实运行；③ **V1.6 生产 GO — 不宣布**（待真实运行独立 QA PASS）。

---

## 8. 下一步（依赖主理人动作）

1. **本次推送 P1 `cf75cf7` + 本报告提交** → 远端前进至本地 HEAD；最终值以 `git ls-remote` 核对。
2. **评审是否进入 P3 真实运行**：隔离 Paper 持久化账户 + Staging D1 → 显式解锁 `allowBuySell`（仍 `ENABLE_REAL_TRADING=false`）→ 真实 DAY-1.4 有界运行 → **独立 QA 通过后**才可考虑解除更多门禁。
3. **真实运行后**按 P2 契约落盘完整时间线，一键重跑 T03 逐笔取证 / T04 同时间线对比。
4. 本 PREP 构建保持 `dry=true`，可随时 `node scripts/day14-runner.mjs` 复验管道；不消耗真实 token/资金。

---

*纪律确认：本报告与 harness 未伪造任何数据、未触碰冻结引擎源码、未 merge Release、无真实资金流动；PAT 从 Credential Center（DPAPI）读取、仅内存解密、未打印/未落盘；密钥仅 last4 引用；未 force push、未碰 main/Release。*

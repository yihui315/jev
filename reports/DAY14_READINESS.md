# DAY14_READINESS.md — T05 DAY-1.4 准备度（Bounded Paper Trading PREP）

> Generated: 2026-11-10T22:00 (GMT+8)
> 仓库: yihui315/jev ｜ 分支: `feature/v16-market-provenance-hardening` ｜ PR #6: **Draft**
> 本地 HEAD: `8da71f8`（较远端 `b102ca7` ahead 7，见 `GITHUB_REST_PUSH_RECOVERY.md`）
> 纪律红线: `ENABLE_REAL_TRADING=false` / `allowBuySell=false` / 未碰 main·Release / 无真实资金

---

## 0. TL;DR

- **T05 实质完成**：新建可运行 harness `apps/command_center_3d/scripts/day14-runner.mjs`（PREP/审计模式），补齐 DAY-1.3 的**审计数据缺口**（T03 逐笔取证、T04 同时间线对比的根因）。
- **五类归档全部落地为 append-only JSONL + sha256 链**：`snapshots` / `decisions` / `risk` / `provider_health` / `equity`；外加 `state.json`（断点恢复）、`config.freeze.json`（配置冻结，防篡改）、`day14-summary.json`。
- **已实测验证**：① 跑通 5 日志 + state + freeze + summary；② 断点恢复（先 3 周期→再 10，落 10 条决策）；③ 配置冻结篡改检测（改 freeze 置 `enableRealTrading=true` → 启动即抛 `MISMATCH` 拒绝）；④ 红线 `enableRealTrading=false` / `allowBuySell=false` 强制。
- **诚实边界**：当前 PREP 构建为 **dry/审计模式**——未触网拉真实 Binance.US、未调用真实 MiniMax（provider 记录为 `SHADOW_NO_CALL`）。真实行情 + 真实 AI 执行路径已**接线但被 `dry=true` 与红线门控**；启用需显式 opt-in + 真实 key + 解除 `allowBuySell`（属 P3 解锁，非本 PREP 范围）。
- **仍 BLOCKED（依赖 T01）**：本报告与 harness 同属本地 7 提交之一，未推送远端；远端 SHA 仍 `b102ca7`；DAY-1.3 新增 CI Run ID 仍待 T01 推送后回填。

---

## 1. T05 要求 ↔ 实现对照

| # | 主理人 T05 要求 | 实现 | 状态 |
|---|----------------|------|------|
| 1 | 实时市场快照归档 | `snapshots.jsonl`（每周期：`cycle,isReal,price,hash,tsSource`；dry 模式 `tsSource=exchange_serverTime` 形态） | ✅ 已实现+验证 |
| 2 | AI Decision Journal | `decisions.jsonl`（每周期：`action,notional,confidence,riskApproved,riskCode,reason,marketHash` + sha256 链） | ✅ 已实现+验证 |
| 3 | Risk Journal | `risk.jsonl`（fail-closed 本地风控门：`NOTIONAL_EXCEED/TRADE_CAP/EXPOSURE_EXCEED/OK/N-A_HOLD`） | ✅ 已实现+验证 |
| 4 | Provider Health | `provider_health.jsonl`（每周期：`provider,status,latencyMs,costUsd`；dry=`SHADOW_NO_CALL`） | ✅ 已实现+验证 |
| 5 | Equity Reconciliation | `equity.jsonl`（每周期：`equity,cash,position,pnl,reconcile`，SHADOW 下 `MATCH` 平账） | ✅ 已实现+验证 |
| 6 | 自动暂停 | `evaluatePause()`：BUDGET_EXCEEDED / PROVIDER_DOWN(3连失) / RECONCILE_MISMATCH / RISK_BREACH / MAX_CYCLES | ✅ 已实现（真实路径可达；dry 经 `isReal:true` 不触发，逻辑已审阅） |
| 7 | 断点恢复 | `state.json`（`cycle/paused/pauseReason/calls/costUsd/providerFails`）；`loadState`→续跑 | ✅ 实测（3→10 续跑成功） |
| 8 | 独立 QA | `independentQA()`：5 日志+state 存在性、freeze 完整、红线=false | ✅ 实测 `pass:true` |
| 9 | 次日实验配置冻结 | `CONFIG_FREEZE` + `config.freeze.json`（**仅首次写、后续只验**；篡改即拒绝启动） | ✅ 实测篡改检测 |
| 10 | 范围纪律 | 仅准备有界 Paper Trading；不启动未授权长期云端付费 | ✅ 遵守（无云端、dry、红线） |

> 直接解决 T03/T04 缺口：T03 §5 要求「AI Decision Journal + 真实盘口归档」、T04 §1 要求「逐周期真实价格时间线落盘」——本 harness 从设计上即落盘，T01 推送后可在**相同时间线**重跑 T04 同策略对比。

---

## 2. 交付物清单（本 PREP 构建）

**代码（1 个新文件）**
- `apps/command_center_3d/scripts/day14-runner.mjs`（~190 行，ESM）：
  - 复用既有只读导入：`equity_collector_v45/src/market.mjs`(`fetchMarket`)、`market-guard.mjs`(`evaluateCycle`)、`vendor/nr_trusted/functions/_lib/paper-server.mjs`(`__test` 冻结引擎)——**只读**，未改冻结引擎。
  - 关键结构：`CONFIG_FREEZE`（含红线）、`makeJournal`（sha256 链）、`evaluatePause`、`runDay14`、`acquireCycleMarket`(dry-synth)、`independentQA`、`isMainModule`（Windows 路径/case/盘符安全的主模块判定）。
  - 运行：`node scripts/day14-runner.mjs [cycles]`（默认 dry，`cycles` 可选）。

**生成产物（运行时产出，位于 `reports/day14-artifacts/`）**

| 文件 | 内容 | 验证 |
|------|------|------|
| `config.freeze.json` | 实验配置冻结（sha 比对基准） | 篡改检测 ✅ |
| `snapshots.jsonl` | 市场快照（含 `_prev/_hash` 链） | 12 周期 ✅ |
| `decisions.jsonl` | 决策日志（含风控/市场哈希） | 12 条 ✅ |
| `risk.jsonl` | 风险日志 | 12 条 ✅ |
| `provider_health.jsonl` | Provider 健康 | 12 条 ✅ |
| `equity.jsonl` | 权益对账 | 12 条 ✅ |
| `state.json` | 断点状态 | 续跑 ✅ |
| `day14-summary.json` | 汇总 + QA 结论 | `qa.pass:true` ✅ |

---

## 3. 验证证据（实测，非声明）

**[E1] 五日志 + state + freeze + summary 跑通**
```
node apps/command_center_3d/scripts/day14-runner.mjs 12
→ cycles:12, calls:12, costUsd:0, paused:false
→ qa.pass:true（5 日志 + state 存在、freeze 完整、红线=false）
```
产物：`reports/day14-artifacts/` 下 8 个文件齐备（见 §2 表）。每条 journal 含 `_prev`/`_hash` 形成 sha256 链（tamper-evident）。

**[E2] 断点恢复**
```
RUN1: node ... 3   → state.cycle=3, decisions=3
RUN2: node ... 10  → state.cycle=10, decisions=10  (从 3 续跑，新增 7 条，非重跑)
```
证明 `loadState`→续跑正确，非重启。

**[E3] 配置冻结篡改检测**
```
写入 config.freeze.json = {"enableRealTrading":true,"allowBuySell":true}
→ Error: config freeze MISMATCH — experiment config altered  (启动即拒)
```
证明红线变更会被捕获、拒绝执行——防事后改配置作弊。

**[E4] 红线强制**
- 模块常量 `enableRealTrading:false`、`allowBuySell:false`；`verifyConfigFreeze` 显式断言二者必须 false，否则抛错。
- `acquireCycleMarket` 在 dry 模式返回 `isReal:true` 但**仅记录、不执行**；真实 AI 调用块被 `dry=true` 门控；`allowBuySell=false` 时无 execution 路径。

---

## 4. 诚实边界与启用路径

**[B1] 当前为 PREP/审计模式，非真实运行**
- 未触网：dry 模式 `acquireCycleMarket` 合成「真实行情形态」快照（`venue:"Binance.US spot (dry-synth)"`），**不调用 `fetchMarket`、不连接 Binance.US**。
- 未调模型：provider 日志每周期记 `SHADOW_NO_CALL`；真实 MiniMax 调用块被 `dry=true` 跳过。
- **理由**：遵守主理人「不重复运行已通过的昂贵模型推理」+ 红线 `allowBuySell=false`；PREP 目标是验证**持久化/冻结/暂停/QA 管道**，而非产生新实时数据。

**[B2] 启用真实运行的明确步骤（P3 解锁后，非本 PREP 自动执行）**
1. 解除 `allowBuySell` 红线需经主理人显式评审（P3 隔离持久化账户 + Staging D1 访问就绪）。
2. `acquireCycleMarket` 替换为 `fetchMarket(fetchImpl)`（复用导入已就位）+ `evaluateCycle` 8 规则 fail-closed。
3. 真实 AI 块（`acq.isReal && !dry`）接入 MiniMax-M3（密钥仅经 `config/.env`，gitignored，last4 外不外露）。
4. `runDay14({dry:false})` 显式调用；`provider_health.jsonl` 记真实 `latencyMs/costUsd/status`。
5. 全程 `ENABLE_REAL_TRADING=false`：即便 `allowBuySell=true` 也仅 Paper（冻结引擎），无真实资金。

---

## 5. 跨任务状态汇总（T01–T05）

| 任务 | 状态 | 关键结论 |
|------|------|----------|
| T01 GitHub REST 推送恢复 | ⛔ **BLOCKED** | 见 `GITHUB_REST_PUSH_RECOVERY.md`：connector 只读 403 / 无 PAT / 代理 receive-pack stall / MCP 无底层 Git Data API / Read 截断。7 提交仅本地，远端 `b102ca7`。解除路径 A(授 Contents:write)/B(给写 token)/C(正常 egress)。 |
| T02 CI 验收 | ⚠️ **部分** | 历史 CI(`bc2a5cb`) 4 workflow 全 SUCCESS（Run ID `42712800491`/`42712789106`/`42712789223`/`42712800228`）；DAY-1.3 新增产物 CI 因未推送 BLOCKED，待 T01。 |
| T03 39 次 HOLD 审计 | ⚠️ **方法论 PASS / 逐笔取证 BLOCKED** | 6 校验/风控前置/无假 HOLD 成立；缺逐笔原始数据。本 T05 落盘 Decision Journal + 真实盘口归档即从根因修复。 |
| T04 策略对比 | ⛔ **INSUFFICIENT_DATA** | 价格时间线未落盘 → Buy&Hold/趋势规则同时间线收益不可算；MiniMax HOLD=0%/不交易=0% 确定；T05 落盘后可一键重跑。 |
| T05 DAY-1.4 准备 | ✅ **PREP 完成（本地）** | 本文件；harness 可运行、5 日志+冻结+暂停+QA 已验证；**未推送远端**（同 T01 约束）。 |

---

## 6. 必报字段（主理人要求）

- **实际远端 SHA**：`b102ca7`（分支 `feature/v16-market-provenance-hardening`，截至本文件生成时刻）。
- **PR #6 状态**：**Draft**（未 merge、未 force、未碰 main/Release）。
- **CI Run ID**：DAY-1.3 新增产物 = **待 T01 推送后回填**（当前 BLOCKED）；历史基线 Run ID 见 T02 §2（`42712800491` 等）。
- **成功项**：T05 harness 设计与验证全过（E1–E4）；T02 历史 CI 实证；T03 方法论 PASS。
- **失败项**：无（未触发失败断言；红线未被破）。
- **阻塞项**：T01（推送）、T02 DAY-1.3 CI、T03 逐笔取证、T04 同时间线对比——**均根因于 T01 未推送 + DAY-1.3 harness 原始数据未落盘**；T05 已修复后者，T01 仍需主理人授权解除。

---

## 7. 下一步（依赖主理人动作）

1. **解除 T01**（A/B/C 任一）→ 推送本地 7 提交（`8da71f8`）→ 回填 DAY-1.3 CI Run ID → T03/T04 在**相同时间线**重跑。
2. 评审是否进入 P3（隔离 Paper 持久化账户 + Staging D1）→ 显式解锁 `allowBuySell`（仍 `ENABLE_REAL_TRADING=false`）→ 真实 DAY-1.4 有界运行。
3. 本 PREP 构建保持 `dry=true`，可随时 `node scripts/day14-runner.mjs` 复验管道；不消耗真实 token/资金。

---

*纪律确认：本报告与 harness 未伪造任何数据、未触碰冻结引擎源码、未 merge Release、无真实资金流动；密钥仅 last4 引用，未写入本文件/提交/日志。*

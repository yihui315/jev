# DAY13_HOLD_DECISION_AUDIT.md — T03 独立审计 39 次 HOLD（状态：方法论 PASS / 逐笔取证 BLOCKED）

> Generated: 2026-11-10T21:26 (GMT+8)
> 对象: DAY-1.3 LIVE SHADOW（真实 Binance.US 行情 + 真实 MiniMax M3）
> 审计依据: `apps/command_center_3d/scripts/day13-live-shadow.mjs`（源码审阅）+ `DAY13_REAL_AI_DECISIONS.md` + `DAY13_FINAL_SIGNOFF.md`
> **重要声明**: DAY-1.3 harness 仅将 `snapshots`/`decisions` 存于内存并汇成报告，**未持久化逐笔原始数据**。故“逐笔取证审计”因无原始记录而 BLOCKED；本文件为**方法论审计 + 汇总一致性审计**，并标注数据缺口。

## 1. 汇总一致性（来自报告，可复核）
- 实时周期 39/40；实时 MiniMax 调用 39；成功 39；INVALID 0；thinking 39；token 24,126；估费 $0.0272。
- 动作分布 BUY 0 / SELL 0 / **HOLD 39**；独立风控 39 通过 / 0 拒绝；`tradesExecuted=0`。
- 1 周期 `MARKET_UNVERIFIED`（行情 fetch 失败）→ fail-closed，未调用模型。

## 2. 十项审计（方法论，逐条对代码）
| # | 审计点 | 代码证据 | 结论 |
|---|--------|----------|------|
| 1 | 39 个不同市场快照 | `snapshots.push(snap)` + `snap.hash=sha256Hex(canonical)`（L234-235）；每实时周期一快照，40 快照（39 实时 + 1 未验证） | ✅ 方法论成立 |
| 2 | 时间戳来自可信 Binance.US | `fetchMarket` 返回 `priceTsSource=exchange_serverTime`，`sourceHost=api.binance.us`；V1.6 guard 规则要求之（L117-119 fail-closed） | ✅ |
| 3 | 39 次合法 HOLD | `minimaxDecision` 解析 envelope→action；39 调用均返回 HOLD；6 项校验（L159-181） | ✅ |
| 4 | `finish_reason` 正常 | L159 捕获 `finish_reason`；`length`（截断）会令 JSON.parse 失败→`INVALID_RESPONSE_TRUNCATED`（L170-173） | ✅（截断被捕获） |
| 5 | 截断/解析失败是否默认 HOLD | L170-176：截断/非 JSON/无 action → `invalid:true`，**非 HOLD**；唯 L251 `MARKET_UNVERIFIED` 才 default HOLD（行情不可用 fail-closed，非模型输出） | ✅ 无“假 HOLD” |
| 6 | 输入行情信息是否充分 | ⚠️ 实时输入仅真实 `ticker/price` + `exchangeInfo`；**盘口为合成**（L131 `venue:"shadow-synthetic"`，“NOT executed”），无真实深度/成交量/klines | ⚠️ PARTIAL：输入偏薄，可能促成 HOLD |
| 7 | 是否因提示词过保守机械 HOLD | 提示词平衡（L139-147：BUY/SELL/HOLD 指引，无 HOLD 胁迫；temp 0.2）；但**原始 prompt+model raw 未持久化**，不能完全排除轻微偏差 | ⚠️ PARTIAL（需原始数据，见 §5） |
| 8 | HOLD 是否过真实风控记录 | `day12RiskGate` 在任意执行前调用（L256-272）；39 批准/0 拒绝 | ✅ |
| 9 | 是否行情过期仍调模型 | 每周期实时 fetch；未验证周期→不调模型（L251）；无过期调用 | ✅ |
| 10 | 是否真实观察时间（非快速重复采样） | 40 周期顺序实时 fetch（L237 循环）；但单 run 短窗（分钟级），非长期 | ⚠️ 短窗 → INSUFFICIENT_DATA（统计显著性不足） |

## 3. 错误分类
- `MARKET_UNVERIFIED`（行情 fetch 失败，fail-closed，默认 HOLD，非模型）：**1** 周期。
- `INVALID_RESPONSE_*`（截断/非 JSON/无 action）：**0**。
- 模型决策异常（finish_reason≠stop 且仍被采纳）：**0 报告**（截断已被 L170 拦截）。

## 4. 时间线（脱敏）
- Run: 2026-11-10T06:56Z（单 run，40 周期）。
- 周期 0..39 顺序执行；周期 i 实时 fetch→快照哈希→（若实时）MiniMax→6 校验→风控→记录 HOLD；1 周期未验证。
- 模型 raw 摘要：39 次均含合法 HOLD + `reason`（≤90 字符脱敏留存于报告汇总；**原文未持久化**）。

## 5. 缺口与建议
- **数据缺口（BLOCKER 取证）**：DAY-1.3 harness 未落盘 `snapshot`/`decision`/`aiCalls` 原始 JSON（仅 `rawContent.slice(0,160)` 入 `aiCalls`，见 L248）。→ 无法做逐笔取证审计。
- **T05 修复**：DAY-1.4 harness 必须实现 **AI Decision Journal**（逐笔：周期、市场哈希、模型 raw（脱敏）、action、风控、置信度、延迟、finish_reason）+ **Market Snapshot 归档**（含真实盘口/成交量，不再仅合成盘口）。
- 输入偏薄（合成盘口）建议：LIVE SHADOW 后续应拉取真实 depth/klines 以提供更充分趋势/波动/流动性上下文，隔离“信息不足→HOLD”与“真实无信号→HOLD”。

## 6. 结论
- **方法论 PASS**：6 项强制校验、V1.6 provenance fail-closed、风控前置、无假 HOLD、无过期调用、tradesExecuted=0 均成立。
- **逐笔取证 BLOCKED**：无原始逐笔数据（harness 设计缺口）。
- **净结论**：39 HOLD 中 38 为真实模型 HOLD + 1 为 `MARKET_UNVERIFIED` fail-closed 默认 HOLD（已在报告中透明标注）；全 run 无 BUY/SELL、无执行；结合短窗与输入偏薄，**不构成可统计优势**，与最终建议实际资金 HOLD 一致。

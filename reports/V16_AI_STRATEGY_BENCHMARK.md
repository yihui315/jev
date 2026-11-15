# JEV V1.6 — AI Strategy Benchmark（轨道 B · 当前状态：NO_DATA）

- 报告人：主理人（Owner）。日期：2026-11-09。
- 状态：**NO_DATA / NOT STARTED**——本报告不包含任何策略绩效数字，因为没有发生过任何一次真实 AI 推理或 Mock 之外的模拟成交。

## 1. 为什么是 NO_DATA

1. **无任何 AI Provider 凭据**（DPAPI 凭据库 18 个条目中零 AI 密钥）→ Real AI 推理 0 次。
2. Mock Contract Test（83/83 PASS）**按协议不计入策略成绩**——Mock 只验证软件契约。
3. Paper E2E 尚未运行（前置：真实 Provider 连通 + 可信市场快照）。
4. 30 天 Paper Trading 计时**未开始**——按主理人规格，只有在可信权益快照、真实 Provider 和安全门禁满足后才可正式计时。

## 2. 已就绪的评测基础设施（VERIFIED）

- 评测口径与基线设计已由主理人规格固定：Buy & Hold / 不交易 HOLD / 简单规则趋势 / AI 策略同条件对比。
- 指标集已定义：净收益率、手续费与滑点、最大回撤、胜率、盈亏比、Sharpe/Sortino（样本不足标 NO_DATA）、风控拒绝率、Provider 成本与延迟、数据缺失率、持仓暴露、OOS 样本数。
- 归因底座已建：`ai_decisions` + `decision_order_links`（旁路关联，不动冻结订单表）+ `ai_market_contexts`（verified 快照引用）。
- Mock / Real 隔离由服务器写入的 `data_mode` 字段强制，统计管道将按 data_mode 分口径，禁止混合。

## 3. 解除 NO_DATA 的前置门禁（按序）

1. 主理人提供至少一个 Provider 凭据（OpenAI 原生 / OpenAI 兼容 / Ollama）。
2. `POST /api/jev/ai/providers/test` 真实最小推理 PASS（网络可达 / 认证有效 / 模型存在 / 结构合法 / 延迟可测）。
3. Real AI Paper E2E 在独立测试账户跑通：决策 → 独立风控 → 现有 Paper 引擎 → D1 账本 → Equity Collector 快照对账。
4. 首轮验收门槛（主理人拟定）：≥1 个 Provider 可验证调用、100 条受控测试决策、100% 风控负例拒绝、≥20 次 Paper E2E 场景、幂等与隔离 100%。

## 4. 禁止误解

- 本报告的 NO_DATA **不是 FAIL**，是诚实标注：未发生即未计分。
- 禁止把 Mock Contract Test 的 83/83 写成任何形式的策略能力证据。
- 禁止把「接口就绪」写成「AI 已运行」。

—— 本报告不含任何 Token / 凭据明文。

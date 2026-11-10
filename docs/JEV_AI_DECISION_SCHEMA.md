# JEV AI 决策契约（V1.6 轨道 B）

- 版本：`jev.ai.decision/v1`
- 实现：`apps/command_center_3d/server/ai/decision-schema.mjs`
- 运行时校验：手写 `validateDecision()`；`decisionSchemaDocument()` 输出等价的 JSON-Schema 文档（仅作文档/对账，不作为运行时校验器）
- 持久化：`ai_decisions`（`server/ai/migrations/0006_ai_decisions.sql`）

---

## 1. 标准决策结构

```json
{
  "schemaVersion": "jev.ai.decision/v1",
  "strategyId": "strat-btc-breakout-01",
  "symbol": "BTCUSDT",
  "action": "BUY",
  "side": "BUY",
  "orderType": "MARKET",
  "quantity": 0.001,
  "limitPrice": null,
  "confidence": 0.62,
  "reason": "breakout above range with volume expansion",
  "riskFlags": ["VOLATILITY_ELEVATED"],
  "marketContextId": "mctx-0123456789abcdef01234567",
  "validForSeconds": 60
}
```

字段白名单固定 14 个，**多余字段一律拒绝**（`DECISION_UNKNOWN_FIELD`）。

| 字段 | 类型 | 约束 |
|---|---|---|
| `schemaVersion` | string | 必须等于 `jev.ai.decision/v1` |
| `strategyId` | string | `[A-Za-z0-9._:-]{3,100}` |
| `symbol` | string | 大写 `[A-Z0-9]{5,20}`；交易白名单由风控另行校验 |
| `action` | enum | `HOLD \| BUY \| SELL` |
| `side` | enum/null | `HOLD` 时必须 `null`；可执行时须等于 `action` |
| `orderType` | enum/null | `HOLD` 时 `null`；否则 `MARKET \| LIMIT` |
| `quantity` | number/null | `HOLD` 时 `null`；否则有限数且 `> 0`（**不接受字符串**） |
| `limitPrice` | number/null | `LIMIT` 时必须 `> 0`；`MARKET`/`HOLD` 必须 `null` |
| `confidence` | number | `[0,1]`；**仅为模型自报，风控不读取** |
| `reason` | string | 1..2000 字符 |
| `riskFlags` | string[] | ≤12 项，每项 1..200 字符 |
| `marketContextId` | string | `[A-Za-z0-9._:-]{3,100}`，必须指向已存快照 |
| `validForSeconds` | integer | 1..3600 |

### 形态不变量

- `action = HOLD` ⇒ `side`、`orderType`、`quantity`、`limitPrice` **全部为 null**。
- `action = BUY | SELL` ⇒ `side` 等于 `action`、`orderType` 必填、`quantity > 0`；`LIMIT` 必须带 `limitPrice > 0`。
- SQL 侧有一份等价 `CHECK`，即使写入方有 bug 也无法落库不一致的行。

---

## 2. 校验错误码

| code | 含义 |
|---|---|
| `DECISION_NOT_OBJECT` | 顶层不是普通对象 |
| `DECISION_UNKNOWN_FIELD` | 白名单外字段 |
| `DECISION_MISSING_FIELD` | 必填字段缺失 |
| `DECISION_TYPE` | 类型错误（不做隐式转换） |
| `DECISION_ENUM` | 枚举外取值 |
| `DECISION_RANGE` | 数值/长度越界 |
| `DECISION_PATTERN` | 标识符/交易对格式不符 |
| `DECISION_HOLD_SHAPE` | HOLD 携带了订单字段 |
| `DECISION_EXEC_SHAPE` | BUY/SELL 形态不一致（side≠action、MARKET 带限价等） |

---

## 3. 服务器风控（独立于 AI，不可覆盖）

`server/ai/risk-gate.mjs` 输出 `{approved, reason, code}`。放行顺序：

1. `RISK_INVALID_DECISION` — schema 不通过
2. `RISK_HOLD_NO_ORDER` — HOLD，无单可下（approved=true）
3. `RISK_SYMBOL_NOT_ALLOWED` — 不在云端白名单 `BTCUSDT / ETHUSDT`
4. `RISK_CONTEXT_MISSING` — `marketContextId` 查无快照
5. `RISK_CONTEXT_NOT_VERIFIED` — `mark_quality != 'verified'`
6. `RISK_SYMBOL_MISMATCH` — 快照与决策交易对不一致
7. `RISK_CONTEXT_INVALID_TS` / `RISK_CONTEXT_STALE` / `RISK_CONTEXT_EXPIRED` — 快照时间无效、超过有效期（`min(validForSeconds, 60s)`）、硬过期
8. `RISK_PRICE_UNAVAILABLE` — 快照无可用价格
9. `RISK_NOTIONAL_TOO_SMALL` / `RISK_LIMIT_EXCEEDED` — 名义金额或数量超限（默认 5..1000 USD，数量 ≤10）

**confidence 免疫**：风控代码不读取 `decision.confidence`；测试 `t3` 用 0.01 与 1.0 两组输入证明裁决完全相同。

**快照来源**：`ai_market_contexts` 仅在网关用既有 `fetchVerified`（真实 2xx + sha256）写入，AI 无法自报行情。

---

## 4. 六角色映射（OWNER-DECIDED 草案）

现状：三套词汇并存（V1.6 需求名 / 冻结编排器代码名 / 旧订单侧词汇）。本表为**草案**，代码标识符**未重命名**，等待主理人批准。

| # | V1.6 需求角色 | 代码角色 | 类型 | 置信度 | 语义 |
|---|---|---|---|---|---|
| 1 | Market Analyst | SPOTTER | LLM | HIGH | 发现机会，产出市场观察 |
| 2 | Strategy Researcher | PRIOR | LLM | MEDIUM | 形成 regime/先验假设，区分事实与推断 |
| 3 | Risk Officer | EDGE | LLM + 服务器 | MEDIUM | LLM 层提出反证；**绑定裁决是 risk-gate 确定性闸门**，不是 LLM |
| 4 | Execution Planner | TAKER | 确定性 | HIGH | 仅提案式执行计划；仓位由 KELLY 决定（无服务端 OOS 样本时 fail-closed） |
| 5 | Performance Analyst | CLOSER | LLM | MEDIUM | 事后复盘、失效条件、失败模式 |
| 6 | Supervisor | （无对应代码角色） | 服务器 + 人工 | NONE_IN_CODE | 由服务器风控 + 主理人签核承担，AI 不能自批执行 |

- 映射状态常量：`ROLE_MAPPING_STATUS = "OWNER-DECIDED DRAFT (pending owner sign-off)"`
- `renamePerformed: false`；`codeChanged: false`（全部 6 行）
- 机器可读副本：`server/ai/roles-mapping.mjs`（测试 `t8` 校验其与本文一致）

---

## 5. D1 表

### `ai_decisions`（主表）

`decision_id`(PK), `strategy_id`, `provider_id`, `model_id`, `run_id`, `symbol`, `action`, `side`, `order_type`, `quantity`(1e8), `limit_price`(1e8), `confidence`(1e8 ratio), `reason`, `risk_flags_json`, `market_context_id`, `input_snapshot_sha256`, `prompt_version`, `request_payload_sha256`, `response_payload_sha256`(脱敏后), `price_ts`, `data_mode`(`mock|real`), `risk_check_result`(`PASS|REJECT|NOT_RUN`), `paper_order_id`, `paper_fill_ids_json`, `status`(`PENDING|REJECTED|APPROVED|SUBMITTED|FAILED`), `error_code`, `created_at`

> 列集合中不存在任何 `secret` / `authorization` / `api_key` 字段（测试 `t7` 断言）。

### `decision_order_links`（旁路归因）

`decision_id`, `order_id`, `created_at`，`UNIQUE(decision_id, order_id)`；**不改冻结 `paper_orders`**。

### `ai_market_contexts`（风控依据）

`market_context_id`(PK), `symbol`, `mark_price`(1e8), `price_ts`, `mark_quality`(`verified|unknown`), `source`, `source_sha256`, `expires_at`, `created_at`

---

## 6. 字段语义红线

- `confidence`：审计字段，**不是概率、不是放行条件**。
- `data_mode`：服务器赋值；`mock` 决策永远不会以 `real` 写入或返回。
- `risk_check_result`：服务器裁决；`PASS` 只可能由 `risk-gate.mjs` 产生。
- `paper_order_id`：仅在冻结引擎真实受理后写入；为空表示未下单，不用 0 或占位符代替。

# V1.4 · T04 — Paper Trading 完整闭环验收

- 执行时间（UTC）：2026-11-08 15:18 – 15:25
- 部署 SHA：`ee237c2d`（主验收）；跨部署复核 `4b4dec81`
- 账户：隔离 Staging 会话 `v14_alpha` / `v14_beta` / `v14_persist`（仅 Paper，无真实资金）
- 测试数量：22 项 → **PASS 21 / FAIL 0 / BLOCKED 1**
- 结论：**PASS** — OPEN / CANCELLED / FILLED 闭环通过；依赖真实行情的测试无需标 BLOCKED（T02/T03 已通过）

## 1. 双会话与初始账户

| 会话 | 初始 equity | cash | positions | version |
|---|---|---|---|---|
| ALPHA | 10000 | 10000 | 0 | 1 |
| BETA | 10000 | 10000 | 0 | 1 |

`initialCash` 请求值 `"100000"` 被引擎忽略：冻结引擎常量 `CFG.initialCash = "10000"`。这是既有设计，本轮未修改，故 equity 为 10000 而非 100000（如实记录，非缺陷）。

## 2. LIMIT → OPEN → CANCELLED

```
POST /api/jev/paper/orders   LIMIT BUY BTCUSDT qty=0.01 limitPrice=40000.00
  -> accepted=true  status=OPEN  limitPrice=40000.00000000
     order_id=O-6685f6e9-4064-459b-b6b0-75f3861d2415

POST /api/jev/paper/orders/{id}/cancel  clientActionId=v14-cancel-0001
  -> ok=true  status=CANCELED  cancelledQty=0.01000000
```

## 3. MARKET → FILLED → 持仓 / 费用 / 余额

```
POST /api/jev/paper/orders   MARKET BUY BTCUSDT qty=0.01
  -> ok=true accepted=true status=FILLED
  -> fills=3  成交价 = 76037.47000000 / 76037.53000000 / 76037.56000000
              手续费 = 0.14979382 / 0.15055431 / 0.46002724
  -> positions = { BTCUSDT: { qty: 0.01, avgPrice: 76113.57, reservedQty: 0 } }
  -> summary.cash = 9238.86426133（初始 10000，含费用扣减）
     marketValue = 760.3564   equity = 9999.22066133
     unrealizedPnl = -0.77933867   drawdown = 7.793e-05
  -> markQuality = verified   stateHash = 1e21007a51c5cc8c37bd…
```

3 笔成交价对应 Binance.US 真实盘口档位，非合成。`cash_decreased=true`、`fees_present=true`。

## 4. 幂等（顺序重复）

重复提交同一 `clientOrderId = v14-market-btc-01`：

```
-> ok=true  duplicate=true  accepted=true  fills=0  order.status=FILLED
```

返回原订单，不产生新成交。

## 5. 四类风控 / 异常拒绝（全部 `fills=0`）

| 用例 | 结果 | 原始错误（脱敏后） |
|---|---|---|
| 余额/风险：qty=5（约 5×76000 > 10% 净值） | `accepted=false`, `status=REJECTED`, fills=0 | `超过单笔风险上限（账户净值的 10%）` |
| 非法数量：qty=0 | HTTP 400, `ok=false`, fills=0 | `订单数量必须为正数` / `INVALID_ORDER` |
| 非法价格：LIMIT price=1 | `accepted=false`, `status=REJECTED`, fills=0 | `订单金额低于交易所最低名义金额` |
| 禁止/未上市标的：ORDIUSDT | `accepted=false`, `status=REJECTED`, fills=0 | `MARKET_HTTP_400`（上游真实 400，绝不伪造成交） |

`qty=0` 一项因引擎在校验层直接返回 400（无 order 对象），已查原始响应体确认不是网关异常，判定 PASS。

## 6. 双会话隔离（账户 / 订单 / 成交 / 事件）

| 会话 | positions | trades | version | audit.counts | 明细 orders/fills/events |
|---|---|---|---|---|---|
| ALPHA | 1 | 3 | 7 | `{accounts:1, orders:5, fills:3, server_events:16}` | 5 / 3 / 16 |
| BETA | 0 | 0 | 1 | `{accounts:1, orders:0, fills:0, server_events:1}` | 0 / 0 / 1 |

`admin_global_counts` 已从两个会话的响应中**消失**（`has_admin_global_counts=False`）。BETA 无法推断 ALPHA 的订单数或成交数。

## 7. 事件链与账本回放一致性

`GET /api/jev/paper/events` → 16 条事件：

```
paper.server.account.snapshot : 7
paper.server.order.filled     : 3
paper.server.order.rejected   : 3
paper.server.order.created    : 2
paper.server.order.cancelled  : 1
```

`GET /api/jev/paper/replay`：

```
verified = true
lastSequence = 56
materializedVersion = 7
replayedVersion     = 7
error = null
stateHash = 6e0d40687d34eea05a3aac…
```

重放版本与物化版本一致，事件链与状态哈希自洽。

## 8. 重启 / 重新部署后一致性（跨 `ee237c2d` → `4b4dec81`）

- ALPHA：version 7 → 8（期间并发测试又下一单，非数据丢失），positions 1 → 1，回放 `verified=true`，materialized/replayed 均 8。
- `v14_persist` 会话（无后续写入）：下单时 `stateHash=43cc6d3163b2f319e03a1bc5…`，跨部署后连续 3 次读取 `version=2`、`cash=9977.5849073`、stateHash **完全一致**，且与重部署前基线相等。

→ D1 账本持久化与重放一致性 PASS。

## 9. 并发生幂等（T05 交叉项）

5 个并发相同 `clientOrderId` 请求：1 个 `FILLED`（6 笔成交），4 个返回 `PAPER_ORDER_FAILED`：

```
D1_ERROR: UNIQUE constraint failed:
paper_orders.session_id, paper_orders.client_order_id
```

最终 D1 中 `v14-concurrent-001` **仅 1 行**，无重复 clientOrderId、无双重成交。数据库唯一约束正确兜底，账本一致（PASS）。

## 10. 未通过 / 阻塞

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| B1 | `paper_equity_snapshots` 权益快照表 | **BLOCKED / NO_DATA** | 0 行；冻结引擎不写该表，V45 collector 未部署。已有的 7 条 `paper.server.account.snapshot` 事件与实时 `summary.equity` 是真实计算，**不冒充快照表**。按"无第二账本/无合成数据"未伪造写入。 |

其余 T04 21 项全部 PASS。真实交易开关：`health.real_money=false`、`real_exchange_orders=false`、`engine_modified=false`（全程）。

## 11. 证据路径

- `reports/evidence/T04_limit_open.json`、`T04_cancel.json`、`T04_market_filled.json`、`T04_idempotency.json`
- `reports/evidence/T05_replay.json`、`T05_audit_isolation.json`（session_id 已哈希脱敏）
- 未入库原始响应（含会话标识）：`D:/cf-deploy/v14_*.json`

> 报告不含 Basic Auth 密码、Cookie 值、Session ID 原文及任何密钥。订单号为业务 UUID，保留以保证证据可复核。

# V1.4 · T05 — D1 可靠性与安全测试

- 执行时间（UTC）：2026-11-08 15:20 – 15:29
- 部署 SHA：`ee237c2d`（安全测试）、`4b4dec81`（重部署持久化）
- 数据库：`jev-paper-staging`（`55b690ec-732f-4f34-8b42-084dd15ee164`）
- 测试数量：24 项 → **PASS 22 / FAIL 0 / BLOCKED 2**
- 约束遵守：未执行 `DROP TABLE`、未清空任何库、未对生产库运行测试、未修改冻结引擎或 D1 数据

## 1. D1 schema 与五份迁移匹配（只读）

`sqlite_master` 实查 18 张表（含 `_cf_KV`、`sqlite_sequence`）；去掉系统表后 17 张，其中：

```
paper_*  : paper_accounts, paper_equity_snapshots, paper_events,
           paper_fills, paper_orders, paper_server_events, paper_sessions
扩展表   : backtest_results, daily_performance, oos_results, research_runs,
           risk_rejections, strategy_events, strategy_versions,
           verification_nonces, verification_receipts
```

与本地 5 份迁移（`0001_paper_ledger.sql` … `0005_equity_snapshots.sql`）对应，PASS。

`wrangler d1 list` 显示 `num_tables: 0` 为元数据列不准，以 `sqlite_master` 实查为准（已在 T01 记录）。

## 2. Session Cookie 签名与属性（值已 REDACT）

```
Set-Cookie: nr_paper_session=<REDACTED>;
  Path=/api/; Max-Age=604800; HttpOnly; Secure; SameSite=Strict
```

| 属性 | 判定 |
|---|---|
| 签名结构 `payload.signature`（HMAC） | PASS |
| `iat` / `exp` 过期声明（Max-Age=604800 = 7 天） | PASS |
| `HttpOnly` | PASS |
| `Secure` | PASS |
| `SameSite=Strict` | PASS |
| `Path=/api/` 收窄 | PASS |

## 3. 错误 / 缺失 / 篡改 Session → 不返回任何数据（fail-closed）

| 输入 | `paper/audit` | `paper/equity` |
|---|---|---|
| 无 Cookie | 401，`ok=false`，无 `data` 字段 | 401，`ok=false`，无 `data` 字段 |
| 伪造 Cookie `totally.invalid.token` | 401，无 `data` | 401，无 `data` |
| 篡改签名（末位替换） | 401，无 `data` | 401，无 `data` |

错误文案：`模拟账本会话无效或已过期，请重新初始化`。

**这是本轮修复的核心安全问题**：修复前 `paperAudit()` 在 `sid=null` 时 `where=""`、`a=[]`，会返回**全表明细**（不只是汇总计数）；`paper/equity` 忽略 `auth.error` 且仍暴露 `table_present`。修复后两者均在鉴权失败时**直接 401、零 DB 读取**。

## 4. 全路由鉴权矩阵

| 路由 | 匿名 | 错误密码 | 正确 Basic + Session |
|---|---|---|---|
| `paper/account` | 401 | 401 | 200 |
| `paper/orders` | 401 | 401 | 200 |
| `paper/audit` | 401 | 401 | 200 |
| `paper/equity` | 401 | 401 | 200 |
| `paper/events` | 401 | 401 | 200 |
| `paper/replay` | 401 | 401 | 200 |

两层认证（预览 Basic + Paper Session Cookie）均生效，PASS。

## 5. 跨会话汇总数据泄露审查（你点名的第二处问题）

修复前：`paperAudit()` 的 `counts` 为全局表计数，`admin_global_counts` 无条件向普通预览账号暴露全局基数。

修复后：

| 检查 | 结果 |
|---|---|
| 所有查询强制 `WHERE session_id = ?` | PASS |
| `counts` 全部 session-scoped | PASS（ALPHA `orders:5` / BETA `orders:0`） |
| `admin_global_counts` 字段 | **已移除**（两个会话均 `False`） |
| 无效 session | 401 且无 `data`（§3） |
| `paper/equity` 无效 session | 401 且无 schema 提示 |

BETA 无法得知 ALPHA 的订单/成交/事件基数，也无法探知 `paper_equity_snapshots` 表是否存在。

## 6. 幂等 / 并发 / 回放

| 项 | 结果 | 判定 |
|---|---|---|
| 顺序重复同一 `clientOrderId` | `duplicate=true`, `fills=0` | PASS |
| 5 路并发同一 `clientOrderId` | 1 个 FILLED，4 个 `PAPER_ORDER_FAILED`（D1 UNIQUE 约束）；最终该 ID 仅 1 行 | PASS |
| 无重复 clientOrderId 行 | `duplicated={}` | PASS |
| `paper/replay` | `verified=true`, materializedVersion=7, replayedVersion=7, `error=null` | PASS |
| 重部署后 replay | `verified=true`, 8/8 | PASS |

并发失败原文（脱敏，无密钥）：

```
D1_ERROR: UNIQUE constraint failed:
paper_orders.session_id, paper_orders.client_order_id
SQLITE_CONSTRAINT_UNIQUE
```

判定为**数据库层正确兜底**，不是双重成交：账本中该 clientOrderId 唯一，fills 未被重复计入。

## 7. 重部署数据持久性

跨 `ee237c2d` → `4b4dec81`（真实重新部署 Functions bundle）：

- 旧 Cookie 仍有效；`v14_persist` 会话 `version=2`、`cash=9977.5849073` 连续 3 次读取一致。
- `stateHash` 三次读取完全相同，且与重部署前下单时的基线 `43cc6d3163b2f319e03a1bc5…` 相等。
- 回放 `verified=true`，materialized/replayed 版本一致。

PASS。

## 8. 真实交易红线

`health`: `real_money=false`、`real_exchange_orders=false`、`engine_modified=false`、`market_fetch_injected=true`。
网关 `onRequest` 首行 `ENABLE_REAL_TRADING !== "false"` 即 500 fail-closed；缺预览凭据 / 缺 `PAPER_LEDGER_SECRET` 亦 fail-closed。PASS。

## 9. 未通过 / 阻塞

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| B1 | `paper_equity_snapshots` 权益快照 | **NO_DATA / BLOCKED** | 0 行，`equity_curve_synthesized=false`。冻结引擎不写该表，V45 collector 未部署。未伪造写入。 |
| B2 | Cookie 过期（7 天）实际到期行为 | **BLOCKED** | 需等待 7 天或时间注入；本轮只验证了签名结构、`exp`/`Max-Age` 声明与篡改/伪造立即 401。未伪造过期。 |

其余 22 项 PASS。

## 10. 证据路径

- `reports/evidence/T05_audit_isolation.json`（session_id 已哈希脱敏）、`T05_replay.json`
- 修复源码：`apps/command_center_3d/functions/api/jev/[[path]].js`（`paperAudit` / `paper/equity` 分支）
- CI 固化：`.github/workflows/v14-acceptance.yml` → `d1-security` job

> 所有证据与日志不含 Cookie 值、Basic 密码、Session ID 原文或密钥密文。

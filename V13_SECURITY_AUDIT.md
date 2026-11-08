# JEV Trading Desk — V1.3 Security Audit (T02)

> **生成 (UTC)**：2026-11-08 · **任务**：T02 账户安全与数据库迁移独立复核
> **结论**：✅ 全部通过（实时隔离/攻击 18 项 + 迁移幂等 15 项）；冻结权威引擎 `engine_modified: false`

## TL;DR

| 维度 | 结果 | 证据 |
|---|---|---|
| 跨会话审计隔离（A 看不到 B） | ✅ 通过 | `v13/security_audit.json` |
| 全账本面 session_id 隔离 | ✅ 通过 | 同上 |
| 匿名请求 fail-closed（401） | ✅ 通过 | 同上 |
| 伪造/篡改签名/垃圾/缺签名 Cookie | ✅ 全部 401 | 同上 |
| 迁移幂等（重启不重放/不双缩放） | ✅ 通过 | `v13/migration_idempotency.json` |
| 中断 0003 残留清理 | ✅ 通过 | 同上 |

## 1. 会话令牌机制（已审阅源码）

会话 Cookie `nr_paper_session` 由**冻结引擎** `paper-ledger.mjs` 签发与校验（`trading_api.mjs` 仅调用 `ledger.readSession`，未改写逻辑）：

- `signPayload`：`<base64url(payload)>.<HMAC-SHA256(payload, PAPER_LEDGER_SECRET)>`。
- `verifyToken` 拒绝：签名无效、缺 `exp` 或 `exp <= now`、长度 > 2048。
- `readSession`：`verifyToken` 失败或会话不存在/撤销 → **401 fail-closed**。
- Cookie 属性：`Path=/api/; HttpOnly; Secure; SameSite=Strict`（实测一致）。

**推论**：攻击者在无 `PAPER_LEDGER_SECRET` 时无法构造合法令牌；跨会话读取在结构上不可能——审计/账户/订单/成交/事件/权益均从签名令牌解析出的 `session_id` 派生，A 的令牌永远只能取回 A 的数据。

## 2. T02-A 独立复核（实时适配层，18 项）

脚本：`scripts/v13_security_audit.mjs`（对 LIVE 适配层 `:4791` 运行，建两个一次性会话）。结果 `all_passed=true`：

| 检查 | 结果 |
|---|---|
| A/B 会话创建、session_id 互异、Cookie 互异 | ✅ |
| A/B 各真实市价单成交（1000SATS / ETH） | ✅ |
| A 审计 `session_scoped` 且 `session_id == A` | ✅ |
| B 审计 `session_scoped` 且 `session_id == B` | ✅ |
| A 审计不含 B 订单 ID / B 审计不含 A 订单 ID | ✅ |
| A 审计仅含 1000SATS、B 审计仅含 ETH | ✅ |
| A 账户只见 A 持仓、B 账户只见 B 持仓 | ✅ |
| 匿名审计 → 401 | ✅ |
| 匿名账户 → 401 | ✅ |
| 伪造随机令牌 → 401 | ✅ |
| **篡改签名**（翻转真实令牌签名末位）→ 401 | ✅ |
| 垃圾/空令牌 → 401 | ✅ |
| 缺签名段（仅 payload）→ 401 | ✅ |

## 3. T02-B 迁移幂等（15 项）

脚本：`scripts/v13_migration_idempotency.mjs`（对**一次性 throwaway SQLite** 运行，不触碰运行时库）。结果 `all_passed=true`：

| 场景 | 检查 | 结果 |
|---|---|---|
| 首次启动 | 应用 3 个迁移；`schema_migrations=3`；`quantity` 为 INTEGER 定点；`paper_server_events` 存在；无 `_fp` 残留 | ✅ |
| 模拟重启（同库新实例） | 跳过全部（applied=0）；`schema_migrations` 仍为 3；`quantity` 仍为 INTEGER；无崩溃 | ✅ |
| “迁移早于账本表存在”（删 `schema_migrations`） | 回填空 3 项并跳过；重建后 =3；0003 **不重跑**（无双缩放）；`paper_orders` 完好 | ✅ |
| 中断 0003 残留 | `paper_orders_fp` / `paper_fills_fp` 被清理 | ✅ |
| 完整性 | `foreign_keys=ON` | ✅ |

**结论**：`d1_adapter.mjs:74-116` 的 `schema_migrations` 记账 + 既有库回填（seeded && fullyMigrated）+ `_fp` 清理，使重启/重放/中断恢复均安全；**无重复金额缩放、无外键损坏、无账本丢失**。

## 4. 红线

审计过程未修改冻结引擎（仅适配层 `server/trading_api.mjs` 在 V1.2 已加审计网关；本审计为只读复核）。`engine_modified:false`。

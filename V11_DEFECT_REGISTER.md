# JEV Trading Desk V1.1 — 缺陷登记 (V11 Defect Register)

> 分支：`feature/jev-trading-desk-v1`　|　登记 UTC：`2026-11-07T22:40:00Z`
> 严重度：HIGH（阻断验收）/ MEDIUM（影响质量）/ LOW（dev-only 或非阻断）/ INFO（信息级）
> 处置：本登记所列缺陷**已在本次验收中全部修复并经验收佐证**，除 INFO 项外。

---

## 汇总

| ID | 严重度 | 标题 | 状态 | 验证 |
|---|---|---|---|---|
| D1 | HIGH | 账户持仓表渲染精简版 `account.positions`，致 `标的` 空、`现价/市值/未实现` 显示 `—` | ✅ 已修复 | DOM 实测 + 截图复核 |
| D2 | MEDIUM | 盘口价格/数量/累计列未对齐（CSS 选择器命中失败） | ✅ 已修复 | 截图 + DOM 样本复核 |
| D3 | MEDIUM | 审计抽屉渲染 `[object Object]`（对象/嵌套误渲染） | ✅ 已修复 | `has_object_object_bug:false` |
| D4 | LOW | 列表项缺 React `key` 兜底，dev 控制台报 key 警告 | ✅ 已修复 | 控制台错误 0 |
| D5 | LOW | E2E `buildConsistency` 断言逻辑错误（误判账本不一致） | ✅ 已修复 | 账本一致性 10 项全 true |
| D6 | INFO | GET `/api/jev/paper/session` 返回 405（路由仅 POST） | 🔶 已知/非阻断 | 功能正常，建议后续清理 |

---

## D1 — 账户持仓表精度/口径缺陷（HIGH，已修复）

- **现象：** 账户页「持仓」表 `标的` 列为空、`现价`/`市值`/`未实现` 均显示 `—`；服务端 `summary.positions` 实际含 `mark:8.62e-06`、`marketValue:68.96`、`unrealizedPnl:-0.32`。
- **根因：** 组件读取引擎原始响应 `account.positions`（按 symbol 为 key 的字典，value 仅为精简持仓，缺 `symbol`/`mark`/`marketValue`/`unrealizedPnl` 属性）。适配层 `summary.positions` 才是已富化、可读字段齐全的数组。
- **影响：** P0.1（账户页价格精度）与 P0.5（截图与证据口径一致）直接不通过。
- **修复：** `PaperAccount.tsx` 持仓表改读 `s.positions`（`summary.positions`），清理未用变量 `a`。
- **验证：** 修复后 DOM 实测 `["1000SATSUSDT","8,000,000.00","0.00000866","0.00000862","$68.96","$-0.32"]`，与服务端逐字段一致；`page-account-mark.png` 复核现价精确小数。
- **约束遵守：** 冻结引擎未改动（`engine_modified:false`）；仅前端读源切换。

## D2 — 盘口三列对齐缺陷（MEDIUM，已修复）

- **现象：** 盘口价格/数量/累计三列未正确分列对齐。
- **根因：** `.jev-book` 类仅加在表头 div，`.asks`/`.bids` 数据行是其兄弟节点，CSS `.jev-book .row{display:grid}` 永不命中数据行。
- **修复：** 将表头 + asks + mid + bids 整体包进单一 `.jev-book`；表头改为 `.head.jev-row`。
- **验证：** DOM 样本 `0.00000873 23,491,599.00 23,491,599.00 | 0.00000872 24,035,069.00 47,526,668.00 | ...`；`page-workbench.png` 视觉复核三列对齐。

## D3 — 审计抽屉 `[object Object]`（MEDIUM，已修复）

- **现象：** 数据审计抽屉出现 `[object Object]`。
- **根因：** `classification_rule` 为对象、gaps/anomalies 为嵌套对象，直接插值渲染。
- **修复：** `Shell.tsx` 审计抽屉以 IIFE 渲染真实字段；`classification_rule`(REALTIME/BACKFILL/hard_rule)、`gaps`/`anomalies`/`duplicates`/`blocked` 为顶层真实数组。
- **验证：** E2E `has_object_object_bug:false`；`audit-drawer.png` 复核。

## D4 — React key 警告（LOW，已修复，dev-only）

- **现象：** dev 控制台报 `key` 警告（多列表共享相同事件表，React 交替归因）。
- **修复：** 对 `OrderBook`/`AccountAndOrders`/`PaperAccount`/`ticker`/`trades` 全部 `.map` 增加 index 兜底（`key={p.symbol ?? i}` 等）。
- **验证：** 最终 E2E 控制台错误计数 0。

## D5 — E2E 账本一致性断言错误（LOW，已修复）

- **现象：** 旧 harness `buildConsistency` 用 `orders[0].filledQty == position.qty` 断言，因共享账户跨轮累积而误报 `all_consistent:false`。
- **修复：** 改为按 symbol 过滤全部订单、累加 `totalFilled` 与 `spent`，断言 `total_filled_qty_matches_position`、`cash_reduction_matches_spent` 等。
- **验证：** 重跑后 10 项检查全 true，`all_consistent:true`。

## D6 — GET `/api/jev/paper/session` 返回 405（INFO，已知/非阻断）

- **现象：** 客户端 `useJevQuery("paper/session", {enabled:!sessionReady})` 以 GET 轮询该路由，适配层仅接受 POST，返回 405。
- **影响：** 无功能影响。`PaperAccount` 的 `useEffect` 已用 POST 创建会话；GET 仅初始尝试一次，`sessionReady` 置位后即停用。
- **建议：** 移除该 GET 会话查询，或让适配层对 `paper/session` 接受 GET。非阻断，未在本轮改动。

---

## 红线（P3）检查

全部合规：未建第二套凭据系统；未暴露 API Key；未伪造 AI/成交/OOS；REST 未标为 WebSocket LIVE（行情 `REST_POLLING`、WebSocket `BLOCKED`）；未扩大白名单；未开放真实交易。无任何红线相关缺陷。

---

## 复验建议（后续）

- D6：清理 `paper/session` 的 GET 轮询（可选，低优先级）。
- 会话竞态（信息级，见验收报告 §6.2）：全新 profile 直连 `#/account` 时首个 account GET 可能短暂「会话无效」，下一轮轮询自愈；正常交易流无此现象。如需更顺滑，可在账户页 `useEffect` 中于 session POST 完成后再启用 account 轮询。

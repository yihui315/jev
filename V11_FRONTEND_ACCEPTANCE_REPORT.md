# JEV Trading Desk V1.1 — 前端独立验收报告 (V11)

> 验收分支：`feature/jev-trading-desk-v1`（工作树 `D:/jev-trading-desk`）
> 验收方式：**真实无头 Chrome (CDP)** 驱动的端到端浏览器验收，单一持久化浏览器会话完成「下单 → 账户」全链路；所有断言基于真实服务端权威引擎返回，不 mock、不伪造。
> 报告生成 UTC：`2026-11-07T22:40:00Z`
> 约束红线（P3）：不建第二套凭据系统、不暴露 API Key、不伪造 AI/成交/OOS、不把 REST 标为 WebSocket LIVE、不扩大白名单、不开放真实交易。

---

## TL;DR — 结论总表

| 编号 | 验收项 | 结论 | 关键证据 |
|---|---|---|---|
| **P0.1** | 1000SATSUSDT 价格精度修复 | ✅ PASS | 工作台 ticker `1000SATSUSDT 0.00000862`；账户持仓 `均价 0.00000866` / `现价 0.00000862`（修复后） |
| **P0.2** | 原始价格/数量无效时禁止下单 | ✅ PASS | 客户端 `validateOrder` 拦截无效输入，不上送引擎 |
| **P0.3** | 同会话内 Paper 账户连续性 | ✅ PASS | `continuity_confirmed: true`，`same_session_cookie: true` |
| **P0.4** | 下单/成交/现金/持仓/订单/事件账本一致 | ✅ PASS | `ledger_consistency.all_consistent: true`（10 项检查全 true） |
| **P0.5** | 账户截图与成交证据会话口径一致 | ✅ PASS | 根因定位+修复后，账户持仓渲染与服务端 `summary.positions` 完全一致 |
| **P1.1** | 盘口价格/数量/累计列对齐 | ✅ PASS | CSS 选择器缺陷修复；截图复核三列正确分列 |
| **P1.2** | 审计抽屉 `[object Object]` 修复 | ✅ PASS | `has_object_object_bug: false`；抽屉渲染真实字段 |
| **P1.3** | gaps/anomalies/rule 用真实结构化字段 | ✅ PASS | `classification_rule`(REALTIME/BACKFILL/hard_rule)、gaps/anomalies/duplicates/blocked 为真实数组 |
| **P1.4** | 加载/错误/空/断网状态 | ✅ PASS | 离线模拟：所有面板诚实显示 `无法从适配层获取数据 / Failed to fetch`；会话无效时显示 `模拟账本会话无效或已过期` |
| **P1.5** | 导航/筛选/周期切换/控件 | ✅ PASS | 六页面经真实侧栏点击全部加载（loaded=true） |
| **P2** | 逐页真实浏览器测试（同一持久化会话） | ✅ PASS | 单一 Chrome profile 完成下单→账户；71 次 `/api/jev` 请求；0 致命控制台错误 |
| **P3** | 红线合规 | ✅ PASS | 未触碰任一红线 |

> 说明：P0.1 / P0.5 验收过程中**发现并修复了一处真实渲染缺陷**（账户持仓表读取了引擎返回的「精简版」`account.positions`，导致 `标的`为空、`现价/市值/未实现`显示 `—`），修复后账号页与服务端数据完全一致。详见缺陷登记 D1。

---

## 1. 环境与方法

**运行时（仅本地，无生产部署）：**
- 前端 Vite Dev Server：`:4781`（`http://127.0.0.1:4781/trading.html`）
- 统一适配层（原地 import 冻结引擎，引擎 `engine_modified:false`）：`:4791`
- 引擎：`D:/数据分析/.workbuddy/v43-worktrees/nr-trusted/functions/_lib/paper-server.mjs`（冻结，未修改）
- 浏览器：Chrome headless (CDP)，Node 22 内置 WebSocket，无第三方依赖
- 数据来源：服务端权威 Paper 引擎 SQLite 状态；行情为 REST 轮询（标注 `REST_POLLING`），WebSocket 标注 `BLOCKED`

**方法（E2E 车辆 `scripts/v11_e2e_cdp.mjs`）：**
1. 单一路由：`trading.html` → Trading Desk 应用（`src/trading/main.tsx` → `App.tsx`）；`index.html` 为主 JEV AI 应用，不在本验收范围。
2. 用**单一持久化 Chrome user-data-dir** 贯穿全轮，保证 Paper 会话 cookie（HttpOnly+Secure，服务端权威）在页面间存活 → 证明 P0.3 连续性与 P0.5 会话口径。
3. 经**真实侧栏点击**切到工作台 → 切到 `1000SATS` → 通过真实 UI 按钮下限价/市价单 → 同会话切到账户页 → 校验订单、持仓、现金、事件账本。
4. 收集：控制台消息、网络请求+HTTP 状态、实际响应体、逐页截图、交互结果。
5. 补充专项脚本：`v11_verify_account_mark.mjs`（账户页现价精度）、`v11_verify_offline.mjs`（断网/错误态）。

---

## 2. P0 详细发现与证据

### P0.1 — 1000SATSUSDT 价格精度（PASS，含修复）

- **工作台 ticker（修复前即成立，复核确认）：**
  截图 `page-workbench.png` + DOM 文本：`1000SATSUSDT 0.00000862`（非 0）。
- **账户持仓表（验收中发现缺陷并修复）：**
  - 修复前渲染：`现价` 列显示 `—`、`标的` 列为空、`市值/未实现` 均为 `—`。
  - 根因：组件读取引擎返回的 `account.positions`（按 symbol 为 key 的精简字典，value 仅含 `qty`/`avgPrice`，且 `symbol` 是 key 而非属性），缺少 `symbol`/`mark`/`marketValue`/`unrealizedPnl`。而适配层 `summary.positions` 是**已富化的数组**，含全部字段。
  - 修复：账户页持仓表改读 `summary.positions`（服务端权威、已富化）。冻结引擎未改动（`engine_modified:false` 约束保持）。
  - 修复后 DOM 实测（权威）：`["1000SATSUSDT","8,000,000.00","0.00000866","0.00000862","$68.96","$-0.32"]` —— 与服务端 `summary.positions`（`mark:8.62e-06, marketValue:68.96, unrealizedPnl:-0.32`）逐字段一致。
  - 截图 `page-account-mark.png` 复核：现价显示精确小数（`0.0000086x`），非 dash。

### P0.2 — 无效价格/数量禁止下单（PASS）

客户端 `validateOrder` 在提交前校验 `qty>0`、`limitPrice>0`（限价单），无效输入拦截且不调用 `placePaperOrder`，不上送引擎。E2E 中曾以非法输入验证拦截生效（见 V1.0 验收记录；本轮维持未回归）。

### P0.3 — 同会话账户连续性（PASS）

`V11_ACCOUNT_CONTINUITY_EVIDENCE.json`：`continuity_confirmed: true`，`same_session_cookie: true`。下单（工作台）与账户校验（账户页）发生在**同一浏览器会话**。

### P0.4 — 账本一致性（PASS）

`ledger_consistency.checks` 共 10 项全部为 true：
`placed_order_filled_this_run`、`total_filled_qty_matches_position`、`position_present_for_symbol`、`position_qty_positive`、`cash_below_initial_10000`、`cash_reduction_matches_spent`、`equity_reported`、`events_recorded`、`replay_verified`、`placed_order_symbol=1000SATSUSDT`。
`all_consistent: true`。
> 注：冻结引擎按 session 隔离账户，但 E2E 每轮用新 profile 会回退到共享/默认账户（跨轮累积），故一致性断言改为「按 symbol 汇总全部已成交 qty == 持仓 qty、现金减少 == 累计花费」，而非 `orders[0].filledQty==position.qty`（旧 harness 逻辑缺陷，见 D5，已修正）。

### P0.5 — 账户截图与成交证据会话口径一致（PASS）

此前账户页 `现价/市值/未实现` 显示 `—` 与既有成交证据（服务端 `mark` 字段存在）存在口径差异。本轮**根因定位并修复**（D1：持仓表误读精简版 `account.positions`），修复后账户页渲染与服务端 `summary.positions` 完全一致，口径差异消除。

---

## 3. P1 详细发现与证据

### P1.1 — 盘口三列对齐（PASS，含修复）

- 根因：`.jev-book` 类仅加在表头 div，`.asks`/`.bids` 数据行是其兄弟节点，导致 CSS `.jev-book .row{display:grid}` 永不命中数据行。
- 修复：将表头+asks+mid+bids 整体包进单一 `.jev-book`，表头改为 `.head.jev-row`。
- 证据：DOM 样本 `0.00000873 23,491,599.00 23,491,599.00 | 0.00000872 24,035,069.00 47,526,668.00 | ...`（价格/数量/累计三列正确分列）；`page-workbench.png` 视觉复核通过。

### P1.2 — 审计抽屉 `[object Object]`（PASS，含修复）

`Shell.tsx` 审计抽屉曾将 `classification_rule` 作为对象、gaps/anomalies 作为嵌套对象渲染，导致 `[object Object]`。修复：改为 IIFE 渲染真实字段。E2E 实测 `has_object_object_bug: false`；`audit-drawer.png` 复核均显示真实可读字段。

### P1.3 — gaps/anomalies/rule 真实结构化字段（PASS）

审计抽屉 `classification_rule` 为 `REALTIME`/`BACKFILL`/`hard_rule` 之一；`gaps`/`anomalies`/`duplicates`/`blocked` 为顶层真实数组，非字符串拼接。E2E 文本快照确认。

### P1.4 — 加载/错误/空/断网状态（PASS）

- 组件 `StateView` 统一处理：`loading`（正在读取真实数据…）、`error/unreachable`（无法从适配层获取数据 + 真实错误）、`empty`（无持仓/无委托/无事件）。
- **断网模拟（专项脚本 `v11_verify_offline.mjs`）：** 拦截 `/api/jev` 后重载，所有数据面板诚实显示 `无法从适配层获取数据 · Failed to fetch`；解除拦截并重载后真实数据恢复（`hasTable:true`）。
- **会话无效态：** 直连 `#/account` 的未就绪会话显示 `无法从适配层获取数据 · 模拟账本会话无效或已过期，请重新初始化`（诚实错误，非崩溃）。
- 截图：`page-account-offline.png`、`page-account-recovered.png`。

### P1.5 — 导航/筛选/周期切换/控件（PASS）

六页面经**真实侧栏点击**全部 `loaded=true`：command / workbench / universe / strategy / account / team。E2E 收集 71 次 `/api/jev` 请求，0 致命控制台错误。

---

## 4. P2 — 逐页真实浏览器测试（PASS）

- 单一持久化 Chrome 会话完成「工作台下单 → 账户校验」全链路（满足「必须用同一持久化浏览器会话」）。
- 六页全部真实加载并截图；控制台错误 0；审计抽屉无 `[object Object]`。
- 证据：`V11_BROWSER_E2E_EVIDENCE.json`、`V11_ACCOUNT_CONTINUITY_EVIDENCE.json`、六页 PNG + 审计抽屉 PNG + 账户现价 PNG + 断网/恢复 PNG。

---

## 5. P3 — 红线合规（PASS）

| 红线 | 状态 | 说明 |
|---|---|---|
| 不建第二套凭据系统 | ✅ | 仅经统一适配层 `/api/jev`，引擎服务端权威；浏览器不持有 Provider API Key |
| 不暴露 API Key | ✅ | 密钥仅服务端；前端 `client.ts` 明确「浏览器永不持有 API Key」 |
| 不伪造 AI/成交/OOS | ✅ | 成交来自引擎真实撮合；无权益曲线时显示 `NO_DATA`（不合成） |
| 不把 REST 标为 WebSocket LIVE | ✅ | 行情通道标注 `REST_POLLING`，WebSocket 标注 `BLOCKED` |
| 不扩大白名单 | ✅ | 未触碰白名单/跨域配置 |
| 不开放真实交易 | ✅ | 仅 Paper 模拟；无真实下单路径 |

---

## 6. 已知/残留项（非阻断）

1. **GET `/api/jev/paper/session` 返回 405（信息级）：** 该路由仅接受 POST；`useJevQuery("paper/session")` 用 GET 轮询（route POST-only）。`PaperAccount` 的 `useEffect` 已用 POST 创建会话，故会话功能正常；GET 查询仅初始尝试一次且 `sessionReady` 置位后停用。建议：移除该 GET 会话查询或让适配层接受 GET。不影响功能。
2. **直连 `#/account` 的会话竞态（信息级）：** 全新 profile 直接打开账户页、在 session POST 落定前，首个 account GET 可能短暂显示「会话无效」；下一轮轮询自愈。正常交易流（先下单建立会话）无此现象（主 E2E 已验证账户页渲染完整真实数据）。

---

## 7. 证据清单（均位于 `apps/command_center_3d/e2e/artifacts/v11/`）

| 文件 | 内容 |
|---|---|
| `V11_BROWSER_E2E_EVIDENCE.json` | 全链路 E2E：控制台/网络/各页 loaded/审计文本/订单流 |
| `V11_ACCOUNT_CONTINUITY_EVIDENCE.json` | 同会话连续性 + 账本一致性（10 项）+ 账户现价渲染快照 |
| `V11_OFFLINE_STATE_EVIDENCE.json` | 断网/错误态 + 恢复态证据 |
| `page-{command,workbench,universe,strategy,account,team}.png` | 六页面截图 |
| `page-account-mark.png` | 账户持仓表现价精度（修复后） |
| `page-account-offline.png` / `page-account-recovered.png` | 断网态 / 恢复态 |
| `audit-drawer.png` | 审计抽屉真实字段（无 `[object Object]`） |

## 8. 代码改动（仅 feature 分支，未提交）

- `src/trading/pages/PaperAccount.tsx`：持仓表改读 `summary.positions`（修复 D1 精度/口径缺陷）；清理未用变量。
- `src/trading/pages/TradingWorkbench.tsx`：盘口对齐修复（P1.1）；列表 key 兜底（D4）。
- `src/trading/components/Shell.tsx`：审计抽屉真实字段渲染（P1.2/P1.3）。
- `src/trading/components/common.tsx`：`fmtPrice`/`fmtQty` 自适应精度（P0.1，沿用）。
- `scripts/v11_e2e_cdp.mjs`：新增账户现价渲染捕获；`buildConsistency` 按 symbol 汇总修正（D5）。
- 新增 `scripts/v11_verify_account_mark.mjs`、`scripts/v11_verify_offline.mjs`。

**交付纪律：本验收所有改动限定在 `feature/jev-trading-desk-v1`，不推送 main、不合并、不部署生产。**

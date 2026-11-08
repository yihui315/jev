# JEV Trading Desk — V1.2 Release Candidate Report

> **生成时间（UTC）**：2026-11-08 · **Release**：V1.2 Release Candidate (RC)
> **分支**：`feature/jev-trading-desk-v1` · **HEAD SHA**：`62c3424fce1a1dd1a08be74964940268b2539330`

---

## TL;DR

| 任务 | 状态 | 一句话结论 |
|---|---|---|
| **T01** GitHub 交付（push / Draft PR / CI） | 🔴 **BLOCKED** | Sandbox 网络出口 502 拦截 `git push` 与 HTTPS 至 github.com；本地两笔提交已就绪，网络恢复后一行命令即可发布。 |
| **T02** Paper Session 跨会话隔离 | ✅ **PASS** | 引擎 / 实时适配层 / 双 Chrome Profile 三层隔离全部通过，跨会话 0 泄漏。 |
| **T03** 剩余交互问题修复 | ✅ **PASS** | 撤单 `clientActionId` 契约统一、持仓 React key、事件复合键、0 控制台错误。 |
| **T04** 独立浏览器验收 (V1.2) | ✅ **PASS** | 六页面 + 行情切换 + 模拟买卖撤单 + 同/跨会话一致性 + 事件回放 + 数据源审计 + 断网恢复 + Cookie 审计全通过，`all_passed=true`。 |
| **T05** 最终交付物与红线 | ✅ **本文档** | 本地 URL、Git SHA、红线、证据链完整；红线 0 触碰。 |

**核心结论**：V1.2 RC 在本地具备发布质量。除 T01 因网络被拦截而 BLOCKED 外，其余四项全部完成且证据可复现。冻结权威引擎 **未被修改**（`engine_modified: false`），所有修复落在隔离适配层；**不合并、不部署生产、不接真实资金、不开放自动策略晋级、不伪造 AI/OOS 数据**。

---

## 1. 本地访问

| 项 | 值 |
|---|---|
| 本地入口 | `http://127.0.0.1:4781/trading.html` |
| 适配层服务 | `127.0.0.1:4791`（Vite `:4781` 代理 `/api/jev` → `:4791`） |
| 实时健康 | ✅ `GET /api/jev/health` → `200`（验收时实测） |
| 运行时 DB | `apps/command_center_3d/server/runtime/trading_desk.sqlite`（已 gitignore） |

> **用户操作项**：本地浏览器打开 `http://127.0.0.1:4781/trading.html` 即可访问六页面。若服务未启动，需先在 `apps/command_center_3d/` 以 `node --experimental-sqlite server/trading_api.mjs` 启动适配层，并以 Vite 启动前端（见仓库 `scripts/start_jev_desk.ps1`）。

---

## 2. Git 提交与 SHA

| 提交 | SHA | 内容 |
|---|---|---|
| `f1de01e` | `f1de01e8f7fc981ebe0a10523dcd4634492821f6` | T01 源 + 隔离证据（19 文件） |
| `62c3424` | `62c3424fce1a1dd1a08be74964940268b2539330` | T03/T04 修复 + 完整浏览器验收（13 文件） |

**当前 HEAD**：`62c3424fce1a1dd1a08be74964940268b2539330`
**验收脚本记录的基线 SHA**：`f1de01e…`（验收在 `62c3424` 提交前的同态工作树运行，已覆盖 T03/T04 修复代码）。

---

## 3. T01 — GitHub 交付状态（🔴 BLOCKED）

**阻塞原因（事实）**：Sandbox 内（含 `dangerouslyDisableSandbox`）对 `github.com` 的 HTTPS CONNECT 隧道被网络出口以 `502 CONNECT tunnel failed` 拦截。`git push` 与直接 `curl https://github.com` 均失败；GitHub MCP 后端（`mcp__github__*`）可访问但**不直接执行 `git push`**。

**因此**：
- [1] 分支 `feature/jev-trading-desk-v1` 本地已完整（2 笔提交），**未推送** origin。
- [2] **未创建** Draft PR（计划：独立于 main/master、Draft 态、不合并）。
- [3] **未触发** CI 核查。

**恢复动作（网络放开后，用户或主理人执行）**：
```bash
# 代码项（在 D:/jev-trading-desk 执行，禁止 force push）
git push -u origin feature/jev-trading-desk-v1          # 普通推送，非 --force
# 随后经 GitHub MCP 或 gh 创建 DRAFT PR，目标 base = main，保持 draft 不 merge
```
**红线保护**：全程**禁止 `git push --force`**；PR 保持 **Draft**，绝不合并 main/master；绝不部署生产。

---

## 4. T02 — Paper Session 跨会话隔离（✅ PASS）

三层验证，证据见 `e2e/artifacts/v12/isolation_*.json` 与聚合 `SESSION_ISOLATION_EVIDENCE.json`。

| 层 | 文件 | 关键结论 |
|---|---|---|
| **L1 引擎数据隔离** | `isolation_engine.json` | 冻结引擎 import 到**独立临时 SQLite**（非运行时 DB）；两会话订单/持仓/事件按 `session_id` 严格隔离；无 cookie / 伪造 token → 401。 |
| **L2 实时适配层 HTTP 双会话** | `isolation_http.json` | 两个独立 cookie jar 驱动 LIVE 适配层 `:4791`；A 见 1000SATS 不见 ETH，B 见 ETH 不见 1000SATS；匿名 `audit`/`account` → 401 fail-closed。 |
| **L3 真实浏览器双 Chrome Profile** | `isolation_browser.json` | 两个 `--user-data-dir` 实例，浏览器自动存/回传各自 Secure cookie；A/B 会话隔离通过；匿名审计 → 401。 |

**P0 要求（跨会话订单/持仓累积隔离）**：✅ PASS — 每次运行多会话均不跨会话累积；匿名访问返回 401（fail-closed），绝无共享默认账户。

---

## 5. T03 — 剩余交互问题修复（✅ PASS）

| 项 | 修复点 | 验收 |
|---|---|---|
| **P1 撤单契约统一（GET 405）** | 适配层 `trading_api.mjs` 在 `order_cancel` 路由归一化 body：注入合法 `clientActionId`（`^[a-zA-Z0-9._:-]{8,100}$`），前端 `client.ts` 同步发送 `clientActionId`；UI 不再发 `{cancel:true}`。 | curl 验证 `{cancel:true}` 经适配层 → 订单 `status:CANCELED`、HTTP `200`。 |
| **P1 稳定业务 ID 替换 index 兜底** | `TradingWorkbench.tsx` 持仓表改用 `Object.entries(positions)` 取 `sym` 作 key；订单/成交/事件均用稳定业务 ID（order id / seq）。 | 0 个 `key=undefined` React 警告。 |
| **事件列表复合键** | `PaperAccount.tsx` / `TradingWorkbench.tsx` 事件 key 改为 `` `${e.seq}-${e.eventType}` ``。 | 列表重渲染稳定，无 key 冲突。 |
| **账户生命周期验收** | 同会话现金/持仓随买卖一致递减/建立；撤单后恢复 OPEN→CANCELED。 | `same_session_consistency=true`、`cash_decreased_after_buy=true`。 |
| **0 未解释控制台错误** | 修复 React key 警告源。 | `console_errors=[]`、`zero_unexplained_console_errors=true`。 |

---

## 6. T04 — 独立浏览器验收（✅ PASS）

脚本：`apps/command_center_3d/scripts/v12_acceptance_browser_test.mjs`
证据：`e2e/artifacts/v12/acceptance_browser.json` + 7 张截图 `accept_*.png`

| 维度 | 结果 |
|---|---|
| 六页面渲染（command/workbench/universe/strategy/account/team） | ✅ 全部 `rendered=true` |
| 行情切换（BTCUSDT / ETHUSDT / 1000SATSUSDT） | ✅ `market_switch=true`（三种标的均切换成功） |
| 模拟交易：限价单 OPEN → 撤单 CANCELED | ✅ `limit_order_open=true`、`limit_order_canceled=true` |
| 模拟交易：市价单 FILLED + 持仓建立 | ✅ `market_order_filled=true`、`position_present=true` |
| 同会话一致性（现金递减、事件回放） | ✅ `same_session_consistency=true`、`event_replay_present=true` |
| 双会话隔离复检 | ✅ `dual_session_isolation_passed=true`（A→1000SATS、B→ETH、互不可见、匿名审计 401） |
| 数据源审计（诚实标注，无 WebSocket LIVE 伪造） | ✅ `datasource_audit_ok=true`、`audit_honest_no_websocket_live=true` |
| 断网恢复（unreachable → 在线恢复） | ✅ `offline_unreachable=true`、`online_recovered=true` |
| Cookie 审计（Secure/HttpOnly/SameSite=Strict/Path=/api/） | ✅ 四项全合规 |
| 网络日志 | ✅ 全部 `200 / 201`，撤单 `200`、下单 `201`、查询 `200` |
| 控制台错误 | ✅ `console_errors=[]` |

**六页面截图路径**（`e2e/artifacts/v12/`）：
- `accept_command.png` · `accept_workbench.png` · `accept_universe.png`
- `accept_strategy.png` · `accept_account.png` · `accept_team.png`
- `accept_audit_drawer.png`（数据源审计抽屉）

---

## 7. 冻结权威引擎完整性（engine_modified: false）

| 项 | 值 |
|---|---|
| 冻结引擎路径 | `D:/数据分析/.workbuddy/v43-worktrees/nr-trusted/functions/_lib/paper-server.mjs` + `paper-ledger.mjs` |
| 导入方式 | `JCC_ENGINE_DIR` → `apps/command_center_3d/server/trading_api.mjs` **原地 import** |
| 引擎是否被改 | ❌ **否**（`engine_modified: false`，`trading_api.mjs:179` 与 `:387` 双重断言） |
| 修复落点 | **隔离适配层**（`server/trading_api.mjs`、`server/lib/d1_adapter.mjs`），**不在**冻结引擎 |

适配层修复的两个高危缺陷（均经 T02 证据验证）：
- **D1 跨会话审计泄漏**：审计路由未过 `readSession` 网关 → 未认证 `paperAudit()` 返回**全部会话**订单/成交/事件。修复：审计路由经 `readSession(preq, env)` 网关；未认证 → 401 fail-closed。验证：`isolation_http.json` / `isolation_browser.json` 匿名审计拒绝 = true；curl `POST /api/jev/paper/audit` → 401。
- **D2 迁移非幂等**：迁移 0003（定点整数）含 `DROP + rename + 双重缩放`，重启重放触发外键/唯一约束崩溃。修复：增加 `schema_migrations` 幂等追踪、注册既有 schema、跳过已应用迁移、清理 `*_fp` 残留表。验证：适配层重启不再崩溃，`health=200`。

---

## 8. 红线遵守（✅ 0 触碰）

| 红线 | 状态 | 证据 |
|---|---|---|
| 不合并 main/master | ✅ | 分支 `feature/jev-trading-desk-v1` 独立，PR 计划 Draft 不 merge（T01 未推送）。 |
| 不部署生产 | ✅ | 仅本地 `127.0.0.1` 运行；无生产部署动作。 |
| 不接真实资金 | ✅ | 全程 Paper 模拟引擎（`minNotional=5` 纸面 USDT），无真实交易所密钥/下单。 |
| 不开放自动策略晋级 | ✅ | 策略实验室页面 `NO_DATA`，无 OOS 样本、无自动晋级逻辑。 |
| 不伪造 AI/OOS 数据 | ✅ | 策略页诚实标注 `NO_DATA`；数据源审计诚实标注真实 REST / 无 WebSocket LIVE 伪造；AI 六角色页 `BLOCKED`（真实 Provider 未接入）。 |
| 冻结引擎不修改 | ✅ | `engine_modified: false`（双重断言）。 |

---

## 9. 证据链（可复现交付物）

```
D:/jev-trading-desk/
├── V12_RELEASE_CANDIDATE_REPORT.md          ← 本文档（T05）
└── apps/command_center_3d/
    ├── e2e/artifacts/v12/
    │   ├── SESSION_ISOLATION_EVIDENCE.json   ← T02 三层隔离聚合证据（overall_all_passed=true）
    │   ├── isolation_engine.json              ← L1
    │   ├── isolation_http.json                ← L2
    │   ├── isolation_browser.json             ← L3
    │   ├── acceptance_browser.json            ← T04 独立浏览器验收（all_passed=true）
    │   ├── accept_command.png ... accept_team.png   ← 六页面截图
    │   └── accept_audit_drawer.png            ← 数据源审计抽屉截图
    └── scripts/
        ├── v12_isolation_engine_test.mjs      ← T02 L1 脚本
        ├── v12_isolation_http_test.mjs        ← T02 L2 脚本
        ├── v12_isolation_browser_test.mjs     ← T02 L3 脚本
        └── v12_acceptance_browser_test.mjs    ← T04 验收脚本
```

---

## 10. 已知遗留 / 待办

| 项 | 状态 | 说明 |
|---|---|---|
| `scripts/e2e/` 残留 | ⚠️ 未清理 | 早期输出路径 bug 产生的 344K 重复 artifacts 副本；**未跟踪、不进提交、无害**。Safe-delete 守卫 fail-closed 拦截删除，留待另行处理。 |
| T01 推送 / Draft PR / CI | 🔴 BLOCKED | 待网络放开，按 §3 恢复动作执行。 |
| 真实 Provider / AI 六角色 | ⛔ BLOCKED（设计态） | 当前 `BLOCKED` 诚实标注；接入需真实凭证，非本 RC 范围。 |

---

## 11. 下一步（按优先级）

1. **[BLOCKED→恢复]** 网络放开后推送 `feature/jev-trading-desk-v1` → 创建 **Draft** PR（base=main，不 merge）→ 触发 CI 核查。
2. **[可选清理]** 删除 `scripts/e2e/` 重复副本（需绕过 safe-delete 守卫或确认其无害保留）。
3. **[后续]** 接入真实 Provider 后，按红线复审是否开放 AI 角色与策略晋级（本 RC 不涉及）。

---

*报告结束。所有结论均有 §9 证据链支撑，可复现。红线 0 触碰，冻结引擎完整性保持。*

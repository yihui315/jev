# JEV Trading Desk — V1.3 Frontend E2E (T03)

> **生成 (UTC)**：2026-11-08 · **任务**：T03 前端 Release Candidate 验收（最终 HEAD）
> **验收 SHA**：`84e4cc28c6ce73c721892d8442d6ba92d55779be` · **结果**：✅ `all_passed=true`

## TL;DR

在最终提交 HEAD 上以独立 headless Chrome（CDP）重跑完整 E2E，覆盖六页面、行情切换、市价成交、合法限价挂单、`clientActionId` 撤单、双独立浏览器隔离、同会话刷新账本连续性、审计回放、断网/超时/错误态、控制台与网络。每轮记录原始证据，**不以旧工作区测试冒充最终提交验收**。

## 1. 验收矩阵

| 维度 | 结果 |
|---|---|
| 六页面渲染（command/workbench/universe/strategy/account/team） | ✅ 全部 `rendered=true` |
| 行情切换（BTCUSDT / ETHUSDT / 1000SATSUSDT） | ✅ |
| 小数精度 | ✅ |
| 市价成交（MARKET FILLED） | ✅ |
| 合法限价单挂单（OPEN） | ✅ |
| `clientActionId` 撤单（→ CANCELED） | ✅ |
| 两个独立浏览器账户隔离 | ✅ |
| 同会话刷新账本连续性 | ✅ |
| 审计回放 | ✅ |
| 断网 / 超时 / 错误态 | ✅（unreachable → 在线恢复） |
| 浏览器控制台错误 | ✅ `console_errors=[]` |
| 网络状态 | ✅ 全 `200/201`（撤单 200、下单 201、查询 200） |

## 2. Cookie 审计（实测）

`nr_paper_session` → `httpOnly=true, secure=true, sameSite=Strict, path=/api/, domain=127.0.0.1` —— 与源代码声明完全一致。

## 3. 六页面截图（原始证据）

`apps/command_center_3d/e2e/artifacts/v12/`：
`accept_command.png` · `accept_workbench.png` · `accept_universe.png` · `accept_strategy.png` · `accept_account.png` · `accept_team.png` · `accept_audit_drawer.png`

## 4. 原始日志

- 完整网络日志（全部请求 + HTTP 状态）见 `e2e/artifacts/v12/acceptance_browser.json` → `network_log`（约 60 条 Fetch，全部 200/201）。
- 控制台错误：`[]`（0 条）。
- 数据源审计抽屉诚实标注：REST 为真实公开数据、WebSocket `BLOCKED`（不冒充 LIVE）、策略 `NO_DATA`、AI 六角色 `BLOCKED`。

## 5. 证据文件

- `e2e/artifacts/v12/acceptance_browser.json`（`git_sha=84e4cc2`, `all_passed=true`）
- `e2e/artifacts/v12/accept_*.png`（7 张）
- 脚本：`scripts/v12_acceptance_browser_test.mjs`

## 6. 红线

仅本地 `127.0.0.1` 运行；纯 Paper；无真实资金/实盘；未部署生产。

# V1.4 · T01 — Cloudflare CI 与环境验收

- 执行时间（UTC）：2026-11-08 14:55 – 15:30
- 执行环境：WorkBuddy 本地 Windows + Cloudflare Worker 边缘
- 基线 SHA：`05d3ec8`（远端）／本地 `6777878`（本轮起点）
- Pages 项目：`jev-cloud-staging`；D1：`jev-paper-staging`
- 测试数量：22 项检查 → **PASS 16 / FAIL 1 / BLOCKED 5**
- 结论：**FAIL — CI 门禁未通过，PR #4 必须保持 Draft**

## 1. 本地与远端一致性（规范预期"两个 SHA 相同、差异为空"）

| 项 | 结果 | 判定 |
|---|---|---|
| 工作区 `git status --short` | 空 | PASS |
| 本地 HEAD | `6777878c484a6cb64b880faa5b9b7fa02a1dfd3e` | — |
| 远端 `origin/feat/jev-cloudflare-staging` | `05d3ec86944510f1023d0a3c44b867930f01e404` | — |
| `git diff --exit-code HEAD origin/...` | 非空（3 文件变化） | **FAIL** |
| ahead/behind | `2 / 0`（本地领先 2，可快进） | 信息 |

规范预期不成立的原因：本轮开始时，本地领先远端 2 个提交 —— `bb0eb10`（Stage 2 真实行情网关）与 `6777878`（Stage 2 验收报告），均由上一阶段产生但尚未推送。用户方案基于推送前的 `05d3ec8` 编写。`git fetch` 在沙箱内返回 `CONNECT tunnel failed, response 502`，远端真值改用 GitHub 连接器独立核验。

## 2. 本地门禁

| 命令 | 退出码 | 判定 |
|---|---|---|
| `npm ci --no-audit --no-fund` | 1 | **BLOCKED**（非代码缺陷，见下） |
| `npm run typecheck` | 0 | PASS（恢复后） |
| `node cloud/tests/cf-pages-gate.check.mjs` | 0，6/6 PASS | PASS |
| `node cloud/tests/market-freshness.check.mjs` | 0，10/10 PASS | PASS（本轮新增） |
| `npm run build` | 0 | PASS（恢复后） |

`npm ci` 失败原文（脱敏）：

```
npm error [safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]
{"count":75,"threshold":50,"scope":"turn",
 "targets":["...\\node_modules\\.bin"]}
npm_ci_exit=1
```

沙箱批量删除保护阻止 `npm ci` 清理 75 个文件，且已部分删除 `node_modules`，导致紧随其后的 typecheck（`Cannot find module '@vitejs/plugin-react'`）与 build（`rollup native.js MODULE_NOT_FOUND`）连锁失败。**这不是代码缺陷**：远端 CI 同名步骤 "Install exact dependencies" 为 SUCCESS。处置：未绕过安全钩子，改用非破坏性 `npm install`（added 9 packages, exit 0）恢复后，三项门禁全部重跑为 0。

## 3. GitHub Actions 全量检查（不止 Docker Gate）

PR #4 head `05d3ec8`，`mergeable_state: unstable`，共 **4 个 check runs**：

| 检查 | 结论 | 时间(UTC) |
|---|---|---|
| Real Binance market-data probe (non-blocking) | success | 14:14:18–14:14:33 |
| staging-container-smoke | success | 14:13:47–14:14:21 |
| **verify-and-deploy** | **failure** | 14:13:44–14:14:07 |
| Typecheck · Build · Unit tests (mock) | success | 14:13:43–14:14:16 |

失败 job `verify-and-deploy` 的 13 个 step 中，step 1–7 全部 success（含 npm ci / typecheck / gate / build），**step 8「Check available Cloudflare deploy authorization (fail closed)」failure**，step 9–13 全部 skipped：

```
step 5 Typecheck                      success
step 6 Verify preview auth + gate     success
step 7 Build staging                  success
step 8 Check available CF deploy auth FAILURE  <-- root cause
step 9 Install Wrangler               skipped
step 10 Find or create Pages project  skipped
step 11 Install encrypted secrets     skipped
step 12 Upload Pages                  skipped
step 13 HTTPS smoke                   skipped
```

check-run annotations 原文（脱敏）给出五项缺失：

```
Missing GitHub Actions secret: CLOUDFLARE_ACCOUNT_ID
Missing GitHub Actions secret: CLOUDFLARE_API_TOKEN
Missing GitHub Actions secret: JEV_PREVIEW_USER
Missing GitHub Actions secret: JEV_PREVIEW_PASSWORD
Missing GitHub Actions secret: JEV_PAPER_LEDGER_SECRET
Process completed with exit code 1.
```

→ **五项 Secrets 全部未配置（0/5）**，CI 按设计 fail-closed。这是「Cloudflare 部署 CI 从未实跑」的确切根因，不是"尚未确认"而是**已确认 FAILURE**。

注意：annotation 另有 warning（Node.js 20 deprecated，actions 被强制跑在 Node 24）与 notice（ubuntu-latest 将迁移 Ubuntu 26），均非阻断。

## 4. 工作流清单（3 个，非仅 Docker Gate）

| 文件 | 用途 |
|---|---|
| `.github/workflows/ci.yml` | 主 CI（Typecheck/Build/Unit + market probe + container smoke） |
| `.github/workflows/cloud-staging-runtime.yml` | 运行时容器门禁 |
| `.github/workflows/jev-cloudflare-staging.yml` | **失败的** Cloudflare 部署工作流 |

本轮新增 `.github/workflows/v14-acceptance.yml`（4 个独立 Jobs：local-gates / market-diagnosis / paper-trading-e2e / d1-security），仅 `workflow_dispatch` 触发，避免在 Secrets 配齐前给 PR #4 增加红/绿检查；Secrets 缺失时输出 `::warning::BLOCKED` 并显式记录"未执行任何测试"，绝不伪造 PASS。

## 5. 本轮发现并修复的 CI 缺陷（2 处）

**[FIX-1]** smoke 假定 `/api/jev/paper/account` 仅凭 Basic auth 返回 200。实测两层认证下 Basic-only = **401**（需同时携带 Paper Session Cookie）。即使 Secrets 配齐，原 smoke 也必然失败。已改为：先验 Basic-only 401，再 POST `/api/jev/paper/session` 取 `-c` cookie，带 `-b` cookie 断言 200。

**[FIX-2]** `/trading.html` 带 Basic 返回 **308**（Cloudflare clean-URL 重定向到 `/trading`），原 `curl --fail` 未加 `-L`，会得到空响应体导致 `test -s` 失败。已改为 `--location` 并直接请求 `/trading`。

另将新单测 `market-freshness.check.mjs` 纳入 verify-and-deploy 的门禁步骤。

## 6. Cloudflare 资源核验（只读，未创建/未迁移）

| 配置 | 规范值 | 实测 | 判定 |
|---|---|---|---|
| Pages 项目 | `jev-cloud-staging` | 存在，最新生产部署 `f2157f65` → 本轮 `4b4dec81` | PASS |
| D1 数据库 | `jev-paper-staging` | 存在 | PASS |
| D1 UUID | `55b690ec-732f-4f34-8b42-084dd15ee164` | 一致 | PASS |
| D1 绑定名 | `PAPER_DB` | `wrangler.toml` 一致 | PASS |
| `ENABLE_REAL_TRADING` | `false` | Pages secret 存在且网关首行 fail-closed；`health.real_money=false` | PASS |
| 兼容标志 | `nodejs_compat` | `wrangler.toml` 一致 | PASS |
| D1 表 | 五份迁移 | 18 表（含 7 张 `paper_*`），只读查询 | PASS |

`wrangler whoami` 授权有效。Pages 生产环境 Secrets **仅 4 项**（`ENABLE_REAL_TRADING`、`JEV_PREVIEW_PASSWORD`、`JEV_PREVIEW_USER`、`PAPER_LEDGER_SECRET`，值均加密未读取）——与 GitHub Actions 的五项 Secrets 是两个不同体系，不要混淆。

`d1 list` 显示 `num_tables: 0` 属 wrangler 元数据列不准，以 `sqlite_master` 实查 18 表为准。

## 7. HTTPS 鉴权矩阵（部署 `c340c120`，无凭据输出）

| 测试 | 预期 | 实测 | 判定 |
|---|---|---|---|
| 匿名 `/trading` | 401 | 401 | PASS |
| 错误密码 `/trading` | 401 | 401 | PASS |
| 正确 Basic `/trading` | 200 | 200 | PASS |
| 匿名 `/api/jev/health` | 401 | 401 | PASS |
| 正确 Basic `/api/jev/health` | 200 JSON | 200 | PASS |
| `real_money` / `real_exchange_orders` | false | false / false | PASS |
| `engine_modified` | false | false | PASS |
| 6 个 paper 路由匿名/错密 | 401 | 全 401 | PASS |

## 8. 生产/Staging 混用审查

仅操作 `jev-cloud-staging` Pages 项目与 `jev-paper-staging` D1；未触碰 Release 分支、未合并 PR、未创建/删除数据库、未执行破坏性迁移。D1 中存在 `paper_test`（另一既有库），本轮**未读写**。**BLOCKED 项**：需由仓库主理人确认该库是否为历史遗留并决定是否清理（本轮不动）。

## 9. 未通过 / 阻塞汇总

| # | 项 | 状态 | 归属 |
|---|---|---|---|
| B1 | 五项 GitHub Actions Secrets 0/5 | **FAIL**（CI 阻断） | 用户操作 |
| B2 | `verify-and-deploy` CI 实跑回归 | BLOCKED（依赖 B1） | 用户操作 |
| B3 | 本轮 `npm ci` 本地执行 | BLOCKED（沙箱删除钩子；远端 CI 已 SUCCESS） | 环境 |
| B4 | 本地领先远端 2 提交未推送 | 待用户 push（本轮新增提交后为 3+） | 用户操作 |
| B5 | `v14-acceptance.yml` 4 Jobs 实跑 | UNVERIFIED（依赖 B1） | 用户操作 |

## 10. 证据路径

- `reports/evidence/` 下 10 个脱敏 JSON（本报告的原始 CI 结论来自 GitHub check-run annotations，见 §3 引文）
- 工作流定义：`.github/workflows/jev-cloudflare-staging.yml`、`v14-acceptance.yml`
- 失败 job：`https://github.com/yihui315/jev/actions/runs/42488765254/job/128756309991`

> 日志中不含 Basic Auth 密码、Cookie、Session ID 或任何密钥密文。

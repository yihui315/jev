# JEV V1.5 · P0 — Cloudflare 凭据最小权限治理与轮换报告

**结论：P0 GO（轮换已验证）；生产晋级仍 NO-GO。**
**本报告不记录任何 Token 明文；所有凭据值仅存于 Windows DPAPI(CurrentUser) 加密库。**

## 1. 核验口径

用户要求「核验当前 Token 的**实际授权策略**，而非仅统计权限组目录」。本轮通过
`GET /accounts/{AC}/tokens/verify` + `GET /accounts/{AC}/tokens/{id}` + `GET /accounts/{AC}/tokens`
读取**账户级 Token 的真实 policy 对象**，而非只调用权限组目录接口。

> 关键纠错（沿用 V1.4）：该 Token 是 **account-owned**，验证端点是
> `/accounts/{AC}/tokens/verify`，不是 `/user/tokens/verify`（后者 401 Invalid API Token）。

## 2. 旧 Token 实测授权（轮换前）

| 项 | 实测值 |
|---|---|
| Token ID | `86ba72a91fb934bce8eb8da30d3c97b2` |
| 名称 | `young-credit-afaa` |
| 状态 / 签发 | active / 2026-11-09T03:40:40Z |
| 策略数 | **1** |
| 权限组数 | **290** |
| resources | `com.cloudflare.api.account.b300a9b5… : "*"`（账户全域） |
| 高危授权（实测存在） | `Account API Tokens Read/Write`、`Workers Admin`、`Secrets Store Read/Write`、`Workers R2 Storage Write`、`Workers Containers Write`、`Access: Service Tokens Write`、`D1 Write`、`Pages Write` 等 |
| 判定 | **超权限（OVER-PRIVILEGED）**，远超 JEV Staging 所需 |

账户内另有第三个 Token `d144e38899ca4433b2646a9015b12418`（`wandering-paper-edce`，297 权限组，
2026-11-09T03:44:04Z 有使用记录）。它**不在本次凭据库、不在本次轮换范围**，见 §7 风险。

## 3. 是否需要账户管理员授权

Cloudflare 官方文档（Create Token / Delete Token）标明：
`POST /accounts/{account_id}/tokens` 与 `DELETE /accounts/{account_id}/tokens/{token_id}`
的 Accepted Permission 均为 **`Account API Tokens Write`**。旧 Token 的真实 policy 已确认包含该权限，
且实测 `POST` 返回 200、`DELETE` 返回 200。
**因此本轮创建/撤销不需要额外账户管理员授权，未触发暂停条件；未做任何绕过。**

## 4. 新 Token（最小权限）

| 项 | 值 |
|---|---|
| Token ID | `8ead4d5c86a4165149955dce0404370f` |
| 名称 | `jev-v15-staging-least-privilege` |
| 策略数 / 权限组数 | **1 / 5** |
| 权限组 | `Pages Read`、`Pages Write`、`D1 Read`、`D1 Write`、`Workers Scripts Write` |
| 组 ID | `e247aedd…` / `8d282977…` / `192192df…` / `09b2857d…` / `e086da7e…` |
| resources | `com.cloudflare.api.account.b300a9b5… : "*"`（legacy `permission_groups` 仅支持账户级作用域） |
| 有效期 | 2026-11-09T04:21:37Z → **2027-02-07T15:59:59Z**（与原 Token 同到期日，非永久 Token） |

选型依据：`Pages R/W` 用于受控部署与部署查询；`D1 R/W` 用于权益快照读写与只读核验；
`Workers Scripts Write` 用于 P2 独立 Scheduled Worker 的隔离部署。**不包含** `Account API Tokens *`、
`Workers Admin`、`Secrets Store *`、`R2 *` 等任何凭据/账户管理能力。
权限组由 `GET /accounts/{AC}/tokens/permission_groups`（415 项）逐项取 ID，非猜测。

## 5. 轮换时间线与证据

| # | 时间 (UTC) | 操作 | 结果 |
|---|---|---|---|
| 1 | 04:21:32 | 只读核验旧 Token 真实 policy | 1 policy / 290 groups / 含 `Account API Tokens Write` |
| 2 | 04:21:37 | `POST /accounts/{AC}/tokens` 创建新 Token | 200，`status=active`，1 policy / 5 groups |
| 3 | 04:21:37 | 立即写入 DPAPI 加密库（`cf_api_token_v15`） | 加密保存，**明文未打印、未落盘** |
| 4 | 04:21:44 | 新 Token `/tokens/verify`；旧 Token 读回新 Token policy | 200 / active；读回 5 groups 一致 |
| 5 | 04:22:xx | 新 Token 页面与 D1 探测 | Pages project 200、deployments 200、D1 list 200、D1 只读 query 200 |
| 6 | 04:22:17 | 用**新 Token** 创建隔离 D1 `jev-v45-equity-isolated-test`（`cf5cf033-3796-4de1-b295-93f35a8ce82e`），执行 CREATE/INSERT/SELECT | 3/3 200 success → **D1 写权限已实测** |
| 7 | 04:22:48 | 公钥加密更新 GitHub Secret `CLOUDFLARE_API_TOKEN` | `PUT` **204**，5/5 Secrets 仍 PRESENT |
| 8 | 04:23:07 → 04:23:52 | **受控 Staging 部署 + HTTPS Smoke** | CI Run **42561524124** SUCCESS（14/14 步） |
| 9 | 04:23:44 | 新部署 | `3da51661-cb7e-4929-840e-adb9eac0a3ba` → `https://3da51661.jev-cloud-staging.pages.dev` |
| 10 | 04:23:48 | 新 Token `last_used_on` 更新 | 与部署时刻吻合 → **证明 CI 实际使用了新 Secret** |
| 11 | 04:25:37 | **撤销旧 Token**（`DELETE`） | 200 success |
| 12 | 04:25:37+ | 撤销后复核 | 旧 Token verify **401 Invalid API Token**；新 Token verify **200 active**；新 Token Pages **200**；新 Token 列 Token **403**（无权管理凭据，符合最小权限） |

**顺序合规：旧 Token 直到第 8–10 步全部验收通过后才在第 11 步撤销，未先撤销。**
一次 `workflow_dispatch` 重复触发产生的多余 run `42561546546` 已 `cancel`（202），
**不计入 PASS**，本轮只保留一次受控部署。

## 6. CI Run ID 与凭据状态

- 最新受控部署 + HTTPS Smoke：**`42561524124`**（`feat/jev-cloudflare-staging` @ `7f26e1f`，14/14 步 SUCCESS）。
- GitHub Actions Secrets：`CLOUFLARE_ACCOUNT_ID`、`CLOUDFLARE_API_TOKEN`、`JEV_PREVIEW_USER`、
  `JEV_PREVIEW_PASSWORD`、`JEV_PAPER_LEDGER_SECRET` — **5/5 PRESENT**（只报 PRESENT/MISSING，不回显值）。
- `JEV_PAPER_LEDGER_SECRET` 本轮**未改动**，不构成轮换，历史 Paper Session 不失效。
- Credential Center 条目：`cf_api_token` / `cf_api_token_v15` = 新 Token；
  `cf_api_token_rotated_20261109` = 旧 Token（已撤销，仅加密留存供审计/回滚取证）。

## 7. 风险与 BLOCKED

| 级别 | 项 | 说明 / 下一步 |
|---|---|---|
| HIGH | 账户内仍有 `d144e388…`（297 权限组，含 `Account API Tokens Write`）处于 active 且近期有使用 | **不在本次轮换范围、未擅自撤销**。需主理人在 Dashboard 确认其用途后单独决定撤销/收敛；记 BLOCKED，待明确批准。 |
| MEDIUM | legacy `permission_groups` 资源只能账户级 `*`，无法把新 Token 限定到单个 Pages project / 单个 D1 | 权限面已由 290 → 5；建议后续迁移到 Cloudflare 新 granular resource-level scopes，实现单资源授权。 |
| LOW | 新 Token 无 `Account API Tokens Write` | 符合预期（防凭据自提升）；未来轮换需账户管理员或既有高权限 Token。 |
| INFO | 隔离测试 D1 `cf5cf033…` 由 P2 使用 | 非 `jev-paper-staging`、非 Night Researcher 生产 D1；未删除任何 D1。 |

## 8. 硬门禁复核（只读）

- `ENABLE_REAL_TRADING=false`：Pages env var 为 `secret_text`，API 回显值被隐藏，故采用**构造性证明**——
  网关 `[[path]].js:719` 在非 `"false"` 时对所有 paper 路由返回 500 `RED_LINE`；
  实测 `POST /api/jev/paper/session` → **200 / `session=created`** ⇒ 运行时该值必为 `"false"`。
- `jev-cloud-staging` D1 绑定仅 `PAPER_DB = 55b690ec-732f-4f34-8b42-084dd15ee164`；域名仅 `*.pages.dev`。
- `night-researcher-dash` production/preview **均无 D1 绑定**，本轮未触碰。
- `paper_equity_snapshots`（Staging D1）仍 **0 行**：未伪造样本。
- 冻结引擎 `server/engine/` 15 个文件在 `ce8d570` / feat / audit / 集成 HEAD 四处
  tree sha256 全为 `aec5d1b7cc26db50736d355e7ef14e066fae3f2c2a376d94637c9a977008a9ad` → **字节零改动**。
- 无 force push、无 D1 删除、无 Release 合并、无 PR #4 修改。

## 9. DECISION

**P0 = GO（轮换 VERIFIED_ROTATED）。** 生产晋级仍为 **NO-GO**（见最终验收报告）。

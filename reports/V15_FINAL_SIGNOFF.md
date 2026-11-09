# JEV V1.5 — 最终验收 Signoff

**日期**：2026-11-09（UTC）
**集成分支**：`feature/v15-staging-hardening`
**新 Draft PR**：**https://github.com/yihui315/jev/pull/5**（`draft=true`，`state=open`，base `release/jcc-v1.3-final`，**未合并**）

## TL;DR

| 项 | 决策 |
|---|---|
| **P0 Cloudflare 凭据最小权限治理** | **GO**（290 → 5 权限组，旧 Token 已撤销并验证失效） |
| **P1 Git 分支安全收口** | **GO**（双亲 merge，12/12 差异回归 PASS，无强推） |
| **P2 V45 Equity Collector（隔离测试环境）** | **GO WITH CONDITIONS**（本地 8/8、95 断言；云端隔离 D1 12 行真实快照、回放 12/12） |
| **P2 接入 `jev-paper-staging`** | **NO-GO / 仅评估，未执行** |
| **P3 真实市场覆盖** | **GO（核查完成）**；新增 Global 来源接入 **NO-GO / CONDITIONAL** |
| **V1.5 整体（CI 与 Staging 加固）** | **GO** |
| **生产晋级（V1.4 → 生产）** | **NO-GO**（BLOCKED 未清零） |

统计：**PASS 24 / FAIL 0 / BLOCKED 9**（P0 6 项核验、P1 12 项差异回归、P2 本地 8 组+95 断言、P2 云端 12 行回放、P3 56 份证据；统计口径为本轮独立完成项，未把 SKIPPED/CANCELLED 计为 PASS）。

## 1. 交付物清单

| 交付 | 路径 |
|---|---|
| V1.5 Token 权限与轮换报告 | `reports/V15_TOKEN_ROTATION.md` |
| Git 分支差异及整合记录 | `reports/V15_BRANCH_INTEGRATION.md` |
| Equity Collector 设计与独立测试证据 | `reports/V15_EQUITY_COLLECTOR.md`、`apps/equity_collector_v45/DESIGN_AND_TESTS.md` |
| Collector 源码 / schema / 配置 / 测试 | `apps/equity_collector_v45/`（src、schema.sql、tests、wrangler.*、deployed_source_sha256.json） |
| D1 权益快照与回撤校验结果 | `reports/evidence/equity/`（`v15_cloud_collector_evidence.json`、`v15_equity_d1_replay.json`、`local/*.json`） |
| 市场覆盖与网络上下文 | `reports/V15_MARKET_COVERAGE.md`、`reports/V15_MARKET_EDGE_CONTEXT.md`、`reports/evidence/market/`（56 份脱敏 JSON） |
| 分支差异回归脚本 / 行情复现脚本 | `scripts/v15_branch_diff_check.py`、`scripts/v15_market_probe.py` |
| 综合脱敏证据 | `reports/evidence/V15_CONSOLIDATED_EVIDENCE.json`、`V15_BRANCH_DIFF_CHECK.json` |
| 本 Signoff | `reports/V15_FINAL_SIGNOFF.md` |

## 2. P0 — Cloudflare 凭据最小权限治理（GO）

- 旧 Token `86ba72a9…` 真实 policy：**1 策略 / 290 权限组 / 账户全域**，含 `Account API Tokens Write`、
  `Workers Admin`、`Secrets Store Write` → 判定超权限（不是只数目录）。
- 官方文档确认 `POST/DELETE /accounts/{AC}/tokens` 只需 `Account API Tokens Write`（旧 Token 已具备）
  → **不需要额外账户管理员授权**，未触发暂停条件，未绕过。
- 新 Token `8ead4d5c…`：**5 权限组** `Pages R/W`、`D1 R/W`、`Workers Scripts Write`，
  有效期至 2027-02-07；不含任何凭据/账户管理能力。
- 顺序：创建 → DPAPI 加密入库 → Pages/D1 读写实测 → GitHub Secret 更新（204）
  → 受控部署 + HTTPS Smoke（**14/14 SUCCESS**）→ **最后**撤销旧 Token。
  撤销后：旧 Token verify **401 Invalid API Token**；新 Token verify **200 active**、Pages **200**、
  列 Token **403**（确无凭据管理权）。
- 5/5 Secrets PRESENT；`JEV_PAPER_LEDGER_SECRET` 未改动（不构成轮换，历史 Session 不失效）。
- **报告中无任何 Token 明文**；值仅存 Windows DPAPI 加密库。

## 3. P1 — Git 分支安全收口（GO）

- 基点 `ce8d570`；feat 独有 3 提交、audit 独有 8 提交，在独立 worktree 中逐文件比对。
- 集成提交 `e92391b` 为**双亲 merge**，两条分支历史与证据全部可达；无 force push / squash / rebase。
- 取 feat 已验证 CI 修复（有界就绪重试 + 更强幂等断言），取 audit 两份报告，
  **排除**重复的 `v14-audit-acceptance.yml`。
- `scripts/v15_branch_diff_check.py` 真实执行 **12/12 PASS / 0 FAIL**（含冻结引擎
  15 文件四处 tree sha256 一致 `aec5d1b7…`）。
- PR #4、`release/jcc-v1.3-final`、`master` 均未修改；本地 5 个未推送提交原样保留。

## 4. P2 — V45 Equity Collector（GO WITH CONDITIONS）

- 独立 Scheduled Worker `jev-v45-equity-collector-isolated` + 独立 D1
  `cf5cf033-3796-4de1-b295-93f35a8ce82e`，**只绑隔离库**；测试写入入口验收后关闭（403）。
- 复用冻结引擎纯函数（vendor 字节副本），**冻结引擎 15 文件零改动**；
  `paper_accounts` state_hash 不变、`paper_server_events`/`paper_orders` 0 行。
- 本地 `node tests/run-all.mjs`：**8/8 PASS、95 断言 0 FAIL**
  （多次采集 / 重复运行 / 断线恢复 / 账户隔离 / 历史回放 / session fail-closed / 静态审计 / 行情探测）。
  T7 首次 FAIL 为检查器误报（注释与 vendor 副本被当成代码引用），按「修检查器、不删断言」修复后复跑全绿。
- 云端隔离 D1：3 cycle × 4 个**明确标注的 FIXTURE 账户** = **12 行 `paper_equity_snapshots` + 12 行 provenance**；
  C1 连跑 3 次 `written=0`（幂等）；9 VERIFIED / 3 UNKNOWN；真实 `api.binance.us` http 200 +
  payload_sha256 留痕；ORDIUSDT → `NO_VENUE_QUOTABLE_SYMBOLS`、equity=0、marks={}（无虚构价格）。
- **D1 回放 12/12 peak 逐条一致、12/12 drawdown 逐条一致 → PASS**；
  beta 回撤 `24263480`（1e8 定点 → 24.26348%），alpha/cash/ordi 各自独立，未跨账户抵消。
- Staging D1 `55b690ec…` 的 `paper_equity_snapshots` 仍 **0 行**（未伪造样本）。
- **未接入 `jev-paper-staging`、未接真实资金/正式生产账户**；接入条件见 `V15_EQUITY_COLLECTOR.md` §6。

> 诚实限制：云端 3 周期在 ~10 秒内完成，真实盘口价未变化，故云端未展示权益随行情变化；
> 该场景由本地 T1 注入真实响应覆盖。云端证据用于证明真实 D1 写入、真实 HTTP 溯源、幂等、隔离、回放。

## 5. P3 — 真实市场覆盖（GO / 新增来源 NO-GO）

- 三网络上下文：**A1** 本地 52498（US 502 传输失败）、**A2** 本地 10808（US 200）、
  **B** Cloudflare 边缘运行时（US 200 / FRESH / skew 38ms，Global 403）。
  连通性差异是**代理/上下文属性**，不是交易所属性；任何结论须带 `network_context`。
- Binance.US：BTCUSDT / ETHUSDT = **VERIFIED**（A2 五端点 200，含真实成交与盘口时间戳）；
  ORDIUSDT / 1000SATSUSDT = **BLOCKED**（A2 4/4 端点 `400 -1121 Invalid symbol`，可复现）。
- Binance Global spot（`data-api.binance.vision`）四 symbol 均有真实行情，但属**不同市场**，
  仅作候选来源；1000SATS 在该来源实测 **STALE**（滞后约 96.5s > 60s）。
  **不自动解除 Binance.US 的 BLOCKED，不得无标识混用或互为兜底。**

## 6. 最新 GitHub CI Run ID

| Run ID | 分支 / 提交 | 内容 | 结论 |
|---|---|---|---|
| **`42563795444`** | `feature/v15-staging-hardening` @ `d46e7aa` | 集成分支最终态完整 CI（Typecheck/Build/Unit + 真实行情 probe，2 Job） | **SUCCESS** ← 最新 CI |
| `42562286297` | 同分支 @ `60c8678` | 首次集成推送 CI（2 Job） | SUCCESS |
| **`42561524124`** | `feat/jev-cloudflare-staging` @ `7f26e1f` | P0 受控 Staging 部署 + HTTPS Smoke（14/14 步） | **SUCCESS** |
| `42561546546` | 同上 | 重复触发的部署 run，已 `cancel`(202) | **CANCELLED，不计 PASS** |
| `42559209039` | V1.4 审计分支 | 4-Job 验收（V1.4 已通过） | SUCCESS（引用，未重复执行） |

- 本次受控部署产物：`3da51661-cb7e-4929-840e-adb9eac0a3ba` → `https://3da51661.jev-cloud-staging.pages.dev`。
- **按「不重复执行已通过的所有测试」要求，V1.4 的 4-Job 验收套件本轮未重跑**，引用其 `42559209039` 4/4 SUCCESS。
  P1 的「完整 CI」由集成分支 `ci.yml` 2 Job + 12/12 差异回归 + P0 的 14/14 部署 smoke 构成。
- 新 Token `last_used_on = 2026-11-09T04:23:48Z` 与部署时刻吻合 → 证明 CI 实际用了轮换后的 Secret。

## 7. 硬门禁复核

| 门禁 | 结果 |
|---|---|
| `ENABLE_REAL_TRADING=false` | **PASS**：网关 `[[path]].js:719` 非 `"false"` 即 500 `RED_LINE`；实测 `POST /api/jev/paper/session` → 200 / `session=created` |
| 不强推 | PASS（双亲 merge，无 `--force`，无 squash/rebase） |
| 不删除 D1 | PASS（新增隔离测试 D1，未删任何库） |
| 不修改冻结交易引擎 | PASS（15 文件四处 tree sha256 全为 `aec5d1b7…`） |
| 不合并 Release / 不改 PR #4 | PASS（PR #5 为 Draft、未合并；`release/jcc-v1.3-final` @ `b8b2e60` 未动） |
| 不伪造样本及收益 | PASS（Staging D1 权益表 0 行；隔离库账户全部标 `v45-FIXTURE-`；UNKNOWN 不写虚构价） |
| Staging 独立运行 | PASS（`jev-cloud-staging` 仅绑 `PAPER_DB=55b690ec…`，域名仅 `*.pages.dev`） |
| Night Researcher 生产 D1 | PASS（production/preview 均无 D1 绑定，未触碰） |

## 8. BLOCKED / 未清零风险（9 项）

| # | 级别 | 项 | Owner / 下一步 |
|---|---|---|---|
| 1 | **HIGH** | 账户内另有 `d144e388…`（297 权限组、含 `Account API Tokens Write`）仍 active 且近期有使用；**不在本次轮换范围，未擅自撤销** | 主理人在 Dashboard 确认用途后单独决定收敛/撤销 |
| 2 | HIGH | `v45_snapshot_provenance` 缺独立 `exchange` / `market` / `base_asset` / `quote_asset` / `price_unit` / `network_context` / `book_ts` 字段 | 独立 QA 建议；V1.5 记为待补 schema，接入前必须补齐 |
| 3 | MEDIUM | `paper_equity_snapshots`（Staging D1）仍 0 行；Collector 未接入 Staging | 待独立评审后再评估接入，本轮不执行 |
| 4 | MEDIUM | ORDIUSDT / 1000SATSUSDT 在 Binance.US **BLOCKED**（`-1121`） | 保持 BLOCKED；不得用 Global 价格平替 |
| 5 | MEDIUM | Binance Global spot 接入 = CONDITIONAL / NO-GO（且 1000SATS 该源 STALE） | 需逐字段标识与独立评审，主理人决策 |
| 6 | MEDIUM | 30 天真实研究样本仍 0 | 后续真实运行累积，不得伪造 |
| 7 | MEDIUM | legacy `permission_groups` 只能账户级 `*`，新 Token 未限定到单个 Pages project / 单个 D1 | 后续迁移 Cloudflare granular resource-level scopes |
| 8 | LOW | Cookie 7 天过期未实测（V1.4 遗留） | 后续专项测试 |
| 9 | LOW | 本地 52498 路径 `api.binance.us` 传输失败（A1） | 记录 `network_context`；本地测试用注入 fetcher，不泛化结论 |

> 另：PAT `df4ebf8c` 仍缺 `actions=write`（dispatch 403），但 `33f4a666` 本轮实测
> `workflow_dispatch` 204 成功，故该缺口已不构成阻塞；未做任何权限绕过。

## 9. GO / NO-GO 决策

- **V1.5 安全治理、分支收口、CI 加固 = GO。**
- **V45 Equity Collector 隔离测试环境 = GO WITH CONDITIONS**（条件：补齐 #2 溯源字段、
  完成 #3 接入评审、保持 #4/#5 市场 BLOCKED）。
- **接入 `jev-paper-staging` = NO-GO；生产晋级 = NO-GO。**  blocker 未清零（#1–#9）。
- 本轮**未**合并任何 Release、未修改 PR #4、未强推、未删 D1、未改冻结引擎、未开启真实交易、
  未伪造任何样本或收益。

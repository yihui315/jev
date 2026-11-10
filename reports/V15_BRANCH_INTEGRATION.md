# JEV V1.5 · P1 — 双分支差异核验与整合记录

**结论：P1 GO（整合完成、差异回归 12/12 PASS）；未强推、未改写历史、未动 PR #4 / Release。**

## 1. 只读核验基线

| 项 | 值 |
|---|---|
| 共同基点 | `ce8d570` |
| 功能分支 | `origin/feat/jev-cloudflare-staging` @ `7f26e1f` |
| 审计分支 | `origin/feature/v14-ci-audit-20261109` @ `9923067` |
| 独立 worktree | `D:/jev-v15-wt/feat`（detached `7f26e1f`）、`D:/jev-v15-wt/audit`（detached `9923067`）、`D:/jev-v15-wt/integration` |
| 远端引用同步 | `git fetch --prune origin '+refs/heads/*:refs/remotes/origin/*'`（经 10808 代理），仅更新 remote-tracking，不改工作区 |

### 功能分支独有提交（3，以 `ce8d570` 为基点，实测）

| SHA | 说明 |
|---|---|
| `3d7ac61` | trigger staging deploy after Secrets configured（仅注释，无逻辑变化） |
| `b05028e` | CI smoke 有界就绪重试（保留并强化断言） |
| `7f26e1f` | 幂等断言对齐真实重复单契约（更强检查） |

### 审计分支独有提交（8，实测）

| SHA | 说明 |
|---|---|
| `376c5de` | 创建审计 CI 分支标记（不消耗 Secrets） |
| `13c33d6` | 专用审计分支上的最终 GitHub CI signoff |
| `3a515dc` | 补第二个真实 CI Run ID（[skip ci]） |
| `78c12b0` | 在审计分支落 4-job 验收工作流（workflows write probe） |
| `ef846be` | 部署成功后在审计分支跑真实 4-job 验收 |
| `c869e4b` | push 触发下补默认 base_url（无断言变更） |
| `53330a4` | 幂等断言对齐真实重复单契约（4 job 复跑） |
| `9923067` | 最终 CI signoff v3（5/5 Secrets、部署 14/14、4/4 jobs、NO-GO） |

## 2. 逐文件差异（`git diff --name-status feat audit`）

| 文件 | 状态 | 处置 |
|---|---|---|
| `.github/workflows/jev-cloudflare-staging.yml` | M | **取 feat 版**（已验证的 14/14 部署修复） |
| `.github/workflows/v14-acceptance.yml` | M | **取 feat 版**（更强的幂等断言） |
| `.github/workflows/v14-audit-acceptance.yml` | A（审计独有） | **不引入**：仅是同一验收工作流的 push 触发副本，重复且会与 canonical 工作流并发 |
| `reports/V14_AUDIT_BRANCH.md` | A（审计独有） | **引入**（审计链证据） |
| `reports/V14_GITHUB_CI_SIGNOFF.md` | A（审计独有） | **引入**（V1.4 最终验收证据，v3） |

### 工作流断言差异（逐行核验）

1. `jev-cloudflare-staging.yml`：feat 版把匿名 `paper/account`、创建 session、`paper/account`
   三段改为**有界重试**（20/20/10 次、每次 3s），但**保留并强化**原断言：
   `test "$anon" = 401`、`test "$session_status" = 200`、`test "$status" = 200`，
   并新增 `d['data']['session']=='created'`。持久失败仍 FAIL，不存在「重试掩盖失败」。
2. `v14-acceptance.yml`：feat 版把错误断言
   `assert b['duplicate'] is True and len(b['fills'])==0` 改为真实契约强断言：
   `duplicate is True` + `not (b.get('fills') or [])` + `b['orderId']==m['order']['id']`
   （重复响应**没有 `fills` 字段**，且必须回指原订单）。
3. 测试脚本 `cloud/tests/market-freshness.check.mjs` 与 `reports/evidence/**` 在两分支**完全一致**
   （两分支 name-status 差异仅上述 5 个文件），故无需合并、无冲突。

## 3. 整合方式

- 新分支 `feature/v15-staging-hardening` 以 `origin/feat/jev-cloudflare-staging`（`7f26e1f`）为起点，
  用 `git merge --no-ff --no-commit origin/feature/v14-ci-audit-20261109` 合并审计分支。
- 合并自动完成、无冲突；在合并提交中 `git rm` 掉重复的 `v14-audit-acceptance.yml`，
  保留两份 V1.4 报告。
- 集成提交 **`e92391b`**，双亲（`7f26e1f` + `9923067`）→ **两条分支全部原有历史与证据均可达**。
- 无 force push、无 squash、无 rebase、无 cherry-pick 丢弃。

### 集成结果差异

| 对比 | 结果 |
|---|---|
| `HEAD` vs feat | 仅 `A reports/V14_AUDIT_BRANCH.md`、`A reports/V14_GITHUB_CI_SIGNOFF.md`、`A scripts/v15_branch_diff_check.py`、V1.5 报告 |
| `HEAD` vs audit | 仅 `M jev-cloudflare-staging.yml`、`M v14-acceptance.yml`、`D v14-audit-acceptance.yml` |

### 本地 5 个未推送提交

本地 `feat/jev-cloudflare-staging` @ `3132144` 仍有 5 个未推送提交
（`dec44ba`/`59182c2`/`31d7a89`/`86750cf`/`3132144`），与远端 `3d7ac61`/`b05028e`/`7f26e1f`
内容重复（V1.4 报告内容与 `9923067` 逐字节一致，已用 `git diff` 验证）。
**本轮未强推、未删除、未改写该本地分支**，其历史完整保留；同步方式仍由主理人决定。

## 4. 差异回归测试（真实执行）

脚本：`scripts/v15_branch_diff_check.py`，输出：`reports/evidence/V15_BRANCH_DIFF_CHECK.json`，
**exit code 0，12/12 PASS，0 FAIL**：

| # | 检查 | 结果 |
|---|---|---|
| 1–2 | 两个工作流与 feat 的 blob sha256 完全一致 | PASS |
| 3–4 | 两份报告与 audit 的 blob sha256 完全一致 | PASS |
| 5 | `v14-audit-acceptance.yml` 在集成 HEAD 不存在 | PASS |
| 6 | 冻结引擎 `server/engine/` 15 文件在 HEAD/feat/audit/ce8d570 四处 tree sha256 一致 | PASS |
| 7–11 | 5 条关键断言（session=created、有界重试、`status=200`、幂等强断言）全部存在 | PASS |
| 12 | 集成提交双亲 → 两分支历史保留 | PASS |

## 5. CI 与 PR

- 推送到 `origin/feature/v15-staging-hardening` 后由 `ci.yml` 的 `feature/**` push 触发器自动跑
  Typecheck / Build / Unit + 真实行情 probe；另以 `workflow_dispatch` 在集成分支跑
  `v14-acceptance.yml` 4 Job。**最新 Run ID 与 Draft PR 地址见 `V15_FINAL_SIGNOFF.md`。**
- `jev-cloudflare-staging.yml` 的 push 分支过滤仍为 `feat/jev-cloudflare-staging`，
  因此集成分支推送**不会**额外触发生产部署；P0 的受控部署已在 `42561524124` 独立完成。
- PR #4（已合并）、`release/jcc-v1.3-final`、`master` 均未修改；未执行任何合并。

## 6. DECISION

**P1 = GO（INTEGRATION VERIFIED）。** 保留全部历史与证据；重复旧工作流未引入。

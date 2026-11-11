# JEV V2.0 — 远端 CI 可签收性评估（独立 QA）

> 作者：independent-qa（结论来自本人实跑与本人直读的远端元数据；不采信任何人的自述）
> 仓库：`C:/Users/Administrator/WorkBuddy/jev-v16`｜本地分支 `feature/v16-p0-gap-closure`｜本地 HEAD `0d1d6c5`
> 远端分支 `feature/jev-v2-ops-integration`｜**远端尖端 `2b6be4a`（2026-11-11T06:12:34Z 实测）**
> 基线 `ebc3fcc`（= `origin/feature/v16-market-provenance-hardening`）｜PR **#7（Draft，base `feature/v16-market-provenance-hardening`）**
> 姊妹文档：`JEV_V2_PAPER_READINESS.md`（Paper 基线证据 / F-18 / F-19 / 入口陷阱，已冻结不再改）
> 本文件只管一件事：**这份提交推上去，远端 CI 到底跑没跑、跑出了什么、能不能签收。**

---

## 0. 结论速览

| 问题 | 结论 | 判定依据 |
|---|---|---|
| 远端 CI 是否真实运行过 | **是，7 次 run，全部落在 `feature/jev-v2-ops-integration`** | 本人直读 GitHub REST `actions/runs` |
| 最终验证 SHA | **`0d1d6c5`**（Run `42970318111` 全绿） | 同上，步骤级 |
| `ci.yml` 的 `build-and-test` 是否通过 | **通过**，10 个 step 全 `success`（含 gate step，非 skipped） | 本人直读 `/jobs` 步骤级 |
| 「94 条断言在 CI 中被执行」 | **成立**（gate step 非 skipped + 7 个脚本字节与本地重放同一份 + 本地重放合计 94） | 字节比对 + 本机实跑 |
| 「94 条逐条在日志中可见」 | `NOT_MEASURED` | 日志下载需 admin 权限，本人实测 403 |
| `v16-market-provenance.yml`（V1.6 的 104 条） | **一次都没跑**（分支名不匹配触发规则，F-22-C 实证） | 7 次 run 里无该工作流 |
| 真实行情 | **每次 CI run 4 条真实读取断言，3 次 CI run 均绿**（非交易、非 AI 推理） | job/step 结论本人直读；「4」来自主理人日志摘录，本人无法复核 |
| 真实 AI 推理 / 真实下单 | **0 次**（不是"未测"，是从未发生） | 代码路径 + run 中无相关 job |
| Gate 是否每次都会执行 | **不会**：它排在 unit tests 之后，前面红则整段 skipped（`42968141252` 实证，F-21） | 本人直读步骤级 |
| 可否据此宣称生产就绪 | **否** | 见 §12 |

**一句话：`ci.yml` 这一条线已经用真实远端证据签收（SHA `0d1d6c5`）；但签收面窄——V1.6 溯源门禁没跑、真实 AI 推理 0、真实下单 0、gate 位置脆弱、门禁自身仍有 F-22-G/F-23 未修缺陷。**

### 0.1 本文件与前一版的关系（不抹除历史）

- 前一版（291 行 / `46f5c263…`）写于**推送之前**，当时结论是「无 Run ID / 分支未推送 / `UNVERIFIED`」。**在那个时点上它是真的。**
- 推送到 `f5fea09` 后，主理人在提交 `2b6be4a` 中对本文件**追加了 17 行取代声明**（正文一字未改），并登记为 **DEV-12**。本人复核该提交：`files` 里本文件 `additions=17 / deletions=0`，声明内容与本人收到的指令一致。这是对独占文件的最小介入，动机合理，但它只是**临时告示**。
- 本版（v2）是**作者本人**对该文件的正式替代：吸收取代声明、补齐远端实测、把推送前分析压缩进 §10 并明确标注时点。**§10 保留原结论，不回填、不美化。**

### 0.2 判定口径（本文件全篇遵循）

| 标记 | 含义 |
|---|---|
| `SELF-VERIFIED` | 本人用 GitHub REST 直读 run/job/step/commit 元数据，或本机实跑复现 |
| `DERIVED` | 由已验证事实按确定性逻辑推出，未直接目验（如「gate 绿 ⇒ fetch-depth 生效」） |
| `REPORTED` | 主理人直读日志后提供，本人无法独立复核（日志接口 403） |
| `NOT_MEASURED` | 测不到。**不等于 0，不等于通过** |

> **编号去歧义**：本项目的 `F-22` 被两处同时使用——旧版 CI 报告指「聚合门无断言数下限」，主理人台账指「门禁未覆盖 `v16-market-provenance.yml`」。本文件拆开写：**F-22-G**（Gate，聚合门无下限）、**F-22-C**（Coverage，V1.6 门禁未覆盖）。引用该缺陷时请带上后缀，否则对话两边说的不是同一件事。

---

## 1. 证据源：我读到了什么、读不到什么

**能读（`SELF-VERIFIED`）**——`api.github.com` 的只读端点，无需凭据：

- `GET /repos/yihui315/jev/actions/runs?branch=…` —— run 列表（事件类型、SHA、结论、时间）
- `GET /repos/yihui315/jev/actions/runs/{id}/jobs` —— **job 与 step 级结论，含 `skipped`**
- `GET /repos/yihui315/jev/commits/{sha}` —— 提交内容与改动文件
- `GET /repos/yihui315/jev/pulls/7` 与 `.../check-runs` —— PR 元数据与挂载的 check

**读不到（`NOT_MEASURED`）**：

- `GET /actions/jobs/{id}/logs` → **403 `Must have admin rights to Repository.`**（本人实测）
- 因此**所有断言计数、`LIVE(...)` 行、`built in x.xx s` 只能记为 `REPORTED`**，不得写成"我验证过"。

> 这修正了前一版 §7 的能力判断：**元数据（run/job/step）不是缺口，日志文本才是缺口。** 前一版写「全团队取不到 Actions 读接口」过宽——run/job 可读，只有日志不行。

---

## 2. 运行时间线（7 次 run，本人直读）

| Run | 事件 | SHA | 工作流 | 结论 |
|---|---|---|---|---|
| `42968141252` | push | `fa7740f` | JEV Command Center — Trading Workbench CI | **failure**（修复前基线） |
| `42969534182` | push | `f5fea09` | 同上 | success |
| `42969557367` | pull_request | `f5fea09` | Cloud Staging Docker Runtime Gate | success |
| `42970318111` | push | `0d1d6c5` | 同上（CI） | **success（最终验证）** |
| `42970326463` | pull_request | `0d1d6c5` | Cloud Staging Docker Runtime Gate | success |
| `42970628974` | push | `2b6be4a` | 同上（CI） | `in_progress`（06:12:34Z 观察时） |
| `42970636862` | pull_request | `2b6be4a` | Cloud Staging Docker Runtime Gate | `in_progress`（同上） |

三个必须点明的口径问题：

1. **`42969557367` 不是 `ci.yml` 的通过证据。** 它是 `Cloud Staging Docker Runtime Gate`。把「PR run 成功」当成「CI 四步 + gate 通过」是错的。
2. **`ci.yml` 在本 PR 上根本不会被 PR 事件触发。** 其 `on.pull_request.branches` 只有 `main` / `master`，而 PR #7 的 base 是 `feature/v16-market-provenance-hardening`。7 次 run 里**所有 `ci.yml` 的 run 都是 `event=push`**，这是实证。
3. PR #7 页面能看到 3 个 check，全部来自 **push run 绑定同一 head SHA**，不是 PR 事件产物。因此**"PR 上没有红灯"仍然等于零证据**，签收只能取 push run。

`2b6be4a` 是文档提交（改动 `JEV_FINAL_MASTER_STATUS.json`、本文件、`JEV_V2_FINAL_GAP_REGISTER.md`），它触发的两个 run 在本人观察时**尚未完成**，故不纳入签收；签收锚点仍是 `0d1d6c5`。

---

## 3. 失败基线 `42968141252`：gate 被整段跳过（F-21 实证）

`fa7740f` 是修复前的提交。本人直读步骤级结论：

| step# | 名称 | 结论 |
|---|---|---|
| 4 | Install（`npm ci`） | success |
| 5 | Typecheck（`npx tsc --noEmit`） | success |
| 6 | Build | success |
| 7 | **Unit tests (mock — offline, authoritative contract)** | **failure** |
| 8 | Unit test statistics | success |
| 9 | Archive unit report | success |
| 10 | **P0/P1/P2/P3P4 gap-closure gate** | **skipped** |

两条结论：

- **前一版 §1 的推断被远端证实**：`fa7740f` 推送必然红，原因是 vitest 收集到两个手写 `node <path>` 脚本（`No test suite found`），而 unit step 没有 `continue-on-error`。该推断当时只有本机提交态复现，现在有真实 Run ID。
- **新缺陷 F-21（medium）：gate 的位置本身是风险。** gate 是 step 10，排在 unit tests 之后。unit 一红，gate 直接 `skipped`——**94 条断言一条都没执行，而 job 只呈现"1 个测试步骤失败"**。这意味着：任何前置步骤的偶发失败，都会让"门禁覆盖 94 条"这句话在该次 run 里变成 0；反过来，读报告的人若只看"gate 步骤存在"就以为它在跑，会被误导。
  修法二选一：① 把 gate 拆成独立 job，用 `needs` 显式声明依赖（推荐：失败面清晰）；② gate 前移。**不建议**加 `if: always()`——那会让前置失败时 gate 仍运行，产生"前置红了但 gate 绿"的混合结论。

- **`noGreenBySkipping` 这一条是反向成立的**：跳过不会被误判为绿——job 整体是 failure。这点对签收有利，应予记录。

---

## 4. 绿基线：`42969534182` 与 `42970318111` 步骤级

两次 run 的 `build-and-test` job 步骤结论完全一致（本人直读）：

| step# | 名称 | 结论 |
|---|---|---|
| 4 | Install（`npm ci`，干净安装） | success |
| 5 | Typecheck | success |
| 6 | Build | success |
| 7 | Unit tests (mock — offline) | success |
| 8 | Unit test statistics | success |
| 9 | Archive unit report | success |
| 10 | **P0/P1/P2/P3P4 gap-closure gate** | **success（不是 skipped）** |

同一次 run 的第二个 job `Real Binance market-data probe (non-blocking)` 也 success（step 5 `Live market-data verification (LIVE_MARKET=1)` success），**但按既定口径不得计入通过**：它是 `continue-on-error` 的非阻断探针，绿了不证明主门禁，红了也不阻断。

签名位置：`42970318111` 的 `head_sha` = `0d1d6c5`，`head_branch` = `feature/jev-v2-ops-integration`，`run_attempt=1`。**签收锚点 = `0d1d6c5`。**

`0d1d6c5` 相对 `f5fea09` 只多一个文档提交。本人用 blob 比对（`git rev-parse <sha>:<path>`）确认两个关键配置**逐字节相同**：

```
.github/workflows/ci.yml                      468238f4…  (f5fea09) = 468238f4…  (0d1d6c5)
apps/command_center_3d/vite.config.ts         bb9f9a78…  (f5fea09) = bb9f9a78…  (0d1d6c5)
```

**所以"在 `0d1d6c5` 绿的 CI"检验的正是本人审计过的那份配置**，不是另一份碰巧能过的配置。

---

## 5. 「94 条」到底验证到了哪一层

这是本文件最容易被夸大的地方，必须分层说清。

**已验证（`SELF-VERIFIED` / `DERIVED`）：**

1. gate step（step 10）在 `42969534182` 与 `42970318111` 中结论为 `success` 且**非 `skipped`**（本人直读）。
2. gate 步骤的脚本体是 `set -e` + 逐条 `test -f … || exit 1` + `node <path>`，**任意一条脚本非 0 退出即 step 红**。step 绿 ⇒ **7 个脚本全部 exit 0**。
3. 这 7 个脚本在 `0d1d6c5` 的字节 == 本人工作树字节（工作树无受跟踪 `M` 文件，`git status` 仅 `??` 未跟踪项）。
4. 本人在同一份字节上实跑得到 7 / 24 / 10 / 17 / 12 / 14 / 10 = **94**，0 失败（§10.4）。

⇒ **「CI 确实执行了这 7 个套件、且全部通过」= 已验证。「合计 94 条」= 由确定性脚本 + 字节同一推出（`DERIVED`）。**

**未验证（`NOT_MEASURED` / `REPORTED`）：**

- 「日志里逐条打印了 94 条断言」——主理人给了日志摘录（`7 + 24 + 10 + 17 + 12 + 14 + 10 = 94`），**本人无法读日志复核（403）**，只能记 `REPORTED`，与本人推导值恰好一致。
- vitest 的 `total=220 passed=217 failed=0 skipped=3`——job step 7 绿 + 本人同参本机复跑同为 220/217/3，**数量一致（`DERIVED`）**；日志原文 `REPORTED`。

> 结论写法建议：对外只能说「远端 gate 步骤绿、7 个套件全过、合计 94 条」，**不要说「我在日志里逐条数过 94 条」**——后者我没做，也做不了。

---

## 6. `fetch-depth: 0` 是否生效

- 主理人给的日志证据：gate 日志含 `B1 apparatus: ebc3fcc baseline WITH broken gate DOES warn+continue + approve via simplified fallback`（`REPORTED`）。
- 本人可推的更强证据（`DERIVED`）：`day14-p0-runner.mjs` 的 B1 用例调 `runnerSourceAt("ebc3fcc")` → `git show ebc3fcc:…`，浅克隆下该对象不存在且 `day14-p0-lib.mjs` **抛错而非跳过**。而 gate step 是绿且 24 条全过。**⇒ 该对象在 CI 工作区里存在 ⇒ `fetch-depth: 0` 生效。**
- 边界说明：这条推理依赖「B1 确实执行了」。B1 属于 `day14-p0-runner.mjs` 的 24 条之一，若被跳过则总数会低于 24 而脚本仍 exit 0（脚本不校验下限）——这是 `DERIVED` 而非 `SELF-VERIFIED` 的唯一原因。主理人的日志行恰好补上了这一格，两者互为印证。
- **遗留**：B1 里 `ebc3fcc` 是**硬编码**的（F-17，low）。`fetch-depth: 0` 只消除浅克隆失败模式，不解决硬编码——基线一改，这条用例就指向错误对象。

---

## 7. 真实行情探针：口径修正（推翻旧的「0 真实行情样本」）

**旧口径（前一版 §7 与 PR 描述）：** 全部 `SYNTHETIC_FIXTURE`，0 真实行情样本。
**新事实：** `ci.yml` 的 `real-data-probe` job 在每次 run 里真实访问 Binance，主理人日志摘录为 `LIVE(real Binance): total=4 passed=4 failed=0 skipped=0`，且本人直读该 job 的 step 5 `Live market-data verification (LIVE_MARKET=1)` **结论 success**（3 次 run：`42968141252` / `42969534182` / `42970318111` 均 success）。

修正后的准确表述（**分三层，不可合并**）：

| 维度 | 次数 | 判定 |
|---|---|---|
| 真实行情**读取**（Binance 公共端点，只读） | **4 条断言 × 每次 run，3 次 run 都有，job 全绿** | 发生（job 结论 `SELF-VERIFIED`；「4」为 `REPORTED`） |
| 真实 **AI 推理** | **0** | 从未发生（run 中无相关 job；P2 全为 fixture） |
| 真实**下单 / 真实资金** | **0** | 从未发生（`allowBuySell=false`、`ENABLE_REAL_TRADING=false` 未改） |
| 真实 API 成本 | `realApiCostUsd: null` | `NOT_MEASURED`，不是 0 |

**"有 4 条真实行情"绝不等于"系统接触了真实市场数据链路"。** 它只证明：公共行情端点可达、字段能解析、离线契约在该路径上不崩。它不覆盖：私有 API、鉴权、下单、成交回报、滑点、费率。且该 job 是**非阻断**的——它绿了不能替代主门禁，它红了也不阻断签收。

---

## 8. 覆盖缺口：V1.6 溯源门禁（104 条）在本分支一次都没跑

`v16-market-provenance.yml` 的 `on`（本人从 `0d1d6c5` 取出）：

```
push.branches:        ["feature/v16-market-provenance-hardening", "feature/v16-p0-gap-closure", "feature/v16-**"]
pull_request.branches: ["release/jcc-v1.3-final"]
```

实际推送分支是 `feature/jev-v2-ops-integration`：

- 不匹配 `feature/v16-market-provenance-hardening`；
- 不匹配 `feature/v16-p0-gap-closure`（**名字差一截，这是 DEV-10 的后果**）；
- 不匹配 `feature/v16-**`（通配只吃 `feature/v16-` 前缀）；
- PR base 是 `feature/v16-market-provenance-hardening`，也不是 `release/jcc-v1.3-final`。

**⇒ 该工作流 0 次触发。** 7 次 run 里只有两种工作流（CI 与 Cloud Staging），无第三种，这是硬证据。

后果：本人本地实跑通过的 V1.6 双套件（`equity_collector_v45/tests/v16/run-all.mjs` 的 104 条）**在远端从未被执行**。它们仍是本地证据，`NOT_MEASURED`（远端侧）。

修法（三选一，需拍板）：① 把 `feature/jev-v2-ops-integration` 加入 `push.branches`；② 分支改名为 `feature/v16-p0-gap-closure`（会连带影响 PR #7）；③ 用 `workflow_dispatch` 手动跑一次并留 Run ID。
**注意 F-23 面三**：`check-no-fake-live.mjs` 全文 `grep -c pull_request` = 0，即 `ci.yml` 自己的 PR 触发分支**没有门禁覆盖**——上面这个缺口的成因（PR 不触发）不会有任何脚本报警。

---

## 9. 门禁守门能力：F-22-G / F-23 仍未修

这两项与远端绿无关，**绿了也不会变好**。

### 9.1 F-22-G：聚合门没有断言数下限（medium）

`server/ai/tests/run-all.mjs` 的判定式只比 `failed`：

```
const failed = allChecks.length - passed;
overall: failed === 0 ? "PASS" : "FAIL"
return failed === 0 ? 0 : 1;
```

本人 harness 实测空 suite：`checks=0 failed=0 => overall=PASS exitCode=0`。**0 条断言也 PASS。** 对照 `apps/equity_collector_v45/tests/run-all.mjs` 做对了：缺 `RESULT_JSON` 时 verdict 记 `PASS_NO_RESULT_JSON`，`allPass = every(v => v === "PASS")` → FAIL，且清单静态 8 项不会空真。
修法：补最小断言数下限（如 `allChecks.length < 100 → fail`）。

### 9.2 F-23 面一：Y2 可被整体注释化（medium）

`check-no-fake-live.mjs` 的 `yamlLines()` 只按 `\n` 切分、**不剔注释行**；Y2 把 5 个脚本名**硬编码**，逐条断言"有 `test -f` + 有 `node `"。本人 4 变体实测：

| 变体 | Y2 | 能否抓到 |
|---|---|---|
| 基线（7 条 gate） | PASS | — |
| A：注释掉已登记的 p3-contracts | **PASS** | 抓不到 |
| B2：删掉 Y2 未登记的 2 条新 gate | **PASS** | 抓不到（清单硬编码） |
| C：删掉已登记的 `market-data.test.mjs` | **FAIL** | **抓得到** |
| D：5 条已登记 gate 全注释（实际执行 0 条） | **PASS** | 抓不到 |

最坏情形 D：CI 一条 gate 都不跑，`check-no-fake-live` 仍 `TOTAL=10 exit=0`。
修法：① `yamlLines` 剔除 `^\s*#`；② 清单改为「遍历 gate 步骤内所有 `test -f` → 提取路径 → 与期望集合**全等**比对」。

### 9.3 F-23 面二：Y1 只查"字符串还在不在"

Y1 断言 `l.some(x => /release\/jcc-v1\.3-final/.test(x))`，扫描**整个文件行集合**，不分 `push:` / `pull_request:` 段。5 变体实测：基线 PASS / 追加分支 PASS / 替换为 `main` FAIL / **注释掉该行 PASS** / **挪进 `push.branches` PASS**。
真实语义 = 「禁止该字符串从整个文件消失」，**不是**「PR 目标被锁死」。替换 PR 目标但把字符串挪到别处，Y1 照样绿。

### 9.4 单文件空转

`server/ai/tests/` 的 `t1_schema` … `t9_gateway_routes` 共 9 个文件**无主模块守卫**，单独 `node <file>` = exit 0 + 0 断言。当前 CI 没把它们当独立 gate（假门未兑现），但配合 F-22-G 的"无下限"，"整体空转"仍然可能。

---

## 10. 推送前分析（历史快照，时点 = `fa7740f`，保留原结论）

> 本节是 v1 的实质结论，**反映推送前的真实状态**。抹掉它等于丢掉"我们曾经无法验证"这个事实。所有结论已在 §3 被远端证据证实或取代的，逐条标注。

### 10.1 提交态 vs 工作树（已被 §3 证实）

当时 `vite.config.ts` 与 `ci.yml` 两个 `M` 文件只在工作树、未提交。用 `git show HEAD:` 的提交态复跑 app 目录：

```
EXIT=1  Test Files 2 failed | 22 passed (24)   Tests 217 passed | 3 skipped (220)
```

工作树配置同参复跑：`EXIT=0`，`numTotalTests=220 passed=217 failed=0`。
**教训（已写入值班准则）：任何"CI 会/不会绿"的判断，必须用 `git show HEAD:<path>` 的提交态复现，禁止拿工作树当基准。** 本人当时正是拿工作树当基准，才误判过一次"CI 是绿的"，已当场更正。

### 10.2 70 vs 94（已被 §5 取代）

`fa7740f` 的 `ci.yml` = 148 行 / **5 条** `test -f` / **70** 条断言；`f5fea09` 起 = 155 行 / **7 条** / **94** 条。
本人当时明确写了「70 是静态推导值，不是实跑值」——因为 HEAD 态实跑会重写受跟踪的 `reports/evidence/**`，超出只读授权。
**现状**：远端已实测 7 条全过（§5），这条区分不再影响签收，但记录保留。

### 10.3 构建四件套（工作树，仍有效）

| 命令（`working-directory: apps/command_center_3d`） | exit | 结果 |
|---|---|---|
| `tsc --noEmit` | 0 | 零诊断 |
| `npx vitest run` | 0 | 22 files / 217 passed / 3 skipped |
| `npm run build` | 0 | `built in 6.71s`（CI 侧 `REPORTED` 为 4.67s） |
| `yaml` 真实解析 5 个 workflow | 0 | 5/5 `PARSE OK` |

依赖前提（仍成立）：解析用的 `yaml@2.9.1` **不是直接依赖**，由 `postcss-load-config` 传递引入。若把 Y1/Y2 改成真解析，**必须先 `npm i -D yaml`**，否则工具链一变动守卫脚本会以"模块找不到"崩溃——比弱断言更糟。首选仍是零依赖的文本级修正。

### 10.4 本地套件实跑（`SELF-VERIFIED`，字节同 §5 第 3 点）

| 套件 | exit | 断言 |
|---|---|---|
| `day14-p0-contracts.mjs` | 0 | 7 |
| `day14-p0-runner.mjs` | 0 | 24 |
| `market-data.test.mjs` | 0 | 10 |
| `inference-evidence.test.mjs` | 0 | 17 |
| `p3-contracts.mjs` | 0 | 12 |
| `supervisor.test.mjs` | 0 | 14 |
| `check-no-fake-live.mjs` | 0 | 10 |
| **合计（= CI gate 的 94）** | — | **94 / 0 fail** |

其他：`equity_collector_v45/tests/run-all.mjs` 8/8（95 断言）、`tests/v16/run-all.mjs` 8/8（104 断言）、`server/ai/tests/run-all.mjs` 120/0、`e2e_real_engine.mjs` 37/0。

### 10.5 触发矩阵（已被 §2/§8 实证，结论收紧）

前一版写「PR→基线分支 = 0 个 check」。**更准确的表述**：PR 事件确实不触发任何工作流（`ci.yml` 要 `main`/`master`，`v16` 要 `release/jcc-v1.3-final`），但 PR 页面**会显示 push run 挂到同一 head SHA 的 check**。所以「0 个 check」是 PR 事件层面的真，「页面无红灯」不等于零证据的说法仍成立但理由变了——不是没有 check，而是**那些 check 不是 PR 触发的**。

---

## 11. 未测清单（UNKNOWN ≠ 0）

| 项 | 状态 | 原因 |
|---|---|---|
| Actions **原始日志文本**（断言逐条、`LIVE(` 行、构建耗时） | `NOT_MEASURED` | `GET /actions/jobs/{id}/logs` 返回 403，需 admin |
| 94 条断言的日志逐条目验 | `NOT_MEASURED` | 同上；结论由字节同一 + 本机重放 + step 绿推出 |
| `2b6be4a` 的两次 run 结果 | `NOT_MEASURED` | 观察时 `in_progress` |
| V1.6 溯源门禁（104 条）远端结果 | `NOT_MEASURED` | 工作流未触发（§8），非"失败"也非"通过" |
| 真实 venue 费率 / 滑点 | `NOT_MEASURED` | 代码侧 `venue_verified: null` |
| 真实 AI 推理 | **0 次** | 从未发生，不是未测 |
| 真实下单 / 真实资金 | **0 次** | 从未发生 |
| 真实 API 成本 | `NOT_MEASURED` | `realApiCostUsd: null` |

**已被远端证据升级为已验证的项**（相对 v1）：干净 `npm ci`（step 4 success）、`tsc --noEmit`（step 5）、`npm run build`（step 6）、`vitest run`（step 7）、gate 七套件（step 10）、YAML 被 GitHub 真正接受（工作流真的跑起来了）、`fetch-depth: 0` 生效（§6）。

---

## 12. 对 T04 的裁定

**CI 签收（限 `ci.yml` 的 `build-and-test`）= `VERIFIED`，锚点 SHA `0d1d6c5`，Run `42970318111`。**

不构成签收的部分，必须同时写进 T04：

1. **覆盖面**：签收只覆盖 `ci.yml`。`v16-market-provenance.yml`（V1.6 的 104 条）在本分支 0 次运行（§8）。
2. **脆弱性**：gate 排在 unit 之后，前置红则 94 条整段 skipped（F-21，§3 实证）。
3. **守门人**：F-22-G（聚合门无下限）、F-22-C（V1.6 门禁未覆盖）、F-23（Y2 注释化绕过 / Y1 只查字符串 / `ci.yml` PR 分支零覆盖）均未修。
4. **语义边界**：真实行情 4 次（只读公共端点）≠ 真实交易链路；真实 AI 推理 0；真实下单 0；真实成本 `NOT_MEASURED`。
5. **不可外推**：本文件不证明生产就绪，也不证明 Paper 基线可复现——后者见 `JEV_V2_PAPER_READINESS.md`（已冻结，含 F-18 / F-19）。

**签发前仍需**：把 `2b6be4a` 的 run 结论补齐（或明确以 `0d1d6c5` 为唯一锚点并在 PR 正文写明）；决定 V1.6 门禁的补救路径；对 F-21 拍板修法。

> 本文件不构成签发。签发需要 Run ID + 干净环境 + 覆盖面声明三者齐备。

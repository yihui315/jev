# JEV V2.0 集成签收（T01–T06 收口）

> **本文件是主理人裁定，不是第三方证明。** 所有数字均来自主理人一手实测或 GitHub API / Actions 运行日志直读，不采信任何成员自述。
>
> 生成时点：2026-11-11（UTC+8）｜分支 `feature/jev-v2-ops-integration`｜HEAD `f5fea0943d10a98e8e4629683c320e418a9d0136`

---

## 0. 结论先行

| 裁定项 | 结论 | 一句话理由 |
|---|---|---|
| **Engineering Integration** | **GO WITH CONDITIONS** | 4 个提交已推送，远端 SHA 与本地逐字一致；94 条断言在远端逐套件复现 |
| **CI** | **GO WITH CONDITIONS** | 真实 Run ID 在案，`build-and-test` 全绿；但 V1.6 溯源门禁未在本分支触发 |
| **Security** | **GO WITH CONDITIONS** | 见 `reports/JEV_V2_SECURITY_SIGNOFF.md`；剩余 C2–C6 + C8，R6 仍 UNVERIFIED |
| **Live Market Shadow** | **NO-GO** | 真实时间窗口尚未开始累积，无 7 天连续样本 |
| **Paper Trading** | **NO-GO** | 无 30 天 / 100 笔有效交易样本；P3 策略仍 `INSUFFICIENT_DATA` |
| **30-Day Research** | **NO-GO** | 属下一阶段工作，本轮物理上不可能完成 |
| **Real-Money Trading** | **NO-GO（硬性）** | 未获单独授权，全程未接触真实资金 |

**工程成功 ≠ 策略盈利成功。** 本轮证明的是「系统能按设计跑、门禁能拦、证据可查」，完全没有证明「这个策略能赚钱」——后者需要 P5/P8 的真实时间窗口，本轮一次都没有。

---

## 1. T01 本地提交与文件收口

### 1.1 短 SHA 歧义（已解决）

此前报告中出现的 `5834c3c` **在 git 中不是一个合法对象名**：

```
$ git cat-file -t 5834c3c
fatal: Not a valid object name 5834c3c
```

这是主理人转写时的截断错误（漏掉末位 `7`）。正确的短 SHA 是 **`5834c37`**，完整 SHA `5834c3745843baa33b82e04a746fa8691684b2f6`。已登记为流程偏差 **DEV-09**。

### 1.2 提交历史（4 个，全部本地已提交，已推送）

| # | SHA | 内容 | 规模 |
|---|---|---|---|
| 1 | `5834c37` | P0 收口 + P1–P7 实现骨架 | 70 文件（45 新增 / 25 修改），+9664 / −257 |
| 2 | `c0cad25` | 提交时点冻结台账 `JEV_FINAL_MASTER_STATUS.json` | 1 文件，+488 |
| 3 | `fa7740f` | 四份 §13 交付文档 + DEV-08 登记 | 5 文件 |
| 4 | `f5fea09` | **闭合两项 CI 必红阻断** + 证据归一化视图 | 50 文件（28 新增 / 22 修改），+7197 / −77 |

`ebc3fcc..f5fea09` 累计（`git diff --shortstat` 实测）：**106 文件 / +18344 / −280**。

> 更正记录：初次撰写时误写为「121 文件 / +17148 / −334」，那是把 4 次提交的增量直接相加得到的。同一文件被第 4 提交二次修改时，累计 diff 只算一次净变化，故正确值不同。已按实测更正，登记 **DEV-11**。

**第 4 个提交不是可选项。** `5834c37..fa7740f` 本地全绿但推上去必然红，详见 §3。

### 1.3 工作树状态

- **tracked 文件：clean**（`git status --porcelain | grep -v '^??'` 为空）
- 未跟踪且**刻意不入库**（不写 `.gitignore`，保持可见）：

| 路径 | 规模 | 原因 |
|---|---|---|
| `reports/day14-artifacts/_verify/` | 391 文件 | 本地运行 scratch |
| `reports/day14-artifacts/runs/` | 10 文件 | 同上 |
| `reports/day14-artifacts/active-run.json` | 1 文件 | 同上 |
| `apps/equity_collector_v45/evidence/` | 9 文件 | 每次 `run-all` 覆写，入库会造成提交噪声 |

### 1.4 校验结果

| 检查 | 结果 |
|---|---|
| `git diff --check` | **1 处 trailing whitespace**，在 `reports/JEV_REAL_TRADING_SECURITY.md`（安全审计员独占且已冻结）。**刻意不修**：改它需要重算 sha256 并让多份文档引用失效，代价远大于收益 |
| 秘密扫描（tracked 全量） | 命中项全部为合成夹具：`unit-test-not-a-real-key-abc`、`sk-live-...SENTINELdeadbeef`、`AKIAIOSFODNN7EXAMPLE`（AWS 官方示例）。**无真实凭据** |
| `config/.env.example` | 仅示例，无真实值 |
| 冻结锚点 | 5/5 未漂移（见 §2） |
| 台账 52 条内嵌 sha256 | 逐条比对磁盘，**0 处不符** |

---

## 2. 冻结锚点（提交时点）

| 文件 | sha256 |
|---|---|
| `apps/command_center_3d/scripts/day14-runner.mjs` | `002653fed0d48e3cd20c95a397400d93830192ca87f60a8361a88b3aa3726d4a` |
| `apps/command_center_3d/server/ai/risk-gate.mjs` | `5797ba8cd6308e11c75c53121f151402e872ec1f1551780eb6953d60c992278f` |
| `apps/command_center_3d/server/ai/decision-schema.mjs` | `328b7a9b140069d354e9b06a08d85dca79ed47f3394acb8fd43c031be3184ba1` |
| `apps/command_center_3d/server/engine/_lib/paper-server.mjs` | `f9ac6e142446ef2aa8deb928f2b129eb2d42f4227d1bc22469191087312e4d74` |
| `reports/day14-artifacts/config.freeze.json` | `d4edce64c48bf15ab6803ce9ea08a164404d84642c61065d74550d7983208658` |

第 4 个提交**未触碰以上任何文件**，仅改 `vite.config.ts`、`ci.yml`、证据与报告。

---

## 3. T02 全量构建与测试

### 3.1 本地七套件（主理人一手复跑）

| 套件 | 断言 | exit |
|---|---|---|
| `day14-p0-contracts.mjs` | 7 pass / 0 fail | 0 |
| `day14-p0-runner.mjs` | 24 pass / 0 fail | 0 |
| `market-data.test.mjs` | PASS=10 / FAIL=0 | 0 |
| `inference-evidence.test.mjs` | PASS=17 / FAIL=0 | 0 |
| `p3-contracts.mjs` | PASS=12 / FAIL=0 / TOTAL=12 | 0 |
| `supervisor.test.mjs` | PASS=14 / FAIL=0 | 0 |
| `check-no-fake-live.mjs` | CORE=7 / EXTRA=3 / TOTAL=10 | 0 |
| **合计** | **94** | **全 0** |

另：V1.6 `run-all.mjs` 8/8 PASS；`v16/run-all.mjs` 8/8 PASS，104/104 断言。

### 3.2 两项必红阻断（第 4 提交闭合，均已远端实证）

**阻断 A：vitest 确定性变红。** 两个手写 `node <path>` 断言脚本（`node:assert` + `process.exit`，无 `describe/it`）命中 vitest 默认 glob `**/*.test.mjs`，报 `No test suite found in file`。unit 步骤无 `continue-on-error`，故必红。
修复：`vite.config.ts` 的 `test.exclude`，**展开 `configDefaults.exclude` 再追加**（直接给数组会整体替换默认值，静默失去对 `node_modules`/`dist` 的排除）。
实测：`npx vitest run` → 22 文件 / 217 passed / 3 skipped / exit 0。

> **远端反向验证**：修复前的 `fa7740f` 在 Actions 上 unit 步骤**确实 failure**（Run `42968141252`）。这条诊断不是推测。

**阻断 B：CI gate 只覆盖 5/7 套件。** `supervisor.test.mjs`（14 条）与 `check-no-fake-live.mjs`（10 条）没有任何步骤——号称的 94 条断言里有 **24 条从未进 CI**。
修复：补齐到 gate，保持原纪律（先 `test -f` 硬失败、再 `node`、`set -e`、无 `continue-on-error`）。现为 **7 条 `test -f` + 7 条 node**。

### 3.3 三条硬缺口的最终状态

| 缺口 | 收口前 | 现在 | 依据 |
|---|---|---|---|
| TYPECHECK | UNVERIFIED（本机 `node_modules` 缺失，`npx tsc` 会误装 `tsc@2.0.4` 假包） | **CI VERIFIED** | Run `42969534182` 步骤 `Run npx tsc --noEmit` success，无报错输出 |
| Build | 未执行 | **CI VERIFIED** | 同 Run，`built in 4.67s` |
| vitest | 未执行 | **CI VERIFIED** | `UNIT(mock/offline): total=220 passed=217 failed=0 skipped=3`，与本地逐字一致 |

> 注意：`3 skipped` 是 `LIVE_MARKET` 门控用例，属既有设计，**不得**当作通过。

### 3.4 证据归一化视图

新增 `apps/command_center_3d/scripts/normalize-evidence.mjs`（纯 stdlib）→ `reports/evidence/v16/_normalized/`。

- 21/21 文件归一化成功，**verdict 自检 PASS**（剥离前后所有结论完全一致）
- 8 组跨文件配对 → **8 IDENTICAL / 0 different**
- 剥离 76 处（时间字段 + `node:<PID>`）
- **未为凑哈希删除任何 verdict / check / detail**

一条重要的诚实修正：此前观测到的 2 组「差异」是**陈旧性假象**（`group_*` 与单文件产生于不同批次），不是真实分歧。同一批次内重跑后全部一致。

---

## 4. T03 独立 PR

| 项 | 值 |
|---|---|
| 分支 | `feature/jev-v2-ops-integration` |
| 远端 SHA | `f5fea0943d10a98e8e4629683c320e418a9d0136` |
| 本地 HEAD | `f5fea0943d10a98e8e4629683c320e418a9d0136`（**逐字一致**） |
| PR | **#7**，**Draft**，base `feature/v16-market-provenance-hardening` |
| URL | https://github.com/yihui315/jev/pull/7 |

**过程事故（如实登记）**：集成分支最初建在 `fa7740f`，而第 4 提交落在了 `feature/v16-p0-gap-closure`，导致首次推送只推了 3 个提交，远端一度停在会红的 `fa7740f`。已通过 `git branch -f` 前移并重推修正，远端现为 `f5fea09`。此为 **DEV-10**（主理人自身，medium）。

**未做**：未强推、未自动合并、未改 `master` / `release/jcc-v1.3-final`、未动 PR #6。

---

## 5. T04 远端 CI 真实签收

### 5.1 三次真实运行

| Run ID | 事件 | SHA | 结论 |
|---|---|---|---|
| `42968141252` | push | `fa7740f` | **failure** |
| `42969534182` | push | `f5fea09` | **success** |
| `42969557367` | pull_request（Cloud Staging Docker Runtime Gate） | `f5fea09` | **success** |

### 5.2 逐条核对

| # | 要求 | 结论 | 证据 |
|---|---|---|---|
| 1 | 测试在最新代码上执行 | **VERIFIED** | 两个 success 运行的 `head_sha` 均为 `f5fea09` |
| 2 | `fetch-depth: 0` 生效 | **VERIFIED** | gate 日志含 `B1 apparatus: ebc3fcc baseline WITH broken gate DOES warn+continue + approve via simplified fallback`；该用例必须 `git show ebc3fcc` 才能跑 |
| 3 | 正式 `build-and-test` 成功 | **VERIFIED** | 步骤 5/6/7/10 全 success |
| 4 | 非阻断探针不冒充通过 | **已隔离** | 探针为独立 job `Real Binance market-data probe (non-blocking)`，其 success **不计入**本签收 |
| 5 | 94 条断言可对应 | **VERIFIED** | 见 §5.3 |
| 6 | 无跳过测试获得错误绿 | **VERIFIED** | 修复前 `fa7740f` 的 gate 步骤为 `skipped` 且整体 failure——说明跳过不会被误判为绿 |

### 5.3 断言级对应（从运行日志直读）

```
7 pass, 0 fail                                  day14-p0-contracts
24 pass, 0 fail                                 day14-p0-runner
PASS=10 FAIL=0                                  market-data
PASS=17 FAIL=0                                  inference-evidence
PASS=12 FAIL=0 TOTAL=12                         p3-contracts
PASS=14 FAIL=0                                  supervisor
CORE_ASSERTIONS=7 EXTRA_ASSERTIONS=3 TOTAL=10   check-no-fake-live
```

合计 **94**，与本地逐套件完全一致。

### 5.4 两条新发现（必须记住）

**（一）gate 位置风险。** gate 步骤排在 typecheck/build/unit 之后。`fa7740f` 那次运行里，unit 一挂，gate 直接 `skipped`——94 条断言一条没跑。这不制造假绿（整体已 failure），但意味着**门禁在最需要它的时候最先缺席**。建议将 gate 前移为 job 第一步，本轮未改。

**（二）真实行情不是 0 次。** 探针日志：`LIVE(real Binance): total=4 passed=4 failed=0 skipped=0`。CI 上确有 **4 次对 Binance 的真实网络请求且全部通过**。
因此长期沿用的「0 真实行情样本」口径必须修正为：

| 项 | 修正后 |
|---|---|
| 真实行情读取 | **4 次（CI 侧实测）** |
| 真实 AI 推理 | 0 |
| 真实下单 | 0 |
| 真实 API 计费 | `NOT_MEASURED` |

### 5.5 覆盖缺口

`v16-market-provenance.yml` 的 `on.push.branches` 为 `["feature/v16-market-provenance-hardening", "feature/v16-p0-gap-closure", "feature/v16-**"]`，**不匹配** `feature/jev-v2-ops-integration`。因此 V1.6 溯源门禁（V1.5 baseline 8 组 + V1.6 新套件，104 断言）**未在本分支运行**。一行配置可补，本轮刻意未改（避免再次扩大提交面）。

---

## 6. T05 正式风险与真实运行准备

八条逐条的独立结论见 `reports/JEV_V2_SECURITY_SIGNOFF.md` 与 `reports/JEV_REAL_TRADING_SECURITY.md`。此处只记主理人认为最要紧的一条：

**「风险门降级」已被双重证实，且结论是「已消除」。**

- 历史事实（远端日志原文）：`ebc3fcc baseline WITH broken gate DOES warn+continue + approve via simplified fallback` —— 旧实现确实在正式风控导入失败后 warn 一声继续走简化风控并放行。
- 现实现：`[day14] FATAL: formal risk-gate import failed — fail-closed termination`，直接终止，不产生任何记录与审批。
- 该 FATAL 在 CI 日志里出现两次，均来自 **QA 故意注入的导入失败用例**（`P0-QA injected formal-gate import failure`），不是真实故障。

即：fail-closed 不是「代码里看着像」，而是**在远端被注入失败后实测终止**。

---

## 7. 七项裁定详述

### Engineering Integration = GO WITH CONDITIONS

成立依据：4 提交已推送、远端 SHA 与本地一致、Draft PR 已建、94 断言本地与远端逐套件对齐、5 个冻结锚点零漂移、工作树 tracked clean。

条件：① gate 位置前移；② V1.6 溯源门禁接入本分支；③ F-19 未修（锚只写不读）；④ F-14/F-15 未修，P3 仍锁。

### CI = GO WITH CONDITIONS

成立依据：真实 Run `42969534182` / `42969557367` 在 `f5fea09` 上 success，步骤级与断言级证据齐备。

条件：① 上条两处覆盖缺口；② 本结论仅覆盖 `f5fea09` 这一个提交，不构成对分支未来提交的保证；③ `real-data-probe` 的 success 不进入本裁定。

### Security = GO WITH CONDITIONS

依 `reports/JEV_V2_SECURITY_SIGNOFF.md`。C1/C7 已满足，C2–C6 + C8 仍开放，R6 静态结论保持 `UNVERIFIED`。

### Live Market Shadow = NO-GO

需要 7 天连续的真实行情 + 决策 + 风控 + 完整原始证据。当前真实行情只有 4 次 CI 探针，**连续时间窗口为 0 秒**。

### Paper Trading = NO-GO

需要 30 天 / 100 笔有效交易。P3/P4 当前 `INSUFFICIENT_DATA`，D 臂 `NO_DATA`，`walk-forward` 为 `SYNTHETIC_OOS_DRILL` 且 `oos_pass: 0` / `verified: 0`。**没有任何可签字的策略绩效。**

### 30-Day Research = NO-GO

属阶段 3 工作，本轮不存在所需样本。不得以「工程通过」替代。

### Real-Money Trading = NO-GO（硬性）

`allowBuySell = false`、`ENABLE_REAL_TRADING = false` 均未解除。未创建实盘账户、未注入资金、未提交任何真实订单。解除需要单独授权，本轮不申请。

---

## 8. 本轮的失败项与偏差（不隐藏）

| ID | 级别 | 内容 | 责任 |
|---|---|---|---|
| DEV-09 | low | 报告中写 `5834c3c`，该 SHA 在 git 中不存在（合法短 SHA 为 `5834c37`） | 主理人 |
| DEV-10 | medium | 集成分支建在 `fa7740f` 而第 4 提交落在另一分支，首次推送只推 3 个提交，远端一度停在会红的 SHA | 主理人 |
| DEV-08 | medium | 曾把另一条业务线的 `ReferenceInput.url` 误当作 JEV 安全底线下发，该字段本仓 0 命中 | 主理人 |
| — | — | 首轮 `fa7740f` CI **failure** | 工程事实（已修复并复验） |

**没有任何一次「全绿」是删断言或放宽断言换来的。** 修复前的那次红色运行记录在案，未删。

---

## 9. 下一阶段的入口条件

进入阶段 2（真实行情 Shadow）前必须先完成：

- [ ] gate 步骤前移至 job 首位
- [ ] `v16-market-provenance.yml` 触发分支补 `feature/jev-**`
- [ ] F-19 修复（锚必须被读取，否则「篡改检测」名不副实）
- [ ] F-14 / F-15 修复（P3 解锁硬前置）
- [ ] 独立凭据域（F-05）
- [ ] `check-no-fake-live.mjs` Y2 文案与实际 gate 数量对齐（F-23）

Shadow 本身**不得自动开启持续付费任务**，必须单独授权。

---

*纪律确认：本文件所有数字来自主理人一手实测或 GitHub Actions 运行日志直读；未伪造 Run ID、未宣称生产 GO、未宣称真实资金 GO、未宣称策略盈利；缺失一律记 `NOT_MEASURED`，未用 0 冒充。*

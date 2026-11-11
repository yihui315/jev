# JEV V2.0 — 远端 CI 可签收性评估（独立 QA）

> 作者：independent-qa（结论均来自本人实跑；不采信他人自述，也不采信自己的工作树快照）
> 仓库：`C:/Users/Administrator/WorkBuddy/jev-v16`｜分支 `feature/v16-p0-gap-closure`｜HEAD `fa7740f2`
> 基线 `ebc3fcc`（= `origin/feature/v16-market-provenance-hardening`）
> 实测：Windows 11 / Node v22.22.2｜**本文件不宣称任何 CI PASS、不宣称任何 Run ID、不宣称生产就绪**
> 姊妹文档：`JEV_V2_PAPER_READINESS.md`（Paper 基线证据 / F-18 / F-19 / 入口陷阱）
> 本文件只管一件事：**这份提交推上去，远端 CI 能不能签收。**

---

## 0. 结论速览

| 问题 | 结论 | 等级 |
|---|---|---|
| 提交态（`fa7740f`）上 `vitest run` 会绿吗 | **不会，确定性红**（`2 failed｜22 passed (24)`，exit 1） | 本机实测（提交态复现） |
| 修复在哪 | **只存在于工作树，未提交**（`vite.config.ts` + `ci.yml` 两个 `M` 文件） | `git status` 实证 |
| `tsc --noEmit` | PASS，exit 0，零诊断 | 本机实测 |
| `npm run build` | PASS，exit 0 | 本机实测 |
| YAML 语法 | PASS，5 个 workflow 全部真实解析通过 | 本机实测（`yaml` 包） |
| 门禁覆盖 | 提交态 5 条 / 工作树 7 条（新增 2 条**未提交**） | 本机实测 |
| PR 上会有几个 check | **取决于 base；开向基线分支 = 0 个** | 静态确认 + 真实 YAML 解析 |
| 门禁的"守门人"可靠吗 | **不可靠**：Y2 可被注释化绕过（5 条 gate 全注释仍 PASS，F-23） | 4 变体实测 |
| 远端 CI 真实签收 | `UNVERIFIED`（无 Run ID，分支未推送；且全团队取不到 Actions 原始日志，详见 §7） | — |

**一句话：当前提交推上去，`build-and-test` job 会在 vitest 步骤失败；能救它的补丁还躺在工作树里没提交。**

---

## 1. 决定性事实：提交态与工作树不一致

`git status` 显示两个**已修改未提交**的配置文件：

```
 M .github/workflows/ci.yml
 M apps/command_center_3d/vite.config.ts
```

### 1.1 `vite.config.ts`：`test.exclude` 是新增的，HEAD 里没有

`git diff HEAD` 显示新增了 `import { configDefaults } from "vitest/config"` 与整个 `exclude` 块（排除 `scripts/supervisor/supervisor.test.mjs`、`server/ai/evidence/tests/inference-evidence.test.mjs`）。

直接从 HEAD 取出该文件验证：

```
git show HEAD:apps/command_center_3d/vite.config.ts | grep -c exclude   -> 0
```

**HEAD 版本从未有过 `exclude`。**

### 1.2 用 HEAD 版配置在 CI 的工作目录下复跑 → 红

方法（不改仓库文件：把 HEAD 版配置写到临时文件，用 `--config` 指定）：

```
cd apps/command_center_3d
npx vitest run --config ./_qa_head_vite.config.ts
```

结果：

```
EXIT=1
apps/command_center_3d/scripts/supervisor/supervisor.test.mjs        <- No test suite found
apps/command_center_3d/server/ai/evidence/tests/inference-evidence.test.mjs
 Test Files  2 failed | 22 passed (24)
      Tests  217 passed | 3 skipped (220)
```

### 1.3 用工作树配置跑 → 绿

```
cd apps/command_center_3d
npx vitest run --reporter=json --outputFile=...      # 与 ci.yml 完全同参
EXIT=0
numTotalTestSuites=77  numPassedTestSuites=77  numFailedTestSuites=0
numTotalTests=220  passed=217  failed=0  pending=3   success=true
```

### 1.4 为什么会出现两种结论（含本人自己的一次误判）

- 从**仓库根**跑 `vitest run --root .`：读不到 `apps/command_center_3d/vite.config.ts`，3 个 `.test.mjs` 脚本被默认 glob 误收（第三个是 `apps/equity_collector_v45/tests/market-data.test.mjs`）→ 红。这是**工作目录假象**。
- 从**app 目录**跑但用 **HEAD 配置**：命中真实 CI 语义 → 红（2 个文件）。
- 从**app 目录**跑且用**工作树配置**：绿。

**本人一度据第 3 种情形得出「CI 是绿的」，该结论只对工作树成立、对提交态不成立，现予更正。**
判别准则（建议写入值班规范）：**任何"CI 会/不会绿"的判断，必须用 `git show HEAD:<path>` 的提交态复现，禁止拿工作树当基准。**

### 1.5 对 T03 的直接含义

- 若 T03 只推送现有 3 个提交（`5834c37` / `c0cad25` / `fa7740f`）→ **CI 必红**，且是"测试运行器配置不匹配"的红，不是真缺陷。
- 若 T03 把这两个文件作为**第 4 个提交**推上去 → 红转绿，但这是一次**CI 配置 + 构建配置变更**，需要显式评审记录（不涉及 5 个冻结锚点文件，冻结面不受影响）。
- 两条路都要有人拍板，**不能默认"应该没事"**。

---

## 2. 构建四件套实测（工作树）

| 命令（`working-directory: apps/command_center_3d`） | exit | 结果 |
|---|---|---|
| `./node_modules/.bin/tsc --noEmit` | **0** | 零输出，无任何 TS 诊断 |
| `npx vitest run`（工作树配置） | **0** | `Test Files 22 passed (22)` / `Tests 217 passed | 3 skipped (220)` |
| `npm run build` | **0** | `✓ built in 6.71s`；仅 chunk >900kB 非致命警告 |
| `yaml` 真实解析 `.github/workflows/*.yml` | **0** | 5/5 `PARSE OK`（ci / cloud-staging-runtime / jev-cloudflare-staging / v14-acceptance / v16-market-provenance） |

依赖说明：`apps/command_center_3d/node_modules` 存在（156 项，tsc 5.6.3）；`apps/equity_collector_v45` **无 package.json、无依赖**，其测试为纯 node 脚本。
**注意**：这些依赖是**他人在本工作区安装的，非干净 `npm ci`**，因此仍不能替代 CI 签收。

> **YAML 解析的依赖前提（重要）**：本人用于解析的 `yaml@2.9.1` **不是直接依赖**——`package.json` 的 `dependencies` / `devDependencies` 均无 `yaml`，它是由 `postcss-load-config` 等**传递引入**的（`package-lock.json` 中存在该条目）。因此"把 Y1/Y2 改成真解析"这个建议**必须先执行 `npm i -D yaml`**；否则上游工具链一变动，`yaml` 会静默消失，守卫脚本将以"模块找不到"直接崩溃——**那比弱断言更糟**（容易被人当成偶发故障跳过）。故本报告把它列为**备选方案**，首选仍是零依赖的文本级修正（剔除注释行 + 行首精确锚 + 集合全等比对）。

---

## 3. 门禁覆盖：提交态 5 条，工作树 7 条

### 3.1 现状

`ci.yml` 的 `P0/P1/P2/P3P4 gap-closure gate` 步骤：

| 版本 | 行数 / sha256 | `test -f` 守卫 | `node` 调用 | 断言合计 | 性质 |
|---|---|---|---|---|---|
| **HEAD（提交态）** | 148 行 / `39ce5d0d…` | **5** | **5** | **70** | **静态推导** |
| **工作树（未提交）** | 155 行 | **7** | **7** | **94** | 实跑 |

工作树新增的 2 条正是 `scripts/supervisor/supervisor.test.mjs`（守卫文案 `SUPERVISOR TEST MISSING`）与 `scripts/check-no-fake-live.mjs`（`NO-FAKE-LIVE CHECK MISSING`），`git diff` 显示 `1 file changed, 7 insertions(+)`。

**关于「70」的性质（避免夸大）**：这 5 条 gate 脚本在 HEAD 与工作树**逐字节相同**——本人用 `git show HEAD:<path>` 与工作树文件逐个比对 sha256，5/5 全部 `SAME`，且 `git status` 显示它们均无 `M`。因此它们的自报值（7/24/10/17/12）可直接沿用，合计 70。
但**本人未在 HEAD 态实跑这 5 条**（实跑会重写受跟踪的 `reports/evidence/**`、`reports/day14-artifacts/**`，超出只读授权）。所以 70 是**静态推导值，不是实跑值**。

因此：
- 提交态推送 → CI 只执行 **70** 条（5 条：contracts 7 + runner 24 + market-data 10 + inference-evidence 17 + p3 12）——**该数为推导**；
- 提交这两个文件后 → CI 执行 **94** 条——**该数为本人实跑**（`GATE_EXIT=0`）。

> 谁在对外说"CI 已覆盖 94 条"，必须先确认这两个 `M` 文件已被提交。**在未提交状态下，"94 全进 CI" 不成立。**

### 3.2 `check-no-fake-live.mjs` 的 Y2 是**弱断言**（实测）

工作树已是 7 条 gate，复跑该脚本：`CORE=7 EXTRA=3 TOTAL=10 exit=0`，其中 `Y2` 仍打印 **"ci.yml: 5 gate scripts each test -f hard-fail"**。

读源码确认原因：Y2 里把 5 个脚本名**硬编码**成数组，逐条断言"有 `test -f` 行 + 有 `node ` 行"，**不校验总数、也不校验那 2 条新增 gate 是否存在**。

后果（**本人复现 4 个变体后的准确结论**，非"双向都哑"）：

| 变体 | Y2 结果 | 能否抓到 |
|---|---|---|
| 基线（现状 7 条） | PASS | — |
| A：把已登记的 `p3-contracts` 两条**注释掉**（prefix `# `） | **PASS** ❌ | **抓不到** |
| B2：删掉 Y2 **未登记**的 2 条新 gate | **PASS** ❌ | 抓不到（清单硬编码） |
| C：删掉 Y2 **已登记**的 `market-data.test.mjs` | **FAIL** ✅ | **抓得到** |
| D：把 5 条已登记 gate **全部注释掉**（实际执行 0 条） | **PASS** ❌ | **抓不到** |

结论修正：Y2 **能**抓到「删除已登记项」（C），但抓不到「注释化」（A/D）与「未登记项的增删」（B2）。
根因是 `yamlLines()` 只按 `\n` 切分、**不剔除注释行**，于是 `# node apps/.../p3-contracts.mjs` 这样的注释行依然满足 `includes(s)` + `/node /`。同一文件在 `continue-on-error` 那条断言里**专门排除了注释行**，却在 gate 清单断言上没有做同样处理——不一致。

→ 登记为 **F-23（medium）：门禁的"守门人"本身可被整体注释化废掉，而自身仍报 PASS。** 最坏情形是 D：5 条 gate 全被注释、CI 实际一条都不跑，`check-no-fake-live` 依然 `TOTAL=10 exit=0`。

建议（两条一起做，缺一不可）：① `yamlLines` 返回前剔除 `^\s*#` 行；② 清单不再硬编码，改为「遍历 gate 步骤内所有 `test -f` 行 → 提取脚本路径 → 与期望集合做**全等**比对」。

### 3.3 聚合门没有断言数下限（F-22，medium）

`server/ai/tests/run-all.mjs` 的判定式（源码）：

```
const failed = allChecks.length - passed;
overall: failed === 0 ? "PASS" : "FAIL"
return failed === 0 ? 0 : 1;
```

本人用 harness 实测空 suite：

```
checks=0 failed=0 => overall=PASS exitCode=0
```

即 **0 条断言也输出 `TOTAL checks=0 ... => PASS` 且 exit 0**。一旦某模块 `run()` 被改空、或 exports 改名导致 harness 收集不到 checks，聚合门会静默变绿。

对照：`apps/equity_collector_v45/tests/run-all.mjs` 做对了——缺 `RESULT_JSON` 时 verdict 记为 `PASS_NO_RESULT_JSON`，而 `allPass = every(v => v === "PASS")` → 判 FAIL，且测试清单是静态 8 项不会"空真"。

建议：给 `server/ai/tests/run-all.mjs` 补一个**最小断言数下限**（例如 `if (allChecks.length < 100) fail`），照 V1.5 的写法即可。

### 3.4 单文件空转（V1.6 聚合门内的模块）

`server/ai/tests/` 下 `t1_schema` … `t9_gateway_routes` 共 9 个文件**无主模块守卫**，单独 `node <file>` = exit 0 + 0 断言（输出仅 167 字节 SQLite 警告）。它们只能经 `run-all.mjs` 执行。
（`apps/equity_collector_v45/tests/t*.mjs` 与 `tests/v16/v16_*.mjs` 均有独立入口、能单跑、含 `RESULT_JSON`，是安全的。）

结论：**当前 CI 没有把这 9 个文件当独立 gate**，故假门"未兑现"；但 §3.3 的聚合门下限缺失让"整体空转"仍然可能。

---

### 3.5 Y1 的「PR 目标规则」断言是 `some()` 存在性检查，不是"规则被固化"

`check-no-fake-live.mjs` 的 Y1 有这一句：

```
assert.ok(l.some((x) => /release\/jcc-v1\.3-final/.test(x)), "PR target rule changed");
```

实测该字符串在 `v16-market-provenance.yml` 中**只出现 1 次**（`pull_request.branches: ["release/jcc-v1.3-final"]`）。`some()` 扫描的是**整个文件的行集合**，**不区分该字符串位于 `push:` 还是 `pull_request:` 段**。本人实测 5 个变体：

| 变体 | Y1 |
|---|---|
| 基线（PR 仅 `["release/jcc-v1.3-final"]`） | PASS |
| 追加 `feature/v16-**` 到 PR 目标 | PASS |
| 替换 PR 目标为 `["main"]`（字符串消失） | FAIL |
| 注释掉 PR 分支行（字符串仍在注释里） | **PASS** ❌ |
| 挪段：PR 改 `["main"]`，把 `release/jcc-v1.3-final` 挪进 `push.branches` | **PASS** ❌ |

因此这条断言的真实语义是：**禁止「把该字符串从整个文件里删掉」，而不是「禁止移除 PR 目标」。**

- **新增** PR 目标 → 照过；
- **替换** PR 目标但字符串在别处仍出现（挪进 `push.branches`、或写进注释）→ **照过**，而 PR 规则其实已被破坏；
- 只有让该字符串在整个文件中彻底消失 → 才 FAIL。

也就是说，断言名 `"PR target rule changed"` **名不副实**——它守的是"文件里出现过这个词"。把它理解成「PR 规则被锁死，谁都不能改」是过宽的解读。

> 对 T03 的实际含义：给 `pull_request.branches` 加 `feature/v16-**` **不会**触发 Y1 失败；但如果选择"替换成 `main`"并顺手把 release 名挪去 `push`，**Y1 也照样绿**——这种情况下 Y1 不会替你报警，必须靠人工评审兜住。

另注：`check-no-fake-live.mjs` 全文 `grep -c "pull_request"` = **0**，即 **`ci.yml` 的 PR 触发分支完全没有门禁覆盖**（该脚本只校验了 `v16-market-provenance.yml` 的）。



## 4. 触发矩阵：PR 上可能是 0 个 check

（用 `yaml` 真实解析 `on:` 块，非文本猜测）

| 工作流 | `on.push.branches` | `on.pull_request.branches` |
|---|---|---|
| `ci.yml` | `feat/jev-trading-workbench`, `feat/**`, `feature/**`, `main`, `master` | **`main`, `master`** |
| `v16-market-provenance.yml` | `feature/v16-market-provenance-hardening`, **`feature/v16-p0-gap-closure`**, `feature/v16-**` | **`release/jcc-v1.3-final`** |

远端 heads（`git ls-remote --heads origin`，读权限通）：`master` `2e5ef36` / `release/jcc-v1.3-final` `b8b2e60` / `feature/v16-market-provenance-hardening` `ebc3fcc` / 另有 3 个 `feat/*` 与 `feature/v14-…`、`feature/v15-…`。

推论：
1. **push 本分支 → 两个工作流都会跑**（`feature/**` 与显式分支均命中）。**签收证据只能取 push-event run。**
2. **没有任何单一 PR base 能同时触发两者**：PR→`master` 只跑 ci；PR→`release/jcc-v1.3-final` 只跑 v16；PR→基线 `feature/v16-market-provenance-hardening` **两个都不触发 → PR 页面 0 个 check**。
3. 因此"PR 上没有红灯"**不等于通过**，属于零证据。T04 必须写明以 push run 为准。

---

## 5. 本地套件实测（供 CI 结果对账用）

| 套件 | exit | 断言 | 末尾关键行 |
|---|---|---|---|
| `day14-p0-contracts.mjs` | 0 | 7/0 | `SUMMARY P0-F CONTRACTS ...: 7 pass, 0 fail` |
| `day14-p0-runner.mjs` | 0 | 24/0 | `SUMMARY P0-F RUNNER ...: 24 pass, 0 fail` |
| `market-data.test.mjs` | 0 | 10/0 | `PASS=10 FAIL=0` |
| `inference-evidence.test.mjs` | 0 | 17/0 | `PASS=17 FAIL=0` |
| `p3-contracts.mjs` | 0 | 12/0 | `PASS=12 FAIL=0 TOTAL=12` |
| `supervisor.test.mjs` | 0 | 14/0 | `RESULT PASS=14 FAIL=0` |
| `check-no-fake-live.mjs` | 0 | 10/0 | `CORE=7 EXTRA=3 TOTAL=10 exit=0` |
| `equity_collector_v45/tests/run-all.mjs` | 0 | 8/8（断言 95） | `OVERALL=PASS` |
| `equity_collector_v45/tests/v16/run-all.mjs` | 0 | 8/8（断言 104） | `assertions={"total":104,"passed":104,"failed":0}` |
| `server/ai/tests/run-all.mjs` | 0 | 120/0 | `TOTAL checks=120 passed=120 failed=0` |
| `e2e_real_engine.mjs`（单跑） | 0 | 37/0 | `E2E checks=37 passed=37 failed=0` |

合计七套件 **94 断言、0 失败**。本地全绿；**但因 §1 的提交态差异，本地绿推不出 CI 绿。**

---

## 6. 其他已核实项（详版见 `JEV_V2_PAPER_READINESS.md`）

- **风控不可降级 = VERIFIED**：静态（catch 内只有 throw，12 处 catch 无一能产出 approved）+ 动态（loader 注入 `risk-gate.mjs` 导入失败 → exit 1，**运行目录 0 文件**）。
- **日志链 = 部分 VERIFIED**：内容篡改 / 断链 / 截断可检出；**整本自洽重算不可检出**（anchor 只写不读，F-19 medium）。
- **冻结锚点 5 个全部一致**（`day14-runner` 实际路径在 `apps/command_center_3d/scripts/`）。
- **F-18 = `NOT_REPRODUCIBLE`，Medium**：`latency_ms` 进入整行哈希；35 列逐字段比对仅此 1 列不同；已排除隐藏状态污染。

---

## 7. 未测 / 未验证清单（UNKNOWN ≠ 0）

| 项 | 状态 | 原因 |
|---|---|---|
| 远端 CI 实跑（push 或 PR） | `UNVERIFIED` | 分支未推送，无 Run ID |
| **Actions 原始 job 日志** | **`NOT_MEASURED`（能力缺口）** | 无 `gh`；实测 GitHub MCP 工具集**不含** workflow-runs / job-logs 端点（只有 commit/branch 与 PR check-runs）。**全团队含本人均取不到**，须由人下载日志文件 |
| CI 历史 run 基线 | `NOT_MEASURED` | 同上，无 Actions 读接口 |
| 提交态 `npm ci` 全量安装 | `NOT_MEASURED` | 现有 `node_modules` 为他人就地安装，非干净安装 |
| YAML 语义（GitHub 接受度） | `NOT_MEASURED` | 只做了语法解析，未提交到 GitHub 验证 |
| 真实 venue 费率/滑点 | `NOT_MEASURED` | 代码侧 `venue_verified: null` |

> 能力缺口的后果：T04 的「94 条断言与远端结果可对应」「vitest 收集了几个文件」**全部依赖原始日志行**。取不到时只能记 `NOT_MEASURED`，**不判绿也不判红**，且不得用绿勾 / 截图 / "整体 PASS"总结替代。

---

## 8. 若要 T04 能成立，最小前置

1. **决定**：把 `ci.yml` + `vite.config.ts` 两个改动作为第 4 个提交（或明确接受 CI 在 vitest 步红）。
2. **决定**：PR base 与签收口径（建议以 push-event run 为唯一证据源）。
3. 给 `server/ai/tests/run-all.mjs` 补断言下限（F-22）。
4. 把 `check-no-fake-live.mjs` 的 Y2 从硬编码 5 条改为集合比对，并让 `yamlLines()` 剔除注释行（F-23：否则 5 条 gate 可被整体注释化、CI 一条不跑而 Y2 仍 PASS）。
5. 以上完成前，CI 结论只能是 `CONFIG-CHANGE PENDING / UNVERIFIED`。

> 本文件不构成签发。签发需要真实 Run ID + 干净环境复现。

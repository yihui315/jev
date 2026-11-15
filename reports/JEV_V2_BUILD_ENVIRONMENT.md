# JEV V2.0 — 构建环境与 CI 结构验证报告（DevOps / SRE）

- 仓库：`C:/Users/Administrator/WorkBuddy/jev-v16`
- 分支：`feature/v16-p0-gap-closure`　HEAD：`fa7740f2ab8be973b8407ed68d5e3514e034a2e3`
- 基线：`ebc3fcc`（= `origin/feature/v16-market-provenance-hardening` 的 tip）
- 本机：Windows 11 / Node v22.22.2 / 无 `gh` CLI / 无 Actions runner
- 范围：本报告只写 DevOps 口径的四件事（构建三条硬缺口、YAML 结构核对、`fetch-depth: 0` 必要性、证据归一化视图）
  ＋ 本轮经授权的两处修复。
- 纪律：未 `git add`/`commit`/`checkout`/`stash`/`push`/`restore`；未改被测断言、冻结文件、`package.json`、`package-lock.json`。

> **CI 结论：`CONFIG-CHANGE / UNVERIFIED`。本报告不宣称 CI PASS，不宣称任何 Run ID，不宣称生产就绪。**

---

## 0. 本轮两处修复的结果（先给结论，细节在 §1、§2）

| 修复项 | 前 | 后 | 状态 |
| --- | --- | --- | --- |
| `vite.config.ts` 的 `test.exclude`（排除两个 node 断言脚本） | `vitest run` exit **1**，Test Files 2 failed / 22 passed | 22 passed (22)，217 tests passed / 3 skipped，exit **0** | **已闭合（工作树，待提交）** |
| `ci.yml` gap-closure gate 补齐 2 套件 | 5 guards / 5 node（漏 24 项断言） | **7 guards / 7 node**，实跑 exit 0，断言合计 **94** | **已闭合（工作树，待提交）** |

改动指纹（before → after）：

```
apps/command_center_3d/vite.config.ts   84 行  0f9b6e7388a48b872f5c3838cb438e1ab5cdb6f293f355c95a06f4345ef1b57c
                                     →  98 行  ba041096f2cd5cd7fa241411fecea309b380c95e1dce8bb6fcdf64b61c37b099

.github/workflows/ci.yml               148 行  39ce5d0d7961df209deb8e043a1dc307225f6fc62d6bedcc72304bf26d36a7fc
                                     → 155 行  38ec1fe118abb2419219b927aba2767b762405dda5916708419c75f70feefe8b
```

### 0.1 防误读：`test.exclude` 是本轮新增的，不是仓库原有配置

这一点必须钉死，因为它极易被读成「配置本来就有 exclude，是跑错了目录」。三组独立证据：

1. **HEAD 提交版本来就没有**：
   `git show HEAD:apps/command_center_3d/vite.config.ts` → **84 行**，
   sha256 `0f9b6e7388a48b872f5c3838cb438e1ab5cdb6f293f355c95a06f4345ef1b57c`；
   对该 blob 检索 `exclude` / `configDefaults` / `supervisor.test` → **0 命中**（grep exit 1）。
   这个 84 行 / `0f9b6e73…` 的指纹，team-lead 在授权前独立读到过并写进裁定，可作为交叉印证。
2. **工作区里那 14 行是未暂存改动**：`git diff apps/command_center_3d/vite.config.ts` →
   `1 file changed, 14 insertions(+)`，新增的正是 `import { configDefaults }` 与 `exclude` 块。
   即：任何人现在**打开工作区文件**看到的 exclude，都是本轮改的，不是历史配置。
3. **失败发生在同一工作目录下**，不是 cwd 假象：我的失败运行是
   `cd apps/command_center_3d && npx vitest run`，报错路径也指向
   `.../apps/command_center_3d/scripts/supervisor/supervisor.test.mjs`；
   且在**改文件之前**用 `--exclude` 做因果隔离已经得到 exit 0 —— 说明红的原因是收集，不是目录。

### 0.2 这两处修复**尚未提交**，这是推送前最大风险

```
git status --porcelain
  M .github/workflows/ci.yml
  M apps/command_center_3d/vite.config.ts
git diff --stat
  .github/workflows/ci.yml              |  7 +++++++
  apps/command_center_3d/vite.config.ts | 14 ++++++++++++++
  2 files changed, 21 insertions(+)
```

含义（两种结局，差别巨大）：

| 推送内容 | 远端实际结果 |
| --- | --- |
| 只推原 3 个提交（`5834c37` / `c0cad25` / `fa7740f`） | unit 步骤**红**（2 个文件 No test suite found）；gate 只有 5 条 / **70** 条断言 |
| 追加 1 个提交带上这 2 个文件 | unit 步骤**绿**；gate 7 条 / **94** 条断言 |

即「修好了」目前只在本机工作树成立。是否作为第 4 个提交落盘，属 T03 的推送决策，由 team-lead 定；
我不执行 `git add`/`commit`。

---

## 1. 三条硬缺口的真实状态

### 1.1 陷阱实录：装依赖之前的 `npx tsc` 不是 TypeScript

`node_modules` 缺失时执行 `cd apps/command_center_3d && npx tsc --noEmit`：

```
npm warn exec The following package was not found and will be installed: tsc@2.0.4
npm warn deprecated tsc@2.0.4: Package no longer supported. ...
                This is not the tsc command you are looking for
EXIT=1
```

`npx` 从 registry 抓了一个名叫 `tsc` 的**占位包（2.0.4）**，它不是 TypeScript 编译器。
这段输出既不能当成「有类型错误」，也不能当成「没有类型错误」；把它当类型检查结论就是造假。
下文结论全部来自装上真依赖后的重新执行。

### 1.2 依赖安装

```
cd apps/command_center_3d && npm install --no-audit --no-fund
added 239 packages in 30s        EXIT=0
```

副作用核对：`package-lock.json` 未被改写（不在 `git status` 里）；
`node_modules/`、`dist/` 均被 `apps/command_center_3d/.gitignore` 忽略。
`npm ci --dry-run` 也 exit 0，说明 lock 与 `package.json` 同步，CI 的 `npm ci` 不会因锁文件失配报错。

### 1.3 typecheck = PASS（修复前后均 PASS）

```
cd apps/command_center_3d && npx tsc --noEmit      （无输出）  EXIT=0
npx tsc --version                                  → Version 5.6.3
```

口径（不夸大）：
- `tsconfig.json`：`strict: true`、`skipLibCheck: true`、`noEmit: true`、`include: ["src", "vite.config.ts"]`、`types: ["vite/client"]`
- 覆盖 57 个 `.ts`；`server/**` 是 `.mjs`，**不在 include 内** → 类型检查不覆盖 P0/P1/P2 的 `server/ai` 代码
- `skipLibCheck: true` 意味着第三方 `.d.ts` 内部的类型错误不会被报出（仓库既有配置，本轮不改）
- 修改 `vite.config.ts`（新增 `import { configDefaults } from "vitest/config"` 与 `test.exclude`）后重跑，仍 exit 0

### 1.4 build = PASS

```
cd apps/command_center_3d && npm run build
[prepare-dist] dist/ absent; vite will build into it fresh
[build] file deletion works; esbuild keeps its normal temp-file path
vite v5.4.11 building for production...
[plugin:vite:dynamic-import-vars] src/App.tsx: invalid import "./views/trading/pages/${name}".
  A file extension must be included in the static part of the import.
✓ 1773 modules transformed.
✓ built in 6.44s                  EXIT=0
```

两条 **warning，不是 error**：`dynamic-import-vars` 提示；`react-three-fiber` chunk 922 kB 超 900 kB 阈值。
`dist/` 构建前不存在，构建后生成、被 gitignore。

### 1.5 单元测试：从 FAIL 到 PASS（本轮修复的阻断项）

**修复前**（HEAD `fa7740f` 原样，`npx vitest run`，vitest 2.1.9 与 lock 一致）：

```
 FAIL  scripts/supervisor/supervisor.test.mjs
       Error: No test suite found in file .../supervisor.test.mjs
 FAIL  server/ai/evidence/tests/inference-evidence.test.mjs
       Error: No test suite found in file .../inference-evidence.test.mjs
 Test Files  2 failed | 22 passed (24)
      Tests  217 passed | 3 skipped (220)
EXIT=1
```

根因：这两个文件是**手写 `node <path>` 断言脚本**（`node:assert/strict` + 计数 + `process.exit`，没有 `describe/it`），
文件名命中 vitest 默认收集 glob `**/*.test.mjs`，于是被判成「空测试套件」。
它们由 `5834c37` 引入（`git cat-file -e ebc3fcc:...` 两文件均 **不存在**；`git log --all` 唯一引入提交 `5834c37`）。

因果隔离（证明红是且仅是这两个文件）：加 `--exclude` 排除后 → `22 passed (22)`、`217 tests`、`EXIT=0`。

精确复现 CI 命令 `npx vitest run --reporter=json --outputFile=vitest-unit.json` → **EXIT=1**，
JSON：`success=false`、`numFailedTestSuites=2`、`passed=217`、`failed=0`、`pending=3`。
CI 的 `build-and-test` 里该 step **没有 `continue-on-error`**，所以推送原样提交必然拿到红色 Run。

**修复**（团队裁定后授权，最小侵入）：在 `apps/command_center_3d/vite.config.ts` 已有的 `test:` 段内追加
`exclude`，并**展开 `configDefaults.exclude` 再追加**两个路径——vitest 一旦显式设置 `exclude` 就会整体替换默认值
（`node_modules`、`dist` 等），裸数组会静默失效，所以必须先展开默认项：

```ts
import { configDefaults } from "vitest/config";   // 新增（第 2 行）
...
  test: {
    cache: false,
    fileParallelism: false,
    exclude: [
      ...configDefaults.exclude,                                  // 保留 vitest 默认排除
      "scripts/supervisor/supervisor.test.mjs",
      "server/ai/evidence/tests/inference-evidence.test.mjs",
    ],
  },
```

只加了这两个路径，没有顺手排除别的；两个脚本**未改扩展名**（否则会打断 `ci.yml` gate 与多份文档里的路径引用）。

**修复后实测**：

```
npx tsc --noEmit                     → EXIT=0
npx vitest run
 Test Files  22 passed (22)
      Tests  217 passed | 3 skipped (220)
EXIT=0
```

- 测试文件数 **24 → 22，精确减少 2**，与排除的两个文件一一对应；
- 用例数 **217 passed / 3 skipped 不变**（被排除的两个文件本来就没进 vitest 计数，所以「217」不变是正确的；
  它们的 14 + 17 条断言由 `node` 直跑覆盖，见下）；
- 两个脚本的正确入口仍是 `node`，改后各跑一次：`supervisor.test.mjs` → `RESULT PASS=14 FAIL=0` EXIT=0；
  `inference-evidence.test.mjs` → `PASS=17 FAIL=0` EXIT=0。

---

## 2. CI 结构核对的范围与限度

### 2.1 实际做到的程度：**真实解析，不是字符级核对**

任务书按「本机无 YAML 解析器」下达。装完依赖后，`node_modules` 里出现了 **`yaml@2.9.1`**（vite/vitest 依赖链带入），
因此本轮两个 workflow 走的是**真解析器**，比字符级核对强一档：

```
YAML.parse(".github/workflows/ci.yml")                     → PARSE=OK  顶层键 name,on,permissions,concurrency,jobs
YAML.parse(".github/workflows/v16-market-provenance.yml")   → PARSE=OK  顶层键 name,on,permissions,concurrency,jobs
```

`yaml` v2 是 YAML 1.2：`on:` 保持字符串（不会像 YAML 1.1 的 js-yaml 把 `on` 解析成布尔 `true`），
默认对重复键报错 → PARSE=OK 同时说明**无重复键**。两个文件 TAB 扫描 **0 处**。

### 2.2 解析后的结构断言（遍历对象，不是数缩进）

| job | `continue-on-error` | checkout `fetch-depth` | steps |
| --- | --- | --- | --- |
| `build-and-test` | 无（false） | **0** | 9 |
| `real-data-probe` | **true** | 无（默认 1，未受影响） | 6 |

`v16-market-provenance.yml`：单 job `v16-regression`，无 `continue-on-error`，checkout `fetch-depth: 0`，8 steps。

关键字段归属：**`fetch-depth: 0` 只在 `build-and-test` 的 checkout 上**；`real-data-probe` 的 checkout 没有该字段，
仍按默认浅克隆——这是应该保持的（该 job 只跑联网 live 探针，不需要历史）。
`real-data-probe` 的 `continue-on-error: true` 仍在，且它的统计走独立的 `LIVE(real Binance)` 计数器，
与单元测试计数分离 → 联网失败不会被冒充成通过。

### 2.3 gap-closure gate：7/7 全覆盖（本轮补齐）

补齐前只有 5 条 `node` 调用，漏了 `supervisor.test.mjs`（14 项）与 `check-no-fake-live.mjs`（10 项），
即「94 项断言」里有 **24 项从未进 CI**。按原 step 的写法纪律补齐（先 `test -f` 硬失败、再 `node`、`set -e`、无 `continue-on-error`）：

解析后断言结果：

```
step.name            = P0/P1/P2/P3P4 gap-closure gate (frozen contracts + data tests)
gate.continue-on-error = ABSENT
gate.set-e             = true
guards = 7    node-calls = 7    every-node-guarded = true
  N1 day14-p0-contracts.mjs      N5 p3-contracts.mjs
  N2 day14-p0-runner.mjs         N6 scripts/supervisor/supervisor.test.mjs
  N3 equity market-data.test.mjs N7 scripts/check-no-fake-live.mjs
  N4 inference-evidence.test.mjs
```

按 CI 语义完整执行该 step（提取 `run` 块，`bash -e`，cwd = 仓库根）：

```
SUMMARY P0-F CONTRACTS : 7 pass, 0 fail
SUMMARY P0-F RUNNER    : 24 pass, 0 fail
market-data            : PASS=10 FAIL=0  RESULT=PASS
inference-evidence     : PASS=17 FAIL=0
P3/P4 CONTRACTS        : PASS=12 FAIL=0 TOTAL=12
supervisor             : RESULT PASS=14 FAIL=0
check-no-fake-live     : RESULT CORE_ASSERTIONS=7 EXTRA_ASSERTIONS=3 TOTAL=10 exit=0
GATE_EXIT=0
```

断言合计 **7 + 24 + 10 + 17 + 12 + 14 + 10 = 94**，与台账口径一致。

> **限定词必须带上**：以上 7/7 与 94 条成立的前提是 `ci.yml` 这处改动**被提交**。
> 当前它是工作树未提交改动（`git diff --stat` = `1 file changed, 7 insertions(+)`），
> 远端 HEAD 里仍是 5 条 gate / 70 条断言。对外只能写「**提交后** 94 条全部在 CI 里被执行」，
> 不能写成「远端已在跑 94 条」。同理，`vite.config.ts` 的 `exclude` 未提交前，远端 unit 步骤仍是红的。

**刻意未做的事**：step 名称保持 `P0/P1/P2/P3P4 gap-closure gate ...` 原样不改。
原因：`check-no-fake-live.mjs` 的 Y2 断言是用正则锚定这行 `- name:` 的
（`/-\s*name:\s*P0\/P1\/P2\/P3P4 gap-closure gate/`），改名会直接打破该检查器。
改后复跑：`Y1 PASS / Y2 PASS / CORE=7 EXTRA=3 TOTAL=10 exit=0`。

### 2.4 限度（必须说清）

1. **不是 GitHub Actions 的 schema 校验**：解析成功只证明「是合法 YAML」，不证明「是合法的 workflow」。
   `runs-on` / `uses` / `with` 的语义合法性、表达式 `${{ }}` 的求值、`needs` 之间的 DAG 合法性，
   只有真跑一次才知道 → 归入 `UNVERIFIED`。
2. **`check-no-fake-live.mjs` 的 Y1/Y2 仍是纯文本自检**（它自己的 NOTE 也这么写），不是解析器结果。
   经 independent-qa 读源码后确认（我复核同意，本条据此更正）：Y2 **不只是「文案滞后」，而是一条弱断言**——
   它把 5 个脚本名硬编码成数组逐条断言「有 `test -f`、有 `node `」，**完全不校验总数或集合相等**。
   后果**不是「双向都哑」——这个说法是结论错，本条据此更正**。复刻 Y2 谓词实跑四组：
   ```
   基线（7 条）                        → PASS
   A 注释掉 p3-contracts 两行（不执行）  → PASS   ← 抓不到「注释化」
   B 删掉未登记的两条新 gate             → PASS   ← 抓不到「未登记项增删」
   C 删掉已登记的 market-data.test.mjs  → FAIL   ← 能抓到「删除已登记项」
   D 7 条 gate 全注释（执行 0 条）       → PASS   ← 最坏变体
   ```
   正确表述：**能抓「删除已登记项」，抓不到「注释化」与「未登记项的增删」**。
   根因不是清单硬编码，而是 `yamlLines()` 只按 `\n` 切分、**不剔注释行**，于是
   `# node …/p3-contracts.mjs` 仍满足 `includes` + `/node /`；而同一文件在
   `continue-on-error` 那条断言里**专门排除了注释行**，在 gate 清单上却没做 —— 同文件内标准不一致。
   最坏变体 D 的后果：**把整段 gate 注释掉、CI 真实执行 0 条断言，这条自检仍 `TOTAL=10 exit=0`**。
   因此「CI 跑了 94 条」不能以 `check-no-fake-live` 的 PASS 作为证据，只能用 job 日志里实际执行的 7 段输出证明（见 §5 的日志缺口）。
   建议改成：过滤整行注释 → 只认行首非注释的 `node <path>` → 断言 gate 内 `node` 条数 ≥7 → 用提取出的路径集合与期望集合**相等**比较；
   本轮未改该文件（不在授权范围）。
   当前状态：现行 `ci.yml` 的 7 条 gate 均未被注释、本地实跑 94 条 → **Y2 现在的 PASS 是对的，不是假绿**；这是"守卫的守卫"有洞，不是当下在掩盖红灯。
3. **未实跑项**：`v16-market-provenance.yml` 的整条流水（含 `python3 scripts/v16_independent_verify.py`
   与两个 `run-all.mjs`）。原因是它们会重写受跟踪的 `reports/evidence/**` 产物，超出「只新增」边界 →
   记为 **NOT_MEASURED**，由 QA 侧覆盖。
4. 引用路径 12/12 存在性、`npm ci --dry-run` exit 0 —— 属于「配置能不能真跑起来」的静态前提，已核对。

---

## 3. `fetch-depth: 0` 的必要性证明

### 3.1 完整克隆：能读到基线对象，套件绿

```
git show ebc3fcc:apps/command_center_3d/scripts/day14-runner.mjs     EXIT=0（正常输出）
node apps/command_center_3d/server/ai/tests/day14-p0-runner.mjs
SUMMARY P0-F RUNNER (...): 24 pass, 0 fail                            EXIT=0
```

### 3.2 模拟浅克隆：如预期确定性变红

```
git clone --depth 1 file:///C:/.../jev-v16 <tmp>/repo
  → HEAD=fa7740f, git rev-list --count HEAD = 1, .git/shallow 存在

cd <tmp>/repo
git show ebc3fcc:apps/command_center_3d/scripts/day14-runner.mjs
  → fatal: invalid object name 'ebc3fcc'.                             EXIT=128

node apps/command_center_3d/server/ai/tests/day14-p0-runner.mjs
  [FAIL] B1 apparatus: ebc3fcc baseline WITH broken gate DOES warn+continue + approve
         via simplified fallback (...) :: git show ebc3fcc failed: fatal: invalid object name 'ebc3fcc'.
SUMMARY P0-F RUNNER (...): 23 pass, 1 fail                            EXIT=1
```

失败源不是被测逻辑，而是 `git show` 拿不到对象（`day14-p0-lib.mjs` 的 `runnerSourceAt()` 在 `spawnSync` 失败时
直接 `throw`，不会静默跳过）。→ **`fetch-depth: 0` 是必要修复，已被实测证实，不是臆想。**
临时目录已删除（`/tmp/jev-shallow-*`、`/tmp/jev-cold-*`），仓库内无残留。

### 3.3 可达性与脆弱性（风险提示，非当前故障）

```
git rev-parse origin/feature/v16-market-provenance-hardening  → ebc3fcc...
git rev-list --count ebc3fcc..origin/feature/v16-market-provenance-hardening → 0   # ebc3fcc 就是该远端 tip
git merge-base --is-ancestor ebc3fcc HEAD → YES                                     # 也是当前 HEAD 祖先
```

当前安全。脆弱点：测试把**具体 SHA 硬编码**为夹具，若该远端分支被 rebase / 强推 / 删除，或上游 gc 了不可达对象，
即使 `fetch-depth: 0` 也会红。建议（本轮未改代码）：加一条「夹具对象可达」的前置断言，
把「夹具不可达」与「被测逻辑失败」区分成两种失败信号。

---

## 4. 证据归一化视图：生成规则与残留差异

### 4.1 工具与边界

`apps/command_center_3d/scripts/normalize-evidence.mjs`（425 行，纯 `node:` 标准库，零依赖，`node --check` 通过）。
只读 `reports/evidence/v16/*.json`（顶层 21 个；`cloud/`、`_normalized/` 两个子目录跳过并记录），
只写 `reports/evidence/v16/_normalized/**`，不联网、不 spawn、**原始证据一字未改**。脚本连跑两次产物逐字节一致（幂等）。

**剥离白名单（逐条计数写入 MANIFEST）**：

1. 执行簿记时间戳键：`startedAt` / `finishedAt` / `capturedAt` / `generatedAt` / `completedAt` / `createdAt` / `updatedAt`
2. `stderr` 内嵌进程号：`(node:<PID>)` → `(node:<PID>)` 占位
3. 绝对路径中的用户名 / 盘符：`X:\Users\<name>` → `<USER_HOME>`；其余盘符路径 / UNC → `<ABS_PATH>`
4. `stdout` 里的 `RESULT_JSON:{...}`：解析后按同一规则归一化再序列化回去

**明确保留、绝不剥离**：`verdict` / `overall` / `exitCode` / `checks` / `pass` / `detail`、
所有 `sha256` / `fullHash` / `strippedHash` / `payload_sha256`、以及领域时间戳 `price_ts` / `fetched_at` / `freshness_ms`
（后者是哈希载荷的一部分，剥掉等于销毁证据）。序列化：对象键升序 + 2 空格 + 末尾换行。

### 4.2 运行结果

```
json files scanned : 21     normalized OK : 21     parse failed : 0
skipped dirs       : _normalized, cloud
verdict self-check : PASS（21/21 归一化前后 verdict 一致，0 失配）
compared pairs     : 10 (identical=8 different=2)
stripped occurrences total: 78
```

- 生成文件 **23 个**（21 归一化 + `MANIFEST.json` + `_external/` 1 个外部载荷），全部 `JSON.parse` 通过（23/23）。
- 泄漏自检：`_normalized/**` 中 `(node:<数字>)` 出现 **0** 次、用户名 `Administrator` 出现 **0** 次。
- `V16_T2` 单文件剥离 6 处：`capturedAt` / `result.startedAt` / `result.finishedAt` /
  `stdout.RESULT_JSON.startedAt` / `.finishedAt` / `stderr` 的 `node:PID`。

### 4.3 归一化后是否仍有差异：有，而且是真实差异

- 仓库内 8 对「同 testId、两次运行」（个体证据 × group 证据）：**8/8 IDENTICAL**。
  原因必须说清，否则会被误读成「证据已确定性」：现有所有副本都来自同一执行上下文
  —— `git show HEAD:...V16_T2...` 与工作区版本的 `fullHash` **都是 `af384d33…`**，归一化后自然全等。
- 为验证跨上下文，把 `V16_T2` 放进**全新临时克隆首跑**（产物只写在仓库外），与仓库内证据纳入同一视图比较：

```
[冷上下文首跑] fullHash = 2fa0e3aa94d3b52c092ecd78d76c6ac32154b0a2d565a0bacfb233684ab2fd81
[仓库内]       fullHash = af384d3367d49b5f16f10ac8a77b38ef2a898e6f34d9f368d658650d65c117e2

V16_T2_idempotency_fields.json       vs 冷上下文 → DIFFERENT (5 diffs)
group_v16_t2_idempotency_fields.json vs 冷上下文 → DIFFERENT (5 diffs)
差异路径：checks.3.detail.0 / .1 / .2、checks.5.detail.fullHash、checks.5.detail.strippedHash
        （strippedHash：865f432e… vs 147bf7ec…）
```

结论：
- 时间戳 / PID / 绝对路径共 78 处噪声**已被完全消除**（8/8 同上下文配对全等可证）；
- 但行哈希的**上下文相关性是真实内容差异，归一化无法也不应消除**；
- **没有**为了 MANIFEST 好看而扩大剥离范围，`fullHash` / `strippedHash` 原样留在视图里；
- 根因判定归 QA。**本报告最初写的「冷上下文确定、非随机」是过度断言，已撤回**：
  independent-qa 的逐字段比对（`reports/JEV_V2_PAPER_READINESS.md` §4.2b）把两档哈希对应的整行 35 列全量 dump 后，
  差异字段只有 **1 个：`latency_ms`（A=1 / B=0）**，`snapshot_id` / `session_id` / `created_at` / `fetched_at` /
  `payload_sha256` / `mark_contracts_json` / `network_context` 全部逐字相同，且两次都是全新内存库（不存在 DB 文件状态）。
  → 机制：`latency_ms` 是**时长**（`Date.now() - t0`），不带「时间戳」字样，所以我的剥离规则漏掉了它；
  它被 `hashRow()` 覆盖（整行 canonical JSON 后 sha256）。同进程内 3 次循环同处热态 → 同档 → 那条
  `full_row_hash_identical_across_3_runs` 恒 PASS；跨进程则分档。QA 13 次运行实测为 `2fa / af3 / 2fa`、
  加压下 `2fa0 / af38 / af38 / af38`、12 并发出现 `2fa0 ×8 / af38 ×4` —— **是概率分档，不是确定性**。
  我观测到「两个全新克隆首跑都是 2fa0e3aa」只能说明冷启动更容易落在 1ms 档，不能推出确定性。
- 因此**本视图的可复现口径**应改写为：`fullHash` / `strippedHash` 不可跨进程比对；
  跨进程稳定的替代口径是 `snapHash`（不含 `latency_ms`，QA 实测恒为 `62a52d8c…`）。
  我**没有**把 `latency_ms` 加进剥离白名单 —— 那会把一个真实的执行属性伪装成噪声，
  与「不为了哈希一致而删掉真实信息」的约束冲突；保留差异、标注原因，是正确处置。

复现：

```
node apps/command_center_3d/scripts/normalize-evidence.mjs                        # 纯仓库内视图
node apps/command_center_3d/scripts/normalize-evidence.mjs --extra <外部RESULT_JSON>  # 含跨上下文比较
```

---

## 5. UNKNOWN ≠ 0：没做到 / 未测量清单

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 远端 CI 实跑 / Run ID | **UNVERIFIED** | 无 runner、无 `gh`、提交未推送、凭据不可用 |
| `build-and-test` 的真实结论 | **推断** | 修复后本机 exit 0，但 Actions 上的行为未观测 |
| GitHub Actions schema / 表达式 / DAG 合法性 | **UNVERIFIED** | 无 schema 校验器，需真跑 |
| `v16-market-provenance.yml` 全流程、`v16_independent_verify.py` | **NOT_MEASURED** | 会重写受跟踪产物，超出「只新增」边界 |
| P0–P7 其余套件的独立复核 | 未由我执行 | 我只跑了 CI gate 的 7 个脚本（全 exit 0） |
| 真实资金 / 真实交易所下单 | **未触及** | 全程离线 mock / 冻结引擎 |

---

## 6. 工作区状态与我产生的副产物

- 经授权的 tracked 改动只有 2 个：`apps/command_center_3d/vite.config.ts`、`.github/workflows/ci.yml`（均未 commit）。
- 我新增的未跟踪交付物：`apps/command_center_3d/scripts/normalize-evidence.mjs`、
  `reports/evidence/v16/_normalized/**`、本报告。
- `node_modules/`、`dist/` 被 gitignore 覆盖；复现 CI 命令产生的 `vitest-unit.json` 已移出仓库；
  本次 `vitest run` 生成的 `.vite/`（vitest results 缓存，未被 gitignore 覆盖）已删除。
- 提交时**不要**把 `reports/evidence/**` 的既有改动与本轮改动混在一起判断（那些改动来自各套件自身的证据重写）。

## 7. 一个必须公开的过程事故（同文件双写）

`reports/JEV_V2_CI_VERIFICATION.md` 在 13:03 被我用本报告的前身内容**整体覆盖**（我的 Write 返回 "overwrote"，
当时我误判为新建文件，未先确认该路径归属）。覆盖后该路径是我写的 458 行版本
（sha256 `06c080b4efc7f99042d93245a4f69c3617b35a216a62bfb475dd13d637041d94`），
independent-qa 原有的 188 行版本已丢失，我无法恢复。

**后续（已闭环）**：independent-qa 已于 13:12 重新落盘该路径，作者署名 QA；
本报告**不记录该文件的 sha256 或行数**：它归 independent-qa 维护且已多次更新（本文写作期间就变过 3 次），
任何写死在这里的数字都会过期，且会形成"文件 A 记 B 的 sha"的相互拖累。
**需要锚点时以最终台账为准**（台账集中持有冻结点指纹）；我此后不再写该路径，本报告内容自洽，不依赖该文件。

## 8. 建议（未执行）

1. `check-no-fake-live.mjs` 的 Y2 **能抓「删除已登记项」，抓不到「注释化」与「未登记项增删」**（实测四组见 §2.4；
   此前"双向哑"的说法是结论错，已更正）—— 建议改为「过滤注释行 → 只认行首非注释的 `node <path>` →
   断言条数 ≥7 → 提取集合与期望集合相等比对」（本轮未改该文件）。
2. 为 `ebc3fcc` 夹具加「对象可达」前置断言，区分夹具缺失与逻辑失败。
3. `latency_ms` 不应加入归一化剥离白名单；跨进程可比口径用 `snapHash`。若要长期比对 `fullHash`，
   需要让测试在固定 `latency_ms` 的夹具下重算（属测试侧改动）。
4. 推送并拿到真实 Run ID 后，再把 CI 结论从 `CONFIG-CHANGE / UNVERIFIED` 升级 —— 在那之前不升级。
5. 推送时务必把 `vite.config.ts` 与 `ci.yml` 两处改动带上（见 §0.2）；否则远端仍是「红 + 70 条」。

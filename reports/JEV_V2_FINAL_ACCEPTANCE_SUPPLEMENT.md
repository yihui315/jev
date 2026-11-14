# JEV V2.0 最终验收增补报告（T07）

> **本文件不覆盖任何历史证据。** 它只做一件事：把验收锚点从 `f5fea09` / `0d1d6c5` 前移到远端实际 HEAD `be80d3c`，并记录该提交态在**三个门禁通道**上的真实结论，含**红色项**。
>
> 锚定提交：`be80d3c46ab7410ee1e1da6c4741938e1bcde1f4`
> 锚定 PR：[#7](https://github.com/yihui315/jev/pull/7)（Draft，未合并）
> 出具时间：2026-11-11（CST）
>
> **锚点与当前 tip 的关系（务必区分）**：上文锚点 `be80d3c` 是本报告的**验收范围**，其后代码已继续前进。凡引用**锚点之后**提交的 gate 输出，本文一律在就地标注 SHA（如 §(b) 的 `9e883f3`）；**未标 SHA 的数字默认指锚点态**。引用任何 gate 输出前请先做「per-suite 行之和 = summary 行」加总校验。

---

## 0. 为什么必须另出一份，而不是改旧报告

| 文件 | 冻结锚点 | 处理方式 | 原因 |
|---|---|---|---|
| `reports/JEV_FINAL_MASTER_STATUS.json` | `f5fea09` | **不回改** | 提交时点快照，改了就失去「当时认为什么」的证据价值 |
| `reports/JEV_V2_INTEGRATION_SIGNOFF.md` | `f5fea09` | **不回改** | 同上 |
| `reports/JEV_V2_CI_VERIFICATION.md` | `0d1d6c5` | **不回改** | 其内部结论自陈「签收面窄」，回填会掩盖该自我限定 |
| `reports/JEV_V2_SECURITY_SIGNOFF.md` | 旧集成分支 | **不回改** | 安全复核的分支语境与本次不同 |
| **本文件** | **`be80d3c`** | 新增 | 唯一一份以远端实际 HEAD 为锚的验收记录 |

**一句话：历史报告记录「过去认为什么」，本报告记录「现在实测到什么」。两者并存，不互相覆盖。**

---

## 1. 远端事实（独立核验，非转述）

### 1.1 PR #7

| 项 | 值 | 来源 |
|---|---|---|
| 状态 | `open` / `draft=true` / `merged=false` | GitHub API `pulls/7` |
| 标题 | JEV V2.0 P0-P7 integration candidate (LOCAL PASS - not a production or real-money GO) | 同上 |
| base | `feature/v16-market-provenance-hardening` @ `ebc3fccbf4186b9651bbde2371ef884633d527f0` | 同上 |
| head | `feature/jev-v2-ops-integration` @ `be80d3c46ab7410ee1e1da6c4741938e1bcde1f4` | 同上 |
| 提交数 | 7 | 同上 |
| 变更规模 | 108 files，+19519 / −280 | 同上 |
| ahead / behind | 7 / **0** | `git rev-list --count` |

**关于「落后 0」的机制说明（避免下次误判）**：`git merge-base be80d3c origin/feature/v16-market-provenance-hardening` 的结果是 `ebc3fcc`，即**目标分支当前 head 本身**。目标分支已前移到本分支的分叉点，因此不存在「落后」。这不是巧合，也不是本分支被 rebase 过——是目标分支在 `acc8433 → ebc3fcc` 前进后与分叉点重合。

### 1.2 本轮在 `be80d3c` 上实际触发的三通道

| 通道 | 工作流 | Run ID | 事件 | 结论 |
|---|---|---|---|---|
| A | `ci.yml` | `42971185302` | push | **success** |
| B | `cloud-staging-runtime.yml` | `42971192054` | pull_request | **success** |
| C | `v16-market-provenance.yml` | `43031996748` | workflow_dispatch | **failure** |

> **通道 B 此前未被任何报告记录。** 它是 `be80d3c` 上唯一自动运行的 PR 检查（`cloud-staging-runtime.yml` 的 `pull_request` **无分支过滤**，只有 `paths` 过滤），实测内容为：Docker 构建（pinned frozen engine）→ fail-closed 启动校验 → 端到端健康检查，3 个实质 step 全 success。这是「容器层面能起来」的独立证据，但它**不是**正确性门禁。
>
> **通道 C 是本轮新增信息，也是本报告最重要的一节**——见 §3。

---

## 2. 通道 A：主 CI 逐 step 实证

### 2.1 Run 元数据

| 字段 | 值 |
|---|---|
| run id | `42971185302`（run_number 37，attempt 1） |
| head_sha | `be80d3c46ab7410ee1e1da6c4741938e1bcde1f4` |
| event / path | `push` / `.github/workflows/ci.yml` |
| 起止 | `2026-11-11T06:15:44Z` → `06:16:35Z`（51 s） |
| 结论 | `success` |

### 2.2 job / step 级结论（jobs API 直读）

**job `130359789114` — Typecheck · Build · Unit tests (mock) — success**

| # | step | 结论 |
|---|---|---|
| 2 | `actions/checkout@v4` | success |
| 3 | `actions/setup-node@v4`（node 22） | success |
| 4 | Install（`npm ci`） | success |
| 5 | Typecheck（`npx tsc --noEmit`） | success |
| 6 | Build（`npm run build`） | success |
| 7 | Unit tests（`npx vitest run`） | success |
| 8 | Unit test statistics | success |
| 9 | Archive unit report | success |
| **10** | **`P0/P1/P2/P3P4 gap-closure gate`** | **success** |

> **这张表同时是 F-21 的现场证据**：门禁排在第 10 位，上游 5/6/7 任一失败，第 10 位就变 `skipped`。F-21 不是理论推演。

**job `130360023723` — Real Binance market-data probe (non-blocking) — success**

`needs: build-and-test` + `continue-on-error: true` + `if: ${{ !cancelled() }}`。step 5 `Live market-data verification (LIVE_MARKET=1)` success，实测输出原文：

```
LIVE(real Binance): total=4 passed=4 failed=0 skipped=0
```

### 2.3 94 个**用例块**的逐套件对齐（日志原文，非推算）

> **命名更正（重要，见 §9.3）**：这里冻结的 94 是**具名用例块**（`check(...)` / `suite.check(...)`）的数量，**不是** `assert.*` 语句数。
> 因此「94 条断言通过」这一说法在此前若干轮里被反复使用，**是错的**；它还掩盖了一个真实缺口——用例块内部的断言被删掉，门禁不会发现。
> 块内 `assert.*` 语句数曾记为 **751，该值不可复现，已撤回**：它的分项混用了两种 grep 口径、又混用了两个提交——suite6 取"任意位置"口径、suite7 取"行首"口径、suite2 的 `133` 在两种口径下都取不到，且 suite7 的 `74` 属于 `8da3947` 而不是本节对应的 `be80d3c`。
> 按**单一已声明口径**重算（逐套件、可复跑）：
> - `be80d3c`（本节 94 个用例块所对应的提交）：**730** 条（口径 A＝**排除整行注释后**任意位置 `\bassert\.\w+\(`）／**722** 条（口径 B＝行首 `^\s*assert\.`，**该口径天然免疫注释行**，因为注释行以 `//` 开头）；口径 A 分项 = 43 / 134 / 114 / 165 / 120 / 102 / **52**
> - `8da3947`（Y5 之前的最后一个提交，96 个用例块）：**754** 条（A）／**746** 条（B）；口径 A 分项 = 43 / 134 / 114 / 165 / 120 / 102 / **76**
> **「排除整行注释」是口径的一部分，漏掉它就会多算。** 复现命令：
> `git show <sha>:<path> | grep -vE "^[[:space:]]*(//|#|\*|/\*)" | grep -oE '\bassert\.\w+\(' | wc -l`
> 反例（**由 harbor 指出，我原写法没写这一步，属我的口径定义缺项**）：`8da3947` **不**排除注释行时七套件合计是 **755** 而不是 754；单文件 `check-no-fake-live.mjs` 则是 `8da3947` 76→77、`9e883f3` 79→80（差值恰是被注释掉的那一整行）。`be80d3c` 两种算法同为 730／52，所以它没能暴露这个缺项——**这正是必须写清口径的原因**。
> 引用此类静态计数**必须同时声明口径与 SHA**，否则不可复现。
> **本报告不对 `8da3947` 之后的提交冻结任何静态计数。** 每新增一个守卫都会同时改变用例块总数与 `assert.*` 条数；以 floor 脚本的 `expect` 表（精确、每次运行强制）与 CI 打印的 `GATE_FLOOR_OK suites=N total_cases=M` 行为准。同理，本报告也不复制该 `expect` 表的数值。

| # | 套件 | 用例块 | 日志收尾签名（原文） |
|---|---|---|---|
| 1 | `day14-p0-contracts.mjs` | 7 | `SUMMARY P0-F CONTRACTS (frozen modules, VERIFIED now): 7 pass, 0 fail` |
| 2 | `day14-p0-runner.mjs` | 24 | `SUMMARY P0-F RUNNER (sandbox fault injection + R1-R6 + F-12): 24 pass, 0 fail` |
| 3 | `market-data.test.mjs` | 10 | `PASS=10 FAIL=0` |
| 4 | `inference-evidence.test.mjs` | 17 | `PASS=17 FAIL=0` |
| 5 | `p3-contracts.mjs` | 12 | `PASS=12 FAIL=0 TOTAL=12` |
| 6 | `supervisor.test.mjs` | 14 | `RESULT PASS=14 FAIL=0` |
| 7 | `check-no-fake-live.mjs` | 10 | `RESULT: CORE_ASSERTIONS=7 EXTRA_ASSERTIONS=3 TOTAL=10 exit=0` |
| | **合计** | **94** | — |

单测汇总同源原文：`UNIT(mock/offline): total=220 passed=217 failed=0 skipped=3`。

**取数纪律**：以上一律取「输出签名行」，**不数日志里 `node …mjs` 的文本出现次数**——Actions 会回显 `run:` 块，同一路径在一次日志里恒出现多次，按出现次数统计会系统性高估。本报告 7 个套件均已锚定唯一签名行。

---

## 3. 通道 C：V1.6 补跑 —— 它跑起来了，而且是红的

### 3.1 触发

`workflow_dispatch`，`ref=feature/jev-v2-ops-integration` → Run `43031996748`（`2026-11-11T13:45:23Z`）。

触发前的事实基础：该工作流最后一次运行是 `42839642563`（`ebc3fcc`，pull_request，success）。**`be80d3c` 上从未运行过**——`on.push.branches` 为 `["feature/v16-market-provenance-hardening","feature/v16-p0-gap-closure","feature/v16-**"]`，`on.pull_request.branches` 仅 `["release/jcc-v1.3-final"]`，两条都不覆盖本分支（F-22-C）。

### 3.2 结果：step 4 失败，后续 104 条断言全部 skipped

job `130561140371`：

| # | step | 结论 |
|---|---|---|
| 4 | Independent integrity — frozen engine + frozen DDL + old test byte-identity + new columns | **failure** |
| 5 | Reproduce the V1.5 local test environment | skipped |
| 6 | V1.5 baseline suite（8/8，95 断言） | **skipped** |
| 7 | V1.6 new suites | **skipped** |
| 8 | Track B — AI Mock Contract Tests | **skipped** |
| 9 | Preserve machine-readable evidence | success |

日志原文（`合计: PASS=8 FAIL=1 PENDING=0`）：

```
[PASS   ] 1. V1.5 旧测试逐字节不变（H1）          11/11 个旧测试文件与基线逐字节一致
[PASS   ] 2. 旧断言总数 = 95（H3）                运行时 evidence=95 且静态 R.check(=92 与基线相等
[PASS   ] 3. 冻结引擎零改动（H2）                 15 文件, tree_sha256=aec5d1b7cc26… 与锚点一致
[PASS   ] 4. 冻结权益快照表 DDL 对基线逐字节一致（H2）
[PASS   ] 5. v2 新列与 CHECK/NOT NULL 约束存在（§1.2）
[FAIL   ] 6. V1.6 新增/修改文件敏感串扫描（H7）   扫描 215 个 V1.6 新增/修改文件, 命中 1 处
[PASS   ] 7. src 顶层禁止 Global 主机名（H4）
[PASS   ] 8. 无 console / 无凭据引用 / INSERT OR IGNORE（H6）
[PASS   ] 9. 契约 §4.2 新测试文件齐全（§4.2）     9 个新测试文件齐全
```

### 3.3 根因（本地逐行复现，命中唯一）

| 项 | 值 |
|---|---|
| 文件 | `reports/JEV_REAL_TRADING_SECURITY.md` 第 828 行 |
| 模式 | `github_pat_classic` = `ghp_[A-Za-z0-9]{20,}` |
| 性质 | **文档示例值，不是真实凭据** |
| 命中行本身在说什么 | §11.2.5《全仓密钥模式命中复核 — 全部为合成夹具 / 文档示例值》，该行正是在**说明**这些是格式样本 |

按 `scripts/v16_independent_verify.py` 的 H7 实现（`BASE=bfb3206`，只扫相对基线**新增的行**）逐行复刻，得到 `FILES 215 / HITS 1`，与 CI 完全一致。

### 3.4 这一节真正说明的问题（不要读成「文档笔误」）

三点必须分开说清：

1. **`be80d3c` 的「94/94 绿」与「V1.6 门禁红」是同时成立的。** 前者由 `ci.yml` 通道证明，后者由独立校验通道证明。二者不矛盾，但**只引用前者会得出过度乐观的结论**。
2. **104 条溯源断言在 `be80d3c` 上从未执行过。** 不是「通过了没记录」，也不是「失败了」，是 **skipped**。任何「V1.5/V1.6 在新分支依然成立」的说法，**在 `be80d3c` 这个锚点上**都是 UNVERIFIED。修复后的复跑（§3.5）证明的是 `6a12192`，**不能倒推回 `be80d3c`**。
3. **一个文档里的示例值就能让整条门禁红、让 104 条断言集体缺席。** 这说明门禁的**失效模式偏向「过度敏感」而不是「静默放行」**——方向上是安全的（fail-closed），但代价是：只要有人在报告里引用一次全格式 token 样本，门禁就不再产出任何覆盖率。这是**门禁可用性缺陷**，与 F-22-G 的「无断言下限」是同一族问题的两端。

---

### 3.5 修复后的复跑：同一条门禁，两个事件都绿

修复提交 `6a12192` 推送后，`v16-market-provenance.yml` 因为这个分支第一次被**自动**触发（F-22-C 修复生效），并且 **push 与 pull_request 两个事件都跑通**：

| 事件 | Run ID | 结论 | 说明 |
|---|---|---|---|
| push | `43034811896` | **success** | 自动触发（无需 workflow_dispatch） |
| pull_request | `43034822227` | **success** | PR #7 页面上首次出现 v16 check |

job `130570738375` 逐 step：4 Independent integrity → **success**；5 Reproduce → success；6 V1.5 baseline → **success**；7 V1.6 new suites → **success**；8 Track B → **success**。

日志原文（这三行此前从未在该分支出现过）：

```
[PASS   ] 6. V1.6 新增/修改文件敏感串扫描（H7）
扫描 216 个 V1.6 新增/修改文件, 命中 0 处
合计: PASS=9  FAIL=0  PENDING=0

V1.5_BASELINE_OK groups=8 assertions=95
V16_SUITE_OK {"total":8,"pass":8,"fail":0}
V16_AI_CONTRACT_OK {"suites":10,"checks":120,"passed":120,"failed":0} network_calls=0
```

> **关于「104 条」这个数字的处理（含一次自我更正）**：本报告初稿写「104 不出现在任何日志行，故不沿用」，并据此暗示该数字不可核验。**这个判断是错的**：
> `reports/evidence/v16/summary.json` 明写 `assertions: {total: 104, passed: 104, failed: 0}`。104 只是不出现在 **stdout**，它一直是机器可读的。
> 本轮已把它冻结进流水线（`assertions.total!==104` 即硬失败），并同时保留 stdout 侧的四条签名：H 9/9、V1.5 **8 组 / 95 断言**、V1.6 套件 **8 组 / 8 pass**、Track B **10 套件 / 120 检查 / 120 通过**。
> 教训：**「我在日志里没看到」不等于「不存在」**——把检索范围当成证据范围，是同一类错误的另一种形式。

---

### 3.6 门禁转绿之后的新问题：V1.6 是「单次绿」，不是「稳绿」

`6a12192` 的绿是**一次**观测。继续追查后发现 Track B 里有一个**结构性抖动**，它不改变本次运行的结果，但改变了「绿」这个字的含义。

**失败样本（本机实跑，24 次中 3 次失败）**

```
[FAIL] scenario scSellAfterBuy :: no mock decision matched action=SELL for BTCUSDT
[FAIL] real engine exercised in every order/tick scenario :: engine market path not exercised in: scSellAfterBuy
E2E checks=35 passed=33 failed=2 => FAIL
```

**根因链（源码逐行）**

| 步 | 位置 | 内容 |
|---|---|---|
| 1 | `e2e_real_engine.mjs:152` | `priceTs = priceTs \|\| new Date().toISOString()` → **每次运行取当前毫秒时间** |
| 2 | `mock-provider.mjs:45` | 种子 = `sha256([strategyId, symbol, marketContextId, priceTs, price, seedExtra])` → **含 priceTs**，故每次运行种子不同 |
| 3 | `mock-provider.mjs:67` | `targetNotional = 20 + (byte % 60)` → BUY 名义额随机落在 **[20,79]** |
| 4 | `e2e_real_engine.mjs:281` | SELL 用 `maxQty = posQty * 0.8` → SELL 名义额上限 = `0.8 × buyNotional` |
| 5 | — | 当 `buyNotional < 25` 时 `0.8 × buyNotional < 20`，而 mock 生成的**每一个** SELL 数量名义额都 `≥ 20` → `maxQty` 过滤把 3000 个种子全部滤掉 → `throw` |

**概率（此处修正一次先前口径错误）**

初判写作 `P = 5/60 = 8.33%`，把 `byte % 60` 当成了均匀分布。**这是错的**：`pick()` 返回 0–255 的字节，`256 = 4×60 + 16`，因此 `b ∈ {0..15}` 有 5 个原像、`b ∈ {16..59}` 有 4 个。失败集是 `b ∈ {0,1,2,3,4}`：

```
preimages per b : min 4 max 5
P(fail) uniform60 : 8.3333 %
P(fail) true(%60) : 9.7656 % = 25/256
```

**真值 9.7656%**。实证 3/24 = 12.5%，与 9.77% 同量级（24 次期望 2.34 次，观测 3 次，不构成反证）。

**边界余量（比概率更值得警惕）**

```
b=4: buyNotional=24 maxQty=0.000320000000 minSellQty=0.000333330000 margin=-1.333e-05
b=5: buyNotional=25 maxQty=0.000333336000 minSellQty=0.000333330000 margin=+6.000e-09
b=6: buyNotional=26 maxQty=0.000346664000 minSellQty=0.000333330000 margin=+1.333e-05
```

`b=5` 的通过余量只有 **6.0e-9**——`quantityForPrice` 的 8 位小数取整差一点就会翻面。也就是说这个场景不是「大部分时候能过」，而是**紧贴着悬崖边过**。

**定性**：这是**夹具缺陷，不是产品缺陷**。场景自己构造的数量把自己压到了交易所最小名义额之下。但它足以推翻「V1.6 门禁稳绿」这一说法：

> `6a12192` 的 V1.6 PASS 是**单次观测**。按 9.77% 的独立失败概率，连续 5 次全绿的概率约 59.8%，连续 10 次约 35.7%。**不能把一次绿当作「已稳定通过」**，也不能据此声明 V1.6 门禁可以作为长期放行依据。

本条登记为 **F-24（medium，本轮新发现）**。修复方向：夹具给定固定 `priceTs` 使种子可复现 + 给 BUY 施加数量下界（使 `0.8×` 后仍显著高于最小名义额），**断言一条不改**。

→ **已按此方向修复并验证**：见 §5.3（5 次决策 ID 完全相同 + 12 连绿 + 断言仍为 37）。

---

## 4. 三处口径纠正（相对此前报告与转述）

| # | 旧口径 | 实测 | 影响 |
|---|---|---|---|
| C-1 | 「PR 页面 0 个 check」 | PR 上有 1 类检查：`Cloud Staging Docker Runtime Gate`（本分支 4 次 pull_request 全 success）。**但** `ci.yml` 与 `v16-market-provenance.yml` 均不在其中 | 结论方向不变（正确性门禁确实不在 PR 上），但「0 个」不准确，且漏掉了一条真实存在的容器级证据 |
| C-2 | 「V1.6 的 104 条测试没跑，可以手动补跑」 | 补跑了，**红了**，且失败点在 104 条之前（H7），导致 104 条仍未执行 | 「补跑就能补齐覆盖」这一假设不成立 |
| C-3 | 「Binance 公开行情探针 4/4 PASS」 | `LIVE(real Binance): total=4 passed=4` 的 4 是 **4 个网络请求**，不是 4 条真实行情样本 | 探针 ≠ 数据集。真实行情样本计数仍为 `realMarketSamplesUsed=0`。非阻塞 job（`continue-on-error: true`）不计入主门禁 |

---

## 5. 本轮修复（同一 PR 内追加提交）

| ID | 问题 | 修复 | 状态 |
|---|---|---|---|
| **H7-阻断** | 安全报告引用全格式 `ghp_` 样本 → V1.6 整条门禁红、104 断言 skipped | 脱敏为 `ghp_ABCD…6789` + 保留「36 位构成」描述与可核验位置 | 已修（本地复扫 `HITS 0`） |
| **F-21** | gate 排第 10 位，上游失败即 skipped | gate 前移为 job 首个 `run` step | 见 §5.1 |
| **F-22-C / C14** | 两个工作流都不覆盖本分支 / 本 PR | 补 `feature/**`（ci PR）、`feature/jev-**`（v16 push）、`feature/v16-**`（v16 PR），只加不减 | 见 §5.1 |
| **F-23** | Y2 文案写 5 个 gate（实数 7），且注释化可绕过 | Y1/Y2 改为剔注释 + 行首锚定 + 路径集合相等 | 见 §5.1 |
| **C12 / F-22-G** | 聚合门只看退出码，无断言数下限 | 增加最小断言数下限 | 见 §5.1 |
| **F-24** | Track B `scSellAfterBuy` 夹具抖动（9.77%） | 固定 `priceTs` + `nowMs`，并给 BUY 加数量下界（≥40 USD） | 见 §5.3 |
| **F-25** | Track B 同样只看 `overall/failed`，无形状下限 | 冻结 `suites===10 && checks===120`；V1.6 套件冻结 `total===8` | 见 §5.3 |

### 5.1 修复后的验证

#### (a) 用例块总数守恒（但 `assert.*` 语句数**没有**被冻结）

| 套件 | 修复前（`be80d3c`） | 修复后（`8da3947`） | 日志签名 |
|---|---|---|---|
| `day14-p0-contracts.mjs` | 7 | **7** | `SUMMARY P0-F CONTRACTS …: 7 pass, 0 fail` |
| `day14-p0-runner.mjs` | 24 | **24** | `SUMMARY P0-F RUNNER …: 24 pass, 0 fail` |
| `market-data.test.mjs` | 10 | **10** | `PASS=10 FAIL=0` |
| `inference-evidence.test.mjs` | 17 | **17** | `PASS=17 FAIL=0` |
| `p3-contracts.mjs` | 12 | **12** | `PASS=12 FAIL=0 TOTAL=12` |
| `supervisor.test.mjs` | 14 | **14** | `RESULT PASS=14 FAIL=0` |
| `check-no-fake-live.mjs` | 10 | **12**（7 CORE + 5 EXTRA；Y3/Y4 从 Y1 拆出） | `RESULT: CORE_CASES=7 EXTRA_CASES=5 TOTAL_CASES=12 exit=0` |
| **合计** | **94** | **96** | — |

> **本表两列各绑定一个 SHA，不可与 §(b) 的 97 混读**：「修复前 94」对应 `be80d3c`，「修复后 96」对应 `8da3947`（即 Y5 加入之前）。`9e883f3` 及之后 `check-no-fake-live.mjs` 为 13 个用例块、下限合计为 **97**（见 §(b) 的真实单次捕获）。三者分属三个提交；引用本表任何数字都必须同时写出所属 SHA。

`check-no-fake-live.mjs` 的 A1–A8 / Y1 / Y2 十条用例（该单文件的 `assert.*` 条数：`be80d3c` **52**（B 口径 50）、`8da3947` **76**（74）、`9e883f3` **79**（77）——**三个 SHA 三个值，且 `74` 是 `8da3947` 的 B 口径值，不是 `be80d3c` 的值**；此前本行把 `74` 锚到 `be80d3c` 是错的，已改。口径定义与七套件全量分项见 §2.3）**一条未删、一条未放宽**；Y1 由「文件里存在某字符串」升级为「在 `pull_request.branches` 数组内存在」；Y2 由「5 个脚本各含 test -f」升级为「7 脚本路径集合与声明集合**相等**」。

#### (b) 下限机制可用性（真实日志，单次运行，`13882c4`）

用 7 个套件的真实输出 tee 到日志目录后运行下限脚本。以下为 `13882c4` 上**一次完整捕获的原文，未拼接**。选 `13882c4` 而不写「当前 HEAD」，是因为本文件在本轮被反复修订，任何「当前」措辞会立刻过期；`13882c4` 是一个不可变的锚。该提交只改本文件、未动任何脚本（`git show --stat 13882c4` 可当场核），准确条件是「**不改变用例块数量与各套件计数的提交**」，而不是「只改文档的提交」——后者太窄。反例即 `34325e7`：它**改了脚本**（`check-no-fake-live.mjs` 在 Y1 内加了一条断言），文件 sha256 随之改变，但输出未变，本捕获仍然有效。该提交树上复跑实测：`RESULT: CORE_CASES=7 EXTRA_CASES=6 TOTAL_CASES=13 exit=0`、`GATE_FLOOR_OK suites=7 total_cases=97 exit=0`，与 §(b) 引用块逐字相同。所以判据是**计数与输出**，不是提交性质——只看「是否只改文档」会得出「34325e7 之后捕获失效」的错误结论。

```
GATE_FLOOR ok 1-p0-contracts expected=7 actual=7 — server/ai/tests/day14-p0-contracts.mjs (P0-F CONTRACTS)
GATE_FLOOR ok 2-p0-runner expected=24 actual=24 — server/ai/tests/day14-p0-runner.mjs (P0-F RUNNER)
GATE_FLOOR ok 3-market-data expected=10 actual=10 — equity_collector_v45/tests/market-data.test.mjs
GATE_FLOOR ok 4-inference-evidence expected=17 actual=17 — server/ai/evidence/tests/inference-evidence.test.mjs
GATE_FLOOR ok 5-p3-contracts expected=12 actual=12 — scripts/day14-research/p3-contracts.mjs
GATE_FLOOR ok 6-supervisor expected=14 actual=14 — scripts/supervisor/supervisor.test.mjs
GATE_FLOOR ok 7-no-fake-live expected=13 actual=13 — scripts/check-no-fake-live.mjs (static P7 + YAML self-checks)

GATE_FLOOR_OK suites=7 total_cases=97 exit=0
```

`7+24+10+17+12+14+13 = 97`，与末行 `total_cases=97` 一致。**「per-suite 行之和 = summary 行」就是判断这是否为单次运行的判据**，引用任何下限日志都应先做这一步加总。

把第 4 个套件的 `PASS=17` 改成 `PASS=16`（模拟「退出码 0 但悄悄少一条」），仍在同一份日志上：

```
GATE_FLOOR_VIOLATION 4-inference-evidence expected=17 actual=16

GATE_FLOOR_FAILED suites_checked=7 violations=1 total_cases_seen=96
```

退出码 1；`97−1=96`，同样自洽。

两点补充（都能当场复算，不要只看末行）：① 违规行与 `GATE_FLOOR_FAILED` 走 **stderr**，stdout 上只少了第 4 个套件那一行 `GATE_FLOOR ok`——所以「stdout 的 ok 行数」在违规场景下会少 1，不能拿它当总块数；② `total_cases_seen` **仍计入**违规套件（`7+24+10+**16**+12+14+13 = 96`），它是本次运行的**实际总块数**，不是「通过数」。把 96 读成「通过了 96 条」会高估 1。

> **本节曾有一处错误，已更正（与 `751` 同源：跨口径 / 跨提交拼接）**：原块中 per-suite 行来自 `7-no-fake-live expected=10`（该口径下合计 **94**）的旧捕获，而末行被写成 `total_cases=96`——**94 与 96 并列，算术上不可能来自同一次运行**；违规块则使用已退役字段名 `total_assertions_seen=93`（现脚本输出 `total_cases_seen`，R19 正名时改名）。现已替换为 `13882c4` 的单次捕获，并附上「per-suite 之和 = summary」自校验，使同类拼接可被读者当场发现。

#### (c) 门禁自身的抗绕过（主理人独立复现，未采信成员自证）

以下 4 组变体均由主理人在 `feature/jev-v2-ops-integration` 工作树上手工构造，跑完立即用备份还原并校验 `md5` 一致。

| # | 构造的绕过/回归 | 期望 | 实测 |
|---|---|---|---|
| T1 | 把 `node …/supervisor.test.mjs` 那一行注释掉 | 红 | `AssertionError: ci.yml gate suite set drifted … missing(1): ["apps/command_center_3d/scripts/supervisor/supervisor.test.mjs"]`，exit 1 |
| T2 | `ci.yml` 的 `pull_request.branches` 回退为 `["main","master"]` | 红 | `AssertionError: F-22-C: ci.yml pull_request.branches must include feature/** …`，exit 1 |
| T3 | 把 `Install` 步骤抢到 gate 之前（F-21 回归） | 红 | `AssertionError: F-21: the gate must run BEFORE \`npm ci\` (Install) …`，exit 1 |
| T4 | **用例块数** 17 → 16（不是「断言数」，R19） | 红 | `GATE_FLOOR_VIOLATION 4-inference-evidence expected=17 actual=16`，exit 1 |

**对照组**：还原后重跑，`RESULT: CORE_CASES=7 EXTRA_CASES=5 TOTAL_CASES=12 exit=0`、`GATE_FLOOR_OK suites=7 total_cases=96 exit=0`（此二数对应 `8da3947`，即 Y5 加入之前；`9e883f3` 及之后为 `TOTAL_CASES=13`、`total_cases=97`，见 §(b)）。

> **这里有一次我自己的失误，写下来**：第一次提交下限时，脚本产出从 10 个用例变成了 12 个（Y1 的 Track-B 守卫被拆成独立的 Y3/Y4），而我的 `expect` 仍是 10、验证用的日志还是拆分前的旧日志——于是**本地全绿、CI 必红**。这与 F-26 要防的「陈旧证据为不存在的运行背书」是同一类错误，只是发生在我自己的验证流程里。修正后 `total_cases=96`。

> **这组测试针对的正是旧 Y2 的核心缺陷**：修复前，即使 `ci.yml` 已被大改（gate 从第 10 步前移到第 1 步、新增下限步骤），旧 Y2 仍然输出 `[PASS][EXTRA] Y2 ci.yml: 5 gate scripts …` 且 `TOTAL=10 exit=0`。也就是说，旧断言对它所声明的属性**没有鉴别力**。修复后同样的改动会被抓住。

#### (d) F-21 前置方案的安全性前提（实测，非推断）

gate 前置到 `npm ci` 之前，唯一成立条件是「7 个套件都不依赖 `node_modules`」。实测：在 `apps/command_center_3d/node_modules` **不存在**的本机上逐个执行 7 个套件，全部 `exit 0`，且签名与上表逐一吻合。`ci.yml` 已把这条前提写进注释并要求「若某套件日后引入 node_modules 依赖，必须重新证明顺序而不是沿用假设」。

#### (e) 本轮改动文件

| 文件 | 性质 | 说明 |
|---|---|---|
| `.github/workflows/ci.yml` | 修改 | gate 前移为首个 `run` step；`pull_request.branches` 增 `feature/**`；每套件 tee 到 `${{ runner.temp }}/jcc-gate`；新增独立的「断言数下限」step |
| `.github/workflows/v16-market-provenance.yml` | 修改 | `push.branches` 增 `feature/jev-**`；`pull_request.branches` 增 `feature/v16-**`（保留 `release/jcc-v1.3-final`）；只加不减 |
| `apps/command_center_3d/scripts/check-no-fake-live.mjs` | 修改 | Y1 改为按 trigger 块解析；Y2 改为剔注释 + 行首锚定 + 路径集合相等 + F-21 顺序守卫；**用例块数随 Y3/Y4/Y5 拆出而递增**（原 10 → 本提交值见 floor `expect` 表），内部 `assert.*` 条数**未冻结** |
| `apps/command_center_3d/scripts/gate-assertion-floor.mjs` | **新增** | 声明式**用例块**下限（7 套件，**精确 `expect` 表；本文件不复制其数值**，以避免重演「陈旧硬数字」缺陷），纯 `node:` 标准库 |
| `reports/JEV_REAL_TRADING_SECURITY.md` | 修改 1 行 | H7 阻断脱敏 |
| `reports/JEV_V2_FINAL_ACCEPTANCE_SUPPLEMENT.md` | **新增** | 本文件 |

**未引入任何新依赖**（无 yaml / yargs / chalk / js-yaml）。日志写入 `${{ runner.temp }}`，不落在工作区，因此 `.gitignore` 未改动，也不存在日志被误提交的路径。

### 5.2 被否决的替代方案（记录下来，避免下次重新讨论）

H7 阻断有一个成本更低的「修法」：给 `v16_independent_verify.py` 的白名单加一条 `*.md` 豁免，让文档永不参与敏感串扫描。**已否决。**

理由：H7 唯一真正想防的是「凭据被写进文档并推送出去」，而文档恰恰是最常见的泄漏载体。把 `*.md` 整体豁免，等于关掉这条防线最该覆盖的区域，且会让「0 命中」这个结果失去含义——它只会说明「没扫」。当前做法（改文档、不改扫描器）虽然多改一行字，但保住了扫描器对文档类文件的约束力。

同一逻辑适用于 F-23：没有选择「把 Y2 的 5 改成 7」，因为那只是让文案追上现实，检查本身仍然没有鉴别力；选择的是让检查从「含某字符串」升级为「集合相等」。

### 5.3 F-24 / F-25：把「单次绿」变成「可复现绿」

**修法（只动夹具，不动断言）**

| 改动 | 位置 | 作用 |
|---|---|---|
| 固定 `priceTs = "2026-01-01T00:00:00.000Z"` | `e2e_real_engine.mjs` `scSellAfterBuy` | 种子不再含墙钟时间 → 场景可复现 |
| 同步固定 `nowMs = Date.parse("...00:00:00.500Z")` | 同上 | **必要配套**：风控会拒绝过期上下文（`risk-gate.mjs:126-133`），只固定 `priceTs` 会把「抓不到 SELL」换成「`RISK_CONTEXT_STALE`」 |
| 给 BUY 加 `minQty = 40 / 60000` | 同上 | 使 `0.8 × buyNotional ≥ 32 USD`，对 20 USD 下限留出 12 USD 余量，远离 6.0e-9 的悬崖 |
| 冻结 `suites===10 && checks===120` | `v16-market-provenance.yml` Track B step | 与 C12 同类：防止「少跑一半检查仍然绿」 |
| 冻结 `total===8` | 同上，V1.6 套件 step | 防止套件组数悄悄缩水 |

`nowMs` 不是新造的后门——它是 `createDecision` 既有入参（`runtime.mjs:237/248/326`），风控仍以 `nowMs` 正常执行新鲜度校验，只是换了夹具时钟。**断言一条未改，也未加任何重试循环。**

**验证（主理人独立执行，未采信成员自证）**

| 项 | 结果 |
|---|---|
| 连续运行 12 次 | `pass=12 fail=0`，每次均 `E2E checks=37 passed=37 failed=0` |
| 断言条数守恒 | 仍为 **37**，与修复前 CI 基线一致 |
| **确定性**（关键） | 连续 5 次运行的 `scSellAfterBuy` 决策 ID **完全相同**：`dec-d3f3daaafde9158b3cda51f6f09a926d`。修复前该 ID 每次变化（因种子含 `priceTs`） |
| 累计 | 17/17 全绿 |

> 为什么「12 连绿」本身不够：若抖动仍在，12 连绿的概率约 `0.902^12 ≈ 28.9%`，不能证伪。真正的判据是**决策 ID 逐次相同**——种子一旦固定，随机性在结构上就消失了，这才是「可复现」的定义。

**F-25 的两处下限同时补在 `v16-market-provenance.yml`**：这是同一个「绿 ≠ 真跑了」缺陷在第二个工作流里的同构表现。C12 只修了 `ci.yml`，本轮把另一条线补齐。

### 5.4 本轮**刻意不做**的一件事

修完 F-24 后，仓库里被跟踪的 `reports/evidence/v16_ai/*.json` 会因为本地跑测试而被重写（`generated_utc`、随机 `orderId`、浮点权益都会变）。**这三个都不提交**：它们是不可复现的运行噪声，正是 F-18 所说的「墙钟派生值进入证据」的具体表现。

正确做法应是：要么不把运行产物纳入版本控制，要么像 `_normalized/` 那样归一化后再入库。本轮两样都没做，只保持工作树干净并在此登记——免得下一次有人以为「evidence 文件没更新 = 测试没跑」。

### 5.5 R22 / Y5：V1.6 的同一个 fail-open，以及「冻结 104」为什么挡不住它

Track B 的 fail-open（F-26）在 V1.6 步骤里**一字不差地存在**：`reports/evidence/v16/summary.json` 同样被 git 跟踪。

**两侧都实测了（不是推断）**

| 侧 | 操作 | 结果 |
|---|---|---|
| 失败侧 | 去掉 `rm -f`，把 `node tests/v16/run-all.mjs` 换成 no-op（`true`），其余验证逻辑原样执行 | 打印 `V16_SUITE_OK {"total":8,"pass":8,"fail":0} assertions={"total":104,"passed":104,"failed":0}`，**exit 0** |
| 修复侧 | 保留 `rm -f`，同样 no-op | `require()` 报 `MODULE_NOT_FOUND`，**exit 1** |

> **这一条推翻了我自己上一轮的一个判断。** 我在 `d3b4ee5` 里冻结了 `assertions.total === 104`，当时认为这足以防住「少跑」。但失败侧的实测显示：**陈旧文件里的 `assertions.total` 恰好就是 104**，冻结它反而让陈旧证据更容易通过。真正起作用的只有 `rm -f`——顺序是判据，数字不是。这与 F-26 是同一条教训。

新增的 Y5 就是把这条不变式钉死（`check-no-fake-live.mjs`）。**它的可证伪性也验了**：删掉 `.github/workflows/v16-market-provenance.yml:94` 的 `rm -f` 后，Y5 报
`R22: the V1.6 evidence file is no longer deleted before the runner runs … (verified fail-open, same as F-26)`，exit 1。
同时 Y3 从「只看 Track B 的 3 个致命守卫」扩为「看全部 8 个 `V1.5_*` / `V16_*` 守卫」；把 `V16_ASSERTION_COUNT_CHANGED` 那行的 `process.exit(1)` 删掉后，Y3 精确报出该行，exit 1。**两条变异都命中了对应用例，不是被别的断言顺手挡下。**

### 5.6 这条不变式的**正确**表述（防止后来者加 16 行无效防护）

有人会想把它推广成「凡被跟踪的 evidence 都必须 `rm -f`」。**这个推广是错的**，按字面执行会新增十几行纯噪音，反而淹没真正的判据。

正确的判据是：**被读回来用于认证的 evidence ⇒ 必须 `rm -f`**。

依据（`tests/v16/run-all.mjs`）：`results` 只在内存里累积（`:29` 声明、`:55` `push`），green 与 counts 都只看内存（`:64` / `:73`），`writeFileSync` 分别写分组文件（`:54`）与 `summary.json`（`:84`），全文**没有任何 `readFileSync`**（第 9 行只 import 了 `writeFileSync, mkdirSync`）。因此整个 V1.6 目录里被 `require()` 回读认证的只有 **2 个**：

| 被回读的文件 | 位置 | `rm -f` |
|---|---|---|
| `reports/evidence/v16/summary.json` | 工作流 `:98` | `:94` ✅ |
| `reports/evidence/v16_ai/v16_ai_contract_tests.json` | 工作流 `:135` | `:131` ✅ |

**当前 2/2 满足，无需新增任何 `rm -f`。** 其余约 16 个文件是纯留痕。

### 5.7 两处**已知未修**的盲区（不修就别说自己已覆盖）

| 盲区 | 具体表现 | 为什么不影响本轮裁定 |
|---|---|---|
| **H7 的自我排除** | `scripts/v16_independent_verify.py` 的敏感串扫描把 `scripts/v16_*` 自身和 `.md` 报告一并纳入扫描域，同时又内置排除规则；改排除规则即可让 H7 对自己视而不见 | 本轮 H7 的**运行结果**是真实的（`6a12192` 命中 0）；它是「未来可被绕过」，不是「本轮的 0 是假的」。登记为待修 |
| **用例块内部的断言** | floor 冻结的是用例块数量，块内 `assert.*` 被删不会被发现（R19） | 已在 §9.4 明确标注「不成立（未修）」，未据此声明任何覆盖 |

---

## 6. 修正后的运营裁定

| 维度 | 裁定 | 依据 | 与旧裁定差异 |
|---|---|---|---|
| 工程集成 | **GO WITH CONDITIONS** | PR #7 draft、7+2 提交、ahead 9/behind 0 | 提交数由 7 增至 9，条件不变 |
| 主 CI（`ci.yml`） | **PASS** | Run `42971185302`（`be80d3c`）+ Run `43034812162`（`6a12192`，gate 已成为首步） | 证据锚点前移；F-21 由「已知缺陷」变为「已修且被 CI 观测」 |
| 容器运行时 | **PASS** | Run `42971192054` / `43034822375` | 新增维度 |
| V1.6 溯源门禁 | **PASS（单次观测）／非稳绿** | Run `43034811896`(push) / `43034822227`(PR)：H 9/9、V1.5 95 断言、V1.6 8/8、Track B 120/120。但 Track B 存在 **F-24：9.77% 结构性失败率**（§3.6） | 由 FAIL 转 PASS，但**只能读成「这一次绿」**。`be80d3c` 仍为 FAIL，历史结论不回改 |
| 安全审计 | **GO WITH CONDITIONS** | 见 `JEV_V2_SECURITY_SIGNOFF.md`（锚点仍为旧分支，未回改） | 不变 |
| 真实 AI 自动决策 | **NO-GO** | 真实推理 0 次、真实成本 NOT_MEASURED、真实决策记录 0 条 | 不变 |
| Live Market Shadow | **NO-GO** | F-19 / F-18 / F-14 / F-15 / C9 / F-05 未清 | 不变 |
| 30 天 Paper Trading | **NO-GO** | 无 30 天有效样本、无 100 笔模拟交易 | 不变 |
| 真实资金运营 | **NO-GO** | 上述全部前置未满足 | 不变 |

**没有一档裁定因为门禁变绿而上调。** 本轮修好的五件事全部属于「门禁比它声明的更弱」，修好它们只让「门禁通过」这句话重新有意义，不产生任何一条关于盈利能力的证据。

### 6.1 本轮修好之后，仍然不足以进入 Shadow 的清单

这五项与门禁强弱无关，是**语义层面的缺口**，必须逐条关闭：

| ID | 仍缺什么 | 会让什么结论失效 |
|---|---|---|
| **F-19** | 外部锚只在写、从不读 | 「日志链不可篡改」→ 实际只有链内自洽。攻击者重算整本账本 + 内部锚即可通过 |
| **F-18** | `latency_ms` 仍在行哈希覆盖内 | 跨批次哈希比对必然假阳性，本地 vs CI 无法互校 |
| **F-14** | `LEGACY_FREEZE_EXPECTED` 是死配置 | 「风控上限 = 200」错误，实际生效 `maxNotionalUsd = 1000` |
| **F-15** | `s.riskBreach` 恒 false | 「发生过几次风控拦截」无法统计 |
| **C9 / F-16** | 未绑定真实出网证据 | P3 一旦解锁 `allowBuySell`，正式门可能用 fixture 价格放行真实下单 |
| **F-05** | 无独立凭据域 | 无法保证模型调用故障不牵连交易侧凭据 |

F-24（夹具抖动）与 F-25（Track B 无形状下限）**已在本轮修掉**（§5.3），不在上表。它们被修掉后，本清单从 7 项减为 6 项——**但没有任何一项裁定因此上调**，因为剩下 6 项全是语义缺口，与门禁强弱无关。

> 注意 F-14 与 F-15 的性质：它们不是「没做」，而是**做了但没接线**。这类缺口比缺失更危险，因为读数的人会以为字段是真的。

---

## 7. 本报告不主张什么（显式负面清单）

- 不主张 V1.6 的溯源断言已在 `be80d3c` 通过——该提交上它们是 **skipped / UNVERIFIED**；PASS 属于 `6a12192`（§3.5）。
- 不主张「104 条」是一个可从日志读出的数字——可读出的是 9/9、95、8/8、120/120 四条签名。
- 不主张 `chainHeadSha256` 提供了对外不可否认性——F-19 未修，当前只有**链内自洽性**。
- 不主张证据哈希跨批次可比——F-18 未修，`latency_ms` 仍在哈希覆盖范围内。
- 不主张真实行情数据集已建立——`realMarketSamplesUsed = 0`。
- 不主张风控参数为「200 / 6 / 0.2」——F-14 未修，实际生效 `maxNotionalUsd = 1000`。
- 不主张 `riskBreach` 可用于统计风控拦截次数——F-15 未修，该字段恒 `false`。
- 不主张 AI 已接入运行链路——真实推理 0 次、真实成本 `NOT_MEASURED`。
- 不主张修好五条门禁缺陷等于「审计完整」——门禁只是重新变得可信，可信的门禁绿 ≠ 系统安全。

---

## 8. 附录：`6a12192` 的运行台账（同一提交，5 个 run 全绿）

| Run ID | 工作流 | 事件 | 结论 | 作用 |
|---|---|---|---|---|
| `43034811896` | `v16-market-provenance.yml` | push | success | **首次自动触发**，H7 + V1.5/V1.6 + Track B 全跑 |
| `43034812162` | `ci.yml` | push | success | gate 为首步，`GATE_FLOOR_OK suites=7 total_assertions=94`（**原文照引**：该 run 时字段名为 `total_assertions`、口径为用例块；R19 正名后现为 `total_cases`，数值亦随 Y3/Y4/Y5 增至 97。勿把此行的 94 当作当前值） |
| `43034822227` | `v16-market-provenance.yml` | pull_request | success | PR #7 页面首次出现该 check（C14 生效） |
| `43034822261` | `ci.yml` | pull_request | success | PR #7 页面首次出现主 CI check（F-22-C 生效） |
| `43034822375` | `cloud-staging-runtime.yml` | pull_request | success | 容器构建 + fail-closed 启动 |

对照 `be80d3c`（修复前）：同分支只有 3 个通道，其中 V1.6 为 `failure`、PR 上无任何正确性 check。

**提交序列（PR #7 现为 10 个提交）**

| # | SHA | 内容 |
|---|---|---|
| 1–7 | `5834c37` … `be80d3c` | 原有 P0–P7 收口（不变） |
| 8 | `c4db677` | `fix(sec)`：H7 阻断脱敏（Aegis） |
| 9 | `6a12192` | `fix(ci)`：F-21 / F-22-C / C14 / F-23 / C12（Harbor） |
| 10 | `4eeb354` | `docs(v16)`：T07 增补（主理人） |
| 11 | `2b50ac6` | `fix(test)`：F-24 确定性夹具 + F-25 形状下限（Harbor 产出 + 主理人整合） |
| 12 | 本文件所在提交 | `docs(v16)`：增补报告补 F-24 / F-25 与 §9 方法学 |

> **`6a12192` 的 5 个绿 run 是「F-21 等五项已修」的证据，不是「V1.6 稳绿」的证据。** 后者直到 `2b50ac6` 才成立，且其成立依据是**确定性**（决策 ID 逐次相同），不是「多跑几次都过」。

---

## 9. 方法与可核验性（这一节是为了防止本报告自己被误用）

### 9.1 提交署名不可用于判断归属（provenance 缺陷）

本轮的 3 个提交（`c4db677` / `6a12192` / `4eeb354`）在 git 里**全部署名 `Harbor Falsifier`**，因为所有工作都在**同一个 worktree**、共用同一份 `git config user.name`。

因此：

- `c4db677` 的内容是 Aegis 写的，`6a12192` 是 Harbor 写的，`4eeb354` 是主理人写的——**但 git 元数据无法区分**。
- 任何「按 author 字段统计谁改了什么」的做法，在本仓库当前配置下都是**无效**的。
- 这本身是一个需要修的门禁级缺陷：**四个角色的身份在提交历史里不可区分**。修复方向是为每个角色配置独立的 `user.name`/`user.email`（或使用 `Co-authored-by` 尾注），而不是继续靠报告自述。

**在修复之前，本报告中所有「谁做的」信息只能取自正文自述，不能取自 git。**

### 9.2 「当前 tip 有 CI 证据」是一个会自我否证的命题

本文件每修改一次就会产生一个新提交、移动一次 tip。所以：

- 任何写进文件的 Run ID，**必然指向本文件之前的某个提交**，绝不可能是本文件自己所在的那个提交。
- 因此「tip 是否有 CI 证据」这个问题，**不能用文件自述来回答**，只能用 PR #7 的 checks 列表回答。
- 本报告的 CI 结论一律锚定**代码承载提交 `6a12192`**（这是唯一一次同时改动两个工作流与检查脚本、且跑过 5 个 run 的提交）。之后的纯文档提交（`4eeb354` 及更后）应由 PR checks 页面验证，不由本文件自称。

### 9.3 本轮口径修正台账（写下来，避免下一轮重新争论）

| # | 曾经的说法 | 修正后 | 性质 |
|---|---|---|---|
| 1 | F-24 失败率 `5/60 = 8.33%` | **`25/256 = 9.77%`**（`byte % 60` 有偏，`b∈{0..15}` 有 5 个原像） | 主理人计算错误，已被成员指出并复算确认 |
| 2 | 「V1.6 的 104 条断言」 | 日志可读出的是 **9/9、95、8/8、120/120** 四条签名；`104` 不出现在任何日志行 | 口径不可核验 |
| 3 | 「PR 页面 0 个 check」 | PR 上有 1 类检查（Docker 冒烟），**但正确性门禁不在其中** | 方向对、表述错 |
| 4 | 「Y2 检查 5 个 gate scripts」 | 实际 7 个；且旧 Y2 对被检查文件大改后仍然 `TOTAL=10 exit=0` | 检查无鉴别力 |
| 5 | `b=5` 视为「通过」 | 通过余量仅 **6.0e-9**，紧贴悬崖 | 稳定性不等于正确性 |
| 6 | 「94 条断言」 | **94 个用例块**（`be80d3c`）；块内 `assert.*` 语句数口径依赖（`be80d3c` 730/722，`8da3947` 754/746，见 §2.3）。下限冻结的是前者 | 主理人措辞错误，由成员指出并实测确认。已把输出字段改名为 `*_CASES` / `TOTAL_CASES`；曾写的 `751` 不可复现，已撤回 |
| 7 | 「104 不在任何日志里，口径不可核验」 | 104 确实不在 stdout，但**在 `reports/evidence/v16/summary.json` 的 `assertions.total` 里**，完全可核验 | 主理人以「日志」偷换了「证据」，并据此否定了正确数字 |

### 9.4 本报告的证据等级

| 结论 | 等级 | 依据 |
|---|---|---|
| `be80d3c` 主 CI 逐 step + 94 个用例块 | **VERIFIED** | GitHub jobs API + 日志逐行 |
| 94 个用例块背后的 `assert.*` 条数（口径 A/B：`be80d3c` 730/722，`8da3947` 754/746） | **VERIFIED（本机实测，口径 + SHA 已声明）** | 逐文件正则计数；原 `751` 不可复现，已撤回（见 §2.3） |
| 「用例块内部删断言会被门禁抓住」 | **不成立（未修）** | 下限只冻结用例块数量 |
| `be80d3c` V1.6 红（H7 命中 1/215） | **VERIFIED** | Run 日志 + 本机算法复刻 |
| `6a12192` 五 run 全绿 | **VERIFIED** | GitHub runs API |
| `6a12192` V1.6 各项数字 | **VERIFIED** | 日志原文签名 |
| F-24 概率 9.77% | **VERIFIED（源码级）** | 逐行推导 + 分布复算 + 实跑 3/24 |
| F-24 修复有效性（`scSellAfterBuy` 确定性） | **VERIFIED（本机 40/40 + 哈希绑定）** | 修复已入库 `2b50ac6`；`e2e_real_engine.mjs` sha256 `442bf00c…`，连跑 40 次全绿、`E2E checks=37` 恒定、`run-all 120/120`；Aegis 独立评审确认无 fail-open 并给出 GO。失败概率 `25/256 = 9.77%` 且修复为结构性消除（非概率降低） |
| 任何关于盈利/策略有效性的结论 | **不存在** | 本轮未产生任何相关证据 |

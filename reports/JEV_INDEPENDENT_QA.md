# JEV_INDEPENDENT_QA.md — P0-F 独立验收（故障注入 / 反例 / 全量链 / 重放 / 独立对账）

> Owner: **independent-qa**（P0-F，团队 `jev-v16-p0-gap-closure`）
> 范围：仅 QA。**不修改任何实现代码**，不替 Security / Architecture 签字，不宣布生产 GO。
> 纪律：零自证绿、零伪造、禁止删除断言过关、不触网 / 不调付费模型 / 不下单 / 不 git push / 不碰 main·Release。
> 本文件为**最终版**：在 F1 修复版 / 主理人裁定的唯一权威锚点 `002653fed…` 上全量重跑后重写，并据主理人「C1 版本锚点裁定」更新（C1 关闭、QA DECISION 升级为 GO）。首轮 `24 PASS / 1 FAIL` 与 F1 关闭过程作为历史完整保留，非「一直全绿」。
> 本轮再据主理人「C1 权威锚点 · Task #9 正式启动」裁定追加更新：C1 关闭、**QA DECISION 统一为 `GO WITH CONDITIONS`（剩余条件仅 C2 与 C3）**、`829 行 / 39606 B` 口径更正、追加可逐字引用的 F-12 书面确认段（§6A）。历次 DECISION 演变在 §9 留痕，非「一直全绿」；未删任何原文、条件文字或断言。

---

## 0. 结论先行（TL;DR）

- **本轮最终版真跑 31 项检查，全部 PASS / 0 FAIL / 0 BLOCKED**（契约文件 7/7，沙箱文件 24/24；两文件 exit code 均为 0）。
- 上一轮的**唯一 FAIL = F1**（「解析失败/超时不得改写成 HOLD」）已由 trading-engineer 修复为 `NO_ACTION`；我在**最终版上独立重跑 T6b-strict**，断言保持（不是删掉），实测 `decisionClass=NO_ACTION` → **PASS**。F1 关闭。
- 应 team-lead / security-auditor 追加要求，显式补齐 **R1–R6 精确断言**、**合法 BUY 全量 context 链路**、**双口径成本模型**、**`riskCheckImpl:null` 注入点交叉验证**、**F-12 外部调用计数**。
- **F-12（QA 侧独立计数）：本轮编写+运行测试全程，真实外部 HTTP / 付费模型 / 订单 / 生产 DB 调用 = 0**。依据见 §6（静态扫描 + 禁网守卫 + 33 个子进程 stderr 动态计数 = 0）。此为实现方书面声明之外的**第二条独立证据**，不可互相替代。
- 所有硬安全属性（fail-closed、无简化门放行、链全量校验、重启续接、累计预算、独立对账、冻结、红线、无网）均以反例/故障注入实证通过，不是文件存在性、`qa.pass` 或日志条数。
- **证伪能力证明**：ebc3fcc 基线对照（B1）+ 变异测试（M3）证明「移除保护则对应断言必 FAIL」，测试有牙、非橡皮图章。
- QA 范围裁决：**GO WITH CONDITIONS（仅限 QA 范围）** —— 本轮按主理人「C1 权威锚点」裁定统一口径：**C1 已关闭**，**剩余条件仅 C2（`day14-runner.mjs` 不得再改动）与 C3（信息项）**，详见 §9。此前一轮 QA 曾依当时「C1/C2/C3 均已满足」授权给过 `GO（无条件）`，本轮主理人把 C2 重新界定为持续性剩余条件，故取**更保守**的 `GO WITH CONDITIONS`；DECISION 演变全程留痕（§9），非「一直全绿」。GO 不解除红线、不等于生产 GO、不替 Security / Architecture 签字、不宣布整体通过。
- **C1 已关闭（主理人裁定）**：主理人已裁定 `002653fed0d48…`（39606 B）为**唯一权威最终版**，`415cdd36e…`（38203 B，F1 修复前的中间版本）作废、不得据其出任何结论。此恰为我全量重跑所实测的那一版，与裁定一致。版本抖动链（37158 B → 415cdd36 → 002653fe）及「QA 首轮 24/1 基于旧指纹、须按最终锚点重做」已在 `JEV_GAP_CLOSURE.md §4.1` 登记。**历史保留**：首轮 `24 PASS / 1 FAIL`（F1 OPEN，旧指纹）→ 实现修复 → 最终指纹原样重验 `31 PASS / 0 FAIL`（F1 关闭）；**非「一直全绿」**。见 §1、§2.1、§5、§9。

---

## 1. 环境与被测实现指纹（实读，不采信任何声明）

| 项 | 值 |
|---|---|
| 仓库 | `C:/Users/Administrator/WorkBuddy/jev-v16` |
| 分支 | `feature/v16-p0-gap-closure` |
| git HEAD | `ebc3fcc`（P0 基线，**未 commit**） |
| 工作树 | `WORKTREE_MODIFIED`（runner 重写，未提交） |
| **被测 runner 实读 sha256** | `002653fed0d48e3cd20c95a397400d93830192ca87f60a8361a88b3aa3726d4a` |
| 实测 runner 体量 | **829 行 / 39606 字节**（末尾含换行；部分编辑器显示 830，sha256 为唯一权威） |
| `day14-verify.mjs` 实读 sha256 | `7b6fd5391ecd71dd036ecafd1ac2629a531c021352b535d23e4359dad0ebfbca` |
| Node | v22.22.2 / ESM |
| 实测时间 | 2026-11-11 |

**SHA 演变（如实记录）**：`b8969e1e`（补导出前）→ `415cdd36e`（补聚合导出；team-lead 曾以此冻结锚点）→ `002653fed`（F1 修复，trading-engineer 声明「不再改动」的最终版）。两次最终运行（契约 + 沙箱）的 `IMPL` 行均为 `runnerSha:"002653fed0d4"`，本报告所有结论以此指纹为准。

> **锚点漂移说明**：team-lead 第三条消息冻结 `415cdd36e`（807 行/38203 字节）时，实现方随后又落了 F1 修复。按 team-lead「直接读工作树最新文件、用文件 sha256 作版本锚点」的指示，我测的是当前磁盘上的 `002653fed`。**这两个 sha 的差异正好是 F1 修复**：若在 `415cdd36e` 上验收，T6b-strict 会 FAIL（F1 OPEN）。请主理人明确哪个是权威最终版（见 §9 C1）。
> **主理人裁定结果（本轮追加）**：`002653fed0d48e3cd20c95a397400d93830192ca87f60a8361a88b3aa3726d4a`（**829 行 / 39606 B**，sha256 为唯一权威；部分编辑器显示 830 系末行计数口径，末尾字节 `0a`）为**唯一权威最终版**；`415cdd36e…`（807 行 / 38203 B）**作废、不得再作为验收基线**；**F1 保持关闭，§9 C1 关闭**。依据 MASTER PLAN V2.0 §4/P2：在 `415cdd36e…` 上 T6b-strict 会 FAIL，故只有 `002653fe…` 能作为验收指纹。本轮 QA 再次 `sha256sum` 复核：runner / `day14-verify.mjs`（292 行 / 16959 B）/ `config.freeze.json` 三项指纹与 HEAD=`ebc3fcc` 均未漂移。

### 隔离方法（不破坏真实文件）
- 每个 runner 级测试把「最小子树」**复制**到 OS 临时目录：`scripts/day14-runner.mjs` + `server/ai/*` + `vendor/.../_lib/*` + `equity_collector_v45/src/*` + `reports/day14-artifacts/config.freeze.json`（旧冻结文件的只读副本）。
- 复制后的 runner 以其自身位置推导 `ROOT`，所有产物写进临时沙箱（实测 runDir 均在 `C:/Users/Administrator/AppData/Local/Temp/jev-p0-qa-*`），**不写真实 `reports/day14-artifacts/`**。
- 故障注入只作用于副本：形式风控 `risk-gate.mjs` 换成「import 即 throw」、篡改/断链 JSONL、注入 `providerImpl`/`fetchImpl`/`initialAccount`/`riskCheckImpl:null`、变异 runner 源码。
- **禁网守卫**：测试进程与每个子进程均把 `globalThis.fetch` 替换为「记录 `P0-QA-NONET-ATTEMPT` 并抛错」的函数。
- 子进程 driver 以 `QA_RUN_RESULT` / `QA_RUN_THREW` / `QA_IMPORT_FAILED` 标记输出，`exit code` 由 `process.exitCode` 自然给出（0 成功 / 3 导入失败 / 4 运行抛错）。

---

## 2. 运行命令与 exit code（真实执行）

```
cd C:/Users/Administrator/WorkBuddy/jev-v16

# A. 冻结模块契约测试（不依赖 runner 版本）
node apps/command_center_3d/server/ai/tests/day14-p0-contracts.mjs
  → exit code 0 ; SUMMARY: 7 pass, 0 fail ; IMPL {gitHead:ebc3fcc, dirty:WORKTREE_MODIFIED, runnerSha:002653fed0d4}

# B. 沙箱故障注入验收（被测 runner 在临时副本中作为子进程运行）
node apps/command_center_3d/server/ai/tests/day14-p0-runner.mjs
  → exit code 0 ; SUMMARY: 24 pass, 0 fail ; IMPL {gitHead:ebc3fcc, dirty:WORKTREE_MODIFIED, runnerSha:002653fed0d4}
```

`node --test` 兼容性实测（等价执行，同样以 exit code 反映结果）：
```
node --test .../day14-p0-contracts.mjs → exit 0 ; # pass 1 # fail 0
node --test .../day14-p0-runner.mjs    → exit 0 ; # pass 1 # fail 0
```
（推荐用 `node <file>` 取得逐用例 `[PASS]/[FAIL]` 与 EVIDENCE 明细；`node --test` 给出聚合。）
完整输出摘录见 §4/§5/§6；本轮终端重定向副本 `/tmp/p0-contracts-v2.txt`、`/tmp/p0-runner-v2.txt`。

### 2.1 最终指纹原样重跑（历史对照，非「一直全绿」）

按 team-lead「F1 硬门禁裁定 + 新指纹原样重跑指令」，在**唯一权威最终指纹 `002653fed0d48…`** 上原样重跑全部用例（不改断言、不删用例、不降低强度），并与首轮旧指纹逐项对照：

| 轮次 | 被测指纹 | 命令 | exit code | 结果 |
|---|---|---|---|---|
| 首轮（历史，F1 修复前） | `415cdd36e…`（38203 B，中间版） | `node .../day14-p0-contracts.mjs` + `node .../day14-p0-runner.mjs` | 契约 `0` / runner 非 `0`（含 1 FAIL） | **24 PASS / 1 FAIL / 0 BLOCKED**，唯一 FAIL = **F1**（超时/解析失败被记 `decisionClass=HOLD`） |
| 最终指纹重跑（本结论依据） | `002653fed…`（39606 B，唯一权威） | 同两文件，输出 `IMPL runnerSha:"002653fed0d4"` | `0` / `0` | **31 PASS / 0 FAIL / 0 BLOCKED**（契约 7/7 + 沙箱 24/24）；**T6b-strict 由 FAIL 转 PASS**（`decisionClass=NO_ACTION`） |

- `node --test` 兼容路径在最终指纹上亦 `exit 0`（见上文）。两次运行的 `IMPL` 行均 `runnerSha:"002653fed0d4"`，与当期磁盘 `sha256sum` 一致。
- **F1 失败断言保留**：断言由「应为 NO_ACTION」保持（不是删除、不是降低强度），因 trading-engineer 在 `002653fed…` 上把 provider 失败修为 `NO_ACTION` 而 PASS —— 是「实现修复 → 新指纹重验关闭」，不是改测试变绿、不是一直全绿。详见 §5。
- 实现方已被绝对冻结；本轮 `sha256sum` 复核锚点未漂移。主理人明确本地 commit 不改变工作树文件内容，结论对 `002653fed…` 仍有效、无需重跑（C2 满足）。

> **透明披露（测试侧修正，非删断言）**：上一轮 runner 首跑为 `16 pass/2 fail`，多出的 T2a FAIL 是把头注释「彻底删除 fallbackRiskGate 的放行能力」误判为「简化门仍在」——**测试自身假阳性**，不是产品缺陷。修正为「剥离注释后查可执行代码模式」，断言强度**提高**（`function fallbackRiskGate` / `fallbackRiskGate(` / `"N/A_HOLD"` / `"prep-simplified-fallback"` / `approved: breach === null` / `notional > CFG.maxNotionalPerTrade`）；重跑 PASS。首轮失败输出保留在过程记录中，未隐藏。真正的产品相关 FAIL 自始至终只有 F1 一个，现已关闭。

---

## 3. 证据等级说明

| 等级 | 含义 | 本报告适用 |
|---|---|---|
| **VERIFIED** | 本轮独立实跑、反例/故障注入/参考重算得出 | 全部 31 项检查的 PASS 结论；F-12 零调用计数 |
| **COMMUNITY-VERIFIED** | 实现自标的保守口径，非本轮实测 | `costModels.engine_conservative`（fee 0.001 / slippage 0.015）——QA 仅验证其**未被合并/未回填**，**未**独立验证交易所费率 |
| **UNVERIFIED** | 明确未测得 | `costModels.venue_verified`（`NOT_MEASURED`）；真实 venue 费率/滑点；真实行情+真实 AI 运行（PREP 阶段门控） |

> 本报告**不把**「文件存在」「日志条数」「`qa.pass=true`」「`internalSanity.pass`」当作任何业务正确性证明；runner 自带 `internalSanity` 已自我标注「非独立 QA」（R6 已实证核验）。实现方 `day14-verify.mjs` 的 14/17 项自测**未采信为验收依据**，仅参考其用例设计，全部断言由本 QA 独立编写并运行。

---

## 4. 逐项结果（任务书 10 项 + R1–R6 + F-12）

### 测试文件归属
- `day14-p0-contracts.mjs`：T0 / T7 / T7b / T6a-contract / T2c / T8-engine / T3-ref —— 针对**冻结模块**（`risk-gate.mjs`、`decision-schema.mjs`、`paper-server.mjs::__test`），不受 runner 迭代影响。
- `day14-p0-runner.mjs`：B1 / R1① / T2a / T2b / T3* / M3 / T4 / T5 / R1② / T6b / F1-re-run / T8* / T9* / T10 / R1③ / R1-counter / R1-cross / R6 / cost / F-12 —— 沙箱跑真实 runner。

### 任务书 10 项 ↔ 测试映射

| # | 任务书要求 | 对应测试 | 结论 | 证据等级 |
|---|---|---|---|---|
| 1 | formal gate 导入失败 → fail-closed | **R1①** + B1 对照 | **PASS** | VERIFIED |
| 2 | fallback 简化门不得放行 | T2a / T2b / T2c (+B1) | **PASS** | VERIFIED |
| 3 | 日志哈希链全量校验 | T3 control/T3a/T3b/T3-ref/M3 | **PASS** | VERIFIED |
| 4 | 重启续接 `_prev===上一条_hash` | T4（=R4） | **PASS** | VERIFIED |
| 5 | 断点恢复重放 + 累计量 | T5（=R5） | **PASS** | VERIFIED |
| 6 | NO_ACTION ≠ HOLD | R1②/T6a、T6b、**F1-re-run** | **PASS**（F1 已修） | VERIFIED |
| 7 | 风控反例逐一 | T7 / T7b / T6a-contract | **PASS** | VERIFIED |
| 8 | 独立账户对账 | T8 / T8-injection / T8-engine | **PASS** | VERIFIED |
| 9 | 配置冻结 | T9a / T9b | **PASS** | VERIFIED |
| 10 | 红线 + 无网 | T10 | **PASS** | VERIFIED |

### security-auditor R1–R6 ↔ 测试映射（应 team-lead 要求显式纳入）

| R | security 要求 | 对应测试 | 结论 |
|---|---|---|---|
| R1 | 正式风控导入失败 fail-closed（非零退出、零 approved:true） | **R1①**（精确三场景：① 导入失败零日志零 RISK_APPROVED；② 未验证行情允许 RISK_HOLD_NO_ORDER 但零 orderable/零 RISK_APPROVED；③ 合法 BUY 才 RISK_APPROVED）+ R1-cross（`riskCheckImpl:null`） | **PASS** |
| R2 | 简化门无 approved 放行通路 | T2a（静态无可执行码）/ T2b（gate DOWN 零放行）/ T2c（反例：简化门 approve 而正式门 reject） | **PASS** |
| R4 | 日志链跨重启续接（_prev 接续而非 GENESIS） | T4（两独立子进程真实重启） | **PASS** |
| R5 | 断点恢复带完整性校验（累计预算/失败计数不归零） | T5（累计 6>5 暂停 + 重跑不归零） | **PASS** |
| R6 | 证明 independentQA 不再仅靠文件存在性判 PASS | R6（所有文件齐全且非空、篡改内容后仍被拒绝） | **PASS** |

---

#### R1① / T1 — formal gate 导入失败 → fail-closed ｜ PASS ｜ VERIFIED
```
EVIDENCE control (intact gate) code = 0
EVIDENCE broken gate code = 3
EVIDENCE broken gate first output line = QA_IMPORT_FAILED=[day14] FATAL: formal risk-gate import failed — fail-closed termination: P0-QA injected formal-gate import failure
EVIDENCE run dirs created (should be 0) = 0
EVIDENCE journal records written in total = 0
EVIDENCE RISK_APPROVED count = 0
EVIDENCE any approved:true count = 0
```
- 精确按主理人界定：导入失败在**模块加载期**终止 → `resolveRun` 未调用 → **零 run 目录 / 零日志 / 零 RISK_APPROVED / 连 HOLD 的 approved:true 都没有**。无 warn-and-continue。
- 对照 gate 完好：`code=0` 正常。**B1 证伪能力**：同注入放 ebc3fcc → `code 0` + warn+continue + `{"gate":"prep-simplified-fallback","approved":true,"code":"N/A_HOLD"}`×2。
- **R1-cross（实现方注入点交叉验证）**：`QA_SCENARIO=riskcheck_null` → `riskCheckImpl:null`，`code=4`，`QA_RUN_THREW=[day14] FATAL fail-closed: formal riskCheck unavailable`，零日志零 RISK_APPROVED，**未改任何冻结文件**。

#### R1② / T6a — 未验证行情 fail-closed ｜ PASS ｜ VERIFIED
```
EVIDENCE decisions = [{"decisionClass":"NO_ACTION","aiKind":"none","action":"HOLD","orderable":false,"execution":"NONE","code":"RISK_HOLD_NO_ORDER","reason":"MARKET_UNVERIFIED_FAILCLOSED"}, ...]
EVIDENCE provider statuses = ["NO_CALL_UNVERIFIED_MARKET", ...]
EVIDENCE risk codes = [{"code":"RISK_HOLD_NO_ORDER","approved":true,"orderable":false,"failClosedReason":"MARKET_UNVERIFIED"}, ...]
EVIDENCE RISK_APPROVED count (must be 0) = 0
EVIDENCE orderable=true count (must be 0) = 0
```
- 精确按主理人界定：**允许** `RISK_HOLD_NO_ORDER` 的 `approved:true`（HOLD 无订单，合法），但断言 `decisionClass=NO_ACTION`、`execution=NONE`、`provider=NO_CALL_UNVERIFIED_MARKET`、**零 orderable=true、零 RISK_APPROVED**。`context:null` 只出现在 HOLD/fail-closed 分支（合法，riskCheck 的 HOLD 分支不读 context）。

#### R1③ — 合法 BUY 全量 context 链路 ｜ PASS ｜ VERIFIED
```
EVIDENCE decisions = [{"action":"BUY","decisionClass":"ORDER_INTENT","orderable":true,"execution":"SHADOW_GATED","code":"RISK_APPROVED"}, ...]
EVIDENCE risk = [{"gate":"formal-risk-gate","code":"RISK_APPROVED","approved":true,"orderable":true,
                  "context":{"symbol":"BTCUSDT","markQuality":"verified","priceTs":"...","markPriceUsd":70000},
                  "riskLimits":{"minNotionalUsd":5,"maxNotionalUsd":1000,"maxQuantity":10,"maxContextAgeMs":60000}}, ...]
EVIDENCE RISK_APPROVED records = 2
EVIDENCE non-HOLD risk records with context:null = 0
```
- 合法 BUY（verified context，markPriceUsd=70000，quantity 0.01 → notional 700）→ 出现 `RISK_APPROVED`。
- **非 HOLD 路径 context 非 null**（主理人要求：若非 HOLD 出现 context:null 即为缺陷 → 实测 0 条）。
- **limits 固定**：`riskLimits` 恒等于冻结默认值，未被 provider/外部放宽。
- `orderable=true` 但 `RED_LINES.allowBuySell=false` → `execution="SHADOW_GATED"`、`noFills=true`，**零成交零下单**。
- **反例 R1-counter**：BUY `ORDIUSDT`（非白名单，confidence 0.99）→ `approved:false, RISK_SYMBOL_NOT_ALLOWED, orderable=false, execution=RISK_REJECTED`，零 RISK_APPROVED，非 HOLD 的 context 仍非 null。

#### T2 — fallback 简化门不得放行 ｜ PASS ｜ VERIFIED
- **T2a 静态**：`EVIDENCE executable simplified-gate patterns found = []`（剥离注释后查可执行码）；`formal-risk-gate` 已接线。
- **T2b 运行时**（gate DOWN）：`risk records=0`、`fallback-approved=0`、`approved tradeable conclusions=0`。
- **T2c 契约反例**：按 ebc3fcc `fallbackRiskGate` 逻辑复算，`BUY/ORDIUSDT,notional=100` 与 `BUY/BTCUSDT,context=null` 均被简化门 `approved:true (OK)`，正式 `riskCheck` 分别返回 `RISK_SYMBOL_NOT_ALLOWED` / `RISK_CONTEXT_MISSING`。

#### T3 — 日志哈希链全量校验 ｜ PASS ｜ VERIFIED
- **T3-ref（contracts）**：独立参考校验器 `hash = sha256(prev + "|" + JSON.stringify(rec))`。合法链 `count=4` 通过；改一条内容 → `line 2 _hash mismatch`；中间 `_prev` 改 `GENESIS` → `line 2 _prev != previous _hash (chain break)`。
- **T3 control**：合法既有链 resume 不误报，`run1/run2 code=[0,0]`。
- **T3a 内容篡改**：`run2 code=4`，`QA_RUN_THREW=journal decisions.jsonl HASH TAMPER at line 1: content hash mismatch`，参考校验器同样 `ok:false`。
- **T3b 断链**：`run2 code=4`，`QA_RUN_THREW=journal decisions.jsonl CHAIN BROKEN at line 1: _prev !== previous _hash`。
- **M3 变异证明**：把 runner 链校验两处 `if` 置 `false &&`，同样篡改 → `run2 code=0`（不再 throw）→ 证明 T3 断言能抓回归。

#### R4 / T4 — 重启续接 ｜ PASS ｜ VERIFIED（两独立子进程真实重启）
```
EVIDENCE run1 / run2 code = [0,0]
EVIDENCE total decision records = 5
EVIDENCE reference full-chain check = {"ok":true,"error":null}
EVIDENCE boundary = {"line2._hash":"db49319ea755c9ba4d0c0d0d","line3._prev":"db49319ea755c9ba4d0c0d0d","line3._hash":"fbf129da5125f877add76dfd","line4._prev":"fbf129da5125f877add76dfd"}
EVIDENCE state after resume = {"cycle":5,"calls":5}
```
第二条运行首记录 `_prev` = 前一条 `_hash`（`db49319e…`），**不是 GENESIS**；全链参考校验通过。新链在 `runs/<runId>/`，**未使用根目录 ebc3fcc 遗物**做断言。

#### R5 / T5 — 断点恢复重放 + 累计量 ｜ PASS ｜ VERIFIED
```
EVIDENCE run1 state = {"cycle":2,"costUsd":6,"calls":2,"providerFails":0,"paused":true,"pauseReason":"BUDGET_EXCEEDED"}
EVIDENCE run2 cyclesThisRun = 0
EVIDENCE run2 state = {"cycle":2,"costUsd":6,"calls":2,"providerFails":0,"paused":true}
```
注入每周期 +3 USD（预算 5）：单周期 3<5 却在累计 6 时暂停 → **预算用累计值**。重跑 `cyclesThisRun=0`，cycle/calls/costUsd/providerFails 不归零、未从 0 重头。

#### R6 — independentQA 不再仅靠文件存在性判 PASS ｜ PASS ｜ VERIFIED
```
EVIDENCE old independentQA() present = false
EVIDENCE internalSanity self-labelled non-independent = true
EVIDENCE existence checklist (all files exist & non-empty) = {"allExistNonEmpty":true,"items":{snapshots.jsonl:true,...,day14-summary.json:true}}
EVIDENCE existence checklist AFTER corruption = true
EVIDENCE resume code (must be non-zero) = 4
EVIDENCE resume output = QA_RUN_THREW=journal decisions.jsonl HASH TAMPER at line 1: content hash mismatch
```
构造「**所有文件存在且非空**」（正是 ebc3fcc `independentQA()` 判 PASS 的条件），仅篡改一条日志内容（文件仍存在且非空）→ 实现仍 **throw 拒绝**。静态确认旧 `function independentQA(` 已删、`internalSanity` 自标非独立 QA。

#### F1 — NO_ACTION ≠ HOLD ｜ PASS（已关闭）｜ VERIFIED
- **R1②/T6a PASS**：未验证行情 → `NO_ACTION`（非模型 HOLD）。
- **T6b PASS**：provider 超时/解析失败 → `orderable=false`、无 `RISK_APPROVED`。
- **F1-re-run / T6b-strict PASS**（最终版 `002653fed` 上独立重跑，失败断言保留）：
```
EVIDENCE provider_error decisionClass = ["NO_ACTION","NO_ACTION"]
EVIDENCE provider_error fields = [{"reason":"PROVIDER_FAILED_FAILCLOSED","orderable":false,"execution":"NONE","code":"RISK_HOLD_NO_ORDER"}, ...]
EVIDENCE provider_error failClosedReason = ["PROVIDER_FAILED","PROVIDER_FAILED"]
EVIDENCE provider_error provider status = ["PROVIDER_ERROR","PROVIDER_ERROR"]
EVIDENCE parse_failure decisionClass = ["NO_ACTION","NO_ACTION"]
EVIDENCE parse_failure fields = [{"reason":"PROVIDER_FAILED_FAILCLOSED","orderable":false,"execution":"NONE","code":"RISK_HOLD_NO_ORDER"}, ...]
EVIDENCE parse_failure failClosedReason = ["PROVIDER_FAILED","PROVIDER_FAILED"]
EVIDENCE parse_failure provider status = ["PARSE_FAILED","PARSE_FAILED"]
```
provider 失败已**不再改写为 HOLD**，记 `NO_ACTION` + `PROVIDER_FAILED_FAILCLOSED` + `execution=NONE`，两类 NO_ACTION 可由 `failClosedReason`（`PROVIDER_FAILED` vs `MARKET_UNVERIFIED`）区分。F1 关闭。
- **T6a-contract（contracts）PASS**：非法/解析失败 decision → `riskCheck` 返回 `RISK_INVALID_DECISION, approved:false`。

#### T7 — 风控反例逐一 ｜ PASS ｜ VERIFIED（contracts 文件）
8 个反例全部 `approved:false` 且 code 命中 `RISK_CODES`：
```
non-whitelist ORDIUSDT → RISK_SYMBOL_NOT_ALLOWED ; mark_quality!=verified → RISK_CONTEXT_NOT_VERIFIED
missing context → RISK_CONTEXT_MISSING ; stale(120s>30s) → RISK_CONTEXT_STALE
expired_at past → RISK_CONTEXT_EXPIRED ; future ts(+10s) → RISK_CONTEXT_INVALID_TS
quantity>max → RISK_LIMIT_EXCEEDED ; notional>max → RISK_LIMIT_EXCEEDED
valid BUY(0.01@70000) → approved:true, RISK_APPROVED, notional_usd:700
HOLD + context:null → approved:true, RISK_HOLD_NO_ORDER
```
`T7b`：`confidence 0.01/0.99` 判定一致；`0.99` 不能翻案 `RISK_SYMBOL_NOT_ALLOWED` → **永不读 confidence**。`T0`：`DECISION_SCHEMA_VERSION="jev.ai.decision/v1"`，严格校验。

#### T8 — 独立账户对账 ｜ PASS ｜ VERIFIED
- **T8 运行时**：`equity.jsonl` 含 `basis.engine`（equity/cash/marketValue/version/generation/trades）、`basis.harness`、`basis.delta`、`basis.tolerance`、`noFills:true`、note「no fills …」；静态已无旧硬编码 `const equity = CONFIG_FREEZE.virtualCapitalUsdt` 与 `reconcile:"MATCH (frozen-engine account vs harness ledger)"`。
- **T8-injection 反例（关键证伪）**：注入非默认引擎账户（cash 9500 + BTC 持仓）→
```
EVIDENCE injected-account equity = {"equity":16500,"reconcile":"MISMATCH","delta":6500,"engineEquity":16500,"harnessEquity":10000}
```
证明 equity / reconcile 是**实算**，不是写死 `10000` / `MATCH`。
- **T8-engine（contracts）**：冻结引擎 `summarize(createAccount())` 基线 10000；改造账户后 `equity=16600`（非 10000）→ `10000/MATCH` 不是通用结论；fresh account `trades=0, orders=0` 支持「no fills」如实标注。

#### 双口径成本模型 — 未合并 ｜ PASS ｜ VERIFIED
```
EVIDENCE engine_conservative = {"feeRate":0.001,"slippagePct":0.015,"provenance":"COMMUNITY-VERIFIED ...","status":"需实测 / NOT venue-measured"}
EVIDENCE venue_verified = {"feeRate":null,"slippagePct":null,"provenance":"UNVERIFIED","status":"NOT_MEASURED — 需要真实 venue 费率/滑点实测"}
```
两口径分列，`venue_verified.feeRate/slippagePct` 保持 `null` **未被回填**，`engine_conservative` 数值未被冒充为 venue 实测。（QA 仅验证「未合并/未回填」，不独立验证费率真实性。）

#### T9 — 配置冻结 ｜ PASS ｜ VERIFIED
- **T9a**：隔离副本改 `callBudgetUsd=999` → `code=4`，`QA_RUN_THREW=config.freeze.json MISMATCH on field 'callBudgetUsd' — legacy freeze altered`。
- **T9b**：`run1` + `run2(newRun:true)` → 冻结文件 `before=after=d4edce64c48bf15ab6803ce9…`；`runs/` 出现 2 个 runId 目录，旧目录保留 → 新 runId 不覆盖旧冻结。

#### T10 — 红线 + 无网 ｜ PASS ｜ VERIFIED
```
EVIDENCE true-valued red-line literals in source = []
EVIDENCE RED_LINES false present = true
EVIDENCE freeze red lines = {"enableRealTrading":false,"allowBuySell":false}
EVIDENCE provider statuses = [{FIXTURE_NO_LLM_CALL,0,0} ×3]
EVIDENCE real-call-shaped records = 0
EVIDENCE network guard triggered = false
EVIDENCE run.json redLines = {enableRealTrading:false,allowBuySell:false,noEgress:true,noPaidModel:true,noOrderSubmit:true}
```

---

## 5. F1 状态：已关闭（上一轮 FAIL → 修复 → 独立重跑 PASS）

| 字段 | 内容 |
|---|---|
| 上一轮 | **FAIL**：provider 超时/解析失败被记成 `decisionClass:"HOLD"`（`415cdd36e` 版） |
| 裁定 | trading-engineer 按严格口径**修为 NO_ACTION**（非「按设计可接受」），我未改绿、未替实现方补代码 |
| 本轮 | 在最终版 `002653fed` 上**独立重跑 T6b-strict**，断言保持不变（`assert decisionClass==="NO_ACTION"`，不是删除），实测两种失败场景均 `NO_ACTION` → **PASS** |
| 证据 | §4 F1 段的 8 行 EVIDENCE（`PROVIDER_FAILED_FAILCLOSED` / `failClosedReason=PROVIDER_FAILED` / `execution=NONE` / `PARSE_FAILED`） |
| 残留 | 无。缓解事实（orderable=false、无 RISK_APPROVED、provider 状态可区分）在最终版成为主路径而非兜底 |

---

## 6. F-12 — QA 侧外部调用计数（应 team-lead / security-auditor 要求，独立证据）

**结论：本轮编写与运行 P0-F 测试全程，真实外部 HTTP / 付费模型 / 订单 / 生产 DB 调用 = 0。证据等级 VERIFIED。计数非 0 的 BLOCKED 未触发。**

依据（test `F-12 QA-side external calls = 0` 的真实输出）：
```
EVIDENCE network module imports in QA sources = 0
EVIDENCE raw network call primitives in QA sources = 0
EVIDENCE globalThis.fetch guard installations (lib + child driver) = 2
EVIDENCE parent globalThis.fetch is the NONET guard = true
EVIDENCE child processes spawned (sandboxed local node) = 33
EVIDENCE P0-QA-NONET-ATTEMPT hits across ALL child stderr (blocked egress) = 0
```
1. **静态**：3 个 QA 源文件（`day14-p0-lib/contracts/runner.mjs`）中 `node:http(s)/net/tls/undici/axios/ws` import = 0；`http.request/https.get/net.connect/new WebSocket` 等原始网络原语 = 0。故障注入一律用本地 fixture（fixture fetcher / provider / engine），无网络。
2. **守卫**：`day14-p0-lib.mjs` 顶层把父进程 `globalThis.fetch` 硬替换为「记录 + throw」守卫；每个子进程 driver 同样替换（共 2 处安装）。父进程 `globalThis.fetch` 实测就是 NONET 守卫（任何 fetch 调用会抛错并留痕）。
3. **动态**：33 个沙箱子进程全部为本地 `node` 进程（`spawnSync(process.execPath, [driver], {cwd: 临时沙箱})`），累计 stderr 中 `P0-QA-NONET-ATTEMPT` 命中 = **0** → 守卫从未触发，即无任何真实外呼尝试。
4. **边界**：这是 QA 侧独立计数；与 trading-engineer 的书面声明是**两条独立证据，不互相替代、不合并**。security-auditor 的 F-12 UNVERIFIED 仅指其静态只读审计无法证明运行期零外呼，本 QA 的运行时动态计数补上了这一块（但仅覆盖本 QA 进程树）。

---

## 6A. F-12 书面确认段（可逐字引用 / 独立于 §6，供 team-lead 中转）

> 以下整段自包含、可逐字引用，为 independent-qa 的 F-12 独立书面确认；与 §6 的详细依据分离，便于单独转交 security-auditor。

**F-12 书面确认（independent-qa · QA 侧独立证据）**

QA 侧本轮编写与运行 P0-F 测试全程，**真实外部 HTTP / 付费模型 / 订单 / 生产 DB 调用 = 0**。

依据（本 QA 进程树的运行期动态计数，非静态推断）：
1. 全部被测 runner 以 **33 个本地 `node` 沙箱子进程**运行（`spawnSync(process.execPath, [driver], {cwd: 临时沙箱})`），故障注入一律使用本地 fixture（fixture fetcher / provider / engine），无网络依赖；QA 三个源文件中网络模块 import = 0、原始网络原语 = 0。
2. 父进程与每个子进程 driver 共 **2 处**把 `globalThis.fetch` **硬替换**为「记录 `P0-QA-NONET-ATTEMPT` 并 throw」的守卫；任何 fetch 调用都会抛错并留痕，父进程 `globalThis.fetch` 实测即为该守卫。
3. 动态计数：33 个子进程 stdout/stderr 中 `P0-QA-NONET-ATTEMPT` **命中 0** → 守卫从未触发，即无任何真实外呼尝试。

边界声明（不可省略）：
- 该动态计数**只覆盖 QA 进程树**，不覆盖、也**不替代 trading-engineer 的书面声明**（后者是另一条独立证据，各自独立、不互相替代或平均）。
- 本确认**不要求 security-auditor 把其静态只读审计的 F-12 结论从 UNVERIFIED 改写为 VERIFIED**：security-auditor 的静态只读审计与 QA 的运行期动态计数是方法不同的两条独立证据，各自按其方法标注证据等级即可。
- 本确认不构成对实现方、Security 或 Architecture 的签字，也不构成生产 GO 或整体通过。

证据等级：本 QA 进程树范围内 VERIFIED；进程树之外不越界宣称（UNVERIFIED）。
（计数来源：最终指纹重跑 §2.1 期间的动态统计；此后的报告更新仅为文件编辑，未新增测试运行、未发起任何外部调用。）

---

## 7. 零自证绿 / 禁止删除断言过关 自查

- 无任何检查以「文件存在」「日志条数」「`qa.pass`」「`internalSanity.pass`」作为业务正确性证明；R6 专门反证了「文件齐全仍须拒绝」。
- 所有 PASS 来自：故障注入后**断言未出现坏行为**、参考哈希链**重算比对**、冻结模块**反例**、`summarize` **实算**、变异/基线**证伪能力证明**。
- **F1 的失败断言被保留**（改为断言 NO_ACTION 而非删除），在最终版上因实现修复而 PASS——不是改测试变绿。
- 上一轮唯一的真实 FAIL（F1）已如实报告并推动修复；T2a 假阳性修正过程全文披露。
- 实现方 14/17 项自测**未采信**；所有断言本 QA 独立编写、独立运行。

---

## 8. BLOCKED 项与「版本变更后需补跑」清单

**本轮 0 BLOCKED**：被验实现已在磁盘（`002653fed`），10 类测试 + R1–R6 + F-12 全部真实运行，结果均 VERIFIED。

需主理人/后续 owner 处理的清单与销账状态（非 QA 代码缺陷）：
1. **版本锚点裁定（§9 C1）— 已裁定 / 已关闭**：主理人本轮裁定 `002653fed…`（829 行 / 39606 B，sha256 为唯一权威）为唯一权威最终版，`415cdd36e…`（807 行 / 38203 B）作废、不得再作为验收基线；F1 关闭结论有效，恰为本轮全量重跑实测指纹。首轮 24/1 基于旧指纹、最终 31/0 基于最终指纹，历史保留（§2.1 / §5），非「一直全绿」。
2. **任何后续改动后补跑（§9 C2，持续性剩余条件）**：`day14-runner.mjs` 不得再改动；实现方已被绝对冻结，本轮 `sha256sum` 复核未漂移（HEAD 仍 `ebc3fcc`，未 commit），主理人明确本地 commit 不改变工作树文件内容、结论对该指纹仍有效、无需重跑。**若未来确有文件内容改动**，仍须原样重跑并核对 `IMPL runnerSha`：
   - `node .../day14-p0-contracts.mjs`（期望 exit 0）
   - `node .../day14-p0-runner.mjs`（期望 exit 0）
3. **超出 QA 范围、本轮未做**（不预写 PASS）：真实 Binance.US 行情 + 真实 MiniMax 路径的独立 QA（仍被红线门控）、真实 venue 费率/滑点实测（`venue_verified=UNVERIFIED`）、Security / Architecture 签字（属 security-auditor #3/#8）、生产 GO。

---

## 9. QA 范围裁决

**DECISION：GO WITH CONDITIONS（仅限 QA 范围）** —— 本轮按主理人「C1 权威锚点」裁定统一口径：**C1 已关闭**；**剩余条件仅 C2（`day14-runner.mjs` 不得再改动）与 C3（信息项）**。不替 Security / Architecture 签字，不宣布生产 GO，不宣布整体通过。

依据：唯一权威最终版 `002653fed…`（829 行 / 39606 B，sha256 为唯一权威）上 **31/31 检查以反例与故障注入实证通过（0 FAIL / 0 BLOCKED）**，含 B1 基线 + M3 变异的证伪能力证明；F1「旧指纹 FAIL → 实现修复 → 最终指纹原样重验关闭」（§2.1 / §5）；R1–R6 精确断言、合法 BUY 全量 context 链路、双口径成本模型、F-12 零外呼均通过；硬安全属性全成立。首轮 `24 PASS / 1 FAIL` 历史保留（§0 / §2.1 / §5），非「一直全绿」。

**原条件文字（历史证据，未删原文）与其后追加的裁定结果**：

- **C1（必须）**：主理人裁定并统一版本锚点 —— 确认 `002653fed…`（**829 行 / 39606 B**，sha256 为唯一权威；部分编辑器显示 830 系末行计数口径）为权威最终文件；若坚持 `415cdd36e…`（807 行 / 38203 字节）为最终版，则 F1 仍 OPEN、本裁决需改为 **NEEDS REWORK** 直到 F1 修复。
  - → **C1 已关闭 — 主理人本轮裁定 `002653fed…` 为唯一权威最终版，`415cdd36e…` 作废（不得再作为验收基线）；F1 关闭结论有效**。依据 MASTER PLAN V2.0 §4/P2：在 `415cdd36e…` 上 T6b-strict 会 FAIL，故只有 `002653fe…` 能作为验收指纹。原「NEEDS REWORK」分支已因裁定 + 最终指纹重验而消解。
- **C2（必须，剩余条件）**：`day14-runner.mjs` 不得再改动；一旦改动，按 §8 清单原样重跑两文件并更新 `IMPL` 指纹。（实现当前未提交，HEAD 仍 `ebc3fcc`；本轮 `sha256sum` 复核未漂移，主理人明确本地 commit 不改变工作树文件内容。）
- **C3（信息，剩余条件）**：F-12 = 0 是 QA 侧独立证据，与实现方书面声明、security-auditor 的静态审计**各自独立、不得互相替代或平均**（书面确认段见 §6A，可逐字引用）。

**DECISION 演变（全程留痕，非「一直全绿」）**：

| 阶段 | DECISION | 触发 |
|---|---|---|
| 首轮（旧指纹 `415cdd36e…`） | **NEEDS REWORK**（F1 硬门禁） | 24 PASS / 1 FAIL，唯一 FAIL = F1 |
| F1 修复 + 最终指纹重验后 | **GO WITH CONDITIONS**（C1/C2/C3） | 31 PASS / 0 FAIL，C1 待裁定 |
| 上一轮（依当时「C1/C2/C3 均已满足」授权） | **GO（无条件）** | QA 据该授权独立判断三条件销账 |
| **本轮（主理人 C1 权威锚点裁定）** | **GO WITH CONDITIONS（剩余 C2 + C3，C1 已关闭）** | 主理人把 C2 重新界定为**持续性剩余条件**（runner 不得再改动）；QA 据此取更保守口径统一 |

> 口径说明：上一轮 `GO（无条件）` 与本轮 `GO WITH CONDITIONS` 的差异，仅在于主理人本轮把 C2 从「当前已满足」明确为「持续剩余条件」；这不是新缺陷、不是任何 FAIL→PASS 改写、未删除任何断言或条件原文，方向**更保守**。两轮结论均为 QA 范围内的 GO，均未解除红线、均未替 Security / Architecture 签字。

**GO 的边界（纪律声明，非条件）**：
- 本 GO **不等于生产 GO**、不解除 `enableRealTrading=false` / `allowBuySell=false` 等红线；**R1③ 恰好证明合法 BUY 仍被 `SHADOW_GATED`**（`orderable=true` 但零成交零下单），`RISK_HOLD_NO_ORDER` 的 `approved:true` 是 HOLD 无订单语义，**不可误读为可交易放行**。31/31 PASS 不构成解除红线 / 触网 / 下单的依据；本裁决仅限 QA 范围，不宣布整体通过、不宣布生产 GO。
- `costModels.venue_verified` 等 UNVERIFIED 项保持 UNVERIFIED；真实行情 / 真实 AI / 真实 venue 费率仍被红线门控、本轮未测（§3 / §8），不预写 PASS。

> 不越权：Security gate（security-auditor 报 NO-GO）与 Architecture 签字不在本裁决内；本结论不推翻、不覆盖、不平均其他角色结论。

---

## 10. 交付物

| 文件 | 说明 |
|---|---|
| `apps/command_center_3d/server/ai/tests/day14-p0-lib.mjs` | 沙箱 / 禁网守卫 + F-12 计数器 / 子进程 driver（含 7 个故障注入场景）/ 参考链校验器 / IMPL 指纹 工具库 |
| `apps/command_center_3d/server/ai/tests/day14-p0-contracts.mjs` | 冻结模块契约测试（7 项，exit 0） |
| `apps/command_center_3d/server/ai/tests/day14-p0-runner.mjs` | 沙箱故障注入验收（24 项，exit 0，含 R1–R6/F-12/F1-re-run） |
| `reports/JEV_INDEPENDENT_QA.md` | 本报告 |

**未修改**：`scripts/day14-runner.mjs`、任何实现文件、旧 `config.freeze.json`、`server/ai/*`、`vendor/*`；未 git push、未碰 main/Release、未触网、未调付费模型、未下单。真实 `reports/day14-artifacts/` 未被本 QA 写入（全部产物在临时沙箱）。

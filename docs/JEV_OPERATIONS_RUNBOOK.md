# JEV 运维手册（OPERATIONS RUNBOOK）

> 读者：未来的值班者（on-call）
> 版本：冻结快照锚定提交 `5834c3745843baa33b82e04a746fa8691684b2f6`；本手册编写时实测 `HEAD = c0cad254…`（其后追加了发布状态账本的提交）。分支 `feature/v16-p0-gap-closure`，该分支**无 upstream**（未 push）。
> 唯一事实来源：`reports/JEV_FINAL_MASTER_STATUS.json`（提交时点冻结快照）+ `reports/JEV_GAP_CLOSURE.md` + `reports/JEV_REAL_TRADING_SECURITY.md` + `reports/JEV_INDEPENDENT_QA.md`
> 引用约定：本手册引用其他报告一律用「文件名 + 章节号」，**不使用裸行号**（行号会随报告追加而漂移，见 `reports/JEV_GAP_CLOSURE.md` §4.6 DEV-06）。

---

## 0. 结论先行

**本手册现阶段是「手动执行 + 校验」手册，不是「7×24 无人值守运维」手册。这两种东西不是同一件事，不要混读。**

| 问题 | 当前答案 | 依据 |
|---|---|---|
| 有 7×24 守护进程在跑吗 | **没有** | `reports/JEV_FINAL_MASTER_STATUS.json` §phases.P6：`daemonBinding = DECLARED_NOT_WIRED` |
| 有调度器（scheduler）吗 | **没有** | 同上 §phases.P6 `boundaries`：「无守护进程」「无 scheduler」 |
| 有人会自动发现故障并重启吗 | **没有** | P6 的 watchdog / leader-election / resume 只是**可本地验证的骨架**，无进程调用它们 |
| 有监控告警接出吗 | **NOT_MEASURED** | 本轮无任何告警通道证据；不得写成「0 个告警」或「无告警」 |
| 有 CI 在把关吗 | **没有验证过** | CI 状态 `CONFIG-CHANGE / UNVERIFIED`，无 Run ID；不得读作 CI PASS |
| 那值班者能做什么 | 手动跑 7 个校验套件 + 校 5 个冻结锚点 + 判读 F-16 噪声 | 见 §2 §3 §5 |

**一句话**：现阶段「值班」= 人工确认「代码边界还是提交时那个样子」。任何「系统自己会处理」的假设都不成立。

---

## 1. 本手册能做什么 / 不能做什么

### 1.1 能做

| # | 动作 | 产出 | 章节 |
|---|---|---|---|
| 1 | 手动运行 7 个校验套件，确认 94 项断言全过 | 环境是否可信的判据 | §2 |
| 2 | 手动校验 5 个冻结锚点的 sha256 | 基线是否漂移的判据 | §3 |
| 3 | 按 P6 编排顺序读懂 Supervisor 的**设计**语义 | 将来接线时的验收基准 | §4 |
| 4 | 判读 `reports/evidence/v16/*.json` 的已知脏化噪声 | 避免误判为「有人改了代码」 | §5 |
| 5 | 判读未跟踪 scratch 目录的合法性 | 避免误 `git add` | §6 |
| 6 | 执行紧急止损（不解除红线） | 防止误开真实交易 | §7 |

### 1.2 不能做

| # | 做不到的事 | 原因 | 如实标注 |
|---|---|---|---|
| 1 | 无人值守、自动重跑、自动恢复 | P6 无守护进程、无 scheduler | `DECLARED_NOT_WIRED` |
| 2 | 自动发现「某周期挂了」 | 无 watchdog 进程在跑（`watchdog.mjs` 只被 `supervisor.mjs` 在内存中调用） | 同上 |
| 3 | 依赖 CI 兜底 | 无本地 Actions runner，改动未经真实 runner 验证 | `CONFIG-CHANGE / UNVERIFIED` |
| 4 | 依赖类型检查兜底 | `node_modules` 缺失，`tsc` 从未执行 | `TYPECHECK = UNVERIFIED` |
| 5 | 声称「真实行情 / 真实 AI / 真实资金已验证」 | 全部结果为 `SYNTHETIC_FIXTURE`，零真实推理、零真实订单 | `allResultsProvenance = SYNTHETIC_FIXTURE` |
| 6 | 提供历史运行曲线 / 长期容量趋势 | P5（7 天 Shadow / 30 天 Paper）为 BLOCKED，真实时间窗口不存在 | 无数据，写 `NOT_MEASURED` |

> 铁律：**不要把「本地 node 直跑通过」写成「生产就绪」。** 本仓的口径是 `IMPLEMENTED / LOCAL PASS`，它只等于「本地 `node` 直跑 exit 0」。

---

## 2. 七个可执行校验套件（当前唯一真实的运维动作）

在仓库根目录执行。以下期望输出取自 `reports/JEV_FINAL_MASTER_STATUS.json` §verification（提交前 team-lead 一手复跑，不采信成员自评）；**本手册编写时未重跑，数字为快照值**。

| # | 命令 | 期望输出 | 期望 exit |
|---|---|---|---|
| 1 | `node apps/command_center_3d/server/ai/tests/day14-p0-contracts.mjs` | `7 pass, 0 fail` | 0 |
| 2 | `node apps/command_center_3d/server/ai/tests/day14-p0-runner.mjs` | `24 pass, 0 fail` | 0 |
| 3 | `node apps/equity_collector_v45/tests/market-data.test.mjs` | `PASS=10 FAIL=0` | 0 |
| 4 | `node apps/command_center_3d/server/ai/evidence/tests/inference-evidence.test.mjs` | `PASS=17 FAIL=0` | 0 |
| 5 | `node apps/command_center_3d/scripts/day14-research/p3-contracts.mjs` | `PASS=12 FAIL=0 TOTAL=12` | 0 |
| 6 | `node apps/command_center_3d/scripts/supervisor/supervisor.test.mjs` | `PASS=14 FAIL=0` | 0 |
| 7 | `node apps/command_center_3d/scripts/check-no-fake-live.mjs` | `CORE_ASSERTIONS=7 EXTRA_ASSERTIONS=3 TOTAL=10` | 0 |

合计：**7 套件 / 94 项断言 / 0 FAIL / exit 全 0**（7+24+10+17+12+14+10 = 94）。

其中套件 1+2（7+24 = 31 项）即 `reports/JEV_INDEPENDENT_QA.md` §0 所述独立 QA 的 31 项检查，两处口径一致。

### 2.1 处置规则

| 现象 | 判定 | 处置 |
|---|---|---|
| 7 套件全 exit 0 且断言数与上表一致 | 环境可信 | 继续值班动作 |
| 任一非 0 退出 | **环境损坏** | **立即停止**，不得继续任何后续动作，不得改断言、不得跳过该套件 |
| exit 0 但断言数少于上表 | **视为损坏**（断言被删/被静默跳过） | 同上，停止并上报 |
| 断言的**文字**与上表不同（例如 pass 计数口径变了） | 可能是版本差异 | 先核对 §3 锚点；锚点未漂移却仍不一致 → 停止并上报 |
| 某套件报 `Cannot find module` | 依赖缺失 | 记为 `NOT_MEASURED`，**不得**记为 0 失败或 PASS |

### 2.2 这些套件为什么「有牙」

套件不是橡皮图章，它们被变异测试证明过「拿掉保护就会红」：

| 证据 | 说明 | 来源 |
|---|---|---|
| 套件 2 的 B1 基线证伪 | 用 `ebc3fcc` 基线的源码证明旧实现确实会漏检 | `reports/JEV_FINAL_MASTER_STATUS.json` §verification.suites[2].note |
| 套件 2 的 M3 变异证明 | 关掉链校验的 MUTANT 使篡改漏检 → 证明 T3 断言有牙 | 同上 |
| 套件 6 的变异证明 | 把 `DAEMON_BINDING` 篡改为 `MUTANT_WIRED_FAKE` 后 `PASS=13 FAIL=1` | 同上 §verification.suites[6].note |

> 值班纪律：**永远不要为了让套件变绿而改断言、删用例、加 `continue-on-error`。** 本仓历史上出现过 1 次 FAIL（F1：provider 失败被改写成 HOLD），该失败记录被完整保留，是靠修实现 + 独立原样重跑关闭的，不是靠改断言。

---

## 3. 冻结锚点校验程序

### 3.1 五个锚点

在仓库根目录执行：

```bash
sha256sum \
  apps/command_center_3d/scripts/day14-runner.mjs \
  apps/command_center_3d/server/ai/risk-gate.mjs \
  apps/command_center_3d/server/ai/decision-schema.mjs \
  apps/command_center_3d/server/engine/_lib/paper-server.mjs \
  reports/day14-artifacts/config.freeze.json
```

| 文件 | 期望 sha256 | 角色 |
|---|---|---|
| `apps/command_center_3d/scripts/day14-runner.mjs` | `002653fed0d48e3cd20c95a397400d93830192ca87f60a8361a88b3aa3726d4a` | P0 冻结核心（829 行） |
| `apps/command_center_3d/server/ai/risk-gate.mjs` | `5797ba8cd6308e11c75c53121f151402e872ec1f1551780eb6953d60c992278f` | 正式风控唯一权威门 |
| `apps/command_center_3d/server/ai/decision-schema.mjs` | `328b7a9b140069d354e9b06a08d85dca79ed47f3394acb8fd43c031be3184ba1` | 14 字段白名单 |
| `apps/command_center_3d/server/engine/_lib/paper-server.mjs` | `f9ac6e142446ef2aa8deb928f2b129eb2d42f4227d1bc22469191087312e4d74` | 冻结引擎 |
| `reports/day14-artifacts/config.freeze.json` | `d4edce64c48bf15ab6803ce9ea08a164404d84642c61065d74550d7983208658` | 旧配置冻结快照（未改） |

来源：`reports/JEV_FINAL_MASTER_STATUS.json` §frozenAnchors（`verifiedUnchanged = true`，提交前终检逐字符一致）。

`reports/JEV_GAP_CLOSURE.md` §4.8.1 另列 10 个终检锚点（含 `cost-models.mjs`、`inference-evidence.mjs`、`fee-wrap.mjs`、`p3-contracts.mjs`、`.github/workflows/ci.yml`）。若要一次性全查，以 §4.8.1 的 10 项清单为准，本节 5 项是**最低必查集**。

**本手册编写时实测**：上述五项 `sha256sum` 输出与表中期望值**逐字匹配**，零漂移。这是唯一一项本文作者亲手跑过的校验；§2 的套件结果则是快照值，未重跑。

### 3.2 判定与处置

| 情形 | 判定 | 处置 |
|---|---|---|
| 5 项全部逐字匹配 | 基线未漂移 | 可继续 |
| 任一不匹配 | **锚点漂移** | 见 3.3 |
| 文件不存在 | 工作树不完整 | 停止，先查是否误删 / 浅克隆缺对象 |

### 3.3 锚点漂移处置（硬性）

1. **立即停止**后续一切动作。
2. **严禁**把新算出的哈希抄下来当成「新的通过基准」。用新哈希顶替旧哈希 = 把污染洗白成合规。
3. **查明性质**，二选一：
   - **合法变更**：有明确授权、有文档记录的改动。此时 QA 条件 C2（`day14-runner.mjs` 不得再改动，见 `reports/JEV_INDEPENDENT_QA.md` §9）与 Security 条件 C7（版本锁定，见 `reports/JEV_REAL_TRADING_SECURITY.md` §10.4）**同时失效**，94 项断言结论作废，必须由独立 QA 在**新**指纹上原样重跑全部套件，并由安全侧重新触发复核。
   - **污染**：非授权改动。此时不得提交、不得 push，需回溯改动来源。
4. 无论哪种性质，**都不得在未重跑的情况下继续引用旧的 94 项断言结论**。

> 为什么这么严：本仓真实发生过锚点漂移事故（`reports/JEV_GAP_CLOSURE.md` §4.6 DEV-06，引用位置从 L153-156 漂到 L160-163），也真实发生过「绕过裁定擅自改被验对象」（§4.2 DEV-03）。漂移不是理论风险。

### 3.4 锚点的性质（避免误用）

sha256 / 行数 / 字节数**只是某一版本快照**，不是永恒的真理标识。真正稳定的锚点是**章节号 + 逐字证据内容**。这一点由安全侧正式声明：`reports/JEV_REAL_TRADING_SECURITY.md` §10.9.1。

---

## 4. P6 Supervisor 编排顺序（设计已定，尚未接线）

### 4.1 先记住这句话

`DAEMON_BINDING = "DECLARED_NOT_WIRED"`（`apps/command_center_3d/scripts/supervisor/supervisor.mjs` 顶部导出）。文件顶部注释自述：「本轮仅落地可验证本地骨架，24/7 守护进程未接线、未启动。」

**没有任何进程会自动调用 `runCycle`。** 下列顺序是**将来接线时的验收基准**，现在不是运行时行为。

### 4.2 七阶段顺序与失败语义

`STAGES = ['killSwitch', 'lease', 'idempotency', 'resume', 'cycleImpl', 'record', 'heartbeat']`（同文件导出，顺序即执行顺序）。

| 序 | 阶段 | 失败/异常时的语义 | 返回标记 | 是否执行周期体 |
|---|---|---|---|---|
| 1 | `killSwitch` | 已处于停机态则**直接返回**，不进入后续任何阶段；**永不自动清除**（注释原文：`never auto-cleared`） | `status: HALTED` | 否 |
| 2 | `lease` | 未拿到租约则返回，不做任何工作（防双主） | `status: NO_LEASE` | 否 |
| 3 | `idempotency` | 同 `(runId, cycle)` 已完成则拒绝重做 | `status: DUPLICATE` | 否 |
| 4 | `resume` | 校验链不通过则 **throw**，绝不执行周期（fail-closed） | 抛异常 | 否 |
| 5 | `cycleImpl` | 默认纯内存 no-op fixture；异常向上抛 | 抛异常 | 是（进入后） |
| 6 | `record` | 记录完成标记；若此处失败，下一轮会被 `idempotency` 判为未完成 | — | 已执行 |
| 7 | `heartbeat` | 打点；若失败，watchdog 侧会看不到心跳（当前无进程消费） | — | 已执行 |

补充：租约在 `finally` 中释放，且释放时内部校验 `ownerToken`——即「只能释放自己持有的租约」。

### 4.3 接线时必须守住的语义

| # | 要求 | 理由 |
|---|---|---|
| 1 | `killSwitch` 必须保持「不自动清除」 | 自动恢复的 kill switch 等于没有 kill switch |
| 2 | `resume` 失败必须 `throw`，不得降级为「从头开始」 | 降级 = 静默丢弃证据链 |
| 3 | `idempotency` 必须按 `(runId, cycle)` 判重 | 重复执行会污染账本 |
| 4 | 接线后不得因此打开真实资金 | 见 §7 |

---

## 5. `UNKNOWN ≠ 0` 运维纪律

### 5.1 规则

| 情形 | 正确写法 | 禁止写法 |
|---|---|---|
| 没有数据 | `NOT_MEASURED` | `0` |
| 请求失败 | `NOT_MEASURED` / `—` | `0` |
| 未接线的能力 | `DECLARED_NOT_WIRED` | 「已启用」 |
| 未实测的成本 | `null` + `provenance: UNVERIFIED` | 回填预算值（见 §4.7 R-4） |
| 未验证的结论 | `UNVERIFIED` | `VERIFIED` |

代码层面禁止：`?? 0`、`|| 0`、`"0 个"`、`length || 0`。

### 5.2 反例（本仓现存，未修）

`apps/command_center_3d/src/trading/pages/StrategyArena.tsx` 当前仍在用：

- `:23` → `st.data?.oos_samples ?? 0`、`st.data?.backtest_runs ?? 0`
- `:32` → `` st.data?.strategies?.length ? `${...} 个` : "0 个" ``

后果：当请求**失败**时（`st.data` 为 `undefined`），页面渲染出「真实 OOS 样本 = 0」「回测运行 = 0」——把一个**未知**渲染成了**事实上的 0**。读者会以为「测过了，样本是 0」，而真相是「没测到」。

这属于运维口径污染，不是显示瑕疵。值班者看到界面上出现 `0` 时，**必须先确认是「测出来是 0」还是「没测到」**。

> 该写法目前未被列入已修清单，属已知残留。本手册只登记事实，不代为修复（本轮文档任务无源码写权限）。

### 5.3 正确样例（本仓已有）

| 位置 | 写法 | 说明 |
|---|---|---|
| P4 `venue_verified` 成本模型 | `feeRate: null` / `slippagePct: null` / `status: NOT_MEASURED` / `provenance: UNVERIFIED` | 未实测就写 `null`，不回填 |
| P2 推理成本 | `actualInferenceCost` 为 `NOT_MEASURED`，`realApiCostUsd: null` | 与预算口径 `state.costUsd` 分列，严禁合并 |
| P3 walk-forward | `oosPass: 0` **但** 同时标注 `label: SYNTHETIC_OOS_DRILL`、`overallVerdict: INSUFFICIENT_DATA` | 「确实跑了 0 个样本通过」+「样本不足」，两个事实同时说清 |

注意 P3 这一行的区别：`oos_pass: 0` 是**真实测得的 0**（跑了，无一通过），不是「没测到」。两者都写 0 才是灾难，所以必须带 `INSUFFICIENT_DATA` 标签。

---

## 6. F-16 已知运维噪声：`reports/evidence/v16/*.json` 会变脏

### 6.1 现象

重跑 `tests/v16/run-all.mjs` 后，**17 个** `reports/evidence/v16/*.json` 文件会显示为 ` M`（已修改）。

### 6.2 原因（已定性）

| 噪声源 | 处数 |
|---|---|
| 时间戳（`startedAt` / `finishedAt` / `capturedAt`，8 组） | 24 处 |
| `stderr` 内嵌的 node 进程 PID（如 `(node:10816) ExperimentalWarning: SQLite ...`） | 10 处 |

**语义零变化**：归一化时间戳与 PID 后，diff 的 `+`/`-` 行完全成对；含 `verdict` / `pass` / `detail` / `checks` 的变更行为 **0**。即「字节不可复现，结论可复现」。

来源：`reports/JEV_GAP_CLOSURE.md` §4.8.2 F-16。

### 6.3 值班者的正确反应

| 做法 | 是否允许 |
|---|---|
| 先看 diff 是不是只有时间戳 + PID | **必须做** |
| 确认 `verdict` / `pass` / `detail` / `checks` 无变化后，判定为噪声 | **应该做** |
| `git checkout -- reports/evidence/v16/` 丢掉这些改动 | **不做**。这会把「确实跑过」的痕迹抹掉，也掩盖真实回归 |
| `git add` 提交这 17 个文件 | **不做**。F-16 未修复，提交只会制造无意义 diff |
| 因为工作树脏就判定「有人改了代码」 | **不做**。先按上表排除噪声 |

### 6.4 判别命令（只读）

```bash
git status --porcelain reports/evidence/v16/
git diff -- reports/evidence/v16/ | grep -E '^[+-]' | grep -vE 'startedAt|finishedAt|capturedAt|node:[0-9]+'
```

第二条命令若**无输出**，即证明改动只含时间戳与 PID（噪声）；若**有输出**，必须逐条看清楚——那可能是真回归，不是噪声。

### 6.5 边界

F-16 是**未修缺陷**（medium），不是「已接受的正常行为」。它的实际危害是：任何重跑都会永久弄脏工作树，使「tree clean」类门禁失效。修复需改 v16 证据 harness 的 stderr 采集方式，不在 P0–P7 范围。**不得因为它「不影响结论」就当它不存在。**

---

## 7. 未跟踪 scratch：`reports/day14-artifacts/` 下三类路径

### 7.1 清单

| 路径 | 规模 | 性质 |
|---|---|---|
| `reports/day14-artifacts/_verify/` | 约 1.5 MB / 401 文件 | 本地校验运行产物 |
| `reports/day14-artifacts/runs/` | 约 84 KB | 按 runId 分目录的运行产物 |
| `reports/day14-artifacts/active-run.json` | 单文件 | 当前 run 指针 |

### 7.2 为什么是 untracked

冻结快照原文：**「本地运行 scratch，刻意保持 untracked 而非写进 `.gitignore`——让它可见，而不是被藏起来。」**（`reports/JEV_FINAL_MASTER_STATUS.json` §notCommittedOnPurpose）

这不是遗漏，也不是忘了加 `.gitignore`。把未跟踪文件**显式暴露**在 `git status` 里，是一种刻意的可观测性选择。

### 7.3 值班者行为

| 做法 | 是否允许 |
|---|---|
| 看到 `?? reports/day14-artifacts/_verify/` 等三条 | 正常，无需处理 |
| `git add reports/day14-artifacts/` | **不做**（会把 401 个 scratch 文件带进仓库） |
| 手工 `rm -rf` 清理 | 先确认无正在进行的运行；默认不清理 |
| 把它写进 `.gitignore` | **不做**（会把它藏起来，违背设计意图） |

---

## 8. 紧急止损

### 8.1 红线（维持不解除）

| 开关 | 值 | 位置 | 谁能改 |
|---|---|---|---|
| `allowBuySell` | `false` | `day14-runner.mjs` 的 `RED_LINES` 模块常量；`config.freeze.json` | **任何人都不能**，是编译期常量，不在 `runDay14(opts)` 解构内，外部不可覆盖 |
| `enableRealTrading` | `false` | 同上；cloud/worker 层另有 `ENABLE_REAL_TRADING` 环境变量强制 `'false'`（非 `false` 即 500 `RED_LINE`） | 同上 |

来源：`reports/JEV_REAL_TRADING_SECURITY.md` §6.2。

### 8.2 硬性规定

1. **任何情况下不得为了「让流程跑通」而打开这两个开关。** 「跑不通」是本轮设计的正确结果，不是故障。
2. 真实资金运营 = **NO-GO**，须用户单独明确授权；本手册任何内容都不构成该授权。
3. P9 实盘准备为 **DESIGN ONLY**：不创建实盘账户、不注入资金、不执行真实买卖。
4. 若发现有人（含自动化脚本、测试钩子）在试图解除红线：**立即停止该动作并上报**，不要「先跑通再解释」。

### 8.3 触发止损时的动作

| 步 | 动作 |
|---|---|
| 1 | 确认 `allowBuySell` / `enableRealTrading` 仍为 `false`（读代码 + 读 `config.freeze.json`） |
| 2 | 若有 P6 Supervisor 进程在跑（当前不存在），令其进入 `killSwitch` 停机态；注意该态**不自动清除** |
| 3 | 保留现场：不要 `git checkout`、不要删 scratch、不要重跑覆盖证据 |
| 4 | 按 §3 校验锚点，判断是否有非授权改动 |
| 5 | 上报，附上 §2 套件输出 + §3 锚点比对结果 |

---

## 9. 值班检查表

每次值班（或每次交接）按顺序走一遍：

| # | 检查项 | 通过标准 | 不通过 |
|---|---|---|---|
| 1 | 确认分支与 HEAD | 分支 = `feature/v16-p0-gap-closure`；`git rev-parse HEAD` 与交接记录一致（编写本手册时实测为 `c0cad254…`） | 停止，查是否有人切换分支 |
| 2 | 跑 7 个套件（§2） | 94 项断言 / exit 全 0 | 停止 |
| 3 | 校 5 个锚点（§3） | 全部逐字匹配 | 停止，按 §3.3 |
| 4 | 看工作树 | 仅有 §7 的三条 `??`，以及可能的 §6 噪声 | 其他 ` M` 需逐个查明 |
| 5 | 若见到 `reports/evidence/v16/*.json` 为 ` M` | 按 §6.4 判别，仅时间戳+PID 则记噪声 | 有实质 diff → 上报 |
| 6 | 确认红线 | `allowBuySell=false` / `enableRealTrading=false` | 立即止损（§8） |
| 7 | 记录 | 写下「跑了几套件 / 锚点是否匹配 / 有无噪声 / 有无异常」 | — |

**交接时必须说清的三件事**：① 谁在什么时间跑了哪些套件；② 锚点是否漂移；③ 有没有任何未解释的 diff。**不要用「一切正常」代替这三条**——那不是证据。

---

## 10. 本手册不覆盖的内容

| 未覆盖 | 原因 |
|---|---|
| 真实行情接入后的巡检 | 真实行情未接入，全部为 fixture |
| 真实 AI provider 的可用性巡检 | 零真实推理，`providerId` 恒为 `mock` |
| 24/7 无人值守值守流程 | 守护进程不存在（§4） |
| 告警阈值与升级路径 | 无告警通道证据，`NOT_MEASURED` |
| 容量 / 成本趋势 | 无长期运行数据，P5 BLOCKED |
| 数据库运维 | P1 记录全部落文件（`storeKind: file`，`dbPersisted: false`）；`ai_market_contexts` 表未建 |

### 10.1 尚未接线的能力（`DECLARED_NOT_WIRED` 清单）

| 能力 | 状态 | 不得读作 |
|---|---|---|
| P6 Supervisor 守护进程 | `DECLARED_NOT_WIRED` | 「已 24/7 运行」 |
| P7 TradingDesk 前端挂载 | `DECLARED_NOT_WIRED` | 「驾驶舱已上线」 |
| P2 provider 清单接线 | 只声明未接线 | 「多模型已接入」 |
| P1 `ai_market_contexts` D1 表 | 只声明未建 | 「数据已落库」 |
| P2 `MODEL_ID_AUTHORITY` | 只声明未接线 | 「模型 ID 已受控」 |

---

## 11. 溯源表

| 本手册论断 | 权威来源 |
|---|---|
| 94 项断言 / 7 套件 / exit 全 0 | `reports/JEV_FINAL_MASTER_STATUS.json` §verification |
| 5 个冻结锚点 sha256 | 同上 §frozenAnchors；`reports/JEV_GAP_CLOSURE.md` §4.8.1 有 10 项清单 |
| P6 `DECLARED_NOT_WIRED` / 编排顺序 / 无守护进程 | 同上 §phases.P6；`apps/command_center_3d/scripts/supervisor/supervisor.mjs` 的 `DAEMON_BINDING`、`STAGES` |
| F-16 噪声（17 文件 / 24 时间戳 / 10 PID） | `reports/JEV_GAP_CLOSURE.md` §4.8.2 |
| 未跟踪 scratch 三条路径与理由 | `reports/JEV_FINAL_MASTER_STATUS.json` §notCommittedOnPurpose |
| 红线值 `allowBuySell` / `enableRealTrading` | `reports/JEV_REAL_TRADING_SECURITY.md` §6.2 |
| `UNKNOWN ≠ 0` 纪律与反例 | `reports/JEV_GAP_CLOSURE.md` §2 口径纪律；`reports/JEV_FINAL_MASTER_STATUS.json` §hardDeclarations.note |
| 引用稳定性（章节号 > 行号） | `reports/JEV_REAL_TRADING_SECURITY.md` §10.9.1；`reports/JEV_GAP_CLOSURE.md` §4.6 DEV-06 |
| QA 条件 C2（runner 不得再改动） | `reports/JEV_INDEPENDENT_QA.md` §9 |
| Security 条件 C7（版本锁定） | `reports/JEV_REAL_TRADING_SECURITY.md` §10.4 |

---

*本手册所有数字均取自上述来源，未重跑套件、未做任何推断补写。凡是来源中没有的，一律标注 `NOT_MEASURED`，不以 0 代替。*

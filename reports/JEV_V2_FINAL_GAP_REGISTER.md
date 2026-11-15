# JEV V2.0 最终缺陷台账（T06）

> 本台账汇总本轮（P0–P7 收口）全部已知缺陷与缺口，含**未修复**者。
> 未修复不等于可忽略——每条都写明「它会让什么结论失效」。
>
> 锚定提交 `f5fea0943d10a98e8e4629683c320e418a9d0136`｜Draft PR #7

---

## 0. 台账总览

| 级别 | 数量 | 其中本轮新发现 |
|---|---|---|
| high | 0 | 0 |
| medium | 6 | 3（F-18 / F-19 / F-21） |
| low | 4 | 2（F-22-C / F-23） |
| 流程偏差 | 3 | 2（DEV-09 / DEV-10） |
| 覆盖面缺口 | 3 | 1（CI 触发分支） |

**没有任何一条 high。** 但有两类「medium」必须在进入 Shadow 前解决，否则后续结论会建立在错误前提上：F-19（锚不生效）与 F-14/F-15（风控口径失真）。

---

## 1. 已修复（本轮闭合）

| ID | 原问题 | 修复 | 验证 |
|---|---|---|---|
| P0-A | 正式风控依赖未注入，可被绕过 | `day14-runner.mjs` 改为 fail-closed import | 远端日志：`formal risk-gate import failed — fail-closed termination` |
| P0-B | 导入失败后 warn + 简化风控放行 | 同上，直接 throw | 远端日志：旧基线 `warn+continue...approve via simplified fallback` vs 新实现终止 |
| P0-C | 日志链无校验、异常不可恢复 | `verifyChainFile` + recovery | 篡改可检出（**除 §2 F-19 的例外**） |
| P0-D | 无真实对账 | 对账用例 | `day14-p0-runner` 24 断言 |
| P0-E | runId 与配置可变 | 不可变配置 + runId | `day14-p0-contracts` 7 断言 |
| P0-F | 无独立 QA | 独立 QA 独占测试文件 | 31/31 PASS |
| — | **vitest 确定性变红** | `vite.config.ts` `test.exclude`（展开 `configDefaults.exclude`） | 本地 22 文件 / 217 passed；远端同值 |
| — | **CI gate 只覆盖 5/7 套件**（24 条断言从未进 CI） | gate 补齐为 7/7 | 远端日志逐套件 94 条 |

---

## 2. 未修复缺陷（按严重度）

### F-19（medium，本轮新发现）— 日志链锚只写不读

**现象**：`config.freeze.json` 与 `day14-anchor.json` 里的 `chainHeadSha256` 在恢复路径**从未被读取**。篡改者若同时改写账本与 `run.json.chainHeadSha256`，检测会通过。

**它让什么失效**：「日志链可检测篡改」这一表述在**严格意义上不成立**。当前 `hashChainVerification` 提供的是**链内自洽性**（intra-chain consistency），不是**对外不可否认性**（external non-repudiation）。

**定级说明**：仍记 medium 而非 high——因为攻击者需要已具备写权限才能篡改，且链内自洽检查仍能抓住**只改中间记录**的低成本篡改。但**不得**把它宣传为「不可篡改审计」。

**修复方向**：恢复路径必须读取锚并比对；锚应落在链外（独立文件 / 只追加的锚日志）。

### F-18（medium）— 证据跨批次不可复现

**现象**：证据行的 payload hash 覆盖了 `latency_ms`（墙钟时延）。同一进程内 3 次运行一致（故 T2 断言不假），但**跨批次必然不同**。

**它让什么失效**：任何「按哈希跨环境比对证据」的做法（本地 vs CI、今天 vs 昨天）都会假阳性。**同批次内可比，跨批次不可比。**

**已做的缓解**：`_normalized/` 归一化视图剥离时间字段与 PID，verdict 自检 PASS，8 组配对全 IDENTICAL。但归一化**不改变** `latency_ms` 仍在哈希里这一事实。

**修复方向**：行哈希覆盖的列应排除墙钟派生值，或显式声明 `hash_scope` 并分离「性能字段」与「事实字段」。

### F-14（medium，前序遗留）— `LEGACY_FREEZE_EXPECTED` 是死配置

**现象**：`day14-runner.mjs` L66-70 的 200 / 6 / 0.2 / 1 / 2 仅被 `verifyConfigFreeze()` 使用；实际生效的是 `DEFAULT_RISK_LIMITS.maxNotionalUsd: 1000`。

**它让什么失效**：任何基于 `LEGACY_FREEZE_EXPECTED` 的「风控上限 = 200」推断都是错的，真实上限是 1000。

**状态**：**P3 解锁硬前置，本轮未修。**

### F-15（medium，前序遗留）— `s.riskBreach` 恒 false

**现象**：风控违规标志永远为 false；`riskCheckImpl` 注入面未锁定。

**它让什么失效**：「发生过几次风控拦截」这类统计无法从该字段得到，读它只会得到恒假的信号。

**状态**：**P3 解锁硬前置，本轮未修。**

### F-21（medium，本轮新发现）— gate 步骤位置导致「门禁最先缺席」

**现象**：`ci.yml` 的 gap-closure gate 排在 typecheck / build / unit 之后。修复前的 Run `42968141252` 中，unit 一挂，gate 直接 `skipped`——94 条断言一条没跑。

**它让什么失效**：不制造假绿（整体已 failure），但意味着**最关键的门禁在最需要它的时候不执行**，且一次运行的证据完整度取决于上游步骤是否通过。

**修复方向**：gate 前移至 job 第一步。

### F-22-C（low，本轮新发现）— V1.6 溯源门禁未覆盖集成分支

> **编号冲突说明**：旧 CI 评估报告里的 `F-22` 指另一件事（配置文件与工作树指纹一致性，记为 `F-22-G`）。本轮新增本条记为 **`F-22-C`**（C = coverage）。两者不是同一个缺陷，引用时必须带后缀。

**现象**：`v16-market-provenance.yml` 的 `on.push.branches` = `["feature/v16-market-provenance-hardening", "feature/v16-p0-gap-closure", "feature/v16-**"]`，不匹配 `feature/jev-v2-ops-integration`。同理 `ci.yml` 的 `on.pull_request.branches` 只有 `["main", "master"]`，本 PR 的 PR 事件也不会触发 `ci.yml`。

**它让什么失效**：本分支上 V1.5 baseline 8 组 + V1.6 新套件（104 断言）**未运行**；CI 覆盖率在该维度上低于 `feature/v16-p0-gap-closure`。

**修复方向**：两处触发分支各补一条（`feature/jev-**`）。本轮刻意未改，避免再次扩大提交面。

### F-23（low，本轮新发现）— 静态断言文案与实际 gate 数量脱节

**现象**：`check-no-fake-live.mjs` 的 Y2 断言叙述仍写「5 gate scripts」，而 gate 现为 7。它本身 10/10 通过——脱节在**文案**，不在检查逻辑。

**它让什么失效**：读者若只读断言文案会误判 CI 覆盖范围。

### F-17（low）— 测试硬编码历史基准

**现象**：`day14-p0-runner` 的 B1 用例硬引用 `ebc3fcc`。`fetch-depth: 0` 只消除了浅克隆导致的假红，**没有消除硬编码**。

**它让什么失效**：仓库迁移 / 历史重写 / 该提交被 gc 后，该用例会失效。**且失效方式是 throw，不是 skip**——这是好的（不会静默放过），但需要显式版本化基线引用。

### F-16（medium）— 证据跨次运行不可复现

**现象**：时间戳 + `stderr` 内嵌 `node:<PID>`。已被 F-18 覆盖并部分缓解（归一化视图）。

### F-05（medium，前序遗留）— 无独立凭据域

**现象**：凭证域与业务域未隔离；历史 MiniMax 凭据位置 `UNVERIFIED`。

**它让什么失效**：无法保证「模型调用失败不会牵连交易侧凭据」。

### R6（UNVERIFIED，静态结论）— 保持不拔高

安全审计的 R6 静态结论仍为 `UNVERIFIED`。**不得**因本轮 CI 变绿而改写成 `VERIFIED`——两者证明的是不同命题。

---

## 3. 流程偏差

| ID | 级别 | 内容 | 责任 |
|---|---|---|---|
| DEV-09 | low | 报告写 `5834c3c`，git 中不存在该对象（合法短 SHA 为 `5834c37`） | 主理人 |
| DEV-10 | medium | 集成分支建在 `fa7740f`，第 4 提交落在 `feature/v16-p0-gap-closure`，首次推送只推 3 个提交，远端一度停在会红的 SHA | 主理人 |
| DEV-11 | low | 集成签收文档与台账初版把 4 次提交增量直接相加，写成「121 文件 / +17148 / −334」，实测累计为 **106 文件 / +18344 / −280** | 主理人 |
| DEV-12 | low | `JEV_V2_CI_VERIFICATION.md`（QA 独占）正文仍写「无 Run ID / UNVERIFIED」，与推送后的现实矛盾。主理人做了**最小介入**：仅在文件头追加取代声明并指向权威记录，正文一字未改 | 主理人 |
| DEV-08 | medium | 误将 `ReferenceInput.url` 当作 JEV 安全底线下发（本仓 0 命中），已撤回并删除 | 主理人 |

**四条都是主理人自身错误，已全部当场纠正并在台账留痕。** 成员未出现伪造 PASS 或删断言行为。

> DEV-11 值得单独说一句：它正是「数字不能靠算术推，只能靠命令量」的又一个例子。同一文件被二次修改时，累计 diff 只计净变化，逐次相加必然偏大。

---

## 4. 明确的能力边界（`DECLARED_NOT_WIRED`）

以下能力**有名字、无调用者**，任何「已启用」的推断都是错的：

| 能力 | 常量 | 实际状态 |
|---|---|---|
| P6 守护进程 | `DAEMON_BINDING = "DECLARED_NOT_WIRED"` | 无 daemon、无 scheduler、无告警通道 |
| P7 驾驶舱挂载 | `TRADING_DESK_MOUNT = "DECLARED_NOT_WIRED"` | 组件存在，未挂载 |
| P2 provider 清单 / `MODEL_ID_AUTHORITY` | 只声明未接线 | `providerId` 恒 `mock`，零真实推理 |
| P1 `ai_market_contexts` | D1 表未建 | `db_persisted: false` |

---

## 5. 口径纪律（本轮新增/强化）

| 纪律 | 内容 |
|---|---|
| `UNKNOWN ≠ 0` | 缺失记 `NOT_MEASURED` / `—`，禁 `?? 0` / `\|\| 0` / 「0 个」 |
| 双口径成本不可合并 | `engine_conservative` vs `venue_verified`（后者保持 `null` + `NOT_MEASURED`） |
| **exit code 不是证据** | `node tests/t1_multi_cycle.mjs` 单独跑 exit 0 但**零断言零输出**；真实入口是 `run-all.mjs`。必须同时看断言数与 `RESULT_JSON` |
| **真实行情已非 0** | CI 实测 `LIVE(real Binance): total=4 passed=4`。旧「0 真实行情样本」口径作废 |
| 引用锚点 | 唯一稳定锚 = 章节号 + 逐字证据内容；sha256 / 行数 / 裸行号只是版本快照 |

---

## 6. 进入下一阶段的硬性前置

任何一项未完成，**不得**启动 Live Market Shadow：

1. F-19 修复（锚必须被读取）
2. F-14 / F-15 修复（P3 解锁）
3. F-21 gate 前移
4. F-22-C 触发分支补齐（`v16-market-provenance.yml` 与 `ci.yml` 的 PR 触发各补一条）
5. 独立凭据域（F-05）
6. 单独授权的 Shadow 环境（禁止自动开启持续付费任务）

---

*本台账不含任何未实测的数字。凡未测量处均标 `NOT_MEASURED` / `UNVERIFIED`。*

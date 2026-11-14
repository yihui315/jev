# JEV 真实交易安全 —— 索引页（SECURITY INDEX）

> **权威源不是本文件。**
> 权威源 = **`reports/JEV_REAL_TRADING_SECURITY.md`**（595 行，sha256 `259bc65f6febc0adf22facfb15b602bf965666b7f772c5c6ddd684a0344940e4`）。
> **本文件只是索引 + 摘要，与权威源冲突时一律以权威源为准。**
>
> 为什么不在 `docs/` 下再存一份全文：同一内容存两处必然漂移。本仓已真实发生过引用锚点漂移事故（`reports/JEV_GAP_CLOSURE.md` §4.6 DEV-06），也发生过「拿旧快照当现行结论」的口径混乱（同文件 DEV-05）。因此 `docs/` 下的交付文档一律采用「索引 + 摘要 + 指向权威源」形态，不得复制 `reports/` 正文。

---

## 0. 结论先行

| 项 | 值 |
|---|---|
| **Security DECISION** | **`GO WITH CONDITIONS（仅限 Security 范围）`** |
| 已满足条件 | **C1、C7** |
| 仍开放条件 | **C2、C3、C4、C5、C6**，以及 **C8（边界声明）** |
| R6（本轮实际外部调用 = 0） | **保持 `UNVERIFIED`**（静态结论未升级） |
| 真实资金 | **NO-GO** |
| 生产 GO | **未宣布** |
| 首轮结论 | `NO-GO`（原文保留在权威源 §0–§9，属实现未落盘时点的历史结论，**不得当作当前结论引用**） |

**DECISION 的适用范围（务必逐字理解）**：仅表示 **P0 阶段 Security 代码边界**（fail-closed / 依赖注入 / 默认 fixture / 证据链）复核通过。**不等于** P0 整体 PASS、**不等于**可晋级 P1、**不等于**可解除 P9、**不等于**生产 GO、**不替** `independent-qa` 或 `trading-engineer` 签字。

依据：`reports/JEV_REAL_TRADING_SECURITY.md` §10.5、§10.5.1、§10.9.5。

---

## 1. 条件状态表

条件定义见 `reports/JEV_REAL_TRADING_SECURITY.md` §10.4；状态更新见 §10.5.1。

| 条件 | 关联缺陷 | 状态 | 含义 |
|---|---|---|---|
| **C1** | F-12 / R6 | **已满足** | 他方书面确认（两条彼此独立的证据）已经 team-lead 中转到位，晋级前置条件已满足。**注意：这不等于把安全侧静态结论升级为 VERIFIED** |
| **C2** | F-05 | **仍未闭环** | §9 独立凭据域未落地；P9 维持 DESIGN ONLY；真实运行前须建立独立凭据域（不复用 `cf_*` / `github_pat_*` 管理凭据）+ 最小权限 / 提币禁用证据 |
| **C3** | F-15 | **仍未闭环** | `riskCheckImpl` 注入面允许调用方替换整个风控门；P3 解锁 `allowBuySell` 前须锁定不可注入，或对 `gate.code` / provenance 做二次独立校验 |
| **C4** | F-14 | **仍未闭环（P3 解锁硬前置）** | 风控参数口径 200 vs 1000 冲突；`s.riskBreach` 恒 `false`；日亏损 / 回撤 / 持仓 / 最大交易数 breach 未参与判定 |
| **C5** | F-07 能力面 | **仍未闭环** | 冻结引擎 handler 的 `dbFetch` → 全局 `fetch` → `data-api.binance.vision`（FORBIDDEN 主机）+ 历史真实外呼脚本，P3 前须收敛或证明不可达 |
| **C6** | F-09 残留 | **仍未闭环（low）** | anchor / summary / recovery / state 在 `runs/<runId>/` 内为覆盖写；如需同 runId 全量锚点留存须改 append-only 或分文件 |
| **C7** | 版本锁定 | **已满足** | 最终锚点 `002653fed0d48…`（`day14-runner.mjs`，829 行）经独立 `sha256sum` 核实未漂移，无需重新触发复核 |
| **C8** | 不越权 | **边界保留** | 不替 `independent-qa` 签字、不评价 QA 测试质量、P0 整体门禁由 team-lead 依 `reports/JEV_GAP_CLOSURE.md` §4 整合、不宣布 P0 整体 PASS |

### 1.1 关于 R6 的准确表述

R6 = 「本轮实际外部调用 = 0」。这是一个**静态只读审计无法证明**的命题（无法证明「全仓库任意进程在运行期零外呼」）。

- 已有证据三条，**彼此独立、不得互相替代、不得平均**：① 实现方书面声明；② QA 侧运行时动态计数（仅覆盖 QA 进程树）；③ 安全侧静态只读（保持 UNVERIFIED）。
- QA 侧动态计数实测：禁网守卫 = `true`、33 个沙箱本地 node 子进程、`P0-QA-NONET-ATTEMPT` stderr 命中 = 0。
- **结论：C1 条件已满足，但 R6 本身仍为 `UNVERIFIED`。**

依据：`reports/JEV_REAL_TRADING_SECURITY.md` §10.1 R6 及其第三轮补注、§10.2 F-12/R6 行。

---

## 2. 两大安全底线

| # | 底线 | 状态 | 依据 |
|---|---|---|---|
| 1 | `allowBuySell = false` **未解除** | 成立 | 模块常量 `RED_LINES`，不在 `runDay14(opts)` 解构内，外部不可覆盖；`config.freeze.json` 同值 |
| 2 | 真实资金 **NO-GO** | 成立 | P0 全程未接触真实资金、未创建实盘账户、未注入资金、未提交订单 |

补充：`enableRealTrading = false` 同样未解除；cloud/worker 层另有 `ENABLE_REAL_TRADING` 环境变量强制 `'false'`（非 `false` 即 500 `RED_LINE`）。详见 `reports/JEV_REAL_TRADING_SECURITY.md` §6.2。

> 这两条红线是**代码事实**，不是配置建议。任何「为了跑通流程」而解除的动作都属违规，见 `docs/JEV_OPERATIONS_RUNBOOK.md` §8。

### 2.1 勘误：曾有第三条「`ReferenceInput.url` 仅记录不触网」，实为**误置**，已删除

本页初稿按主理人下发口径列出过第三条底线「`ReferenceInput.url` 仅记录、不触网（禁止爬虫 / CAPTCHA bypass）」。经全仓检索（`*.md` / `*.mjs` / `*.ts` / `*.tsx` / `*.py` / `*.json`，含 `reports/JEV_REAL_TRADING_SECURITY.md` 全文），**标识符 `ReferenceInput` / `reference_input` 命中 0 处**。

| 项 | 事实 |
|---|---|
| 该字段是否存在于本仓 | **不存在**。代码、配置、权威安全报告、门禁台账中均无此标识符 |
| 该条底线是否属于 JEV | **不属于**。它是主理人从另一条业务线（短视频增长系统）的约束中误抄过来的，与本仓 V1.6 / JCC 无关 |
| 唯一被误引的相关文字 | `JEV_CLOUDFLARE_STAGING_ACCEPTANCE.md` 的「未引入第二账本/行情系统」段：「无新定价、无新凭证、无爬虫/CAPTCHA 绕过」。该段属 staging 验收范围，**与本命题无关** |
| 处置 | **整条删除**，不保留「待补证据」占位 |

**为什么删除而不是保留待补位**：保留一个「待补」条目，会让读者以为 JEV 确实存在这条安全要求、只是尚未取证，从而去追一个不存在的东西。**误置的事实应当撤销，不应当降级保留。**

**登记**：此项作为 `DEV-08`（主理人跨业务线概念污染，责任在主理人）记入 `reports/JEV_GAP_CLOSURE.md` §4.9。发现者是本页撰写成员，其在无法取得出处时拒绝编造章节号并如实上报，处理正确。

> 教训与 `DEV-06` 同源：**没有逐字证据的引用，宁可缺，不可编。**

---

## 3. `DECLARED_NOT_WIRED` 的安全含义

**「已声明」不等于「已启用」。** 以下四项都是「代码里有这个名字，但没有任何东西调用它」：

| 能力 | 状态 | 若误读为「已启用」会怎样 |
|---|---|---|
| P6 Supervisor 守护进程 | `DECLARED_NOT_WIRED` | 会误以为有 24/7 自动运行与自动恢复；实际无守护进程、无 scheduler，killSwitch / lease / resume 只是内存中可验证的骨架 |
| P7 TradingDesk 前端 | `DECLARED_NOT_WIRED` | 会误以为驾驶舱已挂载上线；实际未挂载 |
| P2 provider 清单 / `MODEL_ID_AUTHORITY` | 只声明未接线 | 会误以为多模型已接入、模型 ID 已受控；实际 `providerId` 恒为 `mock`，零真实推理，provider 契约与 `ALLOWED_ENDPOINTS` 扩容明确推迟 |
| P1 `ai_market_contexts` D1 表 | 只声明未建 | 会误以为数据已落库；实际 `storeKind: file`、`dbPersisted: false` |

**安全判读规则**：看到 `DECLARED_NOT_WIRED` 时，一律按「**不存在**」处理其运行时效果，但按「**存在设计意图**」记录其验收债。

依据：`reports/JEV_FINAL_MASTER_STATUS.json` §phases.P1/P2/P6/P7；`reports/JEV_GAP_CLOSURE.md` §5「阻塞项」末段。

---

## 4. 与安全相关的未修缺陷

### 4.1 三个必须记住的

| ID | 等级 | 内容 | 为什么与安全有关 |
|---|---|---|---|
| **F-05** | high | 独立凭据域缺失。凭据库现有键仅 `cf_*` / `github_pat_*` / `jev_paper_ledger_secret` / `jev_preview_*` / `r2_*` / `x_*`，**无 LLM / 交易 Provider 键**；历史 MiniMax M3 真实调用的凭据存储位置 `UNVERIFIED` | 一旦进入真实运行，若沿用现有管理域凭据即违反「独立凭据域、不复用 WorkBuddy/GitHub/Cloudflare 管理权限」。P0 语境为非 blocker（不接触真实凭据/资金），**P3 解锁语境下仍是 blocker** |
| **F-16** | medium | `reports/evidence/v16/*.json` 跨次运行不可复现（17 文件；24 处时间戳 + 10 处 stderr 内嵌 node PID） | **完整性影响**：语义零变化但字节不可复现 → 任何重跑都会弄脏工作树，使 `tree clean` 类门禁失效。证据不可复现本身就是证据链弱点 |
| **F-17** | low | `day14-p0-runner.mjs` 的 B1 可证伪性用例硬编码基准提交 `ebc3fcc` | 若 rebase / squash / 换 base 使该提交不可达，B1 会抛错 → 要么稳定假红（噪声淹没真问题），要么诱使有人为了「让测试过」而弱化断言 |

### 4.2 其余安全相关残留（一律未修）

| ID | 等级 | 内容 |
|---|---|---|
| F-07 | high（能力面）/ medium（注入） | 仓库具备真实付费模型 / 真实行情 / 全局 `fetch` 直连能力；runner 链路已闭环绕过，但能力面仍在 |
| F-09 | high（主体已闭环）/ low（残留） | 哈希链跨重启已修复（启动全量校验 + 重算 `_hash` + 恢复末尾哈希）；残留为 runId 内 anchor/summary/state 覆盖写 |
| F-14 | medium | 风控口径双源（`LEGACY_FREEZE_EXPECTED` 死配置 vs 实际 `maxNotionalUsd:1000`；`s.riskBreach` 恒 `false`） |
| F-15 | medium | `riskCheckImpl` 注入面未锁定 |
| CRED-MINIMAX | medium | 历史 MiniMax 凭据位置 `UNVERIFIED` |
| CI 覆盖缺口 | — | 曾存在：`v16-market-provenance.yml` push 触发硬编码旧分支名。已按 `CONFIG-CHANGE / UNVERIFIED` 闭合（追加分支，原条目未动），**未经真实 runner 验证** |

> 已闭环项（F-01 / F-02 / F-03 / F-04 / F-08 / F-13）的逐项证据见 `reports/JEV_REAL_TRADING_SECURITY.md` §10.2 状态表。**已闭环不等于可删记录**——该表要求保留全编号。

---

## 5. 引用稳定性声明

> **唯一稳定锚点 = 章节号 + 逐字证据内容。**
> **sha256 / 行数 / 字节数 / 裸行号一律只是某一版本的快照，不得单独作为引用锚点。**

| 引用方式 | 是否稳定 | 说明 |
|---|---|---|
| 「`reports/JEV_REAL_TRADING_SECURITY.md` §10.4」 | **稳定** | 章节号不随追加而漂移 |
| 「§10.4 中 C4 的逐字条件文字」 | **稳定** | 逐字内容可跨版本比对 |
| 「`reports/JEV_REAL_TRADING_SECURITY.md` 第 511 行」 | **不稳定** | 报告一追加即漂移 |
| 「sha256 `259bc65f…`」 | **仅快照** | 本页开头引用它，只为标明「本索引基于哪一版权威源」，不构成结论本身 |
| 「595 行」 | **仅快照** | 同上 |

真实证据：权威源自身在 §10.9.3 记录了「中转快照指纹 `5aa0c66a…` / 263 行」与「本轮实测 `cc0ab7d8…`」等多次漂移，并明确「不抹平、不择一冒充」。

### 5.1 本页的引用纪律

- 本页**所有**引用一律使用「文件名 + 章节号」；不复制权威源正文段落——如需逐字引用（如条件 C4 原文），请直接打开权威源对应章节。
- 若本页摘要与权威源冲突 → **以权威源为准**，并按流程修正本页（不得反过来改权威源去迁就索引页）。

---

## 6. 证据等级口径

| 等级 | 含义 | 不得如何误用 |
|---|---|---|
| `VERIFIED` | 本轮实读源码 / 实跑本地只读命令得到的结论 | 不得用于「未实读」的事项 |
| `UNVERIFIED` | 无法核实，或实现未落盘 | **不得改写成 `VERIFIED`** |
| `COMMUNITY-VERIFIED` | 转引外部多来源一致信息，非本轮一手核实 | 不得作为 GO 依据 |
| `NOT_MEASURED` | 未测 | **绝不用 `0` 冒充** |

特殊说明：本仓 `VERIFIED` 是**保留的 provenance 词**（门禁矩阵用它表示数据真实性核验，`walk-forward` 用 `verified:0` 表示零样本通过）。因此 P4 费率契约的自测状态不借用该词，已按裁定改为 `FEE_CONTRACT_MATCHES_FROZEN_ENGINE` + `provenance: "SYNTHETIC_FIXTURE"`，避免 grep 式审计撞车。

依据：`reports/JEV_REAL_TRADING_SECURITY.md` §8、§10.6；`reports/JEV_GAP_CLOSURE.md` §4.8 裁定 B。

---

## 7. 本页不能支撑的结论（禁止清单）

不得据本页（或据权威源）作出以下任何陈述：

| # | 禁止陈述 | 真相 |
|---|---|---|
| 1 | 「安全已全部通过」 | 仅 Security 范围内 `GO WITH CONDITIONS`；C2–C6 与 C8 边界仍在 |
| 2 | 「P0 整体 PASS」 | P0 = `CLOSED WITH CONDITIONS`，整体判定权在 team-lead |
| 3 | 「生产 GO」 | 未宣布，且明确不构成该授权 |
| 4 | 「真实资金 GO」 | **NO-GO**，须用户单独明确授权 |
| 5 | 「CI PASS」 | `CONFIG-CHANGE / UNVERIFIED`，无 Run ID |
| 6 | 「R6 已 VERIFIED」 | 仍为 `UNVERIFIED` |
| 7 | 「F-05 已解决」 | 维持 DESIGN ONLY 语境；进入真实运行前仍是 blocker |
| 8 | 「可以解锁 allowBuySell 做 P3」 | C3（F-15）与 C4（F-14）是 P3 解锁硬前置，均未闭环 |

---

## 8. 索引表：本页每个论断 → 权威源位置

| 本页论断 | 权威源章节 |
|---|---|
| DECISION = `GO WITH CONDITIONS（仅限 Security 范围）` | §0、§10.5、§10.5.1、§10.9.5 |
| 首轮 NO-GO 原文保留、不得当现行结论 | §0（开篇说明）、§9.1；`reports/JEV_GAP_CLOSURE.md` §4.0.2 |
| C1 / C7 已满足；C2–C6 + C8 边界 | §10.4（条件定义）、§10.5.1（状态表） |
| R6 保持 UNVERIFIED、三条证据独立不平均 | §10.1 R6 + 第三轮补注、§10.2 F-12/R6 行、§10.6 |
| 安全底线 ①：`allowBuySell` / `enableRealTrading` 未解除 | §6.2（红线值核实与双轨说明） |
| 安全底线 ②：真实资金 NO-GO | §9.3（不扩大授权声明）、§10.5.1（保留的其它未闭环 / 红线项）；`reports/JEV_FINAL_MASTER_STATUS.json` §hardDeclarations |
| ~~安全底线 ③：`ReferenceInput.url` 仅记录不触网~~ | **已撤销**：该字段在本仓不存在，属主理人跨业务线误抄（`DEV-08`），见本页 §2.1。本仓安全底线只有两条 |
| F-05 独立凭据域 | §1.2、§1.3、§5 第 12 项、§10.4 C2 |
| F-16 / F-17 | `reports/JEV_GAP_CLOSURE.md` §4.8.2；`reports/JEV_FINAL_MASTER_STATUS.json` §defects.registeredNotFixed |
| F-14 / F-15 / F-07 能力面 / F-09 残留 | §10.2 状态表、§10.3 新增残留风险 |
| 已闭环 F-01/02/03/04/08/13 | §10.1 R1–R6 逐项、§10.2 状态表 |
| 引用稳定性声明（章节号 > sha256/行号） | §10.9.1、§10.9.2、§10.9.3 |
| 证据等级口径 | §8、§10.6 |
| `DECLARED_NOT_WIRED` 四项 | `reports/JEV_FINAL_MASTER_STATUS.json` §phases.P1/P2/P6/P7；`reports/JEV_GAP_CLOSURE.md` §5；provider 契约与 `ALLOWED_ENDPOINTS` 推迟见 `reports/JEV_GAP_CLOSURE.md` §4.7 R-2 |
| 保留 `VERIFIED` 作为 provenance 词的裁定 | `reports/JEV_GAP_CLOSURE.md` §4.8 裁定 B |

---

*本页不复制权威源正文，不新增任何未经来源支持的结论。凡来源未测者，标注 `NOT_MEASURED`，不以 0 代替。*

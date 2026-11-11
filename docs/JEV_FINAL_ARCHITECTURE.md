# JEV_FINAL_ARCHITECTURE.md — V1.6 / MASTER PLAN V2.0 最终架构（索引型）

> 性质：**索引 + 摘要 + 指向权威源**。本文不构成第二个真相源。
> 权威源（唯一，本文只转引、不回改）：
> - `reports/JEV_FINAL_MASTER_STATUS.json`（提交时点唯一冻结快照；简称 **MASTER_STATUS**）
> - `reports/JEV_GAP_CLOSURE.md`（门禁台账；简称 **GAP_CLOSURE**）
> - `reports/JEV_INDEPENDENT_QA.md`（独立 QA；简称 **QA**）
> - `reports/JEV_REAL_TRADING_SECURITY.md`（安全只读复核；简称 **SECURITY**）
>
> 分支 `feature/v16-p0-gap-closure`，快照 commit `5834c3745843baa33b82e04a746fa8691684b2f6`（MASTER_STATUS `snapshotPoint`），未 push。
> 引用纪律：**锚点 = 文件名 + 章节号**；sha256 / 行数 / 裸行号一律是版本快照，不作引用锚点（GAP_CLOSURE §4.6 DEV-06 的教训）。
> 口径纪律：`UNKNOWN ≠ 0`。凡来源未测得的，本文写 `NOT_MEASURED` 或「未测量」，不写 0。

---

## 0. 结论先行

1. 本仓库是**双栈仓库**，V1.6 交易运营主线与控制平面栈是两套不同工程，**不得混为一谈**（§1）。
2. `docs/` 下存在**编号撞名陷阱**：`docs/P3_P4_SPEC.md` 的 P3/P4 与 MASTER PLAN 的 P3/P4 毫无关系（§2）。
3. 十个阶段里，只有 P0–P2、P4、P6、P7 有真实落地代码；P3 是骨架且数据不足；P5/P8 阻塞；P9 仅设计（§3）。
4. 凡「声明权威来源但未接线」的四处，本文集中列在 §4，**不得读作已启用**。
5. 本文所述「本地直跑通过」只覆盖 7 套件 / 94 断言，**不含**类型检查与 CI（§7）。

---

## 1. 双栈事实（先分清工程，再读任何结论）

| 维度 | V1.6 交易运营主线 | JCC 控制平面栈 |
|---|---|---|
| 组成 | `apps/command_center_3d` + `apps/equity_collector_v45` | `packages/jcc_core` + `packages/supervisor` + `packages/operator_skill` + `apps/control_ui`（另有 `packages/mcp_facade`、`packages/observability`） |
| 语言/形态 | Node ESM（`.mjs`）为主 + React/TS 前端 + Vite 类前端工程 | **纯 Python**，含 `conftest.py` / `tests/` |
| 本轮角色 | P0–P8 的实现与验证对象 | **不是**本轮 P0–P9 的实现对象 |
| 与本轮关系 | 全部实现落点在此 | 仅可「接线 / 复用」的存量资产，本轮无新实现 |

事实依据：GAP_CLOSURE §1.3（增量发现 #5「仓库实为双栈」）；目录实测（`apps/control_ui` 共 16 个文件，`*.tsx`/`*.jsx`/`*.js` 计数为 0）。

**三条必须记住的边界：**

- 主理人裁定「不套用外部 `D:\jev` 控制平面 SOP」，**不等于**禁止复用本仓库内的 `packages/supervisor` / `apps/control_ui` 资产；P6/P7 应优先盘点「接线 / 复用」而非一律新建（GAP_CLOSURE §1.3）。
- 本文所有阶段结论只对 V1.6 交易主线成立；对 JCC 控制平面栈**不成立、不适用、不背书**。
- 两栈之间没有共享的运行时、没有共享的凭据域、没有共享的门禁判定。

---

## 2. 编号撞名陷阱（最容易造成「假委托」的一条）

`docs/P3_P4_SPEC.md`（其 §1「编号映射（唯一权威）」）把 **P3 = CR-H5**、**P4 = CR-H6**，两者实现位置均在 `packages/jcc_core/server.py`：

| 该文件的编号 | 别名 | 含义 | 实现位置 |
|---|---|---|---|
| P3 | CR-H5 | `/api/health` 未暴露 `control_token_configured` | `packages/jcc_core/server.py` |
| P4 | CR-H6 | 非 ASCII 令牌抛未捕获 `TypeError` | `packages/jcc_core/server.py`（两处） |

而 MASTER PLAN V2.0 的：

| 编号 | 含义 | 实现位置 |
|---|---|---|
| P3 | 策略研究体系（4 组对照 / 回测 / Walk-forward / OOS） | `apps/command_center_3d/scripts/day14-research/` |
| P4 | 成本风险收益双口径核算 | 同上（`cost-models.mjs` / `fee-wrap.mjs`） |

> **警告：两组 P3/P4 仅编号撞名，毫无关系。** 不得把 `docs/P3_P4_SPEC.md` 当作本轮 P3/P4 的规格依据，也不得把本轮 P3/P4 的结论回填到 JCC 缺陷台账。同理，`docs/FINAL_STOP_CONDITIONS.md` 属于 JCC 的 `PROJECT_COMPLETE` / A8 pin 验收体系，与本轮门禁无关（GAP_CLOSURE §1.3 增量发现 #6，已向 p3-research / p6-research 下发纠偏）。

**判别口令**：看到 P3/P4 时，先问「实现落点在 `packages/jcc_core/server.py` 还是 `apps/command_center_3d/scripts/day14-research/`」。前者是 JCC 缺陷，后者才是 MASTER PLAN 阶段。

---

## 3. 阶段实现落点（每个阶段的代码在哪、状态是什么）

状态词来自 MASTER_STATUS `phases[*].status`，语义边界见 `docs/JEV_PRODUCTION_READINESS.md` §2。

| 阶段 | 状态 | 实现落点 | 体量（来源） |
|---|---|---|---|
| **P0** | `CLOSED_WITH_CONDITIONS` | `apps/command_center_3d/scripts/day14-runner.mjs`（**绝对冻结**）+ `server/ai/risk-gate.mjs`、`server/ai/decision-schema.mjs`、`server/engine/_lib/paper-server.mjs` | runner 829 行（MASTER_STATUS `frozenAnchors`） |
| **P1** | `IMPLEMENTED_LOCAL_PASS` | `apps/equity_collector_v45/src/market-data/` | 8 个模块 + 1 测试（MASTER_STATUS `phases[P1].files` 共 9 项） |
| **P2** | `IMPLEMENTED_LOCAL_PASS` | `apps/command_center_3d/server/ai/evidence/` | 3 个模块 + 1 测试（4 文件）；证据记录 **19 字段** |
| **P3** | `SKELETON_ONLY_INSUFFICIENT_DATA` | `apps/command_center_3d/scripts/day14-research/` + `.../arms/` | 12 文件（含 `p3-contracts.mjs`、4 个 arm） |
| **P4** | `IMPLEMENTED_LOCAL_PASS` | 同在 `scripts/day14-research/`（`cost-models.mjs` / `fee-wrap.mjs`）+ `p3-contracts.mjs` 契约 | 双口径模型 + 费率契约 |
| **P5** | `BLOCKED` | 无实现（真实时间窗口） | — |
| **P6** | `IMPLEMENTED_LOCAL_PASS` | `apps/command_center_3d/scripts/supervisor/` | 10 文件（9 模块 + 1 测试） |
| **P7** | `IMPLEMENTED_LOCAL_PASS` | `apps/command_center_3d/src/components/` + `src/trading/pages/` + `src/views/trading/components/` | 见 §3.1 |
| **P8** | `BLOCKED` | 无实现（需市场观察样本自然形成） | — |
| **P9** | `DESIGN_ONLY` | 无实现（禁止实施） | — |

补充事实（MASTER_STATUS `phases`）：

- P0 本轮清零的六项缺陷登记为 `P0-A` 依赖注入、`P0-B` fail-closed（F1 硬门禁）、`P0-C` 链校验与恢复、`P0-D` 真实对账、`P0-E` runId 与不可变配置、`P0-F` 独立 QA。
- P3 的研究结论是 `INSUFFICIENT_DATA`：无排名（`ranking:null` / `NOT_PRODUCED`），D 臂（AI 决策）为 `NO_DATA`（仓库无 `isReal:true` 的 `decisions.jsonl`），walk-forward 标签为 `SYNTHETIC_OOS_DRILL`、`oosPass:0`、`verified:0`。**`verified:0` 是「零真实样本通过」，不是「已验证」。**
- P4 的费率契约状态词是 `FEE_CONTRACT_MATCHES_FROZEN_ENGINE`，并带结构化 `provenance:"SYNTHETIC_FIXTURE"`；它只表达与冻结引擎的**算术一致性**，不代表数据真实性（GAP_CLOSURE §4.8 裁定 B）。

### 3.1 P7 只能落在 `command_center_3d/src/`（事实性约束）

`apps/control_ui` 是**纯 Python 桌面/服务端 UI（16 文件，零 React/零 TSX）**，不具备承载前端驾驶舱的技术形态。因此：

- P7 的唯一合法落点是 `apps/command_center_3d/src/` 下的 React/TS 文件；
- 不得把 P7 的「驾驶舱前端」结论映射到 `apps/control_ui`，后者属于 JCC 控制平面栈（§1）；
- P7 的实际改动点（MASTER_STATUS `phases[P7].files`）：新建 `components/ProvenanceBadge.tsx`、`components/TradingDesk.tsx`；`views/trading/components/PageContainer.tsx` 新增 `deriveStatusLabel()`；`trading/pages/` 下四个页面合计清零 16 处硬编码 `label="LIVE"`（PaperAccount 8 / TradingWorkbench 6 / AICommandCenter 1 / MarketUniverse3D 1）；新建静态断言脚本 `scripts/check-no-fake-live.mjs`（10 条断言，扫描 111 个非 test 的 `src` 文件）。

### 3.2 冻结边界（R-1，全员硬约束）

`day14-runner.mjs` **绝对冻结**：P1–P7 一律不得修改、不得为其新增导出、不得复制其内部代码（GAP_CLOSURE §4.7 R-1）。任何改动会使 QA 的 C2 条件失效，并使 31/31 与 7 套件 94 断言结论作废、须全量重跑。另据 R-2，P1/P2 为纯增量：只新建文件，不改任何 tracked 源码。

---

## 4. `DECLARED_NOT_WIRED` 清单（声明了权威来源但没接线）

四处都必须按「**未接线**」阅读，不得读作已启用（MASTER_STATUS `phases`；GAP_CLOSURE §4.5「随 P0 结转的条件」）。

| # | 声明项 | 所在阶段 | 状态 | 准确含义 |
|---|---|---|---|---|
| 1 | Provider 清单 / `MODEL_ID_AUTHORITY` | P2 | `DECLARED_NOT_WIRED` | 只声明了权威来源，真实 Provider 调用仍被禁止（R-2）；`providerId` 恒为 `mock` |
| 2 | `DAEMON_BINDING` | P6 | `DECLARED_NOT_WIRED` | 无常驻守护进程、无 scheduler；套件 `supervisor.test.mjs` 用变异测试证明「篡改该绑定会让套件 FAIL」，即绑定本身未被真实守护进程消费 |
| 3 | `TRADING_DESK_MOUNT` | P7 | `DECLARED_NOT_WIRED` | `TradingDesk.tsx` 已存在但未挂载到路由/页面树；**未经浏览器渲染验证，无浏览器 E2E** |
| 4 | `ai_market_contexts` D1 表 | P1 | `DECLARED_NOT_WIRED` | 表只声明未建；`storeKind:"file"`、`dbPersisted:false`，全部记录落文件 |

**统一读法**：`DECLARED_NOT_WIRED` 表示「契约与类型存在、消费方不存在」。任何对外表述中出现「已接入 Provider / 已启用守护进程 / 驾驶舱已上线 / 数据已入库」都与事实不符。

---

## 5. 数据流与口径边界

### 5.1 哈希口径（P1）

`HASH_SCOPES` 是**五值枚举**，但默认且**唯一允许**的取值只有一个：

| 项 | 值 |
|---|---|
| `HASH_SCOPES`（枚举） | `raw_upstream_bytes` / `response_payload_sha256` / `input_snapshot_sha256` / `normalized_input` / `derived_metric` |
| `ALLOWED_HASH_SCOPE`（默认且唯一允许） | `raw_upstream_bytes` |
| `allowedHashScopeOnly` | `true` |
| `forbiddenAliases` | `response_payload_sha256`、`input_snapshot_sha256` |

要点：枚举里有五个值，**不代表五个都能用**。后两个是**被禁止的别名**（一旦被当作合法 scope 使用，等价于用「派生值」冒充「上游原始字节」）。任何哈希口径统计只认 `raw_upstream_bytes`。

### 5.2 存储落点（P1）

`storeKind:"file"`、`dbPersisted:false`。即：append-only 日志、快照、Bar、订单簿日志、质量监控、回放数据集全部落**文件**，未落数据库；`ai_market_contexts` 表未建（§4 #4）。真实行情未接入，全部为 fixture（MASTER_STATUS `phases[P1].boundaries`）。

### 5.3 成本双口径（P4，严禁合并）

| 口径 | feeRate | slippagePct | provenance |
|---|---|---|---|
| `engine_conservative` | `0.001` | `0.015` | `COMMUNITY-VERIFIED` |
| `venue_verified` | `null` | `null` | `UNVERIFIED`（状态 `NOT_MEASURED`） |

- `merged:false`，并有 `assertNoMerge()` 机器可检（MASTER_STATUS `phases[P4].dualCostModels`）。
- `venue_verified` 的 `null` 是 `NOT_MEASURED`，**不得回填** `engine_conservative` 的数值。
- 双口径差的主因是**滑点不是费率**：以 notional 200 USD 计，往返手续费 0.4、往返滑点 6.0，合计差 6.4；仅按费率口径只有 0.32（约 19 倍偏差由滑点贡献）。只统一费率不标注滑点来源，会让「保守口径」掩盖偏差（GAP_CLOSURE §1.1）。

### 5.4 推理成本三口径（P2，R-4）

预算口径 `state.costUsd`、实测 `actualInferenceCost`、估算 `estCostUsd` **各自独立、严禁合并**；`UNVERIFIED` 分量不得参与合计；未实测写 `amount:null` + `provenance:"UNVERIFIED"`，绝不回填预算值（GAP_CLOSURE §4.7 R-4）。

### 5.5 决策与溯源语义（P2 / P7）

- P2 的 `decisionClass` 枚举只有 `APPROVED` / `NO_ACTION`，`structurallyNoHold:true`：**provider 超时 / 抛错 / 解析失败必须记 `NO_ACTION` + `PROVIDER_FAILED_FAILCLOSED` + `execution:"NONE"`，不得改写成模型主动 HOLD**（这是 P0-B 的 F1 硬门禁，QA §5 记录了「旧版 FAIL → 修复 → 最终指纹原样重验 PASS」的全过程）。
- P2 证据记录 19 个字段，其中输出侧字段名为 `rawResponseBase64`（存的是 base64 原始字节，不是字节数整数），输入侧 `providerResult.rawResponseBytes` 保持原名（GAP_CLOSURE §4.8 裁定 A）。
- P7 的溯源三态为 `REAL` / `FIXTURE` / `NOT_MEASURED`，由 `resolveProvenanceState()` 解析，**fail-closed**：只有完整正证据才允许 `REAL`。
- P7 的 provider 字段分三套，不得互相顶替：`providerVerdict`（`BLOCKED`/`UNVERIFIED`）、`providerStatus`（`NOT_CONFIGURED`/`CONFIGURED`/`CONFIGURED_LOCAL_ONLY`/`NOT_CONNECTED`）、`testProviderStatus`（`CONNECTED`/`BLOCKED`/`FAILED`）。`CONNECTED` 仅由 `POST /providers/test` 真实 200 且有 content 产生，不是全局禁词（MASTER_STATUS `phases[P7].providerFieldDistinction`）。

### 5.6 风控门的唯一权威与已知缺口

- 正式风控 `apps/command_center_3d/server/ai/risk-gate.mjs` 是**唯一权威门**；简化门 `fallbackRiskGate` 的放行能力已被彻底删除，导入失败走 `throw`（P0-B，SECURITY §10.1 R1/R2）。
- 但**冻结配置里的风控参数并未真正生效**：`LEGACY_FREEZE_EXPECTED` 声明的 `maxNotionalPerTrade:200` / `maxTrades:6` / `maxExposurePct:0.2` / `dailyLossBreachPct:1` / `drawdownBreachPct:2` 除定义与导出外无使用点，实际生效的是 `DEFAULT_RISK_LIMITS.maxNotionalUsd:1000`；`s.riskBreach` 恒为 `false`，`RISK_BREACH` 暂停恒不触发（F-14，SECURITY §10.3 / GAP_CLOSURE §4.0.3）。**200 与 1000 的口径冲突未解决前，不得声称「单笔 ≤200 USDT 已落地」。**
- `riskCheckImpl` 为可注入参数，调用方可替换整个风控门（F-15）；默认链路安全且被 `RED_LINES.allowBuySell=false` 阻断真实成交，但 P3 解锁前必须收紧。

---

## 6. 权威指纹表（五项冻结锚点）

来源：MASTER_STATUS `frozenAnchors.anchors`（`verifiedUnchanged:true`，提交前终检逐字符一致）。以下 sha256 为**版本快照**，用于核验，不作引用锚点。

| 名称 | 路径 | sha256 | 角色 |
|---|---|---|---|
| `day14-runner.mjs` | `apps/command_center_3d/scripts/day14-runner.mjs` | `002653fed0d48e3cd20c95a397400d93830192ca87f60a8361a88b3aa3726d4a` | P0 冻结核心（829 行） |
| `risk-gate.mjs` | `apps/command_center_3d/server/ai/risk-gate.mjs` | `5797ba8cd6308e11c75c53121f151402e872ec1f1551780eb6953d60c992278f` | 正式风控唯一权威门 |
| `decision-schema.mjs` | `apps/command_center_3d/server/ai/decision-schema.mjs` | `328b7a9b140069d354e9b06a08d85dca79ed47f3394acb8fd43c031be3184ba1` | 14 字段白名单 |
| `paper-server.mjs` | `apps/command_center_3d/server/engine/_lib/paper-server.mjs` | `f9ac6e142446ef2aa8deb928f2b129eb2d42f4227d1bc22469191087312e4d74` | 冻结引擎 |
| `config.freeze.json` | `reports/day14-artifacts/config.freeze.json` | `d4edce64c48bf15ab6803ce9ea08a164404d84642c61065d74550d7983208658` | 旧配置冻结快照（未改） |

另有两份 gate 报告指纹（MASTER_STATUS `gateReports`，用于确认引用的是哪一版）：`JEV_INDEPENDENT_QA.md` = `cc0ab7d8…`（397 行）、`JEV_REAL_TRADING_SECURITY.md` = `259bc65f…`（595 行）。**新锚点只追加在新文件里，不回改旧报告。**

---

## 7. 验证边界（本轮到底验了什么、没验什么）

| 事实 | 值 | 边界 |
|---|---|---|
| 本地 7 套件 | `suites:7` / `assertionsPass:94` / `assertionsFail:0` / `allExitZero:true` | 由主理人一手复跑，非采信成员自评 |
| 套件构成 | contracts 7 + runner 24 + market-data 10 + evidence 17 + p3-contracts 12 + supervisor 14 + check-no-fake-live 10 | 合计 94 |
| 证伪能力 | B1 基线对照 + M3 变异测试（关闭链校验的 MUTANT 使篡改漏检） | 证明断言有牙 |
| 类型检查 | `TYPECHECK = UNVERIFIED` | `node_modules` 缺失，`npx tsc --noEmit` **从未执行** |
| 单测运行器 | `npx vitest run` **从未执行** | 同上，`node_modules` 缺失 |
| CI | `CONFIG-CHANGE / UNVERIFIED`，`ciRunId: null` | 无本地 Actions runner，不宣称 CI PASS、不编造 Run ID（R-6） |
| 真实资金/账户/推理 | `realOrdersPlaced:0` / `realAccountsCreated:0` / `realFundsInjected:0` / `realInferenceCalls:0` | 均为实测事实（非占位） |
| 真实 API 费用 | `realApiCostUsd: null`，状态 `NOT_MEASURED` | `null` 不是 0 |
| 全部结果溯源 | `allResultsProvenance: "SYNTHETIC_FIXTURE"` | 无真实行情参与 |

**正确读法**：94 断言通过 = 「本地 `node` 直跑通过」。它**不等于** CI 通过、**不等于**类型检查通过、**不等于**生产就绪、**不等于**真实数据验证。

---

## 8. 未测量 / 未决清单（不许填补）

| 项 | 状态 | 说明 |
|---|---|---|
| 类型检查 | `UNVERIFIED` | `node_modules` 缺失 |
| CI 运行结果 | `CONFIG-CHANGE / UNVERIFIED`，无 Run ID | 需真实 runner |
| 真实 API 成本 | `NOT_MEASURED` | 零真实推理，故无费用事实 |
| `venue_verified` 费率/滑点 | `NOT_MEASURED` | `null`，不得回填 |
| P3 OOS 验证 | 未验证 | `oosPass:0` / `verified:0` / 标签 `SYNTHETIC_OOS_DRILL` |
| 浏览器渲染 / 前端 E2E | 未做 | P7 无渲染验证 |
| 历史 MiniMax 凭据存储位置 | `UNVERIFIED` | `CRED-MINIMAX` 登记项 |
| R6（无进程发起外部 HTTP） | `UNVERIFIED` | 安全侧静态只读的方法论边界，保持不改写 |

---

## 9. 溯源说明（本文每条结论来自哪）

- §1 双栈、§2 编号撞名：GAP_CLOSURE §1.3（增量发现 #5 / #6）+ `docs/P3_P4_SPEC.md` §1 + 目录实测。
- §3 阶段状态与文件清单：MASTER_STATUS `phases`；冻结边界来自 GAP_CLOSURE §4.7 R-1/R-2；P7 落点约束来自 `apps/control_ui` 目录实测（16 文件、0 TSX）。
- §4 `DECLARED_NOT_WIRED`：MASTER_STATUS `phases`（P1 `contracts.aiMarketContextsBinding`、P2 `boundaries`、P6 `daemonBinding`、P7 `tradingDeskMount`）+ GAP_CLOSURE §4.5 结转条件。
- §5 口径边界：MASTER_STATUS `phases[P1].contracts`、`phases[P4]`、`phases[P2].contracts`、`phases[P7].provenanceStates` / `providerFieldDistinction`；GAP_CLOSURE §4.7 R-4、§4.8 裁定 A/B、§1.1（滑点占比）；F-14/F-15 见 SECURITY §10.3 与 GAP_CLOSURE §4.0.3。
- §6 指纹：MASTER_STATUS `frozenAnchors.anchors`（逐字抄录，未改写）。
- §7 验证边界：MASTER_STATUS `verification` / `environment` / `hardDeclarations`。
- §8 未测量项：MASTER_STATUS `hardDeclarations` 与 `defects.registeredNotFixed`。

**留空项**：无。本文所有断言均可在上述四份权威源中找到对应章节；对来源中不存在的数据（如真实费率、CI Run ID、类型检查结果），一律标注为 `NOT_MEASURED` / `UNVERIFIED`，未以任何数字填充。

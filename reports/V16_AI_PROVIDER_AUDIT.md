# V1.6 轨道 B — AI / Paper 系统审计（正式转写）

- 原始审计：`D:/jev-v16-wt/ai-audit/B1_SYSTEM_AUDIT.md`
- 原审计员：**v16-ai-auditor**（AI 架构审计员），审计日期 2026-11-09，只读审计，未读取任何密钥明文、未访问云端
- 本文性质：由 **v16-ai-engineer** 转写并做有限复核。**结论归原审计员**；本文不新增未经验证的判断
- 复核方式：对原文中可本地验证的结构性断言逐条比对代码（见 §5），不重跑云端、不读凭据
- 口径：所有行号以基线提交 `bfb3206` 的工作树为准（本分支对网关只在 `:746` 之后新增 10 行，`:719` 红线行号保持原样）

---

## 1. 执行摘要（转写）

| # | 能力 | 原判定 | 一句话 |
|---|---|---|---|
| 1 | Provider Center / Bridge | EXISTS（本地 Python 栈） | 前端 + `provider_registry.py` + DPAPI SecretStore 完整；Cloudflare 侧只有 BLOCKED 状态桩，桥只有接收端 |
| 2 | AI 六角色决策 | PARTIAL | `agent-orchestrator.mjs` 六角色为 SPOTTER/PRIOR/EDGE/KELLY/TAKER/CLOSER；KELLY/TAKER 为确定性闸门；前端跑 mock |
| 3 | 服务器权威 Paper 引擎 | EXISTS（冻结） | `paper-server.mjs` 1106 行：定点算术、撮合、幂等、事件账本、哈希链、熔断齐备 |
| 4 | Cloudflare 网关 | EXISTS | `[[path]].js` 753 行；`ENABLE_REAL_TRADING` 红线 `:719`；全站 Basic Auth |
| 5 | D1 表结构 | EXISTS | 5 个迁移 18 张表，金额 1e8 定点 |
| 6 | 凭据引用模式 | EXISTS（三套并存） | DPAPI / Worker secrets / `serviceCredentials` 内存单次传递；均只回 last4 |
| 7 | 已有 AI 调用代码 | EXISTS | `ai-core.mjs` 仅 minimax/qwen 硬编码；本地 `generation.py` 为一份 openai_compat；`providers/real.py` 为 fail-closed 桩 |
| 8 | 轨道 B 缺口 | 7 项：2 EXISTS / 4 PARTIAL / 1 MISSING | 见 §4 |

---

## 2. 关键结构事实（转写，附原文出处）

1. **仓库分裂**：`D:/jev/apps/command_center_3d` 是旧构建；现行源码在 `D:/jev-v16-wt/v16`；`server/trading_api.mjs:30-35` 以绝对路径引用本机 NR 引擎与 artifacts。
2. **Provider 状态桩**：`[[path]].js:435-452` 的 `providersStatus()` 硬编码 `PROVIDER_NOT_CONFIGURED`；`:453-468` `agentsStatus()` 同理。
3. **六角色命名三套词汇**：`[[path]].js:445-451` 自述 `naming_discrepancy`，`mapping: "UNVERIFIED - human decision required"`。
4. **智能体安全闸门**：`agent-orchestrator.mjs:352` 向 `computeFractionalKelly` 传 `null`（客户端快照不可信）→ KELLY 恒 BLOCKED；TAKER 输出 `PROPOSAL_ONLY`、`autoSubmit:false`、`requiresManualConfirmation:true`。
5. **Paper 引擎幂等**：`clientOrderId + request_hash`（`paper-server.mjs:756-759`），重复不同参 409 `IDEMPOTENCY_CONFLICT`，提交竞态 409 `ACCOUNT_VERSION_CONFLICT`。
6. **云端市场面收窄**：仅 `api.binance.us`，仅 BTCUSDT/ETHUSDT；引擎原 host 经 `env.__fetch` 改写，引擎源码零修改。
7. **凭据范式**：serviceCredentials 随 HMAC 签名请求到达、执行前从 body 剥离（`research.js:77-96`），不可能进入事件账本/日志/响应。

---

## 3. 已存在可复用资产（转写）

- Paper 引擎全套（`paper-server.mjs` + 迁移 0001-0003）：AI 下单意图按 7 字段契约 POST，幂等 + cookie 会话。
- 会话/鉴权链：`paper-ledger.mjs` HMAC cookie；`service-auth.mjs` HMAC/OIDC 双信任锚。
- 六角色骨架：逐角色调用 + `roleValid()` 强校验 + 事件记录。
- 策略域表：`research_runs` / `risk_rejections` / `daily_performance` / `strategy_events`。
- 绩效计算：`computeDailyPerformance` / `buildPerformanceWindow` / `equity-snapshot`。
- 前端 Provider Center 与 `/probe`、`/live` 接口。

---

## 4. 轨道 B 缺口（转写，原判定）

| 需求 | 原判定 | 关键缺口 |
|---|---|---|
| 统一 Provider Adapter（OpenAI 原生 + compatible + Ollama） | PARTIAL | 云端 `ai-core.mjs` 仅 minimax/qwen 硬编码，不走统一 adapter；OpenAI 原生与 Ollama 专用类缺失 |
| Provider 连通性测试 | EXISTS（本地） | 云端只有状态桩 |
| AI 决策 JSON Schema 校验 | PARTIAL | `roleValid()` 手写校验，无标准 schema 文档；KELLY/TAKER 无对称校验 |
| AI 决策持久化 | PARTIAL | 无专门决策明细表；六角色输出只在事件 payload |
| 决策→风控→下单链路 | PARTIAL（设计使然） | 人工确认制，无受控自动提交通道 |
| 收益归因 | PARTIAL | 无决策维度归因表/join |
| Mock/Real 隔离 | PARTIAL | 前端 `USE_MOCK` 源码常量；无端到端 `data_mode` 协议 |

---

## 5. 本次转写人复核（仅复核原文可本地验证的结构性断言）

| 原断言 | 复核方式 | 结果 |
|---|---|---|
| 冻结引擎 15 文件、整体 sha256 锚点 | 独立校验脚本 `scripts/v16_independent_verify.py` 常量 `ENGINE_TREE_SHA256` / `ENGINE_FILE_COUNT = 15`；本分支 `git status -- server/engine/` | 一致；本分支零改动 |
| 网关红线在 `:719` | `grep -n "ENABLE_REAL_TRADING"` | 一致（`:719 / :720-722`） |
| 市场白名单仅 `api.binance.us` + BTC/ETH | 读 `[[path]].js:34-46` 与 `MARKET_SUPPORTED_SYMBOLS` | 一致 |
| `ai-core.mjs` 仅 minimax/qwen | 读导出与 provider 白名单 | 一致（`["minimax","qwen"]`） |
| 六角色代码名 | 读 `agent-orchestrator.mjs` `AI_ROLES` 与安全块 | 一致（SPOTTER/PRIOR/EDGE/KELLY/TAKER/CLOSER；KELLY 传 null） |
| D1 迁移 5 个、18 表 | 列目录 + 迁移内容 | 一致 |
| 云端 provider 状态为硬编码桩 | 读 `providersStatus()` | 一致（`verdict: BLOCKED`, `credentials: 0`） |

未复核项（保持原审计的 UNVERIFIED）：本地 DPAPI 凭据库内容（按规约不读取）、`packages/jcc_core` 内部细节、NR 仓库 `functions/api/agents/research.js` 运行时行为、`D:/jev/artifacts/**` 证据内容、Cloudflare 线上实际状态。

---

## 6. 本轮据此做出的工程决定（仅记录，不改变原审计结论）

1. 统一 Adapter **平行新建**于 `server/ai/`，不改冻结的 `ai-core.mjs`。
2. 决策持久化使用**新表** `ai_decisions`（`server/ai/migrations/0006`），订单归因走**旁路表** `decision_order_links`，不改 `paper_orders`。
3. 风控权威放在独立 `risk-gate.mjs`，AI `confidence` 不参与裁决；执行前二次校验已存裁决。
4. `data_mode` 由服务器写入（`mock|real`），无凭据时 real 路径返回 `PROVIDER_BLOCKED`，不落行。
5. 六角色映射以 `OWNER-DECIDED DRAFT` 形式交付（`server/ai/roles-mapping.mjs` + `docs/JEV_AI_DECISION_SCHEMA.md`），**不重命名任何代码标识符**。

---

*转写完。原文的全部风险与未知见 B1 §11，本条目不重复。*

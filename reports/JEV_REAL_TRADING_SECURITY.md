# JEV_REAL_TRADING_SECURITY.md — V1.6 MASTER PLAN V2.0 / P0 安全只读复核

> 角色：`security-auditor`（团队 `jev-v16-p0-gap-closure`）｜性质：**只读安全复核，不写实现代码、不改任何源码**
> 仓库：`C:/Users/Administrator/WorkBuddy/jev-v16`｜分支：`feature/v16-p0-gap-closure`｜基线 HEAD：`ebc3fcc`
> 独占写范围：本文件（`reports/JEV_REAL_TRADING_SECURITY.md`），**仅此一个文件**
> 生成日期：2026-11-11 (GMT+8)
> 纪律：不触网 / 不调付费模型 / 不提交订单 / 不 push / 不碰 main·Release / **未打开任何凭据文件、未输出任何凭据值或 last4**

---

## 0. 结论摘要（DECISION 先行）

> **【第二轮复核更新 · 2026-11-11】** 下方 §0–§9 为**第一轮**（实现未落盘时）的基线与 NO-GO 结论，**原文保留不删**，作为证据历史。
> trading-engineer 实现已落盘后，主理人重新触发 R1–R6 复核；本审计独立实读最终 829 行新源码（复核期间历经 3 个并行版本，见 §10.0）+ `day14-verify.mjs` + 运行产物，结论见 **§10**：
> **更新后 Security DECISION = GO WITH CONDITIONS（仅限 Security 范围）**——第一轮 3 个阻断根因中 F-01/F-02/F-04 已 VERIFIED 闭环，F-05 按 P9 DESIGN ONLY 不构成 P0 blocker；但 R6 保持 UNVERIFIED、注入面/风控参数口径/能力面等 medium 残留项仍列为晋级前置条件。
> 结论升级**仅依据本审计独立实读证据**，非采信实现方自评；不代表 P0 整体 PASS、不替 `independent-qa` / `trading-engineer` 签字、不得据此晋级 P1 或解除 P9。
> **【第三轮更新 · 2026-11-11】** team-lead 已中转 F-12 / R6 的两条独立书面证据，**C1 与 C7 已满足**；Security DECISION **仍为 GO WITH CONDITIONS（仅限 Security 范围）**，R6 静态结论保持 UNVERIFIED、未被升级。详见 §10.1 R6 第三轮补注、§10.4 C1/C7、§10.5 第三轮 DECISION。
> **【第四轮（最后一次）更新 · 2026-11-11 · 引用锚点修正】** 按 team-lead 纠偏新增 §10.9「引用稳定性声明与证据锚点」：权威锚点 = 文件 sha256 + 章节号，裸行号降级为版本快照（v2 快照 359 行 / `0d42b43d…`）；§10.4 C8 补注 QA 现行 DECISION 与演变。**不改 DECISION（仍 GO WITH CONDITIONS）、不改 R6（仍 UNVERIFIED）、不删任何 F 项 / blocker、不改源码 / 测试 / 旧 freeze、不 commit / push、不直接联系其他成员、不宣布 P0 整体 PASS 或生产 GO。** 详见 §10.9。

**第一轮 Security gate DECISION = NO-GO（仅限安全范围），已被 §10 更新为 GO WITH CONDITIONS。**

理由（三条，任一即构成阻断）：

1. **P0-B fail-closed 边界在 `ebc3fcc` 基线中 VERIFIED 存在缺陷**：正式风控 `server/ai/risk-gate.mjs` 导入失败仅 `console.warn` 不 `throw`（fail-open 到简化门）；简化门 `fallbackRiskGate` 仍保留「可交易路径的放行能力」（非 HOLD 仅查 notional/exposure 即可 `approved:true`，HOLD 直接 `approved:true`）；正式门调用时 `context:null`；`riskCheck` 的 `limits` 形参可被覆盖。详见 §2。
2. **trading-engineer 的新实现尚未落盘，修复状态 UNVERIFIED**：`git status --porcelain` 为空、`git diff --stat HEAD` 为空、HEAD 仍为 `ebc3fcc`、reflog 自 checkout 起无新提交、`reports/day14-artifacts/` 全部 journal/anchor/summary/state 均被 git 跟踪且内容未变（即本轮无运行痕迹）。按主理人指示「实现可能正在并行修改中；若尚未落盘，标注 UNVERIFIED 并给出待复核清单，不要预写 GO」——本审计不预写 GO。详见 §2.5。
3. **§9 独立凭据域要求当前未满足（VERIFIED 现状 + UNVERIFIED 位置）**：真实交易 API 应使用独立凭据域、不复用 WorkBuddy/GitHub/Cloudflare 管理权限；主理人基线（`JEV_GAP_CLOSURE.md` §1.1）已记录凭据库键名仅 `cf_*`/`github_pat_*`/`jev_paper_ledger_secret`/`jev_preview_*`/`r2_*`/`x_*`，**无 LLM/交易 Provider 键**，历史 MiniMax M3 真实调用的凭据存储位置为 UNVERIFIED。详见 §1、§5。

> 本轮授权边界（未 push、未碰 main/Release、`allowBuySell=false`、`enableRealTrading=false`）经 git 只读命令核实为 **PASS / VERIFIED**（§6），但这**不构成**对上述 fail-closed 缺陷的豁免。
> 本结论**不替** `independent-qa`（P0-F）或 `trading-engineer`（P0 A–E）签字；P0 整体门禁由 `team-lead` 依 `JEV_GAP_CLOSURE.md` §4 整合判定。

---

## 1. 复核项 1：凭据隔离（MASTER PLAN §9）

### 1.1 核实命令（只读）

```
git check-ignore -v config/.env
git clean -ndX                              # dry-run，仅列被忽略文件，不删除
git ls-files | grep -iE '\.env|secret|credential|token|\.pem|\.key|apikey|api_key'
git ls-files --error-unmatch config/.env.example
git grep -l -I -E '<私钥/tok 前缀模式>' -- '*.mjs' '*.ts' '*.js' '*.py' '*.json' '*.yaml' '*.yml'
Read .gitignore
```

### 1.2 发现

| 项 | 结论 | 证据等级 |
|----|------|----------|
| `.env` 是否被 gitignore | **是**。`.gitignore:120-122` = `.env` / `.env.*` / `!.env.example`；`git check-ignore -v config/.env` → `.gitignore:120:.env  config/.env` | VERIFIED |
| 本地真实 `.env` 是否存在、是否被跟踪 | **存在但被忽略、未被跟踪**：`git clean -ndX` 输出 `Would remove config/.env`（仅路径）；`git ls-files` 中凭证类只有 `config/.env.example`（被跟踪的示例文件）。**未打开其内容、未输出任何值** | VERIFIED |
| 私钥/凭据文件模式是否可能被提交 | `.gitignore:123-133` 覆盖 `*.key`/`*.pem`/`*.pfx`/`*.p12`/`*.keystore`/`*.jks`/`credentials*`/`auth.json`/`.sandbox-secrets`/`id_rsa`/`id_ed25519`；`:141-149` 另含 v1.1 provider secret 规则；`:182` 含 `shadow_endpoint.token` | VERIFIED |
| 被跟踪源码中是否存在凭据硬编码 | 对**被跟踪**的 `.mjs/.ts/.js/.py/.json/.yaml/.yml` 运行典型私钥/token 前缀模式（`-----BEGIN ... PRIVATE KEY`、`sk-`、`AKIA`、`ghp_`、`github_pat_`、`xox*`）`git grep -l`，返回 `(no hardcoded-credential pattern matches in tracked source)` | VERIFIED（模式匹配口径） |
| §9「独立凭据域，不复用 WorkBuddy/GitHub/Cloudflare 管理权限」 | **未满足**。凭据库含 `cf_*`（Cloudflare）、`github_pat_*`（GitHub）等管理域键；无独立真实交易 API 凭据域；历史 MiniMax M3 真实调用凭据位置 UNVERIFIED | VERIFIED（现状，转引 `JEV_GAP_CLOSURE.md` §1.1）/ UNVERIFIED（位置） |

### 1.3 风险

- **HIGH — F-05**：§9 独立凭据域未满足。当前 P0 为 Paper/SHADOW 阶段，尚无真实交易 API 凭据，故未发生「复用管理权限直连真实交易」的事实；但一旦进入真实运行，若沿用现有 `cf_*`/`github_pat_*`/DPAPI 凭据库即违反 §9。**当前状态：设计缺口，非本轮已发生事件**。
- **LOW — F-11（元数据）**：历史 commit message / reflog 中出现 provider key「形态/前缀」级描述与代理（`127.0.0.1:10808`）字样（见 `git log`/`git reflog`，属 `ebc3fcc` 之前的历史，非本轮）。非完整凭据值，但属凭据形态元数据进入 git 历史的低风险提示。本审计不复制其具体字符串。
- **PASS / VERIFIED — F-06**：`.env` 隔离规则完备、真实 `.env` 未被跟踪、被跟踪源码无典型硬编码凭据模式。
- **边界声明**：模式匹配 ≠ 绝对无泄漏；`config/.env` 内容本审计**未读取**，故不作「文件内容安全」断言。若发现疑似泄漏，本审计只报路径与风险等级，不展示内容（本次未发现需升级上报的疑似泄漏路径）。

---

## 2. 复核项 2：fail-closed 边界（P0-B，主理人重点）

### 2.1 核实对象

- `apps/command_center_3d/scripts/day14-runner.mjs`（`ebc3fcc` 基线，全文 303 行，实读）
- `apps/command_center_3d/server/ai/risk-gate.mjs`（正式风控，全文 182 行，实读）
- 新实现（trading-engineer 负责）：**当前未落盘**，见 §2.5

### 2.2 基线逐行证据（VERIFIED，实读源码）

| 主理人指出的缺陷 | 源码位置 | 实读结果 |
|------------------|----------|----------|
| 正式风控导入失败只 warn | `day14-runner.mjs:117-122` | `try { ({riskCheck: formalRiskGate} = await import(...risk-gate.mjs)); } catch(e){ console.warn("[day14] formal risk-gate import unavailable; using prep-simplified fallback:", e.message); }` —— **不 throw**，导入失败后 `formalRiskGate` 保持 `undefined` | 
| 简化门保留可交易路径放行能力 | `day14-runner.mjs:124-133` | `fallbackRiskGate(action, notional)`：非 HOLD 时仅判 `notional > maxNotionalPerTrade` 与 `notional/virtualCapital > maxExposurePct`，合规即 `approved:true`；HOLD 直接 `return {approved:true, code:"N/A_HOLD"}`。**缺日亏损/回撤/持仓/新鲜度/白名单/过期/数量下限/schema 校验** |
| 正式门缺失时改走简化门 | `day14-runner.mjs:225-231` | `if (formalRiskGate) { gate = formalRiskGate(...) } else { gate = fallbackRiskGate(...) }` —— fail-open 通路成立 |
| 正式门调用参数 | `day14-runner.mjs:226` | `formalRiskGate({ decision, context: null, nowMs: Date.now() })` —— **`context:null`**；未传 `limits` |
| 正式门 `limits` 可被调用方覆盖 | `risk-gate.mjs:91-92` | `riskCheck({ decision, context, nowMs=Date.now(), limits={} })` → `const cfg = {...DEFAULT_RISK_LIMITS, ...limits}`，`minNotionalUsd/maxNotionalUsd/maxQuantity/maxContextAgeMs` 均**可被入参覆盖** |
| 正式门非 HOLD 的完整 context 校验 | `risk-gate.mjs:108-170` | 非 HOLD 时强制：symbol 白名单 → `normalizeContext(context)` → `mark_quality==='verified'` → symbol 匹配 → `price_ts` 合法/新鲜/未过期 → markPrice → notional 上下限。即「完整 context 校验」由**正式门自身**实现，而非 runner 主动构造 |
| 正式门 HOLD 分支 | `risk-gate.mjs:100-106` | `action==="HOLD"` 经 `validateDecision` 后直接 `approved:true, code:RISK_HOLD_NO_ORDER`（语义为「不携订单、无需执行」，非放行下单） |
| 调用方能否覆盖 decision/limits | `day14-runner.mjs:187` | `runDay14({ cycles, dry, fetchImpl } = {})` 对外仅 3 参；`decision` 由内部 `buildDecision` 构造（`:137-153`），`runDay14` 不暴露 decision/limits 覆盖。但 `riskCheck` 作为导出接口接受任意 `decision`+`limits`（接口层可覆盖） |
| 当前实际是否下单 | `day14-runner.mjs:206,239` | prep 下 `action` 恒为 `"HOLD"`、真实 AI 块被 `dry=true` 门控；`:239` 执行路径为空注释 `if (action !== "HOLD" && CONFIG_FREEZE.allowBuySell) { /* reserved */ }`；`allowBuySell=false`。**当前版本实际不产生任何订单**（风险事实，非缺陷豁免） |

### 2.3 安全判定（基线）

- **BLOCKER — F-01**：fail-closed 边界未建立。正式风控是唯一权威门，但其导入失败时系统**静默降级**到仅查两项的简化门（`warn` 而非 `throw`），且该简化门**保留 `approved:true` 的放行能力**。主理人要求的「真正删除可交易路径的放行能力」在基线中**未做到**。
- **BLOCKER — F-02**：`fallbackRiskGate` 的「可交易路径放行能力」客观存在：一旦未来 `action` 非 HOLD（真实路径打开）或导入失败，仅需 notional/exposure 合规即可 approved，绕过白名单/新鲜度/schema/数量下限/过期校验。
- **HIGH — F-03**：runner 调用正式门时 `context:null`——非 HOLD 场景依赖正式门自行 `CONTEXT_MISSING` 拒绝才算 fail-closed，runner 本身**未构造/未传入 verified market context**；且 `riskCheck` 的 `limits` 形参允许放宽限额，接口层面存在被覆盖的通道。

> 说明：基线中 `action` 恒 HOLD 且 `allowBuySell=false`，因此**本轮实际**未发生「经简化门放行的真实交易」。但 P0-B 的验收对象是**代码边界**（导入失败是否 throw、简化门是否还能返回 approved、非 HOLD 是否强制完整 context、调用方能否覆盖参数），该边界在基线中 VERIFIED 不成立。

### 2.4 与历史报告的口径冲突（独立性声明）

`reports/DAY14_READINESS.md`（旧分支 `feature/v16-market-provenance-hardening` / PR #6 / P1 `cf75cf7`）自述「P1 代码安全收口已完成」「正式风控接线 PASS」「独立 QA PASS」「formal risk gate wired: YES」，并把简化门定性为「防御性 fallback，非替代」。**主理人本轮 `JEV_GAP_CLOSURE.md` §1 缺陷 #2 已推翻该定性**（「属实且更严重：可交易路径仍有放行能力」）。本审计以**本轮实读源码**为准，不采信历史报告的自评 PASS；历史报告不得作为本轮 GO 依据。

### 2.5 新实现状态：UNVERIFIED（未落盘）

核实命令与结果：

```
git branch --show-current            → feature/v16-p0-gap-closure
git rev-parse HEAD                   → ebc3fccbf4186b9651bbde2371ef884633d527f0
git status --porcelain=v1 --untracked-files=all   → （空）
git diff --stat HEAD                 → （空）
git reflog -12                       → HEAD@{0} 仅 "checkout: ... to feature/v16-p0-gap-closure"，其后无 commit/push/merge/reset
git ls-files reports/day14-artifacts/ → config.freeze.json / 5 个 *.jsonl / state.json / anchor / summary 全部被跟踪且内容未变
```

结论（**时点性表述**，核实时点 2026-11-11 本次审计）：被跟踪文件 `git diff --stat HEAD` 为空（`day14-runner.mjs` 未修改）、HEAD 仍为 `ebc3fcc`、reflog 自 checkout 起无新提交、`apps/command_center_3d/scripts/` 下**无新增 `day14-*.mjs` 实现文件**（Glob 仅返回原有 `day14-runner.mjs`）→ **trading-engineer 针对 A–E 的新实现尚未落盘**。因此对「新实现是否修复 P0-B」的所有判断均为 **UNVERIFIED**。按主理人纪律，不预写 GO、不把 UNVERIFIED 改写成 VERIFIED。

> **并行落盘动态性（VERIFIED，复核末次 `git status`）**：核实时末次 `git status --porcelain -uall` 另出现 `?? reports/JEV_GAP_CLOSURE.md`——这是 `team-lead` 并行新建、尚未提交的主理人基线文档（本审计实读其内容，**非我创建、非 trading-engineer 实现文件**）。它说明团队成员正在并行写文件：本审计的「未落盘」结论仅对上述时点成立。即便新实现随后落盘，本审计**仍未核实过它**，故本轮 Security 结论不签字、不预写 GO，需按 §2.6 待复核清单重新触发安全复核。此动态性不改变 NO-GO 结论。

### 2.6 待复核清单（新实现落盘后必须逐项实读复核，当前全部 UNVERIFIED）

| # | 待复核项 | 期望的安全属性 | 证据要求 |
|---|----------|----------------|----------|
| R1 | 正式风控导入失败处理 | 必须 `throw`（或等价 abort 整轮），**不得** `console.warn` 后继续 | 实读 catch 分支 + 故障注入（删除/改名 risk-gate.mjs 后运行应非零退出） |
| R2 | 简化门 `fallbackRiskGate` | 彻底删除，或**任何输入下都不返回 `approved:true`**（永不存在可交易路径放行能力）；HOLD 若保留须明确 no-order 语义且不可执行 | 实读函数体 + 反例测试 |
| R3 | 非 HOLD 的完整 context 校验 | 非 HOLD 必须走 `server/ai/risk-gate.mjs` 的完整 context（verified/symbol/ts/expires/price/notional/quantity/白名单），`context` 不得为 `null` 占位 | 实读调用点参数构造 + 非 HOLD 反例 |
| R4 | 调用方覆盖 limits / 风控参数 / decision | `runDay14`（及任何调用点）不得允许外部覆盖 `limits`、`decision` 字段或风控参数；若 `riskCheck` 仍保留 `limits` 形参，runner 必须显式传固定常量或冻结 | 实读函数签名 + 调用点 |
| R5 | 默认 fixture / dry 红线 | 默认 `dry=true`、合成行情、Mock AI、SHADOW 不执行；`allowBuySell=false`、`enableRealTrading=false` 常量与落盘 freeze 一致 | 实读 + `config.freeze.json` 比对 |
| R6 | 本轮实际外部调用 = 0 | 由 trading-engineer / independent-qa 书面确认；静态只读审计无法证明「无进程发起过 HTTP」 | 他方确认（见 §3） |

---

## 3. 复核项 3：网络 egress 边界

### 3.1 核实命令

```
Grep '\bfetch\s*\(' in apps/**/*.mjs（files_with_matches）
git grep -n -E 'fetchImpl|providerImpl|engineImpl|ENABLE_REAL_TRADING|enableRealTrading|allowBuySell' -- '*.mjs' '*.ts' '*.js'
git grep -n -E '(globalThis|global)\.fetch|= fetch\b|await fetch\(' -- '*.mjs'
git grep -n -E 'api\.(minimaxi|binance\.us|openai|dashscope)|chat/completions' -- '*.mjs'
Read apps/equity_collector_v45/src/market.mjs
```

### 3.2 发现（区分「具备能力」与「本轮实际调用」）

**（A）代码具备真实调用能力 — VERIFIED**

- 真实外呼端点存在于被跟踪脚本：`https://api.minimaxi.com/v1/chat/completions`（付费 LLM，`day1-shadow-minimax.mjs:93`、`day12-live-market-paper.mjs:165`、`day13-live-shadow.mjs:150`、`verify-minimax-keys.mjs:59`）、`https://api.binance.us/api/v3/ticker/price`（行情）。历史脚本用 `fetchImpl = curlFetch()` 经 `127.0.0.1:10808` 代理直连（对应 `git log` 中 `8da71f8` "direct egress"）。
- `apps/equity_collector_v45/src/market.mjs:40-54,61`：`fetchMarket(fetcher, ...)` 的 `fetcher` 为**注入参数**，`rawFetch` 有 host/endpoint 白名单（仅 `https://api.binance.us` 的 `/api/v3/time`、`/api/v3/ticker/price`）；注释 `L16-18` 明示「production uses global fetch on the Cloudflare edge」。
- **不受控全局 fetch 直连（server 层）**：`server/ai/provider-adapter.mjs:270` `const fetchImpl = typeof deps.fetchImpl === "function" ? deps.fetchImpl : fetch;`、`server/engine/_lib/ai-core.mjs:74` `handleAiChat(request, fetchImpl = fetch)` —— 未传注入时**默认 fallback 到全局 `fetch`**。这是「默认真实路径」而非「默认 fixture」。
- 其余大量 `fetch(` 为 `127.0.0.1` 本地 CDP / paper API 测试调用（`v11_*`/`v12_*`/`v13_*`/`verify-*`），非外部 egress。

**（B）本轮实际发起外部调用 — 纪律要求为 0，静态审计边界**

- 本审计会话内执行的全部命令均为本地只读（`git`/`grep`/`read`/`stat`/`ls`），**未发起任何网络调用**（VERIFIED，自述）。
- 仓库证据显示本轮无运行/无提交/无 push（§2.5、§6，VERIFIED）：工作区干净、HEAD 未变、journal 未追加、无 upstream。
- **诚实边界（UNVERIFIED）**：静态只读审计**无法证明**「本轮中是否有其他进程（trading-engineer / independent-qa 的测试）曾发起外部 HTTP」。该事实须由两位成员书面确认，本审计不替其签字、不伪造「=0」结论。

**（C）依赖注入完整性 — VERIFIED 不完整**

| 注入点 | 当前 `runDay14`（`ebc3fcc`） | 判定 |
|--------|------------------------------|------|
| `fetchImpl` | `runDay14({cycles,dry,fetchImpl})` 有形参；但 `acquireCycleMarket` 在 `dry=true` 时直接合成价格（`:170-180`）**完全不调用** `fetchImpl`；`!dry` 时返回 `unverified` 也**不调用** `fetchMarket`（`:165-169` 注释「would call ... here」） | 存在但未接线 |
| `providerImpl` | 全仓 `git grep` 无此标识符 | **缺失** |
| `engineImpl` | 全仓 `git grep` 无此标识符；`frozenEngine`（`:113` 导入）声明后从未调用 | **缺失** |
| 默认 fixture | dry 默认 `true`（`:187`），合成行情 + Mock AI + SHADOW | 部分（默认 dry 成立，但注入面不全） |

### 3.3 风险

- **HIGH — F-07（能力面）**：仓库**具备**真实付费模型 / 真实行情 / 全局 fetch 直连能力，且 server 层默认 fallback 到全局 `fetch`；P0 红线要求「真实路径做成依赖注入且默认 fixture」，当前 `runDay14` 仅 1 个注入点且未接线，providerImpl/engineImpl 缺失。
- **MEDIUM — F-08**：依赖注入不完整 + dry 路径未用 `fetchImpl`，真实路径若被打开将缺乏可控边界。
- **UNVERIFIED — F-12**：本轮实际外部调用数 = 0 需他方确认；若他方曾调用真实 API/付费模型，将直接违反 P0 红线，须升级为 BLOCKER。

> 补充：`V16_FINAL_SIGNOFF.md:69`、`V16_AI_PAPER_E2E.md:35`（历史报告）亦记载「云端 LLM 出网白名单与 SSRF 边界未在真实 Worker 上验证」「没有以真实密钥从 Worker 边缘发起 provider 请求」，与本节能力面判断一致（转引，VERIFIED 实读这些报告行）。

---

## 4. 复核项 4：日志 / 证据完整性

### 4.1 核实对象与命令

`Read day14-runner.mjs`（日志/state/anchor 相关行）、`git ls-files reports/day14-artifacts/`、转引 `JEV_GAP_CLOSURE.md` §1 缺陷 #3/#6。

### 4.2 发现（VERIFIED，实读源码）

| 项 | 源码 | 判定 |
|----|------|------|
| append-only 是否真 append | `makeJournal.write`（`:74-79`）用 `appendFileSync(path, ...)` | 真 append ✓；但仅追加，不校验历史链 |
| hash 链是否可绕过（跨重启） | `:71` `let prev = "GENESIS"` 为**模块级闭包变量**，进程重启归零；`:77` 把 `_prev` 写成当前 `prev`。重启后追加旧文件时 `_prev=GENESIS`，与文件末条 `_hash` 断裂 | **可绕过 / 断点恢复必断链**（主理人 §1 缺陷 #3 已认定） |
| 是否存在链校验 | `verifyConfigFreeze`（`:58-66`）只校验 freeze 与红线常量；`independentQA`（`:279-290`）=`existsSync`+非空+两条常量断言，**无链校验/重放/故障注入/账户对账** | 缺失（主理人 §1 缺陷 #6） |
| anchor / summary / state 是否可静默重写 | `writeAnchor()`（`:275`）`writeFileSync(day14-anchor.json)`、`day14-summary.json`（`:254`）`writeFileSync`、`saveState`（`:96`）`writeFileSync(state.json)` —— **均为覆盖写，非 append-only** | 旧 anchor/summary/state 可被静默覆盖，`git status` 在无提交时无法区分「新证据」与「被改写的旧证据」 |
| runId 机制 | 全文件**无 runId**；5 个 journal 路径固定（`snapshots.jsonl` 等），多轮 append 到同一文件；`state.json` 以 `cycle` 续跑 | 无 runId 隔离；journal 不覆盖旧行（append），但 anchor/summary/state 覆盖写，且续跑依赖单一 `state.json` |
| state 恢复 TOCTOU | `loadState()`（`:92-95`）读 → 循环处理 → `saveState()`（`:96`）整文件覆盖；单进程顺序执行无并发锁，但两步间无校验/无版本/无 HMAC，`state.json` 可被外部改写后诱导重跑或跳过周期 | 存在 TOCTOU / 无完整性保护 |
| 产物是否被 git 跟踪 | `git ls-files reports/day14-artifacts/` 显示 freeze + 5 journal + state + anchor + summary **全部被跟踪** | 证据入仓；但覆盖写文件在「未提交的新一轮运行」下会 dirty，当前 clean 反证本轮无运行 |

### 4.3 风险

- **HIGH — F-09**：日志链跨重启断链（`prev` 重置）、anchor/summary/state 覆盖写可静默重写、无 runId、state 恢复无完整性校验 → 证据链不满足「append-only 不可篡改 + 可重放 + 抗 TOCTOU」。注：这同时是 P0-C/D/E 的实现缺陷范畴（trading-engineer 修复），本审计仅从**证据完整性/不可否认性**角度标记，不替 QA 签字。
- **MEDIUM — F-13**：`independentQA` 仅检查文件存在性即 PASS，历史上曾据此产出「QA PASS」结论；安全角度要求链校验/重放/故障注入（由 independent-qa 负责，本审计不越权）。

---

## 5. 复核项 5：P9 实盘安全设计清单（仅设计，不实施）

> 依据：MASTER PLAN V2.0 §9（原文文件不在本仓库内，由主理人持有；清单按主理人任务书逐项列出）+ `JEV_GAP_CLOSURE.md` §2「P9 = **DESIGN ONLY / 禁止实施**：不创建实盘账户、不注入资金、不执行真实买卖」（VERIFIED 实读）。
> 状态口径：**未实现 / 部分 / 已实现**，逐项给依据。本轮纪律：不实施、不创建账户、不注入资金、不下真实单。

| # | §9 控制项 | 当前状态 | 依据（证据等级） |
|---|-----------|----------|------------------|
| 1 | 运营主体 / 司法辖区界定 | **未实现** | 仓库内无主体/辖区/合规实体文档；`JEV_GAP_CLOSURE.md` §2 P9 仅 DESIGN ONLY（VERIFIED） |
| 2 | 交易所条款 / ToS 合规审查 | **未实现** | 无条款审查记录；Binance.US 费率仅为主理人文档 §1.2 的 COMMUNITY-VERIFIED 转引，非合规意见（UNVERIFIED 本轮） |
| 3 | 最小权限 + 提币禁用 | **部分** | Cloudflare Token 历史已做最小权限轮换（`V15_TOKEN_ROTATION.md`，VERIFIED 实读报告存在）；但**真实交易 API 密钥的最小权限 + 提币禁用未配置**（无真实交易凭据域），§9 独立凭据域未满足（VERIFIED 现状） |
| 4 | IP 白名单 | **未实现** | 无交易所侧 IP allowlist 配置证据；`market.mjs` 仅有**出站** host 白名单（`api.binance.us`），非账户侧 IP 白名单（VERIFIED） |
| 5 | 凭据轮换 | **部分** | Cloudflare/GitHub 历史轮换有报告（`V15_TOKEN_ROTATION.md`）；真实交易 API 凭据轮换策略/周期未建立（VERIFIED 现状 / UNVERIFIED 策略） |
| 6 | 订单确认 / 撤单 / 回报 | **部分** | 仓库存在 **paper** 订单端点与下单/撤单/回报测试（`v12_acceptance_browser_test.mjs` 的 `/api/jev/paper/orders`、`/cancel`、`/account`，VERIFIED）；但为模拟盘，非真实交易所订单生命周期 |
| 7 | 真实手续费 / 滑点 | **部分** | `config.freeze.json` 有 `feeRate:0.001`、`slippagePct:0.015`（VERIFIED）；主理人 §1.1 指出滑点口径差约 19 倍、盘口能力缺失，未与真实交易所费率对齐（Binance.US 费率为 COMMUNITY-VERIFIED 转引，UNVERIFIED 本轮） |
| 8 | 全局资金风控 | **部分** | `CONFIG_FREEZE` 有 `maxNotionalPerTrade/maxExposurePct/dailyLossBreachPct/drawdownBreachPct/callBudgetUsd` 与 `evaluatePause`（VERIFIED）；但 `ctx` 每轮新建、状态未持久化（主理人 §1 缺陷 #4），且非跨账户/跨策略的全局资金风控 |
| 9 | 人工紧急停止 | **部分** | `evaluatePause` 自动暂停（BUDGET/PROVIDER/RECONCILE/RISK/MAX_CYCLES，VERIFIED）；无独立「人工 kill switch / 紧急停止」证据（UNVERIFIED） |
| 10 | 资金对账 / 税务 | **未实现** | `equity.jsonl` 当前硬编码平账 `MATCH`（主理人 §1 缺陷 #5，VERIFIED）；无真实资金对账与税务处理 |
| 11 | Canary 与退出策略 | **未实现** | 无 canary 发布 / 灰度 / 退出（kill 条件、回滚到 Paper）设计证据（UNVERIFIED） |
| 12 | 独立凭据域（不复用管理权限） | **未实现** | `JEV_GAP_CLOSURE.md` §1.1 明示未满足；凭据库含 `cf_*`/`github_pat_*` 管理域键、无 LLM/交易 Provider 键（VERIFIED 现状 / UNVERIFIED 历史位置） |

> 汇总：12 项中 **0 项已实现、5 项部分、7 项未实现**（部分项多为 Paper/模拟盘或历史 Cloudflare 治理，不构成实盘就绪）。P9 按主理人门禁为 **DESIGN ONLY**，本审计不实施、不宣布任何实盘就绪。

---

## 6. 复核项 6：本轮授权边界（git 只读核实）

### 6.1 命令与结果（全部 VERIFIED）

```
git branch --show-current          → feature/v16-p0-gap-closure
git rev-parse HEAD                 → ebc3fccbf4186b9651bbde2371ef884633d527f0
git status --porcelain=v1 -uall    → （空，工作区干净）
git diff --stat HEAD               → （空）
git rev-parse '@{u}'               → fatal: no upstream configured（当前分支无 push 目标）
git branch -r                      → 仅 origin/feature/v16-market-provenance-hardening（未出现本分支远端）
git branch --list main master 'Release*' 'release*'  → （空，本地无 main/master/Release 分支）
git reflog -12                     → HEAD@{0} 仅一次 checkout 到本分支；其后无 commit/push/merge/reset/rebase
git clean -ndX                     → 仅列 config/.env（忽略文件），无其他未跟踪新增
```

### 6.2 红线值核实

- `reports/day14-artifacts/config.freeze.json:16-17`：`"enableRealTrading": false`、`"allowBuySell": false`（VERIFIED，实读）。
- `day14-runner.mjs:50-51` `CONFIG_FREEZE` 常量同值；`:63-64` `verifyConfigFreeze` 显式断言二者必须为 false，否则抛错（VERIFIED）。
- `ENABLE_REAL_TRADING`（环境变量）在 cloud/worker 层强制 `'false'`：`cloud-entry.mjs:11`、`functions/api/jev/[[path]].js:719`（非 false 即 500 `RED_LINE`）、`equity_collector_v45/src/collector.mjs:38-41`、`gateway-routes.mjs`、`tests/t9_gateway_routes.mjs:86-87`（VERIFIED）。
- **双轨说明（VERIFIED）**：`day14-runner.mjs` 本身**不读取** `process.env.ENABLE_REAL_TRADING`，其红线为编译期常量 `CONFIG_FREEZE.enableRealTrading=false`；环境变量红线只作用于 cloud/worker 层。两者语义一致（均 false），但不是同一个全局开关。

### 6.3 结论

**PASS / VERIFIED**：本轮未 push、未碰 main/Release、无 force、无新提交、`allowBuySell=false`、`enableRealTrading=false`、cloud 层 `ENABLE_REAL_TRADING` 强制 false。历史提交（`git log`/`reflog` 中 `8da71f8` "direct egress"、PAT push、`api.github.com proxy` 等）均发生在 `ebc3fcc` 之前 / 旧分支，**不计入本轮**。末次 `git status -uall` 仅见两个**未跟踪未提交**的 `.md`（`reports/JEV_GAP_CLOSURE.md` 为 team-lead 并行新建、`reports/JEV_REAL_TRADING_SECURITY.md` 为本审计唯一产出），二者均不构成 push、分支切换或 main/Release 改动；`git diff --stat HEAD` 为空证明无任何被跟踪文件被改动。

---

## 7. 发现汇总（风险等级）

| ID | 复核项 | 发现 | 风险等级 | 证据等级 |
|----|--------|------|----------|----------|
| F-01 | §2 fail-closed | 正式风控导入失败仅 warn 不 throw，静默降级（fail-open） | **blocker** | VERIFIED |
| F-02 | §2 fail-closed | `fallbackRiskGate` 保留可交易路径 `approved:true` 放行能力 | **blocker** | VERIFIED |
| F-03 | §2 fail-closed | 正式门调用 `context:null`；`riskCheck.limits` 形参可覆盖限额 | high | VERIFIED |
| F-04 | §2 新实现 | trading-engineer 新实现未落盘，P0-B 修复状态不可核实 | **blocker（未验证）** | UNVERIFIED |
| F-05 | §1/§5 凭据域 | §9 独立凭据域未满足；复用 cf_*/github_pat_* 管理域风险；历史真实调用凭据位置未知 | high | VERIFIED（现状）/ UNVERIFIED（位置） |
| F-06 | §1 凭据隔离 | `.env` 被 gitignore、真实 `.env` 未跟踪、被跟踪源码无硬编码凭据模式 | low（PASS） | VERIFIED |
| F-07 | §3 网络 | 仓库具备真实付费模型/行情/全局 fetch 直连能力；server 层默认 fallback 全局 fetch | high（能力面） | VERIFIED |
| F-08 | §3 注入 | `runDay14` 仅 `fetchImpl` 且 dry 路径未接线；无 providerImpl/engineImpl、无默认 fixture | medium | VERIFIED |
| F-09 | §4 证据 | 哈希链跨重启断链、anchor/summary/state 覆盖写、无 runId、state 恢复无完整性校验 | high | VERIFIED |
| F-10 | §6 授权 | 本轮未 push/未碰 main·Release/红线 false | PASS | VERIFIED |
| F-11 | §1 元数据 | 历史 commit message 含凭据形态/代理字样（非完整值） | low | VERIFIED |
| F-12 | §3 本轮行为 | 「本轮实际外部调用 = 0」静态审计不可证，须他方书面确认 | medium（待确认） | UNVERIFIED |
| F-13 | §4 QA | `independentQA` 仅文件存在性即 PASS，缺链校验/重放/故障注入 | medium | VERIFIED |

---

## 8. 证据等级说明

- **VERIFIED**：本轮实读源码（`day14-runner.mjs`、`risk-gate.mjs`、`market.mjs`、`.gitignore`、`config.freeze.json`、`JEV_GAP_CLOSURE.md`、`DAY14_READINESS.md` 等）或实跑本地只读 git/grep 命令。本报告所有「基线缺陷 / 授权边界 / 凭据隔离 / 注入点」结论均属此类。
- **UNVERIFIED**：无法核实或实现未落盘——新实现修复状态（F-04）、本轮是否曾由他方进程发起外部 HTTP（F-12）、历史真实调用凭据存储位置（F-05 部分）、MASTER PLAN §9 原文（不在仓库内，按主理人任务书清单列出）、Binance.US 费率/交易所条款的本轮独立核实。
- **COMMUNITY-VERIFIED**：本轮**未触网、未做外部检索**，故**不产出**新的 COMMUNITY-VERIFIED 结论。报告中出现的「Binance.US 费率」仅为转引 `JEV_GAP_CLOSURE.md` §1.2 所标注的 COMMUNITY-VERIFIED 条目，**非本审计独立核实**，不作为本轮 GO 依据。

> 纪律：主理人不得把本报告中任何 **UNVERIFIED** 改写成 **VERIFIED**；不得删掉 blocker；不得把多角色结论平均掉。

---

## 9. 安全结论与晋级条件

### 9.1 DECISION（仅限 Security 范围）

**NO-GO。**

- 阻断依据：F-01 / F-02（基线 fail-closed 缺陷 VERIFIED）、F-04（新实现未落盘 UNVERIFIED）、F-05（§9 独立凭据域未满足）。
- 本轮授权边界（F-10）与凭据文件隔离（F-06）为 PASS，但**不足以**使 fail-closed 缺陷降级。
- 按 `JEV_GAP_CLOSURE.md` §4「任何一项 NO-GO → P0 整体 NO-GO」，本结论应作为 P0 安全 gate 的 NO-GO 输入；**P0 整体判定权在 team-lead**，本审计不越权整合、不替 QA/架构签字。

### 9.2 晋级为 GO 所需的安全条件（须逐项 VERIFIED 后复核）

1. 新实现落盘；对 §2.6 待复核清单 R1–R6 逐项实读 + 故障注入复核并全部通过（导入失败 throw、简化门无 approved 通路、非 HOLD 完整 context、limits/decision 不可被覆盖、默认 fixture、红线常量）。
2. 网络边界：真实路径做成 `fetchImpl`/`providerImpl`/`engineImpl` 三注入且默认 fixture；消除 server 层「未传注入即全局 fetch」的默认直连，或证明其在 P0 全程不可达。
3. 凭据域：真实交易 API 建立独立凭据域（不复用 `cf_*`/`github_pat_*`/DPAPI 管理凭据），并给出真实 Provider 凭据存储位置与最小权限/提币禁用证据（当前 UNVERIFIED → 须转 VERIFIED 或明确维持 DESIGN ONLY）。
4. 证据链：哈希链跨重启连续（从文件末条 `_prev` 续接）、anchor/summary/state append-only 或 runId 隔离、state 恢复带完整性校验、独立 QA 含链校验/重放/故障注入（由 independent-qa 出具）。
5. 本轮实际外部调用 = 0 由 trading-engineer / independent-qa 书面确认（F-12 转 VERIFIED）。
6. P9 保持 DESIGN ONLY：不创建实盘账户、不注入资金、不执行真实买卖；12 项清单在实盘晋级前逐项补齐。

### 9.3 不扩大授权声明

本审计全程只读：未修改任何 `.mjs` / `config.freeze.json` / `tests/` / 其他报告；未 push；未碰 main/Release；未触网；未调用任何真实 API/付费模型；未打开或输出任何凭据文件内容、值或 last4；未替 QA、架构或实现角色签字。唯一写入为 `reports/JEV_REAL_TRADING_SECURITY.md`（本文件）。

---

## 附录 A：本轮只读核实命令清单

| # | 命令 | 用途 |
|---|------|------|
| A1 | `ls -la <repo>` | 仓库根结构 |
| A2 | `git branch --show-current` / `git rev-parse HEAD` / `git log --oneline -8` / `git branch -a` / `git remote -v` | 分支/HEAD/历史/远端 |
| A3 | `git status --porcelain=v1 --untracked-files=all` / `git diff --stat HEAD` / `git clean -ndX` | 工作区干净度、忽略文件 |
| A4 | `git reflog -12` / `git branch --list main master 'Release*'` / `git rev-parse '@{u}'` | push/main/Release 边界 |
| A5 | `git check-ignore -v config/.env` / `git ls-files \| grep -iE '<敏感路径>'` / `git ls-files --error-unmatch config/.env.example` | 凭据文件跟踪状态 |
| A6 | `git grep -l -I -E '<凭据前缀模式>' -- '*.mjs' ...` | 被跟踪源码硬编码凭据模式（仅文件名） |
| A7 | `git ls-files reports/day14-artifacts/` | 证据产物跟踪状态（反证本轮无运行） |
| A8 | `git grep -n -E 'fetchImpl\|providerImpl\|engineImpl\|ENABLE_REAL_TRADING\|allowBuySell' -- '*.mjs' '*.ts' '*.js'` | 注入点与红线标识符 |
| A9 | `git grep -n -E '(globalThis\|global)\.fetch\|= fetch\b\|await fetch\(' -- '*.mjs'` | 全局 fetch 直连形态 |
| A10 | `git grep -n -E 'api\.(minimaxi\|binance\.us\|openai\|dashscope)\|chat/completions' -- '*.mjs'` | 外部 egress 端点（代码行） |
| A11 | `Read` 下列文件（实读源码/配置/报告） | 见附录 B |

## 附录 B：实读文件清单

| 文件 | 关键行 |
|------|--------|
| `apps/command_center_3d/scripts/day14-runner.mjs` | 全文（L22-303），重点 L50-66 / L69-96 / L108-133 / L164-181 / L187-256 / L258-290 |
| `apps/command_center_3d/server/ai/risk-gate.mjs` | 全文（L1-182），重点 L20-25 / L91-106 / L108-170 / L176-181 |
| `apps/equity_collector_v45/src/market.mjs` | 全文（L1-184），重点 L16-18 / L40-54 / L61 |
| `.gitignore` | 全文（L1-190），重点 L120-133 / L141-149 / L182 |
| `reports/day14-artifacts/config.freeze.json` | 全文（L1-19） |
| `reports/JEV_GAP_CLOSURE.md` | 全文（L1-107），重点 §1 / §1.1 / §2 / §3 / §4 |
| `reports/DAY14_READINESS.md` | 全文（L1-184），历史自评，仅作口径冲突对照 |

---

*第一轮安全审计结论：NO-GO（Security 范围）。0 项伪造 PASS，0 项输出凭据内容，0 项越权签字。*

---

# §10 第二轮复核（实现落盘后）：R1–R6 逐项 + F 项状态 + 更新后 DECISION

> 触发：team-lead 消息「重新触发安全 R1-R6 复核（实现已落盘）」。性质仍为**只读安全复核**，唯一写入本文件；不改任何源码/测试/旧 freeze，不 push、不触网、不输出凭据内容、不替 QA 签字。
> **本轮结论仅依据本审计独立实读源码/产物/只读命令**（team-lead 的提示位置不构成我的结论），证据等级逐条标注。

## §10.0 复核输入、版本锚点与落盘事实（VERIFIED）

```
git branch --show-current          → feature/v16-p0-gap-closure
git rev-parse --short HEAD         → ebc3fcc（仍无新提交）
git status --porcelain -uall       → M apps/command_center_3d/scripts/day14-runner.mjs
                                     ?? apps/command_center_3d/scripts/day14-verify.mjs
                                     ?? server/ai/tests/day14-p0-{lib,contracts,runner}.mjs（independent-qa 范围）
                                     ?? reports/day14-artifacts/_verify/... 与 runs/<runId>/...（运行产物）
git diff --stat HEAD               → 1 file changed, 690 insertions(+), 185 deletions(-)（仅 runner）
```

- **版本演进（本轮复核期间捕获 3 个并行版本，均 VERIFIED）**：
  | 版本 | 字节/行数 | mtime | sha256 |
  |------|-----------|-------|--------|
  | V1 | 37310 / 790 | 06:50:37 | （未固定，team-lead 提示的 37158 字节为其早期读数） |
  | V2 | 38203 / 807 | 06:57:53 | `415cdd36e11c6f2b4098334a2a8225400dd49c26dd371dc2ab3655b74bc4a72d` |
  | **V3（本轮最终复核对象）** | **39606 / 829** | **07:01:05** | **`002653fed0d48e3cd20c95a397400d93830192ca87f60a8361a88b3aa3726d4a`** |
- **V1→V2 增量**：L774-789 新增 `export { sha256Hex, verifyConfigFreeze, makeJournal, reconcile, makeFixtureFetcher, makeFixtureProvider, RED_LINES, CFG, COST_MODELS, LEGACY_FREEZE_EXPECTED, RISK_LIMITS }`，注释明示「纯增量，不改变运行行为」，供 independent-qa 单测。
- **V2→V3 增量（本审计已重新实读 L620-711 核实）**：L640-659 新增 **provider 意图校验 / 失败 fail-closed 子分支**——`intentValid` 校验 `action∈{HOLD,BUY,SELL}`、非 HOLD 时 `quantity>0`（L642-644），`providerFailed`（L645）时记 `NO_ACTION`（**不伪装成模型 HOLD**）、decision 恒 HOLD、`orderable=false`、`execution="NONE"`、`gateName="formal-risk-gate(fail-closed-provider)"`，并新增 `failClosedReason` 日志字段（L696-697）。此为 **fail-closed 加强**，对 R1–R6 结论无负面影响。
- **对 V3 的 R1–R6 关键行重核（VERIFIED）**：L141/L147/L149/L562 四处 throw 仍在、无 `console.warn`/无 `fallbackRiskGate` 定义/无 `approved` 字面量赋值；L152 `RISK_LIMITS` 冻结、L552-555 默认 fixture、L630 fail-closed `context:null`、L655-656 与 L665-666 均 `buildRiskContext` + `limits: RISK_LIMITS`、L670 `RED_LINES.allowBuySell`、L790 `INTERNAL_SANITY_ONLY` 全部保持。**R1–R6 结论对 V3 成立。**
- **条件 C7 强调**：文件在复核结束后仍可能继续变更；最终提交版本须由 team-lead 锁定并与 `002653fe…` 比对，若不同须重新触发本复核。
- 实读文件：`day14-runner.mjs`（807 行全文）、`day14-verify.mjs`（253 行全文）、`vendor/nr_trusted/functions/_lib/paper-server.mjs`（关键段 L493-511、L1102-1106）、`market-guard.mjs:44-49`、运行产物 `run.json` / 主 run `risk.jsonl` / `_verify/t4a` `risk.jsonl`+`decisions.jsonl` / `_verify/t7` `risk.jsonl`。
- 被跟踪的 `server/ai/risk-gate.mjs`、`reports/day14-artifacts/config.freeze.json` **均未修改**（`git status` 无 M，旧 freeze 由 `day14-verify.mjs` T6 用 sha 前后比对验证未变）— 符合「不改正式风控源码/旧 freeze」纪律。

## §10.1 R1–R6 逐项复核

### R1 导入失败 fail-closed（无 warn-and-continue、无静默降级）— **VERIFIED 闭环**

| 主理人提示位置 | 本审计实读结果 |
|----------------|----------------|
| L134-142 reuse import catch | `throw new Error("[day14] FATAL: reuse import (provenance/engine/schema) failed — fail-closed: " + e.message)` ✓ |
| L143-148 formal risk-gate import catch | `throw new Error("[day14] FATAL: formal risk-gate import failed — fail-closed termination: " + e.message)` ✓ |
| L149 类型校验 | `if (typeof formalRiskGate !== "function") throw new Error("[day14] FATAL: riskCheck not exported as function")` ✓ |
| L561-562 riskCheckImpl 校验 | `if (typeof riskCheckImpl !== "function") throw new Error("... FATAL fail-closed: formal riskCheck unavailable — termination (no records, no approvals)")` ✓ |
| 额外 | L563-564 `typeof fetchMarket/evaluateCycle/!engineImpl` 缺失亦 throw；顶层 `await import` 本身失败会使整个 ESM 模块无法加载（Node 行为，强于 catch） |

- `git grep -n 'console.warn' -- day14-runner.mjs` → **无匹配**；`git grep -n -E 'fallbackRiskGate|approved\s*[:=]\s*true'` → 仅 L19 注释与 L670-671 说明性注释，**无函数定义、无 approved 字面量赋值**。
- 其余 catch 均为 throw 或合法 fail-closed 降级：L117-118（freeze JSON parse → throw）、L165-166（journal JSON parse → throw）、L315（fixture syms parse，仅 fixture 内部）、L609-610（行情异常 → `acq.verified=false` → 走 `!acq.verified` NO_ACTION 分支，**不放行**）、L635-639（provider 异常 → `ok:false`、intent 默认 HOLD）、L536-538（gitSha → UNKNOWN，非风控路径）。
- `day14-verify.mjs` T3 实证：注入 `riskCheckImpl:null` 应终止且不产生 risk 日志/`approved:true`（L117-134）。
- **判定：所有正式风控/核心模块不可用路径均 throw；无 warn-and-continue、无静默降级到简化门。F-01 闭环。**

### R2 简化门删除 / 无「可交易路径放行能力」— **VERIFIED 闭环（默认链路）＋ medium 残留（注入面）**

- 全文件无 `fallbackRiskGate` 函数定义（仅 L19 注释「彻底删除」）；`approved` 唯一来源是 `riskCheckImpl({...})`（L630、L647），默认 `formalRiskGate`（L555）。
- `orderable`（L649）= `gate.approved===true && gate.code===RISK_APPROVED && decision.action!=="HOLD"` —— 是对正式门结果的**收窄**，非独立批准通道。
- **L651 `if (orderable && RED_LINES.allowBuySell)`**：`RED_LINES`（L55 `Object.freeze({enableRealTrading:false, allowBuySell:false})`）是模块常量、**不在 `runDay14(opts)` 解构中（L546-558）**，外部不可覆盖 → 真实成交路径恒不进入；L654 `simulateFills` 仅本地内存（L440-471 `executeIntent` 实读：只改内存 account/harness，无网络/无 DB）；L659 `SHADOW_GATED` 只记日志。
- 产物实证（VERIFIED 实读）：`_verify/t4a` 注入 BUY intent → `riskCode:"RISK_APPROVED"` / `orderable:true`，但 `decisions.jsonl` `execution:"SHADOW_GATED"`（**未真实下单**）；`_verify/t7` 未验证行情 → `NO_ACTION` / `context:null` / `orderable:false` / 无 `RISK_APPROVED`。
- **残留（medium，F-15）**：`riskCheckImpl`（L555）是**可注入参数**，调用方（QA 故障注入）可替换整个风控门，返回任意 `approved/code` → 构成「调用方可覆盖风控判定」通道。默认链路 `= formalRiskGate` 安全；缓解是 `RED_LINES.allowBuySell=false` 不可注入，故即便注入恶意门也无法真实下单。**P3 解锁 allowBuySell 前必须收紧**（锁定不可注入，或在 `orderable` 判定前对 `gate.code` / provenance 做二次独立校验）。详见 §10.3。
- **判定：F-02 闭环（默认链路已删除简化放行能力）；注入面如实列为 GO 前置条件，不隐去。**

### R3 非 HOLD 完整 context — **VERIFIED 闭环**

- L646 `const context = buildRiskContext(acq, decision)`；L647 `riskCheckImpl({ decision, context, nowMs, limits: RISK_LIMITS })`。
- `buildRiskContext`（L363-372）：`if (!acq.verified) return null`；返回 `{ marketContextId, symbol, markPriceUsd, priceTs, markQuality:"verified" }`。字段为 camelCase，被 `risk-gate.mjs` 的 `normalizeContext`（L70-83）映射为 `market_context_id / symbol / price / price_ts / mark_quality / expires_at`。
- `acq.verified = guard.ok && market.dataQuality==="VERIFIED"`（L395-396），依赖真实门控链路 `fetchMarket(fetchImpl,...) → buildMarkContracts → evaluateCycle`（L375-394），**非凭空写死**；`markPriceUsd = acq.marksUsd[decision.symbol] ?? null`（L368），标的不匹配时 price=null → 正式门 `PRICE_UNAVAILABLE` 拒绝（fail-closed）。
- 产物实证（VERIFIED）：`_verify/t4a/risk.jsonl` 含 `gate:"formal-risk-gate"`、`code:"RISK_APPROVED"`、`context.markQuality:"verified"`、`evaluated:{notional_usd:70, mark_price_usd:70000, context_age_ms:0, allowed_age_ms:30000, market_context_id:...}`、`riskLimits` 四项、`confidenceIgnored:0.5` → 正式门**全量校验**（schema/白名单/verified/symbol/ts/新鲜度/price/notional/quantity），非简化门。
- L630 `context:null` 仅在 `if(!acq.verified)` fail-closed 分支，decision 恒由 L629 构造为 HOLD；`riskCheck` HOLD 分支（L100-106）不读 context，`orderable` 在该分支恒 false（L622 初始化、分支内不修改）→ 合法。
- **如实标注**：`expiresAt` 未提供（`ctx.expires_at=null`），但正式门该字段**可选**（L134-139 仅存在时校验），不构成缺口；`markQuality:"verified"` 为 runner 侧断言，可信度由 `acq.verified` 门控。
- **判定：F-03 闭环。**

### R4 limits / decision / 风控参数不可被覆盖 — **VERIFIED 部分闭环 ＋ 新发现 medium（F-14）**

- ✓ L152 `RISK_LIMITS = Object.freeze({ ...DEFAULT_RISK_LIMITS })`；L545-558 `runDay14(opts)` 解构**不含 limits**；L630/L647 显式传 `limits: RISK_LIMITS`（内部冻结常量）；产物 `riskLimits` = `{minNotionalUsd:5, maxNotionalUsd:1000, maxQuantity:10, maxContextAgeMs:60000}` 实证。
- ✓ `RED_LINES`（L55）模块常量，外部不可覆盖；`decision` 由内部 `buildDecision`（L343-360）构造，经正式门 `validateDecision`（L94）校验。
- ⚠ 注入面：`providerImpl`（L553）可注入 → 影响 intent → 影响 decision 字段；`riskCheckImpl`（L555）可注入 → 可绕过整门（同 R2/F-15）。默认均为 fixture/正式门。
- ⚠ **新发现 F-14（medium）**：`LEGACY_FREEZE_EXPECTED`（L59-77）定义 `maxNotionalPerTrade:200 / maxTrades:6 / maxExposurePct:0.2 / dailyLossBreachPct:1 / drawdownBreachPct:2`（L66-70），但 `git grep` 全文显示 `CFG.maxNotionalPerTrade`（L83）除定义/导出外**无任何使用点**；`maxTrades/maxExposurePct/dailyLossBreachPct/drawdownBreachPct` 连 `CFG` 都未进入；`s.riskBreach` 恒 false（L271 初始化，L297/L520/L596/L733 仅读取，全文无置 true）→ `RISK_BREACH` 自动暂停恒不触发。实际生效风控上限仅正式门 `maxNotionalUsd:1000`，与冻结配置声明的 200 口径不一致；「全局资金风控」(P9 #8) 实际弱于冻结配置声明。P0 阶段 `allowBuySell=false` 无实际后果，**P3 解锁前须统一口径并补齐日亏损/回撤/持仓/最大交易数判定**。
- **判定：R4 的「外部覆盖」面已闭环；冻结风控参数未生效与口径不一致列为 medium 条件，不隐去。**

### R5 默认无全局 fetch 直连 + 绕过 server 两个默认-fetch 模块 — **VERIFIED 闭环（runner 链路）＋ medium 残留（能力面）**

- L552-554 默认 `fetchImpl = makeFixtureFetcher(nowMs)`、`providerImpl = makeFixtureProvider()`、`engineImpl = frozenEngine`、`riskCheckImpl = formalRiskGate`；`dry=true`（L548）。
- `makeFixtureFetcher`（L304-327）仅响应 `/api/v3/time`、`/api/v3/ticker/price`，其它 URL `throw FIXTURE_UNEXPECTED_URL`；`makeFixtureProvider`（L330-340）恒 HOLD/no-signal、`FIXTURE_NO_LLM_CALL`。
- `git grep -n -E 'provider-adapter|ai-core|globalThis|process\.env' -- day14-runner.mjs day14-verify.mjs` → **无匹配**：runner 新链路**完全不导入**上轮指出的 `server/ai/provider-adapter.mjs:270` 与 `server/engine/_lib/ai-core.mjs:74`（两者在未传注入时 fallback 全局 fetch）→ **绕过成立**。
- 新链路模块核实：`market.mjs` 用注入 `fetcher`（非全局 fetch，且有 host/endpoint 白名单）；`market-guard.mjs`/`market-contract.mjs` 无 fetch；`decision-schema.mjs` 仅 JSON Schema `$schema` 字符串；`risk-gate.mjs` 无 fetch。`resolveNetworkContext({fetcherInjected:true})`（L598）→ `market-guard.mjs:44-49` 返回 `NETWORK_CONTEXT.TEST_SHIM`（"local_test_shim"），纯函数无触网。`acq.isReal:false`（L405）硬编码。
- `verifyConfigFreeze`（L112-128）只有 `readFileSync` + 逐字段校验，**无 writeFileSync**；`git grep writeFileSync` 的 6 处（L251/252/289/532/759/774）全部写入 `runs/<runId>/` 目录内 → **旧 `config.freeze.json` 永不重写** ✓。
- ⚠ **残留能力面（medium）**：冻结引擎 `vendor/nr_trusted/functions/_lib/paper-server.mjs` 的**服务端 HTTP handler 路径**（L719/L783/L932）含 `dbFetch(env)`（L89 `typeof env.__fetch==="function" ? env.__fetch : fetch` → 默认全局 fetch）→ `fetchJson`（L203-210）→ `API_BASE="https://data-api.binance.vision/api/v3"`（L6，被 `market.mjs` 注释明确列为 FORBIDDEN）。但 runner 的 `engineImpl = frozenEngine = __test`（L1102-1106 实读：仅 `createAccount/summarize/simulateNewOrder/processLimitOrders/validateInput/updateRiskMarks` 等纯内存函数），`simulateNewOrder`（L495-511 实读）只用传入 `context.book/marks`，**不调用 fetchMarketContext/dbFetch** → 本轮 runner 链路不触发该路径。历史脚本（`day1-shadow-minimax.mjs` / `day12` / `day13` / `verify-minimax-keys.mjs` 的 `api.minimaxi.com`、`api.binance.us` 真实外呼）能力面仍在，不因 runner 修复而消失。
- **判定：F-07（runner 链路）闭环；能力面残留与注入面一并列为 P3 前收敛条件。**

### R6 本轮实际外部调用 = 0 — **UNVERIFIED（按他方书面确认处理，不改写为 VERIFIED）**

- team-lead 转述 trading-engineer 7 段回传书面声明：「真实 Binance.US egress / MiniMax 付费推理 / 真实费率实测 / off-box 锚定均未执行」，provenance 标 `fixture-verified` / `local_test_shim` / `isReal:false`；`day14-verify.mjs` 14 项本地 fixture。
- 本审计实读 `day14-verify.mjs`（253 行）：固定注入时钟 `NOW=1770000000000`、注入 `badFetcher`/`buyProvider`、`spawnSync(process.execPath,[RUNNER,...])` 仅**本地 node 进程**（T1b 真实进程重启）、`LEGACY_FREEZE` sha 前/后比对、**无全局 fetch / 无真实 URL 请求 / 无 curl**；14 项 check 均在 `_verify/<ts>/` 临时目录。
- 产物佐证（VERIFIED 实读产物，**但不能证明无外部调用**）：`run.json` `redLines.{noEgress,noPaidModel,noOrderSubmit}=true`、`costModels.venue_verified` 为 `null / UNVERIFIED / NOT_MEASURED`（诚实未冒充实测）；主 run `risk.jsonl` 全为 `formal-risk-gate` / `RISK_HOLD_NO_ORDER` / `orderable:false`；t4a `SHADOW_GATED`；t7 `NO_ACTION` / `NO_CALL_UNVERIFIED_MARKET`。
- **判定：保持 UNVERIFIED**。静态只读审计无法证明「无进程曾发起外部 HTTP」；independent-qa 的同项确认由 team-lead 待回传后转我。若有任一真实调用，P0 红线即破 → 立即升级 blocker 并重判。**不写成 VERIFIED。**

**【第三轮补注 · C1 已满足 / R6 仍 UNVERIFIED】**（team-lead 中转，2026-11-11；上方原条件与判定文字**不删**）
> **引用口径（第四轮修正，详见 §10.9）**：下方 `JEV_GAP_CLOSURE.md` L153-156 与 `JEV_INDEPENDENT_QA.md` L15 / L93 / L297-299 / L301-304 等裸行号**一律为 v2 快照（359 行 / `0d42b43d…`）**，原文字保留不删；权威锚点 = `JEV_GAP_CLOSURE.md` **§4.3 F-12 外部调用确认**（快照指纹 `04494fab…`）+ `JEV_INDEPENDENT_QA.md` **§6A F-12 书面确认段（可逐字引用）+ §6 动态计数**（快照指纹 `aa390c08…`，现行 L16 / L95 / L299-301 / L303-305）。
- team-lead 已正式中转**两条彼此独立、不得互相替代或平均**的书面证据，本审计已用 `sed -n` / `grep -n` 逐字核实出处：
  1. **trading-engineer 书面声明**：登记于 `reports/JEV_GAP_CLOSURE.md` **L153-156（§4.3 F-12 外部调用确认）**——真实外部 HTTP 0 / 付费模型·LLM 调用 0 / 交易所或任意订单提交 0 / 生产 DB 写入 0，合计 **0**。依据：runner 内无 URL / 无 `globalThis.fetch` / 未 import `provider-adapter.mjs`、`ai-core.mjs`；`fetchImpl` 默认纯内存 fixture；provider 日志 `FIXTURE_NO_LLM_CALL`、`costUsd=0`；快照 `isReal:false`、`dataMode:"fixture"`；唯一子进程为本地只读 `git rev-parse HEAD`。
  2. **independent-qa QA 侧动态计数**：`reports/JEV_INDEPENDENT_QA.md` **L15**（F-12 QA 侧独立计数 = 0，明确「实现方书面声明之外的**第二条独立证据**，不可互相替代」）、**L93**（F-12 零调用计数在 QA 自身范围内标 VERIFIED）、**L297-299**（`EVIDENCE parent globalThis.fetch is the NONET guard = true` / `EVIDENCE child processes spawned (sandboxed local node) = 33` / `EVIDENCE P0-QA-NONET-ATTEMPT hits across ALL child stderr (blocked egress) = 0`）、**L301-304**（静态网络 import = 0；父进程与每个子进程 driver 共 2 处把 `globalThis.fetch` 硬替换为「记录 + throw」NONET 守卫；33 个子进程全为沙箱本地 node 进程；stderr 命中 0 → 守卫从未触发；**边界：该动态计数仅覆盖 QA 进程树**。team-lead 转述为 L302-304，本审计逐字核实实际为 L301-304）。
- **主理人裁定（如实记录）**：C1 关闭的含义是「他方书面确认已经 team-lead 中转到位，晋级前置条件已满足」，**不是**把本审计静态结论升级。R6 在本审计证据等级里**仍保持 UNVERIFIED**，不得改写成 VERIFIED、不得替 QA / 实现方签字、不得把两条（连同本审计静态核实共三条）证据平均成一条。
- **回退触发条件未成立**：两条证据均未发现任何真实外部调用 → 原「如有任一真实调用 → 升级 blocker + 回退 NO-GO」触发条件**未成立**，如实写明。
- **方法论边界保留**：QA 的运行时动态计数仅覆盖其自身进程树；本审计静态只读审计仍无法证明「全仓库任意进程在运行期零外呼」，故 R6 的 UNVERIFIED 边界继续保留。

## §10.2 F 项状态更新表（保留全部编号，不删 blocker）

| ID | 第一轮 | 本轮状态 | 依据 |
|----|--------|----------|------|
| F-01 | blocker | **已闭环 VERIFIED** | R1：导入失败全路径 throw、无 console.warn |
| F-02 | blocker | **已闭环 VERIFIED** | R2：简化门删除、approved 唯一来源正式门、t4a SHADOW_GATED |
| F-03 | high | **已闭环 VERIFIED** | R3：非 HOLD 完整 context + t4a `evaluated` 全量校验 |
| F-04 | blocker（未落盘 UNVERIFIED） | **时点前提解除 VERIFIED** | 实现落盘，807 行实读 + verify + 产物；本轮 R1-R5 已核实 |
| F-05 | high | **仍未闭环（high，非 P0 blocker）** | §9 独立凭据域未落地；P9 = DESIGN ONLY / 禁止实施，P0 不接触真实凭据/资金 |
| F-06 | low（PASS） | **维持 VERIFIED** | `.env` 隔离规则未变、真实 `.env` 未跟踪、被跟踪源码无硬编码凭据模式 |
| F-07 | high（能力面）/ medium（注入） | **runner 链路闭环 VERIFIED；能力面 medium 残留** | R5 五注入+默认 fixture+绕过 server 两模块；历史脚本/冻结引擎 handler 能力面仍在 |
| F-08 | medium（注入不全） | **已闭环 VERIFIED** | `fetchImpl/providerImpl/engineImpl/riskCheckImpl/nowMs` 五注入且默认 fixture |
| F-09 | high | **主体闭环 VERIFIED；low 残留** | L157-193 启动全量链校验+重算 `_hash`+恢复末尾哈希、L205-263 runId 隔离、state 原子写、T1a/T1b/T2/T6 实证；`day14-anchor.json`/`day14-summary.json`/`recovery-report.json`/`state.json` 仍 `writeFileSync` 覆盖写但均落在 `runs/<runId>/`（不跨 run 静默重写） |
| F-10 | PASS | **维持 VERIFIED** | HEAD 仍 `ebc3fcc`、无新提交、无 upstream/未 push、无 main/master/Release、红线 false |
| F-11 | low | **维持 VERIFIED** | 历史 commit message 元数据，未新增 |
| F-12 / R6 | medium（待确认） | **C1 条件已满足 / R6 仍 UNVERIFIED（静态只读方法论边界）** | 静态只读不可证本轮外部调用=0；两条独立书面证据经 team-lead 中转（`JEV_GAP_CLOSURE.md` L153-156 §4.3；`JEV_INDEPENDENT_QA.md` L15/L93/L297-299/L301-304），均未发现真实调用 → 回退触发条件未成立；**C1 关闭但静态结论不升级、不替 QA 签字、不与实现方证据平均**（详见 §10.1 第三轮补注） |
| F-13 | medium | **已闭环 VERIFIED** | `internalSanity`（L757-772）明确 `scope:"INTERNAL_SANITY_ONLY — 非独立 QA，不得据此宣称整体验收通过"`，不自证整体绿；独立 QA 测试文件已落盘（测试质量由 independent-qa 签字，本审计不越权） |
| **F-14（新）** | — | **medium** | 冻结风控参数（maxNotionalPerTrade/maxTrades/maxExposurePct/日亏损/回撤）未参与判定、`riskBreach` 恒 false、200 vs 1000 口径不一致（R4） |
| **F-15（新）** | — | **medium** | `riskCheckImpl` 注入面允许调用方替换风控门；默认链路安全、被 `RED_LINES.allowBuySell=false` 阻断真实成交，P3 前须收紧（R2/R4） |

> **仍为 blocker 的项：P0 Security 范围内已无 blocker。** 但须显式说明两个语境：① F-05 在「进入真实运行 / P3 解锁」语境下仍是 blocker（§9 独立凭据域未满足）；② R6 若 independent-qa 回传前无法确认、或发现任何真实外部调用，F-12 立即升级为 blocker 且本结论回退 NO-GO。
> **【第三轮补注】** independent-qa 的 QA 侧计数已回传并经 team-lead 中转，两条独立证据**均未发现任何真实外部调用** → 上述 ② 的回退触发条件**未成立**（C1 已满足）；但 ①（F-05 / P9 DESIGN ONLY）与 ② 的 blocker 语境文字**原样保留不删**，且 R6 静态结论仍为 UNVERIFIED。

## §10.3 新增残留风险（如实标注，未隐去）

- **F-14（medium）冻结风控参数未生效 / 口径不一致**：详见 R4。影响：P9 #8「全局资金风控」实际弱于冻结配置声明；P0 无实际后果（不下单），P3 前必须统一 `RISK_LIMITS` 与 `maxNotionalPerTrade` 口径，并让日亏损/回撤/持仓/最大交易数 breach 参与 `evaluatePause`（当前 `s.riskBreach` 恒 false）。
- **F-15（medium）`riskCheckImpl` 注入面**：详见 R2。影响：调用方（测试钩子）可替换整门；默认安全 + 红线阻断，但 P3 解锁 `allowBuySell` 前须锁定或二次校验，否则在真实运行下会成为绕过正式门的通道。
- **F-07 能力面（medium）**：冻结引擎 HTTP handler 的 `dbFetch`→全局 fetch→`data-api.binance.vision`（FORBIDDEN 主机）+ 历史真实外呼脚本。本轮 runner 链路不触发，但仓库「具备真实调用能力」的事实仍在，P3 前须收敛/证明不可达。
- **F-09 残留（low）**：anchor/summary/recovery/state 覆盖写（runId 内），`active-run.json` 指针覆盖写（run.json 有 configHash 校验）。runId 隔离 + journal 全量链校验已消除「跨 run 静默重写旧证据」，旧根 `snapshots.jsonl`（旧版 synthetic-dry）未被覆盖。
- **版本动态性（条件 C7）**：复核期间文件由 37310→38203→39606 字节变化，结论最终锚定 sha256 `002653fe…`（829 行）；若最终提交版本不同，须重新触发。**【第三轮更新】** team-lead 已实测并最终冻结该锚点（runner 末尾 `0a`、verify `7b6fd539…`、旧 freeze `d4edce64…`、HEAD `ebc3fcc` 未 commit），本审计本轮 `sha256sum` 独立核实四项逐字一致 → **C7 已满足，指纹未漂移，无需重新触发**。
- **其它主理人基线保留项（不属本审计技术结论，一律保留不删）**：`JEV_GAP_CLOSURE.md` §1.1 的 **CI 覆盖缺口**（`.github/workflows/v16-market-provenance.yml` L8 push 触发硬编码旧分支名，本分支仅跑 `ci.yml`，V1.5/V1.6 专有断言不触发，属 DevOps ownership）；**真实资金 NO-GO**（P0 全程未接触真实资金、未创建实盘账户、未解除 §9 独立凭据域缺口）。

## §10.4 晋级前置条件（GO WITH CONDITIONS 的 CONDITIONS）

1. **C1（R6 / UNVERIFIED）【已满足 · 2026-11-11】**：independent-qa 书面确认「本轮实际外部调用=0」并由 team-lead 转我；在此之前结论为有条件，不得据此宣称 P0 红线已 VERIFIED。任何真实调用 → blocker + 回退 NO-GO。（**上方原条件文字保留不删**）
   - **关闭说明**：team-lead 已正式中转**两条彼此独立、不得互相替代或平均**的书面证据，本审计已逐字核实出处——① `reports/JEV_GAP_CLOSURE.md` **L153-156（§4.3 F-12 外部调用确认）**：trading-engineer 书面声明真实外部 HTTP / 付费模型·LLM / 订单提交 / 生产 DB 写入均 0，合计 **0**；② `reports/JEV_INDEPENDENT_QA.md` **L15**（QA 侧独立计数 = 0，明确为「第二条独立证据，不可互相替代」）、**L93**（F-12 零调用计数在 QA 自身范围内标 VERIFIED）、**L297-299**（NONET 守卫 = true / 33 个沙箱本地 node 子进程 / `P0-QA-NONET-ATTEMPT` stderr 命中 0）、**L301-304**（静态网络 import = 0 / 父进程与子进程 driver 共 2 处 NONET 守卫 / 守卫从未触发 / **边界：仅覆盖 QA 进程树**；team-lead 转述 L302-304，本审计核实实际 L301-304）。
   - **触发条件未成立**：两条证据均未发现任何真实外部调用 → 原「任何真实调用 → blocker + 回退 NO-GO」触发条件**未成立**，如实写明。
   - **关闭的准确含义**：C1 关闭 = 「他方书面确认已经 team-lead 中转到位、晋级前置条件已满足」，**不是**把本审计静态结论升级；R6 在本审计证据等级里**仍保持 UNVERIFIED**，不得改写成 VERIFIED、不得替 QA / 实现方签字、不得把三条证据平均成一条。QA 运行时动态计数仅覆盖 QA 进程树，本审计「静态只读无法证明全仓库运行期零外呼」的方法论边界继续保留。
2. **C2（F-05）**：P9 维持 **DESIGN ONLY**；不得创建实盘账户、注入资金、执行真实买卖、解除 §9 独立凭据域缺口；真实运行前须建立独立凭据域（不复用 `cf_*`/`github_pat_*` 管理凭据）+ 最小权限/提币禁用证据。
3. **C3（F-15）**：P3 解锁 `allowBuySell` 前，收紧 `riskCheckImpl` 注入面（锁定不可注入或对 `gate.code`/provenance 二次独立校验）。
4. **C4（F-14）**：P3 前统一风控 limits 口径（200 vs 1000），补齐日亏损/回撤/持仓/最大交易数 breach 判定，使 `riskBreach` 可被真实触发。
5. **C5（F-07 能力面）**：P3 前收敛冻结引擎 handler 的 `dbFetch` 全局 fetch 默认 + `data-api.binance.vision` FORBIDDEN 主机，及历史真实外呼脚本的不可达性证明。
6. **C6（F-09 残留）**：知悉 anchor/summary 在 runId 内覆盖写；如需「同 runId 多次运行的锚点全部留存」，改为 append-only 或按 runId+序号分文件。
7. **C7（版本锁定）【已满足 · 2026-11-11】**：team-lead 锁定最终 runner 版本；若与最终锚点不同，重新触发本复核。最终锚点为 `day14-runner.mjs` sha256 `002653fed0d48e3cd20c95a397400d93830192ca87f60a8361a88b3aa3726d4a`（829 行 / 39606 B / 末尾 `0a`）、`day14-verify.mjs` `7b6fd5391ecd71dd036ecafd1ac2629a531c021352b535d23e4359dad0ebfbca`、旧 `config.freeze.json` `d4edce64c48bf15ab6803ce9ea08a164404d84642c61065d74550d7983208658`（未改）、HEAD `ebc3fcc` 未 commit。本审计本轮 `sha256sum`/`wc -l`/`git rev-parse`/`git diff --stat` 独立核实四项逐字一致 → **指纹未漂移，无需重新触发**（历史抖动 V1 37310/790 → V2 38203/807 `415cdd36…` → V3 39606/829 `002653fe…` 见 §10.0）。
8. **C8（不越权）【QA 回传已到位 / 边界继续保留】**：`independent-qa`（P0-F）结论尚未回传，本结论不替其签字；P0 整体门禁由 team-lead 依 `JEV_GAP_CLOSURE.md` §4 整合，任何一项 NO-GO → P0 整体 NO-GO。（**上方原条件文字保留不删**）**【第三轮补注】** QA 侧 F-12 独立计数已由 `reports/JEV_INDEPENDENT_QA.md` 出具并经 team-lead 中转（C1 已满足）；但「**本结论不替 independent-qa 签字**」与「**P0 整体门禁由 team-lead 整合**」两条边界**继续保留**——本审计不读取、不评价 QA 测试质量，不宣布 P0 整体 PASS；C8 作为边界声明而非待办条件保留。
   - **【第四轮补注 · QA 现行 DECISION（team-lead 明确要求）】** C8 原文「independent-qa 结论尚未回传」已成历史表述（**原文保留不删**）：**QA 现行 DECISION = `GO WITH CONDITIONS（仅限 QA 范围；C1 已关闭，剩余 C2 / C3）`**（`JEV_INDEPENDENT_QA.md` **§9 QA 范围裁决**，现行 L358）。**演变留痕**：`GO WITH CONDITIONS → GO（无条件）→ GO WITH CONDITIONS`（§9 DECISION 演变表，现行 L369-378）。**归因**：系 team-lead 就同一 C1 事项先后发出两条口径不一致的消息所致，属**主理人口径不一致，非 QA 伪造**（如实转引 `JEV_GAP_CLOSURE.md` **§4.6 DEV-05**）；QA 未删任何断言、未把 FAIL 变绿，最终采用更保守的 `GO WITH CONDITIONS` 作为权威结论。**「本结论不替 independent-qa 签字」与「P0 整体门禁由 team-lead 依 §4 整合」两条边界继续保留**，不宣布 P0 整体 PASS / 生产 GO；C8 仍作为边界声明而非待办条件保留。详见 §10.9.4。

## §10.5 更新后 DECISION（仅限 Security 范围）

**GO WITH CONDITIONS（仅限 Security 范围）。**

- **依据（本审计独立实读，非采信实现方自评）**：第一轮 NO-GO 的 3 个根因中，① F-01/F-02（基线 fail-closed 缺陷）经 R1/R2/R3 实读源码 + `_verify/t4a`/`t7` 产物实证 **VERIFIED 闭环**；② F-04（新实现未落盘）因实现落盘并实读最终 829 行（V1/V2/V3 三版本，V3 已重核 R1–R6 关键行）+ `day14-verify.mjs` + 产物而**解除**；③ F-05 按 `JEV_GAP_CLOSURE.md` §2 的 **P9 = DESIGN ONLY / 禁止实施** 门禁，在 P0（paper/SHADOW、不接触真实凭据/资金）语境下**不构成 P0 blocker**（但仍是进入真实运行的 blocker，见 C2）。
- **不是「把 NO-GO 与实现方 GO 平均掉」**：结论升级基于本轮独立 VERIFIED 证据（R1–R5 逐项实读 + `git grep` 交叉验证 + 运行产物实证），且**完整保留**所有未闭环项（F-05、R6 UNVERIFIED、F-14、F-15、F-07 能力面、F-09 残留、版本动态性）并列为 C1–C8 前置条件；任一 blocker 出现即回退 NO-GO。
- **边界**：本结论仅表示 **P0 阶段 Security 代码边界（fail-closed / 注入 / 默认 fixture / 证据链）复核通过**；**不代表** P0 整体 PASS、**不代表**可晋级 P1、**不代表**可解除 P9、**不替** `independent-qa`（P0-F）或 `trading-engineer`（P0 A–E）签字。P0 整体判定权在 `team-lead`。
- **本轮授权边界维持 VERIFIED**：HEAD 仍 `ebc3fcc`、无新提交、无 upstream/未 push、无 main/master/Release、`RED_LINES.allowBuySell=false`/`enableRealTrading=false`、旧 freeze 未改。

### §10.5.1 第三轮更新后 DECISION（team-lead 中转 F-12 / C1 关闭后，2026-11-11）

**仍为 GO WITH CONDITIONS（仅限 Security 范围）——不升级为 GO、不回退 NO-GO、不替任何 gate 签字。**

- **DECISION 原文**：`Security DECISION = GO WITH CONDITIONS（仅限 Security 范围）`。
- **为何不升级为 GO**：C1 关闭仅表示「他方书面确认经 team-lead 中转到位、晋级前置条件已满足」，**不是**把本审计静态结论升级；R6 在本审计证据等级里**仍为 UNVERIFIED**（静态只读无法证明全仓库任意进程运行期零外呼）；P9 仍 **DESIGN ONLY**；C2–C6 的 medium / 残留项及 C8 的不越权边界仍未闭环；本结论不替 `independent-qa`（P0-F）签字、不代表 P0 整体 PASS。
- **为何不回退 NO-GO**：C1 要求的两条独立书面证据均已到位且**均未发现任何真实外部调用**（`JEV_GAP_CLOSURE.md` L153-156；`JEV_INDEPENDENT_QA.md` L15/L93/L297-299/L301-304）→ 原「任何真实调用 → blocker + 回退 NO-GO」触发条件**未成立**；C7 已满足（最终锚点 `002653fe…` 未漂移，本审计独立核实）；R1–R6 技术结论未因本轮中转改变。
- **已满足的条件（原文字保留不删）**：**C1**（他方书面确认中转到位 / 触发条件未成立）、**C7**（最终锚点锁定、指纹未漂移）。
- **剩余 CONDITIONS（C2–C8 中仍未闭环 / 保留者）**：
  | 条件 | 状态 | 对应未闭环项 |
  |------|------|--------------|
  | **C2** | **仍未闭环** | F-05 §9 独立凭据域未落地；P9 维持 DESIGN ONLY / 禁止实施；真实运行前须建独立凭据域 + 最小权限/提币禁用证据 |
  | **C3** | **仍未闭环** | F-15 `riskCheckImpl` 注入面：P3 解锁 `allowBuySell` 前须锁定或二次校验 `gate.code`/provenance |
  | **C4** | **仍未闭环（主理人重点跟踪项 / P3 解锁硬前置）** | F-14 风控参数口径 200 vs 1000、`riskBreach` 恒 false、日亏损/回撤/持仓/最大交易数 breach 未参与判定 |
  | **C5** | **仍未闭环** | F-07 能力面：冻结引擎 handler `dbFetch`→全局 fetch→`data-api.binance.vision`（FORBIDDEN）+ 历史真实外呼脚本，P3 前须收敛/证明不可达 |
  | **C6** | **仍未闭环（low）** | F-09 残留：anchor/summary/recovery/state 在 runId 内覆盖写；如需同 runId 全量锚点留存须改 append-only / 分文件 |
  | **C8** | **边界保留（QA 回传已到位）** | 不替 `independent-qa` 签字、不评价 QA 测试质量、P0 整体门禁由 team-lead 依 §4 整合、不宣布 P0 整体 PASS |
- **一并保留的其它未闭环 / 红线项**：F-05（high，P9 DESIGN ONLY 语境下为 blocker）、F-14、F-15、F-07 能力面、F-09 残留、**CI 覆盖缺口**（`JEV_GAP_CLOSURE.md` §1.1，DevOps ownership）、**真实资金 NO-GO**（P0 全程未接触真实资金）。**未删任何 blocker。**
- **边界不变**：仅表示 P0 阶段 Security 代码边界（fail-closed / 注入 / 默认 fixture / 证据链）复核通过；≠ P0 整体 PASS、≠ 晋级 P1、≠ 解除 P9、≠ 生产 GO、≠ 替 QA / 实现方签字。P0 整体判定权在 `team-lead`。

## §10.6 证据等级与纪律声明

- **VERIFIED**：本轮实读 `day14-runner.mjs`(807 行) / `day14-verify.mjs`(253 行) / `paper-server.mjs` 关键段 / `market-guard.mjs:44-49` / 运行产物，及本地只读 `git`/`grep`/`stat`/`sha256sum`/`wc` 命令。R1–R5、F 项状态表、授权边界均属此类。
- **UNVERIFIED**：R6（本轮实际外部调用=0，静态只读不可证，按他方书面确认处理、待 independent-qa 回传）；历史 MiniMax M3 真实调用凭据存储位置；MASTER PLAN §9 原文（不在仓库内）；Binance.US 费率/条款本轮独立核实。**【第三轮补注】** C1 已满足（两条独立书面证据经 team-lead 中转、均未发现真实调用），但 **R6 在本审计证据等级里仍保持 UNVERIFIED、未改写成 VERIFIED**：QA 运行时动态计数仅覆盖 QA 进程树，本审计静态只读方法论边界继续保留；两条（连同本审计静态核实共三条）证据**彼此独立、不互相替代、不平均成一条**；本审计不替 QA / 实现方签字。
- **COMMUNITY-VERIFIED**：本轮**未触网、未做外部检索**，不产出新结论。`run.json` 中 `engine_conservative.provenance` 的 "COMMUNITY-VERIFIED" 是实现方自标注（保守规划估计），**本审计不背书**、不作为 GO 依据。
- **纪律**：未把任何 UNVERIFIED 改写为 VERIFIED；未删任何 blocker（保留全 F 编号并更新状态）；未替 QA/实现签字；未改任何源码/测试/旧 freeze；未 push/未碰 main·Release/未触网/未调付费模型/未提交订单；**未打开或输出任何凭据内容、值或 last4**；唯一写入为本文件。

## §10.7 本轮只读核实命令清单

| # | 命令 | 结果要点 |
|---|------|----------|
| B1 | `git branch --show-current` / `git rev-parse --short HEAD` / `git status --porcelain -uall` / `git diff --stat HEAD` | 分支/HEAD=`ebc3fcc`、runner 为 `M`、新增 verify + QA 测试 + 产物 |
| B2 | `stat -c '%s %y'` / `wc -l` / `sha256sum` runner | 38203 字节 / 807 行 / `415cdd36…`（两次读数捕获并行修改） |
| B3 | `Read day14-runner.mjs`（全文 + 末尾 L755-807） | R1–R6 逐行证据、L774-789 新增 export 块 |
| B4 | `Read day14-verify.mjs`（全文） | 14 项本地 fixture、无全局 fetch、T3/T4a/T7/T8 |
| B5 | `Grep 'fallbackRiskGate\|console\.warn\|approved\s*[:=]\s*true\|globalThis\|\bfetch\s*\(\|process\.env\|expiresAt\|mark_quality' runner` | 无 warn、无简化门定义、无 approved 字面量、无全局 fetch/env 读取 |
| B6 | `git grep -n -E 'provider-adapter\|ai-core\|globalThis\|process\.env' -- runner verify` | 无匹配 → 绕过 server 两个默认-fetch 模块 |
| B7 | `git grep -n -E '\bfetch\s*\(\|https?://' -- <新链路 6 模块>` | 仅 `decision-schema.mjs` `$schema` 字符串、`paper-server.mjs` L6 `API_BASE` 常量 |
| B8 | `Grep 'fetch\|API_BASE\|__test\|simulateNewOrder' paper-server.mjs` + `Read L493-511 / L1102-1106` | handler 路径含 `dbFetch`→全局 fetch；`__test` 仅纯内存函数，`simulateNewOrder` 无 fetch |
| B9 | `git grep -n -A 8 'export function resolveNetworkContext' market-guard.mjs` | `fetcherInjected` → `TEST_SHIM`（"local_test_shim"），纯函数 |
| B10 | `git grep -n 'console.warn' runner` / `git grep -n 'writeFileSync' runner` | 无 warn；6 处写均在 `runs/<runId>/`，旧 freeze 不重写 |
| B11 | `Grep 'maxNotionalPerTrade\|maxTrades\|maxExposurePct\|dailyLossBreachPct\|drawdownBreachPct\|RISK_LIMITS\|DEFAULT_RISK_LIMITS' runner` | F-14：冻结风控参数未参与判定、limits 取自正式门默认值 |
| B12 | `Grep 'riskBreach\|riskDenials\|providerFails' runner` | `riskBreach` 恒 false，`RISK_BREACH` 暂停恒不触发 |
| B13 | `Read` 产物：`run.json` / 主 run `risk.jsonl` / `_verify/t4a` `risk.jsonl`+`decisions.jsonl` / `_verify/t7` `risk.jsonl` | redLines、双口径、`RISK_HOLD_NO_ORDER`/`orderable:false`、t4a `RISK_APPROVED` 但 `SHADOW_GATED`、t7 `NO_ACTION` |
| B14 | V3 重核：`sha256sum`/`wc -l` runner + `git grep -n -E '<R1-R6 marker>'` + `Read L620-711` | 捕获 37310→38203→39606 字节三版本；V3 sha `002653fe…`；新增 L640-659 provider 失败 fail-closed 子分支（加强），R1–R6 关键行全部保持 |
| B15 | 第三轮：`sha256sum` 报告（改前）+ `sha256sum` runner/verify/freeze + `wc -l` runner + `git rev-parse --short HEAD` + `git diff --stat HEAD` + `tail -c 2 runner \| od -An -tx1` | 改前报告 `af52d73f…`/509 行；三锚点 `002653fe…`/`7b6fd539…`/`d4edce64…` 逐字一致、829 行、末尾 `7d 0a`（末字节 `0a`）、HEAD `ebc3fcc` 未 commit → **C7 已满足、指纹未漂移** |
| B16 | `sed -n '15p;93p;297,304p' reports/JEV_INDEPENDENT_QA.md` + `grep -n -A 6 '4.3' reports/JEV_GAP_CLOSURE.md` | 逐字核实两条书面证据出处：`JEV_GAP_CLOSURE.md` L153-156（§4.3）；`JEV_INDEPENDENT_QA.md` L15/L93/L297-299/L301-304（NONET 守卫 / 33 子进程 / stderr 命中 0 / 仅覆盖 QA 进程树）→ **C1 已满足、回退触发条件未成立** |
| **B17** | 第四轮：`sha256sum` / `wc -l` / `wc -c` 主报告 + 两份被引用 gate 报告；`Read` 两份被引用报告全文 | 改前主报告 `2717c458…` / 546 行 / 76170 B；`JEV_GAP_CLOSURE.md` 本轮实测 `5aa0c66a…` / 263 行 / 35277 B（team-lead 中转快照 `04494fab…` / 193 行）、`JEV_INDEPENDENT_QA.md` 本轮实测 `cc0ab7d8…` / 397 行 / 37880 B（中转快照 `aa390c08…` / 397 行 / 37908 B）；§4.3 / §6A / §6 / §9 章节号经实读稳定存在、内容逐字对应 → 引用锚点修正、指纹漂移如实并列（详见 §10.9） |

## §10.9 引用稳定性声明与证据锚点（第四轮 · 最后一次修正，2026-11-11）

> 触发：team-lead 纠偏「引用锚点漂移（主理人侧并行编辑所致，责任在 team-lead 不在本审计）」。本轮为**最后一次报告更新**，性质仍为**只读安全复核**，唯一写入本文件；不改 DECISION、不改 R6、不删任何 F 项 / blocker、不改源码 / 测试 / 旧 freeze、不触网、不 commit / push、不直接联系其他成员、不宣布 P0 整体 PASS 或生产 GO。

### §10.9.1 引用稳定性声明（权威）

> **精确行号会随报告版本漂移；本审计证据的权威锚点 = 文件 sha256 + 章节号，行号仅为该版本快照。**

- **强化说明（本审计本轮实读后补充，如实记录）**：并行编辑下，不仅**精确行号**会漂移，**文件 sha256 / 行数 / 字节数**同样会随报告被追加而漂移（本轮即实测到，见 §10.9.3）。因此三者均为「某一版本快照」；真正稳定、可跨版本定位的是**章节号（如 §4.3 / §6A / §6 / §9）及其所承载的证据语义**。本审计的证据结论以「章节号 + 该章节内的逐字证据内容」为准，sha256 与行号仅用于锁定「当时所依据的具体版本」、供复核比对，不作为结论本身。
- 本报告 §10.1 第三轮补注、§10.2 F-12/R6 行、§10.4 C1、§10.5.1、§10.7 B16 中出现的裸行号引用（`JEV_GAP_CLOSURE.md` L153-156；`JEV_INDEPENDENT_QA.md` L15 / L93 / L297-299 / L301-304 等）**一律按 v2 快照（359 行 / `0d42b43d…`）理解**，原文字保留不删；其权威锚点以下表为准。

### §10.9.2 证据锚点表（引用统一口径）

| 证据 | 权威锚点（章节号 + team-lead 中转快照指纹） | 行号（快照，非锚点） |
|------|--------------------------------------------|----------------------|
| trading-engineer F-12 书面声明（真实外部 HTTP / 付费模型·LLM / 订单提交 / 生产 DB 写入均 0，合计 0） | `reports/JEV_GAP_CLOSURE.md` **§4.3 F-12 外部调用确认**，快照指纹 `04494fab588c8440402ab982ed58756e24155ff6f17bbba31920c4b26d70a107`（193 行 / 24930 B） | team-lead 纠偏后的现行位置 L160（标题）/ L162（书面声明）/ L163（QA 独立计数要求）；第三轮报告所引 **L153-156 = v2 快照（359 行 / `0d42b43d…`）** |
| independent-qa F-12 书面确认段（可逐字引用、独立于 §6） | `reports/JEV_INDEPENDENT_QA.md` **§6A F-12 书面确认段（可逐字引用 / 独立于 §6，供 team-lead 中转）**，快照指纹 `aa390c08f2cc84b9f2c8c76332247a6ddcfd4789dc5e1e833f0a71cbdb27f693`（397 行 / 37908 B） | 现行 §6A L310-330；team-lead 给出的现行 F-12 行号 L16 / L95 / L299–301 / L303–305 |
| independent-qa QA 侧动态计数（NONET 守卫 / 33 个沙箱本地 node 子进程 / `P0-QA-NONET-ATTEMPT` stderr 命中 0 / **边界：仅覆盖 QA 进程树**） | `reports/JEV_INDEPENDENT_QA.md` **§6 F-12 — QA 侧外部调用计数**，同上快照指纹 `aa390c08…` | 现行 §6 L290-306；第三轮报告所引 **L15 / L93 / L297-299 / L301-304 = v2 快照（359 行 / `0d42b43d…`）** |
| QA 现行 DECISION 与演变留痕 | `reports/JEV_INDEPENDENT_QA.md` **§9 QA 范围裁决**（DECISION 演变表现行 L369-378），同上快照指纹 | 见 §10.9.4 C8 补注 |

> 本审计本轮 `Read` 实读确认：上述章节 **§4.3、§6A、§6、§9 均稳定存在且内容与所引证据逐字对应**（`JEV_GAP_CLOSURE.md` §4.3 在现行 L160-163；`JEV_INDEPENDENT_QA.md` §6A 在 L310-330、§6 在 L290-306、§9 在 L356-384）。章节号不随行号漂移，故作为权威锚点成立。

### §10.9.3 指纹漂移如实记录（独立实读，不改任何技术结论）

- team-lead 纠偏消息给出的**中转 / 冻结快照指纹**：`JEV_GAP_CLOSURE.md` = `04494fab…`（193 行 / 24930 B）、`JEV_INDEPENDENT_QA.md` = `aa390c08…`（397 行 / 37908 B）。
- 本审计本轮（最后一次修正前）独立 `sha256sum` / `wc -l` / `wc -c` **实测**：`JEV_GAP_CLOSURE.md` = `5aa0c66a24f0c00e98189647f73a7b846050a90fdd0eb1944517bf6aad2d4628`（263 行 / 35277 B）、`JEV_INDEPENDENT_QA.md` = `cc0ab7d8394e0d686211886e8321e5c7361e2f40d27f52468e0d2d6bbc6c70fa`（397 行 / 37880 B）。
- **差异归因（VERIFIED 实读）**：`JEV_GAP_CLOSURE.md` 在中转后又追加了 §4.4（主理人独立复跑）、§4.5（P0 整体门禁整合裁定）、§4.6（流程偏差 DEV-05 / DEV-06 / DEV-07）、§4.7（P1–P7 实现边界裁定）等章节，行数 193 → 263；其 **§4.6 DEV-06（low，引用漂移）** 已明文登记本次纠偏，并写明「安全报告改用『文件 sha256 + 章节号』为权威锚点，裸行号仅标注为版本快照」，与本轮修正完全一致。`JEV_INDEPENDENT_QA.md` 行数仍 397、字节微差（37908 → 37880），属并行文字微调。
- **该差异本身即印证 §10.9.1 声明**：不仅行号、连 sha256 / 行数 / 字节数都是快照，会随并行编辑漂移。**本审计如实并列记录「team-lead 中转快照指纹」与「本轮实测指纹」，不抹平、不择一冒充、不改写任何引用数字**。
- **对结论无影响**：两条书面证据的**实质内容**（F-12 外部调用 = 0、两条证据彼此独立不平均、QA 计数仅覆盖 QA 进程树）经本审计本轮逐字实读**未变**；C1 / C7 仍已满足；R6 **仍保持 UNVERIFIED**；Security DECISION **仍为 GO WITH CONDITIONS（仅限 Security 范围）**；F 项 / blocker 一个未删。本轮改动仅为**本报告的引用标注与锚点声明**，未改被测源码 / 测试 / 旧 freeze / 任何他方报告，**不触发 C7 重新复核**（C7 锚定的是 runner `002653fe…` / verify `7b6fd539…` / 旧 freeze `d4edce64…` 三个源码指纹，本轮未动）。

### §10.9.4 C8 补注详版（QA 现行 DECISION，team-lead 明确要求）

- §10.4 C8 原文含「`independent-qa`（P0-F）结论尚未回传」，已在 §10.4 就地补注（**原文字保留不删**），此处为详版：
  - **QA 现行 DECISION = `GO WITH CONDITIONS（仅限 QA 范围；C1 已关闭，剩余 C2 / C3）`**（`JEV_INDEPENDENT_QA.md` §9，现行 L358）。
  - **DECISION 演变（QA §9 全程留痕，现行 L369-378）**：`GO WITH CONDITIONS` → `GO（无条件）` → `GO WITH CONDITIONS（C1 已关闭，剩余 C2 / C3）`。
  - **归因（主理人裁定，如实转引 `JEV_GAP_CLOSURE.md` §4.6 DEV-05）**：演变系 team-lead 就同一 C1 事项先后发出两条口径不一致的消息所致，属**主理人口径不一致，非 QA 伪造**；QA 未删任何断言、未把 FAIL 变绿，最终采用更保守的 `GO WITH CONDITIONS` 作为权威结论。
  - **边界不变**：本审计**不替 independent-qa 签字**、不读取 / 不评价 QA 测试质量、**P0 整体门禁由 team-lead 依 `JEV_GAP_CLOSURE.md` §4 整合**、不宣布 P0 整体 PASS / 生产 GO。C8 仍作为边界声明而非待办条件保留。

### §10.9.5 本轮纪律声明

- 未改 DECISION（仍 `GO WITH CONDITIONS（仅限 Security 范围）`）、未改 R6（仍 `UNVERIFIED`、未改写为 VERIFIED）、未删任何 F 项 / blocker、未把 UNVERIFIED 升级为 VERIFIED、未把多角色结论平均掉。
- 未改任何 `.mjs` / `config.freeze.json` / `tests/` / 其他报告（含两份被引用的 gate 报告，本轮仅 `Read` 只读实读）；未 push / 未碰 main·Release / 未触网 / 未调付费模型 / 未提交订单 / 未 commit / 未输出任何凭据内容、值或 last4；未直接联系其他成员（跨成员信息一律 team-lead 中转）；未宣布 P0 整体 PASS 或生产 GO。
- **唯一写入**为本文件 `reports/JEV_REAL_TRADING_SECURITY.md`。此为**最后一次报告更新**；之后由 team-lead 冻结三份 gate 报告并执行本地统一提交，本审计不参与、不代执行。

---

*第二轮安全审计结论：**GO WITH CONDITIONS（仅限 Security 范围）**，条件 C1–C8 见 §10.4；R6 保持 UNVERIFIED，F-05 维持 DESIGN ONLY；P0 整体门禁由 team-lead 整合。0 项伪造 PASS，0 项输出凭据内容，0 项越权签字，0 项删 blocker。*
*第三轮更新（2026-11-11，team-lead 中转 F-12 后）：**DECISION 仍为 GO WITH CONDITIONS（仅限 Security 范围）**，原文见 §10.5.1；**C1 与 C7 已满足**（原条件文字保留不删），剩余 CONDITIONS 为 C2/C3/C4/C5/C6 与 C8 边界；R6 **仍保持 UNVERIFIED、未改写成 VERIFIED**，两条独立书面证据不互相替代或平均；F-05 / F-14 / F-15 / F-07 能力面 / F-09 残留 / CI 覆盖缺口 / 真实资金 NO-GO 一律保留，未删任何 blocker；P0 整体门禁由 team-lead 整合。0 项伪造 PASS，0 项输出凭据内容，0 项越权签字，0 项改源码。*
*第四轮（最后一次）更新（2026-11-11，引用锚点修正）：新增 §10.9「引用稳定性声明与证据锚点」——权威锚点 = 文件 sha256 + 章节号、裸行号降级为 v2 快照（359 行 / `0d42b43d…`）；引用统一为 `JEV_GAP_CLOSURE.md` §4.3（快照 `04494fab…`）+ `JEV_INDEPENDENT_QA.md` §6A 书面确认段 + §6 动态计数（快照 `aa390c08…`，现行 L16/L95/L299-301/L303-305）；如实并列记录中转快照指纹与本轮实测指纹的漂移（GAP `5aa0c66a…`/263 行、QA `cc0ab7d8…`）；§10.4 C8 补注 QA 现行 DECISION `GO WITH CONDITIONS（C1 已关闭，剩余 C2/C3）` 及 `GO WITH CONDITIONS → GO（无条件）→ GO WITH CONDITIONS` 演变（主理人口径不一致，非 QA 伪造）。**DECISION 仍为 GO WITH CONDITIONS（仅限 Security 范围）**，R6 **仍保持 UNVERIFIED**，C1/C7 仍已满足，剩余 CONDITIONS 为 C2/C3/C4/C5/C6 与 C8 边界；F-05 / F-14 / F-15 / F-07 能力面 / F-09 残留 / CI 覆盖缺口 / 真实资金 NO-GO 一律保留，未删任何 blocker；P0 整体门禁由 team-lead 整合。0 项伪造 PASS，0 项输出凭据内容，0 项越权签字，0 项改源码 / 测试 / 旧 freeze，0 项 commit / push，0 项直接联系其他成员。*

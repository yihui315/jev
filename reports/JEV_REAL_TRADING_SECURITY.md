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

---

# §11 第五轮（V2.0 收口轮 / T05）：八项真实运行准备逐条裁定 + 敏感信息独立复核

> 触发：team-lead 指派 T05「正式风险与真实运行准备」。性质仍为**只读安全复核**，唯一写入为本文件；不改任何源码 / 测试 / 旧 freeze，不 `git add` / `commit` / `checkout` / `stash` / `push` / `restore`，不触网、不调付费模型、不提交订单、不输出任何凭据内容。
> **§10.9.5 与 §10.9.1 中「最后一次报告更新」的表述已失效（原文保留不删）**：本轮系主理人再次指派，故本报告再开一轮；此事实如实登记，不修改历史文字。
> **独立性声明**：以下八条结论全部来自本轮**独立实读源码 + 独立行为注入**，不采信实现方 / QA / team-lead 的任何自评；team-lead 提供的「环境事实」仅作为检索起点，逐条结论均以本审计实测为准。

## §11.0 复核输入与版本事实（VERIFIED，本轮实测）

| 项 | 本轮实测值 | 与既有锚点比对 |
|----|-----------|----------------|
| 分支 | `feature/v16-p0-gap-closure` | 一致 |
| HEAD | `fa7740f2ab8be973b8407ed68d5e3514e034a2e3` | 较第四轮记录的 `ebc3fcc` **前进了 3 个本地提交**（`5834c37` / `c0cad25` / `fa7740f`），均未 push |
| `day14-runner.mjs` | sha256 `002653fed0d48e3cd20c95a397400d93830192ca87f60a8361a88b3aa3726d4a` / 829 行 | **逐字一致**（未漂移） |
| `server/ai/risk-gate.mjs` | `5797ba8cd6308e11c75c53121f151402e872ec1f1551780eb6953d60c992278f` / 181 行 | **逐字一致** |
| `server/ai/decision-schema.mjs` | `328b7a9b140069d354e9b06a08d85dca79ed47f3394acb8fd43c031be3184ba1` / 210 行 | **逐字一致** |
| `server/engine/_lib/paper-server.mjs` | `f9ac6e142446ef2aa8deb928f2b129eb2d42f4227d1bc22469191087312e4d74` | **逐字一致**；`vendor/nr_trusted/functions/_lib/paper-server.mjs` 同值（两份同体） |
| `reports/day14-artifacts/config.freeze.json` | `d4edce64c48bf15ab6803ce9ea08a164404d84642c61065d74550d7983208658` | **逐字一致**（仍未被重写） |
| 运行时 | `node v22.22.2`；复核开始时 `node_modules` 缺失 | 当时 `tsc` / `vitest` 不可执行 → 类型检查面 **UNVERIFIED**。**【§11.8.1 已更正：`apps/command_center_3d/node_modules` 现存在，`tsc --noEmit` exit 0、`vitest run` 22 文件 217 通过 → 该项转为 VERIFIED】原文保留不删** |

- **对 C7 的影响（如实登记）**：C7 原文含「HEAD `ebc3fcc` 未 commit」。该**子句已过时**（源码已被本地提交），但 C7 真正锚定的是三个**源码指纹**，本轮独立复核**四项全等** → **C7 结论仍成立**；仅「未 commit」字样失效，不改写原文、不擅自宣布 C7 重新失效。
- **行为证据获取方式**：本轮不依赖 `node_modules`。所有行为验证均以 `node` 直跑仓库内 `.mjs`（`node:assert` / 自建 suite / 自建 loader），或**在仓库外的独立目录**（`C:/temp/jewsec/`、`d:/数据分析/_sec_probe/`）注入 runner，产物只落 `C:/temp` 与 OS 临时目录，**仓库工作区零污染**（每步后复核 `git status`，行数未增）。
- **并行成员写入声明**：复核期间观察到 `reports/evidence/v16_ai/*.json`、`reports/JEV_V2_CI_VERIFICATION.md`、`reports/evidence/v16/_normalized/`、`apps/command_center_3d/scripts/normalize-evidence.mjs` 出现变更/新增，**系并行成员产生，非本审计写入**；本审计未读取也不评价其内容。

## §11.1 八条逐条裁定

> 口径：**VERIFIED** = 本审计以源码级静态确认 **且** 行为级注入复核，两者同时成立；**NOT_VERIFIED** = 未取得任一维度的确证。凡「条目成立但某子命题超出可证范围」，写成 `VERIFIED（范围=…；超范围子命题 UNVERIFIED）`，**不把超范围部分冒充已验证**。

| # | 用户点名项 | 裁定 |
|---|-----------|------|
| 1 | 正式 Paper 路径不允许简化风控放行 | **VERIFIED** |
| 2 | Real Market / Synthetic Fixture 完全隔离 | **VERIFIED**（系统生成与落盘路径；抗**蓄意伪造**子命题 UNVERIFIED，见 F-16/边界） |
| 3 | Real AI / Mock Inference 完全隔离 | **VERIFIED**（证据层与 paper 链路；仓库「具备真实调用能力」如实披露） |
| 4 | HOLD 与 NO_ACTION 完全区分 | **VERIFIED**（现行 runner 路径；P2 证据层 latent 缺陷见 F-18） |
| 5 | 订单幂等 / 账户隔离 / 权益对账可重放 | **VERIFIED**（本地 D1 shim 范围；云 D1 子命题 UNVERIFIED） |
| 6 | 模型 / 交易 / 运营成本分别核算 | **VERIFIED** |
| 7 | 真实交易开关始终 false | **VERIFIED** |
| 8 | 不允许未经批准连接正式生产资金 | **VERIFIED** |

**八条中无一条在「条目级」判 NOT_VERIFIED。** 但有 4 处**子命题**明确标为 UNVERIFIED（类型检查、抗蓄意伪造、云 D1、真实账单口径），均在 §11.3 / §11.5 逐条列出，**不进 GO 依据**。

### 11.1.1 第 1 条：正式 Paper 路径不允许简化风控放行 — **VERIFIED**

**源码级静态确认（三条独立证据）**

1. **导入失败即终止**：`day14-runner.mjs` §B 头注释声明 fail-closed；L143-148 `try { ({riskCheck: formalRiskGate, ...}) = await import("../server/ai/risk-gate.mjs") } catch (e) { throw new Error("[day14] FATAL: formal risk-gate import failed — fail-closed termination: " + e.message) }`；L149 追加 `typeof formalRiskGate !== "function"` → throw。
2. **简化门已物删除**：全仓 `grep -rn "fallbackRiskGate"` 仅 3 处命中——`day14-runner.mjs` **L19 注释**（「彻底删除 fallbackRiskGate 的放行能力」）、`tests/day14-p0-contracts.mjs` 与 `tests/day14-p0-runner.mjs` 的**断言比对串**。**无函数定义、无调用点**。`day14-runner.mjs` 内 `console.warn` 计数 = **0**（旧实现 `warn-and-continue` 形态不存在）。
3. **运行期二次前置校验**：L561-562 `if (typeof riskCheckImpl !== "function") throw new Error("... FATAL fail-closed: formal riskCheck unavailable — termination (no records, no approvals)")`；L563-564 对 `fetchMarket` / `evaluateCycle` / `engineImpl` 同样 throw。`riskCheck` 全仓仅 `risk-gate.mjs` 一处 `export function`（L91），无第二套门。

**行为级注入验证（本轮实做，4 个互不相同的注入面）**

| 注入 | 手段 | 实测结果 |
|------|------|----------|
| ① 正式风控**模块导入失败** | 自建 ESM loader（`C:/temp/jewsec/failrisk-loader.mjs`）把 `risk-gate.mjs` 解析成求值即抛的 `data:` 模块，`node --experimental-loader` 跑真实 CLI | 进程输出 `[day14] FATAL: formal risk-gate import failed — fail-closed termination: INJECTED_RISK_GATE_IMPORT_FAILURE`，**exit=1**，`--out` 目标目录 **根本未创建**（零 journal / 零 state / 零 approved） |
| ② `riskCheckImpl = null` | 库调用 `runDay14({riskCheckImpl:null})` | throw `formal riskCheck unavailable — termination (no records, no approvals)`；QA 桥独立在沙箱复现同一结果（`run dirs / journal records = [0,0]`） |
| ③ `riskCheckImpl` 内部抛错 | `() => { throw new Error("PROBE_GATE_BOOM") }` | 异常**原样外泄终止**（未被捕吞、未降级） |
| ④ **注入「永远放行」的恶意风控** | `() => ({approved:true, code:"RISK_APPROVED"})` + BUY 意图 | `orderable:true` 但 `execution:"SHADOW_GATED"`；`equity.jsonl` 全 `noFills:true`；`simulateFills:true` 时仅 `SIMULATED_FILL_LOCAL:1`（内存），且**全局 `fetch` 调用计数 = 0** |

- **反向反例（证明门非空转）**：QA 桥 `day14-p0-runner.mjs` 的「BUY on 非白名单标的」→ `RISK_SYMBOL_NOT_ALLOWED`、`approved:false`、`RISK_APPROVED` 记录数 **0**；`day14-p0-contracts.mjs` 的 `T2c` 用「旧简化 2 检查门 APPROVES / 正式门 REJECTS」的对照，**反证替换不成立**。
- **本轮实跑汇总**：`day14-p0-runner.mjs` **24/24 pass**；`day14-p0-contracts.mjs` **7/7 pass**；自建注入探针 **12/12 pass**（B0–B8）。
- **边界（不改写为通过）**：本条证明的是「**不存在 fallback 到简化风控的代码路径，且任何注入面下都不放行真实成交**」。它**不等于**「风控参数完备」——参数口径问题仍在 F-14 / C4；也不等于「`riskCheckImpl` 注入面已消除」——F-15 / C3 仍是 P3 硬前置。

### 11.1.2 第 2 条：Real Market 与 Synthetic Fixture 隔离 — **VERIFIED（系统生成路径）**

**P1 行情层**（`apps/equity_collector_v45/src/market-data/`，8 个模块，本轮实读 `provenance-guard.mjs` 全文 390 行）

| 门控 | 实现 | 是否可绕过（默认路径） |
|------|------|------------------------|
| 7 项 provenance 必填 + 类型 | `PROVENANCE_FIELDS` + 逐字段 `typeof` | 缺一即 `PROVENANCE_FIELD_MISSING/INVALID` |
| venue / symbol 白名单 | `DEFAULT_VENUE_ALLOWLIST`（2 值）/ `DEFAULT_SYMBOL_ALLOWLIST`（BTCUSDT/ETHUSDT） | 不在表即拒 |
| `isReal` ↔ `dataMode` 一致性矩阵 | fixture 模式必须 `isReal:false`；live/real/venue_verified 必须 `isReal:true` | 双向冲突均拒（`ISREAL_DATAMODE_CONFLICT`） |
| **核心红线** | `fixtureIndicator && liveIndicator` → `FIXTURE_MISLABELED_LIVE`。`fixtureIndicator` 含 `dataMode=fixture` / `captureMode` 含 fixture\|local / `source` 含 fixture\|local；`liveIndicator` 含 `isReal:true` / dataMode 含 live\|real / source 含 live\|real | **三条指示器任一交叉即拒** |
| 诚实空值 | `NOT_MEASURED` 声明却填数字（含 `0`）→ `MEASURED_VALUE_FABRICATED`；`MEASURED` 却 null → `MEASURED_VALUE_MISSING` | 双向拒 |
| 哈希信封 | `hash_scope` 仅 `raw_upstream_bytes`；禁用别名 `response_payload_sha256` / `input_snapshot_sha256`；`marketSnapshotHash` 必须 64 位小写 hex；`store_kind` 必须 `file` 且 `db_persisted:false` | 任一不符即拒 |
| **强制点** | `snapshot-store` / `bar-store` / `orderbook-journal` 的 `append()` 均在**写盘前**调 `guard()`，`!approved` 直接 `reject()`（抛错），**不存在「先写后校验」** | 已实读三处 append 序列 |

- `buildFixtureProvenance()` 构造的 fixture provenance **恒 `isReal:false` / `dataMode:"fixture"`**（L126-144），是 P1 唯一的 fixture 生成入口。

**P3 研究层**（`apps/command_center_3d/scripts/day14-research/`）

- `arms/_kit.mjs` `evidenceFields()` 对**四个臂**（hold-cash / buy-and-hold / trend / ai-decisions）统一硬编码 `data_mode:"FIXTURE"`、`evidence_level:"SYNTHETIC_FIXTURE"`、`isReal:false`、`venue:"fixture"`；`armResult()` / `buyAtMark()` / `sellAtMark()` / `equityRecord()` 全部经它出口 → **臂无法自选标记**。
- `market-timeline.mjs` 头注释与实现均为 `isReal:false` / `venue:"fixture"`；`metrics.mjs` L131、`fee-wrap.mjs` L84 同样 `SYNTHETIC_FIXTURE`。
- `p3-contracts.mjs` 本轮实跑 **12/12 PASS**，其中 ⑩「所有新建模块源码无禁用字面量（真实标记 / LIVE / 硬编码净值 / 写死对账）」、⑪「全模块零网络原语」为**静态扫描型断言**，直接覆盖「合成冒充真实」的字面量面。

**runner 侧**：`acquireCycleMarket()` 返回恒 `isReal:false` / `dataMode:"fixture"`（L405-406），`kind/marketKind` 为 `"fixture-verified"`（**诚实命名**，不写 `real` / `live`）。

**残留与如实边界（不隐去）**

- **F-16（新，medium，P3 硬前置 → C9）**：runner 对 fixture 行情写入 `markQuality:"verified"`（`buildRiskContext` L370 硬编码），而 `risk-gate.mjs` 的放行条件只要求 `mark_quality === "verified"`（L115）。当前被 `allowBuySell=false` 阻断，**无实际后果**；但一旦 P3 解锁，同一段代码会用 **fixture 价格**构造「已核实」市场上下文并通过正式门。实测佐证：`_verify/.../snapshots.jsonl` 记录 `price:70000, isReal:false, dataMode:"fixture", networkContext:"local_test_shim", markQuality:"verified"`。**「verified」在本仓当前语义 = provenance 自洽，而非真实 egress**，P3 前必须把 `verified` 绑定到真实出网证据。
- **F-17（新，low，与 F-15 同类）**：P1 三个 store 的 `guard` 是 `opts.guard` **可注入项**（默认 `guardRecord`）。默认路径安全；绕过需显式注入一个永远放行的 guard。
- **抗蓄意伪造子命题 = UNVERIFIED（能力边界，非缺陷）**：`guardRecord` 是**一致性**门控，不是**真实性**证明。一条手工构造、`isReal:true` / `dataMode:"real"` / venue 在白名单 / source 不含 fixture·local 字样的记录**可以通过门控**——因为不联网的前提下无法判别「这串字节是否真来自 Binance.US」。要关闭该命题必须引入外部锚点（交易所签名 / 第三方时间戳），本仓当前**没有**。故本条裁定限定在「系统生成与落盘路径」范围，超范围部分**明确 UNVERIFIED，不进 GO 依据**。

### 11.1.3 第 3 条：Real AI 与 Mock Inference 隔离 — **VERIFIED**

**证据层（`server/ai/evidence/`，本轮实读 `inference-evidence.mjs` 390 行）**

- `providerId: MOCK_PROVIDER_ID`（字面量 `"mock"`）、`isReal: false`、`dataMode: "fixture"` 在 `buildInferenceEvidence()` 返回对象中**是硬编码字面量**（L328-330），**不读任何入参**；测试 ⑮ 以 `providerId:"MiniMax-M3"` 输入 → 输出仍为 `"mock"`（本轮 t1–t9 复跑佐证）。
- 该模块 import 段仅 `node:crypto` + `node:fs`；**不 import** `runtime.mjs` / `provider-adapter.mjs` / `key-store.mjs` / `ai-core.mjs`；`evidence/index.mjs` 登记 `BINDING = "DECLARED_NOT_WIRED"`、`BINDING_TARGETS.day14_runner = "DECLARED_NOT_WIRED"`。全仓 `buildInferenceEvidence` **无 runner / gateway 调用点**（唯一非自身命中是 `check-no-fake-live.mjs` 的文件名清单）。
- `actualInferenceCost`：非严格 `MEASURED` 一律返回冻结常量 `{amount:null, provenance:"UNVERIFIED", evidence_level:"UNVERIFIED", note:HONEST_EMPTY_NOTE}`，**明示不回填** `estCostUsd` / `state.costUsd` / 预算值 / `0`（L172-192）；`assertNoCostMerge()` 拒绝 `merged/aggregated/scope:MULTI_MODEL/sources>1/models>1/多口径键共存`。
- `tokenUsage`：任一分量为 `0`/`null` → 整组降级为 `prompt:null,completion:null,total:null + NOT_MEASURED`（**不用 total 反推、不填 0**）。
- `rawResponseBase64` 由原始字节 base64 派生，`verifyRawResponseHash()` 可第三方独立复算，`decodeRawResponse()` 可还原字节。

**真实 provider 能力面（如实披露，不淡化）**

- 仓库**确实存在**真实 LLM 调用路径：`server/ai/runtime.mjs` 的 `testProvider()` 与 `createDecision({dataMode:"real"})`（`performRequest` + 凭据解析）。隔离由**三重约束**保证：
  1. `createDecision` 的 `mode` 只接受 `"mock"|"real"`，其它 → `DATA_MODE_INVALID`；`mock` 模式**禁止**使用非 mock providerId（L278 `DATA_MODE_MISMATCH`）；`real` 模式**必须有可解析凭据**，否则 `PROVIDER_BLOCKED`（L258-260）。
  2. 落库 `data_mode` 由**服务端**按实际执行分支赋值（`runtime.mjs:315`、`decision-store.mjs:59` `m.dataMode === "real" ? "real" : "mock"`），不由请求体原样透传。
  3. 网关 `functions/api/jev/[[path]].js:719` 在 `ENABLE_REAL_TRADING !== "false"` 时直接 500 `RED_LINE`，且 L729 强制写回 `"false"`；`server/ai/gateway-routes.mjs` 头注释明示所有 AI 路由在红线/凭据/Basic-auth 校验**之后**执行。
- **因此不得表述为「仓库无任何真实 AI 调用能力」**——正确表述是「**paper 链路与证据层完全走 mock，真实调用能力存在但需显式凭据 + 显式 dataMode + 红线为 false，且不产出可交易订单**」。

**残留（不隐去）**

- **F-20（新，low，latent）**：`normalizeActualInferenceCost()` 若入参自称 `evidence_level:"MEASURED"` + 任意有限 `amount`，会被**原样记为 MEASURED**，不做任何来源核验（无账单 / 无 provider 回执交叉比对）。因该层 `DECLARED_NOT_WIRED`，当前无实际后果；接线前须绑定可核验账单来源。
- **同源边界**：`rawResponseBase64` 能自证**完整性**（哈希可复算），不能自证**来源**（字节可由调用方任意合成）。属同一类「一致性 vs 真实性」能力边界，与第 2 条同构。

### 11.1.4 第 4 条：HOLD 与 NO_ACTION 完全区分 — **VERIFIED（现行路径）**

**结构层（`decision-schema.mjs`）**

- `ACTIONS = ["HOLD","BUY","SELL"]`；`DECISION_FIELDS` 白名单 **不含 `decisionClass`** → **决策对象结构上无法表达 NO_ACTION**（这是需求想要的「结构上无法表达」方向：决策契约里只有 HOLD，不存在「模型式 NO_ACTION」这种可自证的行动）。
- HOLD 形状强制 `side/orderType/quantity/limitPrice` **全为 null**（L110-114）；未知字段/缺失字段一律拒（`DECISION_UNKNOWN_FIELD` / `DECISION_MISSING_FIELD`）。
- `confidence` 仅审计用，`risk-gate.mjs` 头注释与实现均不读它做判定（L100-106 直接按 action 分支；`confidence_ignored` 只回写）。

**门控层（`risk-gate.mjs`）**

- HOLD → `{approved:true, code:"RISK_HOLD_NO_ORDER"}`；BUY/SELL 通过 → `{approved:true, code:"RISK_APPROVED"}`。**两者 code 不同**。
- runner 的 `orderable = gate.approved === true && gate.code === RISK_APPROVED && decision.action !== "HOLD"`（L668）——**三重收窄**，`approved:true` 单独不足以可交易。

**记录层（runner 实际产物）**

| 场景 | decisionClass | action | orderable | reason / failClosedReason |
|------|---------------|--------|-----------|--------------------------|
| 模型主动 HOLD | `HOLD` | `HOLD` | false | 模型给的 reason |
| 模型 BUY/SELL（门通过） | `ORDER_INTENT` | `BUY`/`SELL` | **true** | — |
| 行情不可验证 | `NO_ACTION` | `HOLD` | false | `MARKET_UNVERIFIED_FAILCLOSED` / `failClosedReason:"MARKET_UNVERIFIED"` |
| **provider 超时/抛错/解析失败** | `NO_ACTION` | `HOLD` | false | **`PROVIDER_FAILED_FAILCLOSED`** / `failClosedReason:"PROVIDER_FAILED"` |

- **行为实证**（本轮）：provider 抛错 → `decisionClass:"NO_ACTION"` / `reason:"PROVIDER_FAILED_FAILCLOSED"` / `action:"HOLD"`；provider 返回无有效 intent（`action:"MAYBE"`）→ 同样 `NO_ACTION`；`provider_health.jsonl` 的 `status` 分别为 `PROVIDER_ERROR` / `PARSE_FAILED`（**不是**「模型 HOLD」的 `FIXTURE_NO_LLM_CALL`）。QA 桥的 `provider_error` / `parse_failure` 场景独立复现同一结论。
- **结论**：provider 失败被记为 `NO_ACTION + PROVIDER_FAILED_FAILCLOSED`，**未被改写成模型主动 HOLD** —— 满足用户点名的口径。

**残留（不隐去，如实标为观测点）**

- **观测点（不影响本条判定）**：`NO_ACTION` 分支交给风控的 decision 对象，其 `action` 字段**仍是 `"HOLD"`**（为满足 schema 的 HOLD 形状）。因此「这是 NO_ACTION 而非模型 HOLD」**完全依赖 `decisionClass` / `failClosedReason` / `provider.status` 三个旁路字段**。任何下游消费者若**只按 `action` 聚合**，就会把一次 provider 失败读成「模型选择了 HOLD」。当前仓内无此消费者（`decision-store` 记录 `action` 同时记录 `status`/`error_code`），但**跨系统导出时是真实误读风险**。
- **F-18（新，low，latent）**：同名 `decisionClass` 在两层**词汇不一致**——runner ∈ `{HOLD, ORDER_INTENT, NO_ACTION}`；P2 证据层 `DECISION_CLASSES = ["APPROVED","NO_ACTION"]`（**结构上不含 HOLD**），且 `classify()` 对「provider 正常但 riskVerdict 非 APPROVED」一律返回 `NO_ACTION + RISK_NOT_APPROVED`（L221-233）→ **模型主动 HOLD 会被记成「风险未通过」**。因该层 `DECLARED_NOT_WIRED`，当前无实际后果；**接线前必须统一词汇**，否则跨层关联会制造「HOLD 冒充风险拒绝」的反向污染（与本条要防的错误方向相反，但同样污染审计证据）。

### 11.1.5 第 5 条：订单幂等 / 账户隔离 / 权益对账可重放 — **VERIFIED（本地 D1 shim 范围）**

本轮**独立实跑**（`node` 直跑，非 vitest；均 `verdict:"PASS"`、`exit 0`、零仓库写入）：

| 测试 | 断言要点（均已实读源码确认非空断言） | 实测 |
|------|--------------------------------------|------|
| `t2_idempotency.mjs`（V1.6） | 同 `cycle_key` 跑 3 次：首跑 `written=1`、后两次 `written=0`；快照/溯源**行数恒 1**；**全字段哈希三元相等**；`snapshot_id` 确定性稳定；**加强项**：同 key 换价（×0.5）第 4 次仍 `written=0` 且哈希不变、`equity`/`marks_json` 不被回填 | PASS |
| `t4_account_isolation.mjs` | 两独立 `(session_id, generation)` 账户各 3 周期共 6 行；**权益按定点独立重算逐行相等**；亏损账户 `equity_peak` **不被**盈利账户（刻意更大）放大；盈利账户 peak 独立递增；`snapshot_id` 全局 6 个唯一且会话绑定；每周期 `written=2/sessions=2`；两账户 `replay` 全 match | PASS |
| `t5_replay_readonly.mjs` | `paper_accounts` 前后**整表哈希全等**；写日志**仅** `paper_equity_snapshots` + `v45_snapshot_provenance`，`paper_accounts/paper_orders/paper_fills/paper_server_events` **写入 0**；orders/events 行数 0；`replay` 3 行全 match 且 unknown 行不污染 peak/dd；`engine_sha256` 与磁盘重算一致；`vendor/decimal.mjs`、`vendor/equity-snapshot.mjs` 与冻结引擎**字节相同**；scheduled 入口跑通；`fetch` 触发器**默认 403**（需显式 `testTrigger:"true"`） | PASS |
| `t4_idempotency.mjs`（AI 侧） | 同 `decision_id` 只产生 1 单；二次执行**不再调引擎**；`decision_order_links` 每 (decision, order) 仅 1 行；风控拒绝的决策不可执行；HOLD 不可执行（`DECISION_IS_HOLD`）；未知 id → 404 且不调引擎；401 保持可重试；重复建决策只 1 行 | PASS（8 项，含于本轮 t1–t9 83/83） |

- **断言有效性判定（用户明确要求「不是空断言」）**：三类断言均落到**可证伪的量化事实**——① **计数**（`written`、行数、`sessions`）、② **哈希**（整表哈希、全字段哈希、链 `_hash`、`engine_sha256`）、③ **唯一性 / 拒绝码**。且每套都**内置反向反例**（异价同 key、跨账户峰值放大、内容篡改使链校验失败、非白名单标的被风控拒），使「恒真断言」无法通过。
- **边界（UNVERIFIED 子命题）**：以上全部运行在**内存 D1 shim**（`tests/lib/d1-shim.mjs`）与本地文件上；`t5` 自行标注 `cloud_d1_deploy: "UNVERIFIED (owner action)"`。**云 D1 的并发幂等、跨会话隔离、D1 事务边界均未验证**，本审计同样无法证明 → 保持 UNVERIFIED，不进 GO 依据。
- **补充**：`node_modules` 缺失导致 `vitest` 形态未执行，但上述能力由 **Node 直跑等价复现**，非「跳过即通过」。**【§11.8.1 已更正】`apps/command_center_3d/node_modules` 现已存在，`vitest run` 实测 22 文件 / 217 通过 / 3 skipped / exit 0（工作区配置）；上述三项能力的既有结论不变，本节断言有效性与新增通过数无冲突。**

### 11.1.6 第 6 条：模型 / 交易 / 运营成本分别核算 — **VERIFIED**

| 成本域 | 载体 | 分列证据 |
|--------|------|----------|
| **交易成本（双口径）** | `day14-runner.mjs` `COST_MODELS` + `day14-research/cost-models.mjs` | `engine_conservative = {feeRate:0.001, slippagePct:0.015, provenance:"COMMUNITY-VERIFIED", status:"需实测 / NOT venue-measured"}`；`venue_verified = {feeRate:null, slippagePct:null, provenance:"UNVERIFIED", status:"NOT_MEASURED"}`。`slippagePct_is_planning_estimate:true` 明示 0.015 **不是**冻结引擎参数（引擎无显式滑点，隐含在 `walkBook` 逐档撮合） |
| **合并/回填禁令** | `cost-models.assertNoMerge()` | 三类一律抛 `COST_MODEL_MERGE_REJECTED`：(1) 一条记录含多个 `cost_model_id` 或同时拼接两口径数值块；(2) `venue_verified` 出现非 null 但无 `VERIFIED` 实测证据；(3) **把 0.001/0.015 回填进 `venue_verified`**（即使自称 VERIFIED 也拒）。`p3-contracts` ② 三类反例 + 三类合法样例 **全部 PASS** |
| **模型（推理）成本** | `evidence/inference-evidence.mjs` `actualInferenceCost` | 非严格 MEASURED → 恒 `{amount:null, UNVERIFIED}`，**明示绝不回填**预算/估算/0；`assertNoCostMerge` 拒合并 |
| **运营成本** | `supervisor/cost-monitor.mjs` | 三列 `budget / estimated / actual` **严格分列**；`actual` 未实测恒 `null`（`actualMeasured:false`）；`NOT_MEASURED_NOTE` 明写「budget/estimated are separate columns and are NEVER backfilled into actual」；`sumColumn` 对空列返回 `null`（**不返回 0**）；`assertNoMerge` 拒「actual 被预算值/0 回填」 |
| **runner 侧** | `run.json.costModels` | 双口径**并列**写入、并附 `note:"两个口径分列，严禁合并"`；行为复核 B7 实测 `venue_verified` 仍为 `null/NOT_MEASURED` |

- **是否发现合并 / 回填代码**：`grep -rn "assertNoMerge|assertNoCostMerge"` 定位全部 6 处调用（含 supervisor 与 P2/ P3 两侧），**未发现任何把两口径相加、取平均、或把 engine 数值写进 venue 的代码**；反向由上述断言主动拒绝。
- **边界（UNVERIFIED）**：真实账单口径的模型成本 = **未实测**（`UNVERIFIED`）；venue 实际费率/滑点 = **`NOT_MEASURED`**（`null` 保持）。这两项是「诚实空值」而非「已核实为 0」——本审计**不把 null 读成 0**。
- 说明：`engine_conservative.provenance = "COMMUNITY-VERIFIED"` 是**实现方自标注**，本审计**不背书**，不作为任何结论依据（延续 §10.6 口径）。

### 11.1.7 第 7 条：真实交易开关始终 false — **VERIFIED**

**逐处核实（`allowBuySell` / `enableRealTrading` / `ENABLE_REAL_TRADING`）**

| 位置 | 形态 | 能否变 true |
|------|------|-------------|
| `day14-runner.mjs:55` | `const RED_LINES = Object.freeze({enableRealTrading:false, allowBuySell:false})` | **否**：模块常量 + `Object.freeze`，**不在** `runDay14(opts)` 解构内；实测 `Object.isFrozen=true`，opts 显式传 `allowBuySell:true, enableRealTrading:true` → 执行仍 `SHADOW_GATED`（B8） |
| `day14-runner.mjs:74-75,123-126` | `LEGACY_FREEZE_EXPECTED` 同值；`verifyConfigFreeze()` 逐字段比对 + 红线二次断言，不等即 throw | **否**：篡改 `config.freeze.json` → 启动即抛 |
| `day14-runner.mjs` 全文 | **不读 `process.env`**（grep 计数 0） | **否**：无环境变量入口 |
| `run.json` 生成/恢复 | `redLines` 三处 false 写入；恢复时校验 + `configHash` 自校验，篡改即 `immutable run config ALTERED` | **否** |
| `cloud-entry.mjs:11` | `if (process.env.ENABLE_REAL_TRADING !== 'false') throw ENABLE_REAL_TRADING_MUST_BE_FALSE` | **否**：**只有 `"false"` 才放行**（默认拒绝） |
| `functions/api/jev/[[path]].js:719,729` | `!== "false"` → 500 `RED_LINE`；随后**强制写回** `"false"` | **否** |
| `equity_collector/src/collector.mjs:37-42` | **白名单**判定 false 语义：`"false" \| false \| 0 \| "0" \| undefined \| null` 之外一律 throw | **否**：allowlist 而非 blocklist，`"true"`/`"1"`/`"TRUE"` 全部拒绝 |
| `config.freeze.json` | `enableRealTrading:false, allowBuySell:false` | 同值（sha `d4edce64…` 未变） |

- **`config.freeze.json` 的 `realMarket:true` 说明（必须显式指出）**：该文件含历史误导标签 `realMarket:true` 与 `networkContext:"sandbox_proxy_egress_binance_us"`。**这两个字段都不是开关**：runner 只把它当作「配置意图」只读校验，运行标签由 provenance 决定；`networkContext` 由 `resolveNetworkContext({fetcherInjected:true})` 实际取 `"local_test_shim"`，**不读 freeze 里的值**。runner 在 `run.json.legacyNote` 中**明写**该字段为「误导标签，保留只读，不修改」。→ 不构成第二条开启路径，但**任何只读 freeze 就下结论的审计都会被这两个字段误导**，故在此点名。
- **行为复核**：B0（`RED_LINES` frozen=false 双确认）、B8（opts 覆盖无效）、④ 注入恶意放行门（仍 `SHADOW_GATED`）。

### 11.1.8 第 8 条：不允许未经批准连接正式生产资金 — **VERIFIED**

- **资金类原语 = 0**：`grep -rnE "withdraw|deposit|transferOut|/sapi/|api/v3/account|apiKey.*secret|POST /api/v3/order"` 于 `apps/` → **0 命中**；`createRealAccount|liveAccount|REAL_ACCOUNT|real_money:true` → **0 命中**。
- **成交通道唯一**：gateway `buildEngineRequest()` → 冻结 `paper-server.mjs` handler → D1 `jev-paper-staging`；同文件 meta 自述 `real_money: false, real_exchange_orders: false`（L568）；`data_mode` 分支只影响 AI 调用，不影响成交目标。
- **`executeApprovedDecision` 的实际动作**：`submitOrder(payload)` 是**注入的函数**，指向纸面引擎；且前置三重闸门——`decision.action === "HOLD"` → 409、`riskCheckResult !== "PASS" || status !== "APPROVED"` → 409、已有 `paperOrderId` → 幂等返回（不二次调引擎）。
- **出网能力面（如实披露）**：仅两类——① 公开行情**只读** REST（`data-api.binance.vision` / `api.binance.us`，无签名、无资金）；② 需凭据的真实 LLM（见 11.1.3）。**均不含资金/下单面**。`trading_api.mjs` 仅只读 GET 路由，无硬编码出网主机。
- **按要求**：**未检索** `ReferenceInput` 类字段（本仓不存在，team-lead 已提示不得再找）。

## §11.2 敏感信息独立复核（与主理人扫描**独立**进行，结论不互相替代）

### 11.2.1 `config/.env.example` — **无真实值**

所有键均为空模板（`JCC_CONTROL_TOKEN=` / `JCC_CONFIG=` / `JEV_API_KEY=` / `JCC_PROVIDER=mock`），注释明确「Copy to `.env` and fill in. NEVER commit the real `.env`」。

### 11.2.2 `config/.env` — **存在真实风格凭据，但不随推送泄漏**（高风险项，如实登记）

- **跟踪状态**：`git ls-files config/.env` → **空**（未跟踪）；`git check-ignore -v config/.env` → `.gitignore:120:.env` 生效 → **不会被 push**。
- **内容**：7 个非空值，键名（**仅键名，本审计未输出任何值、片段或 last4**）：`DASHSCOPE_API_KEY` / `DASHSCOPE_API_KEY_BACKUP` / `JEV_AI_QWEN_BASE_URL` / `JEV_AI_QWEN_MODEL` / `JEV_API_KEY` / `JCC_PROVIDER` / `MINIMAX_API_KEY`。
- **判定**：属「**本地真实凭据存在**」事实（与 F-05 同一语境），**不构成仓库泄漏**；但足证「不得把 `.env` 纳入任何快照 / 证据 / 日志 / 打包」这一纪律必须继续执行。

### 11.2.3 程序化外泄比对 — **命中 0**

- 方法：从 `config/.env` 取 5 个「长度 ≥12 且非枚举字面量」的值，在**内存中**对 1649 个文件（`git ls-files` 全量 + `reports/day14-artifacts/_verify/` + `.../runs/` + `.../active-run.json` + `apps/equity_collector_v45/evidence/`）做子串比对；**只输出键名与文件路径，不打印任何值**。
- 结果：`scanned_files=1649`、`skipped_big=0`、**`credential_leak_hits=0`**。

### 11.2.4 本轮新增文件 — **50 个，硬编码密钥命中 0**

- `git diff --name-status ebc3fcc..HEAD` 实测：**新增 50（A）/ 修改 25（M）**。（team-lead 计 45，差异属统计口径——以 git 实测 50 为准，如实登记不抹平。）
- 对这 50 个文件逐一扫 `AKIA[0-9A-Z]{16}` / `sk-[A-Za-z0-9_-]{16,}` / `ghp_…` / `-----BEGIN … PRIVATE KEY-----` / `Bearer …` → **`NO_HITS_IN_ADDED_FILES`**。

### 11.2.5 全仓密钥模式命中复核 — **全部为合成夹具 / 文档示例值**

逐条核对（本轮实读命中行）：

| 命中 | 性质 |
|------|------|
| `facades/claw3d/tests/*`、`facades/session_center/tests/*`：`sk-live-S06/S07SENTINELdeadbeef…` | **植入哨兵**，用于 redaction / 泄漏测试（`PLANTED_SECRET`） |
| `packages/jcc_core/tests/*`：`sk-live-DEADBEEF…` / `sk-live-PLANTEDSECRET…` / `sk-live-VALUEEMBEDDED…` / `sk-live-PLANTEDbravo…` / `sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX` / `sk-abcdefghijklmnopqrstuvwx` | 同上，断言「不得回显 / 不得落 spool」 |
| `packages/jcc_core/tests/test_redaction_values.py`：`AKIAIOSFODNN7EXAMPLE`、`xoxb-123456789012-abcdefghijklmn`、GitHub classic PAT 格式样本 `ghp_ABCD…6789`（**本文档已按脱敏规则截断**：原值为 `ghp_` + 26 位 `A–Z` + 10 位 `0–9` 共 36 位，其构成本身即自证为合成样本，非真实凭据；可核验位置 `packages/jcc_core/tests/test_redaction_values.py:79`） | `AKIAIOSFODNN7EXAMPLE` 是 **AWS 官方文档示例值**；其余为格式样本 |
| `scripts/verify-auth.mjs`：`sk-deliberately-invalid-key-for-e2e-0000`；`scripts/verify-providers.mjs`：`sk-e2e-probe-9876543210abcdef` | 字面写明 deliberately-invalid / e2e-probe |

→ **未发现任何真实凭据**；与主理人扫描结论一致（但为独立复核，不互相替代）。

### 11.2.6 三个 scratch 目录 — **无真实凭据，但有「误提交」风险**

| 对象 | 规模 | 内容性质 | 是否随推送泄漏 |
|------|------|----------|----------------|
| `reports/day14-artifacts/_verify/` | **391 文件 / 1.5 MB** | `fixture-verified` 快照（`isReal:false` / `dataMode:"fixture"` / `networkContext:"local_test_shim"` / `price:70000` / `markQuality:"verified"`）、决策与风险日志、纸面账户（`cash:10000.00000000` / `orders:0`） | 未跟踪 → 常规 push **不带**；但 **`git add -A` 会一次带入 401 个文件** |
| `reports/day14-artifacts/runs/` | **10 文件 / 84 KB** | 同上，单 run 目录 | 同上 |
| `reports/day14-artifacts/active-run.json` | 1 文件 | 仅 `{runId, updatedAt}` | 同上 |

- **敏感词命中复核（假阳性确认）**：`grep -E "password|secret|token|api_key|Authorization|Bearer|sk-|AKIA"` 在 20+ 个 `risk.jsonl` 命中，逐字核对后**全部来自 `"formal-risk-gate"` 字符串中的 `sk-` 子串**（如 `"gate":"formal-risk-gate"`）→ **假阳性，非凭据**。
- **`git check-ignore`**：三个路径 **`NOT_IGNORED`**（未跟踪但**未被 .gitignore 覆盖**）。
- **F-19（新，low）**：401 个临时产物未 ignore，存在**误提交污染证据库**的操作风险（不含机密，属流程卫生）。建议（**不代执行**）：加入 `.gitignore` 或提交时用显式白名单。
- **状态数据复核**：scratch 中 `state.json` 的 `engineAccount` 为纸面引擎账户对象（虚拟 10,000 USDT），无真实账户号 / UID / API key 字段。

## §11.3 本轮新增发现（编号续接，旧编号一个不删）

| ID | 级别 | 内容 | 触发条件 / 影响窗口 |
|----|------|------|---------------------|
| **F-16** | **medium（P3 硬前置）** | `markQuality:"verified"` 语义混同：runner 对 **fixture** 行情写 `verified`，而 `risk-gate.mjs` 仅凭 `mark_quality==="verified"` 即放行 → P3 解锁后可用合成价通过正式门 | 当前被 `allowBuySell=false` 阻断；P3 前必须把 `verified` 绑定真实 egress |
| **F-17** | low | P1 三个 store 的 `guard` 为 `opts.guard` 可注入（默认 `guardRecord`），与 F-15 同类注入面 | 需显式注入永远放行的 guard 才能绕过；默认安全 |
| **F-18** | low（latent） | `decisionClass` 跨层词汇不一致：runner `{HOLD,ORDER_INTENT,NO_ACTION}` vs P2 证据层 `{APPROVED,NO_ACTION}`；P2 `classify()` 把「模型 HOLD」记成 `NO_ACTION + RISK_NOT_APPROVED` | 该层 `DECLARED_NOT_WIRED`；接线前必须统一 |
| **F-19** | low | 401 个临时运行产物（`_verify/` 391 + `runs/` 10）未跟踪且未 ignore，存在 `git add -A` 误提交风险 | 无机密；流程卫生 |
| **F-20** | low（latent） | `normalizeActualInferenceCost()` 接受调用方自报 `evidence_level:"MEASURED"` + 任意 amount 并原样记为 MEASURED，无来源核验 | 该层未接线；接线前须绑定可核验账单 |
| **F-21** | low（观测点） | `NO_ACTION` 的 decision 对象 `action` 仍为 `"HOLD"`，区分**完全依赖** `decisionClass`/`failClosedReason`/`provider.status`；只按 `action` 聚合的下游会误读 | 仓内暂无此类消费者；跨系统导出时是误读风险 |
| **F-22** | **medium（证据门完整性）** | 「空转假门」双风险：① `t1_schema`…`t9_gateway_routes` **无主模块守卫**，单独 `node <file>` = exit 0 / **0 断言**（本轮 9/9 实测）；② `server/ai/tests/run-all.mjs` 的 PASS 判定**无最小断言下限**，`allChecks.length===0` 时输出 `=> PASS` + exit 0。**当前未兑现**（CI 只用聚合入口、83 条断言真实存在） | 一旦有人把 9 个文件加成独立 CI step，或重构断掉 harness 收集 → 门恒绿且恒 0 断言。详见 §11.7.2 |

> **本表为第五轮首轮（T05 主体）新增项。后续轮次新增见：F-23 → §11.8.4；C14 → §11.8.6。**

> **F-16 是本轮唯一被列为「P3 硬前置」的新发现**；F-17～F-21 均为 low / latent，**不阻断当前 P0 Security**，但不得隐去。

## §11.4 更新后条件清单（C1–C8 状态保留，新增 C9–C11）

| 条件 | 状态 | 说明 |
|------|------|------|
| **C1**（R6 外部调用=0） | **已满足**（沿用 §10.4，不变） | 本轮未推翻：QA 桥本轮实跑 33 个子进程、NONET 守卫命中 0，与本审计静态结论一致；R6 仍按 UNVERIFIED 等级处理 |
| **C2**（F-05 独立凭据域 / P9 DESIGN ONLY） | **仍未闭环** | 本轮新增事实：`config/.env` 存在 7 个非空真实风格凭据（未跟踪）→ 强化「真实运行前必须建独立凭据域」的必要性 |
| **C3**（F-15 `riskCheckImpl` 注入面） | **仍未闭环** | 本轮**新增同类项 F-17**（P1 store 的 `guard` 注入面），一并纳入本条件的收敛范围 |
| **C4**（F-14 风控参数口径） | **仍未闭环（主理人重点跟踪）** | 本轮未改变：`maxNotionalPerTrade:200` 未参与判定、`riskBreach` 恒 false |
| **C5**（F-07 能力面） | **仍未闭环** | 本轮补充证据：真实 LLM 能力面（`runtime.mjs`）与公开行情只读 egress 均**有意保留**，须在 P3 前证明「不产出可交易动作」 |
| **C6**（F-09 覆盖写残留） | **仍未闭环（low）** | 未变 |
| **C7**（版本锁定） | **结论仍成立，但一处子句失效** | 四项源码指纹**逐字全等**；「HEAD `ebc3fcc` 未 commit」已过时（现有 3 个本地提交）→ 如实登记，不改写原文 |
| **C8**（不越权） | **边界保留** | 本审计不替 QA / 实现方 / team-lead 签字，不评价 QA 测试质量（仅把其脚本当作**独立实现的复现手段**引用） |
| **C9（新）** | **P3 硬前置** | 对应 F-16：把 `verified` / `mark_quality` 语义**绑定真实出网证据**（如 `networkContext` 白名单排除 `local_test_shim`），否则禁止解锁 `allowBuySell` |
| **C10（新，low）** | 未闭环 | 对应 F-18 / F-21：统一跨层 `decisionClass` 词汇；在记录层禁止「仅按 `action` 判定 HOLD/NO_ACTION」 |
| **C11（新，low）** | 未闭环 | 对应 F-19：临时产物 ignore 化或提交白名单化，避免误提交 |
| **C12（新，medium）** | 未闭环（owner = devops / independent-qa） | 对应 F-22：聚合 runner 加**最小断言数下限**（按 suite 声明期望条数，不足即 FAIL）；禁止把无主模块守卫的模块当独立 gate；独立执行须自带 `isMainModule()` 守卫 + 非 0 退出。样板见 `equity_collector_v45/tests/run-all.mjs`（`PASS_NO_RESULT_JSON` 判 FAIL + 静态 8 项清单） |
| **C13（新，决策项）** | 待主理人决策 | 对应 §11.7.1：`config/.env` 本地 7 个真实风格凭据**未发现任何外泄证据**，「是否轮换」属风险偏好决策，**不由本审计单方面裁定**；同时纠正「加 hook 即可拦住」——`.gitignore` 被 `git add -f` 绕过、pre-commit 被 `--no-verify` 绕过，可靠拦截点在服务端 / CI secret scanning 与「凭据不落盘」 |

> **本表为第五轮首轮（T05 主体）条件表。后续轮次新增：C14（PR 触发缺口，medium，owner devops）→ 详见 §11.8.2 / §11.8.6；C12 已于 §11.8.4 扩围纳入 F-23。**

## §11.5 本轮 DECISION（仅限 Security 范围）

**GO WITH CONDITIONS（仅限 Security 范围）——不升级为 GO，不回退 NO-GO。**

- **八条点名项 8/8 达 VERIFIED**，均在「当前 P0 Security 边界」内成立：无简化风控回退路径、合成与真实分列、mock 与真实推理分列、HOLD 与 NO_ACTION 分列、三项能力有真实断言、成本三域分列、红线恒 false、无生产资金通道。
- **为何仍不升级为 GO**：
  1. ~~4 处~~ **3 处**子命题 UNVERIFIED（抗蓄意伪造无外部锚点；云 D1 未验证；真实账单 / venue 费率 `NOT_MEASURED`）——`UNKNOWN ≠ 0`，**不用 0 或"已通过"冒充**。（原「4 处」含「类型检查因 `node_modules` 缺失不可执行」，该子命题已于 **§11.8.1** 转为 VERIFIED：`tsc --noEmit` exit 0。）
  2. C2–C6、C9–C13 均未闭环；其中 **C9（F-16）是 P3 解锁硬前置**：不解决则 P3 一旦解锁 `allowBuySell`，正式门会用 **fixture 价格**放行真实下单。这是本轮最重要的新增结论。**C12（F-22）是"证据门可信"前置**：不解决则任何新增独立 gate 都可能恒绿且 0 断言 —— 它不阻断八条技术裁定，但会污染未来所有"门已通过"的证明力。
  3. R6 仍按 **UNVERIFIED** 处理（方法论边界未变）。
- **为何不回退 NO-GO**：八条点名项均未发现**当前**可执行的真实资金路径或风控降级路径；所有残留项均被 `allowBuySell=false` / `ENABLE_REAL_TRADING=false` / 无资金原语三重阻断，回退触发条件（发现任一真实调用或真实资金路径）**未成立**。
- **适用范围（必须与结论一起引用）**：仅表示 **P0 阶段（paper / SHADOW / 不接触真实凭据与资金）的 Security 代码边界复核通过**。**不代表**：P0 整体 PASS、可晋级 P1、可解除 P9、可解锁 `allowBuySell`、可接真实资金、可对外宣称「安全已全部通过」。P0 整体门禁由 team-lead 整合。
- **阻断关系（若有 NOT_VERIFIED 会阻断哪个裁定）**：本轮**无条目级 NOT_VERIFIED**，故不阻断任何单条裁定；但 **C9/F-16 阻断「P3 解锁裁定」**，4 处子命题 UNVERIFIED 阻断「GO（无条件）裁定」与「生产就绪裁定」。**C12/F-22 阻断「以测试门全绿作为放行依据」这一论证路径**（门本身可能空转，见 §11.7.2）。**不得把本轮结论用于批准真实资金。**

## §11.6 本轮证据等级与纪律声明

- **VERIFIED（本轮独立取得）**：§11.0 全部版本锚点（`sha256sum` / `wc -l` / `git rev-parse` / `git diff --name-status`）；§11.1 八条的源码+行为双维证据；§11.2 全部扫描结论（含 1649 文件 / 0 命中、50 新增文件 / 0 命中）。
- **行为证据清单（全部本轮实跑，均可复现）**：loader 注入导入失败（exit 1 / 零产物）；自建探针 B0–B8（12/12）；`day14-p0-runner.mjs`（24/24）；`day14-p0-contracts.mjs`（7/7）；`p3-contracts.mjs`（12/12）；`t1–t9`（83/83，**经自建聚合 harness 收集；9 个模块单独执行 = 0 断言，见 §11.7.2(e)**）；`check-no-fake-live.mjs`（10/10）；collector `t2/t4/t5`（3/3 PASS）。
- **UNVERIFIED（未改写为 VERIFIED）**：~~TypeScript 类型检查（`node_modules` 缺失）；`vitest` 形态（同上…）~~ **【§11.8.1 已更正：这两项现已 VERIFIED，见下】**；云 D1 行为；抗蓄意伪造的真实性证明；真实账单模型成本；venue 实测费率/滑点；**R6 本轮实际外部调用 = 0**（仍按他方书面确认处理，不升级为本审计 VERIFIED）。
- **【§11.8.1 更正后，新增 VERIFIED 项】**：`tsc --noEmit` = **exit 0，零类型错误**（`apps/command_center_3d`，工作区源码）；`vitest run` = **22 文件全通过 / 217 tests passed / 3 skipped / exit 0**（**工作区** `vite.config.ts` 配置）。**边界**：二者证明的是**当前工作区**状态；`vite.config.ts` 与 `ci.yml` 在复核时均为**未提交改动**，**HEAD 提交态**下 `vitest run` 的行为不同（HEAD 无 `test.exclude`，两个手写 `.test.mjs` 会被默认 glob 收集）→ **提交态的红/绿属 T04，本审计不置评**。
- **NOT_MEASURED**：`venue_verified.feeRate` / `slippagePct` = `null`（**保持 null，不读作 0**）。
- **COMMUNITY-VERIFIED**：`engine_conservative.provenance` 为实现方自标注，本审计**不背书**。
- **纪律执行**：未 `git add` / `commit` / `checkout` / `stash` / `push` / `restore`；未改任何源码 / 测试 / 旧 freeze；**唯一写入为本文件**；未触网、未调付费模型、未提交订单、未创建任何真实账户；**未输出任何凭据值、片段或 last4**（仅键名 + 存在性判定）；未替 `independent-qa` / 实现方 / team-lead 签字；未宣布「安全已全部通过」或「可上真实资金」；未把 UNVERIFIED 写成 VERIFIED、未把 null 读作 0。

## §11.7 交叉提示复核（independent-qa 提示 → 本审计独立核实，结论为我自己的）

> 触发：`independent-qa` 转来两条与本审计范围相关的观察。**本审计逐条独立复跑，不复述其结论、不以其结论代替我的判断**；核实后我给出的判定如下，其中一条**要求我更正自己 §11 中一处证据方法学的表述**（见 §11.7.2 末）。

### §11.7.1 `config/.env` 复核 — **与 §11.2.2 结论一致，独立复现**

- 长度独立实测（**不输出任何值**）：`DASHSCOPE_API_KEY`=115、`DASHSCOPE_API_KEY_BACKUP`=125、`JEV_AI_QWEN_BASE_URL`=49、`JEV_AI_QWEN_MODEL`=9、`JEV_API_KEY`=107、`JCC_PROVIDER`=4、`MINIMAX_API_KEY`=125 —— 与 QA 报的 115 / 125 / 107 / 125 **逐字吻合**。
- 入库状态独立复核：`git ls-files config/.env` 为空；`git check-ignore -v` → `.gitignore:120:.env`。→ **本次三个提交（`5834c37` / `c0cad25` / `fa7740f`）不含该文件，不构成提交级泄露**（与 §11.2.2、§11.2.3 一致：1649 文件比对命中 0）。
- **对「加 hook 即可拦住」的纠正（我这边的独立判断）**：`.gitignore` **不是安全控制**——`git add -f` 直接绕过；pre-commit hook 也不是——`git commit --no-verify` 或 `-n` 绕过。**唯一可靠的拦截点**是服务端 / CI 侧（secret scanning + pre-receive）以及产物侧（凭据不落盘、不打包、不进证据）。故我把它写成**纪律 + 服务端强制**，而非「加个 hook 就安全」。
- **判定**：维持 §11.2.2 结论——「本地存在真实凭据（不构成仓库泄露）」；对应条件 **C2**（独立凭据域）+ 本条件新增的 **C13（凭据轮换决策，owner = 主理人，非本审计可决定）**。**本审计不主张凭据曾被泄露**（未发现任何外泄证据），是否轮换属风险偏好决策，不由安全审计单方面裁定。

### §11.7.2 「空转假门」复核 — **属实，我独立复现；并据此更正我自己一处引用方法**

**（a）`t1_schema` … `t9_gateway_routes` 无独立入口 — 实测确认**

逐个 `node <file>` 执行 9 个文件，**均 `exit=0`、stdout 行数 = 0**：

```
t1_schema / t2_provider_errors / t3_risk_gate / t4_idempotency / t5_data_mode
t6_persistence_attribution / t7_redaction / t8_role_mapping / t9_gateway_routes
→ 全部 exit=0，stdout_lines=0（仅 stderr 一条 SQLite ExperimentalWarning）
```

源码原因：这些文件**只 `export const id / title` 与 `export async function run(suite)`**，**无 `import.meta.url === ...` 主模块守卫**，`assert` / `assertEqual` 只在 `run()` 内部调用 → 直接执行时**一条断言都不跑**。

**（b）但当前 CI 并未这样用 — 实测确认，陷阱是潜在的、未兑现的**

`.github/workflows/` 全量检索 `t1_schema|t2_provider_errors|…|t9_gateway` **零命中**；三处调用点均为**聚合入口**：`v16-market-provenance.yml:70` `node tests/run-all.mjs`、`:83` `node tests/v16/run-all.mjs`、`:98` `node server/ai/tests/run-all.mjs`。→ **目前不存在「把无入口模块当独立门」的 CI 步骤**。

**（c）聚合入口本身的残余风险（我新增，QA 未点出）— 静态实读**

`server/ai/tests/run-all.mjs` 的 PASS 判定为：

```
const passed = allChecks.filter((c) => c.pass).length;
const failed = allChecks.length - passed;
overall: failed === 0 ? "PASS" : "FAIL"
```

**`allChecks.length === 0` 时 `failed === 0` → 输出 `TOTAL checks=0 passed=0 failed=0 => PASS`，exit 0。** 即聚合门**没有最小断言数下限**：若将来某模块被改成空 `run()`、或 exports 被改名导致 harness 收集不到 checks，聚合门会**静默变绿**。

**（d）反向对照（同一仓库里做对了的实现，可作为修复样板）**

`apps/equity_collector_v45/tests/run-all.mjs`：无 `RESULT_JSON` 输出时 verdict = `"PASS_NO_RESULT_JSON"`，而 `allPass = results.every(r => r.verdict === "PASS")` → **`PASS_NO_RESULT_JSON` 判为 FAIL**；且测试清单是**静态 8 项**（`TESTS` 数组），不会因目录为空而 `every` 空真。→ 该门**能抵抗本陷阱**，实测 `t2/t4/t5` 也确有 `RESULT_JSON:` 输出。

**（e）对我自己 §11 的方法学更正（必须写明，不隐去）**

§11.1.4 / §11.1.5 引用「`t1–t9`（83/83）」时，未标明**这 83 条断言来自我自建的聚合调用**（`C:/temp/jewsec/run-t1-t9.mjs`，显式 `import { createSuite }` 后调用 `mod.run(suite)`），**不是** `node <单个文件>` 的结果。**该 83/83 仍然有效**（确实是 83 条真实断言的执行结果，非空转），但若读者误以为「单独执行 t1–t9 也是 83 条」，就会高估。**在此更正表述：83/83 = 经聚合 harness 收集的断言数；9 个模块单独执行 = 0 断言。**

- **新增发现 F-22（medium，证据门完整性 / 非代码缺陷）**：以上 (a)+(c) 构成「证据门可静默空转」的结构性风险。**当前未兑现**（CI 只用聚合入口、且 83 条断言真实存在），但一旦有人把 9 个文件加成独立 CI step，或在重构中断掉 harness 收集，门会恒绿且恒 0 断言。
- **新增条件 C12（medium）**：① 聚合 runner 增加**最小断言数下限**（按 suite 声明期望条数，不足即 FAIL）；② 禁止把无主模块守卫的模块当作独立 gate；③ 若确需独立执行，模块必须自带 `isMainModule()` 守卫 + 非 0 退出语义。（样板见 (d)）
- **归属边界（不越权）**：CI 配置与测试门有效性由 `devops` / `independent-qa` 主责；**本审计只报「门可空转」这一安全/证据完整性事实，不评价其测试内容质量、不代其签字、不改 CI 文件**。

### §11.7.3 附带更正：§10.3 的「CI 覆盖缺口」已部分关闭（原文字保留不删）

§10.3 记录的「`v16-market-provenance.yml` push 触发硬编码旧分支名 → 本分支不跑 V1.6/V1.6 专有断言」**已过时**。本轮实读该文件 `on.push.branches` 现为：

```
["feature/v16-market-provenance-hardening", "feature/v16-p0-gap-closure", "feature/v16-**"]
```

已包含本分支与 `feature/v16-**` 通配 → **该缺口在本轮三个提交中已被修掉**；`ci.yml` 的 `feature/**` 亦覆盖本分支。**但「CI 是否真的跑过、是否全绿」属 T04 远端 CI 签收范围，本审计未验证、不予置评**（本地无远端执行记录，且未 push）。

> **【§11.8.2 重要更正】** 上面「已修掉」只对 **push** 成立。**PR 触发缺口仍然存在**：用 `yaml@2.9.1` 真解析两个 workflow，`v16-market-provenance.yml` 的 `on.pull_request.branches` **只有** `["release/jcc-v1.3-final"]`，`ci.yml` 的 `on.pull_request.branches` **只有** `["main","master"]` → **PR 开向基线 `feature/v16-market-provenance-hardening` 时两个工作流都不触发，PR 页面 0 个 check**。且 Y1 断言里 `assert.ok(l.some(x => /release\/jcc-v1\.3-final/.test(x)), "PR target rule changed")` **等于把该 PR 规则固化**。详见 §11.8.2 / 新增条件 C14。

### §11.7.4 现行 CI 七个 gate 的「非空转」逐项实测（只读；为 C12 提供事实基线）

为确认 F-22 是否**已经兑现**，我把 `ci.yml` 当前 P0/P1/P2/P3P4 gate 步骤里被 `node` 直接调用的**全部 7 个脚本**逐个实跑，检查「是否真有断言输出 + 失败是否非 0 退出」：

| CI gate 脚本 | 独立入口 | 本轮实测输出 | 判定 |
|--------------|----------|--------------|------|
| `server/ai/tests/day14-p0-contracts.mjs` | 有 | `SUMMARY ... 7 pass, 0 fail`，exit 0 | **非空转** |
| `server/ai/tests/day14-p0-runner.mjs` | 有 | `SUMMARY ... 24 pass, 0 fail`，exit 0 | **非空转** |
| `equity_collector_v45/tests/market-data.test.mjs` | 有（尾部 `FAIL>0 \|\| networkFetchCalls!==0 → exitCode=1`） | `PASS=10 FAIL=0` / `RESULT=PASS`，exit 0 | **非空转** |
| `server/ai/evidence/tests/inference-evidence.test.mjs` | 有（`failed>0 → exitCode=1`，末行另断言 `networkCallCount===0`） | 未本机实跑（**该脚本会向仓库内写临时文件**，故只做静态核读） | **非空转（静态确认）** |
| `day14-research/p3-contracts.mjs` | 有 | `PASS=12 FAIL=0`，exit 0 | **非空转** |
| `scripts/supervisor/supervisor.test.mjs` | 有 | `RESULT PASS=14 FAIL=0`，exit 0 | **非空转** |
| `scripts/check-no-fake-live.mjs` | 有 | `CORE_ASSERTIONS=7 EXTRA_ASSERTIONS=3 TOTAL=10 exit=0` | **非空转** |

- **结论：现行 CI 没有任何一步是「空转门」**——7 个 gate 全部有真实断言且失败即非 0；且**没有任何一步单独执行 `t1_schema`…`t9_gateway_routes`**（这三处调用点见 §11.7.2(b) 均为聚合 `run-all.mjs`）。**F-22 属「未来可能踩到」的结构性风险，不是当前已存在的假门。**
- **附带发现（归入 C12 子项，low）**：`inference-evidence.test.mjs` 的 `TMP` 指向 **仓库内** `apps/command_center_3d/server/ai/evidence/tests/_p2_chain_tmp.jsonl`（`new URL("./_p2_chain_tmp.jsonl", import.meta.url)`），仅在**开头**做 `existsSync→unlink` 预清理，**未见收尾清理**；该路径**未被 `.gitignore` 覆盖**（`git check-ignore` = NOT_IGNORED）。CI 工作区是短生命周期的故无提交风险，但本地跑测试会在仓库内留下未跟踪文件。建议随 C12 一并改为 `os.tmpdir()` 并加收尾 `unlink`。
  > **【§11.8.3 更正：我上一句「未见收尾清理」是错的】** 重读源码：两处写 `TMP` 的用例（⑨ L271-311、⑫ L354-367）**都以 `finally { cleanTmp(); }` 收尾**（L308、L365）→ **正常结束与断言抛错都会清理**；只有硬杀（SIGKILL / OOM）才留残留。且我已**实跑**该脚本：`PASS=17 FAIL=0`、exit 0、**运行后仓库无 `_p2_chain_tmp.jsonl` 残留**（`git status` 计数 0）。故本条定性从「会污染仓库」下调为「**硬杀时**可能污染仓库 + 路径落在仓库内属设计味道」，建议仍改 `os.tmpdir()`。
- **未验证边界**：以上均为**本机实跑**；CI（GitHub Actions）是否真的执行、是否全绿，属 **T04 远端 CI 签收**范围，本审计**未验证、不予置评**（本地无远端执行记录，且未 push）。

# §11.8 第三轮交叉核实（independent-qa 二次回复 → 四条更正 + 一条新发现）

> 触发：`independent-qa` 对我 §11.7 逐条回复。其中**两条直接推翻我自己上一版的表述**，我逐条实测后**就地更正**（原文一律保留不删）；另有**一条 QA 的表述被我用实验收窄**。全部结论以**我自己的复跑**为准，不采信任何一方的自述。
> 纪律不变：只读、只写本文件；未 `git add`/`commit`/`checkout`/`stash`/`push`/`restore`；未改任何源码 / 测试 / 配置 / CI。

## §11.8.1 更正 ①：`node_modules` 现已存在 → 类型检查从 UNVERIFIED 转 **VERIFIED**

我 §11.0 写的「`node_modules` 缺失 → `tsc`/`vitest` 不可执行」**在复核开始时成立，现已不成立**：

```
ls -d apps/command_center_3d/node_modules   → 存在（.bin/ 内 tsc、vitest、vite 齐备）
./node_modules/.bin/tsc --noEmit           → exit 0，零输出（零类型错误）
./node_modules/.bin/vitest run             → Test Files 22 passed (22)
                                             Tests 217 passed | 3 skipped (220)
                                             exit 0
```

- **判定**：`tsc --noEmit` = **VERIFIED**（`apps/command_center_3d` 工作区源码零类型错误）；`vitest run` = **VERIFIED（工作区配置）**。
- **必须同时声明的边界（否则会误导 T04）**：这两个结果证明的是**当前工作区**。`apps/command_center_3d/vite.config.ts` 与 `.github/workflows/ci.yml` 在复核时**均为未提交改动**（`git diff --stat` = 2 files / 21 insertions）。`git show HEAD:apps/command_center_3d/vite.config.ts | grep exclude` → **无 `test.exclude`**，而 `scripts/supervisor/supervisor.test.mjs` 与 `server/ai/evidence/tests/inference-evidence.test.mjs` 均以 `.test.mjs` 结尾、命中 vitest 默认 `**/*.test.mjs` → **HEAD 提交态下这两个文件会被收集为「无套件」并导致 vitest 红**。`independent-qa` 报出 HEAD 态实测 `2 failed | 22 passed (24)`，其方法为「把 `git show HEAD:` 的配置内容**另存为另一文件名**喂给 `vitest run --config`，不 checkout、不覆盖工作树」——该数据我按**他方一手实测**采信（方向与我的静态推断一致），**但不升格为本审计的 VERIFIED**（我本人未实跑该形态，且不能用新增配置文件的办法绕过「只写自有报告」的自律）；HEAD 态的最终红绿属 **T04**。
- **对结论的影响**：4 处子命题 UNVERIFIED → **3 处**。§11.0 表格、§11.1.5 补充、§11.5 第 1 条、§11.6 均已就地加注，**原文未删**。

## §11.8.2 更正 ②：CI 触发「已修掉」只对 push 成立，**PR 缺口仍在**（我 §11.7.3 说过宽）

我用 `yaml@2.9.1`（`apps/command_center_3d/node_modules` 内实际存在）**真解析**两个 workflow，而非 grep：

| 文件 | `on.push.branches` | `on.pull_request.branches` |
|------|--------------------|----------------------------|
| `v16-market-provenance.yml` | `["feature/v16-market-provenance-hardening","feature/v16-p0-gap-closure","feature/v16-**"]` | **`["release/jcc-v1.3-final"]`** |
| `ci.yml` | `["feat/jev-trading-workbench","feat/**","feature/**","main","master"]` | **`["main","master"]`** |

触发推演（按 GitHub 的 glob 语义复刻）：

```
push 到 feature/v16-p0-gap-closure        → v16-market-provenance.yml = TRUE, ci.yml = TRUE
PR 开向 feature/v16-market-provenance-hardening → v16-market-provenance.yml = FALSE, ci.yml = FALSE
```

- **结论**：我 §11.7.3 说「该缺口已被修掉」**只对 push 成立**；**PR 路径 0 个 check** 的事实我没看到，是我的漏判，就地更正。
- **关于 Y1 的那条断言（我原判断有误，已被 devops 指出并经我复刻确认，见 §11.8.7）**：`check-no-fake-live.mjs` 的 Y1 用 `assert.ok(l.some(x => /release\/jcc-v1\.3-final/.test(x)), "PR target rule changed")`，**只要求该字符串「存在于文件某一行」**，**不要求它是唯一元素**。因此：
  - **追加** `feature/v16-**` 到 `pull_request.branches`（保留 `release/jcc-v1.3-final`）→ **Y1 仍然 PASS**（复刻实测）；
  - **只有移除** `release/jcc-v1.3-final`（如整段换成 `main`）→ **Y1 才 FAIL**（复刻实测）。
  - → 我原来写「该检查会**阻止**有人放宽 PR 触发分支」**是错的**，已在本轮更正；**但**该断言仍属弱断言（见 §11.8.7 的额外发现：**把 PR 分支整段注释掉、字符串仍在，Y1 也照样 PASS**）。
- **对 T03/T04 的直接影响（必须让 team-lead 知道）**：若 T03 按常规把 PR 开向基线分支 `feature/v16-market-provenance-hardening`，**PR 页面不会有任何 check**，T04「远端 CI 真实签收」在该 PR 上**无从签起**。
- **新增条件 C14（medium，owner = devops）**：PR 目标分支与 workflow `pull_request.branches` 必须对齐。**两条修法成本不同（已按 §11.8.7 实测修正）**：① **追加 `feature/v16-**`（保留 release）→ 不需要改 Y1**，成本最低；② PR 目标改 `main` 并移除 release → **必须同步改 Y1**，否则撞红。**本审计不改 CI，只提出。**

## §11.8.3 更正 ③：`_p2_chain_tmp.jsonl`「未见收尾清理」——**我错了**

我 §11.7.4 写「仅在开头做 `existsSync→unlink` 预清理，未见收尾清理」。重读源码后确认**有收尾**：

- ⑨ 用例：L272 `cleanTmp()`（开头）→ L308 **`} finally { cleanTmp(); }`**（结尾）
- ⑫ 用例：L355 `cleanTmp()`（开头）→ L365 **`} finally { cleanTmp(); }`**（结尾）
- **实跑复核**：`node apps/command_center_3d/server/ai/evidence/tests/inference-evidence.test.mjs` → `PASS=17 FAIL=0`、exit 0、运行后 `_p2_chain_tmp.jsonl` **不存在**、`git status` 该路径命中 **0**。
- **定性下调**：从「会污染仓库」改为「**仅在硬杀（SIGKILL / OOM，`finally` 不执行）时**可能残留；路径落在仓库内仍是设计味道」。建议仍改 `os.tmpdir()`，但**不再作为独立风险项**，只是 C12 的一个附注。
- 顺带：QA 表述「`cleanTmp()` 放在每个用例开头（不是结尾）」**也不准确**——实际是**开头 + `finally` 结尾**。双方都需按源码校正，我已在上面给出具体行位。

## §11.8.4 新发现 **F-23（medium）：`check-no-fake-live.mjs` 的 Y2 是弱断言，可被「注释掉」骗过**

QA 指出「Y2 对新增 2 条 gate 无感」。我独立复刻其判定逻辑（`l.some(x => x.includes(s) && /test -f/.test(x))` 与 `/node /`）做了 4 组对照实验：

| 变体 | 场景 | Y2 判定 | 说明 |
|------|------|---------|------|
| 基线 | 5 条 gate 齐备（`test -f` + `node`） | **PASS** | 正常 |
| A | 把 `p3-contracts` 整条**注释掉**（实际不执行） | **PASS** ❌ | 注释行仍含脚本名与 `node ` / `test -f` |
| B2 | 删掉 Y2 **未登记**的 2 条新 gate（supervisor / check-no-fake-live） | **PASS** ❌ | 与 QA 所述一致 |
| C | 删掉 Y2 **已登记**的 gate（`market-data.test.mjs`） | **FAIL** ✅ | 说明 QA 的「双向都哑」**过宽**：删除已登记项是能抓到的 |
| D | **5 条门全部注释掉**（真实执行 0 条） | **PASS** ❌ | 最严重：守卫门列表的门本身可被整体废掉 |

- **根因**：`yamlLines()`（L187-197）**逐行返回、不剔除注释**，而 Y2 的匹配只做 `includes` + 正则；该脚本在 `continue-on-error` 那一条**恰恰专门排除了注释行**（L226-229），却在脚本断言上**没做同样处理**——不一致。
- **次因**：Y2 的脚本清单**硬编码 5 条**，而 `ci.yml` 现有 **7 条** `node` gate 调用；Y2 的标签文案也仍写「5 gate scripts」。
- **第三因（可修，但我的建议需附加条件）**：脚本自称「pyyaml / ruby-yaml / yq / js-yaml all unavailable」。**`yaml@2.9.1` 确实存在于 `apps/command_center_3d/node_modules`**（我就是用它做的解析），**但它不是直接依赖**——`package.json` 的 `dependencies` / `devDependencies` **均无 `yaml`**，`package-lock.json` 显示它由 **`postcss-load-config`** 传递引入。→ **因此「改用真解析」必须先显式声明依赖**，否则随上游依赖树变动会静默失效。**首选方案应是无依赖方案**（devops 提出的四条，见 §11.8.7.3）：剔除注释行 + 行首锚定 `/^\s*node\s+(\S+\.mjs)\s*$/` + `node` 条数下限 ≥7 + 路径集合相等比较。
- **当前是否已兑现**：**未兑现**。现行 `ci.yml` 的 5 条 gate 均**未被注释**、结构与断言期望一致 → **Y2 现在的 PASS 是「对的」，不是假绿**。F-23 是**守卫能力**缺陷，不是当前红灯被掩盖。
- **归属**：devops / independent-qa（CI 与测试门）。**本审计只报事实与复现方法，不评价其测试内容、不改 CI。**
- **并入 C12**：C12 增加「gate 守卫断言的重写」——**首选无依赖方案**（§11.8.7.3 四条：剔除注释行 / 行首锚定 `node <path>` / `node` 条数下限 ≥7 / 路径集合相等比较）；若坚持真解析，须先把 `yaml` 提升为**直接 devDependency**（当前仅为 `postcss-load-config` 的传递依赖）。

## §11.8.5 与 QA 的数字对账（一致，无冲突）

| 项 | 本审计 | QA | 是否一致 |
|----|--------|----|----------|
| `t1`–`t9` 单文件执行 | 9/9 exit 0、stdout 0 行 | 9/9 exit 0、stdout 0 行 | ✅ |
| `t1`–`t9` 断言数（聚合） | 83（13+18+13+8+9+6+5+4+7） | 83（同分布） | ✅ |
| `run-all.mjs` 总断言 | 未跑（会覆盖 QA 的 tracked 证据文件，故不跑） | 120 = 83 + e2e 37 | 互补，无冲突 |
| `inference-evidence.test.mjs` | **本轮实跑** PASS=17 / FAIL=0 / exit 0 | PASS=17 / FAIL=0 | ✅ |
| `config/.env` 真值长度 | 115/125/49/9/107/4/125 | 115/125/107/125 | ✅（四个值逐字吻合） |

## §11.8.6 结论与条件增量（DECISION 不变）

- **八条点名项裁定不变：8/8 VERIFIED，0 条目级 NOT_VERIFIED。**
- **DECISION 不变：`GO WITH CONDITIONS（仅限 Security 范围）`。**
- **子命题 UNVERIFIED 由 4 处降为 3 处**（类型检查已转 VERIFIED，见 §11.8.1）；剩余 3 处：抗蓄意伪造、云 D1、真实账单 / venue 实测。
- **C12 扩围**：增加 F-23 三项修复（真 YAML 解析 / gate 数量下限 / 剔除注释行）。
- **新增 C14（medium，owner devops）**：PR 触发分支对齐，否则 T03 的 PR 上 **0 check**、T04 无从签收。
- **新增 F-23（medium）**：Y2 弱断言（可被注释化绕过、对未登记 gate 无感、清单与文案均滞后 5→7）。
- **不变的红线**：本结论**不得**用于「安全已全部通过」「可上真实资金」「P0 整体 PASS」；`t1–t9` **不得**被加成独立 CI gate；**HEAD 提交态的 vitest 红绿属 T04，本审计不置评**。

## §11.8.7 更正 ④：我对 C14 的「Y1 会撞红」判断过强（devops 指出，我已复刻确认）；并补一条 Y1 的同类漏洞

> 触发：`devops` 独立复刻 Y1 后指出我「放宽 PR 分支会先撞红 Y1」的判断过强。我按同一方法复刻 Y1 的四段断言，**确认是我错**，同时发现**双方都还没提到的 Y1 注释盲区**。

### §11.8.7.1 复刻实测（我自己的实验，不是转述）

复刻 Y1 的判定形式（`l.some(x => /release\/jcc-v1\.3-final/.test(x))` 等四段），四组对照：

| 变体 | 场景 | Y1 |
|------|------|-----|
| 基线 | PR 仅 `["release/jcc-v1.3-final"]` | PASS |
| **追加** | `["release/jcc-v1.3-final","feature/v16-**"]`（保留 release） | **PASS** |
| **替换** | `["main"]`（移除 release） | **FAIL**（`PR target rule changed`） |
| **整段注释**（我新增） | `# branches: ["release/jcc-v1.3-final"]` | **PASS** ❌ |
| **挪段**（我新增） | PR 目标删成 `["main"]`，但把 `release/jcc-v1.3-final` **挪进 `push.branches`** | **PASS** ❌ |

- **判定（三层，逐层收窄）**：
  1. **`devops` / `independent-qa` 正确、我错**：`l.some(...)` **只要求字符串「存在于某一行」，不要求唯一** → **追加不红，只有移除才红**。我此前「该检查会**阻止**有人放宽 PR 触发分支」**不成立**，已在 §11.8.2 就地更正（原文保留）。
  2. **但要再精确一层（我新增的「挪段」变体）**：Y1 并**不**验证该字符串位于 `pull_request.branches` 段内。只要它出现在文件**任何位置**（`push.branches`、甚至注释）即可满足。→ 严格表述应为「**Y1 禁止把该字符串从整个文件删掉**」，而**不是**「禁止移除 PR 目标」。若有人把 PR 目标改成 `["main"]`、同时把 release 分支名挪进 `push.branches`，**PR 规则已被破坏而 Y1 依然 PASS**。
  3. **实测该字符串当前在文件中只出现 1 次**（`v16-market-provenance.yml` L10 `branches: ["release/jcc-v1.3-final"]`，`grep -c` = 1）→ 眼下的 `some()` 近似等价于「存在性等值」，但一旦出现第二处（任何段、任何注释），断言立刻退化为「文件里提过这个词」。
- **对 C14 措辞的收敛（采纳 `independent-qa` 的表述并加限定）**：『Y1 以 `some()` 存在性锁定 `release/jcc-v1.3-final`，**禁止「从文件移除该字符串」，但不禁止新增分支、也不阻止把 PR 目标替换掉（只要字符串仍在别处出现）**；若 T03 选择「替换成 `main`」而非「追加」，才需要同步改 Y1。』
- **仍未覆盖的面**：`ci.yml` 的 `pull_request.branches` **完全没有被任何 gate 断言覆盖** —— 改它不会有任何自检反应。

### §11.8.7.2 `yaml` 依赖性质核实（我原建议缺少这一层约束）

- `apps/command_center_3d/package.json`：`dependencies` / `devDependencies` **均无 `yaml`**。
- `package-lock.json`：`yaml` 由 **`postcss-load-config`** 传递引入（`node_modules/postcss-load-config` 的 dependencies 含 `yaml`）。
- **结论**：我原建议「改用 `yaml` 真解析」**必须先把它提升为直接 devDependency**，否则它会随 vite/tailwind 工具链变动而静默消失，守卫脚本届时会以「模块找不到」直接崩（比弱断言更糟：可能被当成偶发故障跳过）。**这一点上 `devops` 的工程判断比我稳。**

### §11.8.7.3 首选修复方案（采纳 devops 的无依赖四条，记录备查）

`devops` 给出的最小改法，我认为**优于我原提的 `yaml` 方案**，记录如下（**我不代改，方案归属 devops**）：

1. `yamlLines()` 增加「跳过整行注释」选项，或 Y2 内部先过滤 `!/^\s*#/.test(x)`；
2. 只认**行首非注释**的 `node <path>`：`/^\s*node\s+(\S+\.mjs)\s*$/`；
3. 断言 gate 步骤内 `node` 条数 **≥ 7**（下限而非等值，避免以后加脚本又要改）；
4. 从 `run` 块**提取**路径集合，与期望集合做**相等**比较（而非逐个 `some`）。

→ 这样 §11.8.4 的 A / B / D 三种变体都会红，且**无 node_modules 也能跑**。
→ **补充（我的意见）**：第 3 条「下限 ≥7」与第 4 条「集合相等」存在轻微张力——集合相等会让「新增 gate 未登记」也红（这是好事，能强制更新守卫），但会与「下限避免改脚本」的意图冲突。**建议以第 4 条为准、第 3 条作为兜底**，二者不必同时严格要求。

### §11.8.7.4 对本轮结论的影响

- **C14 的修法成本下降**：**追加 `feature/v16-**` 不需要动 Y1**（我原判断「要同步改 Y1」是错的），故 team-lead 若要最快解除 PR 0 check，**走追加路线即可**。
- **C12 的修复方案以无依赖四条为准**；`yaml` 真解析降为备选且须先声明依赖。
- **F-23 扩为 Y1 + Y2 两处**（Y1：只验存在、可被整段注释骗过；Y2：清单滞后 5→7、可被逐条注释骗过）。
- **八条裁定与 DECISION 不变**；`GO WITH CONDITIONS（仅限 Security 范围）`。

## §11.8.8 F-23 的第四种显形：**行尾注释会让「带 `$` 锚定」的提取器静默漏行**（本轮新增实测）

> 触发：与 `independent-qa` / `devops` 讨论「修复后应如何写断言」时，我实测出此前三方都未提及的一层。因属 F-23 的**同一缺陷的新技术事实**（不是措辞调整），故补记入报告；其余结论不变。

### §11.8.8.1 实测：`$` 锚定 vs 行尾注释

对候选正则逐例实跑（我的实验，非转述）：

| 输入 | `/^\s*node\s+(\S+\.mjs)\s*$/`（带 `$`） | `/^\s*node\s+(\S+\.mjs)/`（无 `$`） |
|------|------------------------------------------|--------------------------------------|
| `          node apps/x/p3-contracts.mjs` | match | match |
| `   node apps/x/p3-contracts.mjs   ` | match | match |
| `# node apps/x/p3-contracts.mjs` | no（`^\s*` 遇 `#` 即停，天然安全） | no |
| `   # node apps/x/p3-contracts.mjs` | no | no |
| `test -f apps/x/p3-contracts.mjs \|\| { exit 1; }` | no | no |
| **`node apps/x/p3-contracts.mjs  # legacy`** | **no** ❌ | **match** ✅ |

- **结论**：带 `$` 时，**一条完全合法的 gate 行若被加上行尾注释，会被静默判为「不存在」**。这是 F-23 的**第四种显形**（前三种：Y2 注释化、Y1 注释化/挪段、路径计数偏 1）。
- **修正**（已与 `independent-qa` / `devops` 达成一致）：**主修法是去掉 `$` 锚定**，使提取正确性**不依赖**「注释是否被剔干净」；「剔除行尾注释」仅作辅助，且朴素 `/\s+#.*$/` 对**引号内的 `#`** 可能误伤（如 `echo "see issue #123"` → 被切成半条命令，比漏检更危险）。
  **【§11.8.9 已更正本节一处事实错误】** 我此处原写「实测当前 `ci.yml` 内『引号含 `#`』的行 = 0 条」——**错**。该结论源于我的检索口径过窄：我只用了**双引号**模式 `"[^"]*#[^"]*"`，未覆盖单引号。`devops` 指出后我重测：**单引号内含 `#` 实有 2 条**（L65 `'### Unit tests (mock)\n\n'`、L140 `'### Live market-data probe\n\n'`）。**现状仍然安全，但原因不同**：朴素 `/\s+#.*$/` 要求 `#` 前有空白，这 2 处的 `#` 紧跟 `'` → 不匹配。详见 §11.8.9。

### §11.8.8.2 `hits==1` 必须限定作用域（实测计数）

7 条 gate 脚本在 `ci.yml` 中的出现次数（我独立统计）：

| 脚本 | 全文出现 | 其中 `node` 调用行 | 其中注释行 |
|------|----------|--------------------|------------|
| `day14-p0-contracts.mjs` | 2 | 1 | 0 |
| **`day14-p0-runner.mjs`** | **3** | 1 | **1**（L26） |
| `market-data.test.mjs` | 2 | 1 | 0 |
| `inference-evidence.test.mjs` | 2 | 1 | 0 |
| `p3-contracts.mjs` | 2 | 1 | 0 |
| `supervisor.test.mjs` | 2 | 1 | 0 |
| `check-no-fake-live.mjs` | 2 | 1 | 0 |

- 每条 gate 天然出现 **2 次**（`test -f <path>` 一行 + `node <path>` 一行）→ 若按**全文**要求 `hits==1`，会在**当前完全正确的文件上直接误报**（**假红**）。
- 假红与假绿同害：它会挡住正常 CI，维护者最可能的反应是**放宽断言**（把洞重新打开）或 `--no-verify` 跳过。
- → 唯一性断言**只能限定在 `node` 调用行内**；`day14-p0-runner.mjs` 的第 3 处正是 **L26 的注释**，是仓库里现成的「路径出现在注释中」样本。

### §11.8.8.3 三方收敛后的修复口径（记录备查，实施归 `devops`）

> **本节规范经 §11.8.10 细化后为最终版；下表第 2、5、6 步以 §11.8.10 为准。** 保留原文以便对照演进过程。

```
1. 剔整行注释（^\s*#）；（辅助）剔行尾注释
2. S = /^\s*node\s+(\S+\.mjs)/ 提取          // 不带 $，避免行尾注释漏提；作用域=node 行
3. assert EXPECTED.length > 0               // 封「提取空 + 期望空」双空对撞，不写死 7
4. assert S.size >= 7                        // 先证提取器没坏（防空集相等假 PASS）
5. assert setEqual(S, EXPECTED)              // 主判据：新增未登记也红
6. assert ∀s∈S：s 在 node 行内出现 1 次       // 防重复执行；作用域=node 行，不是全文
7. assert ci.yml 的 on.pull_request.branches 非空且含 feature/v16-**   // 补 F-23 第三面
```

- 第 3 步用 `> 0` 而非 `=== 7`：`EXPECTED` 为字面量时 `===7` 零保护且制造第二个改动点；若为派生值，`>0` 已足够封双空。**「加第 8 条 gate」仍会被第 5 步 `setEqual` 抓到**（长度不等），保护无损失。
- 第 7 步针对 **F-23 第三面（`ci.yml` 的 `pull_request.branches` 零覆盖）**——修 Y1/Y2 **不会**顺带覆盖它。
- **归因（三方一致的表述，本审计采纳）**：`yamlLines()` 不剔注释 → ① Y2 注释化绕过；② Y1 注释化 / 挪段绕过；③ 任何「路径出现次数」断言因注释偏 1；④ 行尾注释使带 `$` 的提取器漏行。**同一条根因四次显形**；结论是：**在本脚本内做任何文本统计前，必须先剔注释并限定作用域**。

### §11.8.8.4 影响与归属

- **对八条裁定与 DECISION：无影响**（均为实现细节）。
- **归属**：实施 = `devops`（Y1/Y2 修改不在其当前授权内，待 team-lead 批）；复核建议 = `independent-qa`（其对「注释先于提取」「`EXPECTED>0`」与我独立收敛）；**记录与归因 = 本审计**。
- **签字口径（不变）**：`devops` 改完后本审计**独立复跑**「改前 A/B/D/E 全 PASS、C FAIL、基线 PASS；改后 A/B/D/E 全 FAIL、C 仍 FAIL、基线仍 PASS」后另出结论；**不复跑则写「未复核」，不以他人实测值代签**。

## §11.8.9 更正 ⑤：我「引号含 `#` = 0 条」的结论错了（检索口径过窄，devops 指出）

> 性质：**事实性错误更正**。我在 §11.8.8.1 写「实测当前 `ci.yml` 内『引号含 `#`』的行 = 0 条」，`devops` 独立复测指出为 2 条。我重测确认**他对我错**，故记录于此，原文保留不删。

### §11.8.9.1 错在哪

- 我当时的命令是 `grep -E '"[^"]*#[^"]*"'` —— **只覆盖双引号**。`ci.yml` 的两处 `#` 都在**单引号**内，被完全漏掉。
- 重测（`'[^']*#[^']*'`）结果：

| 行 | 内容（截断） |
|----|--------------|
| L65 | `const note = '### Unit tests (mock)\n\n' + s +` |
| L140 | `const note = '### Live market-data probe\n\n' + s +` |

两条都在 gate 步骤内的 inline `node` 脚本里，`#` 确实位于成对**单引号**内部。

### §11.8.9.2 现状仍然安全，但**安全的原因被我写错了**

- 朴素 `/\s+#.*$/` **要求 `#` 前存在空白**；这两处的 `#` 紧跟 `'`（`'###`），**不满足** → 不匹配 → 不会被误切。
- 我独立复测的影响面（与 `devops` 数值一致）：

```
匹配 /\s+#.*$/ 的行数            = 22
  其中非整行注释（即可执行行）    = 0      ← 今天安全
引号内含「空格 + #」的行数        = 0      ← 真正的危险形状，当前不存在
当前 /^\s*node\s+(\S+\.mjs)/ 提取到的 node 行 = 7（与期望一致）
```

- → **真正的危险边界不是「引号内有 `#`」，而是「引号内出现『空白 + `#`』」**（如 `'… see issue #123'`）。方向我判断对了，**触发条件我写错了**。

### §11.8.9.3 为什么这条更正对结论有正面作用（而非削弱）

1. **它加强了「去 `$` 为主修法」的理由**：只要未来出现一行「引号内『空格 + `#`』」，剔除逻辑就会出错；而不带 `$` 的第 2 步**不受剔除逻辑正确性影响**，仍能提出正确路径。
2. **`devops` 另补一条我未想到的排查体验问题**：带 `$` 时合法行消失会表现为「`S.size >= 7` 不满足」，错误信息指向「**数量不足**」而非「**提取失败**」→ 误导排查方向。这条我采纳：**失效时的错误信息质量本身也是可靠性的一部分**。
3. **三方（我 / `independent-qa` / `devops`）对最终 7 步口径无分歧**，第 2 步统一为**不带 `$`**。

- **对八条裁定与 DECISION：无影响**。本节仅更正一处事实与两处措辞。

## §11.8.10 修复规范的最终细化：`hits` 按行计 + 前瞻正则（避免"修完制造假红"）

> 性质：**把我自己 §11.8.8.3 里的两条规范改准确**。原因不是措辞，而是**按原文实现会造出假红**——假红同样会诱发"放宽断言"从而把洞重新打开。以下为**最终口径**。

### §11.8.10.1 「不剔行尾注释」是对的选择，但它把要求转移到了 `hits` 的定义上

`devops` 提出并经我认可的关键论证：`run: |` 是**字面块标量**，` # legacy` 原样进入 shell 脚本，而 shell 把「空白 + `#`」当注释 → `node x.mjs  # legacy` **实际执行的就是 `node x.mjs`**。因此**不剔行尾注释、直接提取**与执行语义一致，且**避开了引号感知剔除的误伤风险**（`echo "see issue #123"`）。→ 结论：**不剔行尾注释不是妥协，是更正确的选择**；第 1 步只需保留「整行注释剔除」这一半。

**但它带来一个必须钉死的定义**：既然不剔注释，`hits` 就**不能**用「路径在文本里出现几次」，否则会假红。实测（仅 1 条真实执行行、行尾注释重复同一路径）：

```
node apps/x/p3-contracts.mjs  # 同 apps/x/p3-contracts.mjs 的旧写法已废弃
  子串计数 = 2  → hits==1 = false   ← 假红
  按行计数 = 1  → hits==1 = true    ← 正确
```

→ **`hits(s) := 「捕获到的参数 === s」的 node 行数**。语义上也更对：要防的是「**同一个脚本被执行两次**」（行级），不是「字符串被提到两次」（文本级）。这与 §11.8.8.3 的「两个域」原则同源：**按语义域计数，不按文本出现次数计数**。

### §11.8.10.2 去掉 `$` 后需要前瞻，否则「提取值 ≠ 执行值」

无 `$` 的 `/^\s*node\s+(\S+\.mjs)/` 对 `node apps/x/p3.mjs#foo` 会捕获 `apps/x/p3.mjs`，而 shell 实际传入的参数是 `p3.mjs#foo`（`#` 前无空白 → **不是** shell 注释）。→ 建议加前瞻 `(?=\s|$)`，实测：

| 输入 | 无前瞻 | 前瞻版 |
|------|--------|--------|
| `node apps/x/p3.mjs` | `p3.mjs` | `p3.mjs` |
| `node apps/x/p3.mjs  # legacy` | `p3.mjs` | **`p3.mjs`**（仍容忍行尾注释） |
| `node apps/x/p3.mjs#foo` | `p3.mjs` ❌ 提取≠执行 | **不匹配** ✅（该行本身已损坏，交给 `S.size` 报错） |
| `# node apps/x/p3.mjs` | 不匹配 | 不匹配 |
| `test -f apps/x/p3.mjs \|\| …` | 不匹配 | 不匹配 |

### §11.8.10.3 最终规范（以本节为准）

```
1) 剔整行注释 /^\s*#/                                    // 必需：否则污染第 5 步
2) S = /^\s*node\s+(\S+\.mjs)(?=\s|$)/ 的捕获            // 无 $，带前瞻；不剔行尾注释
3) assert EXPECTED.length > 0                            // 封双空
4) assert S.size >= 7                                    // 证提取器没坏
5) hits(s) := 捕获参数 === s 的 node 行数；assert ∀s∈S: hits(s) === 1   // 按行计，非子串
6) assert setEqual(S, EXPECTED)                          // 主判据
7) assert ci.yml 的 on.pull_request.branches 含 feature/v16-**          // 补第三面
8) 失败码区分 EXTRACT_FAILED（第 4 步，指向提取器/正则）与 MISMATCH（第 6 步，指向清单变更）
```

- **第 8 步（`devops` 提出，本审计采纳为验收标准）**：**失败信息指向哪个部件，决定维护者会不会修错地方**。若 `S.size` 不足却报「少写了一条 gate」，人就会去翻配置而非正则，问题会被"修"成更坏的形态（放宽断言）。故两类失败必须用不同错误码。
- **对八条裁定与 DECISION：无影响**；本节仅细化实现规范，不改变 F-23 的定性与级别。

---

*第五轮（V2.0 收口轮 / T05）结论：**GO WITH CONDITIONS（仅限 Security 范围）**。八条点名项 **8/8 VERIFIED**（范围逐条标注），**0 条目级 NOT_VERIFIED**；新增 F-16（medium，P3 硬前置 → C9）/ F-17 / F-18 / F-19 / F-20 / F-21 / F-22（medium，证据门可空转 → C12）/ **F-23（medium，守卫断言弱：Y1 只验存在且可被注释/挪段绕过；Y2 清单滞后 5→7 且可被逐条注释绕过；`ci.yml` 的 PR 触发零覆盖；行尾注释使带 `$` 的提取器静默漏行 → 并入 C12）**，条件扩至 **C1–C14**（C1、C7 已满足；C2–C6、C9–C12、C14 未闭环；C13 待主理人决策）。R6 仍 **UNVERIFIED**；子命题 UNVERIFIED 经 §11.8.1 更正后为 **3 处**（抗伪造 / 云 D1 / 真实账单·venue 实测），**不进 GO 依据**；类型检查已转 VERIFIED（`tsc --noEmit` exit 0、`vitest run` 22 文件 217 通过，限工作区态）。**C9（verified 语义须绑定真实 egress）是 P3 解锁硬前置**：不解决则 P3 解锁后正式门会用 fixture 价格放行真实下单。**C12/F-22/F-23 阻断「以测试门全绿作为放行依据」的论证路径**；**C14 阻断 T03 的 PR 签收路径**（PR 开向 `feature/v16-market-provenance-hardening` 时两个 workflow 均不触发；**最省事的解除方式是给 `pull_request.branches` 追加 `feature/v16-**`，经复刻确认不需要改 Y1**）。本结论不构成 P0 整体 PASS、不构成生产 GO、不构成可上真实资金的授权。0 项伪造 PASS，0 项把 UNKNOWN 记作 0，0 项输出凭据内容，0 项越权签字，0 项改源码 / 测试 / 旧 freeze / CI，0 项 commit / push。*

> **§11.7 复核留痕**：`independent-qa` 提示两条（`config/.env` 真实凭据未入库、`t1–t9` 单独执行 = 0 断言空转），本审计**独立复现后均予采信**；并据此**更正了 §11.1.4 / §11.1.5 / §11.6 中「t1–t9 83/83」的方法学表述**（83 条来自聚合 harness，非单文件执行）。同时新增两条 QA 未点出的项：聚合 runner 的 `0 checks ⇒ PASS` 缺陷、以及 §10.3「CI 覆盖缺口」在三个提交中被修掉（两处均为我独立实读所得）。**（后者已于 §11.8.2 部分更正：仅 push 成立，PR 仍缺口。）**

> **§11.8 二次交叉核实留痕**：`independent-qa` 二次回复后，本审计**实测推翻了自己两处表述并就地更正**——① `node_modules` 现已存在，`tsc --noEmit` exit 0、`vitest run` 22 文件 217 通过 → **类型检查子命题由 UNVERIFIED 转 VERIFIED**（4 处子命题降为 3 处）；② 我 §11.7.3 说「CI 覆盖缺口已修掉」**只对 push 成立**，用 `yaml@2.9.1` 真解析确认 **PR 开向 `feature/v16-market-provenance-hardening` 时两个 workflow 均不触发（0 check）** → 新增 **C14**；③ 我 §11.7.4 说临时文件「未见收尾清理」**是错的**，源码有 `finally { cleanTmp(); }`，实跑无残留，已下调定性。同时**收窄了 QA 的一处过宽表述**（Y2 删除**已登记** gate 是能抓到的），并**新增 F-23**：Y2 可被「全部注释掉」骗过（4 变体实验）、只登记 5 条而实际 7 条、`yamlLines` 不剔注释；而 `yaml@2.9.1` 现可用，故「只能纯文本自检」的前提已不成立。**DECISION 不变，八条裁定不变，F-23/C14 均不阻断条目级裁定，但 C14 阻断 T03 的 PR 签收路径。** **（其中「`yaml@2.9.1` 现可用」一句已于 §11.8.7.2 附加条件：它只是 `postcss-load-config` 的传递依赖，非直接依赖。）**

> **§11.8.7 第三轮更正留痕（我错了第三次，devops 指出）**：我原写「放宽 PR 触发分支会先撞红 Y1 自检」**过强**。复刻 Y1 四段断言实测：**追加** `feature/v16-**`（保留 `release/jcc-v1.3-final`）→ **Y1 仍 PASS**；**只有移除** release（改成 `main`）→ 才 FAIL。原因是 `l.some(x => /.../.test(x))` **只验字符串存在、不验唯一**。→ **C14 两条修法成本需重估：追加路线不需要改 Y1，是最省事路径**；我原「必须同步改 Y1」的判断撤回（原文保留不删）。
> 我另外补了一条双方都没提的：**把 PR 分支整段注释掉、字符串仍留行内 → Y1 照样 PASS**（与 Y2 同一病根），故 **F-23 适用范围扩为 Y1 + Y2 两处**；且 `ci.yml` 的 `pull_request.branches` **完全没有 gate 覆盖**。
> 同时更正我 §11.8.4 的修复建议：`yaml` **不是直接依赖**（由 `postcss-load-config` 传递引入）→ 「改用真解析」须先声明依赖，**首选 devops 的无依赖四条**（剔注释 / 行首锚定 / `node` 条数下限 ≥7 / 路径集合相等）。

---

# §12 第六轮（V2.1 账本写入顺序重排）：独立安全评审 + 三处自纠

> **锚与树状态（必须成对读）**：本节评审的**提交态**是 `00e08e39ba298d73d93ce126eca7e85f02b77238`（`00e08e3`）。本节**开始撰写时** `git status --porcelain` 为空；撰写期间工作树被**其他成员**写入（非本审计所为），收稿时状态为：
>
> ```
>  M apps/command_center_3d/functions/api/jev/[[path]].js
>  M apps/command_center_3d/server/trading_api.mjs
>  M reports/JEV_REAL_TRADING_SECURITY.md          ← 本节自身，未提交
> ?? apps/command_center_3d/server/lib/http-body.mjs
> ?? apps/command_center_3d/server/tests/
> ```
>
> 因此 **§12 的结论锚在 commit，H7 的扫描计数锚在"上述状态"**，二者不等价，不可混用。可证伪的痕迹：本节三次 H7 复跑的扫描文件数依次为 **218 → 219 → 220**，增量全部来自期间新增的未跟踪文件，且这些文件被**逐个**纳入扫描（收稿时实见 `apps/command_center_3d/server/lib/http-body.mjs` 与 `apps/command_center_3d/server/tests/http-body-read.mjs` 均在 `scanned_files` 内）——**计数本身随树状态变化**，这正是"证据必须绑定状态"的现场例证。
> 两条附带事实：① 以上未提交改动**无一落在冻结树内**（`apps/command_center_3d/server/engine/` 命中 0）⇒ 新模块 `server/lib/http-body.mjs` 不触 check3、不逼人重钉 15 文件锚；② 因此 P-01「洁净树」门禁在此期间**为红**，冻结只能以 commit 为单位表述。
>
> 评审对象是 **D-14-0 / D-14-3 的写入顺序重排**（`lease_epoch → cycle_intent → cycleImpl → cycle_completion`），不评审代码是否已落地（现状：`git log --all -S"cycle_intent"` 与 `-S"IN_DOUBT"` 均为空，`core.mjs` 仍是 `appendFileSync` 直写）。

## §12.0 三处自纠（先纠，再评审）

**自纠 ①：`ai-core.mjs` 在冻结树内，我上一轮的「更正」是错的。**
按 `scripts/v16_independent_verify.py` 的 check3 算法复算（`git ls-tree -r HEAD -- apps/command_center_3d/server/engine/`，行过滤后 `"\n".join`、末尾不带换行、utf-8 取 sha256）：

```
n = 15
tree_sha256 = aec5d1b7cc26db50736d355e7ef14e066fae3f2c2a376d94637c9a977008a9ad
expected    = aec5d1b7cc26db50736d355e7ef14e066fae3f2c2a376d94637c9a977008a9ad   → MATCH
  efe3bd3e…  server/engine/_lib/ai-core.mjs   ← 在 15 个之内
  9ed13096…  server/engine/_lib/paper-server.mjs
```
我错在**数错了清单**：用 `apps/equity_collector_v45/src/engine-anchor.mjs` 的 4 文件锚去否证 `check3` 的 15 文件锚。本仓目前有三套近名冻结清单，任何「某文件是否冻结」的断言都必须先指明是哪一套：

| 清单 | 位置 | 覆盖 | 执行时机 |
|---|---|---|---|
| 逐文件 sha256 锚（4 条） | `apps/equity_collector_v45/src/engine-anchor.mjs` | 4 文件，指向 `D:/jev-cloud-staging` 参考引擎 | collector T5（本地/测试） |
| 整树 sha256 锚 | `scripts/v16_independent_verify.py:54-56` | **15 文件**（含 `ai-core` / `paper-server` / `agent-orchestrator`） | **CI 的 check3** |
| git blob sha1 清单 | `cloud/vendor-manifest.json` | **6 个 blob**，`origin_commit` 指向外部仓库 | 仅 `cloud-entry.mjs` 启动时 |

纪律补一条，排在我原有的「正例 + 反例计数」之前：**成员资格断言，先找到「真正执行该约束的那段代码」，再做计数。**

**自纠 ②：`DUPLICATE_REJECTED` 是恒真式判据（本次评审的直接动因）。**
`idempotency.mjs:21` 的 `dup` 判定基于**已写入的 completion 记录**；重跑时那条记录当然已存在，故必然抛。它测的是账本自洽，不是 at-most-once。实测（harbor，8 trial）`trials_with_duplicate_effect=4`：副作用执行两次而账本链 `verified=true`、`resume` 正常、`recordCompletion` 照抛 ⇒ 该判据在缺陷存在时**照绿**。已在任务表撤回，替换为跨文件守恒（见 §12.2）。

**自纠 ③：`vendor == 上游逐字副本` 不可证，且「fail-late」也不准确。**
可证的只有两件事：`diff` 0 行（vendor 与 `server/engine/_lib` 同字节）、`git hash-object` 等于 manifest 记录的 `git_blob_sha`。但 `origin_commit` 指向的外部仓库对象在本仓不存在，**本地无法证明与上游一致**。另外 `verify-engine-vendor.mjs` 全仓唯一引用是 `cloud-entry.mjs:1`，而 `cloud-entry` 在 5 个 workflow 中命中数为 0 ⇒ 它在 CI 里**从不执行**（不是"晚触发"）。覆盖面上 manifest 只有 6 条、check3 覆盖 15 条 ⇒ 9 个文件没有任何 vendor 身份覆盖。

## §12.1 四个强制点：写错会让攻击者得到什么

| 强制点 | 写法 | 写错的后果 | 攻击者（或误操作者）能做什么 |
|---|---|---|---|
| **P1** `lease_epoch` 落盘先于 `cycle_intent` | `acquireLease → append lease_epoch(+fsync) → intent` | 僵尸领袖 A 与接管者 B 同时持"活" epoch：A 醒来写 intent 时 `epoch >= max(seen)` 判定通过 | **不需要凭证**。只需让进程卡顿/被暂停（CPU 饥饿、调试器挂起、长阻塞），等到租约过期被接管，即出现双写窗口 ⇒ 同一 cycle 两进程各执行一次副作用 = 双下单 |
| **P2** 围栏判定在**写路径内部** | 判定放在 `appendChainEntry` 或其包装里，而非 `acquireLease` 之后 | read-then-act：进门时 epoch 最新，写 intent 时已被抬高 ⇒ 陈旧写被接受 | 窗口比 P1 更长，且**恰好覆盖副作用执行期间**（最需要围栏的时段）。等价于"只在进门查票" |
| **P3** `intentHash` 配对不变量 | ①completion 指向存在的 intent 且 `(runId,cycle)` 相同；②一 intent 至多一 completion | 把 cycle7 的 intent 与 cycle8 的 completion 配对，**链完全校验通过** | 无需攻击者：**运维手工修复**即可构造出"看起来完整"的账本。后果是 at-most-once 判定错位——真实的双执行在账本上表现为"两周期均已完成"，**审计不可发现** |
| **P4** `IN_DOUBT` 双点强制 | `runNextCycle` 抛 `CYCLE_IN_DOUBT_MANUAL_REVIEW` **且** `runCycle` 在存在 `cycle < N` 未完成 intent 时拒绝执行 | 只堵 `runNextCycle` ⇒ 直调 `runCycle({cycle:N})` 的所有路径（未来 daemon / 脚本 / CLI）绕过强制点 | 把"必须人工裁定"降级为"静默重跑"。若 `--resolve-in-doubt` 不取租约，两个运维可同时裁定同一 intent ⇒ 两条 outcome |

**P4 的补充**：`supervisor.mjs:92-95` 的 `runNextCycle` 当前不看任何 flag（`resume.resume()` 后直接 `runCycle`）；`:69` 的 `cycleImpl` 在 `:73` 的 `recordCompletion` 之前。这两处是 at-most-once 的唯一缺口，且**不是存储问题**：任何写入原子性方案（含 `tmp+rename`、`fsync`）都关不掉 `:69→:73` 之间的窗口。

## §12.2 校验位置定理（本次评审的核心结论）

> 在 at-most-once 系统里，**任何可能"在副作用之后才触发"的拒绝路径，都等价于把 at-most-once 换成 at-least-once。**

推论：
1. 对**攻击者/调用方可控字段**（`runId`、以及执行前即可知的 payload 字段）的长度、深度、类型校验，必须**前移到 `cycle_intent` 写入之前**；
2. 副作用发生之后只允许两种行为——**无损记录**，或**带哈希的有界截断**（记 `payload_sha256` + 前 N 字节），**不允许 `throw`**；
3. 因此「entry 上界」**不能**实现成 `recordCompletion` 里的 `throw`：那会把一次正常的超限拒绝变成一次不可恢复的重跑。上界校验必须在 `runNextCycle` 入参处拒绝，并在 intent 里预留摘要字段。

这条同时给出 R2′ 的正确形态：能量守恒必须**跨文件**（`账本中 intent 数 == 副作用日志条数`），因为账本自证在 P3 失守时仍会通过。它的可测性依赖**副作用层提供幂等键**（下单层产出 `clientOrderId` 之类的键）；没有该键，R2′ 只能退化为账本自洽——也就是自纠 ② 里那个恒真式。

## §12.3 entry 上界：8KB 不够，且理由不是"够不够大"

`entry = canonicalize({kind, runId, cycle, completedAtMs, payload, _prev, _hash})`。可控增长项只有 `runId`（字符串）与 `payload`（自由对象，`idempotency.mjs:18` 无界）。当前调用链上 `runId` 来自调用方字面量、`payload` 只有 `cycleResultLabel`（短串），**全仓唯一调用方是测试**（`grep runNextCycle/.runCycle(` 命中全在 `supervisor.test.mjs`）⇒ **今天不可被攻击者驱动**，8KB 是面向未来的边界，不是当前攻击面。

但 8KB 与「kill 造不出撕裂」这个结论的关系不是"够大"，而是**单次 `write(2)` 是否不可分割**：≤4KB（页）在本地文件系统可视为单次写；8KB 跨页 ⇒ 可能多次写 ⇒ 撕裂由 kill 可达。**不能两头都占**：上界写 8KB 又不承认撕裂可达，等于把 harbor 的 12/12 当作结构性结论，而它只观测到"当前 payload 很小"。

建议**两级**：
- **硬上界 4KB**，超出在 `cycle_intent` 写入前拒绝（依 §12.2 推论 1）；
- 另设"摘要 + 有界前缀"落账，使超限不至于让周期永远无法记账；
- 原子性**不依赖**上界：撕裂由 R7（注入截半末行）+ 头锚检出。上界服务的是内存/磁盘与"kill 不可达"这一**前提**，不是原子性本身。

附带更正一处归因：`appendChainEntry` 每次 append 都 `readJsonl` + `verifyChain` **全量重读重校验** ⇒ O(N²) 在**当前 append 设计里本来就存在**，不是 `tmp+rename` 的特有缺点。entry 上界不解决 O(N²)，但不设上界会把它放大成 O(N²·entry)。

## §12.4 威胁模型边界（防止本次评审被过度引用）

- 账本是**本地文件**。能写该目录的本地用户可以直接改账本、改锚、改二进制，本节所有机制**不对其提供保护**；头锚与链哈希只保证"改过会被发现"，不保证"改不了"。
- 本机制保证的是两类事：**崩溃一致性**（掉电/短写后不静默重跑）与**多进程互斥**（同机两个 supervisor 不双执行）。跨机、跨权限域不在范围内。
- 因此"攻击者"在本节里指：能影响进程调度与崩溃时机、能调用 CLI、能写入计划任务的一方；**不是**已 root 的本地用户。

### §12.4.1 三条限制与两条实现陷阱（本轮实测确认，必须与修复同批写进注释）

1. **`headHash` 取「最后一条 `_hash`」是 O(1)，但判的是语义完整、不是字节完整**：插入空行、改空白等不影响解析的编辑不敏感。故本报告不得写"字节级可检出"。
2. **`lastGood` 水位文件在链外**：篡改者同时截断账本并回写水位则检不出。它把"静默"升级为"必须同时改第二个文件"，**不是可证安全**——报告与验收话术都不得写成"已解决"。
3. **内存 `fsImpl` 的 `writeSync(fd, data)` 是 `files.set(key, asText(data))` = 整文件覆盖**，不遵守 `open(p,'a')` 的追加语义；而 `fsyncSync` 与 `truncateSync` 在 `core.mjs` 的命中数均为 **0**。⇒ 实现若从 `appendFileSync` 改为 `open('a') + writeSync + fsyncSync`，**测试语义会与磁盘语义分叉**（fixture 下变成覆盖）。同一提交必须：`writeSync` 尊重 `'a'` 标志、补 `fsyncSync`（且**可被调用计数观测**，"不抛错"不算通过）、按需补 `truncateSync`，并加一条"追加后前序字节仍在"的断言。
4. **自纠**：我上一轮称内存 fs「方法全集（穷举）」为 8 个，实测是 **10** 个（漏了 `writeSync` 与 `mkdirSync`）。"穷举"这个词只应在逐条列出并计数之后使用——这与我要求别人的纪律是同一条。

## §12.5 必须存在的变异用例（每条都要能独立变红）

1. 去掉 `lease_epoch` 条目（退回 `acquire → intent`）⇒ 并发双写用例必须红；
2. 把围栏判定挪到 `acquireLease` 之后（模拟 read-then-act）⇒ 必须红；
3. cycle7-intent 配 cycle8-completion ⇒ 配对不变量必须红；
4. 一条 intent 两条 outcome ⇒ 必须红；
5. `--resolve-in-doubt` 不取租约 ⇒ 并发裁定用例必须红；
6. 注入崩溃于 intent 与 completion 之间 ⇒ R2′ 必须红，且以**元断言**形态（"检查确实失败"本身是被测对象，`expect(exit!=0)`），否则它是一个永远失败、无人能合并的测试；
7. payload 超 4KB ⇒ 必须在 intent 写入前拒绝，用例同时断言**副作用未发生**（若在 completion 处拒绝，即 §12.2 的 at-least-once 转换，必须被判红）。

## §12.6 DECISION 增量（仅限 Security 范围）

- **不新增独立 blocker**：上述四点都是 `LEDGER-ATOMIC` / D-14-0 的**修复判据**，不是新缺陷。**Phase GO 仍为 NO-GO**，三个独立理由不变（B2b 未验证 / `LEDGER-ATOMIC`+D-14-0 / `BODY-READ-UNBOUNDED`）。
- 新增**修复必须满足**的条件（编号续接）：
  - **C15** `lease_epoch` 必须先于 `cycle_intent` 落盘且带 fsync；
  - **C16** 围栏判定位于写路径内部，且 `IN_DOUBT` 在 `runNextCycle` 与 `runCycle` 两点强制；
  - **C17** `verifyChain` 新增配对不变量（存在性 + 同 `(runId,cycle)` + 一 intent 至多一 outcome），并有两个变异用例；
  - **C18** 上界 4KB 且校验前移；副作用之后禁止 `throw`。
- 本节 0 项把 UNKNOWN 记作 0，0 项输出凭据内容，0 项改冻结 15 文件，0 项 commit / push。

## §12.7 方法学留痕：复核动作本身会改写证据（本节新增，与前面所有结论无关）

本节写作过程中实测到一条**证据溯源**风险：**`import` 该 verifier 会改写它自己的证据文件**。以 `importlib` 加载 `scripts/v16_independent_verify.py` 并调用 `check6()` 后，`reports/evidence/v16/independent_verify.json` 的 `git diff` 显示：

```
- "generatedAt": "2026-11-09T15:43:18.025816+00:00"
+ "generatedAt": "2026-11-11T15:46:39.882385+00:00"
- "root": "C:/Users/Administrator/WorkBuddy/jev-v16"
+ "root": "D:/jev_worktrees/v2ops"
  … 以及 v16_changed_files 全量重建（259 insertions / 3 deletions）
```

后果：任何按「import 后调用 `check6()`」复核的人，都会把**别人那次运行**的证据文件改写成**自己机器 root** 的版本；若随后被提交，等于**用复核动作伪造了一次新的原始运行**。这与本报告前面反复处理的「文档改掉自己的锚」「哈希不标算法」是同一类错误的第三种形态：**证据被复核动作改写**。

本次已按 `git show HEAD:reports/evidence/v16/independent_verify.json > …` 原样恢复，工作树只保留本报告的修改。建议后续二选一：脚本加 `--no-write` / 输出重定向；或复核流程规定「先记 blob sha，复核后按 blob 恢复」。

**本节边界**：§12 全部结论锚在 `HEAD=00e08e3`（`h7` 扫描：218 文件、0 命中、本节在扫描范围内）。写作期间工作树出现其他成员**未提交**的改动（`trading_api.mjs` 修改、新增 `server/lib/http-body.mjs`）——**本节不评审工作树**，只评审提交态；工作树评审须等其形成提交后按 SHA 复核。

## §12.8 读数约定：同一批字节在本仓有三种摘要，引用必须标算法

本轮出现一次**差点成立的假发现**：按文件比对 `paper-server.mjs` 时，`9ed13096…`（git blob sha1）与 collector 锚里的 `f9ac6e14…`（逐文件 sha256）不一致，一眼看是"两份分叉的冻结引擎"。实测 `diff` 为 0 行、两处均 1106 行 / 52458 字节 ⇒ **同一个文件，两种算法**。

现有三种约定并存：

| 摘要 | 算法 | 出处 | 覆盖 |
|---|---|---|---|
| `git_blob_sha` | git blob `sha1("blob <len>\0" + bytes)` | `cloud/vendor-manifest.json` | 6 文件 |
| `ENGINE_ANCHORS` 值 | 逐文件 `sha256(原始字节)` | `apps/equity_collector_v45/src/engine-anchor.mjs` | 4 文件 |
| `ENGINE_TREE_SHA256` | `sha256(ls-tree 原始行以 \n 连接、末尾不带换行、utf-8)` | `scripts/v16_independent_verify.py:55` | 15 文件 |

**约定**：本报告及后续任何报告，每个哈希旁边必须写算法与覆盖集合；两个哈希不相等时，**先排除"算法不同"，再断言"内容不同"**。这条不是排版偏好——它防的是审计者自己造出一个假 P1，而本轮已经有两个人各自差一步。同样的道理适用于计数：**先确认在数哪个清单**（§12.0）。

## §12.9 判据灵敏度：三条"看起来在守、实际拦不住"的形态（本轮实测）

### §12.9.1 `trace` / `STAGES` 索引断言对 D-14-0 **零灵敏度**

`STAGES`（`supervisor.mjs:12` `Object.freeze`）与 `trace`（`:33/:43/:54/:64/:68/:72/:78` 的 `trace.push('…')` 字面量）**两个操作数都取自源码位置**，而真实的 `appendChainEntry(intent)` 是**另一条语句**。把这条语句搬到副作用之后，`trace.push` 一个字都不用动 ⇒ **断言照绿**。即它是**声明对声明**，不是"声明与执行一致"。

实测（harbor 在 `%TEMP%/v46/assert-vacuity.mjs` 上跑，`EXIT=0`，只读 import 仓库 `core.mjs`）：

```
scenario                          trace索引断言   盘上状态断言   副作用执行次数
S0 基线 intent 先写 + fsync          PASS(绿)        PASS(绿)        1
S1 副作用先、intent 后写             PASS(绿)        FAIL(红)        1
S2 顺序对但去掉 fsync                PASS(绿)        FAIL(红)        1
S3 两者同时                          PASS(绿)        FAIL(红)        1
trace 索引断言 实际拦住 = 0/3        盘上状态断言 = 3/3
```
**S1 就是 D-14-0 本身**，而它漏掉的正是这个。故本轮定：**判据不得用 trace 索引**。

**正确形态（盘上状态断言）**：在注入的 `cycleImpl` 内部读盘，断言
1. 已存在 `(runId, cycle)` 的 `cycle_intent` 条目；
2. `fsyncCalls >= 1`（计数可观测，"未抛错"不算）；
3. 该 intent 的 `_hash` 与随后 completion 的 `intentHash` 相等。

这条同时解释了为什么 `supervisor.test.mjs:244/:258` 不能充当 D-14-0 的守卫：它们守的是**标签序列**，不是**操作顺序**。

### §12.9.2 内存 fixture 能产出"绿 = 丢了数据"

`core.mjs:55` `appendFileSync` 是追加，`core.mjs:89` `writeSync(fd,data)` 是 `files.set(key, asText(data))` **整文件覆盖**、忽略 `open(p,'a')`。实测后果：
- `openSync(L,'a') + writeSync(fd,'APPENDED\n')` ⇒ 文件只剩 `APPENDED\n`，两条历史条目消失，`readJsonl` 抛 `LEDGER_PARSE_FAILED at line 0`；
- 若被覆盖的 chunk 本身是**合法 entry** ⇒ `verifyChain` 返回 `{"intact":true,"count":1}`，**实际丢了 2 条仍报 intact**。

⇒ 任何「`openSync('a')+writeSync+fsyncSync`」的耐久性用例，**在修 fixture 之前会假绿**。这一项必须排在任何写入路径用例之前（§12.4.1 第 3 条）。

### §12.9.3 邻居未修：`floorSlugs` 只比**数量**，不比**集合**

`check-no-fake-live.mjs:446-448`：
```js
const floorSlugs = (floorSrc.match(/^\s{4}slug:\s*"[^"]+"/gm) || []).length;
assert.equal(floorSlugs, GATE_SUITES.length, …)
```
而**同一个函数、30 行之前**的 Y2（`:396` 起）已被特意从数量升级为 comment-proof **集合**比较（`:415-425` 的 `missing/unexpected` + 相等断言）。代入式：`ci.yml`/`GATE_SUITES` 加 `9-foo`、floor 的 `SUITES` 删 `1-p0-contracts` ⇒ `8==8` 仍绿，而 `1-p0-contracts` 从此没人核用例数——**正是 `:441` 注释声称要防的事，只是方向从"加而不登记"换成"换而不登记"**。
修法：取出 floor 的 slug 字符串，与 `GATE_SUITES` 的 basename 做 **Set 相等**（与 Y2 同一套）。

### §12.9.4 聚合值 `total_cases` 的复制必然过期（本轮已发生一次）

floor 表现有 **8** 个套件（`1-p0-contracts 7 / 2-p0-runner 24 / 3-market-data 10 / 4-inference-evidence 17 / 5-p3-contracts 12 / 6-supervisor 14 / 7-no-fake-live 13 / 8-api-body-read 17`），合计 **114**。而 `total_cases=97` 被字面复制在多处，且**部分不带 SHA 限定**：
`artifacts/release/p0_p2_acceptance/P0_P2_ACCEPTANCE_REPORT.md:51,70,72,225,312,324,327,396`；`reports/JEV_V2_FINAL_ACCEPTANCE_SUPPLEMENT.md:320,323`（该段属 `13882c4` 的捕获，:323 的算术 `7+24+10+17+12+14+13=97` 描述的是**那个 SHA 的 expect 表**，不是当前表）。
**约定（本版修正，原写法自身会过期）**：
1. **每个聚合值必须与其产出 SHA 同现**，且只允许出现在**锚定该 SHA 的捕获块**内；历史值可引用（它是对一个不可变提交的事实），但**不得被读作当前值**。
2. **任何位置都不得陈述"当前值"**——当前值只能由 CI 打印的 `GATE_FLOOR_OK suites=N total_cases=M` 给出，不进入文档。因此**替换文案里也不许写新的聚合数**：把 `97` 换成 `114` 只是把会过期的数字换成一个更快过期的数字。
3. 本条对**我自己上一轮给出的替换句**同样适用：那句里的「合计 114」只在 "#16 已落、Z6 未落" 这一个状态下成立，Z6 一落即为 115 ⇒ 该句作废。**正确的替换句不含任何当前值**：
   > 本表规模与合计以 CI 打印的 `GATE_FLOOR_OK suites=N total_cases=M` 为准，**本报告不复制 N/M 的具体值**；`13882c4` 时刻为 7 套件 / 97 块，此后随套件与 `check()` 的增加而变。

**结构性推演（非当前值；任何一行被单独引用即失效，必须连同其状态标签一起引）**：

| 状态 | `7-no-fake-live` expect | 聚合 `total_cases` | `suites=` |
|---|---|---|---|
| `00e08e3`（7 套件） | 13 | 97 | 7 |
| +#16 第 8 套件（+17 块） | 13（不变） | 114 | 8 |
| +Z6（+1 `check()`） | **14** | 115 | 8 |
| 假设只落 Z6 不落 #16 | **14** | 98 | 7 |

⇒ 两条互不污染：**Z6 改的是 `7-no-fake-live` 的 `expect`**；**#15（R7 修复）改的是 `6-supervisor` 的 `expect`**。两张表、两个数字、两轮提交。**具体条数由实现者按其新增用例块数计算，本报告不签署任何未实测的条数。**
另需区分两件事，勿混：**新增一个 `check()`** 会改 `7-no-fake-live` 的块数（13→14，如 Z6）；**新增一个套件**不改它，只改聚合值（97→114，本轮已发生）——实测当前 `RESULT: CORE_CASES=7 EXTRA_CASES=6 TOTAL_CASES=13 exit=0` 未变。

## §12.10 两条判据裁决（本节为签署结论，后续不得再按旧版执行）

### §12.10.1 entry 上界：对 `intent` 必须在副作用前拒；对 `completion` **禁止事后 throw**

**事实**：`supervisor.mjs:68-75` 的 completion payload 是 `{cycleResultLabel: cycleResult.dataLabel}`，`cycleResult` 来自 `await d.cycleImpl(...)` ⇒ **其大小在副作用之前不可知**。故「上界必须在副作用前拒」只可能覆盖 `intent`，不覆盖 `completion`。我原定的单条规则过宽，此处更正为两条：

- **R-a（副作用前，采纳）**：intent 序列化 > 预算 ⇒ 在任何 append 与 `cycleImpl` **之前** `throw LEDGER_ENTRY_TOO_LARGE_BEFORE_INTENT`；用例必须断言 `cycleImpl` 调用数 === 0。
- **R-b′（副作用后，采纳）**：completion 超预算 **不抛异常**，改写为有界记录——
  `payload` → `{ payload_sha256, payload_prefix_b64(≤512B), payload_bytes, payload_truncated: true }`，
  仍写一条正常 `cycle_completion`，并另发一行结构化警告计数。理由：§12.2 推论 2 已定「副作用之后只允许无损记录或带哈希的有界截断」，**事后 throw 会违反本节自己的定理**——它会留一条看起来像 `FAILED` 的记录，从而**授权重跑**，正是 D-14-0 要防的双执行。保留 `payload_sha256` 使 ㉚ 的两源对账仍可按哈希 join。
- **R-b″（仅在"`dataLabel` 必须逐字保留"时可用）**：若确实不接受截断，则事后拒绝的状态**必须是 `IN_DOUBT`，不得是 `FAILED`**，且 `runNextCycle` 不得自动重试，只能人工裁定。

**裁决**：采用 **R-a + R-b′**。R-b（事后 `throw … AFTER_EFFECT` 并落 `cycle_failed`）**不予采纳**——`FAILED` 在语义上意味着"副作用未发生"，与事实相反。

### §12.10.2 头锚的尾部分支：**采纳优先于隔离**（我原写法有误，已推翻）

写序硬约束是 `append → fsync → 才更新 lastGood`，三者之间必然有窗口。窗口内崩溃时 `actualSize > byteLength`，而**尾部那条是已提交、已 fsync 的数据**。我原写「验前缀后隔离尾巴」= 把已提交条目移走，正是我们否决 `rename` 时定下的那条罪（"链合法但少一条，且静默"）。**该分支改为三支互斥**：

| 尾部形态 | 处置 | 变异用例（必须打红） |
|---|---|---|
| 尾部整行可解析 + 首条 `_prev === headHash` + 自 headHash 起整链验过 + 以换行结尾 | **采纳**，随后更新 lastGood（fsync） | 把合法尾部判成篡改 ⇒ 红 |
| 尾部可解析但 `_prev` 接不上 或 hash 不符 | `LEDGER_TAMPERED` | 改写尾部一条 ⇒ 红 |
| 尾部是半行（无结尾换行 / JSON 不完整） | 截断至最后一条合法行，**并追加一条 `torn_tail_truncated{bytes,at}`（fsync）**，使截断可审计 | 截断但不留记录 ⇒ 红 |

两条限定：① **采纳不是免检**——被采纳的尾部条目同样要过 §12.10.1 的体量约束与 §12.1-P3 的 intent↔outcome 配对不变量；② **静默截断一律禁止**：截断本身必须是账本里可见的事件，否则就是同一类"静默丢失"换了个位置。

**这不削弱"未授权追加可检出"**，因为该目标从来不存在：本机制从无外部头锚，有写权限者本就能追加合法条目（§12.4 已声明该威胁不在范围内）。锚保护前缀，链保护尾部内部一致性，**锚不保护"尾部有没有被追加过"**。

### §12.10.3 边界澄清：`workflowTriggerBranches` 只取**首个**匹配

`check-no-fake-live.mjs:220` 用 `findIndex` ⇒ 只解析第一个 `^  pull_request:\s*$` 块。故：
- **对 Z6 无影响**：Z6 的核心不变量是 T3 **文件级 token 扫描**（`self-hosted` 与 `pull_request`/`pull_request_target` 任一同现即 fail），重复块不构成绕过；
- **对 Y1/Y2 有影响**：它们的 `branches` 断言读的是首块，第二个块的 `branches` 不被检查；且 `pull_request_target` 至今**没有任何检查解析**。
⇒ 若日后扩充覆盖面，扩的是"多块/重复触发"与 `pull_request_target` 这一侧，**不是**行尾注释那一侧（后者已由 T4 覆盖）。

## §12.10.4 签署增补：用例清单、`STAGES` 标签表、两处实现条件（本轮审定）

### (a) 用例清单核对：21 条成立，但**第二条尾巴分支缺一条用例**

逐条核过 harbor 提交的 21 行枚举（`⑮, ⑯a, ⑯b′, ⑰, ⑱, ⑲, ⑲b, ⑲c, ⑳, ㉑, ㉒, ㉓, ㉔a, ㉔b, ㉕, ㉖, ㉗, ㉘, ㉙, ㉚, ㉛`），行数 21、`14+21=35` 算术一致，且每行都写明了"只让它变红的变异"——按我的规矩（说不出拦哪个变异就不该进清单）**合格**。

但**§12.10.2 的三支里，第二支没有用例**：⑳ 覆盖 `entryCount` 不符、⑱ 覆盖 append 遇撕裂尾部拒绝、⑲c 覆盖半行截断，而「尾部整行可解析但 `_prev` 接不上 / 自身 `_hash` 与内容不符 ⇒ `LEDGER_TAMPERED`」这一支没有对应块。⑱ 与 ⑳ 都不拦它。
⇒ **补一条 ⑲d**（变异：改写尾部一条的 `_prev` 使其接不上 headHash；期望 `LEDGER_TAMPERED`），总数 **22**，floor `6-supervisor` `14 → 36`。若实现者认为可合并，必须说明它被哪一条覆盖——**否则又是一个没人数的守卫**。

另注：`⑲` 的「以换行结尾」判据**成立且必须保留**。理由比格式整洁硬：`appendFileSync` 一次写 `canonicalize(entry)+'\n'`，**短写可以只丢末尾那个 `\n`**；缺 `\n` 恰是"这次 append 未完成"的可用信号。**完整 JSON 但缺结尾换行**必须落入 ⑲c（截断并记 `torn_tail_truncated`），**不得因为"能解析"就采纳**——提交点是"整缓冲写完 + fsync"，缺一字节即未达提交点。

### (b) `STAGES` 标签表（冻结，改动须同提交三处同步）

采纳 harbor 提案的 **10 个标签**，顺序如下：

```
killSwitch → lease → epoch → inDoubtGate → idempotency → resume → cycleIntent → cycleImpl → record → heartbeat
```

两条条件：
1. **每个标签必须对应一处真实 `trace.push` 且位置相符**；标签名不得承诺代码没做的事（`epoch` = `lease_epoch` 已 append 且已 fsync，不是"读了一下 epoch"；`cycleIntent` = intent 已 append+fsync；`record` = completion 已 append+fsync）。
2. **`STAGES` 不得被援引为顺序证据**（§12.9.1：声明对声明，对 D-14-0 零灵敏度）。它的作用是变更控制——让 reviewer 一眼 diff 出"声明改了但实现没改"，**不是**证明写入顺序。顺序的证明义务只在 ⑮（盘内读状态）。

> **更正（harbor 指出，我当场取证）——上面那张 10 标签表是"从未落成代码的提案"，不是历史状态。**
> 三行取证（本克隆 `D:\jev_worktrees\v2ops`，HEAD `daa9386`）：
> ```
> $ git grep -n "inDoubtGate" -- apps/ reports/
>   apps/…: 0 命中（仅本报告第 1553、2146 两行）
> $ git log --all --oneline -S'inDoubtGate' -- apps/
>   (空)
> $ git show <c>:apps/…/supervisor/supervisor.mjs | sed -n '/^export const STAGES/,/]);/p'
>   5834c37  'killSwitch' 'lease' 'idempotency' 'resume' 'cycleImpl' 'record' 'heartbeat'          → 7 段
>   5fe0b19  'killSwitch' 'lease' 'idempotency' 'resume' 'leaseEpoch' 'cycleIntent' … 'heartbeat' → 9 段
>   daa9386  同上                                                                                 → 9 段
> ```
> **代码里的阶段集历史只有 7 → 9 两个状态。`inDoubtGate` 从未被写过，`epoch` 这个短名也从未存在（实现里叫 `leaseEpoch`）。**
> 因此第 1553 行那张表必须理解为"harbor 当时的**提案**（含两个后来被否的标签）"，**不得**理解为"我们删掉了一个曾经存在过的守卫"。二者后果完全不同：前者是设计取舍，后者会让人怀疑"是不是还有别的东西被删了"。
>
> **记账（我的第四类同类错误，与前三类同一病根）**：前三次是"把**我以为**的状态当成**有过**的状态"（从状态推意图 / 从日志行推实跑者 / 从工作区推提交字节）；这次是"把**别人的口头提案**当成**代码历史**"。前三次我错在**观测对象**，这次错在**时间维度**。统一规矩：
> **凡写"从 X 改成 Y"，Y 与 X 都必须各自绑定一次可复现的读取；只有一个端点是实测的，就只能写"当前是 Y"，不得写"从 X 改成 Y"。**


### (c) `cycle_intent` 携带 `maxPayloadBytes`：采纳，**但必须校验而非信任**

携带的理由成立（否则 R-a 与 R-b′ 各写一个 4096 魔数，改一处必漏一处）。但**自述的预算不是预算**：若读取方直接信任 intent 里的值，一个有写权限者（或旧版本 writer）写入 `maxPayloadBytes: 10^9` 即自我豁免。故：
- 读取时 `intent.maxPayloadBytes` 必须 == 当前配置常量；**不等即拒绝**（`LEDGER_BUDGET_MISMATCH`），不做"取较大者"之类的宽容处理；
- 该字段随 intent 一起进链哈希（不可事后改）。

### (d) `pull_request_target` 的优先级含义（同意，并补一个 Z6 覆盖不到的面）

"未解析"比"多块"更严重这一点成立。但要说准它属于哪一类风险：Z6 的 T3 已按 **token 级**覆盖「prt + self-hosted 同文件」；**未被覆盖的是另一类**——`pull_request_target` 在工作流里 checkout PR head（如 `actions/checkout` 带 `ref: ${{ github.event.pull_request.head.sha }}`）时，即使用托管 runner 也是任意代码执行（secret 在该事件的上下文里可达）。
这一条**不在 Z6 的不变量内**，登记为候选 **Z8**（"`pull_request_target` 不得与 checkout PR head 同时出现"），是否入册由 team-lead 决定；我不擅自扩 Z6 的语义。

## §12.10.5 二次签署：`⑯c` 采纳；**我 (c) 那条规则本身写错了，改为不等式**

### (a) `⑯c` 独立成块：23 条，`6-supervisor` `14 → 37`

harbor 用我的清单纪律反问：我在 §12.10.4(c) 加的那条读侧校验，本身没有用例。**成立**。且它与 `⑯a` **不同源**：`⑯a` 的变异在**写侧**（把上界检查挪进 `recordCompletion`）；读侧的变异是"读取时信任 `intent.maxPayloadBytes` 而不与常量比对"——植入一条 `maxPayloadBytes: 10^9` 的 intent，写侧一切正常、`⑯a` 仍绿，只有读侧这条会红。
⇒ 采纳 **`⑯c LEDGER_BUDGET_MISMATCH`**，总数 **23**，floor `14 → 37`。

### (b) 但 `maxPayloadBytes` 的判定规则**必须是不等式，不是等式** —— 我的原写法会自锁

我上一轮写的是"读取时必须 **==** 当前配置常量，不等即拒"。**这条会把账本在配置变更时锁死**：某日把预算从 4096 调到 8192，所有写于旧配置的 intent 都变成"不等" ⇒ 全量拒绝 ⇒ 账本不可读、不可续跑 —— 一次正常运维动作变成自伤 DoS。这与"append-only 账本必须跨重启存活"直接冲突。

改为：
1. **写侧是唯一裁决点**：条目是否可接受，由写入时对**当时的常量**判定；读路径**不重新裁决体量**（只校验结构 / `_prev` / `_hash` / 配对不变量）。
2. **读侧的硬线是上界而非等号**：`intent.maxPayloadBytes <= CURRENT_MAX`。**大于** ⇒ 自我豁免企图 ⇒ 拒（`LEDGER_BUDGET_MISMATCH`）；**小于或等于** ⇒ 合法历史值 ⇒ 接受，并作为审计字段保留。
3. 配置**调小**时：读路径**不得**因为旧条目超出新常量而拒绝（它们在写入时合法）；只记 `budget_exceeded_historical` 供审计，新写入按新常量约束。

这样既杀掉"写 `10^9` 自我豁免"，又不会因常量变化把账本变成砖。

**`⑯c` 因此必须是双向用例**（单向"一律拒绝"也能骗过一行断言）：
- 植入 `maxPayloadBytes: 10^9` ⇒ 必须拒（红）；
- 植入 `maxPayloadBytes: 2048`（小于常量）⇒ 必须**接受**（若此断言缺失，一个"总是拒绝"的实现会通过）。
反向断言不写，这条就只是半个守卫——与"只给正例等于没证"是同一条纪律。

### (c) 进程级 blocker：两个未提交写者共用一个文件（**与账本缺围栏是同一类缺陷**）

`#15`（`6-supervisor 14→37`）与 `#16`（`8-api-body-read expect:17`）都要改 `apps/command_center_3d/scripts/gate-assertion-floor.mjs`，而**双方都没有 commit 权限**、文件处于未提交状态。后果是 last-writer-wins：后保存者抹掉对方的 `SUITES` 条目，**而 `floorSlugs` 只比数量（§12.9.3），正好盖住这类丢失**。
⇒ harbor 拒绝进入 `apps/**` 是**正确处置**，不是过度谨慎。此事的形态与我们在账本里修的完全同构：**两个写者 + 没有提交点（fence）= 静默丢更新**。区别是账本丢了会让 `CHAIN_BROKEN` 响，而这里是哑的。
⇒ 必须有**单一提交点**：指定一名提交人按序落两笔，或先把 `floorSlugs` 修成 Set 相等再改表。**在提交点确定前，任何一方改动该文件都应视为未生效。**

### (d) Z8 的优先级（两方意见一致，决定权在 team-lead）

harbor 主张 Z8 优先于 Z6 的"多块覆盖"：Z6 拦的是**配置组合**，Z8 拦的是**任意代码在有 secret 的上下文里执行**。我同意这个排序——前者是卫生，后者是攻击面。但**是否入册仍由 team-lead 定**，我不代签。

## §12.10.6 三次签署：预算判据**彻底换基础**（不得以自述字段为许可）、`⑯c` 拆三块、25 条 / floor 39

### (a) 我上一版的两条规则互斥（harbor 实测，我复核成立）

我写了两条：规则 2「`intent.maxPayloadBytes > CURRENT_MAX` ⇒ 拒」、规则 3「配置调小时不得拒旧条目」。**同一批条目两种裁决。** harbor 的 `filter()` 返回值直接打穿：写入时合法（常量 8192）的三条 intent，在 `CURRENT_MAX` 调到 4096 后，**等式与不等式都得到"接受=0 / 拒绝=3"**，即账本照样变砖。规则 3 说的"不拒"在规则 2 下无法实现。**这是我的错，两条规则必须删掉一条再重建。**

### (b) harbor 提出的替代式在两种读法下分别是**恒真**与**自锁**（我跑了）

他给的是 `reject ⇔ budget > max(CURRENT_MAX, maxSeenBudgetInLedger)`。实测（`hist=[8192,8192,8192]`，伪造追加 `1e9`）：

```
读法A（maxSeen 含被检条目自身）  历史@4096: [false,false,false]   伪造@4096: [false,false,false,false]
读法B（maxSeen 只含此前条目）    历史@4096: [true,false,false]    伪造@4096: [true,false,false,true]
```
- **读法 A 恒真**：`maxSeen ≥ 任何条目的 budget`（它包含自己），故 `budget > max(CURRENT_MAX, maxSeen)` **永不成立**——连 `1e9` 都放行。这是"拦不住任何东西的检查"，正是本轮在杀的形态。
- **读法 B 自锁**：最早那条 `8192` 的 prior 最大值为 0 ⇒ `8192 > 4096` ⇒ 拒 ⇒ **砖**。

⇒ 两种读法都不可用。根因不是公式写错，而是**用一个"随账本变化的量"去裁决账本自身**，必然落到恒真或自锁之一。

### (c) 裁决：读侧门槛改判**实际字节**对**编译期常量**，自述字段只做一致性校验

**新规则（三条，互不冲突）：**
1. **`maxPayloadBytes` 永不作为许可依据**，只作为**审计 + 一致性**字段。
2. **读侧硬线是实际字节**：`actual_bytes(entry) > ABSOLUTE_MAX`（**编译期常量，不可配置**）⇒ 拒 `LEDGER_ENTRY_TOO_LARGE_ON_READ`。因为它是常量，**不存在"配置变更导致自锁"**；因为看的是实际字节，**不存在"自述豁免"**。
3. **一致性校验**：`declared < actual_bytes` ⇒ 拒 `LEDGER_DECLARATION_INCONSISTENT`（这条抓的是"少报"——自我豁免的真实路径不是报大，而是**少报**后写大）。

**可推广的规则（写进本节，供后续所有"上界/预算"类设计引用）：**
> **自述的限制不得作为许可依据，只能作为一致性校验。**
> 任何 `X.limit` 若被读方用来"允许"某个行为，攻击者写一个大 `X.limit` 即自我豁免；若被用来"拒绝"，则一次合法配置变更就把历史数据判为非法（自锁）。唯一安全的位置是：**许可由不可变常量或实际字节决定，自述值只与事实比对**。

### (d) `⑯c` 拆三块：25 条，`6-supervisor` `14 → 39`

harbor 说"双向是 2 个方向，这条规则有 3 个"，方法论对；但他提出的三块建立在 (b) 那个式子上，故三块的**判据要换基础**，条数不变：

| 块 | 断言 | 只让它变红的变异 |
|---|---|---|
| `⑯c₁` | 实际字节 > `ABSOLUTE_MAX` ⇒ 拒 `LEDGER_ENTRY_TOO_LARGE_ON_READ` | 删读侧实际字节门槛 |
| `⑯c₂` | `declared < actual_bytes` ⇒ 拒 `LEDGER_DECLARATION_INCONSISTENT`；`declared == actual`（或 ≥）⇒ 接受 | 删一致性校验 / 改成信任 declared |
| `⑯c₃` | 常量 8192 写成的历史条目，在 `CURRENT_MAX` 调到 4096 后**仍可续跑**（不砖） | 把任何 `> CURRENT_MAX` 或 `== CURRENT_MAX` 的比较加回读路径 |

`⑯c₃` 是**反向回归守卫**：它的变异不是"删代码"而是"**加回**那条看起来合理的比较"。这类"防未来的自己"的用例必须存在，否则 §12.10.6(a) 那个自锁会在下一次重构里无声复活。
合计：原 23 条中 `⑯c` 由 1 块拆为 3 块 ⇒ **25 条，`14 → 39`**（`14+25=39`）。

### (e) 采纳 harbor 的两条实现细化
- 字段名用 **`discarded_bytes`** 而非 `bytes`：`torn_tail_truncated{bytes:…}` 会被误读成"剩余长度"，他自己第一眼就误读了。**自解释字段名能省掉下一次误读**——这是可验收性的一部分，不是风格。
- 证伪第 ④ 步"还原"必须给**变异前 / 还原后两次 `git hash-object`** 回显。理由：本轮已有一次"复核动作改写了证据文件"（§12.7），只靠"我改回去了"不可证。

### (f) `ABSOLUTE_MAX` 定值 4096 —— **并且必须同时钉死"写侧不得越过它"**

我上一版只说"编译期常量、不可配置"，没给数。harbor 问、我补：**`ABSOLUTE_MAX = 4096`**，与写侧 entry 上界同源，避免两处魔数。

但只给数会**原样复现 (a) 的自锁**：`CURRENT_MAX` 是**可配置的**，只要它曾被设为 8192，那批 8192 的合法条目在 `ABSOLUTE_MAX=4096` 下全部被判 `LEDGER_ENTRY_TOO_LARGE_ON_READ` ⇒ 砖。所以常量本身不是安全条件，**必须加一条不变式**：

> **`CURRENT_MAX <= ABSOLUTE_MAX` 在配置装载时强制（clamp 或直接 fail-closed），不是文档约定。**

有这条，"历史条目 ≤ `ABSOLUTE_MAX`"就**由构造保证**，读侧永远不必接受一个写侧从没允许过的值之外的东西，也就没有自锁。没有这条，读侧就在用一个**比写侧更严的门槛**去判历史 —— 这正是 (a) 那个坑换了张皮。
落地：`⑯c₁` 的前置断言里加 `assert(CURRENT_MAX <= ABSOLUTE_MAX)`。它**不是第二条事实**，而是同一条事实的另一半：**读侧门槛必须 ≥ 写侧允许上限**，否则任何门槛都会重新变成自锁。（R-23"一条用例只证明一件事"在这里允许前置断言，因为前置断言证明的是"被测前提成立"，不是第二件被证的事。）

**补充（team-lead 签署时要求，我加一条实现约束）**：clamp 事件**本身必须有断言**，否则"打了一行日志"仍然只是文档约定。而且——
> **断言不得读日志行。** 读日志文本 = 声明对声明（日志可以被改、被注释、被 gate 的 `stripComments` 吃掉）；断言必须读**返回值或计数器**：`currentMax()` 返回 `{ requested, applied, clamped }`，用例断言 `clamped === true && applied === ABSOLUTE_MAX`。这与 `createMemoryFs` 的 `fsyncCallCount()` 同一形态：**可观测的状态，不是可读的叙述。**

---

## §12.10.7 独立复核 `4859e5c`（#16 `BODY-READ-UNBOUNDED`）：关闭 P1，但**用例 13/14 对"转发原始字节"零灵敏度**

**证据绑定状态**：`HEAD = 4859e5c`。复核**开始时**工作树脏文件仅本文件与 `artifacts/release/p0_p2_acceptance/P0_P2_ACCEPTANCE_REPORT.md`；**复核过程中树变了**（同工作区有人在实现 #15）：`gate-assertion-floor.mjs`（`6-supervisor` `14→15`）、`supervisor/core.mjs`（+54）、`supervisor.test.mjs`（+19）转为未提交修改，且**本文件被他人 `git add` 进暂存区**（`git diff --cached --stat` = 本文件 115 行）。因此本节 (a)–(k) 的读数**只对 `4859e5c` 的已提交字节成立**；§12.10.7.1 的三条读数针对的是**未提交的工作树**，按"未生效"处理。~~**任何人现在提交这棵树，会把安全报告与一份未完成的 #15 实现放进同一个提交** —— 这是 provenance 事故，不是风格问题。~~
> **【更正 · 见 §12.10.11(a)】** 上句"provenance 事故"的判断**不成立**：那 115 行是 team-lead `add` 后 `commit` 因 `index.lock` 被占失败留下的**中间态**，重试后由 `182e717` 消费，**未发生混合提交**。我把"暂存区有内容"推成了"有人要混合提交"——**风险可以推断，意图不能断言**。原文保留，不覆盖。

### (a) 冻结树未动（先证这一条，否则后面都不用看）

```
git ls-tree -r HEAD | 过滤 apps/command_center_3d/server/engine/  → 15 个文件
"\n".join(lines) utf-8 → sha256 = aec5d1b7cc26db50736d355e7ef14e066fae3f2c2a376d94637c9a977008a9ad  MATCH
git show --name-only 4859e5c | grep -c "server/engine/"  → 0
```
`server/lib/http-body.mjs` 在 `server/lib/`，**不在** `ENGINE_REL` 内 ⇒ 不触 `check3`、不触 §12.0 的三种摘要约定。**VERIFIED。**

### (b) 三个原始缺陷：我独立给"有牙"证据（不引 harbor 的自述）

我在临时目录对**副本**施加四个变异（仓库零改动），只跑该套件声称守护的四条性质：

```
mutant applied : {"m-close":true,"m-cap":true,"m-perchunk":true,"m-declared":true}
BASELINE       : {"fidelity":true,"cap":true,"abort":"BODY_ABORTED","declared":"BODY_TOO_LARGE/no-read"}
m-close        : {"fidelity":true,"cap":true,"abort":"HANG",         ...}   ← 删 close 监听 ⇒ 挂死，用例7 变红
m-cap          : {"fidelity":true,"cap":false, ...}                        ← 关掉 total>maxBytes ⇒ 接受，用例5/6 变红
m-perchunk     : {"fidelity":false, ...}                                   ← 逐 chunk 解码 ⇒ 字节不等，用例2/3 变红
m-declared     : {..., "declared":"accepted"}                              ← 删 declared 预检 ⇒ 先读后判，用例11 变红
```
四条性质**各自唯一对应的变异都能让它变红**。这套件的核心断言有牙，**VERIFIED**（我自己的变异，不是他的）。

### (c) 我自己提过一个"崩进程"假设，**实测不成立，撤回**

`readNodeBodyBytes` 在超限 reject 时 `cleanup()` 摘掉 `data/end/error/aborted/close` **全部**监听器，并留下一个 pause 未读的请求流。我怀疑：若调用方不 drain 就应答，客户端随后 RST 会打出**未捕获 `req 'error'`** ⇒ 进程被杀（pre-auth 远程 DoS）。实测两种调用形态：

```
mode=drain   (production: sendBodyError -> drainThenRespond) crashed=no exit=0
mode=nodrain (应答但不 drain)                                 crashed=no exit=0
```
两次 RST 攻击、`uncaughtException` 与 `unhandledRejection` 双钩子均未触发。**假设证伪，不写成缺陷。** 但保留一条**契约性提醒**：该模块把"读完/拒完之后流由谁持有"的责任交给了调用方，`sendBodyError` 的 drain 是**唯一**让这形态安全的东西；任何人把 `sendBodyError` 改成"直接应答"就失去了它，而现有 17 条用例里**没有**一条会在那种情况下变红（用例 15 只匹配 `drainThenRespond(req, () => res.end(payload))` 这个**文本形状**，删掉它确实会红——但把 drain 换成"另一个也满足正则的写法"不会）。按 R19 已知缺口记账，不升 blocker。

### (d) 新发现（P2，判据灵敏度）：用例 13/14 断言的是**实参列表**，不是**转发事实**

`http-body-read.mjs:277` 的断言是 `/bodyText,\s*bodyBytes\s*\)/` —— 它只看调用点写了这两个参数。我把 `buildEngineRequest` **内部**的 body 选择改回文本（即把本次修复的效果整体回退），四条断言**全真**：

```
ORIGINAL  case13 = {"readNodeBodyBytes":true,"noConcat":true,"noStringAcc":true,"forwards":true}
MUTANT(把 body 选择改回 (bodyText || "{}"))  case13 = {"readNodeBodyBytes":true,"noConcat":true,"noStringAcc":true,"forwards":true}
--- mutant callee body ---
  const body = !hasBody ? undefined : (bodyText || "{}");
```
即：**"facade 把原始字节交给了引擎"这件事，整套 17 条用例都没有观测**（用例 16 观测的是 `readNodeBodyBytes` 的返回值，不是 `buildEngineRequest` 产出的 `Request` 的字节）。这与我此前判 `trace` 索引断言 0/3 是**同型**：断言的两个操作数都来自源码文本 ⇒ 声明对声明。

**影响面必须诚实收窄**：合法 UTF-8 的 `string` 往返字节相同，所以该变异只在**非法 UTF-8 体**上改变字节；那是"签名永远对不上"的老缺陷换了个位置复活，不是新的越权。**因此定 P2，不定 P1。**
**最小整改**（一条即可）：`buildEngineRequest` 导出后断言 `Buffer.from(await preq.arrayBuffer()).equals(inputBytes)`；变异 = 上面这次回退。**在改好之前，用例 13/14 的 `forwards` 断言不得被任何签署引用为"原始字节已转发"。**

### (e) `floorSlugs` 门禁：我原来那条 P1 被这次提交关掉了，**但关闭的形状与提交说明不同**

八行变异矩阵（脚本在仓库外，逐字复制新检查的断言逻辑；旧检查 = 只数 `slug:` 行）：

```
M0  baseline                                            OLD GREEN       NEW GREEN
M1  gate 增 1、floor 不变                                OLD RED         NEW RED      ← 我第一版设错的反例
M1a floor+GATE_SUITES 同时删 1-p0-contracts、加 foo        OLD GREEN       NEW GREEN    ← 唯一"计数相等"的真读法
M2  某个 label 不再命名 .mjs                              OLD GREEN       NEW RED
M3  slug 改成与另一条重名（整条套件丢 floor 的真路径）        OLD GREEN       NEW RED      ← 新增检查的实际价值
M4  两处 label 互换（slug→log 映射不变）                   OLD GREEN       NEW GREEN
M5  slug 与 ci.yml tee 同时改名                           OLD GREEN       NEW GREEN
```
裁定：
1. **新检查严格更强**：`M2`、`M3` 是旧检查**绿**而新检查**红**，其中 `M3`（slug 重名 ⇒ 某条 `*.log` 再无人读）正是"整条套件静默失去 floor"的真实路径。**我原来是 P1 的这一条，CLOSED。**
2. **但它不是**提交说明里"add-without-register 闭环"：`M1a` 协调删除 floor + `GATE_SUITES` + ci.yml 三处，**两种检查都绿**。**没有任何仓内清单能锚定自己**（递归），所以这类检查只能叫"漂移检测"，覆盖保全必须靠报告侧签署清单（`1-p0-contracts … 8-api-body-read` 八条 basename，任何增删须在此留痕）。
3. `M4`/`M5` 绿而无害：`label` 是**自由文本**，全仓唯一用途是 `gate-assertion-floor.mjs:229` 的一行日志；被新检查当作身份键的是它，而**真正决定"读哪个 log"的是 `slug`**（`join(LOG_DIR, s.slug + ".log")`）。**身份键选错了对象，只是当前两者恰好一致。**
 **登记 P3-IDKEY**：正确形态是用 `slug` 去对 `ci.yml` 里 `tee "$GATE_LOG_DIR/<slug>.log"` 的 **basename 集合**，而不是用 `label:` 里的 `.mjs`。现状严格更强、先收下，不回退；但**报告里不得写成"身份由 label 决定"**——那是把实现细节说成语义。（此条由 harbor 反向提出，我复核成立。）

### (f) 新检查**自身可被静默删除**（同一个 R19 缺口，递归一次）

它整块落在 `Y2` 内部，而 `Y2` 的 `expect` 没动。我实跑确认：

```
node apps/command_center_3d/scripts/check-no-fake-live.mjs
→ RESULT: CORE_CASES=7 EXTRA_CASES=6 TOTAL_CASES=13 exit=0
```
13 未变 ⇒ 删掉这 30 行，`Y2` 仍绿、floor 仍 13。这正是该文件自述的"删掉 case 内部断言不会被这里抓住"。
**最小整改**：升为独立命名检查 `Y6`，`7-no-fake-live` 的 `expect` `13 → 14`。
**与 Z6 冲突（重要）**：`13 → 14` 正是 `Z6` 要用的**同一个槽位**。两者必须**同一次提交**落地，否则又是一次 `R-26` 式的 last-writer-wins。**这条已转给 team-lead 排程。**

### (g) `R-26` 状态变更：从 blocker 降为流程约束，**#15 的提交阻塞解除**

`gate-assertion-floor.mjs` 现在**有提交点了**（`4859e5c` 的父 → 本提交已含 8 号套件）。且 floor 是**双向精确等式**（`:219/:221`）：`#15` 把 `6-supervisor` 改到 39 之后若被别人的提交覆盖回 14，套件实报 39 ⇒ `reason=count-grew-update-the-floor-table` **变红**。
⇒ `R-26` 的前提"丢失是哑的（被 `floorSlugs` 数量洞盖住）"**已不成立**：数量洞已关、丢失会响。**仍需单一提交人**（并发同文件编辑仍会冲突），但**失败模式由哑变响**。#15 可以在此 HEAD 之上落地。

### (h) 我背书的一个正面点：`declared content-length` **只用于拒绝，从不用于放行**

`readFetchBody` 的 stage-1（`:248`）拿 `content-length` 做**拒绝**，stage-2（`:265`）拿**实际字节**做拒绝，全模块**没有一处**用自述值放行。这与 §12.10.6(c) 的"自述的限制不得作为许可依据"同向，且因为拒绝侧用的是"声明值 > 常量"，**配置调小不会自锁**（content-length 是每请求事实，不是历史条目）。**这一族判据我签字。**

### (i) 残留三条 P2（不阻塞 #16 关闭，但必须进 backlog，不得静默）

| # | 事实 | 为什么不是 P1 |
|---|---|---|
| R-a | node 侧**没有** declared-length 预检（`readNodeBodyBytes` 全文不读 `content-length`），与 fetch 侧**不对称**：声明 1 GB、每分钟送 1 字节，可占住连接 | 内存仍 O(maxBytes+1 chunk)，且 `requestTimeout=300000` 是唯一兜底（已在册）。属"连接时长"不属"内存无界" |
| R-b | 空体 POST 被**伪造成 `"{}"`**（`bodyBytes.length ? bytes : (bodyText || "{}")`），facade 在信任边界上**改写**请求体 | 修改前就是 `bodyText \|\| "{}"`，非本次回归；且 `{}` 不改语义方向 |
| R-c | 用例 17 走的是**内联复制**的生产顺序，不是导出的 `sendBodyError`；"生产函数用的就是这个顺序"只靠用例 15 的正则绑定 | 正则 + 复刻两者叠加，重构时至少一者会红 |

### (j) DECISION 增量（C19–C23）

- **C19** `BODY-READ-UNBOUNDED`（P1）→ **CLOSED**。依据：(b) 四变异有牙 + (a) 冻结树 `aec5d1b7…` MATCH + 提交内 `server/engine/` 命中 0。`ai-core.mjs` **零字节改动**（冻结）。
- **C20** 用例 13/14 的 `forwards` 断言**不得**被援引为"原始字节已转发"；以导出后字节相等用例为准（(d)）。**在整改前，`4859e5c` 只能表述为"读侧修复完成"，不能表述为"端到端字节保真已验收"。**
- **C21** `floorSlugs` 升级**只**算漂移检测。报告侧冻结八条 basename 清单；任何增删必须在 §12.10.7(e) 留痕，否则视为未经签署的覆盖变更。
- **C22** `7-no-fake-live` `13 → 14`（Y6）与 `Z6` 同槽位 ⇒ 必须同一次提交；`#15` 的 `6-supervisor 14 → 39` 可在此 HEAD 之上独立落地（互不改同一行）。
- **C23** 我收窄一条自己的口径：**"提交说明里的因果解释"与"检查实际拦住的变异"必须分别签署**。(e) 里 `M1a` 就是例子——说明写的是"关闭 add-without-register"，实际关闭的是 `M2`/`M3`。**签署只认变异矩阵，不认说明文字。**

### (k) 本轮我自己的错误记账（第三次同型，写下来防复发）

第一版变异 `M1` 我把 harbor 描述的场景误设成"gate 增 1、floor 不变"，实测旧检查 **RED** —— 也就是说我那个反例**根本不成立**。改正为 `M1a` 才得到真形态。
**教训**：反驳别人的"洞"之前，先证明这个洞在**我的变异**下真的存在；否则我用一个不成立的变异"证明"了一个不存在的差别，而对方会因此正确地不采信我的整段结论。
与前两次（"上界必须在副作用前拒"过宽、"等式判据"会自锁）共同的根因是**只验证规则能不能拦住别人，不验证规则本身的前提是否成立**。本轮把它写进清单：**变异必须先自证可达。**

---

## §12.10.7.1 工作树读数（未提交，按"未生效"处理）：⑮ 是真 P1 级 fail-open，但 fixture 保真仍差两处

> 这三条针对的是**未提交的工作树**（`core.mjs` +54、`supervisor.test.mjs` +19、floor `6-supervisor 14→15`）。按 §12.9 的纪律，**未提交即未生效**，此处只作评审输入，不作签署依据。

### (a) `⑮ verifyChain` 非数组 ⇒ 静默通过：我在**已提交字节**上复现成立

`git show HEAD:…/supervisor/core.mjs` 导出到临时文件后直接调用：

```
HEAD(4859e5c) verifyChain(wrapper)   = {"intact":true,"head":"GENESIS"}
HEAD(4859e5c) verifyChain(null)      = threw: Cannot read properties of null (reading 'length')
HEAD(4859e5c) verifyChain(undefined) = （同上，TypeError）
```
**一个完整性校验器对"它一个字都没看过的输入"回答 `intact:true`。** 这不是"少一条用例"，是**恒通过的控制**——`0 < undefined` 为假 ⇒ 循环零次 ⇒ 返回 GENESIS 头。与 §12.10.6 的 `maxPayloadBytes` 恒真是同一族。
⑮ 的修法（`Array.isArray` 前置 + `CHAIN_INPUT_NOT_ARRAY`）**方向正确，我签字**；且 `null` 从"TypeError 崩"变成"抛具名错误"也是净改善。
**但计价必须准确**：`floor 14→15` 只落地了 **25 条里的第 1 条**。按 C21，**不得**把这棵树读作"#15 的 25 条已落地"。`verifyChain` 的 fail-open 是 **P1 级**（完整性判据恒真），单独就够撑一个 blocker 条目，直到 ⑮ 提交。

### (b) fixture 的 `writeSync` 现在**模型不对**：没有"文件位置"

`core.mjs` 的注释把根因写对了（`open('a')` 被忽略），但新的实现把 `writeSync` 变成了"整文件覆盖或整文件追加"，**没有位置概念**。真实 `fs.writeSync(fd, buf)` 从当前偏移写并推进偏移，对同一个 fd 连续两次写是**两次追加**。实测同一序列：

```
fixture: openSync(p,'w'); writeSync('{"seq":1}\n'); writeSync('{"seq":2}\n')
   → FIXTURE file = "{\"seq\":2}\n"
   → REAL FS file = "{\"seq\":1}\n{\"seq\":2}\n"
```
即：**任何"一条记录分两次 syscall 写"的用例在这个 fixture 上都绿得没有意义**——正是新注释里那句"A durability test written on that fixture is green for the wrong reason"，只是换到了 `'w'` 这一侧。
**最小整改**：每个 fd 记 `{ key, append, pos }`；`writeSync` 按 `pos` 拼接并推进；`openSync` 在 `'w'/'wx'` 时清空并 `pos=0`、`'a'` 时 `pos=len`；`truncateSync` 后把 `pos` 夹到新长度。

### (c) fixture **无法表示撕裂在中途的半字节** ⇒ 直接阻塞 ⑲c/⑲d

`files` 存的是 **string**，写入时 `asText()` 就 `toString('utf8')`。写 1 个字节（`中` = `e4 b8 ad` 的首字节）：

```
fixture: writeSync(full.subarray(0,1))  → bytes = ef bf bd  len = 3
real fs: 同一调用                        → bytes = e4        len = 1
```
⇒ `truncateSync` 注释里的"byte-accurate"**只在已经损坏的字符串上成立**；`lastGood.byteLength` 这类按字节切点的判据会算在**错的字节数**上。**#15 的 `⑲c`（完整 JSON 但缺 `\n`）与 `⑲d`（接不上）都依赖"撕裂点"。**
**最小整改**：`files` 改存 `Buffer`；`readFileSync(p, enc)` 只在读出时解码；`appendFileSync`/`writeSync` 按字节拼接；`truncateSync` 按字节切；`statSync` 返回真实字节数。
**顺序要求（沿用 §12.9）**：fixture 保真修复**必须先于**任何写入路径用例落地，否则那批用例的证据是"绿在错的模型上"。

---

## §12.10.8 恒真式登记表 + 机械判据（本轮第四次同类后的处置，team-lead ⑥ 采纳）

### (a) 为什么需要"登记表"而不是"更仔细"

本轮恒真式已经不是个别疏忽，而是**这个流程的自然产物**：

| # | 恒真式 | 谁写出 | 被什么抓到 |
|---|---|---|---|
| T1 | `R2_DUPLICATE_REJECTED`（去重键取自**已写入的** completion ⇒ 永不触发） | 规格 | 8 trial 实测 4/8 `trials_with_duplicate_effect` |
| T2 | `trace.index(cycleIntent) < trace.index(cycleImpl)`（两个操作数都来自源码位置） | 清单 | 四场景实测拦截率 **0/3** |
| T3 | `reject ⇔ budget > max(CURRENT_MAX, maxSeen)` 的**读法A**（`maxSeen` 含自身） | harbor | 我逐条代入：连 `1e9` 都放行（§12.10.6(b)） |
| T4 | `assert(S.verifyChain.length >= 0 ? 'guard-present' : 'x', 'guard-present')`（`Function.length` 恒 ≥0） | harbor（自陈，写后自删） | 自查 |

T4 的价值最高：它是在 team-lead 刚要求"每条断言配取反变异"之后**十分钟内**被写出来的。**⇒ 恒真式不是"没查"，是"查的时候也在产出"。**

### (b) 机械判据（可进 gate，不依赖自律）

> **对每条断言，构造"把它的条件取反"的最小变异。若取反后仍然通过，该断言是恒真式。**

真断言必然对取反敏感；恒真式对取反不敏感。这条判据的性质与 T1–T4 全部相容：
- T1 取反 = 让去重键改用"即将写入的 payload" ⇒ 用例必须由绿转红；不红即恒真；
- T2 取反 = 把两个索引之一换成常量 ⇒ 必须转红；
- T3 取反 = 把 `>` 换成 `<=` ⇒ 已实测两种读法都"接受=0"，**注意：这条同时说明"取反后不变红"也可能因为两个方向都错**，所以取反判据只能证伪"恒真"，不能证成"正确"；
- T4 取反 = `length >= 0` → `length < 0` ⇒ 断言恒红，**恒红也不合格**（见 (d)）。

### (c) 落地形态：`case → 变异编号` 的登记，缺编号即红

`7-no-fake-live`（或新 `Y7`）只需要求"每条 case 报告它对应的变异编号"，**缺编号即红**。这把不可验证的自律换成可验证的登记。**注意它自身的槽位问题**：这条守卫**必须占自己的槽位**（见 §12.10.7(f) 的 Y6 同款要求），不能寄生在 Y2 里，否则删掉它仍绿。

### (d) 取反判据的两个边界（必须写清，否则会被当成万能钥匙）

1. **只能证伪"恒真"，不能证成"正确"**。取反后变红，只说明断言对这一个方向敏感；`<=` / `>` 两方向都错时，取反照样"变红"。**所以取反判据必须与"变异矩阵每行只让一条用例变红"叠加使用**（§12.9 的灵敏度纪律）。
2. **恒红同样不合格**。`assert(false)` 会让每条用例都红，形式上"对取反敏感"。合格条件是：**正例绿 + 取反红 + 只红该条**。三者缺一即登记为缺陷。

### (e) 签署前的自陈规则（采纳 harbor 的建议，我加一条限制）

> 任何一次签署前，签署人必须自己指出**至少一条"我原本想写但会恒真、所以删掉"的断言**——说不出就说明他没在找。

**限制**：这条是"过程证据"，**不能替代 (b) 的机械判据**，也**不得**被当作"已做恒真检测"的证明——自陈可以被伪造，取反变异不行。两者关系是：自陈提高发现率，机械判据兜底。

### (f) 本轮我自己的 T5（按要求留痕，不删）

我在 §12.10.6(c) 第一版写的是"`maxPayloadBytes` **等于** `CURRENT_MAX` 才接受"——那不是恒真，是**自锁**（一次正常调常量就把历史判非法）。恒真与自锁是同一个错误的两个方向：**恒真=拦不住别人，自锁=伤到自己**。§12.10.6(a)→(c) 的演进必须留在报告里，理由与 `⑯c₃` 相同：**让下一个读的人知道这里有坑，而不是看到一个写得很漂亮的断言。**

---

## §12.10.9 两条方法学入册（本轮由 harbor / harbor2 各提供一条，我复核后签署）

### (a) 有副作用的验证器：**重实现它的算法，不要 import 它**

`scripts/v16_independent_verify.py` 一被 import 就重写 `reports/evidence/v16/independent_verify.json`（`root` 变成本机路径、`generatedAt` 变、`v16_changed_files` 全量重建）。我踩过一次，靠 `git show HEAD:… > …` 恢复。
harbor2 绕开的方式是**用 bash 重实现 check3**：

```
L=$(git ls-tree -r HEAD -- apps/command_center_3d/server/engine/ | sed '/^[[:space:]]*$/d')
printf '%s' "$L" | sha256sum  → aec5d1b7…a9ad
```
我自己用 node 重实现（`ls-tree` 原始行 `"\n".join` 无尾换行 → utf-8 sha256）得**同一结果**。
⇒ **两个独立实现、同一读数 = 这一条可以按 VERIFIED 引用**（不是"我算过"，是"两种算法给出同一个值"）。
⇒ 入册规则：**要复算一个验证器的判据，重实现它，比 import 它安全。** import 一个会写回仓库的验证器，等于让复核动作成为被复核对象的一部分。

### (b) "三行不是三条守护"（harbor2）

`ENGINE_TREE_SHA256` 被 CI 强制（`check3` 在 `v16_independent_verify.py`，5 个 workflow 里被执行）；`git_blob_sha` 只在 `cloud-entry.mjs` 启动路径被用；`ENGINE_ANCHORS` 只在 collector 本地测试。**后两者在 `.github/workflows/` 命中 0。**
⇒ "三个摘要都算对了"极易被读成"三条路都有守护"。与"两份分叉的引擎"同类：**读错就造出假结论的位置**。§12.8 管"别把两种算法当成两份内容"，这条管"别把三条记录当成三条防护"。

### (c) 提交只在本地 ⇒ 本轮所有门禁结论都是"本地实跑"，不是 CI 证据

`git push` 超时退出（`EXIT=124`），**远端仍停在 `00e08e3`**；本地已有**一串未推送提交**（`4859e5c` 起，数量随每次提交变化 ⇒ **不得引用为固定数**，引用时只写"远端 `00e08e3` = 未推送"这个事实）。
⇒ 本报告中所有 `GATE_FLOOR_OK`、`PASS=17`、`PASS=9` 均为**本地实跑**；**在推送成功前，任何人不得使用"CI 已通过"的表述**。这条与 §12.0 的"证据必须绑定状态"同源：提交的本地/远端位置本身就是状态的一部分。
**`114` 的引用格式**：`GATE_FLOOR_OK suites=8 total_cases=114 exit=0` 的**实跑者是 team-lead、时点是 `4859e5c` 的字节**（我本人只跑了第 8 号套件与 Y 自检 + 算术核对 7+24+10+17+12+14+13+17）。引用时必须带上这两个限定词，否则它就是**第二个会过期的孤立数字**。

---

## §12.10.11 两处更正 + 一条规则入册（`380931d` 之后）

### (a) 更正：我报的"provenance 事故"**不成立**

我在 §12.10.7 的状态行写"**任何人现在提交这棵树，会把安全报告与一份未完成的 #15 实现放进同一个提交 —— 这是 provenance 事故**"。team-lead 复核后回复的事实是：

- 他提交前跑 `git diff --cached --stat`，**输出为空，索引干净**；
- 我观测到的 115 行，是他 `git add` 之后 `commit` 因 **`index.lock` 被另一个 git 进程占用**而失败留下的**中间态**，重试成功后由 `182e717` 消费；
- **没有发生混合提交**；我的顺序要求被照做：报告单独一笔（`ede07cf`）、先于 `#15`、`#15` 至今未提交。

⇒ **我错在"从状态推意图"**。`git diff --cached` 非空只能证明"有内容已暂存"，**不能**证明"有人要把它和别的东西一起提交"。正确表述应是：

> 「观测到暂存区有 115 行本文件内容，与未提交的 #15 同树，**存在混合提交风险**，请确认顺序。」

**风险判断可以推断，事实断言不行。** 这与 §12.4.1 的"推论越界"同族，不是新类型；但与 §12.10.8 的恒真式**不同族**（那条是判据恒真，这条是证据强度越界），不要合并归类。
副产品是对的且已被接受：**`add` 与 `commit` 之间要先 `git diff --cached` 回显**，避免让人看到无法解释的状态。
**原文不删**（§12.10.7 保留并加删除线 + 指向本节），理由同 §12.7：被更正的判断本身是"下一轮会再犯的位置"的证据。

### (b) 规则入册：**任何人不得预告 floor 的未来值**

> 每次提交时 `expect` = 该次提交后**实测**的块数；**差值必须精确等于本次新增的具名块数**，并写进提交信息。

对已有材料的处置：
- **`25 条` 与 `14+25=39` 可以作为"清单与推导"引用，必须与清单同时出现**；**不得**作为 floor 的当前值、也不得作为被预告的值。
- 我 **C25** 的原措辞（"`expect` 等于当前已提交用例数"）与该规则一致，不改；但要补一条**对我自己**的限制：**我只报 delta（`+n`），不报值。**
- 由此，team-lead 自己算错的 `13 → 15`（三个独立具名 check 实为 **+3**）**按其新规不再需要我去纠正数值**：正确表述是"相对基线 **+3**"，具体值由提交时 `grep -c` 给出。**这条规则的价值正在这里——它让"算错某个值"在结构上不再可能被引用为结论。**
- 它是 §12.9.4「聚合值必须与产出 SHA 同现」的扩展：那条管的是报告里的聚合数，这条管的是 **floor 表本身**。我曾把 `total_cases` 写死为 `114`（`Z6` 一落即变），同一个教训的第二次实例。

### (c) `#9` 接线的一个技术前提（放行条件的技术面，非流程面）

`service-auth.mjs` 的 `signServiceBody` 是 **sha256 over rawBody**。现在 facade 侧同时持有 `bytes` 与 `text`，**接线必须用 `bytes`**：

1. `http-body.mjs` 的头注释自己写明："若必须按字节校验（HMAC / service auth），hash `bytes`，**永远不要 hash `text`**"；
2. 已实测：逐 chunk 解码会把 `你`（3 字节）变成 `U+FFFD U+FFFD`（6 字节）；即使一次解码，**非法 UTF-8 仍然 lossy**（`text` 无法表示任意字节序列）；
3. ⇒ 用 `text` 接线，签名对**任何非 ASCII / 非法 UTF-8 体**都 100% 不匹配，而失败会**伪装成鉴权失败**——本轮已经出现过一次同型（"看起来像 auth bug，实际是解码 bug"）。

⇒ **`#19`（字节相等断言）是 `#9` 的前置**：`#19` 证明"引擎收到的是原始字节"，`#9` 的签名校验才有意义。顺序：`#19` → `#9`。
边界我签 team-lead 原话：**不得修改冻结树里的 `service-auth.mjs`**，只在 facade 侧接线；**缺 secret 必须拒绝，不能跳过**（"跳过"就是 fail-open，与本轮全部结论相悖）。

---

## §12.10.12 `Y7` 形态裁定：两种机制必须分开命名，**形状扫描不能证明"不存在恒真式"**

team-lead 定的 `Y7` 验证义务是：**往测试文件里塞一条 `assert(S.fn.length >= 0)`，`Y7` 必须红。** 我复核后认为这个义务**必要但不充分**，因为它对一个**形状扫描器**只提供了**一个**样本。

### (a) 两种 `Y7` 是不同机制，抓的不是同一件事

| 形态 | 判据 | 能抓 | **抓不到** |
|---|---|---|---|
| `Y7a` 登记 | 每条 `case` 必须报告它对应的变异编号，**缺编号即红** | "没声明变异就上线" | 声明了变异、但用例内部**另有一条恒真式** |
| `Y7b` 形状扫描 | denylist 匹配已知恒真写法 | denylist 里那几种写法 | **denylist 外的近义写法** |

两者**互不覆盖**：`Y7a` 对恒真式本身完全无感（case 照样报编号），`Y7b` 对"没登记变异"完全无感（写法看着正常）。

### (b) `Y7b` 的覆盖率我实测：**3/6**，而他给的变异样本正好是被抓住的那一个

拿一份"看起来合理"的三条 denylist（`.length >= 0` / `assert.ok(true)` / `assert.equal(x, x)`）去扫六种**同为恒真**的写法：

```
CAUGHT  S1 assert(S.fn.length >= 0)      ← 正是 team-lead 指定的那个变异
MISSED  S2 assert(S.fn.length > -1)
MISSED  S3 assert(S.fn.length >= -0)
CAUGHT  S4 assert(true)
CAUGHT  S5 assert.equal(x, x)
MISSED  S6 const T = true; assert.ok(T)
coverage = 3/6 shapes
```
⇒ **他的验证义务在 S1 上会通过，而 S2/S3/S6 是同一个缺陷的等价写法。** 一个样本只能证明"这一个是红的"，不能证明"这一类是红的"。**这与本轮反复出现的形态完全一致**：判据的牙齿只在被测样本上被证明过。

### (c) 裁定

1. **两种形态分开命名、各自占槽位**（C28）。合并成一个 `Y7` 会让"哪一半红了"无法归因。
2. **`Y7b` 的验证义务从 1 个样本扩到 ≥3**：`S1`（`.length >= 0`）、`S2`（`.length > -1`）、`S6`（常量间接）。每多一个样本才多一条"这一类"的牙齿证据。
3. **`Y7a` 的登记位置决定它的性质**：
 - 若登记写在注释里（`// MUTANT: ⑮a`）⇒ **它是可读的叙述，可被删、被伪造**，属"声明对声明"；
 - 若登记写在机器可读的清单里（`case → mutant id` 的 JSON）⇒ 可校验唯一性与覆盖率，但**仍不能证明变异被执行过**。
 ⇒ **`Y7a` 提供的是可审计性，不是可证伪性。** 它的作用是提高"无声塞进一条恒真式"的成本（现在你得同时伪造一条登记），不是排除它。
4. **签署措辞钉死**（C23 的直接应用）：`Y7` 的结论只能写成
 > 「覆盖已知的 **N** 种恒真形状 + 每条 case 都有变异登记」
 **不得**写成"恒真式已被排除"或"每条断言都有牙齿"。
5. **唯一能证明"没有恒真式"的是执行证据**：对每条用例跑一次取反变异，要求"只红该条"（C26 的三件套）。那是**变异运行器**，不是扫描器——而变异运行器有它自己的副作用风险（本轮已出过一次"变异体把被测模块整文件覆盖、当时文件未跟踪、`git checkout` 救不回来"的事故）。
 ⇒ 所以现实的选择是：**扫描器（便宜、不完备）+ 抽样执行（昂贵、完备性更好但不可全量）**，并在报告里**分别标注各自的覆盖面**。谁把这两者合成一句"已做恒真检测"，谁就在重复 T4。

### (d) delta 记账（按 §12.10.11(b)，只报 delta 不报值）

- 若 `Y7` 按**单块扫描器**实现：相对基线 **+1**。
- 若按**两机制**实现（`Y7a` + `Y7b`）：相对基线 **+2**。
- 因此 `Z6 + Y6 + Y7` 的 delta 是 **+3 或 +4，取决于实现形态**，不取决于说明文字。**具体值由提交时 `grep -c` 给。**

---

## §12.10.13 `#21` 的两条"欠账"变异我已实测；**C26 遇到它的第一个反例：等价变异**

`dbd6368`（fixture 改成字节 + 位置模型）提交后，我按 C26 在**副本**上跑了三条精确变异（仓库零改动，`cpSync` 整个 `scripts/supervisor/` 到临时目录后改副本）：

```
BASELINE (verbatim copy)   RESULT PASS=18 FAIL=0    failing: none
A-pos   去掉文件位置（整文件覆盖）  PASS=16 FAIL=2   ⑯、⑰
B-bytes 写入时解码（bytes→string→bytes）  PASS=17 FAIL=1   ⑱
C-append 忽略 O_APPEND 标志         PASS=18 FAIL=0   none
```

### (a) 两条"欠账"其实已经可测，且都是**可定位**的

- **`A-pos`** ⇒ ⑯ 红：`w` 模式下同一 fd 两次 `writeSync` 必须接力，去掉位置就只剩最后一条。**⑯ 对位置语义有牙。**
- **`B-bytes`** ⇒ ⑱ 红：写 `中` 的首字节，若写入时解码就变 `ef bf bd`。**⑱ 对字节存储有牙**，且**只红这一条** ⇒ 满足 C26 三件套的"只红该条"。
⇒ `#21` 里"⑰⑱ 只有粗粒度变异"的判断需要更新：**精确变异现在是有的，而且我跑出来了**。粗粒度之所以"红 5 条以上"，是因为它把 fixture 整体别名回 legacy，破坏了多个前提；精确变异不会。

### (b) `⑰` 的失败**归因是错的**（R-23：一条用例两件事）

`A-pos` 下 ⑰ 也红，但**红的是它的"real 半句"**，不是它的"legacy 半句"。看代码：⑰ 三条断言 =「legacy 只剩最后一条」+「real 两条都在」+「两者必须可分」。`A-pos` 破坏 real ⇒ 第二条失败，而**用例名说的是"旧 string fixture 丢失接力写"**。
⇒ 一个失败信息把人指向 legacy，实际坏的是 real。**这正是"红的颜色对、红的理由错"**，排查会从错的那一半开始。⑱ 同构（也在同一用例里断言 legacy 缺陷 + real 正确 + 两者不等）。
**最小整改**：拆成 `⑰a/⑰b`、`⑱a/⑱b`（legacy 侧 = 负对照；real 侧 = 正向判据），或至少把失败消息分成三条、各自指名哪一侧坏了。拆则 delta **+2**。

### (c) **`C-append` 绿，但它不是覆盖缺口，是等价变异 —— 这是 C26 的第一个反例**

`openSync('a')` 已经把 `pos = files.get(key).length` 置到 EOF，所以即使忽略 `append` 标志，非 append 分支在 `at = pos = 文件长度` 处拼接 ⇒ **与 O_APPEND 语义等价**。行为不变，所以**永久不红**。

这不是缺陷，但它把 C26 推向一个必须写清的边界：

> 若 gate 要求"每个变异必须红"，维护者为了让 `C-append` 变红，只能**把 `pos` 清零或改成覆盖** —— 也就是**为了满足门禁而引入一个真缺陷**。

⇒ **C33：变异必须三分类，而不是二分。**
| 分类 | 判据 | 处置 |
|---|---|---|
| **红（可定位）** | 只红目标用例 | 接受为定位证据 |
| **恒真** | 取反后仍绿 | 断言无效，删或重写 |
| **等价** | 绿，且**能给出行为等价论证** | **合法**；必须记录等价性理由，不得为让它变红而改代码 |

`C-append` 的等价性论证就一句：**`pos` 在 `open('a')` 时已等于文件长度，故 append 标志对结果无影响。** 这句话本身就是证据，可以审。
⇒ 由此，`C26` 的"三件套"只适用于**声称可定位**的变异；不适用于等价变异。**把"绿"一律当缺陷，会制造出比它要防的更坏的缺陷。**

### (d) `readFileSync` 契约变更的传播检查：**VERIFIED 已收敛**

`dbd6368` 把 fixture 的 `readFileSync(p)` 改成"不传 encoding 返回 `Buffer`"。我查了全目录 7 个调用点：
- `readJsonl`（`core.mjs:260-268`）与 `readJson`（`core.mjs:313-321`）都做 `Buffer.isBuffer(raw) ? raw.toString('utf8') : String(raw)` ⇒ 两种返回都吃；
- 测试里 5 处调用全部显式传 `'utf8'`；
- 无其它调用点。
⇒ **没有"总是拿到字符串"的隐藏依赖**；且这条改动让 fixture 与真实 `node:fs.readFileSync(p)`（同样返回 Buffer）**更一致**，方向上是对的。**VERIFIED。**

---

## §12.10.14 RA-1 收口、`GATE_FLOOR_OK` 的判决降级、`#19` 关闭（`ab7a467` / `f5bea66`）

### (a) RA-1：是**各自对内嵌常量**，不是"互相比" —— 且我把比对改成程序化的

harbor2 质疑：两个重实现同读数只排除"我算错/打错"，不排除"两人按同一误解实现同一件事"。这个质疑成立。所以我把比对重做成**从验证器源码里解析常量**再比，而不是把我记忆里的值手抄进脚本：

```
$ grep -n "ENGINE_TREE_SHA256" scripts/v16_independent_verify.py
55:ENGINE_TREE_SHA256 = "aec5d1b7cc26db50736d355e7ef14e066fae3f2c2a376d94637c9a977008a9ad"

node: 从该文件正则取 ENGINE_REL / ENGINE_TREE_SHA256 / ENGINE_FILE_COUNT
      → git ls-tree -r HEAD | 过滤 | "\n".join（无尾换行） | sha256
constant_in_file   = aec5d1b7…a9ad
computed_from_tree = aec5d1b7…a9ad
file_count in_file = 15   counted = 15
MATCH vs CONSTANT  = true
```
⇒ **VERIFIED 成立**：锚点是**先于本次会话存在**的常量，不是我俩互相比。harbor2 的 bash 实现给出同一值，作为**独立实现**的旁证保留。

**但必须留一条限制（他提的第三种可能，我们都没做到）**：**权威验证器本身没人跑过**——`python scripts/v16_independent_verify.py` 会重写 `reports/evidence/v16/independent_verify.json`，所以我们只复算了它的算法，没有执行它。
⇒ 结论分层：**「树在冻结值上」= VERIFIED**；**「check3 通过」= UNVERIFIED-BY-EXECUTION**。这两个句子不能互相替代。

### (b) `GATE_FLOOR_OK` 不能标识实跑者 —— 我把自己的引用再降一格（第二次同类错误）

harbor2 实测：`gate-assertion-floor.mjs` **不运行任何套件**，只读 `$GATE_LOG_DIR`。
- 只放一份 8 号日志 ⇒ `ok 8-api-body-read expected=17 actual=17`，其余 7 条 `log-missing`；
- 用**别人**留下的完整日志 ⇒ `GATE_FLOOR_OK suites=8 total_cases=118 exit=0`，而那 7 套他一次都没跑。

⇒ **这一行里没有作者字段。** 我在 §12.10.9(c) 写的"实跑者是 team-lead、时点是 `4859e5c` 的字节"——**是我从口头声明推的，不是这一行说的**。按 C29 我自己刚立的规则（风险可推断、事实不可断言），这句必须降级为：

> 「**某次运行的日志目录**在 `<mtime 区间>` 满足下限；**实跑者与时点不可由该行判定**。」

这是我在同一类错误上的**第二次**（第一次是 §12.10.11(a) 的 provenance），共同点是：**把"从别处推出来的结论"写成了"这行证据说的东西"。** 记账。

### (c) 新风险 **`GATE-LOGDIR-SHARED`**（P2，归 `Y6` 槽位）

floor 的输入是一个**可写的共享目录**，既没有作者字段、也没有时点绑定。harbor2 观测到：同一目录、同一文件字节，前后两次运行分别给出 `violations=1 rc=1`（`3-market-data … summary-signature-not-found`）与 `exit=0`，而该日志 `mtime 00:38:15` **晚于前一次读取**，与"另一个 agent 正在写同一个 `.gate`"吻合（**成因 UNVERIFIED**）。

⇒ 若成因如此，这个目录**既能假绿也能假红**。而"假红"同样有害：它会让人把真实回归当成环境噪声而重跑掩盖。
**最小整改（三条，都便宜）**：
1. 每次运行 `mktemp -d` 建**独占**日志目录，不复用 `.gate`；
2. 在该目录写 `run_started_at`（或 marker 文件），
3. floor 断言**每份日志的 `mtime` ≥ `run_started_at`**，否则报 `GATE_LOG_STALE`。
没有第 3 条，前两条只防"复用目录"，不防"目录里被塞进旧日志"。

**与 E-4 的依赖链叠加**：harbor2 的守卫不管 `slug` 身份（只比 `label:` basename ↔ `GATE_SUITES`）；`slug` 被改名时靠 floor 的 `log-missing` 兜住——而 `log-missing` 的效力**完全取决于这个目录是否本轮新写**。三者（守卫不管 slug / slug 靠 log-missing / log-missing 靠目录新鲜）叠起来，只在"**干净目录 + 同一次运行**"下成立。这也是 `Y6` 里"身份键换成 `slug` 对 `tee …/<slug>.log`"必须和 (c) 三条**同一批**落地的原因。

### (d) `#19` 关闭：`forwards` 断言从"匹配文本"改成"提取源码→执行→断言产出字节"

`f5bea66` 之后我实跑：`RESULT PASS=17 FAIL=0`（**`expect` 未变**，没有新增 `check()` 块，符合 C20 的硬条件）。
关键在技术形态变了：`extractFunctionSource(srcRaw,"buildEngineRequest")` **取出源码后 eval 执行**，断言对象是 `await preq.arrayBuffer()` 的**字节**——两个操作数是"到达的字节"与"发出的字节"，**不是两个源码字符串**。这不再是零灵敏度形态。

**我独立复现了它的灵敏度**（不改仓库，只把变异施加到内存中的字符串再走同一提取器）：

```
ORIGINAL  engine-received-bytes = 7   arrived = 7   IDENTICAL
MUTANT    engine-received-bytes = 9   arrived = 7   DIFFERENT   ← 退回 (bodyText || "{}")
```
⇒ 变异下引擎会收到 **9 字节而非到达的 7 字节**，用例 13 的断言必然触发。**C20 关闭：`forwards` 断言恢复可引用。**
（harbor2 的 M-A 实测 `PASS=16 FAIL=1`、红点 13，与我的独立复现一致；他另给的"**旧测试** under M-A 仍 `PASS=17 FAIL=0`"是这次修改价值的直接证据：同一变异，旧判据全绿。）

---

## §12.10.15 `dbd6368` 是**红提交**（VERIFIED）；我自己的证据绑定错了；`STAGES` 10→9 需重签

### (a) `dbd6368` 的子提交字节是**变异体**，用它自己的字节跑它自己的测试是红的 —— VERIFIED

```
$ git rev-parse dbd6368:apps/command_center_3d/scripts/supervisor/core.mjs
0d578764bb49ceddf6581550580f93d8a1105bf0

导出 dbd6368 的 10 个文件到临时目录 → node <tmp>/supervisor.test.mjs
  FAIL ⑰ …
  + '{"seq":1}\n{"seq":2}\n'      ← legacy 侧竟然接力了
  - '{"seq":2}\n'
  RESULT PASS=17 FAIL=1
```
成因：那条变异（给旧 string 模型加位置语义）**在共享工作区里存活了约 90 秒**，team-lead 恰好在该窗口提交。

**后果（三条，都必须写进任何后续文档）**：
1. **`dbd6368` 上的任何"绿"引用作废**，包括 `total_cases=118` 那次——需要在**新提交**上重跑，不能沿用。
2. "变异不得施于被测路径"从 `#19` 的单条约束升级为**全仓约束**。harbor 已改流程（一切变异只在 `%TEMP%/v47/mut/<id>/` 副本里做，harness 自带 `MUTATION_TARGET_NOT_FOUND` / `MUTATION_NO_OP` 两道自证）——这个自证形态我签。
3. **红提交可以进历史，且没人当场发现**：它由"用别人的工作区跑"产生，不是由"跑错了命令"产生。⇒ 门禁必须能对**已提交字节**重跑，而不是只对工作区跑。

### (b) 我自己的证据绑定错了，已重绑 —— 这一类错误我记第三次

我在 §12.10.13 写"在 `dbd6368` 字节上"，**实际跑的是当时的工作区副本**。证据：若我真跑 `dbd6368` 的字节，baseline 会是 `PASS=17 FAIL=1`（见 (a)），而我记录的是 `PASS=18 FAIL=0`。⇒ **我的表格绑错了对象。**
补救：把 `318c004`（当前 HEAD）的 10 个文件导出到临时目录，对它重跑同一套变异：

```
BASELINE  PASS=18 FAIL=0
A-pos     PASS=16 FAIL=2   ⑯、⑰
B-bytes   PASS=17 FAIL=1   ⑱
C-append  PASS=18 FAIL=0   （等价变异）
```
结论不变，**锚点换成 `318c004` 的已提交字节**。旧表的结论继续有效，但它的出处必须改成这里的重跑，不能沿用"`dbd6368` 上"这个错误出处。
**记账**：这是我第三次同类（provenance 推意图、`GATE_FLOOR_OK` 推实跑者、这次推字节来源）。共同点：**我把"我以为我跑的是它"当成了"我跑的就是它"**。唯一可靠的写法是：**跑之前先打印你跑的那份东西的 hash。**

### (c) `STAGES` 从 10 段改 9 段：**不能静默改，必须重签**

harbor 把顺序改成 `killSwitch → lease → idempotency(读) → resume(读) → leaseEpoch → cycleIntent → cycleImpl → record → heartbeat`，理由成立且是**实质性的**：`entries()` 每次全量重读账本，重复提交/in-doubt 早退若也追加任期记录，调用方靠重复提交就能把账本撑大（O(n²) 自我放大）。"读判定全部先于写"是正确修法。

**但两件事必须同时发生，否则是降级**：
1. **`inDoubtGate` 从标签集合里消失** ⇒ 早退在 `trace` 上**不再可见**。若 `⑲f` 只断言"拒绝"，那么"拒绝发生的**位置**"就没有观测点了。要么恢复一个标签，要么明确写"in-doubt 的可观测性由 ⑲f 的盘上断言承担，`trace` 不再承担"——**这一句必须进签署文本**，否则下一个读 trace 的人会以为没发生早退。
2. 9 段是**新冻结集**，旧 10 段作废。按 §12.10.10(d) 的教训：**顺序标签永不得被援引为顺序证据**，这条在新集上继续有效。

> **更正（harbor 指出，我逐提交验证）——第 1 条的措辞作废，结论保留。**
> 第 1 条写的是"消失了"，前提是"曾经有过"。**没有过**（取证见 §12.10.4(b) 更正块：`-S'inDoubtGate'` 全历史 0 命中，阶段集只有 7 → 9）。所以正确表述是：
> **「`trace` 从来就不带 in-doubt 的原因标签；这不是退化，是设计选择。它的可观测性由 ⑲f 承担——而"由谁承担"必须写成断言，不是写成注释。」**
> 结论方向我保留（理由换了）：`STAGES` 的 9 段里没有"in-doubt"这个词，所以**任何读 `trace` 的人都不可能从 trace 上知道早退是因为 in-doubt**。这件事在 7 段时代同样成立，只是那时没人写下来。
> 第 2 条继续有效且已落进代码注释（`supervisor.mjs:24-35` 明写"THIS NINE-STAGE LIST IS A NEW FROZEN SET … there was never a ten-stage list in code"），**这一句是本轮唯一由实现者主动写进源码的"作废声明"，值得保留**。

### (d) `⑲e` 的判据形态：我签（它正是我要求的那一类）

```js
deps.cycleImpl = async () => {
  const rows = S.readJsonl(fsImpl, ledgerPath);      // 盘上状态
  lastKindAtEffect = rows[rows.length-1].kind;       // 必须是 'cycle_intent'
  completionAtEffect = rows.some(e => e.kind === KIND.completion); // 必须是 false
  fsyncAtEffect = fsImpl.fsyncCallCount();           // 必须 >= 2
  ...
};
```
- 观测点在**副作用内部**，比对对象是**盘上条目**与**计数器**，不是 `STAGES` 索引 ⇒ 不违反 §12.9.1；
- `M-order`（把 intent 写挪到 `cycleImpl` 之后 = D-14-0 原形）**只红 ⑲e 一条**；
- `M-fsync` 同理只红它。

**他 12 条变异表里两条各红 2 条**（`M-in-doubt-is-run` → ⑲f+⑲g；`M-fs-no-pos-advance` → ⑯+⑰），他相应地只主张"**每条用例都有至少一个只红它自己的变异**"，而**没有**主张"每个变异只红一条"。后者是假的。**我签这个弱主张**——这正是 C26 允许的形状：可定位性按**用例**要求，不按变异要求。

### (e) `IN_DOUBT` **没有出口**：单列一条，且禁 `force:true`

harbor 自己把它标"未完成、不算已交付"，我认。现状是**崩溃一次永久卡住**：方向对（at-most-once），但生产上是值班事故。要求：
- 必须有 `resolveInDoubt(runId, cycle, decision)`，**三件套缺一不可**：操作者身份 + 落盘记录 + 批准人；
- **不得提供 `force:true` 后门**（一次"就这一次"的后门，等于把 IN_DOUBT 变回 at-least-once）；
- 解析动作本身也要**先写后做**（否则解析途中崩溃又回到"不知道做过没有"）。
**定级 P2（可运维性/活性），不是 P0**：它不 fail-open，只会 fail-stuck。

### (f) C36：**任何"已验证某提交字节"必须同时声明"在哪份克隆"**

harbor2 指出：`318c004` 等**七个提交远端都没有**（`origin = 00e08e3`）。所以"我验过 `318c004`"只对**本机这份仓库**成立；对一个干净克隆，那是个空指针。
⇒ 本报告所有提交级结论的完整表述形如：**「`318c004` 的字节在 `D:\jev_worktrees\v2ops` 这份克隆上通过 X；远端 `00e08e3` 未包含它」**。省掉后半句的表述一律视为越界。

### (g) `Y7` 槽位撤回 —— **我上一版的两个说法都错了，按实测更正**

我在 §12.10.14 写"`Y7` 占 `7-no-fake-live` `+1`，该套件 delta 只含 `Y6` 的 `+1`"。**两处都不对**（harbor2 指出，我现跑复核）：

```
$ ls apps/command_center_3d/server/tests/service-auth-unreachable.mjs   → 不存在（第 9 套件从未建立）
$ grep -c Y9 apps/command_center_3d/scripts/check-no-fake-live.mjs      → 4
$ grep -c Y7 …                                                          → 1（仅注释："Y7 is the literal-tautology scan"）
$ grep -c W4 …                                                          → 0（旧临时叫法，已作废）
$ grep -A8 'slug: "7-no-fake-live"' gate-assertion-floor.mjs | grep expect → expect: 14
$ node check-no-fake-live.mjs → RESULT: CORE_CASES=7 EXTRA_CASES=7 TOTAL_CASES=14 exit=0
```

> **更正（harbor2 指出，我复跑确认）：上面第 2 行的 `1` 是 `52a6c9b` 时代的值。**
> 主理人在 `e621a7c` 删掉了那处交叉引用，现盘 `grep -c Y7 → 0`、`grep -c Y9 → 4`。
> ⇒ 本节三条结论不变，**佐证那一行的数字过期**；并因此 **RZ-15 可以关闭**（"剩 1 处 Y7"已归零）。
> 这一条的教训比数字本身重要：**我在写"现盘实测"时，前面几行是当场跑的，这一行是抄上一轮的。** 混在同一代码块里，读者无法分辨哪一行是新的。**纪律：一个代码块里所有行必须是同一次执行的原样输出，跨轮的读数不得并排摆放。**

更正三条：
1. **落地的叫 `Y9`（零调用点登记），不是 `Y7`**；`Y7` 是**另一个东西**（字面恒真式扫描），由 harbor 定义。名字分开的目的正是防止"以为恒真式已经扫过"。
2. **`7-no-fake-live` 的 delta 是两次 bump**：`Y9` 已把 13 → **14**（floor 的 `expect: 14` 与实测 `TOTAL_CASES=14` **同盘一致**）；**`Y6` 落地时 14 → 15**。我上一轮的"+1"会让 `Y6` 的作者以为 floor 已就位。
3. 我反对的"把 `Y7` 并入第 9 套件"**反对的是一个已作废的方案**：第 9 套件不存在，五条行为断言整体推迟（触发条件"本仓出现第一条可信写入路由"），且 `Y9` 是**独立命名 check、独占槽位**（`CORE` 7 不动、`EXTRA` 6→7）。**我要的性质已经成立。**

### (h) C36 定版：`commit + clone + clone 当时的 HEAD`，且**跑前跑后必须同一个 HEAD**

harbor2 指出：**克隆本身也会前进**，所以"在某份克隆上通过"十分钟后就失真。C36 的完整形态是**三段**：

> 「`<commit>` 的字节在 `<克隆路径>`（该克隆当时 HEAD = `<HEAD>`）上通过 X；远端 `<origin>` 未包含它。」

**再收紧一层（RZ-17，我签）**：只有"记录了 HEAD"仍是事后填空。判据必须是**锁存**：

```
HEAD_BEFORE=$(git -C <clone> rev-parse HEAD)
<run>
HEAD_AFTER=$(git -C <clone> rev-parse HEAD)
[ "$HEAD_BEFORE" = "$HEAD_AFTER" ] || 本次读数作废
```
理由就是 `dbd6368` 那次事故的**确切形态**：**测量跑在一棵会动的树上，读数是绿的，被提交的字节是红的。**
⇒ 一般化：**任何读数必须与它所读的那份字节同步锁存。** 这与 team-lead 的"测量 → 记 blob → add → 断言暂存 blob == 记录"是同一协议的两个方向。

### (i) 我上一条建议错了：**不要加 `.gitignore`**（采纳 harbor2，我的原建议撤回）

我建议"清理或加 `.gitignore`"。**后半句错**，理由他给得比我强：
1. `.gitignore` 会把测量残留**制度化**——下一份 `.gitignore` 的读者会以为"这些文件本来就该存在"。**忽略规则是给构建产物的，不是给测量残留的。**
2. 更要紧：`.floor-run.txt` 首行是 `LOG_DIR=C:\Users\…\Temp/tmp.ggFZlKjD0g/gl-1451` —— 这行恰恰是**"私有日志目录已在使用"的归属证据**。ignore 掉它，等于把"存在证据"这个信号一起藏了：**噪声没被消除，只是没人再看得见它。** 这与本轮在标记上学到的是同一条——**藏起来的不是不存在，只是不可观测。**
3. 真修法是"测量工具默认写 `%TEMP%`"，不是"仓库学会容忍它们"。

**现盘实测（`318c004` 之后的克隆状态）**：未跟踪文件 **8 个**（`.blob-after.txt` / `.blob-before.txt` / `.blob-y9.txt` / `.commit-msg-1.txt` / `.commit-msg-2.txt` / `.floor-run.txt` / `.measure-25.txt` / `.measure-supervisor.txt`），`git check-ignore` 全部 **not ignored**。首行可归因：`.blob-*` = blob 校验协议产物（`<hash> <path>` 两列）、`.commit-msg-*` = 提交信息草稿、`.measure-*` = 套件测量留痕、`.floor-run.txt` = floor 运行时的 `LOG_DIR`。

> **时态更正（harbor2 指出，我复跑确认）：上面这 8 个是历史读数，现盘 `git status --porcelain | grep -c '^??'` = 0。** 它们全部是**主理人**做"暂存 blob == 被测 blob"断言时的产物，已由他删除。
> ⇒ 本节**结论（不加 `.gitignore`）不变**，且现在有了更强的版本：**它们不仅应该删除，而且确实不必要**——harbor2 在干净克隆（`status --porcelain | wc -l` = 0）上分别于 `e621a7c` 与 `b71a80e` 跑完整 8 套件，两次都是 8/8 `rc=0`、`GATE_FLOOR_OK suites=8 total_cases=126 exit=0`、`LATCH_OK`。**干净克隆上一条不少，说明提交的字节是自足的。**
> 但"8 个"这个数字**保留在正文里不改**——它是我当时读到的真实状态，属于历史证据。**改的是时态标注，不是事实。**

**处置**：由产生者删除或移入 `%TEMP%`；**不加 ignore 规则**。若最终仍要留一条，必须是**带注释的例外**（"这些是测量残留，应删除；加此规则只为防 `-A` 误入"），**不得让规则自己冒充理由**。

**顺带一条正面证据**：`.floor-run.txt` 里的 `LOG_DIR=…/Temp/tmp.ggFZlKjD0g/gl-1451` 说明**私有 `GATE_LOG_DIR` 已经在被使用**（不是只写在文档里）。§12.10.15(c) 那三条整改的第一条（独占目录）已在执行。

### (j) `⑲f` 的"可观测性转移"**目前是空头承诺**：我构造出让它两个断言同时绿的变异（P2，已复现）

harbor 把我要的那句"in-doubt 的可观测性由 ⑲f 承担"**做成了断言**而不是注释（这是对的，注释拦不住下一个人）。但断言本身不够强。`supervisor.test.mjs:499-508` 的两个半句是：

```js
assert.deepEqual(r.trace, ['killSwitch','lease','idempotency'], 'in-doubt 必须在任何写阶段之前早退');
const after = S.readJsonl(fsImpl, sup2.subsystems.idempotency.ledgerPath);
assert.equal(after.length, 2, 'in-doubt 早退不得追加记录（epoch+intent 共 2 条）');
```

**它的名字声称的是"任何写阶段之前"，它的断言只覆盖了"账本没变"和"标签没多"。** 这两处之间有个洞：**一次不经过账本、也不 push 标签的写**。

复现（`%TEMP%\jcc_gate\sup069`，把 `scripts/supervisor/` 整目录拷出后施加变异；被拷源 = 本克隆 `daa9386`）：

```
变异：在 if (state === 'IN_DOUBT') { 之后、return 之前插一条
      watchdog.beat({ runId, cycle });   // 写心跳文件，无 trace 标签
BEFORE sha256_12=f4568bf600d9 bytes=6843 hits=1
AFTER  sha256_12=c56f8d2cf83b bytes=6930 changed=true
变异体整套测试                        RESULT PASS=25 FAIL=0      ← ⑲f 没红

可达性自证（同一变异体上单跑同一场景，证明变异确实执行了）
  status                = IN_DOUBT
  trace                 = ["killSwitch","lease","idempotency"]   长度 3
  ledger rows           = 2        epoch rows = 1        cycleImpl calls = 0
  heartbeat 存在(前/后) = false / true
  heartbeat 内容        = {"cycle":0,"lastBeatMs":5000,"runId":"run-W2","seq":1}
  writeCallCount delta  = 0
```

**两个结论，第二个比第一个重要：**

1. `⑲f` 的**两个断言同时绿**，而**早退前已经发生了一次盘上写**。测试名声称的"任何写阶段之前"当前**无断言支撑**。§12.10.15(c) 我之所以能接受"9 段、trace 不承担原因"，唯一依据就是这句话——所以**这不是风格问题，是我那个签署前提本身没落地**。
2. **我第一版想用 `writeCallCount()` 当兜底，探针当场把这版判死**：delta = 0。原因：`atomicWriteJson` 走 `writeFileSync` + `renameSync`，**不经过 `openSync`/`writeSync`**，而 `writeCallCount()` 只数 `writeSync`。也就是说"用现有计数器兜底"是**假兜底**——它会永远绿。**建议判据必须换成按路径比字节。**

我已把建议判据写成探针，**两向都跑过**（不是推理）：

```js
const snap = () => Object.fromEntries(Object.entries(FILES).map(([k, rel]) => {
  const p = resolvePath(BASE_DIR, rel);
  return [k, fsImpl.existsSync(p) ? fsImpl.readFileSync(p, 'utf8') : null];
}));
const before = snap();
const r = await sup2.runNextCycle({ runId: 'run-W2' });
const after = snap();
assert.deepEqual(Object.keys(before).filter((k) => before[k] !== after[k]), [],
  'IN_DOUBT 早退路径上不得有任何已知文件发生变化');
```

```
干净码   changed = []              → GREEN
变异体   changed = ["heartbeat"]   → RED（只红这一个）
```

**顺带自纠**：我第一版把期望值写成 `['lock']`（以为租约那一写会留下来）。实跑 `changed = []` —— 租约在 `finally` 里已释放，**前后都不存在**。**判据的期望值也必须实跑，不能由语义推理得出。** 这和我 C29 立的那条是同一件事的另一面。

**整改（最小）**：把上面三行并进 `⑲f`，`6-supervisor` `expect: 25 → 25`（不加用例、只加断言 ⇒ **floor 不变**，这正是"加断言不一定要加用例"的正面样本）。附带好处：这个变异**只红 ⑲f**，于是 ⑲f 立刻获得自己的单红定位证据——而 harbor 现在手上那个 `M-in-doubt-is-run` 红两条、只能算"非恒真证据"。**修法与证据是同一件东西。**

**定级 P2**：不 fail-open，是"声称的守卫没覆盖它自己名字里的词"。**但它是 `#15` 签署的前置条件**——我签 ⑲f 的前提是它能兑现那句话。

### (k) `createLegacyStringFs` 是**同一会话里重打的复述**，不是历史字节 —— 且已实测漏了 2 个方法（P3，C34 同族）

`⑰a`/`⑱a` 是负对照：断言"旧 string 模型**必须**丢掉接力写 / **必须**把半字节解成 U+FFFD"。它们的杀伤力完全来自"旧模型确实是这样的"。但那个"旧模型"是 `core.mjs:189` 里**重新手打的一份复述**，不是 `dbd6368^` 的字节。逐方法比对（脚本比对，非眼看）：

```
历史 createMemoryFs @ dbd6368^ : appendFileSync,closeSync,existsSync,mkdirSync,openSync,
                                 readFileSync,renameSync,unlinkSync,writeFileSync,writeSync
现在 createLegacyStringFs     : appendFileSync,closeSync,existsSync,openSync,
                                 readFileSync,unlinkSync,writeFileSync,writeSync
历史有、复述版缺 : mkdirSync, renameSync
unlinkSync        : 历史抛 ENOENT / 复述版 files.delete(...) 静默
```

三条后果：

1. **锚点不合格（C34）**：负对照的权威应当来自先于会话存在的常量（`dbd6368^` 的字节），现在来自**同一会话里作者自己敲的字**。两个重实现互相一致不构成证据——这条我在 C34 里已经立过，这次是它第一次打到测试代码本身。
2. **"重打必失真"已经发生**：漏 2 个方法、1 处语义变更。这不是担心，是实测。
3. **它对生产模块有可见性**：`createLegacyStringFs` 是从 `core.mjs` **export** 出去的，而 `core.mjs` 是 supervisor 的入口模块。`scripts/supervisor/` **不在**冻结引擎树内（`ENGINE_REL = apps/command_center_3d/server/engine/`，15 文件），所以**移走不受冻结约束**。误用后果很具体：任何一处手滑 `import { createLegacyStringFs }`，得到的是一个**静默丢接力写、并把半字节变成 U+FFFD** 的 fs——正是本轮在修的两个失败形状。当前调用点只有测试（已 grep 确认 4 处，均在 `supervisor.test.mjs`）。

**整改（最小，与 `#19` 已有先例同形）**：把 `dbd6368^` 的那段原文作为**字符串常量 + sha256 锁**放进测试文件并 `eval`，负对照从此不可被"改到通过"；生产 `core.mjs` 里的 `createLegacyStringFs` 删除。理由说得直白些：**负对照的天生失效方式就是"调整对照直到它变绿"，所以它比正向用例更需要一个不可编辑的锚。**

### (l) 锚点更新与四个变异的独立复核（harbor 的 4/4 单红主张成立）

**本轮所有读数绑定到 `daa9386`，不再是 `5fe0b19`**（`#15` 的 ⑰⑱ 拆分提交已落）。按 C36 定版格式：

> **更正（harbor2 用 diff 否证了提交信息，我照录）：不得写"`5fe0b19` 把 `dbd6368` 的红修掉了"。**
> `git show 5fe0b19 -- …/core.mjs` 是 **0 删除 / 26 新增**（纯加 `appendChainEntryDurable`），它自称"改回"的值 `2dfd13a3` 在**父提交 `318c004` 上已经就是**了。真正的回退在 `7a2a4e7`：
> ```
> dbd6368   0d578764…  ← 变异体
> 341f8a5   0d578764…
> 7a2a4e7   2dfd13a3…  ← 回退在这里
> 318c004   2dfd13a3…
> 5fe0b19+  b679389b…  （加 durable append）
> ```
> 我 §12.10.15(b) 的结论"用 `dbd6368` 自己的字节跑自己的测试是红的"**成立且已自证**；但"谁修的"必须**以 diff 为准，不以提交信息为准**。这与 §12.10.15(b) 那条（"`GATE_FLOOR_OK` 不标识实跑者"）是同一句话的另一面：**提交信息也不标识提交内容。**


```
克隆   = D:\jev_worktrees\v2ops
HEAD   = daa9386efef36979d8b1032d8d143b20cad85462
test blob (HEAD)    = dd7d059c1f2c925e082ceabed146ee7c5428f4a7
test blob (工作区)  = dd7d059c1f2c925e082ceabed146ee7c5428f4a7   ← 无漂移
实测   = RESULT PASS=25 FAIL=0
```

> **⚠ 本节所有 `PASS=` 读数一律视为"未锁存"，不得引用。** 主理人裁定：「`PASS=28` 在任何 blob 锁存之前不得被引用，包括你自己的报告。」我照办，并把它升级成规则：**只锁 `supervisor.test.mjs` 一个 blob 不够——参与判定的还有 `core.mjs` / `idempotency.mjs` / `ledger-io.mjs` / `index.mjs` / `resume.mjs` / `supervisor.mjs` / `watchdog.mjs` / `leader-election.mjs` / `kill-switch.mjs` / `cost-monitor.mjs` 与 `gate-assertion-floor.mjs`。**
>
> **已按 12 个 blob 全量锁存重跑一次，结果是红的**（`HEAD 0bb240b`，12 个 blob 跑前跑后完全一致 = `LATCH_OK`）：
> ```
> RESULT PASS=25 FAIL=3
> 　　同长度篡改在 5 次读取内必须被抓到（实际没抓到）   ← ⑳ 失败
> ```
> ⇒ **当前锁定状态：`6-supervisor` 是红的，且 `expect` 是 28 而实测 25。** 上面那段 `PASS=25 FAIL=0`（`daa9386`）与后面提到的 `PASS=28`（更晚的一次）**都是未锁存读数，一律作废**；`PASS=28` 那次恰好落在 harbor 正在写文件的中间态。
>
> **补充（主理人已改 `⑳` 的契约，这条改变了那个红的含义）**：同长度篡改**不改变 `byteLength`**，而游标作废条件只比 `byteLength` ⇒ 不是 `K` 取小了，是**作废条件漏了一个维度**。新契约：游标作废条件扩成 `{count, headHash, byteLength, mtimeMs}`，任一变化即全量重验；于是 `⑳` 的正确断言是「同长度原地篡改必须在**下一次读取**被抓到」。**按旧契约它会一直红，而那个红不说明实现坏，说明契约写得比实现能保证的更严。**
>
> **必须一起写下的边界（否则是在卖假保险）**：**能改写文件的人同样能改写 `mtime`（`utimes`）**。所以 `mtimeMs` 是检测辅助、**不是安全边界**；真正的边界仍是「取租约必全量重验」+「每 `K` 条 / `M` 字节强制全量重验」。于是 `K`/`M` 的定位从"性能折中"变成**"对能伪造 `mtime` 的攻击者的检测延迟上界"**——这是一个可以写进威胁模型的量，而不是一个性能参数。

>
> 这一条对本报告的意义比数字大：**我自己在同一节里既主张"未锁存不得引用"，又引用了两个未锁存数。** 我把它记成第二次「用旧状态/中间态评新方案」，并且是**在我自己刚立的规则上**犯的。

> **floor 的 delta（只报变化，不报绝对值，C30）**：`6-supervisor` 的 `expect` 由 `25` 变为 **28**（`+3`），对应在途的 `⑲c` / `⑲d` / `⑳`（游标窗口有界）三条。**该 `+3` 对应的三条用例在锁定读数里只过 2 条（⑳ 红）**，所以此刻 `expect: 28` 与实测不一致，是红而不是"尚未同步"。


> **测量期间工作区被并发修改（新事实，必须登记）**
> 我做 (k) 的逐方法比对时 `core.mjs` 的 blob 是 `b679389`；几分钟后同一路径变成 `0e74ee3`（`+7 -0`：`FILES` 新增 `anchor: 'ledger.anchor.json'`，附注释"锚点必须是独立小文件，因为原地重写 header 正是 append-only 禁止的操作"）。**这不是我的改动**——是共享 worktree 上的并发编辑（`#15` 的后续）。
> 处置：
> - **四变异表**跑在 `core.mjs = 0d22336…`（新拷贝取走那一刻的字节）上，出处锁定在那份拷贝，**不代表 `0e74ee3`**；
> - **`⑲f` 的复现我在两个状态上都做过**：旧状态（无 `anchor` 键）与当前 `0e74ee3`，两次结果一致（baseline `PASS=25` / 变异体 `PASS=25 FAIL=0` / 探针 `changed=["heartbeat"]`）。**结论不依赖那次并发编辑。**
> - 归入 C36：**共享 worktree 不是稳定的测量面。** 读数若不能当场锁存 blob，就必须重测、不得复用——`dbd6368` 的变异体在共享工作区活了 ~90 秒就是这个洞的极端形态。
> - **该时间点之后 HEAD 又前进了三次**（`e621a7c` → `b71a80e` → `a647fac`），工作区另有 6 个 supervisor 文件 + `gate-assertion-floor.mjs` 处于未提交修改、`ledger-io.mjs` 未跟踪。**上面这段里的 `daa9386` 只能作为"那一次读数的时点"引用，不得当作"当前状态"。**

我在**独立拷贝**上重跑四个变异（`core.mjs` baseline `sha256_12 = 0d22336175d2`；每个变异先打印 `hits`，非 1 即判废）：

| 变异 | 结果 | 红的是 |
|---|---|---|
| `M-fs-no-pos-advance` | `PASS=24 FAIL=1` | **⑰b** |
| `M-fs-string-store` | `PASS=24 FAIL=1` | **⑱b** |
| `M-legacy-gains-pos` | `PASS=24 FAIL=1` | **⑰a** |
| `M-legacy-byte-faithful` | `PASS=24 FAIL=1` | **⑱a** |

⇒ 我上一轮提的归因指正（"红在 real 半句、消息指向 legacy 半句"）**已被拆分实测消解**；四个方向各自单红，且红在与主张相符的那一侧。b 侧两条是对他读数的复现，a 侧两条是我自己构造的对应变异。

**一条我自己的险错（记入 C36 推论）**：

```
$ git status --porcelain                →  M apps/…/check-no-fake-live.mjs
$ git diff --numstat -- <该文件>        →  （空）
$ git status --porcelain                →  只剩 M reports/…
```

porcelain 里的 ` M` 可以只是 **stat-dirty**（mtime / index 元数据），内容与 index 完全一致。**"有未提交改动"必须由 `git diff --numstat` 非空来支撑，不得由 porcelain 的 `M` 来支撑。** 我差一步就向 team-lead 报"harbor2 改了 `check-no-fake-live.mjs`"——他明确说过他不再碰这个文件，那条指控会是假的。
归入 C36 推论：**读数必须与它读的那份字节同步锁存；porcelain 锁存的是元数据，不是字节。**

### (m) 接受的四条 + 两条追加条件

**接受**：
1. **R-23**（把"同一 fd 两次写必须接力"从 ⑯ 删掉、归 `⑰b`）：成立。一条用例只证一件事；重复断言会让一处坏掉红两条，从而永远拿不到独立定位证据。**⑯ 的 `a`/`w` 与 fsync 计数仍在，覆盖面没有缩小。**
2. `C-append` 是等价变异、且**没有为了让它是红而改代码**：这是 C33 的正确用法。**若哪天有人把 `append` 分支改坏来"让它红"，那才是事故。**
3. `IN_DOUBT` 出口的第 4 条（带 `intentHash`）：**比我要求的三条更严**，采纳。
4. `M-in-doubt-is-run` 红 ⑲f + ⑲g 且**不去拆 `cycleState`**：接受。C26 的可定位性按**用例**要求，不按变异要求；把三态契约从它的上下文里拆出去，换来的只是一格好看的表格。

**追加条件（`intentHash` 的两条必需项，缺一即等于没加）**：
- **解析前必须有一次完整链校验**，`intentHash` 在**校验通过的链**上解析；否则 `intentHash` 只是一个攻击者可以随手写进去的普通字段（字段级不可伪造只能由链哈希提供）。
- `intentHash` **必须命中那条 `cycle_intent`**，且该 `(runId, cycle)` 当前状态**必须是 `IN_DOUBT`**；否则拒绝（`RESOLVE_TARGET_NOT_IN_DOUBT`）。否则一条"批准"可以被套用到不存在的周期上——**这正是他加这条要堵的洞的余量**。

**对 `mutate.mjs` 的 `hits !== 1` 判废规则，追加一条边界**：`hits === 1` 证明的是"目标串唯一"，**不证明改到的是活代码**。若命中落在注释里，变异体会编译通过、测试全绿，你会把它读成"这条用例没牙"，而真因是**变异不可达**。故：**任何"绿"的变异，必须先给出可达性自证**（计数器 / 盘上副作用 / 探针输出），否则该绿读数作废——这就是我在 §12.10.15(j) 里对 `watchdog.beat` 做的那一步。**变异必须先自证可达，再论红绿。**

### (n) `#15` 状态：**截至 `0bb240b` 仍 OPEN —— 两条前置未满足**（不写"可签/不可签"）

> 按主理人裁定：**未闭合项一律写成「截至 `<SHA>` 仍 OPEN，修复条件 = …」，不用会随修复自动失效的状态断言。** 本节旧标题写的是"有条件不签"，改为上式；结论内容不变。
> 修复后**追加新一节（带新 SHA）**，不改本节——与 append-only 纪律一致。


**已达成的部分**（我逐项核过，不含推断）：25 条清单与 `6-supervisor expect: 25` 一致、实测 `PASS=25 FAIL=0`；四个变异各自单红且红在对的那一侧；`STAGES` 9 段在 `supervisor.mjs:24-39` 的注释与数组同处、并含"7 段与 10 段都作废"的书面声明；`⑲e` 的观测点在副作用内部（读盘条目 + 计数器，不读 `STAGES` 索引）；R-23 已落地。

**两条前置（都很小，且都由我上面的实跑得出，不是风格建议）**：

1. **`⑲f` 补一条"路径×字节快照"断言**（§12.10.15(j)）。否则"in-doubt 的可观测性由 `⑲f` 承担"这句话**没有支撑**——而它是我接受"9 段里没有 in-doubt 标签"的**唯一理由**。加断言不加用例，`floor` 不变；且该变异只红 `⑲f`，等于顺手把它自己的定位证据补上。
2. **负对照换成历史字节**（§12.10.15(k)）：`⑰a`/`⑱a` 改从 `dbd6368^` 的原文（字符串常量 + sha256 锁）求值，`createLegacyStringFs` 从生产 `core.mjs` 移除。现版本是同一会话手打的副本，且**已实测漏 `mkdirSync`/`renameSync` 两方法、`unlinkSync` 语义不同**。
3. 若 `IN_DOUBT` 出口按 `intentHash` 实现，则 (m) 的两条追加条件同时生效（解析前完整链校验、目标必须当前为 `IN_DOUBT`）。

**定级说明**：这两条是 **P2 / P3**，不新增 P0/P1 blocker，因此**不改变下面的裁定**；但它们直接决定 `#15` 的状态——**成立的前提是"那句话由断言承担"，截至 `0bb240b` 它还不成立。**

**修复条件（两条都必须满足，缺一 `#15` 维持 OPEN）**：
1. `⑲f` 加"路径 × 字节快照"断言且白名单为空；修复判据 = 我构造的 `watchdog.beat` 变异**必须只红 `⑲f`**（它现在是全绿）。
2. `⑰a`/`⑱a` 的负对照改为 `dbd6368^` 原文 + sha256 锁，且 `createLegacyStringFs` 移出生产 `core.mjs`；修复判据 = 改动历史原文一个字节，该用例必须红。


---

**状态（截至 `0bb240b`）：Phase GO = NO-GO；安全域 = NO-GO。** 按主理人裁定，本节不写"可签/不可签"，只写**截至哪份字节看到什么、以及关闭条件**：

| 项 | 截至 `0bb240b` | 关闭条件 |
|---|---|---|
| **`B2b`** Windows 重启恢复 | **OPEN，零证据**；且其判定器（`FINAL-10`）本身 fail-open（§12.10.17）⇒ "零证据"与"假证据"同时存在 | 需独占重启窗口 + `release_sha` 绑候选 SHA；判定器先按 §12.10.17(g) 修好 |
| **`LEDGER-ATOMIC` + D-14-0** | **OPEN**。写入序已落 `5fe0b19`；但截断路径 fail-open（§12.10.16，P1，仅在未提交工作树） | `SHORT_TRUNCATED ⇒ 抛` + 锚点强制 + 禁洗证据三条落地，且我构造的重复副作用场景变红 |
| **`ORIGIN-LAUNDER`** | **OPEN**。`trading_api.mjs:195` 硬写 `origin: ORIGIN`、入站不校验、`[[path]].js:24` 的 `isSameOrigin` 导入不调用 | 入站校验 `origin`；`isSameOrigin` 要么调用要么删除导出 |

远端仍停在 `00e08e3`，**本轮全部读数为本地实跑，不得引用为 CI 证据**；且每条读数只在它自带的 blob 清单成立。

---

## §12.10.16 后缀截断是 fail-open：锚点在、被忽略；锚点不在、直接报 CLEAN（P1，截至 `0bb240b` 仍在未提交工作树）

### (a) 结论先行

`ledger-io.mjs` 的锚点**已经能发现截断**（`mode=SHORT_TRUNCATED`、`anchorPresent=true`），但发现了之后**选择继续**。后果：任何「整个周期一起被截掉」的截断，都表现为 `cycleState = ABSENT`，于是**周期被重跑** —— at-most-once 在该路径上是假的。

而且这条路径与 D-14-0 是同一形状：**意图记录（intent）与完成记录（completion）在同一段字节里，一起被截掉 ⇒ 账本说"没开始过" ⇒ 放行重跑。** 写入序本身并没有失效，失效的是"账本没有的一段，不等于没发生过"这个判断。

### (b) 复现（`%TEMP%\jcc_gate\trunc` / `trunc2`，代码 = 本克隆工作树未提交版本：`ledger-io.mjs` 未跟踪 + 5 个 ` M` 文件）

> **读数锁存（补，因为我前面忘了做）**：该复现跑在一份**临时拷贝**上，事后回算它的 blob：
> ```
> core.mjs          0e74ee329135      supervisor.mjs      62067dde2b8a
> ledger-io.mjs     15d0a8e1f290      supervisor.test.mjs 746a05446ba9
> idempotency/index/resume/watchdog/kill-switch/leader-election/cost-monitor：与当前工作区 SAME
> ```
> **这四个 blob 已经不是当前工作区**（工作区现为 `core.mjs 29277ff4` / `ledger-io.mjs b2a6fce0` / `supervisor.mjs 07276330` / `supervisor.test.mjs a7f871a5`）。
> ⇒ 表述必须拆成两半：**行为复现绑在上面的 blob 集合上**；**机制（`:144` / `:145` / `:176` / `:194` 四处）由主理人独立在当前字节上逐行确认存在**。两者互不代替——这正是 C36 要的：**结论可以复用，字节不能。**

```
先跑 5 个周期（runId=run-T, cycle 0..4）⇒ 15 条记录，anchor 记 {byteLength:3993, count:15}
在记录边界把账本截到 9 条（2373 字节）——剩下的链**依然完整**，所以 verifyChain 通过

[A 保留锚点]
  recovery  = {"mode":"SHORT_TRUNCATED","discardedBytes":0,"adopted":false,"anchorPresent":true}
  resume    = 未抛错，lastCycle=2，blocked=false，inDoubtCycles=[]
  cycleState(run-T,3) = ABSENT      ← 副作用已经发生过
  runCycle(run-T,3)   = status=COMPLETED executed=true
  cycleImpl 调用次数  = 1           ← 重复副作用

[B 连锚点一起删]
  recovery  = {"mode":"CLEAN","discardedBytes":0,"adopted":false,"anchorPresent":false}
  runCycle(run-T,3)   = status=COMPLETED executed=true
  cycleImpl 调用次数  = 1           ← 重复副作用
  重开后 anchor = byteLength=3192 count=12   （原 3993/15 已不存在）
```

### (c) 三处独立的机制缺陷

1. **发现了不处置**：`ledger-io.mjs:145` 把 `body.length < anchor.byteLength` 记成 `SHORT_TRUNCATED`，然后只做一件事——把 `anchor.lastCompletion` 指的那**一个**周期降级（`:176`）。而实际被截掉的是两个完整周期，它们的 slot 本来就是 `ABSENT`，**降级是空操作**。降级逻辑只在"丢掉 completion 保留 intent"这一种形状下有效（实测：保留 14/15 条时 `runCycle(cycle 3)` = `DUPLICATE executed=false`，正确）。**两种截断形状都报 `SHORT_TRUNCATED`，行为却差一个数量级。**
2. **锚点缺失 = 防护消失**：`:144` 是 `if (anchor && typeof anchor.byteLength === 'number')`。锚点文件不在了，整段比较直接跳过，`mode` 保持 `CLEAN`。**删掉一个文件就能关掉一道控制，且系统自称健康。**
3. **证据被洗掉**：`:194` 的条件是 `!exists(anchorPath) || mode !== CLEAN || byteLength 不等 ⇒ writeAnchor(...)`。于是 B 场景在下次写入时把锚点**改写成截断后的长度**（3192/12）。此后无论谁来看，账本与锚点完全自洽，**没有人能再发现少了两个周期**。这比"没检测到"更坏：它把"曾经能检测到"也一并消除了。

### (d) 为什么这不是"K/M 没给数"的问题

`#22` 把这一半挂在我给 `K`/`M` 上。**K/M 不是这里的瓶颈**，理由是：
- 同长度原地篡改**不会**改变内存里的已验索引（`st.idx` 由上次全量重验的字节构建），所以宽窗口的后果是**发现延迟 + 往坏链上继续追加**，**不是**重跑；窗口的危害等级因此是 P2，不是 P1。
- 截断**改变字节长度**，`ensureFresh()` 的 `statSync` 是 O(1) 立即发现的。**发现得很快，处置错了。**
⇒ 先把 `SHORT_TRUNCATED` 改成 fail-closed，`K=256 / M=256 KiB` 的默认值可以原样保留。

**K 仍有两处要修（不阻塞，但会静默改变保证）**：
1. **单位不一致**：`K` 同时被当作"读次数"（`:240` 的 `readsSinceVerify`）和"记录条数"（`:251` 的 `entries.length - termStartCount`）。保证的口径会随"热路径多一次读"而变宽——这正是"声明跟着代码形状漂移"。
2. **建议改成以周期计量**：`reverifyEveryCycles`（例如 8），内部再派生读次数/条数/字节数（8×readsPerCycle、24 条、8×3×4096 B）。操作者能理解的单位是"多少周期内会被发现"，不是"多少次读"。

### (e) 最小整改（按顺序）

1. **`SHORT_TRUNCATED` ⇒ 抛 `LEDGER_SHORT_TRUNCATED`**，不得继续。理由：锚点只知道"少了多少字节"，不知道**少的是哪些周期**（`{count, byteLength, headHash, lastCompletion}` 不足以重建缺失期间的 `(runId, cycle)` 集合）。在不知道的集合上放行重跑，就是把 at-most-once 交给运气。
2. **`anchorPresent === false` 且账本非空 ⇒ 抛 `LEDGER_ANCHOR_MISSING`**，不得当作 `CLEAN`。首次启动（账本也为空）才允许无锚点。
3. **`writeAnchor` 不得在 `SHORT_TRUNCATED` / `ANCHOR_MISSING` 的路径上执行**——否则就是在销毁它自己刚产生的证据。
4. 若确实想要"自动恢复"而不是 fail-closed，则锚点必须携带**有界**的已完成周期清单（`completed: [{runId, cycle}]`，按 `K` 窗口裁剪），届时把"缺失期间的所有周期"一律标成 `IN_DOUBT`，再走 `resolveInDoubt`。**这条路径的前提是第 1 条先落地**：先 fail-closed，再谈自动降级。
5. 用例（各一条，且必须**只红该条**）：
   - `㉑a` 整周期截断 + 锚点在 ⇒ `LEDGER_SHORT_TRUNCATED`（当前会重跑）
   - `㉑b` 截断 + 删锚点 ⇒ `LEDGER_ANCHOR_MISSING`（当前报 CLEAN）
   - `㉑c` 截断被拒后，锚点字节**必须仍是原值**（证明没洗证据）
   - 保留现有的"只丢 completion"用例并明说它覆盖的是**另一种形状**——两者不能互相代替。

### (f) 定级与对裁定的影响

**P1。** 但它**不改变**上面的裁定，因为 `ledger-io.mjs` **未跟踪、未提交**——当前没有任何提交包含它。它的正确处置是：**挡住落地**，而不是追加一条已发布代码的 blocker。若它被提交成现状，则升级为 **P0 级**（`LEDGER-ATOMIC` 的直接破口），Phase GO 与安全域都须重新表决。

**本轮我同时验证并撤回了两个自己的假设**（写下来是为了让下一个人少走）：①"crash 落在 append 与 writeAnchor 之间 ⇒ 永久 `LEDGER_TAMPERED`"——**不成立**，`:147-159` 用 `ADOPTED_TAIL`（尾巴是否链到锚定 head）正确处理了"账本比锚点长"；②"`writeCallCount()` 可以兜 ⑲f"——**不成立**，见 §12.10.15(j)。

---

## §12.10.17 FINAL-10 独立验证（截至 `0bb240b`）：判据对"这份证据是不是这一轮的"**零灵敏度**（P0，VERIFIED-BY-EXECUTION）

> **本节只对 `0bb240b` 的已提交字节成立。** 主理人指出：同一时刻**工作区（未提交）**harbor2 已在改——新增 `_final10_judge` 并绑到 `FINAL-10`、`reboot_recovery_sim.py:278` 加 `os.remove(OUT)`、`_judge_artifact` 加 GATE 1/GATE 2；`git show 0bb240b | grep _final10_judge` 为空。
> ⇒ **下表 M0–M7 对在途字节未验、不作任何断言。** 落地后我按新 SHA 追加新一节，不改本节。


### (a) 方法（有意不 import）

`tools/jcc_continuation.py` 是带 `main()` 的 CLI，import 它有副作用风险（本仓已有 `v16_independent_verify.py` 一 import 就重写 evidence 的先例）。故：**用 `ast` 把 `_ok_judge` / `_judge_artifact` 的真实源码段抽出来 `exec`**，只补 `rel/load_json/sha256_file/os` 四个 stub，然后喂构造证据。
**这比"照着源码重写一遍"强：跑的是被提交的那段文本。** 锚点：`0bb240b`，`artifacts/acceptance/REBOOT_RECOVERY.json` 的 blob 由 `git ls-files --error-unmatch` 确认为 **tracked**。

**真实证据字段（原样读出，未经加工）**：
```
release_sha = cefed66159a50ea8bcba5279d4a95cc5b726036d
checked_at  = 2026-09-26T22:18:43+08:00
platform    = None            ← 字段不存在
ok = True    status = PASS
git rev-list --count cefed661..HEAD = 339
```

### (b) 七条变异：**只有两条被挡住，五条是绿的**

| 变异 | 判定 | 说明 |
|---|---|---|
| **M0 原样真实证据**（陈旧 339 提交 + 无 platform） | **PASS** | 就是现实 |
| M1 `release_sha` 换成当前 HEAD | **PASS** | 换 sha 不改变判定 |
| M2 补 `platform=win32` | **PASS** | platform 不参与判定 |
| M3 `checked_at` 改成 2019 | **PASS** | 时态不参与判定 |
| M4 `rc=1` 但旧证据在 | **PASS** | `_judge_artifact:918` 分支只看 `step.result`，**从不读 `record["exit_code"]`** |
| **M5 证据文件不存在** | BLOCKED | 唯一被挡住的"缺失"形状 |
| M6 `ok=false` | BLOCKED | `ok` 字段有效 |
| **M7 启动失败（`rc=None`，rc 上下文写 `launch failed`）** | **PASS** | 见 (d) |

⇒ **"五条变异各只红一条"这个验收条件不成立，而且失效方向是最坏的那个：不是"红错位置"，是"根本不红"。** 判据能证明的集合只有"文件存在且 `ok is True`"这一项，比它看起来小得多。

### (c) 决定性的那条：**`sha256` 就在手上，判据不看它**

`_judge_artifact:933-937` 会算证据的 `sha256` 与 `bytes` 并写进 `record["evidence"]`——**算完从不与任何值比较**。实测四份内容不同的证据：

```
M0 原样        evidence.sha256=2124f5acd98f   → PASS
M1 换 sha      evidence.sha256=7262c88f2923   → PASS
M2 补 platform evidence.sha256=ba4f9ce82bf3   → PASS
M3 改 checked_at evidence.sha256=111d7da2d46a → PASS
```

**哈希在变、判定不变**——这就是"对时态零灵敏度"的直接证明，也是**本仓第四次出现同一形状：产出了一个能判定的量，然后不用它。**（前三次：`GATE_FLOOR_OK` 无作者字段、`git cat-file -e` 只证存在、`writeCallCount()` 看不见 `writeFileSync`。）

### (d) 比"陈旧"更坏的一条：**启动失败被判 PASS，且把失败原因覆盖掉**

```
输入：record = {"exit_code": None, "detail": "launch failed: [WinError 2] python not found"}
输出：status = PASS
      detail = "PASS: a simulated reboot (dead pid in a lock that had not expired, RUNNING ..."
      "launch failed" 是否还在 record 里 = False
```

`_judge_artifact:932` 是 **`record["detail"] = detail`（无条件覆盖）**。于是 `:1004-1006` 辛苦记下的 `launch failed` / `timeout` 被判定结果顶掉——**"这一步没跑"变成"这一步通过"，且证据指向通过。** 这已不是"陈旧证据被当现在时"，是**"没发生的运行被写成发生过"**，与 `52a6c9b` 的提假信息同一形状。

### (e) `_artifact_fresh`：**已有，且从未用于 step 结果**

```
:902  定义
:1047 / :1054 / :1158  三个调用点，全部是 run descriptor 的 attach 恢复
```
⇒ 结论两条：①**整改必须复用它，不得新写第二个新鲜度判据**（两套将来必漂移）；②它现在的缺席不是"忘了写"，是**"写好了但接给了另一条路径"**——比没有更危险，因为 `grep` 会让人以为已经覆盖。

### (f) 覆盖范围：`tools/continuation/**` 不在任何 CI 里

```
$ for f in .github/workflows/*.yml; do grep -c "continuation\|jcc_continuation\|reboot_recovery" "$f"; done
  （全部 0）
```
⇒ **"126/126 覆盖重启恢复"是假陈述。** `126` 是 8 个 supervisor/P0/P3/body-read 套件的断言数，与 `FINAL-10` 无关。**任何"已验收"清单必须把 crash/restart recovery 摘出去**，本报告全文按此口径修正：`B2b`（Windows 重启恢复）**仍是零证据**，现在更准确的说法是——**它的判定器本身 fail-open，所以"零证据"和"假证据"同时存在。**

### (g) 整改与验收（我签这份整改的条件）

1. `_ok_judge` 必须至少读三项并与本轮上下文比对：`release_sha == 本轮 SHA`、`checked_at` 落在本轮时间窗内、`platform` **存在**且与实跑平台一致。任一缺失 ⇒ **BLOCKED，不得 SKIPPED-through**。
2. **`record["detail"]` 不得被判定结果覆盖**：失败原因必须保留（`detail` 追加而非替换），否则证据链在"通过"的那一格断掉。
3. `rc` 必须参与：`rc is None`（启动失败/超时）⇒ **BLOCKED**，不得进入 artifact 判定。
   **补一层（主理人在未提交在途代码里发现同族第二个口子）**：在途版本写的是
   ```python
   rc = record.get("exit_code")
   if rc is not None and rc != 0 and step.id not in EXIT_CODE_ALLOWLIST:
   ```
   **`rc is None` 时整条跳过。** 而 `None` 不是"没有退出码所以无害"，是**这一步没正常结束**（launch failed / timeout / attach）。判据应改为 `rc != 0` 即 FAIL、**`None` 计入**。
   ⇒ **M7（启动失败）必须单列成一条用例**，不能并进 M4（`rc=1`）：两者走的是不同分支，只测 M4 会漏掉 `None` 这一个值。

4. 复用 `_artifact_fresh`，禁止新写第二套；若复用语义不匹配，改它、不复制它。
5. `artifacts/acceptance/REBOOT_RECOVERY.json` **必须移出 tracked**（`0bb240b` 上仍是 tracked）。tracked 的 PASS 文件是可被无限引用的过去时。
   **状态增量（不改本条）**：工作区已删（`git status` 显示 ` D artifacts/acceptance/REBOOT_RECOVERY.json`），未提交。删对方向，但**"删除"本身不构成关闭**——关闭条件是"该文件不再出现在任何提交里 + 判定器不再接受陈旧证据"。只删文件、判定器照旧，任何一次 `git checkout` 就能把它捞回来。

6. **整改当天 `FINAL-10` 必须是红的**。若同一提交里它变绿，且伴随一份新写的 json，按"为变绿而补证据"处理——这是本轮唯一一条不能靠代码审查发现、只能靠时序发现的行为，必须写进验收记录。
7. 变异证据（各只红一条）：`M1/M2/M3/M4/M7` 五条**现在全是绿**，整改后必须全红；`M5/M6` 保持红。**这五条是本节的验收主体，不是附带项。**

**定级 P0，与 `B2b` 并列**：它不是"覆盖不足"，是**判定器 fail-open**，而 `B2b` 本来就靠这个判定器取证。两者叠加后，P0–P2 门禁里"Windows 重启恢复"这一条从"未验证"变成"用错误方法被认为已验证"。

---

## §12.10.18 `#15` 两条前置**已在工作区关闭**（截至未提交 blob，只验了其中一条的验收判据）

按 append-only 纪律，**不改 §12.10.15(n)**，此处新增。锚点：**工作区（未提交）**，`HEAD` 仍是 `0bb240b`。

### (a) 两类数分开写（主理人硬限制）

> **报告只允许出现两类数：① 已提交字节上的读数（必须带 SHA）；② 未提交工作里的缺陷机制（不带任何计数）。** 理由：`PASS` / `expect` / 块数这类数字若不绑提交，既不是 `HEAD` 的状态，也不是锁存那一刻之外任何时刻的状态，下一个读者无法复算——**那是"工作区绿"在文档里的等价物。**

**① 已提交字节上的读数（`0bb240b`）**：把该提交的 `scripts/supervisor/` 10 个文件导出到临时目录，先逐文件比 blob（`git rev-parse 0bb240b:<path>` vs `git hash-object <导出文件>`）——**10/10 完全一致**，再跑：

```
RESULT PASS=25 FAIL=0
```

`gate-assertion-floor.mjs` 在 `0bb240b` 上 `6-supervisor expect: 25`，与同提交的 `await test(` 计数 25 相等。**这是本节唯一可引用的数字。**

**② 未提交工作（不给计数，只给机制与 blob 标识）**：
```
supervisor.mjs      07276330bfb6        ledger-io.mjs       b2a6fce0a945
core.mjs            29277ff4eff8        idempotency.mjs     fb70ba6b753f
index.mjs           54831a718e24        resume.mjs          45c9c5828902
watchdog.mjs        c995df675317        leader-election    a0410c6a4437
kill-switch.mjs     f51cf3dad7fb        cost-monitor       24fffc915359
supervisor.test.mjs 4640312be5b3
```
**这些 blob 只用来指认"我看的是哪份字节"，不承载任何结果数字**——它们随下一次编辑即失效，任何引用都必须重新取证。

### (b) 前置 ① `⑲f`：**验收判据满足，我实测的**

我当初立的条件是"我构造的 `watchdog.beat` 变异**必须只红 ⑲f**（它此前是全绿）"。在上面的拷贝上重跑同一变异：

```
变异：在 if (state === 'IN_DOUBT') { 之后、return 之前插 watchdog.beat({runId, cycle})
  BEFORE sha256_12=5bfa10b01605  hits=1
  AFTER  sha256_12=e9d2654ae5c2  changed=true
变异体整套：
  FAIL  ⑲f 崩溃在 intent 与 completion 之间：重启判 IN_DOUBT，绝不重跑周期体
  RESULT PASS=32 FAIL=1
```

⇒ **从"全绿"变成"只红一条，且红在正确的那一条"**。这一条同时解决了两件事：判据有了牙，且 `⑲f` 第一次拿到了自己的单红定位证据（此前只有 `M-in-doubt-is-run` 的双红，只能算非恒真证据）。

> **撤回（append-only：不改上文，只追加）**：上一段的 `RESULT PASS=32 FAIL=1` 属**未提交工作**的读数。按两类数规则（① 已提交字节带 SHA；② 未提交只写机制、不带计数），该计数**作废、不得引用**；结论（该变异现在红在 `⑲f`，其余用例保持绿）保留。
> **我曾把这段就地改写以删掉计数——那违反 append-only，已恢复原文并改为追加。** 记账见 §12.10.20。

### (c) 前置 ② 负对照锚：**已换为逐字原文 + sha256 锁，形态比我要求的更严**

工作区实测（未提交）：
- `supervisor.test.mjs:399-410` 引入 `LEGACY_FS_SOURCE`（**逐字原文**）与 `LEGACY_FS_SOURCE_SHA256 = 'df6f2cf0…'`，注释写明取自 `d1fc94f`（= `dbd6368^`）并给出复现命令（`git show <c>:<path> | sed -n … | sha256sum`）；
- `:466` 断言 `sha256(LEGACY_FS_SOURCE) === LEGACY_FS_SOURCE_SHA256`，失败消息要求"若要换锚，先给出新提交的 `git show` 输出与新的 `sha256sum`"；
- `:470` 用 `new Function` 从原文求值，**不再 `import` 生产模块的复述**；
- `core.mjs:205` 只剩一句注释提到 `createLegacyStringFs`，**导出已移除**（`grep -c 'export function createLegacyStringFs'` = 0）。

⇒ 这满足 C34（锚点是先于会话存在、可复现的常量），并且**堵住了负对照的唯一失效方式**："调整对照直到它变绿"现在会先撞碎 sha256 断言。

### (d) 附带关闭：`ledger-io.mjs` 已在静态扫描范围内

`supervisor.test.mjs:9-14` 的手写九人名单改为**目录枚举 + 数量断言**，并在注释里写下了失效机理（"守卫的 SCOPE 靠手维护，就会悄悄停止覆盖它本该覆盖的东西"），与 `verify-engine-vendor 命中 0`、`第 9 套件不存在` 并列为同族。我要求的"数量断言用相等不用 `>=`"仍是待核项——**我这次没读到那行断言，故此项记为 UNVERIFIED-BY-ME，不写成已关闭。**

### (e) 仍然 OPEN 的（不改旧节，此处只做状态增量）

1. **`ledger-io.mjs` 截断 fail-open（§12.10.16）**：**未验是否已改**。我最后一次锁定该文件是 `b2a6fce0`；`SHORT_TRUNCATED ⇒ 抛` / 锚点强制 / 禁洗证据三条，我**没有在上面的拷贝上重跑重复副作用场景**，所以不给结论。
2. **FINAL-10（§12.10.17）**：仍只对 `0bb240b` 成立；工作区已在改，未验。
3. **`M7` 单列、`rc is None` 计入 FAIL**：已写入 §12.10.17(g)，工作区未验。

**本节的作用是把"我拒签的两条"从"意见"变成"可复核的状态"**：两条前置各自都有我实测的判据，而不是我说"可以了"。**未提交 ⇒ 不改变 §12.10.15(n) 的 OPEN 结论；提交后按新 SHA 追加新节。**

---

## §12.10.19 `#8` FINAL-10 修复：**独立验证（部分）**——主干成立，但"缺席即放行"仍有两处

锚点：**工作区未提交**（`tools/jcc_continuation.py`、`tools/continuation/reboot_recovery_sim.py`、新增 `apps/command_center_3d/scripts/tests/final10-judge.test.mjs`）。按 §12.10.18(a) 的两类数规则，**本节不写未提交工作的结果计数**，只写机制与我在源码里读到的事实。

### (a) 我执行的（VERIFIED-BY-ME）

1. **新判据套件可跑且全绿**：`node apps/command_center_3d/scripts/tests/final10-judge.test.mjs` → 末行 `RESULT PASS=… FAIL=0`（**计数按规则不写**），末两条为 `ALLOWLIST_EMPTY`、`STEP_USES_FINAL10_JUDGE`。
2. **该套件的做法是对的，且是我要求的形状**：注释明写"**WHY IT SPAWNS PYTHON INSTEAD OF RE-IMPLEMENTING THE RULES** … Re-writing its rules in JS would test the copy, not the code — two implementations agreeing is evidence about the author, not about the target." 子进程 import 真模块、调真 `_judge_artifact`，JS 只断言返回。**这正是 C34 与"不要重实现"两条的同一次落地。**
3. **`python` 缺失时必须红而不是 SKIP**：注释写了、代码里也确实非零退出。这条与 F-26 正面相对。
4. **既有 1924 行 python 套件：UNVERIFIED-BY-EXECUTION，我自己确认的**：
   ```
   $ python -m pytest --version          → No module named pytest
   $ [ -x .venv/Scripts/python.exe ]     → NO_VENV
   ```
   所以我**没有**"整个 continuation 子系统没被碰到"的任何证据；我只有静态旁证（下条）。
5. **静态旁证（不等于测试通过）**：`tools/tests/` 里唯一直接断言 `exit_code` 的地方是 `test_continuation_infra.py:296`，其语境是「退出码 0 + **未写 artifact** ⇒ `BLOCKED`，且 judge 未被咨询」——**它编码的是"rc=0 且无证据 ⇒ 阻断"，与新门的语义兼容，不是旧 fail-open 的编码。**残留风险因此比我原先估计低，但**仍是"读代码判断"，不是"跑过"。**

### (b) 修好的部分（我在源码里逐行读到，不是采信）

- `EXIT_CODE_ALLOWLIST: Tuple[str, ...] = ()`，且 **`if EXIT_CODE_ALLOWLIST: raise RuntimeError(...)` 在 import 阶段**——白名单一旦非空，整条判定链不加载。这比我要求的"默认空"更强：**它是 fail-closed 而不是 default-empty。**
- GATE 1 用的是 `rc != 0`（不是 `rc > 0`，也不是 `rc is not None and rc != 0`）⇒ **`None`（launch failed / timeout）计入 FAIL**。这正是 §12.10.17(g) 第 3 条要求的那一个值。
- `_carry_detail(record, …)` 保留原有 `detail` ⇒ 直接回应 §12.10.17(d) 的"失败原因被覆盖"（P0）。
- GATE 2 复用 `_artifact_fresh`，**没有新写第二个新鲜度判据** ⇒ 满足"复用而非复制"。

### (c) 仍然存在的两处「缺席即放行」（新的，P1 候选）

**1. GATE 1 可以被"键不存在"关掉**

```python
rc_present = "exit_code" in record
if step.script and rc_present and rc != 0 and step.id not in EXIT_CODE_ALLOWLIST:
```

`rc_present` 的语义是"记录里有这个键才检查"。而 `attach_to_run` 构造的 `record` **不含 `exit_code`**（我读了该函数的 record 构造），于是**长驻步骤走 attach 路径时 GATE 1 整条跳过**。
这与我在 §12.10.16(c) 打的那处是**同一形状**：`if (anchor && …)` —— **删掉/不填一个字段就能关掉一道控制。**

**2. GATE 2 有三个宽松出口，其中两个是"不知道 ⇒ 通过"**

```python
if not since_iso:            return True      ← 缺失 ⇒ 新鲜
try:    since = fromisoformat(...)
except ValueError:           return True      ← 不可解析 ⇒ 新鲜
```

而 attach 路径的 `since` 来自 `desc.get("started_at")`：**descriptor 缺该键 ⇒ `since_iso` 为空 ⇒ 返回 True**。

⇒ **在"attach + descriptor 无 `started_at`"这一个组合上，GATE 1 与 GATE 2 同时关闭**，判定强度退回 `_ok_judge` 时代。harbor2 已把 attach 路径登记为"已知缺口"，但**定性可以更准**：它不是"少测了一条"，而是**两道门同时缺席**——单个门缺席还能被另一个兜住，两个同时缺席就是没有门。

### (d) 最小整改（一句话规则）

> **"不知道"必须判 FAIL，不能判 PASS。**

具体三处：
1. `rc_present` 反号：`exit_code` 缺失 ⇒ **FAIL**（`EXIT_CODE_UNKNOWN`），而不是跳过。若要为 attach 例外，必须**显式传 `rc_expected=False`**，让例外出现在调用点、可被 grep 到，而不是由"键恰好不在"隐式触发。
2. `_artifact_fresh` 的两个 `return True` 改为 `return False`（缺失/不可解析 ⇒ 不新鲜），失败消息区分 `FRESHNESS_SINCE_MISSING` / `FRESHNESS_SINCE_UNPARSABLE`。
3. attach 路径的 `since` 取 `desc["started_at"]`（**下标，不是 `get`**），缺键即 `KeyError` → 转成带解释的 FAIL。理由：descriptor 是**我们自己写的**文件，缺 `started_at` 说明写 descriptor 的代码坏了，不是"这一步没有开始时间"。

### (e) 我这次没做的（明确边界，不冒充）

- **既有 1924 行 python 套件没跑**（无 pytest），任何人引用"`#8` 无回归"都必须注明这一点。
- **`M1`–`M5` 变异矩阵我没有逐条复现**。我验的是"新套件跑得起来且全绿 + 源码里的门确实存在且方向正确 + 两处缺席即放行"。**需要方原地实测的 5 条，仍按需求方口径记，不并入我的签署范围。**
- **`#8` 我不给通过**：`B2b` 的关闭条件是**在候选 SHA 上跑出真实重启证据**，而这条修复只是让"判定器不再接受假证据"。**判定器修好 ≠ 证据存在。** 这条必须写在 `#8` 的交接里，否则下一个读者会把"判据修好了"读成"重启恢复已验证"。


---

## §12.10.20 三条判定 + 一次我自己的 append-only 违规（已恢复）

### (a) 我的 append-only 违规：就地改写已提交段落

主理人把报告入库（`HEAD` 上 2694 行）之后，我为执行"未提交工作不带计数"的规则，**把 §12.10.18(b) 就地改写**，删掉了已入库的 11 行（含 `RESULT PASS=32 FAIL=1` 那段）。**这是 append-only 违规。**

处置：已用脚本从 `git show HEAD:` 取回该段**逐字原文**，恢复后再**追加**一个撤回块声明该计数作废。现 `git diff --numstat` 为 **`74 0`**——零删除。

**教训（与我一直在打的同一件事）**：我把"规则要求删掉这个数"读成了"可以改这段文字"。**规则约束的是'能不能被引用'，不是'历史能不能被编辑'。** 正确动作只有一个：**原文不动，追加一条撤回**。这条同时说明了为什么"边改边提交"必须停——它会让 append-only 在我手里变成一句口号。

### (b) `M-recovery-error-swallowed`：**条件等价，不得永久登记**

harbor 在 `cycleState` 外套 try/catch 吞掉恢复错误，实测仍全绿，判为等价变异。**我判：成立，但只在一个前提成立时成立，因此不得登记为永久等价变异。**

等价性论证（他的）：拒绝有第二条防线 —— 被吞掉之后，`recordLeaseEpoch → append → ensureFresh → open()` 会第二次抛，所以副作用仍不会发生。

**这个论证有效，因为它依赖的是"每一次通往 `cycleImpl` 的路径上都有一次重新读取"。** 而这恰好是**游标优化最容易删掉的东西**：游标把全量重验从"每次写"挪到"窗口到期"，若某天 `resume` 也改走游标且不打 `open()`，那条"第二道防线"就不再是防线，**这个变异会在没人改动它的情况下从等价变成应当红**。

⇒ 裁定：
1. **按 C33 归为"等价（有行为等价论证，合法）"，允许它保持绿；**
2. **不许写成"等价变异，永久豁免"**——必须写成 **"条件等价：前提是每条通往 `cycleImpl` 的路径都重新读取。前提变更时本条必须重判"**；
3. 前提要落成断言而不只是注释：**在 `cycleImpl` 入口处断言"本次周期已至少全量验证过一次"**（用 `stats.fullVerifies` 的增量或一个 token），这样前提一旦被删，红的是一条具名断言，而不是一个"看起来仍然绿"的旧变异。

### (c) `P2-LEDGER-ANCHOR-NOT-A-COMMITMENT`：**我判 P1，应当现在加**

他登记的缺口：**锚点只约束长度，不约束内容**——整条链重算、字节数不变的重写，能同时过 `verifyChain` 与锚点。

**为什么不是 P2**：
1. **值已经在手上**：`anchorValid` 已经要求 `typeof headHash === 'string'`，也就是说锚点里**已经存了 `headHash` 和 `count`**，只是从来没比较过。这是本报告第四次"算出来/存下来却不用它"的同一形状（前三次：`GATE_FLOOR_OK` 无作者字段、`git cat-file -e` 只证存在、`evidence.sha256` 算完不比较）。
2. **成本是一行**：长度相等时断言 `entries.length === anchor.count && headHash === anchor.headHash`，不相等时走既有的 `ADOPTED_TAIL` 分支。
3. **它落在篡改模型的核心上**：能写文件的人**同样能重算整条链**（链只是对内容的哈希，不是签名）。所以"锚点不绑内容"等于**只防截断、不防重写**，而重写的后果与截断同级——把某个周期的 `completion` 改掉，`cycleState` 就回到 `ABSENT`，重跑。**这与 §12.10.16 的 P1 是同一条杀伤链的另一半。**

**判据（新增一条用例 `㉔`）**：
- 构造"等长、整链重算自洽、但某一个周期的 `completion` 被换成 `intent`"的账本 ⇒ 必须 `LEDGER_ANCHOR_HEAD_MISMATCH`（新错码）；
- 对照：**长度不等但尾巴链到锚定 head** ⇒ 仍走 `ADOPTED_TAIL`，**不得误红**（否则崩溃恢复会被这道门打死）；
- 变异：把 `headHash` 比较注释掉 ⇒ **只红 `㉔`**。

### (d) 他对 §5 的处理我接受（不改就是对的）

`ledger-io` 里没有"周期"概念，硬引入会让窗口声明依赖上层调用形状——这正是我指的那个病换个方向。他以**具名 + 单位写进名字 + 一个数字三处具名用途**解决，并给了换算（256 ops ≈ 36 周期；1 MiB ≈ 2700 条）与"字节界会先撞"的结论。**这比我建议的"按周期计量"更好**，因为它把单位写在名字上而不是依赖注释。记 `K=256 / M=1 MiB`（主理人裁定的值，非我先前写的 256 KiB）。

# JEV_V2_SECURITY_SIGNOFF.md — V2.0 收口轮（T05）Security 签收单

> 出具人：security-auditor（team `jev-v2-closing`）
> 日期：2026-11-11
> 性质：**只读安全复核的签收单**。本文件为独立产物，与 `reports/JEV_REAL_TRADING_SECURITY.md` **§11** 互为索引；详细证据、命令、边界全部以主报告 §11 为准，本单只做**结论收敛**，不新增未在主报告记载的证据。
> **不替任何人签字**：不替 `independent-qa`、不替实现方（trading-engineer）、不替 team-lead。P0 整体门禁由 team-lead 整合。

## 0. 一句话结论

**Security DECISION = `GO WITH CONDITIONS（仅限 Security 范围）`。**
八条点名项 **8/8 达 VERIFIED**（每条均标注适用边界），**无条目级 NOT_VERIFIED**；但 **C9（verified 语义须绑定真实 egress）是 P3 解锁硬前置**，另 **3 处**子命题明确为 UNVERIFIED。**本结论不构成 P0 整体 PASS、不构成生产 GO、不构成"可上真实资金"的授权。**

## 1. 版本锚点（本轮独立实测，逐字一致）

| 项 | 值 |
|----|----|
| 分支 / HEAD | `feature/v16-p0-gap-closure` / `fa7740f2ab8be973b8407ed68d5e3514e034a2e3` |
| 本地提交（未 push） | `5834c37` / `c0cad25` / `fa7740f`（远端基线 `ebc3fcc`） |
| `day14-runner.mjs` | `002653fed0d48e3cd20c95a397400d93830192ca87f60a8361a88b3aa3726d4a` / 829 行 |
| `server/ai/risk-gate.mjs` | `5797ba8cd6308e11c75c53121f151402e872ec1f1551780eb6953d60c992278f` |
| `server/ai/decision-schema.mjs` | `328b7a9b140069d354e9b06a08d85dca79ed47f3394acb8fd43c031be3184ba1` |
| `server/engine/_lib/paper-server.mjs` | `f9ac6e142446ef2aa8deb928f2b129eb2d42f4227d1bc22469191087312e4d74`（vendor 副本同值） |
| `reports/day14-artifacts/config.freeze.json` | `d4edce64c48bf15ab6803ce9ea08a164404d84642c61065d74550d7983208658` |
| 运行时 | `node v22.22.2`；复核开始时 `node_modules` 缺失 → 当时 `tsc`/`vitest` 不可执行。**后已更正（§11.8.1）**：`apps/command_center_3d/node_modules` 现存在，`tsc --noEmit` **exit 0**、`vitest run` **22 文件 / 217 通过 / 3 skipped / exit 0** → 类型检查 **VERIFIED**（限**工作区**态；`vite.config.ts` 当时为未提交改动，HEAD 态行为属 T04） |

## 2. 八条签收

| # | 项 | 裁定 | 关键证据位置（主报告 §11 内） |
|---|----|------|-------------------------------|
| 1 | 正式 Paper 路径不允许简化风控放行 | **VERIFIED** | §11.1.1 — 静态：runner §B / L143-149 / L149 / L561-562，全仓 `fallbackRiskGate` 仅注释、`console.warn`=0；行为：loader 注入导入失败 → `FATAL ... fail-closed` exit=1 且**零产物**，`riskCheckImpl=null` → throw，注入「永远放行」门 → 仅 `SHADOW_GATED` 且全局 `fetch`=0 |
| 2 | Real Market / Synthetic Fixture 隔离 | **VERIFIED**（系统生成与落盘路径）；抗蓄意伪造 = **UNVERIFIED** | §11.1.2 — `provenance-guard.mjs` 七项+一致性矩阵+`FIXTURE_MISLABELED_LIVE`+哈希信封；三 store 写前强制；P3 四臂 `evidence_level` 硬编码；**F-16/C9** |
| 3 | Real AI / Mock Inference 隔离 | **VERIFIED** | §11.1.3 — `providerId`/`isReal`/`dataMode` 硬编码字面量；层内不 import runtime/provider-adapter；`DECLARED_NOT_WIRED`；真实 provider 能力面**如实披露**；**F-20** |
| 4 | HOLD 与 NO_ACTION 区分 | **VERIFIED**（现行 runner 路径） | §11.1.4 — schema 白名单不含 `decisionClass`；`RISK_HOLD_NO_ORDER` ≠ `RISK_APPROVED`；provider 失败 → `NO_ACTION`+`PROVIDER_FAILED_FAILCLOSED`（行为实证）；**F-18/F-21** |
| 5 | 订单幂等 / 账户隔离 / 权益对账可重放 | **VERIFIED**（本地 D1 shim）；云 D1 = **UNVERIFIED** | §11.1.5 — collector `t2/t4/t5` 独立实跑 PASS（计数+哈希+唯一性+反向反例）；AI 侧 `t4` 8 项 PASS |
| 6 | 模型 / 交易 / 运营成本分别核算 | **VERIFIED** | §11.1.6 — 双口径 null/NOT_MEASURED 保持；`assertNoMerge` 三类抛错；P2 `actualInferenceCost` 恒 UNVERIFIED；supervisor 三列分列；**无合并/回填代码** |
| 7 | 真实交易开关始终 false | **VERIFIED** | §11.1.7 — `RED_LINES` frozen 且不在 opts 解构；runner 不读 `process.env`；cloud-entry / `[[path]].js` 仅 `"false"` 放行；collector 用**白名单**；freeze 同值；**`realMarket:true` 为误导标签非开关** |
| 8 | 不允许未经批准连接正式生产资金 | **VERIFIED** | §11.1.8 — 资金原语 0 命中；成交唯一指向冻结纸面引擎（`real_money:false`）；`trading_api.mjs` 仅只读路由；未检索 `ReferenceInput` |

## 3. 敏感信息扫描（独立复核，与主理人扫描互补不互代）

| 检查项 | 结论 |
|--------|------|
| `config/.env.example` | **无真实值**（全空模板） |
| `config/.env`（本地） | **存在 7 个非空真实风格凭据**（`DASHSCOPE_*` / `JEV_API_KEY` / `MINIMAX_API_KEY` 等，**仅键名，未输出任何值**）；**未跟踪 + gitignore 生效 → 不随推送泄漏** |
| 凭据外泄比对 | 1649 个文件（全量 tracked + 三个 scratch 路径）逐一比对 **5 个真实值** → **命中 0** |
| 本轮新增文件 | 实测 **50 个新增**（主理人计 45，以 git 为准）；硬编码密钥模式 **命中 0** |
| 全仓密钥模式命中 | 全部为**合成哨兵 / 文档示例**（`sk-live-*SENTINELdeadbeef*`、`AKIAIOSFODNN7EXAMPLE`、`sk-deliberately-invalid-key-for-e2e` 等），**无真实凭据** |
| scratch 目录 | `_verify/` 391 文件 1.5 MB + `runs/` 10 文件 84 KB + `active-run.json`：内容为 `fixture-verified` 合成快照与纸面账户（`cash:10000.00000000` / `orders:0`），**无真实凭据/账户标识**；敏感词命中全为 `"formal-risk-gate"` 中 `sk-` 子串的**假阳性** |
| 推送泄漏面 | 三个 scratch 路径 **未跟踪且未被 ignore** → 常规 push 安全，但 `git add -A` 会带入 401 文件（**F-19 / C11**，无机密，属流程卫生） |

## 4. 剩余开放条件

**已满足**：C1（R6 外部调用书面确认到位 / 触发条件未成立）、C7（四项源码指纹逐字未漂移；仅「未 commit」子句已过时）。
**未闭环**：C2（独立凭据域 / P9 DESIGN ONLY）、C3（F-15 注入面，**并入新 F-17**）、C4（F-14 风控参数口径）、C5（F-07 能力面）、C6（F-09 覆盖写残留，low）、C8（不越权边界，持续保留）。
**新增**：

- **C9（P3 硬前置，medium）**：`verified` / `mark_quality` 语义必须绑定**真实出网证据**（`networkContext` 排除 `local_test_shim`）。**未解决前禁止解锁 `allowBuySell`** —— 否则正式门会以 **fixture 价格**放行真实下单。
- **C10（low）**：统一跨层 `decisionClass` 词汇；记录层禁止「仅按 `action` 判定 HOLD / NO_ACTION」。
- **C11（low）**：临时运行产物 ignore 化 / 提交白名单化。
- **C12（medium，owner = devops / independent-qa）**：证据门完整性。① `t1_schema`…`t9_gateway_routes` **无主模块守卫**，单独 `node <file>` = exit 0 / **0 断言**（本轮 9/9 实测）；② `server/ai/tests/run-all.mjs` 的 PASS 判定**无最小断言下限**，`0 checks` 也输出 `=> PASS`。**当前未兑现**：现行 CI 的 7 个 gate 我逐个实跑，**全部有真实断言且失败非 0**，且**无一步单独执行 t1–t9**（只用聚合入口）。另附：`inference-evidence.test.mjs` 向仓库内写 `_p2_chain_tmp.jsonl`（未 ignore、无收尾清理），建议一并改 `os.tmpdir()`。样板修法见 `equity_collector_v45/tests/run-all.mjs`（`PASS_NO_RESULT_JSON` 判 FAIL + 静态清单）。
- **C13（决策项，owner = 主理人）**：`config/.env` 本地 7 个真实风格凭据**未发现任何外泄证据**，是否轮换属风险偏好决策，**不由本审计单方面裁定**。同时纠正「加 hook 即可拦住」：`.gitignore` 被 `git add -f` 绕过、pre-commit 被 `--no-verify` 绕过，可靠拦截点在服务端 / CI secret scanning 与「凭据不落盘」。
- **C14（medium，owner = devops）**：PR 触发缺口。用 `yaml@2.9.1` 真解析确认：`v16-market-provenance.yml` 的 `on.pull_request.branches` **只有** `["release/jcc-v1.3-final"]`，`ci.yml` 只有 `["main","master"]` → **PR 开向 `feature/v16-market-provenance-hardening` 时两个 workflow 都不触发（0 check）**。**直接后果：T03 若按常规开 PR 到基线分支，PR 页 0 check，T04 无从签收。**
  **修法成本（经复刻更正）**：给 `pull_request.branches` **追加** `feature/v16-**`（保留 release）**不需要改 Y1** —— 我曾判断「会撞红 Y1」是**错的**。Y1 的 `l.some(x => /release\/jcc-v1\.3-final/.test(x))` **只验该字符串在文件中出现过**（实测全文仅 1 处，L10）。复刻五变体：追加→PASS / 移除→FAIL / 整段注释→PASS / **把分支名挪进 `push.branches` 后删除 PR 目标→PASS**。→ 严格说 Y1 是「**禁止把该字符串从整个文件删掉**」，而非「禁止移除 PR 目标」。**最省事路径 = 追加；只有「替换成 `main`」才需同步改 Y1。**
- **F-23 扩围（medium，owner devops/QA）**：不限于 Y2，**Y1 同样弱**，且实测共 **四种显形**。① Y1 只验「分支名存在」→ 无法发现 PR 分支被**整段注释掉**，也无法发现**把分支名挪进 `push.branches` 后删除 PR 目标**（复刻实测均 PASS）；② Y2 只登记 5 条 gate（实际 7 条）、`yamlLines` 不剔注释 → **5 条门全部注释掉仍 PASS**；③ **`ci.yml` 的 `pull_request.branches` 完全无 gate 断言**（`grep -c pull_request` = 0）；④ **行尾注释使带 `$` 锚定的提取器静默漏行**（`node x.mjs  # legacy` → 不匹配；去 `$` 后正常）。
  **修复口径（三方收敛）**：去 `$` 锚定为主修法（不依赖注释剔除是否正确）+ 剔注释为辅 + 作用域限定 `node` 行 + `EXPECTED.length > 0` 封双空 + `S.size >= 7` 兜底 + `setEqual` 主判据 + 补 `ci.yml` PR 触发断言。**注意 `yaml` 不是直接依赖**（仅由 `postcss-load-config` 传递引入），故「改用真解析」必须先显式声明 devDependency。详见主报告 §11.8.8。

## 5. 阻断关系（明确写清，避免被断章取义）

- **无条目级 NOT_VERIFIED** → 不阻断八条中的任何单条裁定。
- **C9 / F-16 阻断「P3 解锁裁定」**。
- **C12 / F-22 / F-23 阻断「以测试门全绿作为放行依据」这一论证路径**（门本身可能空转，守卫门列表的断言本身也可被注释化绕过）。
- **C14 阻断「T03 的 PR 签收路径」**（PR 开向基线分支时 0 个 check）。
- **3 处子命题 UNVERIFIED**（抗蓄意伪造、云 D1、真实账单 / venue 实测）→ 阻断「GO（无条件）」与「生产就绪」裁定。（类型检查已于 §11.8.1 转 VERIFIED，不再计入。）
- **本签收单不得用于**：宣布安全全部通过、批准真实资金、替代 QA 签字、替代 team-lead 的整体门禁裁定。

## 6. 方法学更正（自查，不隐去）

初版本单引用「`t1–t9` 83/83」时未标明方法：该 83 条断言来自**我自建的聚合 harness**（显式 `createSuite` 后调用各模块 `run(suite)`），**不是** `node <单个文件>` 的结果。经 `independent-qa` 提示后我独立复现：9 个模块**无主模块守卫**，单独执行 = exit 0 / 0 断言。**83/83 仍然有效（83 条真实断言，非空转），但「单独执行也是 83 条」是错的**，已在主报告 §11.7.2(e) 就地更正。

**二次交叉核实后，我又推翻了自己两处**（详见主报告 §11.8）：

1. **「`node_modules` 缺失 → 类型检查 UNVERIFIED」已过时**：依赖现已安装，`tsc --noEmit` exit 0、`vitest run` 22 文件 217 通过 → 转 **VERIFIED**，子命题 UNVERIFIED 由 4 处降为 3 处。但**限定工作区态**：`vite.config.ts` 与 `ci.yml` 当时均为未提交改动，**HEAD 提交态的 vitest 红绿属 T04，我不置评**。
2. **「CI 覆盖缺口已修掉」只对 push 成立**：真 YAML 解析确认 **PR 开向 `feature/v16-market-provenance-hardening` 时两个 workflow 均不触发**，我上一版漏判，已更正并新增 C14。
3. **「临时文件未见收尾清理」是我错了**：源码有 `finally { cleanTmp(); }`，实跑 `PASS=17` 无残留，定性已下调为「仅硬杀时可能残留」。

同时我**收窄了 QA 的一处过宽表述**：Y2 对「删除**已登记**的 5 条 gate」是**能抓到**的（其主张「双向都哑」过宽）；真正严重的是「**全部门被注释掉仍 PASS**」，这是双方此前都没发现的。

**第三处自我更正（devops 指出）**：我原判断「放宽 PR 分支会撞红 Y1」**过强**。复刻 Y1 四段断言实测：**追加** `feature/v16-**` → **Y1 仍 PASS**；**移除** release（改 `main`）→ 才 FAIL；**整段注释** → **仍 PASS**（我补的）。原因是 `l.some(...)` 只验存在、不验唯一。→ **C14 最省事路径是「追加」，不需要改 Y1**。并据此把 F-23 从「仅 Y2」扩为「**Y1 + Y2**」，且指出 `ci.yml` 的 `pull_request.branches` **完全无 gate 覆盖**。详见主报告 §11.8.7。

## 7. 纪律声明

- 未执行 `git add` / `commit` / `checkout` / `stash` / `push` / `restore`；未改任何源码、测试、旧 freeze、他方报告。
- 本轮**唯一写入**：`reports/JEV_REAL_TRADING_SECURITY.md`（追加 §11）与本文件。
- 未触网、未调付费模型、未提交订单、未创建真实账户；未输出任何凭据值 / 片段 / last4。
- 所有行为证据均在仓库外目录或 OS 临时目录产生：`C:/temp/jewsec/`、`d:/数据分析/_sec_probe/`、`os.tmpdir()/jev-p0-qa-*`；每步后复核 `git status` 未因本审计新增变更。
- `UNKNOWN ≠ 0`：测不到一律写 `UNVERIFIED` / `NOT_MEASURED`，未用 `0` 冒充。

---

*签收：Security = `GO WITH CONDITIONS（仅限 Security 范围）`。八条 8/8 VERIFIED（边界逐条标注），0 条目级 NOT_VERIFIED，**3 处**子命题 UNVERIFIED 不进 GO 依据（类型检查已转 VERIFIED，限工作区态）；**C9（F-16，verified 语义绑定真实 egress）为 P3 解锁硬前置**；**C12/F-22/F-23（Y1+Y2 弱断言）阻断「以门全绿作放行依据」**；**C14 阻断 T03 的 PR 签收路径**（PR 开向基线分支时 0 check；最省事解除方式为追加 `feature/v16-**`，经复刻确认无需改 Y1）；C13 凭据轮换属主理人决策。不构成 P0 整体 PASS、不构成生产 GO、不构成可上真实资金之授权。*

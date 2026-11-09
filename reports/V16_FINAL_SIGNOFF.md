# JEV V1.6 — Final Signoff（双轨：Market Provenance Hardening + AI Paper Trading Phase 1）

- 签发人：主理人（Owner）。日期：2026-11-09。
- 分支：`feature/v16-market-provenance-hardening`，基线 `bfb3206`（V1.5 addendum / PR #5，未触碰）。
- 最终 HEAD：`93b30d4`。Draft PR：**https://github.com/yihui315/jev/pull/6**（draft、open、未合并）。
- 本报告不含任何 Token / 凭据明文。

## TL;DR — 最终决策

| 项 | 决策 |
|---|---|
| 轨道 A（Market Provenance Hardening） | **GO**（独立 QA 三阶段验收：阶段 1 基线、阶段 2 GO WITH CONDITIONS、阶段 2.5 D1 修复签认 PASS → 总 GO） |
| 轨道 B（AI Paper Trading Phase 1 契约层） | **GO WITH CONDITIONS**（Mock 契约层完成；Real Provider **BLOCKED**——无凭据） |
| 接入 `jev-paper-staging` | **NO-GO**（独立评审门禁未解除） |
| 生产晋级 / 自动交易 | **NO-GO** |
| V1.5 已签收成果 | **GO 不变**（8/8 / 95 断言 / 12 行隔离快照全部保留） |

## 1. 轨道 A 交付与证据

| 交付 | 结果 | 证据 |
|---|---|---|
| Schema 迁移 | 0001（与基线逐字节一致）+ 0002（8 新列 CHECK、UNKNOWN/HISTORY_UNPROVEN 回填、legacy 保留、35 列） | `reports/V16_SCHEMA_MIGRATION.md`、`v16_migration_offline_dryrun.json`、`v16_cloud_schema.json` |
| Fail-closed guard | 7+1 规则（含 QA2-D1 修复 `MARKET_CONTEXT_UNRESOLVED`） | `reports/V16_MARKET_PROVENANCE_DESIGN.md`、`V16_MIXING_GUARD_TESTS.md` §9 |
| 测试 | V1.5 **8/8 / 95 断言**（逐字节冻结）；V1.6 **8/8 / 104 断言**（含强制反例、3 类故障注入、变异验证） | `reports/evidence/v16/summary.json` |
| 云端 E2E | 全新隔离 D1 `997aa8fd` + Worker：10 周期 / **34 行快照 = 10 真实 US VERIFIED + 23 UNKNOWN + 1 手工对抗哨兵行**（哨兵禁止计入采集器成绩）；**24/24 检查 PASS**；回放 **10/10**；入口关闭 403（修复前后双探针） | `reports/V16_CLOUD_E2E.md`、`reports/evidence/v16/cloud/`（12 份） |
| 独立 QA | 阶段 1（9 项静态）→ 阶段 2（11 项实测，发现 D1 fail-open）→ 阶段 2.5（D1 修复增量复核 **SIGN-OFF PASS**，总 GO） | `reports/V16_INDEPENDENT_QA.md` 第 1–6 节、`qa_phase2_independent.json`、`qa_d1fix_verification.json` |
| 部署一致性 | 13 个源文件 sha256 与工作区一致（QA3 重算）；`deploy_ok` 结构化判定 | `deployed_source_sha256.json`、`v16_worker_deploy.json` |

## 2. 轨道 B 交付与证据

| 交付 | 结果 |
|---|---|
| 统一 Provider Adapter | openai / openai-compatible / ollama 三型 + SSRF 防护（`server/ai/provider-adapter.mjs`） |
| 决策契约 | `jev.ai.decision/v1` 14 字段白名单严格校验（`decision-schema.mjs`、`docs/JEV_AI_DECISION_SCHEMA.md`） |
| 独立风控 | risk-gate：confidence 不参与放行；无效 symbol / 过期快照 / 限额超限 100% 拒绝 |
| 持久化与归因 | `0006_ai_decisions.sql`（`ai_decisions` + `decision_order_links` + `ai_market_contexts`，**位于 server/ai/migrations/，冻结引擎迁移目录零改动**） |
| 网关路由 | `/api/jev/ai/*` 8 条，位于 `ENABLE_REAL_TRADING` 红线与 Basic Auth 之后（网关仅 +10 行 lazy hook，红线行号不变） |
| Mock Contract Tests | **9 套件 / 83 断言 / 83 PASS / exit 0 / network_calls=0** |
| Real Provider | **BLOCKED**——DPAPI 凭据库无任何 AI 密钥。解除条件：主理人提供 ≥1 个 Provider 凭据（OpenAI / 兼容 / Ollama） |
| 策略基准 | **NO_DATA**（诚实标注：Mock 结果不计入策略成绩；30 天 Paper Trading 未开始计时） |

## 3. CI 记录（全部真实，含失败）

| Run | SHA | 结果 | 说明 |
|---|---|---|---|
| 42627150397 | bcc5b84 | **failure** | check 6 敏感串扫描 4 命中——全部为网关基线行的 `env.PAPER_LEDGER_SECRET` 红线引用（非密钥、非 V1.6 引入）；根因=校验器整文件扫描 + 本地预提交运行漏扫已修改跟踪文件 |
| 42627803460 | 0a4d5e7 | **failure** | 校验器已绿；V1.5 T0/T5 失败——T5 锚点绝对路径 ENOENT、T0 断言 A1 本机网络上下文（V1.6 工作流是这两个测试首次进 CI） |
| **42628475303** | 93b30d4 | **success** | **全绿**：verifier 9/9 → 环境复现 → V1.5 8/8/95 → V1.6 8/8/104 → AI 83/83 离线 |
| 42628475304 | 93b30d4 | **success** | Trading Workbench CI 同步全绿 |

两次真实失败的处理均为**根因修复**：① 校验器改 diff 感知扫描（只扫新增行，断言未删未放宽，本地=CI 等价）；② CI 环境复现（从本仓库冻结引擎重建 T5 锚点树——byte 一致由 verifier check 3 与 T5 四锚点双重证明；`/etc/hosts` 阻断 api.binance.us 受控复现 A1 探测上下文——T0 六断言仍执行真实 curl 探测，无桩、无伪造）。

## 4. 统计口径（禁止合并）

- V1.5 隔离库：12 行快照（9 verified / 3 unknown）——历史验收，未触碰。
- V1.6 隔离库：34 行快照 = **10 条采集器真实 US 输出** + 23 条 UNKNOWN（含三类故障注入与混市场拒绝行）+ **1 条 C8 手工预置对抗哨兵行**（`v16_FIXTURE_ADVERSARIAL`，仅验证峰值不被污染，禁止计入采集器成绩）。
- 断言总数：95（V1.5 冻结）+ 104（V1.6 新增）+ 83（AI Mock 契约）= 282，全部为本地/CI 真实执行。
- V1.6 云端 24/24 检查、回放 10/10、独立 QA 三阶段——各口径独立，禁止混算。

## 5. 残留 BLOCKED（生产晋级前置清零项）

1. **无 AI Provider 凭据** → Real AI 推理 / Real Paper E2E / 策略基准全部 BLOCKED/NO_DATA（唯一主理人动作项）。
2. ORDIUSDT / 1000SATSUSDT 在 Binance.US 仍 BLOCKED（`-1121`）。
3. Binance Global spot 接入仍 NO-GO / CONDITIONAL（混用防护已由 guard 强制，候选源未接入）。
4. `paper_equity_snapshots` 在 Staging D1 仍 0 行（接入评审门禁未解除）。
5. 0006 AI 迁移未在真实 D1 上 apply（node:sqlite 内存验证通过）。
6. Cloudflare 边缘动态 `import()` 打包行为未实测（本地 Node 验证通过）。
7. 云端 LLM 出网白名单与 SSRF 边界未在真实 Worker 上验证。
8. 六角色命名映射为 OWNER-DECIDED DRAFT（三套词汇并存，待主理人拍板）。
9. 30 天真实研究样本 0（V1.5 遗留）；另一个账户级 Token `d144e388…`（297 权限组）仍 active（V1.5 遗留）。

## 6. 建议的验收后动作（待主理人决定）

- 销毁 V1.6 隔离 D1（QA 建议；V1.5 库同样保留决策权在主理人）。
- `V45_TEST_TRIGGER` 保持 `false`（已部署为 false）。
- 生产部署清单必须包含 `MARKET_NETWORK_CONTEXT=cloudflare_pages_edge_runtime_prod`（漏配将按设计 fail-closed 全降级 UNKNOWN——QA2-D1 修复后的预期行为，建议对 `MARKET_CONTEXT_UNRESOLVED` 连续出现设告警）。
- 提供 AI Provider 凭据后按 `V16_AI_PAPER_E2E.md` 的 5 步顺序执行 Real AI 验证。

## 7. 硬门禁复核

- 冻结引擎 15 文件 tree sha256：`aec5d1b7cc26…ad` 不变（本地 + CI verifier 双重验证）。
- 冻结 `paper_equity_snapshots` DDL：与 0001 逐字节一致（三方比对）。
- `ENABLE_REAL_TRADING=false`（红线 :719 保留，CI 中 t9 验证 true→500 RED_LINE）。
- 未强推、未合并 Release、未修改 PR #4/#5、未接 Staging D1、未开启真实交易、未伪造任何样本或收益。

—— 签发。轨道 A 可进入「接入 Staging 评审」讨论；轨道 B 等待 Provider 凭据解锁下一阶段。

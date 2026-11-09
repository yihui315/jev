# V1.6 轨道 B — AI Paper E2E 状态

## 状态：**BLOCKED —— 无 Provider 凭据**

本文件记录的是**真实执行结果**，不是计划。当前无法完成「真实 AI → 决策 → 风控 → Paper 下单」的端到端验证，原因如下。

---

## 1. 已完成的真实工作（可验证）

| 能力 | 状态 | 证据 |
|---|---|---|
| 统一 Provider Adapter（openai / openai-compatible / ollama） | 已实现 | `server/ai/provider-adapter.mjs`；`t2` 18 项检查 |
| 决策契约 + 严格校验 + schema 文档 | 已实现 | `server/ai/decision-schema.mjs`；`t1` 13 项 |
| 独立服务器风控（confidence 免疫） | 已实现 | `server/ai/risk-gate.mjs`；`t3` 13 项 |
| 决策持久化 + 订单归因 | 已实现并跑通（内存 D1） | `server/ai/decision-store.mjs` + `0006_ai_decisions.sql`；`t6` 6 项 |
| 幂等执行（同一 decision_id 不重复下单） | 已实现并跑通（假引擎计数） | `t4` 7 项；重复 execute 时引擎调用数保持 1 |
| Mock / Real 隔离 | 已实现并断言 | `t5` 9 项；real 无凭据 0 行写入 |
| 网关 `/api/jev/ai/*` 路由 | 已接线并冒烟 7 项 | `t9`；红线与 Basic Auth 前置生效 |
| Mock Contract Test | **83/83 PASS，exit 0** | `reports/evidence/v16_ai/v16_ai_contract_tests.json` |

## 2. 真实阻塞（BLOCKED 的具体原因）

1. **本机 DPAPI 凭据库没有任何 AI Provider 密钥**。
   - 已按规约**未读取**任何密钥明文；该结论来自系统审计与凭据库结构，而不是对 secret 的尝试读取。
   - 影响：`POST /api/jev/ai/providers/test` 对所有非 Ollama provider 返回 `409 BLOCKED / SECRET_REF_UNRESOLVED`。
2. **真实推理 → 决策链路无法验证**：
   - `POST /api/jev/ai/decisions` 的 `dataMode:"real"` 在无凭据时返回 `409 PROVIDER_BLOCKED`，且**不写入任何决策行**（`t5` 断言 rows=0）。
   - 因此也没有「真实模型输出 → schema 校验 → 风控裁决 → 引擎受理」的线上样本。
3. **云端 Ollama 不可达**：Cloudflare Worker 无法访问用户本机 loopback；即使配置 `JEV_AI_OLLAMA_BASE_URL`，云端也只会得到 `NOT_CONNECTED`。
4. **云端 AI 出网未经线上验证**：本轮未扩展任何市场出网白名单，也没有以真实密钥从 Worker 边缘发起 provider 请求。
5. **未做真实 Paper 下单 E2E**：`/api/jev/ai/paper/execute` 的逻辑已用假引擎验证幂等与准入，但没有对真实 D1 + 真实引擎 + 真实模型输出的联合运行。

## 3. 解除条件（主理人操作）

满足任意一条即可解除 Provider 层 BLOCKED：

1. 提供至少一个云端 provider 密钥（Worker secret 或本地测试环境变量）：
   - `OPENAI_API_KEY`（provider `openai`），或
   - 任一 compatible：`MINIMAX_API_KEY` / `DASHSCOPE_API_KEY`（需同时设 `JEV_AI_QWEN_BASE_URL`）/ `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`
2. 或者，若要在本机验证 Ollama：提供可访问的 `JEV_AI_OLLAMA_BASE_URL` 与已拉取的模型名（`JEV_AI_OLLAMA_MODEL`），并在**本机 Node**（非 Worker）运行验证脚本。

解除后应执行的验证顺序（每步都要求真实 HTTP 状态与响应摘要，不允许代填）：

1. `GET /api/jev/ai/providers` → `verdict=UNVERIFIED`，目标 provider `credential_present=true`、`last4` 正确。
2. `POST /api/jev/ai/providers/test` → 真实 200 且 `choices[0].message.content` 非空 → `CONNECTED`；401/404/429/超时须如实记录对应错误码。
3. `POST /api/jev/ai/decisions {dataMode:"real"}` → 保存 `ai_decisions` 行 + 服务器风控裁决（可能被拒，这也算成功验证）。
4. 若风控 `PASS`：`POST /api/jev/ai/paper/execute` → 检查 `paper_orders` 新增 1 行、`decision_order_links` 新增 1 行；重复调用不得新增。
5. `GET /api/jev/ai/performance` → 有真实归因数字；无样本时必须是 `NO_DATA`。

## 4. 明确不成立的表述（防误解）

- ❌ 「AI 六角色已在云端跑通」——未跑通，没有任何真实推理记录。
- ❌ 「AI 已能自动下单」——未发生真实下单；`paper/execute` 只在决策已过服务器风控后转发冻结引擎，且本轮仅用假引擎验证。
- ❌ 「Mock 测试通过等于 Provider 可用」——Mock 只证明契约与闸门逻辑，不证明连通性。
- ❌ 「Provider 已 CONNECTED」——状态接口在无凭据时只返回 `BLOCKED`。

## 5. 结论

- 本阶段可交付：Adapter 层、契约层、风控层、持久化层、路由层、可重复的 82 项契约测试。
- 本阶段不可交付：真实 Provider 连通性证据、真实模型决策样本、真实 Paper E2E。
- 判定：**BLOCKED（仅限 Real Provider 通路）**；Mock Contract Test 全部 PASS。

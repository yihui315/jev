# JEV AI Provider API（V1.6 轨道 B 第一阶段）

- 模块根目录：`apps/command_center_3d/server/ai/`
- 状态：**实现完成，连通性 BLOCKED**（本机凭据库无任何 AI Provider 密钥）
- 适用红线：全部路由仅在 `ENABLE_REAL_TRADING === "false"`、预览凭据与 ledger 会话签名密钥（部署期 Worker secret）齐备、Basic Auth 通过后才可达（`functions/api/jev/[[path]].js` 的 `onRequest` 先于路由执行）
- 冻结区：`server/engine/**` 一行未改；`ai-core.mjs`（minimax/qwen 硬编码）未改，本 Adapter 是平行新建

---

## 1. 模块边界

| 文件 | 职责 | 是否触网 |
|---|---|---|
| `provider-adapter.mjs` | 统一 wire 协议：`buildRequest` / `classifyResponse` / `performRequest` / SSRF 校验 / 脱敏 | 仅 `performRequest` |
| `decision-schema.mjs` | 决策契约与手写严格校验器 + JSON 等价 schema 文档 | 否 |
| `risk-gate.mjs` | 独立服务器风控（唯一放行权威） | 否 |
| `decision-store.mjs` | `ai_decisions` / `decision_order_links` 读写、绩效聚合 | 否 |
| `mock-provider.mjs` | 确定性 Mock（seeded，OpenAI 响应形状） | 否 |
| `runtime.mjs` | provider 注册表、真实连通性测试、决策流程、幂等执行 | 经 adapter |
| `roles-mapping.mjs` | 六角色映射草案（OWNER-DECIDED DRAFT） | 否 |
| `migrations/0006_ai_decisions.sql` | 新表（**不在冻结迁移目录**） | 否 |

---

## 2. Provider 配置模型

```json
{
  "providerId": "openai",
  "providerType": "openai | openai-compatible | ollama",
  "baseUrl": "https://api.openai.com/v1",
  "modelId": "gpt-4o-mini",
  "secretRef": "OPENAI_API_KEY",
  "timeoutMs": 45000,
  "maxTokens": 512,
  "enabled": true,
  "capabilities": { "chat": true, "models": true, "requiresApiKey": true, "jsonMode": true }
}
```

- `secretRef` **只是名字**（环境变量名 / Worker secret 名）。代码中不存在任何密钥明文，D1 中也无密钥列。
- 取值顺序：运行时 `env[secretRef]`。解析后的明文只在单次请求的 `Authorization` 头内存在，响应、日志、证据、D1 一律不落。
- 对外只暴露 `configured`、`credential_present`、`last4`。
- 内置注册表（可由环境变量覆盖 baseUrl / model）：

| providerId | 类型 | 默认 baseUrl | secretRef |
|---|---|---|---|
| `openai` | `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `minimax` | `openai-compatible` | `https://api.minimaxi.com/v1` | `MINIMAX_API_KEY` |
| `qwen` | `openai-compatible` | 空（必须配 `JEV_AI_QWEN_BASE_URL`） | `DASHSCOPE_API_KEY` |
| `deepseek` | `openai-compatible` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| `openrouter` | `openai-compatible` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| `ollama` | `ollama` | `http://127.0.0.1:11434` | `OLLAMA_API_KEY`（可选） |

### 出网与 SSRF 边界

- `https` 仅允许公网主机；拒绝 RFC1918、`169.254.169.254`、`.internal`、`.local`。
- `http` 仅允许 `ollama` 类型指向 `127.0.0.1` / `localhost` / `[::1]`。
- **市场出网白名单未扩展**（仍仅 `api.binance.us` 及既有端点）。AI 出网仅由上面的 provider 配置驱动，不接受请求参数传入 URL。
- Cloudflare Worker 无法访问用户本机 loopback：云端 Ollama 必然 `NOT_CONNECTED`，这是环境事实，不是缺陷。

---

## 3. HTTP 接口（前缀 `/api/jev/ai`）

所有响应沿用既有 `jcc.jev.trading_api/v1` envelope；错误码在 `code`，证据在 `data.evidence`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/providers` | 状态与能力；`verdict` 只可能是 `BLOCKED` 或 `UNVERIFIED`，绝不返回 `CONNECTED` |
| POST | `/providers/test` | 真实最小推理。无凭据 → `409 BLOCKED / SECRET_REF_UNRESOLVED`；失败 → `502` + 七类错误码；成功 → `200 CONNECTED` |
| GET | `/strategies` | 读 `strategy_versions`（只读）；无数据 → `NO_DATA` |
| POST | `/decisions` | `{strategyId, symbol, providerId?, dataMode:"mock"\|"real", runId?}`；返回决策 + 服务器风控裁决 |
| POST | `/risk-check` | `{decision}` 或 `{decisionId}`，返回 `{approved, code, reason}` |
| POST | `/paper/execute` | `{decisionId, confirmationId?, clientOrderId?}`；仅当该 decision 的 `risk_check_result='PASS'` 且未下过单时转发冻结引擎 |
| GET | `/runs/{runId}` | 该 run 的决策与订单关联 |
| GET | `/performance` | 决策/订单/成交归因聚合；0 样本 → `NO_DATA` |

### `/decisions` 行为

1. `dataMode` 必须显式给出，缺失即 `400 DATA_MODE_REQUIRED`（不允许默认值猜测）。
2. 先用既有 `fetchVerified` 读取真实行情，落 `ai_market_contexts`（`mark_quality='verified'`，含 sha256）。
3. `mock`：确定性 Mock 生成 OpenAI 形状响应，走**同一条**解析/校验/风控/入库路径。
4. `real`：无凭据 → `409 PROVIDER_BLOCKED`，**不写任何决策行**；请求失败 → 502 + `NOT_CONNECTED / AUTH_FAILED / MODEL_UNAVAILABLE / TIMEOUT / RATE_LIMITED / INVALID_RESPONSE / SERVER_ERROR` 之一。
5. 模型输出必须是合法 JSON 且通过 `decision-schema`；否则 `DECISION_INVALID` / `INVALID_RESPONSE`，不落行。

### `/paper/execute` 行为

- 先查 `ai_decisions`：已有 `paper_order_id` → `duplicate:true`，**引擎零调用**。
- `risk_check_result !== 'PASS'` 或 `status !== 'APPROVED'` → `409 EXECUTE_NOT_APPROVED`（AI 输出无法覆盖）。
- `HOLD` → `409 DECISION_IS_HOLD`。
- 通过后按引擎 **7 字段白名单** 构造请求并调用 `engine.handlePaperOrders`（import 冻结 handler，不复制逻辑、不改阈值）。`clientOrderId = "ai-" + decisionId`，保证跨重试同一幂等键。
- 成功后写 `decision_order_links`，并更新 `ai_decisions.paper_order_id / paper_fill_ids_json / status='SUBMITTED'`。

---

## 4. 错误分类（七类，均带真实证据）

| code | 触发 | retryable | 证据字段 |
|---|---|---|---|
| `NOT_CONNECTED` | DNS/连接失败；无凭据（`buildRequest` 前置失败） | 是 | `network_error` |
| `AUTH_FAILED` | HTTP 401/403 | 否 | `http_status`、`error_message`（已脱敏） |
| `MODEL_UNAVAILABLE` | HTTP 404；400/422 且消息含 model | 否 | `http_status`、`error_message` |
| `TIMEOUT` | AbortError / 超过 `timeoutMs` | 是 | `elapsed_ms`、`timeout_ms` |
| `RATE_LIMITED` | HTTP 429 | 是 | `http_status` |
| `INVALID_RESPONSE` | 2xx 但非 JSON / 无 choices / 空 content / 其它 4xx | 否 | `body_keys`、`response_chars` |
| `SERVER_ERROR` | HTTP 5xx | 是 | `http_status` |

`classifyResponse(provider, httpStatus, body, elapsedMs, extra)` 是纯函数，可离线单测；`httpStatus === null` 时按 `timedOut` 与 `elapsedMs >= timeoutMs` 区分 `TIMEOUT` 与 `NOT_CONNECTED`。

---

## 5. 安全边界（不可协商）

1. **密钥零明文**：无任何硬编码 key；`secretRef` 仅名字；`describeRequest()` 只输出 header 名与 `authorization_present`；`redactSecrets()` 兜底清洗上游错误文本。
2. **AI 不可自证**：`confidence` 不参与风控；`data_mode` 由服务器写；`provider_id` 在 mock 模式下强制为 `mock`。
3. **风控不可绕过**：`risk-gate.mjs` 是唯一放行权威，拒绝码见 `JEV_AI_DECISION_SCHEMA.md`；`/paper/execute` 二次校验已存裁决。
4. **冻结区零改动**：`server/engine/**` 全目录 sha256 锚点不受影响；新迁移在 `server/ai/migrations/`。
5. **不伪造**：无凭据时接口返回 `BLOCKED`，不返回 `CONNECTED`；0 样本绩效返回 `NO_DATA`，不返回 0。

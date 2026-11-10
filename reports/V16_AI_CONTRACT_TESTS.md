# V1.6 轨道 B — Mock Contract Test 结果

- 命令：`node apps/command_center_3d/server/ai/tests/run-all.mjs`
- 结果：**overall PASS，9 套件 / 83 项检查 / 83 通过 / 0 失败，exit code 0**
- 运行时间（UTC）：2026-11-09T13:15:11Z（evidence `generated_utc`）
- Node：v22.22.2 / win32 x64
- 证据：`reports/evidence/v16_ai/v16_ai_contract_tests.json`（结构 `{overall, counts, results[{id, checks[{name,pass,detail}]}]}`）
- 网络调用：**0**（全部走 `node:sqlite` 内存库 + 注入 fetch/引擎）

## 1. 套件明细

| 套件 | 检查数 | 结果 | 覆盖点 |
|---|---:|---|---|
| `t1_decision_schema` | 13 | PASS | 合法 HOLD/BUY/LIMIT；多余字段；HOLD 带数量；BUY 无数量；confidence 越界；枚举外值；side≠action；缺字段；LIMIT 无限价；schema 文档字段一致；canonicalize 稳定 |
| `t2_provider_errors` | 18 | PASS | openai/compatible/ollama 请求构造；`/v1` 不重复；SSRF：http 非环回、私网 https、metadata IP；缺凭据前置失败；401/403、404、400(model)、400(shape)、429、5xx、200 非 JSON/空内容；timeout 两条路径；AbortError；describeRequest 脱敏；/models |
| `t3_risk_gate` | 13 | PASS | 正常放行；非白名单 symbol；无快照；`mark_quality=unknown`；过期快照；symbol 不匹配；`expires_at` 过期；名义金额超限；数量超限；**confidence 0.01 vs 1.0 裁决相同**；HOLD 无单；schema 非法前置拒绝；DB 1e8 价格读取 |
| `t4_idempotency` | 8 | PASS | 首次提交 1 次；重复 execute 引擎零调用；link 唯一；风控拒绝不可执行；HOLD 不可执行；未知 decision 404；401 无 Paper 会话不消耗决策（保持 APPROVED 可重试）；重复 create 只 1 行 |
| `t5_data_mode` | 9 | PASS | 空 env 全 BLOCKED；real 无凭据 0 行；mock 标记；mock 不得借用 real provider；test 无凭据 0 请求；401→FAILED；200→CONNECTED；real 注入通路端到端（含三处 sha256）；real 超时不落行 |
| `t6_persistence_attribution` | 6 | PASS | 1e8 往返 / JSON 字段 / 三处哈希；run 投影含 links；execute+fills 归因（realizedPnl 0.12345678）；空库 NO_DATA；strategy 过滤与 data_mode 分组；策略表降级 |
| `t7_redaction` | 5 | PASS | 源码/迁移敏感串 0 命中；表无 secret/authorization/api_key 列；provider 状态只回 last4；上游错误文本脱敏；落库行无 Bearer/密钥片段 |
| `t8_role_mapping` | 4 | PASS | 6 行映射唯一；代码角色合法且无重名；OWNER-DECIDED DRAFT、零重命名；文档与代码一致 |
| `t9_gateway_routes` | 7 | PASS | providers BLOCKED；providers/test 409（非 CONNECTED）；strategies NO_DATA；缺 dataMode 400；未知 AI 路由 404；红线 500；Basic Auth 401 |

## 2. 关键断言的证据价值

1. **幂等**：`t4` 用计数器证明第二次 `executeApprovedDecision` 后 `engine.calls` 仍为 1；`decision_order_links` UNIQUE 二次插入返回 `linked:false`。
2. **风控独立**：`t3` 在同一个过期快照上分别用 confidence 0.01 与 1.0，`approved` 均为 false、`code` 相同；同时在新鲜快照上用小额通过、大额拒绝。
3. **Mock/Real 隔离**：`t5` 断言 real 无凭据时 `ai_decisions` 行数为 0；mock 行 `data_mode='mock'`、`provider_id='mock'`。
4. **脱敏**：`t7` 扫描 `server/ai/**/*.mjs` 与迁移全文，敏感模式 0 命中；并断言 provider 错误文本里的 `sk-...` 被替换为 `sk-***`。
5. **网关**：`t9` 直接调用导出的 `onRequest`，证明 AI 路由在红线与 Basic Auth 之后，且无凭据不会返回 CONNECTED。

## 3. 测试自身的边界（不得过度解读）

- 数据库是 `node:sqlite` 内存库，**不是** Cloudflare D1；迁移语法已实际执行，但未在 D1 上跑 `wrangler d1 migrations apply`。
- Provider 通信用**注入的 fetch**验证请求形状与错误分类；**没有**任何一次真实 provider HTTP 调用。
- 「真实决策路径」测试（`t5` 最后两例）使用注入的 200 响应，属于**传输层契约验证**，不等于真实模型可用。
- 网关测试只覆盖不需要出网的 7 项；`/decisions`、`/paper/execute` 的 HTTP 级 E2E 未运行（需真实行情与 Paper 会话）。
- 未验证：线上 Worker 的 AI 出网策略、真实 D1 上的 CHECK 约束行为、真实模型输出的 JSON 稳定性。

## 4. 复现

```bash
cd D:/jev-v16-wt/v16
node apps/command_center_3d/server/ai/tests/run-all.mjs
echo $?   # 0 = 全部通过
```

失败时 runner 会打印每条失败检查的 `name :: detail`，并把 `overall` 写为 `FAIL`、退出码 1；runner 自身异常退出码 2。不存在「假 PASS」路径。

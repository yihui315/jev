# server/ai — V1.6 Track B（AI Provider Adapter / 决策契约 / 风控闸门）

本目录是**新建模块**，与冻结引擎平行存在。

## 为什么不去改 ai-core.mjs

`server/engine/**` 是冻结区（独立校验脚本对整棵目录树计算 sha256，新增文件也会破坏锚点）。`ai-core.mjs` 里只有 minimax/qwen 两条硬编码分支，且不可修改；因此本目录实现「一份 OpenAI wire + 每厂商配置」的统一 Adapter，OpenAI 原生 / 任意 compatible / Ollama 共用一条代码路径。

## 目录

```
server/ai/
  provider-adapter.mjs     统一请求构造、SSRF 校验、七类错误分类、脱敏
  decision-schema.mjs      jev.ai.decision/v1 契约 + 严格校验器 + schema 文档
  risk-gate.mjs            独立服务器风控（唯一放行权威，confidence 免疫）
  decision-store.mjs       ai_decisions / decision_order_links 读写与绩效聚合
  mock-provider.mjs        确定性 Mock（seeded，OpenAI 响应形状，data_mode=mock）
  roles-mapping.mjs        六角色映射（OWNER-DECIDED DRAFT，不重命名代码）
  runtime.mjs              provider 注册表、真实测试通路、决策流程、幂等执行
  migrations/0006_ai_decisions.sql   新表（严禁移到 server/engine/migrations）
  tests/                   9 组契约测试 + run-all.mjs
```

## 五条红线

1. **不碰冻结区**：`server/engine/**` 零改动、零新增；AI 迁移只在本目录 `migrations/`。
2. **不存密钥明文**：`secretRef` 只是变量名；对外只有 `configured` + `last4`；`describeRequest()` 只输出 header 名。
3. **AI 不能自证**：`confidence` 仅审计；`data_mode` 由服务器写；`risk_check_result` 只由 `risk-gate.mjs` 产出。
4. **Mock 不冒充 Real**：无凭据时 `real` 路径返回 `PROVIDER_BLOCKED` 且不落行；状态接口永远不返回 `CONNECTED` 除非真实 200 + 有内容。
5. **执行不可绕过**：`/paper/execute` 只接受已存 `PASS` 裁决，`decision_id` 幂等，引擎按自身 7 字段契约被调用。

## 运行测试

```bash
node apps/command_center_3d/server/ai/tests/run-all.mjs
```

- 使用 `node:sqlite` 内存库，不触网（evidence 内 `network_calls: 0`）。
- 证据：`reports/evidence/v16_ai/v16_ai_contract_tests.json`
- 退出码：0 全通过；1 有失败；2 runner 异常。

## 已知阻塞

本机凭据库无任何 AI Provider 密钥 → 真实连通性验证 **BLOCKED**，见 `reports/V16_AI_PAPER_E2E.md`。Mock Contract Test 是本阶段验收主体。

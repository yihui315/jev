# PROVIDER_INTERFACE_MANIFEST

Worktree: `D:\jev_worktrees\v2ops`, branch `feature/jev-v2-ops-integration`.
Audit-time baseline: `bad2828`; HEAD at P0-C delivery: `1345011` (local-only doc commit, 0 push).
Gateway module: `apps/command_center_3d/server/ai/provider-interface.mjs`.
Contract test: `apps/command_center_3d/server/ai/tests/t10-provider-interface.test.mjs`.

> 真实 API 调用 = NOT_RUN（无凭据、禁出网）；已注册 ≠ 已真实调用；不得把模拟响应写成真实推理

Providers are registered PER RUNTIME: python 4 / node 6 / frozen_engine 2. They are never referred to collectively as "four providers".

## 1. Per-runtime breakdown (4 / 6 / 2), real paths

### python = 4 (concrete generation adapters)
- Source: `packages/jcc_core/providers/generation.py`
- Classes: `MiniMaxProvider`, `QwenCodingPlanProvider`, `DeepSeekProvider`, `CustomOpenAICompatibleProvider`
- Class registry: `packages/jcc_core/provider_registry.py` (`PROVIDER_CLASSES`, 4 concrete + 1 `None` decision slot)
- Env vars (`packages/jcc_core/secret_store.py:ENV_FOR_PROVIDER`): `MINIMAX_API_KEY` / `QWEN_API_KEY` / `DEEPSEEK_API_KEY` / `CUSTOM_API_KEY`
- In-repo test that exercises each exact class: `packages/jcc_core/tests/test_model_runtime.py`

### node = 6 (registry rows)
- Source: `apps/command_center_3d/server/ai/runtime.mjs` (`providerRegistryFromEnv`)
- Shared transport: `apps/command_center_3d/server/ai/provider-adapter.mjs`
- Rows / `secretRef`: `openai` (OPENAI_API_KEY), `minimax` (MINIMAX_API_KEY), `qwen` (DASHSCOPE_API_KEY), `deepseek` (DEEPSEEK_API_KEY), `openrouter` (OPENROUTER_API_KEY), `ollama` (OLLAMA_API_KEY)
- In-repo tests exercise the shared transport with synthetic fixtures (t2/t5/t7/t9/e2e_real_engine) and only enumerate the registry via `listProviders()`; no per-row exact-adapter test.

### frozen_engine = 2 (hardcoded providers)
- Source: `apps/command_center_3d/server/engine/_lib/ai-core.mjs` (`handleAiChat`, line 98: `["minimax","qwen"]`)
- Providers: `minimax`, `qwen`
- `configEnvVar = null` (apiKey read from request body, ai-core.mjs line 97)
- Zero in-repo tests import `handleAiChat` / `ai-core.mjs`.

## 2. Four-state table (registered / configured / verified / callable)

| runtime | count | registered | configured | verified | callable |
|---|---|---|---|---|---|
| python | 4 | true | false | true (cited `test_model_runtime.py`) | false |
| node | 6 | true | false | false (cite nothing) | false |
| frozen_engine | 2 | true | false | false (cite nothing) | false |

- `registered`: code fact (entry exists in a runtime registry).
- `configured`: credential fact; false for ALL (0 real keys in repo; 28 key-shaped literals are synthetic sentinels/fixtures; env-var NAMES recorded, values never resolved).
- `verified`: in-repo test exercises that EXACT adapter; local code-coverage claim, NOT connectivity, NOT a real call.
- `callable`: real credentialed egress-allowed call; false for ALL (fail-closed without credentials; default CI/mock never goes out to network).

## 3. Gateway target mapping (MiniMax / M3 / Bailian / JEV)

| target | status | connected | verified in repo | logical / proven |
|---|---|---|---|---|
| MiniMax | ADAPTER_PRESENT_NOT_CONNECTED | false | concrete adapters: `generation.py`, `runtime.mjs`, `ai-core.mjs` | not connected; 真实调用 NOT_RUN |
| M3 | UNVERIFIED | false | none (no concrete adapter) | `MiniMax-M3` appears only as a MODEL ID string (`runtime.mjs` modelId, `ai-core.mjs` MINIMAX_MODEL, `generation.py` known_models); no adapter invented |
| 阿里百炼 (Bailian / Alibaba Model Studio) | ADAPTER_PRESENT_NOT_CONNECTED | false | `QwenCodingPlanProvider` (`generation.py`, coding.dashscope.aliyuncs.com), qwen Model Studio branch (`ai-core.mjs`, maas.aliyuncs.com) | not connected; 真实调用 NOT_RUN |
| JEV internal scheduler | LOGICAL_ONLY | false | nearest: `packages/jcc_core/model_router.py` (ModelRouter), `apps/command_center_3d/server/engine/_lib/agent-orchestrator.mjs` | no external target proven; 真实调用 NOT_RUN |

All four targets: `connected = false` (nothing actually proven reachable; egress disabled).

## 4. UNVERIFIED / 无法核实项

- 真实 API 调用：NOT_RUN（无凭据、禁出网）。无任何真实推理、无网络出网、无花费。
- M3 具体 adapter：UNVERIFIED（仓库内无；仅为模型 id 字符串）。
- frozen_engine 2 个 provider 的 in-repo 测试：无（verified=false）。
- node 6 个 registry 行的逐行 exact-adapter 测试：无（verified=false；仅共享 transport 的 fixture 测试）。
- JEV internal scheduler 外部连接：未证实（LOGICAL_ONLY）。
- 任何 `configured`/`callable`/`connected` 为真：均未证实（全为 false）。

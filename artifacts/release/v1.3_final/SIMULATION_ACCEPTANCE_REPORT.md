# JEV/JCC 原生流水线 —— 全面模拟验收报告

> 生成时间：2026-10-08 | 分支：`release/jcc-v1.3-final` | 模式：SIMULATED-PROVEN（非 REAL-PASS）

## TL;DR（结论先行）

- 新增一套**全面模拟测试**，用确定性协议流驱动**真实的**原生执行代码：`WorkBuddyServeAdapter`（ACP/SSE 路径）与 `NativeLaunchSupervisor`（NDJSON 路径）。
- 模拟测试 **9/9 全部通过**；`jcc_core` 全量回归 **1232 通过 / 8 失败**。
- 修复一个真实潜伏缺陷：`NativeLaunchSupervisor` 此前只记录**第一条**原生事件，现已修正为记录完整时间线（与发布闸门要求的原生可观测性一致）。
- **诚实结论**：JCC 框架逻辑已通过模拟**证明**；真实原生执行仍被**结构性主机缺口**阻塞（daemon 不对外暴露 agent-run 能力），与既往结论一致，**未变**。

## 一、模拟了什么（为何诚实，未伪造）

生产原生路径被一个**结构性主机缺口**阻塞：WorkBuddy daemon 不对外暴露无凭证的 agent-run 能力（`DaemonAgentRunUnsupported`，见 `workbuddy_daemon.py`）。JEV 无法在不复制 daemon 账户凭证的前提下派生真实模型负载——而复制凭证是被禁止的。

我们能做、且在此做到的，是用**确定性模拟协议流**穿过**真实的**适配器/监督器代码，从而证明框架逻辑端到端正确。两个模拟层：

1. **`WorkBuddyServeAdapter.run_task`** —— 喂入与真实 `--serve` 主机同形的 ACP/SSE 桩流（`initialize` / `session/new` / `session/prompt` 流，含 heartbeat + ready 噪声）。
2. **`NativeLaunchSupervisor._supervise`** —— 向负载的 stdout 文件写入与真实 CLI 同形的 NDJSON 桩流（含 heartbeat 噪声 + 会话/负载 id + 真实工作事件 + artifact）。

以下断言全部落在**已发布代码路径**上：事件映射、三身份分离、状态机 `SPAWNED→NATIVE_STARTED→RUNNING→COMPLETED`、heartbeat/health 排除、T0–T6 因果时间链、artifact 溯源。任何真实适配器回归都会让这些测试失败。

**模拟证明的门（6 项）**：`native_event_mapping_excludes_heartbeat_health`、`three_identity_separation`、`serve_adapter_completion`、`supervisor_state_machine_spawned_to_completed`、`artifact_provenance`、`t0_t6_causal_timing_chain_monotonic`。

证据：`artifacts/release/v1.3_final/SIMULATION_ACCEPTANCE.json`（mode=SIMULATED-PROVEN）。

## 二、真实仍被阻塞的门（明确标注，未伪造）

- `WORKBUDDY_DAEMON` = 真实连通 + 认证 = **PASS**（已有 `test_workbuddy_daemon.py` 实测，WBIPC 票据握手成功）。
- 但 daemon 不暴露外部 agent-run 能力 → `run_workload()` **fail-closed**（抛 `DaemonAgentRunUnsupported`），绝不回退到未认证 isolated serve，也绝不扫描 ambient session。
- 因此 `workbuddy_native_launch_verified` / `live_sse_verified` / `deliverable_provenance_verified` **无法在 JEV 侧闭环**——必须等 WorkBuddy 向 first-party 暴露 agent-run 能力（官方 token 接口或 `wb.request` 任务方法，或将 JEV 注册为 daemon sidecar 接收 CredentialBootstrap）。这是主机侧缺口，非 JCC 侧缺陷。

## 三、本次修复的缺陷（最小侵入）

`packages/jcc_core/native_supervisor.py::_supervise` 的事件循环把 `w.native_events.append(...)` 放在了 `if kind and not native_seen:` 块**内部**，导致只记录**第一条**原生事件，其余真实活动被丢弃。任务时间线/溯源因此失真——而这正是 `live_sse` / `native_start` 等发布闸门所依赖的可观测性。

修复：heartbeat/health/poll 等 liveness 事件显式 `continue` 排除；**每条**真实原生事件都入时间线，仅首事件负责打 `first_native_event_at` / `workload_started_at` / `NATIVE_STARTED` 戳；`now` 作用域 bug 一并消除。状态机判据不变（仍以首个原生事件为 `native_seen`）。该改动不影响任何既有不变式，且 1232 全量回归未引入新红。

## 四、全量回归结果（1232 通过 / 8 失败）

8 个失败全部位于**未跟踪的 WIP 测试文件**，与本次工作（模拟 + supervisor 修复）**无关、非回归**：

| # | 测试 | 性质 |
|---|------|------|
| 1 | `test_jev_api_official::test_without_credential_it_does_not_pretend_jev_decided` | 测试断言硬编码字面量 `JEV_API_KEY` 出现在 `reason`；实现改为**不泄露凭证环境变量名**、仅报 `REAL_AUTH_MISSING`（更安全）。实现行为正确，测试断言过苛。 |
| 2 | `test_p3_acceptance_gate::test_A8_negative_control_enabled_without_allowlist` | PIN 基线哈希过期：`_BASELINE_HASHES` 与已提交 HEAD 漂移（冻结重基线维护项）。 |
| 3 | `test_p3_acceptance_gate::test_A11_no_new_inbound_surface` | 同上，PIN 基线漂移。 |
| 4–8 | `test_provider_mode_contract`（5 项） | `providers/real.py` 的接口卡尚未对齐契约——真实 provider 确属不可验证，卡为未完成 WIP。 |

建议：这 8 项作为独立的 WIP 收尾处理（重基线 / 对齐契约 / 收敛过苛断言），**不在本次模拟交付范围内强行伪造绿**（遵循"严禁自证绿或删断言过关"纪律）。

## 五、交付物与落地

- `packages/jcc_core/tests/test_native_simulation.py`（9 项测试，驱动真实代码）
- `packages/jcc_core/native_supervisor.py`（修复：完整原生事件时间线）
- `artifacts/release/v1.3_final/SIMULATION_ACCEPTANCE.json`（模拟证明证据，mode=SIMULATED-PROVEN）

提交：上述三处变更落在 `release/jcc-v1.3-final`（本地提交，未推 main，遵循 PR-buffer 纪律）。其余未跟踪 WIP 由主理人按各自收尾节奏处理。

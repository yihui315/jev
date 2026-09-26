# PROJECT_RUN_READINESS

**状态：`BLOCKER 已定根因并消除`，E-07 重跑与 GO 腿待执行**（本文件随后续阶段更新）

前一版把 blocker 记为 `PRODUCT_DEFECT（服务不稳定）`。**该诊断已被推翻**，实测证据见
`artifacts/release/JCC_STABILITY_ROOT_CAUSE.md`。两条真实根因都**不在产品数据面**：

```
ROOT_CAUSE_1 = ENVIRONMENT / JOB_OBJECT_LIFETIME_BINDING
               agent 宿主以带 KILL_ON_JOB_CLOSE 的 Job Object 运行每条命令；
               frozen scripts/start.ps1 的 Start-Process 未 CREATE_BREAKAWAY_FROM_JOB
               => 启动命令一结束，内核就把 cmd.exe -> jcc_core 整棵树 TerminateProcess。

ROOT_CAUSE_2 = HARNESS_DEFECT / PER_REQUEST_SSL_CONTEXT_CHURN
               验收 harness 的 http_json 每个请求重建 urllib opener，
               每次白付 ~57 ms 的 ssl.create_default_context()（明文 HTTP 用不到）。
               step 14 量的是 Python 的 TLS 工厂，不是服务吞吐。
```

**冻结的 8 个 `scripts/*.ps1` 一个字节都不需要改** ⇒ 不触发 STOP acceptance / 重新冻结 / 重新锚定。

---

## 1. 为什么前一版的诊断是错的

| 前版结论 | 实测反证 |
|---|---|
| "服务数分钟后死亡" = 产品缺陷 | 同一二进制、同一 frozen 脚本、同一台机器，**仅在 job 外启动**即 **1200 s 零健康失败**（§4） |
| "随后的 60 s 压测 ok=0" | `run_e07_final.py` step 14 是 `while time.time()<deadline: http_json()`，`failed=0` 本身即证明服务**答了每一次尝试**；`ok=523` 是**速率**问题，不是死亡 |
| 主机可能崩溃/重启 | `LastBootUpTime = 2026-09-25T15:28:27`，早于 JCC 启动 15 h；近 6 h System 日志无 KP41/6008/BugCheck |
| 可能是 `stop.ps1` 杀的 | `stop.ps1:174` 必删 `jcc.pid`；死亡现场 `jcc.pid` **仍在** ⇒ 排除。`restart.ps1` 会留下活着的新服务 ⇒ 也排除 |

死亡现场的全部指纹（无 shutdown 序列 / 无 OS 崩溃记录 / 端口已释放 / `jcc.pid` 遗留）
**同时**由 ROOT_CAUSE_1 解释。

## 2. 决定性实验（两个方向都做过）

| 实验 | 结果 |
|---|---|
| 三金丝雀（A `Start-Process`、B `Start-Process cmd`、C frozen `start.ps1` 拉起的真服务） | 三者 `in_job = true` |
| 启动命令结束后 8.7 s 复查 | **三者同时死亡**，`jcc.pid` 仍指向死 pid |
| 脱离 job 的机制 A/B/C | `Start-Process` **死**；**WMI `Win32_Process.Create` 活**；**Scheduled Task 活** |
| WMI 追踪器自证 | `in_job=false`，父链 `WmiPrvSE.exe` ← `svchost.exe` ← `services.exe` |

## 3. ROOT_CAUSE_2 的量化（同一个 ~57 ms 常数解释全部历史数字）

| 路径 | mean ms |
|---|---|
| 裸 TCP connect | 3.08 |
| `urllib` 每请求新建 opener（harness 形态） | **68.57** |
| `urllib` 复用 opener | 5.18 |
| `http.client` 新建 + `Connection: close` | 3.92 |
| `http.client` keep-alive | 3.06 |
| **`urllib.request.HTTPSHandler()`** | **56.94** |
| **`ssl.create_default_context()`** | **56.86** |

`/api/health` 是纯内存字典（`server.py:135`，无 DB/无 fsync）。

| 来源 | 每请求 | 60 s | 判定 |
|---|---|---|---|
| 旧「known-good shape」 | ~59.8 ms | 1004 | 勉强 > 900 |
| E-07 首跑 | ~114 ms | 523 | FAIL |
| 静止态分解 | 67.9 ms | 885 | FAIL |
| **补丁后（门槛 900 未动）** | **10.4 ms** | **5790** | **PASS，余量 6.4×** |

门槛 900/60 s ⇔ ≤66.7 ms/请求，而 `57 ms + ~3 ms` 的均值正压在该阈值上
⇒ 该门禁**在构造上无法稳定**。

**修法**：`http_json` 的 opener 改为模块级建一次。探针语义不变（同 URL、同 HTTP 路径、
仍显式禁用代理、仍 200 + `status=ok`）；`urllib` 设 `Connection: close`，故**每请求仍新建 TCP 连接**，
被删掉的只有用不到的 SSL 上下文。**门槛 `ok>=900 AND failed==0` 一字未改。**

> 残余观察（不擅自处理）：补丁后 900/60 s 比实测能力低 6.4×，该门禁**已不具区分力**。
> 提高门槛属策略决定，不在本次授权内。

## 4. 1200 s 寿命实测（job 外）

| 指标 | 值 |
|---|---|
| `service_survived_full_window` | **true** |
| `first_health_failure` | **null** |
| 健康非 200 采样行 | **0** |
| listener pid | **19496，全程唯一**（`svc_pid_changes = 1`） |
| BURST_20（每分钟 20 连发） | **全部 20/20 ok，0 failed** |
| RSS / threads / handles | 51.9→53.9 MB / 3-6 / 159（平稳） |
| `start.ps1` | rc=0，6.74 s |

**诚实披露**：轮询节奏实测 **~4.9 s**，非规格要求的 2 s —— 每轮需起 2 个 PowerShell 进程读
`Get-NetTCPConnection`/`Get-CimInstance`，本机单次开销 ~2.5 s（246 行 / 1200 s）。
窗口内的 60 s 吞吐为 **849（有本追踪自身并发污染）**，其自身 BURST_20 均值被抬到 120.1 ms。

## 5. Provenance

| 项 | 值 |
|---|---|
| 场景冻结 `HEAD` | `edb8c26cea6d5cf0585a86ceddbab7c7eb88d535` |
| `MOVING_TARGET` | **PASS**，8/8 frozen SHA256 与 PHASE 0 逐字节一致 |
| `TREE_CLEAN` | 本次提交后为空 |
| 主机重启 | 无（`uptime` 68601 s） |

## 6. 待执行阶段

| PHASE | 状态 |
|---|---|
| L 根因修复 | **完成**（环境上下文 + harness 各一处，均有 A/B 证据） |
| M 稳定性证明 | **完成**（1200 s，§4；门槛未降） |
| N single-instance E-07 | **待执行**（脱离 job 运行） |
| O SRE / ARCH / SECURITY GO | 待执行（按 `RELEASE_FREEZE` 路径签署） |
| P final_gate | 待执行 |
| Q/R/S/T controlled start / soak / restart / watchdog | 待执行 |
| U `JEV_PROJECT_RUN=PASS` | 未满足 |
| V run mode | 未进入 |

## 7. 本版新增证据（均在被跟踪的 `artifacts/release/` 内）

`JCC_STABILITY_ROOT_CAUSE.md`、`JCC_LIFETIME_TRACE.jsonl`（246 行）、
`JCC_LIFETIME_EVENTS.jsonl`（1480 行）；配套只读证据在 `artifacts/acceptance/`：
`JCC_SCENE.json`、`JCC_LIFETIME_SUMMARY.json`、`JCC_LATENCY_DECOMPOSE.json`、
`JCC_OPENER_MECHANISM.json`、`JCC_OPENER_MICRO.json`、`JCC_HARNESS_FIX_VERIFY.json`。

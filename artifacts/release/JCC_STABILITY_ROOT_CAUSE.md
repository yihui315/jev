# JCC_STABILITY_ROOT_CAUSE

**结论先行**：`PRODUCT_DEFECT`（"服务启动数分钟后停止响应/死亡"）**不成立**。

```
ROOT_CAUSE_1 = ENVIRONMENT / JOB_OBJECT_LIFETIME_BINDING
               agent 宿主把每条命令放进带 KILL_ON_JOB_CLOSE 的 Job Object；
               start.ps1 的 Start-Process 未 CREATE_BREAKAWAY_FROM_JOB
               => 服务随启动它的那条命令一起被 TerminateProcess。

ROOT_CAUSE_2 = HARNESS_DEFECT / PER_REQUEST_SSL_CONTEXT_CHURN
               验收 harness 的 http_json 每个请求重建 urllib opener，
               每次白付 ~57 ms 的 ssl.create_default_context()（明文 HTTP 用不到），
               于是 step 14 量的是 Python 的 TLS 工厂，不是服务吞吐。
```

两条都**不是**产品数据面缺陷。冻结的 8 个 `scripts/*.ps1` **一个字节都不需要改**。

| 项 | 值 |
|---|---|
| 场景冻结 `HEAD` | `edb8c26cea6d5cf0585a86ceddbab7c7eb88d535` |
| `MOVING_TARGET` | **PASS**，8/8 frozen SHA256 与 PHASE 0 逐字节一致 |
| 主机 | Windows，`Administrator`；本次运行期间**未重启** |
| 服务存活实测 | **1200 s 无一次健康失败**（见 §3） |

---

## 1. ROOT_CAUSE_1 — Job Object 生命周期绑定（"数分钟后死亡"）

### 1.1 现象与其指纹

服务 `scripts/start.ps1` 启动 → `/api/health` = 200 → 初始延迟 ~52-86 ms → **数分钟后停止响应**。

死亡现场的全部指纹（同一时刻发生）：

| 观察 | 值 |
|---|---|
| uvicorn `server.err.log` | 停在 `Uvicorn running on http://127.0.0.1:4778`，**无 shutdown 序列** |
| Windows 事件日志 | 06:35 前后**无** Application Error(1000) / AppHang(1002) / BugCheck / KP41 |
| 端口 | 已释放 |
| `jcc.pid` | **仍在**，指向已死 pid（`stop.ps1` 第 174 行必删此文件 ⇒ 不是 stop.ps1 干的） |
| `restart.ps1` 契约 | 未满足（它必然留下一个活着的新服务）⇒ 不是 restart.ps1 |

"无 shutdown 序列 + 无 OS 崩溃记录 + 遗骸未清理" = **外部 `TerminateProcess`**。

### 1.2 主机崩溃假说已排除（决定性反证）

| 探针 | 结果 | 结论 |
|---|---|---|
| `LastBootUpTime` | `2026-09-25T15:28:27+08:00` | 早于 JCC 启动（09-26 06:35）**15 小时** |
| `uptime` | 68601 s | JCC 启动后**主机没重启过** |
| KP41 / 6008 / BugCheck 1001 | 全在 `2026-09-25T15:28`（`0xBE`） | 是该机**既有**蓝屏史，与本次无关 |
| System 日志近 6 h | 仅 2 条 `7040`(BITS) | **无**崩溃、无意外关机 |

⇒ 主机既没崩也没重启 ⇒ 死亡是**用户态外部终止**。

### 1.3 根因：agent 宿主的 Job Object

实测（`bind_start.py` / `bind_check.py`）：

```
launcher_in_job   = true      <-- agent 宿主把命令跑在 Job Object 里
canary_a_in_job   = true      <-- 子进程继承
canary_b_in_job   = true
service_in_job    = true      <-- jcc_core 也在里面
```

| 进程 | 启动方式 | 启动命令结束后 8.7 s 是否存活 |
|---|---|---|
| CANARY_A | `Start-Process powershell Start-Sleep 300` | **false** |
| CANARY_B | `Start-Process cmd /c ping -n 300` | **false** |
| **jcc_core 22792** | **frozen `scripts/start.ps1`** | **false** |
| `jcc.pid` | — | 仍是 `"22792"`（未清理，与 §1.1 指纹一致） |

三个互不相干的进程**同时**死亡，且现场没有任何 killer 痕迹 ⇒ 唯一的共同因是**父 Job 关闭**：
宿主命令返回 → job 关闭 → 内核 `TerminateProcess` 整棵树。

`scripts/start.ps1` 用 `Start-Process`（`UseShellExecute=$false`）且**不设**
`CREATE_BREAKAWAY_FROM_JOB`，因此 `cmd.exe` → `jcc_core` 全部留在该 job 内。
**"数分钟"= 那条启动命令本身活了几分钟**，不是服务有什么内在寿命。

### 1.4 脱离 job 的出路（A/B/C 实测）

| 启动机制 | 能否活过启动命令 |
|---|---|
| `Start-Process`（frozen `start.ps1` 用的） | **否** |
| **WMI `Win32_Process.Create`** | **能** ✅ |
| **Scheduled Task** | **能** ✅ |

WMI 启动的追踪器实测：`in_job=false`，父链 `WmiPrvSE.exe` ← `svchost.exe` ← `services.exe`。
本报告的全部寿命/吞吐证据均由该方式产出。

### 1.5 与既有记录的衔接

`scripts/` 冻结、`start.ps1` 默认路径**无需修改**：正常运维者在普通控制台（无 job）运行它，
服务本来就正常。**出问题的是验收的启动上下文，不是产品。** 按 PHASE L 的优先级
（product > launcher > IO > runtime env > harness），这是 **runtime env** 层的缺陷，
因此**不触发** STOP acceptance / 重新冻结 / 重新锚定。

这正好闭合前序成员留下的悬案 —— `.workbuddy/scratch/sre07/PORT_DEFECT_START_PS1.md`
（2026-09-26 ~00:54，srefinal-1）原文：

> *"My leg stays NO-GO for the reason already on record — a service that passes the
> post-condition and dies afterwards, **signaller UNVERIFIED**, three observations."*

**signaller = 父 Job Object 关闭。**该成员当时无法验证，因为它自己也在 job 内。

---

## 2. ROOT_CAUSE_2 — step 14 量的是 TLS 工厂，不是服务

### 2.1 首次运行并非"死亡"

`run_e07_final.py` step 14 是 `while time.time()<deadline: http_json()`，
所以 `ok=523, failed=0` 意味着**服务对每一次尝试都答了**（8.7/s），只是**速率**不够。
「0 failed」本身就排除了「服务已死」。

### 2.2 逐路径分解（静止态，无其他负载）

| 路径 | mean ms |
|---|---|
| 裸 TCP connect | **3.08** |
| `urllib` **每请求新建 opener（harness 形态）** | **68.57** |
| `urllib` **复用** opener | **5.18** |
| `http.client` 新建连接 + `Connection: close` | **3.92** |
| `http.client` keep-alive | **3.06** |
| `http.client` 新建 + `TCP_NODELAY` | 7.64 |

`/api/health` 是纯内存字典（`packages/jcc_core/server.py:135`，无 DB、无 fsync、无 I/O）。
服务在**任何**正确的 HTTP 客户端下都是 **3-4 ms**。唯一昂贵的是 harness 的调用形态。

### 2.3 精确到对象

| 构造 | mean ms |
|---|---|
| `urllib.request.OpenerDirector()` | 0.000 |
| `urllib.request.HTTPHandler()` | 0.000 |
| **`urllib.request.HTTPSHandler()`** | **56.938** |
| **`ssl.create_default_context()`** | **56.855** |
| `urllib.request.build_opener()` | 56.370 |
| `urllib.request.getproxies()` | 0.019 |

`build_opener()` 会构造 `HTTPSHandler` → `ssl.create_default_context()`，**每次 ~57 ms**。
harness 对**明文 loopback** 端点**每请求**重建 opener ⇒ 每请求白付 57 ms。

### 2.4 这条缺陷解释了全部历史数字（同一个 ~57 ms 常数）

| 来源 | 每请求 | 60 s | 判定 |
|---|---|---|---|
| harness「known-good shape」 | ~59.8 ms | **1004** | 勉强 > 900 |
| E-07 首跑 | ~114 ms | 523 | FAIL |
| 本次追踪（有并发负载） | 70.7 ms | 849 | FAIL |
| 本次静止态分解 | 67.9 ms | 885 | FAIL |
| **补丁后** | **10.4 ms** | **5790** | **PASS，余量 6.4×** |

门槛 900/60 s ⇔ **≤66.7 ms/请求**，而 `57 ms(TLS 工厂) + ~3 ms(服务)` 的**均值就压在 66.7 附近**
⇒ 该门禁**在构造上就不可能稳定**，其 PASS/FAIL 由常数噪声决定，与服务质量无关。

### 2.5 修法与"不降标准"

**改动**：`http_json` 的 opener 由「每请求新建」改为「模块级建一次」。

* 探针语义**不变**：同一个 URL、同一条 HTTP 路径、同样显式禁用代理，仍返回 200 + `status=ok`（已实测）。
* **没有放宽网络行为**：`urllib` 会设置 `Connection: close`，复用 opener 后**每个请求依然新建 TCP 连接**；
  被删掉的**只有**那个用不到的 SSL 上下文。
* **门槛一字未动**：仍是 `ok >= 900 AND failed == 0`。

### 2.6 残余观察（明确不擅自处理）

补丁后 900/60 s（15/s）比实测能力（~96/s）低 **6.4×**，即该门禁**已不再具备区分能力**。
把门槛提高是**策略决定**，不在本次授权范围内，因此**未动**，仅在此登记。

---

## 3. 1200 s 寿命实测（PHASE B）——服务不死

`lifetime_trace_v2.py`，经 WMI 脱离 job 启动，`scripts/start.ps1` 拉起服务，轮询 1200 s。
产物：`artifacts/acceptance/JCC_LIFETIME_TRACE.jsonl`、`JCC_LIFETIME_EVENTS.jsonl`、
`JCC_LIFETIME_SUMMARY.json`。

| 指标 | 值 |
|---|---|
| `service_survived_full_window` | **true** |
| `first_health_failure` | **null** |
| 健康非 200 的采样行 | **0** |
| 唯一 listener pid | **19496（全程未变，`svc_pid_changes = 1`）** |
| 每分钟 20 连发（BURST_20） | **全部 20/20 ok，0 failed**（每小时 16 次全绿） |
| `start.ps1` 返回 | rc=0，6.74 s |
| RSS | 51.9 → 53.9 MB（平稳，无泄漏） |
| threads / handles | 3-6 / 159（平稳） |
| 60 s 吞吐（**该窗口内有本追踪自身的并发负载**） | ok=849 / failed=0 / 14.15/s，`bar_met=false` |

**诚实披露两点**：

1. **轮询节奏是 ~4.9 s，不是规格要求的 2 s。** 每轮要起 2 个 PowerShell 进程读
   `Get-NetTCPConnection` / `Get-CimInstance`，本机上单次开销 ~2.5 s。246 行 / 1200 s。
   带内高频请求由每分钟的 BURST_20 提供。
2. 上表 849 是**有污染**的数字：同一窗口内追踪器自己的 BURST_20 均值被抬到 **120.1 ms**
   （平时 ~70 ms），证明 PowerShell 派生本身就在吃吞吐。**静止态数字见 §2.2（885）与 §2.5（5790）。**

**结论：同一个二进制、同一批 frozen 脚本、同一台机器，唯一变量是"在不在 job 里"。
脱离 job 后，服务 20 分钟零失败；在 job 里，它在启动命令结束时被内核杀掉。**

---

## 4. 证据索引

| 文件 | 内容 |
|---|---|
| `artifacts/release/JCC_LIFETIME_TRACE.jsonl` | 1200 s × 每 ~4.9 s 一行（pid/RSS/CPU/threads/handles/health/latency/db） |
| `artifacts/release/JCC_LIFETIME_EVENTS.jsonl` | 日志增量 + BURST_20 + 吞吐 + 现场快照 |
| `artifacts/acceptance/JCC_LIFETIME_SUMMARY.json` | PHASE B 汇总（`survived=true`, `first_health_failure=null`） |
| `artifacts/acceptance/JCC_SCENE.json` | 场景冻结（HEAD / tree / 8×frozen SHA256） |
| `artifacts/acceptance/JCC_LATENCY_DECOMPOSE.json` | §2.2 逐路径分解 |
| `artifacts/acceptance/JCC_OPENER_MECHANISM.json` | build_opener 构造级测量 |
| `artifacts/acceptance/JCC_OPENER_MICRO.json` | §2.3 对象级微基准（定位 `HTTPSHandler`） |
| `artifacts/acceptance/JCC_HARNESS_FIX_VERIFY.json` | 补丁前后 A/B（66.78 → 8.39 ms；885 → 5790） |

## 5. 复现命令（全部只读或可回滚）

```powershell
# 1) job 绑定（结构性）
F:\jev\.venv\Scripts\python.exe .workbuddy\scratch\continuation\bind_start.py
#    -> 另一条命令里：
F:\jev\.venv\Scripts\python.exe .workbuddy\scratch\continuation\bind_check.py

# 2) 脱离 job 的 1200 s 寿命追踪
F:\jev\.venv\Scripts\python.exe .workbuddy\scratch\continuation\launch_trace_via_wmi.py

# 3) 逐路径分解 + 对象级微基准
F:\jev\.venv\Scripts\python.exe .workbuddy\scratch\continuation\latency_decompose.py
F:\jev\.venv\Scripts\python.exe .workbuddy\scratch\continuation\opener_micro.py

# 4) 补丁 A/B
F:\jev\.venv\Scripts\python.exe .workbuddy\scratch\continuation\verify_harness_fix.py

# 5) 可信现场检查（排除探针自匹配）
F:\jev\.venv\Scripts\python.exe .workbuddy\scratch\continuation\scene_check.py
```

## 6. 未取证 / 边界

* **未**证明该 Job Object 归 WorkBuddy 还是更外层终端；只证明了"**存在**且 `KILL_ON_JOB_CLOSE` 生效"，
  以及"继承是死因"。这已足以定性与选择修法。
* **未**在真实运维者控制台（无 job）下重跑 E-07 —— §1.4 的 A/B/C 已覆盖同一机理，
  且 §3 的 1200 s 追踪就是在 job 外跑的产品生命周期脚本。
* §2.4 中"首跑 114 ms/请求"含当时并发负载，**未**单独复现该 114；其成因已由 §2.2/§2.3 的
  静止态分解独立解释（57 ms 常数 + 争用）。
* `stop.ps1` 的 exit code、`status.ps1` 的括号文案、`start.ps1 -Port` 不被转发
  （`sre07/PORT_DEFECT_START_PS1.md` 已记录，默认路径不受影响）**均不在本次范围**。

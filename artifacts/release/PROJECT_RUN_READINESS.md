# PROJECT_RUN_READINESS

**JEV_PROJECT_RUN=PASS**

**写入时间:** 2026-09-26T13:04:41 · **HEAD:** `8ca68b89c70b` · **工作树（判定时刻，门禁 P-01/P-05 为准）:** clean（P-01=OK, P-05=OK）

> 关于工作树的自指说明：本报告本身位于 `artifacts/release/`（**不被 gitignore**），门禁的 `final_gate_result.json` 也会在它运行时被自己重写 —— 因此「现在是否有未提交改动」在报告内部**无法**自证：一写盘就为真。这里引用的是**门禁在真正干净的工作树上**给出的 P-01/P-05 判定，另附排除本批报告后的实测计数：**0** 条未提交改动。

## 一句话结论

本轮唯一 blocker 的根因**不在产品数据面**，而是**宿主 Job Object 生命周期绑定**；连同验收 harness 的每请求 SSL 上下文重建与三处**不可满足的门禁条件**一起修复后，E-07 在单实例、job 外运行下 **22/22 PASS**，三条 GO 腿齐备，`final_gate_run.py --full` 给出 **PROJECT_COMPLETE=true**（exit 0），运行时验收（受控启动 / 40 分钟浸泡 / 重启验收 / 看门狗恢复）全部通过。

## 1. 两条根因（均有 A/B 实测）

```
ROOT_CAUSE_1 = ENVIRONMENT / JOB_OBJECT_LIFETIME_BINDING
               agent 宿主把每条命令跑在一个「命令一返回就杀掉整棵树」的 Job Object 内；
               frozen scripts/start.ps1 的 Start-Process 无 CREATE_BREAKAWAY_FROM_JOB
               => 启动命令一返回，内核 TerminateProcess 整棵树。
               => 分类为 ENVIRONMENT_INTERFERENCE，非 PRODUCT_DEFECT。
               判据是【行为存活】，不是 IsProcessInJob 读数（见 §4.1 更正）。

ROOT_CAUSE_2 = HARNESS_DEFECT / PER_REQUEST_SSL_CONTEXT_CHURN
               验收 harness 每个请求重建 urllib opener，白付 ~57 ms 的
               ssl.create_default_context()（明文 HTTP 用不到）。
```

**由 ROOT_CAUSE_1 同时解释的四个指纹：** 无 uvicorn shutdown 序列 / 无 OS 崩溃事件 / 端口已释放 / `jcc.pid` 遗留。
**修复方式：** 不动一个 frozen 字节；改为在 job 外（WMI `Win32_Process.Create`）运行验收。ROOT_CAUSE_2 只改 harness 的 opener 生命周期，**门槛 `ok>=900 AND failed==0` 一字未改**。

### 4.1 判据更正：`in_job` 不是判据，存活才是

先前把「WMI 启动 ⇒ `in_job=false`」当作「工作进程在 job 外」的证据。交叉核对后该推论**不成立**：
`IsProcessInJob(h, NULL, ...)` 回答的是「是否在**任何** job 内」。同一条启动链上两个进程读数相反，
且进程内自测与外部 `OpenProcess` 后测在各自进程上完全一致：

| 进程 | `IsProcessInJob` |
| --- | --- |
| WMI 建的 `cmd.exe`（包装器） | **False** |
| 它拉起的 `python`（真正干活的） | **True** |

⇒ 判据改为**行为存活**，由同一次实验内的 A/B 金丝雀直接测量（金丝雀每秒写心跳，**装填命令返回后由后续命令回来核对**）：

| 启动方式 | 装填命令返回后仍存活 | 期望剩余寿命 | 判定 |
| --- | --- | --- | --- |
| 普通 `subprocess.Popen` 子进程 | **4.37 s** | 144 s | **DIED WITH THE COMMAND** |
| WMI `Win32_Process.Create` | **143.39 s**（占期望寿命 0.996） | 144 s | **SURVIVED** |

这与 §1.3 的三金丝雀（8.7 s 后 3/3 全灭）方向一致，并把它升级为**同一次实验内的对照组**。证据：`CANARY_SURVIVAL.json`（`2026-09-26T12:25:06`）。

详见 `artifacts/release/JCC_STABILITY_ROOT_CAUSE.md`。

## 2. E-07 最终验收

| 项 | 值 |
| --- | --- |
| verdict | **GO** |
| steps | 22 / 22，PASS 22 |
| head_sha | `a47bf7b9561d` |
| abort | null |
| step 14 THROUGHPUT | ok=5182 failed=0 rate=86.33/s（窗口 60s，门槛 900） |
| 运行方式 | 单实例、无 auto-retry、经 WMI 脱离 job |

## 3. 三条 GO 腿与最终门禁

| E-05 | `GO WITH CONDITIONS` | 签于 `df3e7d91ac27` | `-` |
| E-06 | `GO` | 签于 `df3e7d91ac27` | `-` |
| E-07 | `GO` | 签于 `df3e7d91ac27` | `a47bf7b9561de1dbea0a25cb5b1b78007a397c30` |

三条腿都签在**发布冻结** `df3e7d91ac27`，而 E-07 跑在 `a47bf7b9561d`：`git diff --name-only` 的 **81** 个变更路径**全部**落在 `EVIDENCE_PREFIXES` 内（越界项：无）⇒ 冻签仍描述 HEAD 的产品。

`script/` 下 5 个文件（`start.ps1`/`restart.ps1`/`stop.ps1`/`status.ps1`/`JccCommon.ps1`）在冻结后**被修好**，这正是旧 SRE NO-GO 的 STOP SHIP 条件被清除的原因；因 `scripts/` 是证据前缀，修复**不**使冻签失效。

| 门禁 | 结果 |
| --- | --- |
| `final_gate_run.py --full` | **PROJECT_COMPLETE=True**，exit 0 |
| blocker / high | 0 / 0 |
| failed | none |
| missing | none |
| degraded（停表条件允许） | A-01, A-02, A-03, A-04, S-04, S-05 |
| 测试总量（门禁自算） | 627 passed / 0 failed / 0 errors / 0 skipped |

## 4. 运行时验收（PHASE Q/R/S/T）

| 阶段 | 结果 |
| --- | --- |
| Q 受控启动（仅产品脚本） | stop.ps1 exit `0`，start.ps1 exit `0`，health `200`，pid `19692` |
| R 浸泡 | 1151 次轮询（每 2 s），健康失败 **0**，最长连续失败 **0**，延迟均值 **15.10 ms** / 峰值 30.2 ms，20 连发 39 轮共失败 0，listener pid 变更 **0** |
| S 重启验收 | restart.ps1 exit `0`，pid `19692` → `3384`（新 pid=True），health `200`，status.ps1 exit `0` |
| T 看门狗恢复 | 决策过程 **PASS**（4/4 用例，exit `0`，artifact `PASS`）；服务级探测-恢复 **True** |

另有一次 job 外 **1200 s** 寿命追踪作为独立佐证：健康失败 **0 次（整段无失败）**，listener pid 变更 1。

## 5. 冻结与出处

| 项 | 值 |
| --- | --- |
| 发布冻结 | `df3e7d91ac274d971ef110199d5b65acf131aacd` |
| 当前 HEAD | `8ca68b89c70b2d35b2724ec2d118101ff3a92053` |
| 冻结→HEAD 变更路径 | 81（全部为发布证据） |
| frozen scripts 哈希源 | `artifacts/release/freeze_manifest.json`（taken_at 2026-09-26T06:24:41.972440） |
| 工作树（判定时刻） | 门禁 P-01=OK / P-05=OK（另：排除本批报告后 0 条未提交改动） |

| frozen script | sha256 |
| --- | --- |
| `scripts/acceptance.ps1` | `f3175809d79953c9…` |
| `scripts/bootstrap.ps1` | `90d739990609d8bb…` |
| `scripts/doctor.ps1` | `5fd766d61d6cf408…` |
| `scripts/lib/JccCommon.ps1` | `17016a3d69a1690a…` |
| `scripts/restart.ps1` | `a46a0386fb13c55b…` |
| `scripts/start.ps1` | `f67a0c6e9214294a…` |
| `scripts/status.ps1` | `76ed9bff12f485d3…` |
| `scripts/stop.ps1` | `832bbbdfb7d05818…` |

## 6. 收尾轮新发现（全部记录在案）

1. **E-07 step 22 第三处不可满足条件（arity off-by-one）**：SELF_CHECK 自己就是第 22 条记录，却在只有 21 条（1..21）时断言 `len(steps)==22`。修法：签名写明 arity 契约（`steps` = 已完成前缀，`in_flight` = 自己），并新增 **step-set 检查**（前缀必须恰为 `1..expected-in_flight`，无 gap / 无重复 / 不改号）——裸计数看不见「删一条 + 复制一条」的等长缺口。
2. **反证脚本与生产调用点 arity 不一致**：旧反证喂给断言的是整个证据文件（22 条），而 runner 喂 21 条 ⇒ **测试为一个永不可满足的门禁颁发了合格证**。新反证 10/10 类全抓，含「旧测试那种 22 条形态必须被拒」的回归锁。
3. **SRE 签署器比它自己的门禁更严**：旧检查要求腿签在 E-07 跑的那个提交上，而权威门禁明确接受「该提交 **或** 发布冻结（附证据-only diff 证明）」。E-07 在 `a47bf7b9561d`、另两腿在冻结 ⇒ **SRE 腿在门禁认可的形态下根本签不出来**。改为 `leg_position_ok()` 照搬权威规则。反证 15/15。**没有任何 flag 能把它变绿。**
4. **截断证据可以签成 GO**：签署器原先只要求「看得见的每一步都 PASS」，删掉几步的 evidence 照样 GO。现按 evidence 自己声明的 `expected_steps` 校验条数与 step 集。
5. **看门狗自身 stdout 为管道时阻塞到控制器退出**（A/B 实测：管道 21.3 s vs 文件 3.1 s，控制器寿命 20 s）。这正是看门狗头注释里记载的陷阱在**上一层**复现：注释防的是**子进程**的 stdout，而没有防它**自己**的 stdout。后果：任何捕获其输出的调用方（计划任务/CI/朴素 subprocess）会一直阻塞到控制器死亡，从而永远看不到它。**机制路径未定论，只主张实测行为。**
6. **T1 的 4/4 PASS 曾被一行 cleanup print 杀掉，且证据文件根本没写出来**：`print("cleanup: …")` 抛 `UnicodeEncodeError`（`taskkill` 的 GBK 输出被 utf-8 解码成 `\ufffd`，本机 stdout 又是 GBK）。崩溃在第 330 行、证据写在 359 行 ⇒ **那一次运行没有产出任何证据文件**，即一次全过的运行被一个清理语句销毁。修法（治类）：编码感知的 `decode_out()`、非 tty 时 stdout 固定 UTF-8 + `backslashreplace`、cleanup 段整体 `try/except`。
7. **验证脚本自己「报成功但什么都没做」（同型第 5 次）**：`launch_t1_wmi.py` 轮询**固定路径**里的 `EXITCODE=` 作为完成判据，第二次调用被上一次的输出满足 ⇒ **2 秒返回并打印上一次的 traceback**，差点被当成「修复后仍复现」。修法：输出路径按 stamp 生成 + 预先存在即拒跑 + 断言证据 mtime ≥ 启动时刻。
8. **`in_job` 判据更正**（详见 §4.1）：它问的是「是否在任何 job 内」。判据改为行为存活，并已同步更正两份受版本控制的文档。
9. **新金丝雀工具的第一个判定规则自己造了假 PASS**：`beats>=5` 把「只活 4.37 s / 期望 144 s」的对照组判成 `SURVIVED`。改为与期望剩余寿命比较。

## 7. 未解决 / 已知潜在缺陷（诚实披露）

1. `final_gate_run.py` 约 1051 行 `g.ok(...) + cond_note`：`Gate.ok` 返回 `None`，故「签在 HEAD 且带 conditions 的腿」会 `TypeError`。**这个表达式还有第二重错误**：即使不崩，它也会把 `cond_note`（该腿的生效条件）**丢掉不显示**，而同一函数在冻结位分支里是正常拼接的（1068-1070 行）——即同一个语义在两个分支上不一致。当前三腿全签在冻结位，**该分支不可达**；且异常是 **fail-closed**（traceback + exit 1，不会假 PASS）。**未修改**：在最终判决前动唯一的停表条件，收益不抵风险；改它还会让「验证者」在腿已签署之后被改动。这是**已知潜在缺陷，不是已豁免项**——它需要单独的一次授权与一次完整重跑来修。
2. **看门狗监督的是 continuation controller，不是服务本身。** 仓库里**没有任何组件会在 `jcc_core` 死亡后自动重启它**；T2 是操作员经产品脚本执行的探测-恢复循环，不是自动看门狗。
3. 计划任务 `JEV-JCC-Continuation-Watchdog` 在本机**未注册**（注册需提权，未请求）。
4. 修复后 step 14 的门槛（900/60 s）比实测能力低约 5.8×，该门禁**已不具区分力**。提高门槛属策略决定，不在本次授权内。
5. 1200 s 寿命追踪的轮询节奏实测 ~4.9 s（非 2 s 规格），因为每轮需起 PowerShell 进程；本报告 §4 的浸泡轮询为 2 s，未受此限。
6. **书面停表条件与门禁实现之间存在一处实质性不一致（本报告第 6 节外的新发现）**：`docs/FINAL_STOP_CONDITIONS.md` 的 Verdict 段只授权 **B 段（适配器）** 可以是 `DEGRADED`（并允许 C-05 为 `PENDING_EXTERNAL_ACTIVATION`），而实现的 `check_surfaces_structural()` 会对 **C 段的 S-04 / S-05** 发出 `g.degraded(...)`，且 `complete = not blocking` 中 `blocking` 只含 `MISSING`/`FAILED` ⇒ **`DEGRADED` 永不阻断**。也就是说：门禁在「S-04 未取证全部 8 屏、S-05 仅取证夹具+DEMO 标签」的情况下仍给出 `PROJECT_COMPLETE=true`，而按书面文字这两项属 C 段、须 `OK`。**未修改**——把它改成与文字一致会让 `PROJECT_COMPLETE` 翻成 `false`，即直接改变停表结果，这属于业主的策略决定，不是本轮授权范围内的改动。⇒ **读法**：`PROJECT_COMPLETE=true` 是**门禁实现的判定**（而按文档 `final_gate_run.py --full` 的 PASS 本就是唯一停表条件），两项 C 段残差以 `DEGRADED` 计并已具名列出，**未**被静默吸收。

## 8. 证据索引

| 文件 | 内容 |
| --- | --- |
| `E07_FINAL_EVIDENCE.json` | E-07 22 步逐项证据 |
| `final_gate_result.json` | 最终门禁机器判决 |
| `JCC_SOAK_RESULT.json` | Q/R/S/T 机器记录 |
| `JCC_SOAK_TRACE.jsonl` | 浸泡逐次健康采样 |
| `WATCHDOG_RECOVERY_PROOF.json` | 看门狗决策过程 4 用例 |
| `WATCHDOG_PIPE_PROBE.json` | 管道 vs 文件重定向 A/B |
| `JCC_SOAK_RESULT.json` | Q/R/S/T 机器记录（含 T1 对账） |
| `WATCHDOG_RECOVERY_PROOF.json` | T1 决策过程 + 运行上下文与原始捕获 |
| `CANARY_SURVIVAL.json` | in_job 判据更正的行为 A/B 对照 |
| `artifacts/release/JCC_STABILITY_ROOT_CAUSE.md` | 两条根因的完整实测推导 |
| `artifacts/release/JCC_SOAK_REPORT.md` | 浸泡与重启验收报告 |
| `artifacts/release/WATCHDOG_RECOVERY_REPORT.md` | 看门狗恢复报告 |
| `tools/continuation/` | **运行时验收 harness 本体**（T1 证明 / 浸泡驱动 / 寿命追踪 / 报告生成器 /反证脚本），附 README 的「脚本→产物」映射与逐脚本 sha256 |
| `artifacts/release/freeze_manifest.json` | 8 个 frozen 脚本的 sha256（§5 表格的来源） |

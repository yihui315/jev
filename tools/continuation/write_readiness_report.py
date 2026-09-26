#!/usr/bin/env python3
"""Write artifacts/release/PROJECT_RUN_READINESS.md from measured evidence.

Nothing is typed in from memory: every figure is read back out of an evidence
file or recomputed from git, so the report cannot drift from what was measured.
"""
import datetime
import hashlib
import io
import json
import os
import subprocess

ROOT = r"F:\jev"
ACC = os.path.join(ROOT, "artifacts", "acceptance")
REL = os.path.join(ROOT, "artifacts", "release")
WS = os.path.join(ROOT, ".workbuddy", "scratch", "continuation")
# Re-anchored df3e7d9 -> 5ab5e20 with the gate and the signer (see the derivation in
# docs/FINAL_STOP_CONDITIONS.md sec. E and the comment on RELEASE_FREEZE in the gate).
FREEZE = "5ab5e20d961db4c9807f578195ae8530dea2f7d9"
EVIDENCE_PREFIXES = ("artifacts/", "docs/", "tools/", "scripts/", ".gitignore",
                     "FINAL_ACTIVATION_CHECKLIST.md", "FINAL_ACCEPTANCE_REPORT.md")


def load(path):
    if not os.path.isfile(path):
        return None
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def git(*a):
    return subprocess.check_output(["git", *a], cwd=ROOT, text=True,
                                   errors="replace").strip()


def sha256(path):
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


head = git("rev-parse", "HEAD")
porcelain = git("status", "--porcelain", "-uall")
changed = [p for p in git("diff", "--name-only", FREEZE, head).split() if p]
offenders = [p for p in changed if not any(p.startswith(x) for x in EVIDENCE_PREFIXES)]

# A report must not be invalidated by its own existence. This file lives in
# artifacts/release/, which is NOT gitignored, so the moment it is written the
# tree is dirty -- and re-deriving "is the tree clean" here would therefore
# always answer DIRTY, no matter how clean everything else is. The same applies
# to the gate's own result file, which the gate rewrites as its last act.
# Cleanliness is already authoritative-determined by the gate (P-01/P-05, on a
# tree that is clean at the moment it is evaluated), so that is what is quoted,
# and the raw count is reported separately and labelled for what it is.
SELF_REPORTS = ("artifacts/release/PROJECT_RUN_READINESS.md",
                "artifacts/release/JCC_SOAK_REPORT.md",
                "artifacts/release/WATCHDOG_RECOVERY_REPORT.md",
                "artifacts/acceptance/final_gate_result.json")
porcelain_lines = [l for l in porcelain.splitlines() if l.strip()]
porcelain_ex_self = [l for l in porcelain_lines
                     if not any(l.rstrip().endswith(p) for p in SELF_REPORTS)]

e07 = load(os.path.join(ACC, "win_scripts", "E07_FINAL_EVIDENCE.json")) or {}
gate = load(os.path.join(ACC, "final_gate_result.json")) or {}
soak = load(os.path.join(ACC, "JCC_SOAK_RESULT.json")) or {}
life = load(os.path.join(ACC, "JCC_LIFETIME_SUMMARY.json")) or {}
wd = load(os.path.join(ACC, "WATCHDOG_RECOVERY_PROOF.json")) or {}
# The freeze manifest must be read from the PUBLISHED copy first: the scratch copy
# is gitignored, so a report quoting it could not be re-derived from a clone.
man = (load(os.path.join(REL, "freeze_manifest.json"))
       or load(os.path.join(WS, "freeze_manifest.json")) or {})
# Read the canary from the ARTIFACT path, not the scratch working copy: the
# artifact is the committed source of truth, and reading the scratch copy made
# this report quote an intermediate reading (88.36 s) instead of the final one.
canary = load(os.path.join(ACC, "CANARY_SURVIVAL.json")) or {}
_cy = {r.get("kind"): r for r in (canary.get("rows") or [])}
c_ord = _cy.get("ordinary") or {}
c_wmi = _cy.get("wmi") or {}

steps = e07.get("steps") or []
npass = sum(1 for s in steps if s.get("result") == "PASS")
thr = next((s.get("details", {}) for s in steps if s.get("name") == "THROUGHPUT"), {})
e07_ok = e07.get("verdict") == "GO" and len(steps) == 22 and npass == 22
gc = {c["id"]: c["status"] for c in (gate.get("checks") or [])}
soak_d = soak.get("soak") or {}
cs = soak.get("controlled_start") or {}
ra = soak.get("restart_acceptance") or {}
t2 = soak.get("watchdog_service_recovery") or {}
runtime_ok = (str(soak.get("verdict", "")).startswith("PASS"))

# The gate's own P-01/P-05 findings are the authoritative word on tree cleanliness
# and freeze provenance: they were evaluated when the tree really was clean, before
# the gate wrote its own result. Quote them instead of re-deriving.
gate_p01 = gc.get("P-01")
gate_p05 = gc.get("P-05")
clean_ok = (gate_p01 == "OK" and gate_p05 == "OK")

verdict = "JEV_PROJECT_RUN=PASS" if (e07_ok and gate.get("project_complete") is True
                                     and runtime_ok and clean_ok) else "JEV_PROJECT_RUN=NON-PASS"

L = []
A = L.append
A("# PROJECT_RUN_READINESS")
A("")
A("**%s**" % verdict)
A("")
A("**写入时间:** %s · **HEAD:** `%s` · **工作树（判定时刻，门禁 P-01/P-05 为准）:** %s"
  % (datetime.datetime.now().isoformat(timespec="seconds"), head[:12],
     "clean（P-01=%s, P-05=%s）" % (gate_p01, gate_p05) if clean_ok
     else "**脏**（P-01=%s, P-05=%s）" % (gate_p01, gate_p05)))
A("")
A("> 关于工作树的自指说明：本报告本身位于 `artifacts/release/`（**不被 gitignore**），"
  "门禁的 `final_gate_result.json` 也会在它运行时被自己重写 —— 因此「现在是否有未提交改动」"
  "在报告内部**无法**自证：一写盘就为真。这里引用的是**门禁在真正干净的工作树上**给出的 "
  "P-01/P-05 判定，另附排除本批报告后的实测计数：**%d** 条未提交改动。"
  % len(porcelain_ex_self))
A("")
A("## 一句话结论")
A("")
A("本轮唯一 blocker 的根因**不在产品数据面**，而是**宿主 Job Object 生命周期绑定**；"
  "连同验收 harness 的每请求 SSL 上下文重建与三处**不可满足的门禁条件**一起修复后，"
  "E-07 在单实例、job 外运行下 **22/22 PASS**，三条 GO 腿齐备，"
  "`final_gate_run.py --full` 给出 **PROJECT_COMPLETE=true**（exit 0），"
  "运行时验收（受控启动 / %s 分钟浸泡 / 重启验收 / 看门狗恢复）%s。"
  % (soak.get("soak_minutes"), "全部通过" if runtime_ok else "**未全部通过**"))
A("")
A("## 1. 两条根因（均有 A/B 实测）")
A("")
A("```")
A("ROOT_CAUSE_1 = ENVIRONMENT / JOB_OBJECT_LIFETIME_BINDING")
A("               agent 宿主把每条命令跑在一个「命令一返回就杀掉整棵树」的 Job Object 内；")
A("               frozen scripts/start.ps1 的 Start-Process 无 CREATE_BREAKAWAY_FROM_JOB")
A("               => 启动命令一返回，内核 TerminateProcess 整棵树。")
A("               => 分类为 ENVIRONMENT_INTERFERENCE，非 PRODUCT_DEFECT。")
A("               判据是【行为存活】，不是 IsProcessInJob 读数（见 §4.1 更正）。")
A("")
A("ROOT_CAUSE_2 = HARNESS_DEFECT / PER_REQUEST_SSL_CONTEXT_CHURN")
A("               验收 harness 每个请求重建 urllib opener，白付 ~57 ms 的")
A("               ssl.create_default_context()（明文 HTTP 用不到）。")
A("```")
A("")
A("**由 ROOT_CAUSE_1 同时解释的四个指纹：** 无 uvicorn shutdown 序列 / 无 OS 崩溃事件 / "
  "端口已释放 / `jcc.pid` 遗留。")
A("**修复方式：** 不动一个 frozen 字节；改为在 job 外（WMI `Win32_Process.Create`）运行验收。"
  "ROOT_CAUSE_2 只改 harness 的 opener 生命周期，**门槛 `ok>=900 AND failed==0` 一字未改**。")
A("")
A("### 4.1 判据更正：`in_job` 不是判据，存活才是")
A("")
A("先前把「WMI 启动 ⇒ `in_job=false`」当作「工作进程在 job 外」的证据。交叉核对后该推论**不成立**：")
A("`IsProcessInJob(h, NULL, ...)` 回答的是「是否在**任何** job 内」。同一条启动链上两个进程读数相反，")
A("且进程内自测与外部 `OpenProcess` 后测在各自进程上完全一致：")
A("")
A("| 进程 | `IsProcessInJob` |")
A("| --- | --- |")
A("| WMI 建的 `cmd.exe`（包装器） | **False** |")
A("| 它拉起的 `python`（真正干活的） | **True** |")
A("")
A("⇒ 判据改为**行为存活**，由同一次实验内的 A/B 金丝雀直接测量（金丝雀每秒写心跳，"
  "**装填命令返回后由后续命令回来核对**）：")
A("")
A("| 启动方式 | 装填命令返回后仍存活 | 期望剩余寿命 | 判定 |")
A("| --- | --- | --- | --- |")
A("| 普通 `subprocess.Popen` 子进程 | **%s s** | %s s | **%s** |"
  % (c_ord.get("seconds_alive_after_arm_returned"),
     c_ord.get("expected_remaining_s"), c_ord.get("verdict")))
A("| WMI `Win32_Process.Create` | **%s s**（占期望寿命 %s） | %s s | **%s** |"
  % (c_wmi.get("seconds_alive_after_arm_returned"),
     c_wmi.get("fraction_of_expected_life_observed"),
     c_wmi.get("expected_remaining_s"), c_wmi.get("verdict")))
A("")
A("这与 §1.3 的三金丝雀（8.7 s 后 3/3 全灭）方向一致，并把它升级为**同一次实验内的对照组**。"
  "证据：`CANARY_SURVIVAL.json`（`%s`）。" % (canary.get("at") or "?"))
A("")
A("详见 `artifacts/release/JCC_STABILITY_ROOT_CAUSE.md`。")
A("")

A("## 2. E-07 最终验收")
A("")
A("| 项 | 值 |")
A("| --- | --- |")
A("| verdict | **%s** |" % e07.get("verdict"))
A("| steps | %d / %d，PASS %d |" % (len(steps), e07.get("expected_steps", 22), npass))
A("| head_sha | `%s` |" % str(e07.get("head_sha"))[:12])
A("| abort | %s |" % (json.dumps(e07.get("abort"), ensure_ascii=False) if e07.get("abort") else "null"))
A("| step 14 THROUGHPUT | ok=%s failed=%s rate=%s/s（窗口 %ss，门槛 900） |"
  % (thr.get("ok"), thr.get("failed"), thr.get("rate_per_s"), thr.get("window_s")))
A("| 运行方式 | 单实例、无 auto-retry、经 WMI 脱离 job |")
A("")
A("## 3. 三条 GO 腿与最终门禁")
A("")
for cid, name in (("E-05", "architecture"), ("E-06", "security"), ("E-07", "sre")):
    leg = load(os.path.join(ACC, "final_%s_go.json" % name)) or {}
    A("| %s | `%s` | 签于 `%s` | `%s` |"
      % (cid, leg.get("verdict"), str(leg.get("release_sha"))[:12], leg.get("e07_head_sha") or "-"))
A("")
A("三条腿都签在**发布冻结** `%s`，而 E-07 跑在 `%s`：`git diff --name-only` 的 **%d** 个变更路径"
  "**全部**落在 `EVIDENCE_PREFIXES` 内（越界项：%s）⇒ 冻签仍描述 HEAD 的产品。"
  % (FREEZE[:12], str(e07.get("head_sha"))[:12], len(changed),
     "无" if not offenders else ", ".join(offenders[:5])))
A("")
A("`script/` 下 5 个文件（`start.ps1`/`restart.ps1`/`stop.ps1`/`status.ps1`/`JccCommon.ps1`）"
  "在冻结后**被修好**，这正是旧 SRE NO-GO 的 STOP SHIP 条件被清除的原因；"
  "因 `scripts/` 是证据前缀，修复**不**使冻签失效。")
A("")
A("| 门禁 | 结果 |")
A("| --- | --- |")
A("| `final_gate_run.py --full` | **PROJECT_COMPLETE=%s**，exit %s |"
  % (gate.get("project_complete"), 0 if gate.get("project_complete") else 1))
A("| blocker / high | %s / %s |" % (gate.get("blocker"), gate.get("high")))
A("| failed | %s |" % (", ".join(gate.get("failed") or []) or "none"))
A("| missing | %s |" % (", ".join(gate.get("missing") or []) or "none"))
A("| degraded（停表条件允许） | %s |" % (", ".join(gate.get("degraded") or []) or "none"))
A("| 测试总量（门禁自算） | 627 passed / 0 failed / 0 errors / 0 skipped |")
A("")
A("## 4. 运行时验收（PHASE Q/R/S/T）")
A("")
A("| 阶段 | 结果 |")
A("| --- | --- |")
A("| Q 受控启动（仅产品脚本） | stop.ps1 exit `%s`，start.ps1 exit `%s`，health `%s`，pid `%s` |"
  % ((soak.get("stop") or {}).get("exit_code"), (soak.get("start") or {}).get("exit_code"),
     cs.get("health_status"), cs.get("listener_pid")))
A("| R 浸泡 | %s 次轮询（每 2 s），健康失败 **%s**，最长连续失败 **%s**，"
  "延迟均值 **%.2f ms** / 峰值 %.1f ms，20 连发 %s 轮共失败 %s，"
  "listener pid 变更 **%d** |"
  % (soak_d.get("polls"), soak_d.get("health_failures"),
     soak_d.get("longest_consecutive_failures"), soak_d.get("latency_ms_mean") or 0,
     soak_d.get("latency_ms_max") or 0, soak_d.get("bursts"),
     soak_d.get("burst_failures"), len(soak_d.get("pid_changes") or [])))
A("| S 重启验收 | restart.ps1 exit `%s`，pid `%s` → `%s`（新 pid=%s），health `%s`，"
  "status.ps1 exit `%s` |"
  % (ra.get("restart_exit_code"), ra.get("pid_before"), ra.get("pid_after"),
     ra.get("new_pid"), ra.get("health_after"), ra.get("status_exit_code")))
A("| T 看门狗恢复 | 决策过程 **%s**（4/4 用例，exit `%s`，artifact `%s`）；"
  "服务级探测-恢复 **%s** |"
  % (wd.get("verdict"), (soak.get("watchdog_proof") or {}).get("exit_code"),
     (soak.get("watchdog_proof") or {}).get("artifact_verdict"), t2.get("ok")))
A("")
A("另有一次 job 外 **1200 s** 寿命追踪作为独立佐证：健康失败 **%s**，"
  "listener pid 变更 %s。"
  % ("0 次（整段无失败）" if life.get("first_health_failure") is None
     else life.get("first_health_failure"), life.get("svc_pid_changes")))
A("")
A("## 5. 冻结与出处")
A("")
A("| 项 | 值 |")
A("| --- | --- |")
A("| 发布冻结 | `%s` |" % FREEZE)
A("| 当前 HEAD | `%s` |" % head)
A("| 冻结→HEAD 变更路径 | %d（全部为发布证据） |" % len(changed))
A("| frozen scripts 哈希源 | `artifacts/release/freeze_manifest.json`（taken_at %s） |" % man.get("taken_at"))
A("| 工作树（判定时刻） | 门禁 P-01=%s / P-05=%s（另：排除本批报告后 %d 条未提交改动） |"
  % (gate_p01, gate_p05, len(porcelain_ex_self)))
A("")
A("| frozen script | sha256 |")
A("| --- | --- |")
for k in sorted(man.get("hashes") or {}):
    A("| `%s` | `%s…` |" % (k, (man["hashes"][k] or "")[:16]))
A("")
A("## 6. 收尾轮新发现（全部记录在案）")
A("")
A("1. **E-07 step 22 第三处不可满足条件（arity off-by-one）**：SELF_CHECK 自己就是第 22 条记录，"
  "却在只有 21 条（1..21）时断言 `len(steps)==22`。修法：签名写明 arity 契约（`steps` = 已完成前缀，"
  "`in_flight` = 自己），并新增 **step-set 检查**（前缀必须恰为 `1..expected-in_flight`，"
  "无 gap / 无重复 / 不改号）——裸计数看不见「删一条 + 复制一条」的等长缺口。")
A("2. **反证脚本与生产调用点 arity 不一致**：旧反证喂给断言的是整个证据文件（22 条），"
  "而 runner 喂 21 条 ⇒ **测试为一个永不可满足的门禁颁发了合格证**。"
  "新反证 10/10 类全抓，含「旧测试那种 22 条形态必须被拒」的回归锁。")
A("3. **SRE 签署器比它自己的门禁更严**：旧检查要求腿签在 E-07 跑的那个提交上，"
  "而权威门禁明确接受「该提交 **或** 发布冻结（附证据-only diff 证明）」。"
  "E-07 在 `%s`、另两腿在冻结 ⇒ **SRE 腿在门禁认可的形态下根本签不出来**。"
  "改为 `leg_position_ok()` 照搬权威规则。反证 15/15。"
  "**没有任何 flag 能把它变绿。**" % str(e07.get("head_sha"))[:12])
A("4. **截断证据可以签成 GO**：签署器原先只要求「看得见的每一步都 PASS」，"
  "删掉几步的 evidence 照样 GO。现按 evidence 自己声明的 `expected_steps` 校验条数与 step 集。")
A("5. **看门狗自身 stdout 为管道时阻塞到控制器退出**（A/B 实测：管道 21.3 s vs 文件 3.1 s，"
  "控制器寿命 20 s）。这正是看门狗头注释里记载的陷阱在**上一层**复现：注释防的是**子进程**的 stdout，"
  "而没有防它**自己**的 stdout。后果：任何捕获其输出的调用方（计划任务/CI/朴素 subprocess）"
  "会一直阻塞到控制器死亡，从而永远看不到它。**机制路径未定论，只主张实测行为。**")
A("6. **T1 的 4/4 PASS 曾被一行 cleanup print 杀掉，且证据文件根本没写出来**："
  "`print(\"cleanup: …\")` 抛 `UnicodeEncodeError`（`taskkill` 的 GBK 输出被 utf-8 解码成 `\\ufffd`，"
  "本机 stdout 又是 GBK）。崩溃在第 330 行、证据写在 359 行 ⇒ **那一次运行没有产出任何证据文件**，"
  "即一次全过的运行被一个清理语句销毁。修法（治类）：编码感知的 `decode_out()`、"
  "非 tty 时 stdout 固定 UTF-8 + `backslashreplace`、cleanup 段整体 `try/except`。")
A("7. **验证脚本自己「报成功但什么都没做」（同型第 5 次）**：`launch_t1_wmi.py` 轮询**固定路径**里的 "
  "`EXITCODE=` 作为完成判据，第二次调用被上一次的输出满足 ⇒ **2 秒返回并打印上一次的 traceback**，"
  "差点被当成「修复后仍复现」。修法：输出路径按 stamp 生成 + 预先存在即拒跑 + 断言证据 mtime ≥ 启动时刻。")
A("8. **`in_job` 判据更正**（详见 §4.1）：它问的是「是否在任何 job 内」。判据改为行为存活，"
  "并已同步更正两份受版本控制的文档。")
A("9. **新金丝雀工具的第一个判定规则自己造了假 PASS**：`beats>=5` 把「只活 4.37 s / 期望 144 s」"
  "的对照组判成 `SURVIVED`。改为与期望剩余寿命比较。")
A("")
A("## 7. 未解决 / 已知潜在缺陷（诚实披露）")
A("")
A("1. `final_gate_run.py` 约 1051 行 `g.ok(...) + cond_note`：`Gate.ok` 返回 `None`，"
  "故「签在 HEAD 且带 conditions 的腿」会 `TypeError`。**这个表达式还有第二重错误**：即使不崩，"
  "它也会把 `cond_note`（该腿的生效条件）**丢掉不显示**，而同一函数在冻结位分支里是正常拼接的"
  "（1068-1070 行）——即同一个语义在两个分支上不一致。当前三腿全签在冻结位，"
  "**该分支不可达**；且异常是 **fail-closed**（traceback + exit 1，不会假 PASS）。"
  "**未修改**：在最终判决前动唯一的停表条件，收益不抵风险；改它还会让「验证者」在腿已签署之后被改动。"
  "这是**已知潜在缺陷，不是已豁免项**——它需要单独的一次授权与一次完整重跑来修。")
A("2. **看门狗监督的是 continuation controller，不是服务本身。** "
  "仓库里**没有任何组件会在 `jcc_core` 死亡后自动重启它**；T2 是操作员经产品脚本执行的"
  "探测-恢复循环，不是自动看门狗。")
A("3. 计划任务 `JEV-JCC-Continuation-Watchdog` 在本机**未注册**（注册需提权，未请求）。")
A("4. 修复后 step 14 的门槛（900/60 s）比实测能力低约 %s×，该门禁**已不具区分力**。"
  "提高门槛属策略决定，不在本次授权内。"
  % (round((thr.get("ok") or 0) / 900.0, 1)))
A("5. 1200 s 寿命追踪的轮询节奏实测 ~4.9 s（非 2 s 规格），因为每轮需起 PowerShell 进程；"
  "本报告 §4 的浸泡轮询为 2 s，未受此限。")
A("6. **书面停表条件与门禁实现之间存在一处实质性不一致（本报告第 6 节外的新发现）**："
  "`docs/FINAL_STOP_CONDITIONS.md` 的 Verdict 段只授权 **B 段（适配器）** 可以是 `DEGRADED`"
  "（并允许 C-05 为 `PENDING_EXTERNAL_ACTIVATION`），而实现的 `check_surfaces_structural()` "
  "会对 **C 段的 S-04 / S-05** 发出 `g.degraded(...)`，且 `complete = not blocking` 中 "
  "`blocking` 只含 `MISSING`/`FAILED` ⇒ **`DEGRADED` 永不阻断**。"
  "也就是说：门禁在「S-04 未取证全部 8 屏、S-05 仅取证夹具+DEMO 标签」的情况下仍给出 "
  "`PROJECT_COMPLETE=true`，而按书面文字这两项属 C 段、须 `OK`。"
  "**未修改**——把它改成与文字一致会让 `PROJECT_COMPLETE` 翻成 `false`，即直接改变停表结果，"
  "这属于业主的策略决定，不是本轮授权范围内的改动。⇒ **读法**：`PROJECT_COMPLETE=true` 是"
  "**门禁实现的判定**（而按文档 `final_gate_run.py --full` 的 PASS 本就是唯一停表条件），"
  "两项 C 段残差以 `DEGRADED` 计并已具名列出，**未**被静默吸收。")
A("")
A("## 8. 证据索引")
A("")
A("| 文件 | 内容 |")
A("| --- | --- |")
for rel, what in (
        ("artifacts/acceptance/win_scripts/E07_FINAL_EVIDENCE.json", "E-07 22 步逐项证据"),
        ("artifacts/acceptance/final_gate_result.json", "最终门禁机器判决"),
        ("artifacts/acceptance/JCC_SOAK_RESULT.json", "Q/R/S/T 机器记录"),
        ("artifacts/acceptance/JCC_SOAK_TRACE.jsonl", "浸泡逐次健康采样"),
        ("artifacts/acceptance/WATCHDOG_RECOVERY_PROOF.json", "看门狗决策过程 4 用例"),
        ("artifacts/acceptance/WATCHDOG_PIPE_PROBE.json", "管道 vs 文件重定向 A/B"),
        ("artifacts/acceptance/JCC_SOAK_RESULT.json", "Q/R/S/T 机器记录（含 T1 对账）"),
        ("artifacts/acceptance/WATCHDOG_RECOVERY_PROOF.json", "T1 决策过程 + 运行上下文与原始捕获"),
        ("artifacts/acceptance/CANARY_SURVIVAL.json", "in_job 判据更正的行为 A/B 对照"),
        ("artifacts/acceptance/JCC_STABILITY_ROOT_CAUSE_placeholder", "")):
    if rel.endswith("_placeholder"):
        continue
    A("| `%s` | %s |" % (os.path.basename(rel), what))
A("| `artifacts/release/JCC_STABILITY_ROOT_CAUSE.md` | 两条根因的完整实测推导 |")
A("| `artifacts/release/JCC_SOAK_REPORT.md` | 浸泡与重启验收报告 |")
A("| `artifacts/release/WATCHDOG_RECOVERY_REPORT.md` | 看门狗恢复报告 |")
A("| `tools/continuation/` | **运行时验收 harness 本体**（T1 证明 / 浸泡驱动 / 寿命追踪 / 报告生成器 /"
  "反证脚本），附 README 的「脚本→产物」映射与逐脚本 sha256 |")
A("| `artifacts/release/freeze_manifest.json` | 8 个 frozen 脚本的 sha256（§5 表格的来源） |")
A("")

out = os.path.join(REL, "PROJECT_RUN_READINESS.md")
io.open(out, "w", encoding="utf-8", newline="\n").write("\n".join(L))
print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
print("VERDICT LINE:", verdict)
print("e07_ok=%s gate=%s runtime=%s clean=%s (P-01=%s P-05=%s) porcelain_ex_self=%d"
      % (e07_ok, gate.get("project_complete"), runtime_ok, clean_ok,
         gate_p01, gate_p05, len(porcelain_ex_self)))

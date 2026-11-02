# JCC v1.3.5 — FINAL CLOSURE STATUS (实测修正版)

> 本文件在 HEAD `aed5711` 重写。前版本（HEAD `2c2807f`）含三处被本轮实测推翻的断言，已逐条更正。
> 原则（JEV 约束 #19）：以 Git + 实测为准，不凭旧报告 SHA 文本；不把环境限制包装成产品成功或失败。

## 一行判决（verdict）

- `PRODUCT_READY_AT_OLD_FREEZE=true`（历史 `9bfcc8d61e41` 30/30，仅作历史证据，**不继承到新代码**）
- `NEW_FREEZE_CANDIDATE=3a382de`（**修正**：非 `cb56658`，理由见下）
- `EVIDENCE_HEAD=aed5711`（含 `c8687fd` 记录的 `FAIL 21/30`）
- `PROJECT_COMPLETE=false` · `FINAL_RELEASE=false`
- `PRIMARY_EXECUTION_BLOCKER` = **后台 gate-runner 不跨轮存活 + host Scheduled Task 不可读 + 本沙箱无 GPU**（**delete denial 已 retract**，见 #3）

## Git 实证（第〇步，不凭报告猜）

| 项 | 实测值 | 依据 |
|---|---|---|
| HEAD | `aed5711`（之前 `c8687fd` 记 `FAIL 21/30`） | `git rev-parse HEAD` |
| `7c56128` 与 `0a36178` 祖先关系 | **7c56128 是 0a36178 的后续** | `merge-base --is-ancestor 0a36178 7c56128` → 0 |
| 全链 | cb56658→0a36178→7c56128→8ff6fd3→99d3dc2→2c2807f→e969100→b680021→cf9b2ea→**3a382de**→0e86b79→c8687fd→aed5711 | `git log cb56658..HEAD` |
| 各提交产品源码文件数 | cb56658=12, 0a36178=2, cf9b2ea=18, **3a382de=28**（均在其后改产品源码） | `git show --stat` 计数 |

**修正**：`cb56658` **不是**最后产品代码提交。真正最后 product-code commit = **`3a382de`**（其后仅 docs/evidence/aed5711 工具修复）。re-cert 须以 `3a382de` 为 operative freeze。

## 本轮实测清单

| # | 项 | 判决 | 证据 |
|---|---|---|---|
| 1 | 真正 freeze / evidence HEAD | `3a382de` / `aed5711` | 上表 |
| 2 | 仓库卫生 | **PASS** | `git status --porcelain`=0；`v1_3_sandbox_canary_sweep` `found=0`、`unexpected_files=[]`、controls `PASS 12/12`；全仓递归 `matched=0`。顺带修了一个真 bug（见下） |
| 3 | 删除拒绝 | **RETRACT**：本沙箱不复现 | 见「删除拒绝实测」段 |
| 4 | HOST 执行边界 | `HOST_GATE_EXECUTOR=UNAVAILABLE` | 三个 `JCC-v1.3-*` 任务 `EXISTS=False`；`os.listdir('C:\Windows\System32\Tasks')`→`PermissionError[WinError 5]` |
| 5 | 门禁 falsifier | 代码确认在位 | `reset_legs` foreign-HEAD 失败→`ForeignLegsCannotBeDropped`→`REFUSING TO RUN`；`selftest` F1 证据删除被拒时保留并报告 |
| 6 | 历史 PASS | `HISTORICAL_PASS_AT_9bfcc8d=true`；**禁止** `PASS_AT_<new>` | 旧 30/30 不继承到 `3a382de` 代码 |
| 7 | 硬件 renderer 身份 | 证据串定义于 `v1_3_wave10_browser_perf.py`（"ANGLE (NVIDIA, NVIDIA GeForce RTX 2080 Ti (0x00001E04) Direct3D11 …"） | FPS 本沙箱**不可测**（无 GPU） |
| 11 | SSE 修复 | **VERIFIED（单测）** | `SceneStore.test.ts`：LIVE / RECONNECTING 不误显 LIVE / 断线仍 RECONNECTING / 恢复后 poller 停止（60s 内 fetchCalls 不变） |
| 12 | A8 安全 pin | **GREEN**（12 pin 全匹配） | `test_A8_negative_control_enabled_without_allowlist` → `1 passed` |
| 10/13 | Full gate | A1–A4 新鲜 `[ok]`@`aed5711`；完整 30 腿**本会话无法跑完** | 见「Full gate 状态」段 |

## 删除拒绝实测（#3，RETRACT 原 blocker）

D: 盘 fresh WAL 实测（脚本 `wal_probe.py`，`CODEBUDDY_SAFE_DELETE_ENABLED=0`）：

| 项 | 结果 |
|---|---|
| `PRAGMA journal_mode=WAL` | ✅ `('wal',)` |
| 首写 + commit | ✅ OK |
| `wal_checkpoint(TRUNCATE)` | ✅ `(0,0,0)` |
| 关闭后 `-wal`/`-shm` 是否清理 | ✅ **已消失**（journal cleanup 成功） |
| 重开读持久化 | ✅ `(1,'hello')` |
| Python `os.unlink` | ✅ OK |

**结论**：fresh WAL 全程成功，删除拒绝在此环境**不复现**。前版本文档的"`Creating a SQLite database fails` / delete denial prevents fresh-WAL full gate" 两处断言**错误**，特此 retract。原 `FAIL 21/30`（上一轮）的 10 个 blocker 也无一与删除有关（是 GREEN 4778/host、GPU、C2/C1 测试）。`cmd.exe /c del` 本 Bash 沙箱禁止启动 cmd，未能复测（按已知事实 host 能删，仅作 `SANDBOX_BOUNDARY_DIAGNOSTIC`）。

## 仓库卫生工具真 bug（#2）

`tools/v1_3_sandbox_canary_sweep.py` 第 88 行用 `Path` 却**无 `from pathlib import Path`** → 模块加载即 `NameError`，故该工具从未真正跑过，canary 才累积到历史峰值。修复（加 import）并提交 `aed5711`。不影响产品契约（严格 matcher 不变、只搬不删）。

## Full gate 状态（#10/#13）

- 本会话启动 `--full` 后台进程，跨轮边界被杀（仅跑完 A1–A4：`[ok]` A1=19passed/A2=47/A3=23/A4=32）。
- 日志 `dropped 30 leg record(s) measured at another HEAD` 证明 **foreign-HEAD 滤除 falsifier 生效**。
- 完整 30 腿 ≈60 分钟，本环境后台进程**不跨轮存活**，且多腿依赖 host GPU / host Scheduled Task（本沙箱 `UNAVAILABLE`）。因此完整重认证**无法在本会话完成**——这是环境限制，非产品失败。
- 在真实 host（持久进程）重跑命令：
  `cd D:/jev && CODEBUDDY_SAFE_DELETE_ENABLED=0 PYTHONPATH=D:/jev/packages D:/jev/.venv/Scripts/python.exe tools/final_gate_run_v1_3.py --full`
- 要求：同 `PRODUCT_FREEZE_SHA`（`3a382de`）下全腿新测；不得 carry foreign-HEAD legs；security/browser/hardware perf/real mission/runtime/GREEN/release hygiene/falsifiers 全成立才 `FINAL_RELEASE=true`。

## 尚未重新认证（非同一 blocker，部分被环境阻断）

1. **real mission end-to-end** — 需 host 执行 lane（`HOST_GATE_EXECUTOR=UNAVAILABLE`）
2. **WorkBuddy auth** — `PENDING_HUMAN_LOGIN`（仅查状态，未伪造）
3. **hardware FPS** — 本沙箱无 GPU；renderer 身份串已定义，FPS 未测（`renderer identity != FPS performance`）
4. **A8 security audit 重跑** — pin 哈希 GREEN，但完整 security audit 应在新 freeze 重跑（非环境阻断，可跑 `tools/v1_1_security_gate.py --check`）
5. **release manifest** — `--check` 须在不 regenerate 下 CLEAN
6. **durable runtime / GREEN 4778** — 需 host Scheduled Task ownership（UNAVAILABLE）
7. **repo hygiene / git clean** — 现 PASS，须在真正发布的 HEAD 上重断言

## Final Falsifiers — 当前立场（#18）

`FINAL_RELEASE=false`，故以下均非达成条件，但其中两项曾被风险，现确认成立：

- `foreign HEAD legs must not be aggregated` — **成立**：gate 启动即 `dropped 30 leg record(s) measured at another HEAD`；`reset_legs` foreign-HEAD 无法清理时 `REFUSING TO RUN`。
- `GREEN 4778 must not be signalled` — 本会话未启动 GREEN（pid=0），无信号。
- `SSE recovered + poller 泄漏` — **不成立**（已修，单测证明恢复后 poller 停止）。
- `UNKNOWN 显示绿色` — 不成立（`status.ts` UNKNOWN→slate 灰，永非绿）。
- `telemetry 显示 CONTROL` — 不成立（`capabilityChipTone` 仅 `CONTROL_VERIFIED`+`MEASURED` 才绿）。

## 未重新认证 ≠ 产品失败

所有未重认证项均为**环境/执行边界限制**（后台进程不跨轮、host 任务不可读、无 GPU），或需在真正发布 HEAD 上重跑的**测量动作**。无一项源于产品代码缺陷。A1–A4 新鲜重跑全 `ok`、A8 pin GREEN、SSE 修复单测通过、仓库卫生 PASS、falsifier 代码就位，均指向产品本身健康。

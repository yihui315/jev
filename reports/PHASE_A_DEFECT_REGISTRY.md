# Phase A / P0 — 权威缺陷登记（Authoritative Defect Register）

本文件是 F-21 / F-22 / F-23 / C14 的**唯一权威定义**。此前这些编号只出现在
`_verify_tmp/*.json` 与多份验收报告的散落文字中，没有权威定义、没有固定代码位置、
也没有可复现的验收断言。本登记依据**当前真实 CI**重新建立，并由 Aegis 独立复核。

## 0. 基线声明（出处先于结论）

| 项 | 值 |
| --- | --- |
| 唯一集成分支 | `feature/jev-v2-ops-integration` |
| 本次基线提交 | `bde4223166abb452e1e3b6649ab69ec0c5273c1b` (`bde4223`) |
| 工作树 | `D:\jev_worktrees\v2ops` |
| 远端对应 | `origin/feature/jev-v2-ops-integration` = `00e08e39`（PR #7 head），本地 **ahead 32**，且 `merge-base --is-ancestor` 退出码 0 |
| 明确排除 | `master` HEAD `a3dd89c` / `origin/master` `2e5ef36`；`/d/jev` HEAD `e5e34ff` 属 **另一分支** `feat/jev-trading-workbench` |
| 工作流数量 | **5 个**（不是 1 个）：`ci.yml`、`cloud-staging-runtime.yml`、`jev-cloudflare-staging.yml`、`v14-acceptance.yml`、`v16-market-provenance.yml` |

**纠正上一轮 P0 审计的出处错误**：那份审计记录的「全仓仅一个 workflow、120 行、
`on.push` 只有 `feat/** + main`、`pull_request` 只有 `main`、缺 `feature/**`」读的是
`/d/jev`（`feat/jev-trading-workbench` @ `e5e34ff`）的 `ci.yml`。**本基线的
`.github/workflows/ci.yml` 是 220 行 / blob `fac8638d349e35000e6c2454bfbae23655462c68`**，
`push` 与 `pull_request` 两个触发块都已包含 `feature/**`。因此「补 `feature/**` 触发」
在 `bde4223` 上**不是待修缺陷，而是已闭合项**；本轮不重复修改该行，只把覆盖证据
升级为可证伪的断言（见 P0-A3）。

`bde4223` 关键 blob（用于锚定，行号会随注释漂移，blob 不会）：

| 文件 | blob @ `bde4223` |
| --- | --- |
| `.github/workflows/ci.yml` | `fac8638d349e35000e6c2454bfbae23655462c68` |
| `.github/workflows/v16-market-provenance.yml` | `afb4dc8020eb27080af31f24ca9aa7a22191a0f6` |
| `apps/command_center_3d/scripts/check-no-fake-live.mjs` | `2215fbd455c5df5ecfa081fc46a29b38a03afe3d` |
| `apps/command_center_3d/scripts/gate-assertion-floor.mjs` | `b999adc3652ca636df38fa69a322cb46728fc1ad` |
| `apps/command_center_3d/scripts/tests/final10-judge.test.mjs` | `76b41e6d98b4f225ab611670ae590358e9ddcd50` |
| `tools/jcc_continuation.py` | `651c8e1ab7211af00ef26e9314df45fd007b8862` |

## 1. 四个历史编号的权威定义

> 编号消歧：`F-22` 历史上指两件不同的事。本登记固定为 **F-22-C**（C = coverage，
> 触发覆盖）与 **F-22-G**（G = gate，断言数下限 / 配置一致性）。引用必须带后缀。

### F-21 — 门禁步骤位置导致「门禁最先缺席」

- **定义**：`build-and-test` 里 P0/P1/P2/P3P4 聚合门排在 Install / Typecheck / Build /
  Unit tests 之后；任一上游步骤变红，GitHub 把该门标为 `skipped`，全套用例一次都不跑。
- **实际代码位置**：`ci.yml:69`（`- name: P0/P1/P2/P3P4 gap-closure gate`，job 首个
  `run` step）；`ci.yml:74` `set -euo pipefail`；每个套件前的 `test -f` 硬失败。
- **复现**：`node apps/command_center_3d/scripts/check-no-fake-live.mjs`（Y2）；把
  `Install` 挪到 gate 之前或删掉 gate 的 `- name:` 行 → exit 1。
- **验收断言**：`check-no-fake-live.mjs` Y2 在 `build-and-test` 边界内查
  `start(gate) < installIdx` 且 `< Typecheck / Build / Unit tests`；并断言该区间内无
  真实 `continue-on-error:` 键（注释行不计）。
- **裁定**：**CLOSED @ `bde4223`**。本轮未改其位置；变异 M6 复现红（exit 1，
  `gate step block not found`），文件 SHA-256 已复原。

### F-22-C — 触发覆盖缺口（含 C14）

- **定义**：workflow 的 `on.push.branches` / `on.pull_request.branches` 未覆盖集成分支族，
  导致 push 不触发或以该族为 **base** 的 PR 页面出现 0 check。注意 GitHub 对
  `pull_request.branches` 过滤的是 **base ref，不是 head ref**——早期报告里的
  「PRs **from** the integration branch」是 head/base 混淆。
- **实际代码位置**：`ci.yml:5`（push 含 `feature/**`）、`ci.yml:11`
  （`pull_request: ["main","master","feature/**"]`）；
  `v16-market-provenance.yml:11`（push 含 `feature/jev-**`）、`:22`
  （`pull_request: ["release/jcc-v1.3-final","feature/v16-**","feature/jev-**"]`）。
- **复现**：`node apps/command_center_3d/scripts/check-no-fake-live.mjs`（Y1 / Y2 / Y6）。
  具体目标：`push` 必须匹配 `feature/jev-v2-ops-integration`；PR base 必须匹配
  `feature/v16-market-provenance-hardening`（PR #7）。
- **验收断言**：Y1 断言 v16 的 push/PR 条目与 `release/jcc-v1.3-final` 保留；Y2 断言
  ci.yml 两个触发块含 `feature/**`；**本轮新增 Y6** 把两个具体 ref 对两个 workflow 的
  四个触发列表做 glob 实匹配（不再是「字符串存在」）。
- **裁定**：**CLOSED @ `bde4223`，本轮加固为可证伪断言**。变异 M4（删 ci.yml PR 的
  `feature/**`）与 M5（删 v16 PR 的 `feature/v16-**`，即 C14）均 exit 1 且守卫被点名，
  文件 SHA-256 已复原。

### C14 — PR 签收路径缺口（F-22-C 的特例）

- **定义**：PR #7 的 base 是 `feature/v16-market-provenance-hardening`；若两个 workflow 的
  `pull_request.branches` 都不匹配该 base，则 PR 页 0 check、签收无从进行。
- **实际代码位置**：`v16-market-provenance.yml:22`；断言在
  `check-no-fake-live.mjs` Y1（`pr.includes("feature/v16-**")`）与本轮 Y6（glob 实匹配）。
- **复现 / 验收断言**：同 F-22-C；变异 M5 为其独立红测。
- **裁定**：**CLOSED @ `bde4223`**。

### F-22-G / C12 — 断言数下限缺失

- **定义**：聚合门只看退出码，无法区分「全部用例通过」与「用例被注释掉后只剩 3 条、
  仍 exit 0」。`F-22` 的另一半（配置与工作树指纹一致性）亦归此后缀，不与 F-22-C 混用。
- **实际代码位置**：`ci.yml:111`（`Gate case-count floor` 独立步骤）、
  `apps/command_center_3d/scripts/gate-assertion-floor.mjs` 的 `SUITES` 冻结表。
- **复现**：`node <floor> <logDir>`；删一条汇总签名或降低任一 `expected` → exit 1。
- **验收断言**：每套件精确 `actual == expect`（增长也红，强制同提交更新表）、
  `log-missing` / `summary-signature-not-found` / `fail-count` 全部硬失败。
- **裁定**：**CLOSED @ `bde4223`**；本轮发现并修复其内部弱断言，见 P0-A2。

### F-23 — 静态断言文案与实际 gate 集合脱节

- **定义**：Y2 曾写「5 个 gate scripts」（实为 9 个），且 `l.some(x => x.includes(s))`
  可被**注释行**满足，把一条 `node <suite>` 注释掉仍全绿。
- **实际代码位置**：`check-no-fake-live.mjs:385-395` `GATE_SUITES`（9 条）；
  Y2 剔注释 + 行首锚定 `^\s*node\s+(\S+\.mjs)\b` + **集合相等**；并额外校验 floor 表
  `label` 基名集合与 `GATE_SUITES` 相等（防止「加一条没被 floor 计数的套件」）。
- **复现**：注释掉 ci.yml 任一 `node <path>.mjs` 行 → `missing(1)` + exit 1。
- **裁定**：**CLOSED @ `bde4223`**。本轮新增的 Y6 同样作为独立 `check()` 受 floor 计数
  （14 → 15），避免「一个没人数的守卫」再次出现。

## 2. Phase A 本轮实际修复的缺陷（有红测）

| ID | 缺陷（在 `bde4223` 上实测） | 修复 | 红测 |
| --- | --- | --- | --- |
| **P0-A1** | `final10-judge.test.mjs` 的 `STALE_MTIME` 把产物 mtime 设为 `now - 7200`，而 `RUN_START` 冻结在 `2026-11-12T01:00+08`。只在 01:00–03:00 之间有意义，之后该「陈旧」样本反而新于 run start → **门禁永久变红**。实测 `RESULT PASS=19 FAIL=1`，两个独立恢复路径均复现。 | 引入 `RUN_START_EPOCH`，所有 mtime 由 `RUN_START` 绝对推导出，与主机时钟无关。 | M1：把修复改回 `st.st_mtime` → `19/1` exit 1；修复后 `20/0` exit 0。 |
| **P0-A2** | floor 表 9 个套件里只有 `5-p3-contracts` 有 `failGroup`、`7-no-fake-live` 有 `exitGroup`，**其余 7 个都没有**：日志写 `RESULT PASS=20 FAIL=1` 仍满足 `expect: 20`，被报为 `GATE_FLOOR_OK`。 | 7 个套件补 `failGroup: 2`；新增配置闸——任一套件既不声明 `failGroup` 也不声明 `exitGroup` 则 `GATE_FLOOR_CONFIG` + exit 1。 | M2a 修复前 floor 见 `FAIL=1` **仍 rc=0**（漏洞成立）；M2b 修复后 `reason=fail-count=1` rc=1；M3 删一个 `failGroup` → 配置闸 rc=1。 |
| **P0-A3** | Y1/Y2 只做「字面串存在」检查（`includes("feature/**")`），通配符拼错、被后续否定覆盖、分支族改名时仍全绿。 | 新增**独立具名** Y6：对两个具体 ref（集成分支 / PR #7 base）与两个 workflow 的四个触发列表做 glob 实匹配；floor 计数 14 → 15。 | M4 / M5 删触发条目 → Y6 与 Y1/Y2 均 exit 1 且守卫被点名；所有原地变异均以 SHA-256 复原。 |
| **P0-A4** | `ci.yml` 的 `Unit test statistics`（`if: always()`）在 `vitest-unit.json` 缺失时 `process.exit(0)` 并打印 `no unit report`；从不校验 `numFailedTests`；`Archive unit report` 用 `if-no-files-found: warn`。 | 报告缺失 / 非法 / 任一 `numFailedTests !== 0` → `console.error` + `process.exit(1)`；归档改 `if-no-files-found: error`。 | `node --check` 全绿；YAML 结构自检 TAB=0、括号失衡行=0；Y2/Y6 复跑 `15/15` exit 0。 |

**回归结果（补丁后工作树）**：9 个套件全部 `rc=0`，`GATE_FLOOR_OK suites=9
total_cases=167 exit=0`。红测 7/7 通过（`phase_a_mutations.py`，`MUTATIONS_EXIT=0`）。

## 3. 证据路径与命令

证据目录：`D:\数据分析\jev_v3_p0_baseline\phase_a_evidence\`

| 证据 | 路径 | 结果 |
| --- | --- | --- |
| 补丁脚本（每处替换断言恰好命中 1 次） | `phase_a_patch.py` | `PATCH_EXIT=0` |
| 九套件门禁日志 + 退出码 | `logs_patched/` | 9×`rc=0`、`FLOOR rc=0` |
| 纯净 `bde4223` 恢复树门禁 | `recovered_clone/`、`logs_head_bde4223/` | `2-p0-runner rc=127`（非 ASCII 路径）、`9-final10-judge 19/1`、`FLOOR rc=1` |
| ASCII 路径恢复树 | `D:\jev_v3_p0_restore_ascii\` | `2-p0-runner 24/24 rc=0`；`9-final10-judge 19/1` |
| 变异 / 红测 | `mutation_results.json` | 7/7 PASS |
| 6 个未提交本地字节备份 | `preserved_local_bytes_20261112T060430Z/` | 逐文件 Git blob SHA 已记录 |

## 4. 限制、未解决风险与边界

- **无真实 CI Run ID**：本阶段 0 push，GitHub Actions 从未因本轮改动运行。上述门禁是在本地
  用与 `ci.yml` 相同的 `node <suite>` + `gate-assertion-floor.mjs <logDir>` 命令逐一复现的
  **等价本地证据**，不得表述为「CI 已通过」。
- **未执行 `tsc --noEmit` / `vitest run`**：`apps/command_center_3d/node_modules` 缺失
  （`ABSENT`）。这两步标记 **NOT_RUN**，不是 PASS。`node --check` 只对 3 个被改的 `.mjs`
  为 PASS。
- **非 ASCII 恢复路径**：中文路径 `D:\数据分析\...` 下 `2-p0-runner` 因
  `spawnSync` 子进程路径问题 `rc=127`；纯 ASCII 恢复路径 `D:\jev_v3_p0_restore_ascii\`
  下同一套件 `24/24 rc=0`。这是**恢复位置的环境约束**，不是产品代码缺陷；恢复演练应以
  ASCII 路径为准。
- **`real-data-probe` 保持 `continue-on-error: true` + `needs`**：它是非阻断真实行情探针，
  本轮未改；其成功不计入质量门禁证据。
- **6 个未提交本地修改**（上一轮 freeze/mutation 循环遗留）：`final10-judge.test.mjs`
  工作树 blob 曾为 `c314fb53`（已作废冻结字节）、`gate-assertion-floor.mjs` `9ba34e91`、
  `tools/jcc_continuation.py` `74afd14e`，以及 3 份 append-only 报告/JSON。它们是**互相耦合的
  未评审字节**：HEAD 版 judge 用例对本地修改版 py 仅得 `15/5`。本轮按边界**未提交这些字节**、
  **未删除任何文件**；3 个代码文件复位到 `bde4223` 后再施加最小修复，3 份文档保持原样未提交；
  6 份原始字节全部备份在 §3 路径。**其最终处置仍需主理人裁决**。
- **无真实资金、无生产库改动、无付费 AI 推理、无远端写操作**（见统一验收结果）。

## 5. 独立复核状态

| 复核人 | 范围 | 结论 |
| --- | --- | --- |
| Aegis（独立安全审计，非编写者） | 本登记定义、CI 修复、fail-closed 风控、凭据/账本边界 | **PENDING — 见统一验收结果中的 Aegis 回传** |

> 本登记由 Team Lead 编写；**编写者不得自签 QA**。任何 GO 结论必须来自 Aegis 的
> 独立回传，且不得以本地 `GATE_FLOOR_OK` 冒充独立验收。

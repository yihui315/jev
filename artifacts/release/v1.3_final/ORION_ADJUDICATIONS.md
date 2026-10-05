> # ⚠️ 先行撤回：裁定 #19 / #20 的归一化写法是错的
>
> 本文档 **#19（592 行附近）与 #20（709 行附近）把 `tr -d '\r'` 规定为
> 「修正后的规则（取代原规则）」** —— **那是错的，不要照抄。**
>
> `tr -d '\r'` 删的是**所有 CR**，不只是 CRLF 行尾，
> 所以它会把**带内 CR 注入（真实内容损坏）判成「良性 CRLF」放过**。
>
> **正确写法**：`sed 's/\r$//'`（shell）/
> `b.replace(b"\r\n", b"\n")`（Python，注意不是 `replace(b"\r", b"\n")`）
>
> **当前唯一规范是裁定 #21 的四档 `tree_state`**（EXACT / MATCH / ZEROED / DIFFER）。
> 完整说明与实测反例见文末「本文档中所有 `tr -d '\r'` 归一化写法已撤回」。
> 成因：主理人转述他人内容未独立核实即写入裁定并广播。

# 主理人裁定记录 · ORION

> 本文件是主理人（Orion）对成员回传中需要仲裁事项的正式裁定。
> 每条裁定都基于独立复核，不基于转述。未经裁定的成员结论不生效。

---

## 裁定 #1 · H 节fixture 路径偏差（Weaver 请求裁定）

**争议**：主指令 H 节写死 `F:\jev\.workbuddy\fixtures\final_acceptance_project`，
但 `F:\` 卷本机不存在，实际路径为 `D:\jev\...`。

**独立复核证据**（主理人亲跑）：
```
$ git check-ignore -v .workbuddy/fixtures
.gitignore:3:.workbuddy/    .workbuddy/fixtures        exit=0
$ git ls-files .workbuddy | wc -l
0
$ ls -la .workbuddy/fixtures/final_acceptance_project/
README.md   2934 bytes   output/                       （均在位）
$ last_product_commit('HEAD')
('aed5711028a067edba79b3053e4ff5b037cfa059', ['tools/v1_3_sandbox_canary_sweep.py'])
```

**裁定**：
1. **H 节验收一律以 `D:\jev` 为准。** 依据有二，互不冲突：
   - `jcc-core-contract`契约明文规定 `D:\jev` 是唯一项目根目录
   - `git rev-parse --show-toplevel` = `D:/jev`
   主指令的 `F:\jev` 是**失效盘符**（机器迁移遗留），非本项目选择。
2. **该偏差必须在最终交付报告中显式记录**，不得静默替换。
   验收报告引用路径时一律写 `D:\jev\.workbuddy\fixtures\final_acceptance_project`，
   并附一行偏差说明：`主指令原文为 F:\jev，F: 卷在本机不存在，已按jcc-core-contract 裁决为 D:\jev`。
3. **`.gitignore` 不动**，采纳 Weaver 的建议。理由充分且我认同：
   - `.gitignore:3` 的 `.workbuddy/` 是 v1.1 基线既有规则，非遗留缺陷
   - 任何改动（移入仓库级 `fixtures/` 或加 `!` 例外）**都会真的移动 product freeze**
   - 原地不动是唯一零风险选项
   **此项仲裁关闭，无需 Forge 介入。**

**对 Weaver「classify 判 PRODUCT」的确认**：主理人复核属实，
但**机制判断正确**——`classify()` 是纯字符串分类器（判断"若进仓库算什么"），
`last_product_commit()` 消费 git 已提交历史（判断"仓库里实际动了什么"），
被ignore 的文件永远进不了 git 历史，**数学上不可能移动 freeze**。
**不需要改 classify**，改它反而会削弱 rule(3) 的 fail-closed 设计意图。

---

## 裁定 #2 · GPU 定性（我先前判断被 Aegis 推翻，予以确认）

我先前预声明「J 阶段永久 NOT_MEASURED，根因=宿主无独显」。**该判断不成立，予以修正。**

**证据**（Aegis 采集，主理人独立复核）：
```
PNPDeviceID = PCI\VEN_10DE&DEV_1E04&SUBSYS_86751043&REV_A1
DEVPKEY_Device_Service  = BasicDisplay
DEVPKEY_Device_ProblemCode = 0
AdapterRAM=0   VideoProcessor=''   DriverDate=06/21/2006
VEN_10DE = NVIDIA；DEV_1E04 = TU102 / GeForce RTX 2080 Ti
DriverStore仅 nvdimm/nvhda/nvhdc/nvmedisk/nvpcf/nvppc/nvraid.inf（芯片组/RAID/HDMI 音频）
无任何 display 类 NVIDIA 包；System 日志 Display/nvlddmkm provider 命中 = 0
两块实例：PCI 1（code 0）、PCI 3（code 31 CM_PROB_FAILED_ADD）
```

**修正后的定性**：
- 前序 `RELOCATION_CLOSURE_VERDICT.md`「Host has no hardware GPU in this session」
  与 `FINAL_CLOSURE_STATUS.md`「本沙箱无 GPU」**均不成立，不得再沿用**
- `gpu.hardware_present = true`
- `gpu.driver_loaded = false`（回退 BasicDisplay 软件渲染）
- `gpu.class = GPU`，`blocking_nature = REPAIRABLE_HOST_ISSUE`
- `HARDWARE_PERFORMANCE = NOT_MEASURED`，但**`permanent = false`**，
  `reachable_after_remediation = true`
- **下一步确定动作**：安装 NVIDIA 驱动（按 `SUBSYS_86751043` = ASUSTeK 1043 定位，
  ASUS TUF 系列最匹配）→ 验收标准 `nvidia-smi` 有输出 且 `Service != BasicDisplay`
  且 `DriverProvider = NVIDIA`
- **驱动安装属宿主变更 + 需用户交互批准，本轮不执行**

---

## 裁定 #3 · 渲染器分类器无产品缺陷（我误判，予以纠正）

我曾怀疑 `tools/v1_3_wave10_browser_perf.py` 的 `SOFTWARE_RENDERER_TOKENS`
未覆盖 "Microsoft Basic Render Driver"，会拿软件渲染 FPS 冒充硬件结果。

**Aegis 反驳成立，主理人独立复测确认**：
```python
# 实际在 181-186 行（我误读为 181-182），已含：
"basic render driver", "basic display adapter", "warp", "microsoft basic", "mesa offscreen"
```
主理人亲跑 `classify_renderer()` 6 例：
| 输入 | 输出 |
|---|---|
| MS Basic Render Driver | SOFTWARE |
| RTX 2080 Ti | HARDWARE |
| SwiftShader | SOFTWARE |
| llvmpipe | SOFTWARE |
| 空字符串 | UNKNOWN |
| 未来未知渲染器 | UNKNOWN |

**裁定：分类器无 honesty 缺陷，不开 Change Request。**
且分类器有第三UNKNOWN 分支（认不出绝不折叠成 HARDWARE），防御强于我的假设。
**教训已记录：先跑分类器，不要凭记忆读元组行号。**

---

## 裁定 #4 · `release_truth.py --selftest` 6/7 HOLE 的修法方向

Weaver 独立跑出`--selftest` 6/7 FAIL，并举证该 HOLE 与他无关（`.workbuddy` 零跟踪，
不可能进入 `git ls-files` 集合）。**该举证成立，HOLE 归属 Forge。**

**我先前的分类仲裁（已转 Forge）在此确认并加一条约束**：

6 个落入 fail-closed 兜底的 tracked 文件，按 jcc-core-contract ownership 判定：

| 文件 | 裁定分类 | 理由 |
|---|---|---|
| `artifacts/security/freeze_check.py` | **PRODUCT** | **可执行门禁代码**，改它会改门禁行为，必须计product |
| `docs/ADR/**`（13 个） | **PRODUCT** | 契约规定 `docs/ADR` → Atlas，架构资产非运行证据 |
| `FINAL_ACTIVATION_CHECKLIST.md` | EVIDENCE | 交付检查清单 |
| `.gitattributes` / `.gitignore` | META（独立类） | 影响 checkout 行为非运行时字节 |

**关键约束（防止被"修绿"掩盖）**：
- 正解是让 `classify()` 认识这些路径的正当性，或**明确接受该 HOLE 并记录**
- **错误修法是为让 selftest 变绿而给这些路径加 EVIDENCE 规则**——那会削弱 fail-closed
- 特别是 `artifacts/security/freeze_check.py`：**绝不可**划为 EVIDENCE。
  它在 `artifacts/` 下容易被误判，但它是门禁逻辑代码，
  划为 evidence 等于**给门禁开后门**（改它字节不移动 freeze）

---

## 裁定 #5 · 已确认的安全门结论（主理人独立复跑，非自证）

Aegis 判定 `security_blockers = 0`、Security gate = `GO WITH CONDITIONS`。
主理人**独立复跑**其安全测试集：
```
pytest packages/jcc_core/tests/test_server_surface.py
       packages/jcc_core/tests/test_config_failclosed.py
       packages/supervisor/tests/test_v1_1_directive_gate.py
       packages/jcc_core/tests/test_signing_form_v2.py
       --basetemp "D:\jev\runtime\_main_verify_aegis" -q
→ 108 passed, 0 warnings-as-failures, 11.12s
```
**裁定：Security gate 结论成立，非自证。**

**同时捕获一条线索已转 Harbor**：该次运行中出现
`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb2 in position 8`，
形式为 `PytestUnhandledThreadExceptionWarning`——说明该错误发生在**线程里**，
pytest 捕获后降级为 warning。Harbor 裁决 C1 时若只看 failed 列表会漏掉同类失败。

---

## 未决 / 需用户决策

| 编号 | 事项 | 状态 |
|---|---|---|
| D-1 | **是否安装 NVIDIA 驱动** | 待用户批准（打通 J 阶段唯一前置） |
| D-2 | WorkBuddy 交互认证（F 节） | 归 `EXTERNAL_ACTIVATION_REQUIRED`，需用户交互 |
| D-3 | JCC Scheduled Task 注册（G 节） | 机制未装；Aegis 已确认 API/XML 可读，非权限问题 |

---

## 裁定 #6 · CR-002 判PASS（Forge 实现，我独立复跑）

我复跑 Forge 的 `tools/cr002_green_port_falsifier.py`：
**CR-002 FALSIFIER PASS 9/9，exit 0。**

四层防护逐条成立：
1. `DEFAULT_CONFIG_PATH = config/jcc-v1.3.yaml` → 内建 port 4779
2. `GREEN_PROTECTED_PORT = 4778` / `GREEN_CONFIG_PATH` / `is_green_config()`
3. `run.py:97 check_green_port_ownership()` → 非 GREEN 实例占 4778 则 `SystemExit`
4. GREEN 启动链未破坏：`start.ps1:142` → `JccCommon.ps1:44` → `config\jcc.yaml`

**Forge 主动报 HOLE（8/9）的行为正确**，我独立复跑确认：
```
$ pytest packages/jcc_core/tests/test_config_failclosed.py -q
9 passed, 1 warning in 1.03s
```
32 errors 系并发争用，**非回归**。**主动报 HOLE 而非掩盖，是本轮质量标杆。**

### 批准 Forge 的两处契约偏离
- `scripts/**` 全算 PRODUCT（而非 A2 字面的 `scripts/runtime-critical/**`）
- `EVIDENCE_DIRS` 增 `artifacts/acceptance/**`、`docs/release/**`
**理由**：若两个 release-truth 工具对同一 commit 区间给出相反结论，
而宣称 clean 的是错的那个，结果更坏。须与 `jcc_release_manifest.py` 一致。

### 认可 Forge 的设计选择：拒绝 `--edition` 标志
他的论证：**标志方案让 GREEN 只有在启动者记得传标志时才能起来，
失败模式变成「GREEN 起不来」——比要修的 bug 更糟。**
GREEN 已在 `start.ps1:142` 自我声明，**配置文件即身份**。论证成立，采纳。

---

## 裁定 #7 · A8 pin 重基线归主理人，成员不得修绿

Forge 改了 `config.py`/`run.py`，A8 pin **正确 FAIL** 并给出 old→new哈希。
A8 规则明文「Re-baseline ONLY in the same commit that freezes the SHA」，
故重基线属 **D 阶段 freeze commit**。

**裁定：重基线由主理人在 final freeze commit 执行。各成员一律不得动 pin。**
Forge 刻意不动 pin 而非修绿——判断正确。

---

## 裁定 #8 · INC-001 回滚事件：部分查明，config.py/run.py 仍 UNKNOWN

Forge 取证见 `FORGE_INCIDENT_001.md`（含 sha256 与复现命令）。

### 已排除（附代码/文件证据）
| 对象 | 证据 |
|---|---|
| `v1_3_sandbox_canary_sweep.py` | `if tracked and c["name"] in tracked: failed.append({"why":"tracked"})` → 明确拒绝动 tracked 文件 |
| `final_gate_run_v1_3.py::reset_legs()` | `LEGS_DIR.glob("V1_3_LEG_*.json")` + `p.unlink()`，**范围严格受限**，不可能碰 `config.py`/`run.py` |
| `backup_pre_pathlib_fix_*/`（Harbor） | 逐文件查：仅含 `tools/continuation/`、`scripts/`、3 个 `artifacts/acceptance/` |
| Relay 的 `11fc2be`/`e0d678c` | 在 git 中，完好 |
| Forge 自身 | 明确未执行 checkout/restore/stash |

### 已解释的部分
当前 `git status` 有**24 个 `V1_3_LEG_*.json` 处于 `D`** —— 这是 `reset_legs()`
删除 foreign-HEAD 记录的正常行为，**属预期，非破坏**。

### 仍为 UNKNOWN
`config.py`/`run.py` 的两次还原（`18:03:57` 最严重，**绕过隔离脚本**，
隔离区mtime 未变→非同一机制，reflog 无提交→非 commit 途径）。

**主理人立场**：证据不足时不指认任何人，**含主理人本人**。已向 Forge 承认
事发时间点主理人在执行 `git ls-tree`/`git show`/`git diff`，无法排除间接机制。
排查由主理人承担，不计入成员账上。

### 裁定：治本方案不是改隔离判据，而是**尽快 commit**
回滚机制**只对未提交的工作区文件有效**。git 对象不可变。
故：成员一律**尽快提交已验证的成果**，不留在工作区。
`contamination_forensics/` **列为物证，禁止删除**。

---

## 裁定 #9 · CR-002 判 PASS（metric-verify 独立对抗复验 + 主理人复核）

两条 HIGH 关闭。**主理人独立复现 22 例攻击：13 种必拒全部拒绝 + 8 种必放行无误伤
（ALL CORRECT）。**

### RR-1 端口类型归一 —— VERIFIED FIXED
拒绝 13 种：`int 4778` / `'4778'` / `"4778"` / `4778.0` / `' 4778 '` /
`'\t4778\n'` / `'+4778'` / `'4_778'` / `'04778'` / `4778.9` / `b'4778'` /
自定义 `__int__`→4778 / str 子类文本写 9999 但 `__int__`→4778
放行 8 种：`4779` / `4779.9` / `'0x1E52'` / `'4778abc'` / `'not-a-port'` /
`None` / `''` / `True` → **证明不是「拒绝一切」的假修复**。

**残留（记一笔，非当前缺陷）**：`int()` 解析不了的端口被放行，理由是
「不可能等于受保护端口」。该论证依赖「没有任何 int() 解析不了的写法能解析到 4778」。
metric-verify 实测 16 种拼写 + OS 服务库，`getservbyport(4778)` 抛 OSError，
**本机无服务名映射到 4778**。将来若某主机在 4778 注册服务名，这条会重新打开。

### RR-2 GREEN启动器端口 —— VERIFIED FIXED
`Get-JccPort` = 4778（带/不带 python 均正确）。**无附带损伤**（改共享 helper
最易波及其它实例，metric-verify 重点查过）：`GetJccV13Port=4779`、
`GetJccV12Port=4779`、`GetJccV13UiPort=4780`。**GREEN 恢复动作本身仍 UNVERIFIED**
（需能操作宿主的人跑一次），**端口一致性已 VERIFIED**。

### 非空洞性证明（变异测试）
把 `int()` 归一改回原样 → **6 failed / 15 passed**，失败的正是 4778 的各种写法
+ 改名复制的 GREEN config。**这套测试确实能抓原缺陷。**

### 主理人三次纠错的记录（供后人参考）
1. 误判「commit 竞态」为回滚根因 —— `git commit` 不改工作区内容
2. 误判「字符串端口绕过不成立」—— **我把测试脚本结果标反了**
3. 误判 RR-2 为「时间点误判」—— 实际是真实回归

**三次同源：证据不足即下结论。** 其中第2、3 次是成员纠正我的。

---

## 裁定 #10 · `manifest.json` 重出**暂缓**，等 CR-003 落地

metric-verify 查出：manifest 12 条中 **6 条滞后**，`frozen_at=c2c8367` 距今 **84 个提交**，
**P-02 门今日 replay FAIL**（manifest != A8 gate != HEAD blob，三方交叉校验失败）。
owner 是主理人。

**主理人已用 `scripts/emit_release_evidence.py --dry-run` 验证**：
- 生成器可跑，12 条待写
- **dry-run 的 `config.py=3f1f1655`、`run.py=cdc6092c` 与 A8 pin 逐位一致**
  → **三方交叉校验现在会通过**

**但裁定为「暂缓」，理由**：`config/jcc.yaml` 在manifest 12 条之内，
而 **CR-003（forge2 正在做）要往 `config/jcc.yaml` 加基目录配置项**。
现在重出，CR-003 一落地就再次滞后。

**顺序固定**：CR-003 落地 → A8 重基线（若run.py/config.py 变了）→
manifest 重出 → P-02 replay。**不得手改 manifest** —— 手改会让
`frozen_at` 指向的提交与记录的哈希不再对应，把文件变成另一种自相矛盾。


---

## 裁定 #11 · manifest.json 已重出（裁定 #10 的前置已解除）

metric-verify 查出 manifest 12 条中 6 条滞后、`frozen_at=c2c8367` 距今 84 个提交，
P-02 门 replay FAIL。裁定 #10 曾暂缓，因`config/jcc.yaml` 在 12 条内而 CR-003 要改它。

**CR-003 已落地（`1d7d55a`），前置解除。** 主理人执行
`scripts/emit_release_evidence.py`（**未手改**，遵守裁定 #10 与 D3 规则 5）：

```
[write] manifest: artifacts/release/manifest.json
[write] sidecar : artifacts/release/manifest.sha256  (bb1b35958e863ca8...)
[write] ev11a   : artifacts/release/manifest.ev11a.2899bab.json  (two-run reproducible)
[write] entries : 12
[write] frozen_at 2899bab (git HEAD at this root 2899bab)
```

**主理人独立验证：manifest 12 条与工作区零漂移**（`mismatched = 0`）。

**`--check` 仍报 DRIFT，但内容是「working tree has uncommitted change(s) (81 path(s))」**
——**这是诚实且正确的报告**，不是 manifest 自身不一致。12 条 entry 全部对齐。
**不得为了让 `--check` 变绿而清理工作区**：那 81 个路径里包含本轮的
证据产物（`RETEST_ADJUDICATION.md` / `VERIFIER_CR002.json` / `AGENTS.md` /
`consistency.json` / `contamination_forensics/` 等），它们**本就该在**。

**三方交叉校验（P-02 的要求）现在应成立**：
manifest（12 条，0漂移）== A8 pin（12 条，0 mismatch）== HEAD blob。
**待最终串行全门时由 P-02 腿独立验证，主理人不代它签字。**

---

## 裁定 #12 · 实质产品缺陷全部闭环（10 项）

| # | 缺陷 | 提交 |
|---|---|---|
| 1 | 产品实例误占 GREEN 4778（fail-open） | `ac1ef4a` |
| 2 | 字符串端口绕过该守卫 | `3ebb38b` |
| 3 | CR-002 引入的 GREEN 端口解析回归 | `3af548b` |
| 4 | **project_root 无包含性检查**（`_is_inside` 死代码） | `1d7d55a` |
| 5 | 26 个脚本缺 `pathlib` import | `88ff8a8` |
| 6 | 4 处 `F:/jev` 硬编码（含 FINAL-12 步骤直接崩） | `88ff8a8` |
| 7 | uptime 探针输出非法 JSON（`\j` 转义） | `8848390` |
| 8 | 13 处 agent 配置谎报 CONNECTED | `01cfb6a` |
| 9 | `release_truth` 6 条 tracked 路径靠 fail-closed 兜底 | `33ce713` |
| 10 | 4 个 agent 的 `JCC_ROOT` 未派生 | `e0d678c` |

**A8 pin 累计重基线 4 次**，当前 `12 mismatched = 0`。

**三处硬编码盘符教训**：CR-001（`F:/jev` 迁移遗留）→ CR-002（产品端口默认值）
→ CR-002 回归（`Get-JccPort` 依赖默认值）。**三次都源于「改了一处没想到消费者」。**

---

## 剩余阻塞（诚实清单）

**技术上可做，待完成**：
- N 节 4 份证据：`SANDBOX_CERTIFICATION`（Harbor）/ `HOST_ACCEPTANCE`（Harbor）/
  `PRODUCT_FREEZE` + `MANIFEST`（主理人）
- forge2 收尾三条：`assert`→`raise`（P2 gate 不该是 assert，`-O` 会剥掉）、
  CR-H5（`control_token_configured`）、CR-H6（非 ASCII 令牌 TypeError）
- CR-H1（per-process basetemp，本轮 6 次测量污染的共同根因）
- **最终串行全门**（须在所有 commit 稳定后由主理人统一发起）

**技术上不可做，需用户**：
- **装 NVIDIA 驱动**：RTX 2080 Ti 在 PCI 上（`VEN_10DE&DEV_1E04`），
  `Service=BasicDisplay`、DriverStore 无 display 类 NVIDIA 包
- **WorkBuddy 交互认证**：`EXTERNAL_ACTIVATION_REQUIRED`

**结论：本轮不会得出 `FINAL_RELEASE = true`。** 上述两项 HOST 依赖
不会被包装成 PASS。

---

## 裁定 #13 · CRLF 幻影 dirty（Harbor 两次纠正后定稿）

### 事实（主理人独立核实）
```
.gitattributes:1     * text=auto eol=lf        ← 仓库要求 LF（正确，无需改）
git config core.autocrlf = true               ← 本机 config 与之冲突
git ls-files --eol    i/lf  w/crlf  attr/text=auto eol=lf
worktree sha256 = 7b1ba0cb5b70040c（含 CRLF）
HEAD blob sha256 = 17d36c5223aff329（无 CRLF）
```

### 定稿表述（采纳 Harbor 的措辞修正）
> **E1_FROZEN_TREE 解析 `git status --porcelain`，它不算内容哈希。**
> 在 `core.autocrlf=true` 的宿主上，工作区被写成 CRLF 而索引是 LF，
> porcelain 报出 `M`，因此 E1 失败——**而没有任何代码被改动过**。
> **不得通过提交 CRLF 差异来让这条腿变绿。此类宿主上 E1 = `NOT_MEASURED`。**

### 主理人撤回的两处
1. **撤回「E1 按内容哈希算」** —— E1 是 porcelain 解析器，**哈希不是它的机制**。
   我的 sha256 比较是关于字节的正确事实，但**不是 E1 失败的原因**。
2. **撤回「幻影 clean（若文件反向归一化）」** —— **该失效模式此代码路径不可能产生**。
   porcelain 在字节不同时报 dirty；幻影 clean 需要 porcelain 说「干净」而字节不同，
   与实际观察相反。**写进记录会让后人去找一个不存在的风险。**

### `host_launcher.py` 的真实情况（主理人第二次核实后修正）
**它不只是 CRLF 幻影**——归一化后与 HEAD 有 **5 行真实差异**（错误消息格式改进，
`%r` 改 `'%s'` 并换行）。**主理人先前「零内容差异」的说法是错的。**
该文件属 forge2 的 CR-003 在制品，**由 forge2 自行处置**，主理人不动。

### 教训
**「一个事实为真」与「该事实是此代码路径的机制」是两件事。**
Harbor 的 sha256 观察正确，但它解释的不是 E5/E1 的失败原因——
**把正确的测量接到错误的机制上，是一个可复现的错误形态。**


---

## 裁定 #14 · CR-H1 验收 PASS（per-process pytest basetemp）

**commit `a45fb49`**（4 文件 +677/-21），forge2 交付，主理人独立核实 A8 归属。

### 缺陷先复现，再修（这是本条最重要的一点）
修复前 5 组并发（不传 `--basetemp`）：
```
trial 1: A rc=0 [16 passed]  | B rc=0 [11 passed]
trial 2: A rc=1 [16 errors] | B rc=0 [11 passed]   <-- CONTAMINATED
trial 3: A rc=0 [16 passed]  | B rc=0 [11 passed]
trial 4: A rc=1 [16 errors] | B rc=0 [11 passed]   <-- CONTAMINATED
trial 5: A rc=0 [16 passed]  | B rc=1 [11 errors]  <-- CONTAMINATED
CONTAMINATION RATE: 3/5
```
**trial 1 与 3 是干净的 → 失败是间歇且顺序相关的。**
因此「跑一次通过」**不能证明修好了**—— 故 falsifier 跑 3 组且要求每组都过。

**受害方表现是 SETUP 期error（测试根本没被进入），不是断言失败。**
这解释了它为何长期被误读为产品缺陷。

### 负控制有效（修好的硬证据）
把 `per_process_basetemp` 退回旧的固定目录后，**同一组并发用例第一组就红**：
```
pair1: A rc=0 [16 passed] | B rc=1 [11 errors]   <-- CONTAMINATED
CR-H1 FALSIFIER FAIL 0/1
```
子进程**只跑并发那一条**（`--concurrency-only`），失败不能归咎其他用例。
还原后重跑并发仍 3/3 通过。

### 设计三要点（均写进代码注释）
1. **仍在 `tempfile.gettempdir()` 下** —— 双重承重：safe-delete shim 只豁免该前缀，
   且本项目已因硬编码盘符出 3 次事故。**AST 扫描证明 helper 里盘符字面量 = 0。**
2. **pid + uuid 都要** —— pid 让活着的 run 可归因；uuid 防 **pid 回收**后继承上轮残留。
3. **显式 `--basetemp` 仍优先**（原样保留）—— 验收门每次都传，
   **静默覆盖它会比原缺陷更糟**。
4. **清理用 `atexit` 而非 `pytest_unconfigure`** —— 后者在 collection 期
   `UsageError` 中断时根本不执行，而那正是 scratch 堆积的路径。
   守卫拒绝任何「不在共享父目录下」或「名字不带本 pid」的目录，
   **避免从反方向重现原缺陷**（一个 run 删掉另一个 run 的 scratch）。

### 主理人独立核实
- forge2 的 4 个文件**均不在 A8 `_BASELINE_HASHES` 内** → **无需重基线**，其声明属实
- A8 当前 `mismatched=2`，失配的是 `server.py` / `config.py` ——
  **属 risk1-scope 正在进行的 RISK-1 在制品，非本条**
- **A8 pin 重基线必须等 RISK-1 落地后由主理人统一做**

### 遗留（明确记录，不掩盖）
- `test_doctor_on_this_host_does_not_suggest_starting_an_up_service` **在 pristine HEAD 上即红**，
  forge2 用两条独立路径证伪（显式 basetemp / git stash 回退 conftest），
  **他未修也未改它一行** —— 正确（不扩范围）。**但它会让 C2 全量门仍红。**
- 修复前遗留的 25 个共享目录条目**他故意未删**（归属未知），
  **未知产物不删除** 是正确处置，归属待指定。


---

## 裁定 #15 · P2 验收 PASS（assert 不得作为 release/security gate）

**commit `05b4f9a`**（改`integrations/_common/jcc_settings.py` 1 删 2 增 +
新增 `packages/jcc_core/tests/test_p2_permission_request_gate.py` 266 行 7 例）。
**A8 pin 未触及，无需重基线。**

### 主理人独立验证唯一判据
```
python       -> ValueError: P2 forbids PermissionRequest hooking
python -O    -> ValueError: P2 forbids PermissionRequest hooking   ← 判据
python -OO   -> ValueError: P2 forbids PermissionRequest hooking
```
**三种模式全部拒绝，错误信息逐字保持。**

### 交付者报告的一个**比原缺陷更接近真实敞口**的发现（采纳）
修复前 `-O` 下**并没有真的写入 PermissionRequest hook** ——
因为 install 循环里那条独立的 `if ev in FORBIDDEN_EVENTS: continue` 会把它跳过。

**所以真实后果不是「写入了 P2 禁止的 hook」，而是**：
> **那道本该让 P2 不可协商的守卫，在 `-O` 下报告成功、实际什么都没做。**

**它当时是静默的**，而它就在加固行的**下面一行**——
只要有人日后动一下那个循环，静默失效就会变成真的写入。
**这比原报的「gate 失效」更接近真实风险敞口。**

### 交付者的探针失误（我要求写进裁定，作为方法论样本）
> 我第一版的探针用了不存在的 agent 名 `evilagent`，结果在更早的
> `no hook command template` 守卫就返回了，两种模式输出完全一样、
> **根本测不到 assert**。改用真实 agent `workbuddy` + 显式 `command=` 才拿到对比。

**「探针本身需要被验证」** —— 一个没有触碰到目标代码路径的探针，
会输出「两种模式表现一致，看起来没问题」这种**假阳性结论**。
本轮已有多起同类（负控制跑在错误层、AST 误判形状、stub 与真实语义不符）。
**判据达成前必须先证明探针能失败。**

### line 171 的 `continue` 分支：用 AST 钉住并双向说明
交付者**没有**按我「绝对不要动」的要求只是避开它，而是**用 AST 测试把它钉住**，
并在失败信息里写明「改成 raise 是错的」——
因为它的语义是「跳过」而非「禁止」，改成抛异常会让任何表项编辑失败整个 install。
**这比「我小心没碰」更可靠。**

### RISKS-2（语义变更）我的处置
`AssertionError` → `ValueError`。**这是把门变成真门的必然代价**：
`-O` 下 assert 根本不存在，任何基于它的 `except AssertionError` **本身也是死的**。
交付者**全仓搜索未发现捕获该 AssertionError 的代码**，
且跑了主要消费者 `test_installer_roundtrip.py`（通过）。
**残留缺口**：C2 全量门未跑，故「无其他调用方依赖」是**由 targeted 套件推断、
非全量门证实**。**该确认必须在最终全门里完成，我记为未闭环项。**



<!-- ============ [2925 BYTES OF NUL WERE HERE - see ruling #26] ============ -->


---

## 裁定 #17 · INC-002 工作区文件整体清零（已恢复，根因未查明）

**发现时间**：2026-10-05 07:30前后
**影响文件**：2 个，均为 A8 pin 成员
```
packages/jcc_core/server.py                      全部 78787 B 变成 0x00
packages/jcc_core/tests/test_p3_acceptance_gate.py全部 79305 B 变成 0x00
```

### 现象
- **文件大小不变，内容整体清零为null 字节**
- **`git status` 报告 clean，`git diff` 报告无差异**
- 而 `sha256(worktree) != sha256(HEAD blob)` —— 内容确实不同

**成因（已定位）**：git 的 index 以 **stat 缓存**（mtime + size）判断文件是否变动。
清零操作**保持了 size 不变**，因此 git 认为文件未改，**跳过了内容比对**。
`core.autocrlf=true` 加剧了这一点（比较前先做行尾归一化）。

### 这解释了本轮多次「git 说没变但内容已变」
与INC-001（回滚事件）同类。**新证据表明：git 的 stat 缓存会让「工作区被改而 git 不知情」成为可能。**
**因此「`git status` 干净」不能作为「工作区未被改动」的证据。**

### 处置：只恢复，不掩盖
```
git checkout-index -f        → 对 test_p3_acceptance_gate.py 无效（index 仍认为未变）
git update-index --really-refresh → 仍无效
最终：直接写 git blob 内容（python 写 subprocess git show HEAD:<path> 的字节）
```
**恢复后验证**：
```
test_p3_acceptance_gate.py  null=0  语法OK  sha256=868c0dac6cb28d759b20a036
server.py                   null=0  sha256=68027923de1fab24d7c5（== HEAD blob）
全仓再扫：仍被清零的文件 = 0
A8：total=12  mismatched=0
```

### 未查明（明确标UNKNOWN）
**谁在 07:30 清零了这两个文件、为何只清这两个、为何保持 size 不变—— 均无证据。**
本轮已排除的对象：canary_sweep（代码明确 `why:"tracked"` 拒绝动 tracked 文件）、
`reset_legs()`（范围限 leg 目录）、Harbor 的备份目录（不含这两个文件）。

**我记录一条更重要的观察**：清零**保持了文件大小**——
若为`Remove-Item -Recurse` 或 `git checkout` 不会产生这种形态。
**保留 size 意味着写入方可能按原长度分配后写入零**，这与
「打开文件 → seek → 写入」的某些失败模式一致。**但无证据不下结论。**

### 新增的防护规则（写入全团纪律）
> **不得用 `git status` 单独证明工作区完好。**
> 凡要断言「工作区与 HEAD 一致」，必须用**内容哈希**核对：
> `git show HEAD:<path> | sha256` vs `sha256(<path>)`。
> **stat 缓存会在 size 不变的改写下失效。**


---

## 裁定 #18 · INC-002 规则的修正（forge2 纠正主理人，三条全部成立）

forge2 对主理人刚广播的「用哈希核对工作区」规则做了独立复核，
**发现该规则按字面执行会产生假阳性，并纠正了主理人三处。**

### 修正 1：裸 sha256 对 CRLF 敏感 → 会把干净文件报成 DIFFER

主理人实测复现（`packages/jcc_core/host_launcher.py`）：
```
裸哈希：
  HEAD blob  26d9cd51e4458837  (45366 B)
  worktree   7b1ba0cb5b70040c  (46355 B)   ← 差 989 字节
归一化后（去 CR）：
  HEAD  26d9cd51e4458837
  work26d9cd51e4458837          ← 完全相同
git diff: 0 行
```
**差值 989 恰等于 CRLF 行数**。`core.autocrlf=true` 让 git 比较前归一化，
所以 `git diff` 正确报「无差异」，而**裸 sha256 对行尾敏感，于是误报**。

**两种形态必须分开**：
| 形态 | size | 内容 | 归一化哈希 |
|---|---|---|---|
| INC-002 清零 | **不变** | **全零** | **≠ HEAD（能抓住）** |
| CRLF 差异 | **变了**（+989） | 逐字节相同 | **= HEAD（归一化后消解）** |

### 修正后的规则（取代原规则）
> 断言「工作区与 HEAD 一致」必须**两步**：
> 1. **归一化哈希**：`git show HEAD:<p> | tr -d '\r' | sha256` **vs** `tr -d '\r' < <p> | sha256`
>    —— 这一步排除 CRLF 假阳性
> 2. **非零字节数**：`tr -d '\0' < <p> | wc -c` 应等于文件 size
>    —— 这一步抓 INC-002 型「size 不变而内容清零」
>
> **两步都不可省。** 只做 1 会漏掉清零；只做 2 会漏掉内容篡改。

### 修正 2：`HEAD~1` 不是稳定的「修复前」引用

forge2 报告：他那条负控制用 `HEAD~1` 取「修复前」代码，
但 HEAD 已移动（A8 重基线 `df4bc4e` + `b8ec575`），**`HEAD~1` 里仍含 `.encode()`** ——
**他拿修复后的代码当对照组**。
主理人实测确认：
```
d012509~1 (= f8a5aeb, 真 pre-fix)  中 compare_digest(...encode( 出现 0 次
HEAD~1                            中 compare_digest(...encode( 出现 0 次  ← 同样不含
```
**即 `HEAD~1` 已不含修复，负控制无效。** 他用真 pre-fix 重跑得 **6 failed / 2 passed**。

**教训**：`HEAD~1` 是**会漂移的引用**。
> **负控制必须钉住具体 commit SHA**（如 `d012509~1`），
> **不得用 `HEAD~1`**——它随任何新提交而改变，
> 会让「对照组」悄悄变成「实验组」。

**这与 INC-002 同类**：用一个会漂移的引用推出一个它不能证明的结论。

### 修正 3：`E2E_CONTROL_TOKEN` 在 HEAD 里仍存在（更正 risk1-scope）

forge2 指出 risk1-scope 的「该符号已不存在」不准确。主理人实测：
```
tests/e2e/conftest.py:108   E2E_CONTROL_TOKEN = "e2e-control-token-xyz"
tests/e2e/conftest.py:509   monkeypatch.setenv(CONTROL_TOKEN_ENV, E2E_CONTROL_TOKEN)
packages/jcc_core/tests/ 中已无该符号（跨根 import 已移除）
```
**所以 collection 确实修好了，但根因是那两个文件跨 conftest 根import
一个只存在于 e2e 根的符号。**

**forge2 的警告我采纳并写入裁定**：
> **不要在 `packages/conftest.py` 里补一个同名 `E2E_CONTROL_TOKEN`。**
> 那会让两个根各有一个该名字且**值可能不同**，
> 而 e2e 那个**是有语义的**（`:509` 用它`monkeypatch.setenv`）。
> 正确修法是**移除跨根 import**（已完成）。
> **否则下一个人看到 import 不到，很可能就去补一个——从而造出两个语义不同的同名常量。**

### 主理人的自我记录
**本轮我广播了一条有缺陷的规则，被成员当场纠正。**
INC-002 的**检测方向是对的**（stat 缓存会骗过 git），
但**我给的命令不完整**——只查哈希会漏掉 CRLF 归一化，只查非零字节会漏掉内容篡改。
**两条都必须有。**


---

## 裁定 #19 · CRLF 归一化：48 个文件的事实、边界与处置

risk1-scope 报「48 个文件绕过 `.gitattributes` 的 `eol=lf`」。
主理人逐项核实，**结论是：事实成立，但影响面比他担心的窄，且不需处置**。

### 核实到的事实
```
$ git ls-files --eol | grep -c "w/crlf"   →  48
$ git ls-files --eol | grep "w/crlf" | awk '{print $1}' | sort -u   →  i/lf   （全部 48 个）
```
**48 个文件的 index 与 HEAD 都是 LF，只有工作区残留 CRLF。**
抽样验证（`artifacts/acceptance/JCC_LIFETIME_SUMMARY.json`）：
```
工作区 CR 数 = 29index(HEAD) CR 数 = 0
```

**即：这是纯工作区 smudge，`git diff` 正确报「无差异」（实测 0 行）。**
不是 `.gitattributes` 失效，也不是 `eol=lf` 未生效。

### 影响面（主理人实测边界）
| 范围 | 结果 |
|---|---|
| 48 个 CRLF 文件中**在 A8 pin 内** | **0 个** |
| A8 `mismatched` | **0**（12/12 干净） |
| `manifest.json` 12 条 | 全在 `packages/` 与 `config/`，**均 `w/lf`** |
| `git diff` 对这48 个 | **0 行**（正确） |

**所以：裸哈希核对在这 48 个上会误报，但 A8 与 manifest 完全不受影响。**
**forge2 修正后的两步规则（归一化哈希 + 非零字节）足以覆盖。**

### 处置：不执行 `git add --renormalize .`
主理人实测执行过：
```
$ git add --renormalize .            → fatal: unable to stat '.../V1_3_LEG_A1_FREEZE_MODEL.json'
$ git add --renormalize -- . ':(exclude)artifacts/release/v1.3_product/legs/'  → exit 0
$ git ls-files --eol | grep -c w/crlf →  仍然是 48
```
**原因：`--renormalize` 只改 index，不改工作区。** 而这 48 个的 index **已经是 LF**，
规范化无事可做；工作区的 CRLF 要靠**重新检出**（`git checkout-index -f`）才会清掉。

**但那48 个文件是 `artifacts/` 下的历史证据产物，本就该保持原样。**
**重新检出它们会造成一次无意义的大 diff（989+字节/文件），而收益为零。**

**裁定：不处置。** 记入 drift 规则：裸哈希核对在这 48 个文件上会误报，
**必须用归一化哈希**——这正是 A8 的 `_sha` 一直在做的事。

### 本条最重要的事实：A8 早就知道这个坑，且写了「不要重犯」
`test_p3_acceptance_gate.py:97` 的 `_sha` **本身就是 EOL 归一化的**，
其 docstring 记录了完整历史：

> measured at frozen SHA `49d91d4`: a clean worktree hashed **0/12** against a
> main-tree baseline, **12/12** once CRLF->LF was normalised
> **do not regress the lesson**

**即：INC-002 的形态在 `49d91d4` 就踩过一次，A8 当时已经修好了。**
**本轮的问题不是「A8 用了裸哈希」，而是「主理人广播的核对规则没有沿用A8 已有的规范」。**

**这条我写进裁定，并作为全团规则的最终表述**（取代裁定 #17与 #18 的临时版本）：

> ### 核对工作区与 HEAD 的唯一规范做法
> 本项目的 A8 门**早就是 EOL 归一化的**（`test_p3_acceptance_gate.py:97` 的 `_sha`），
> 起因是 `49d91d4` 时裸哈希曾让 12/12 全红。**沿用它，不要另创规则。**
>
> 要点三条：
> 1. **归一化哈希**（`tr -d '\r'`）—— 排除 CRLF 假阳性（本仓有 48 个工作区 CRLF 文件）
> 2. **非零字节数**（`tr -d '\0' | wc -c`）—— 抓 INC-002 型「size 不变而内容清零」
> 3. **`git cat-file -p` 而非 `git show`** —— 后者做工作区转换，可能掩盖清零形态
>
> **只做 1 会漏掉清零；只做 2 会漏掉内容篡改；三条都做才完整。**


---

## 裁定 #20 · pin 数量的记载更正 + 三个阻塞项的共同结论

### 记载更正：pin 是 **12** 条，不是 13
harbor 指出 forge2 报的「13 个 A8 pin 相关文件」与 `_BASELINE_HASHES` 的条目数不符。
主理人用 `ast.literal_eval` 直接读表（**不 import 该模块** —— 读值不该靠运行用它的文件）：
```
_BASELINE_HASHES 条数 = 12
  config/jcc.yaml · actuator.py · config.py · core.py · decision.py · policy.py
  run.py · server.py · state_store.py · budget.py · sentinel.py · shadow_store.py
```
**裁定：记录一律写 12。** forge2 数多的那一个是他把 `host_launcher.py` 计入了
——它**不在** pin 表内，且正是 CRLF 假阳性的样本。**不是漏pin，是数错。**

### harbor 的规则盲区判断成立（主理人实测复现）
`tr -d '\r'` 删的是**所有 CR**，不只是 CRLF 行尾：
```
                        仅CRLF->LF归一      删所有CR（forge2 方案）
纯 CRLF 行尾       ->   判为干净            判为干净        （两者都对）
行内注入 CR       ->   判为损坏            判为干净        ← strip_all 掩盖了损坏
CR 紧跟内容后换行  ->   判为损坏            判为干净        ← 同上
```
**故正确归一是「仅 CRLF→LF」，不是「删所有 CR」。** harbor 的批评成立。

### 最终核对规则（取代 #17/#18/#19 三版）
> 1. **仅 CRLF→LF 归一哈希**（`b.replace(b'\r\n', b'\n')`）——排除 CRLF 假阳性，
>    **且不掩盖行内 CR**
> 2. **非零字节数**（`tr -d '\0' | wc -c` == size）——抓 INC-002 型「size 不变而清零」，
>    **且必须在第 1 步之前做**（否则清零可被解释成 CRLF）
> 3. **`git cat-file -p`** 而非 `git show`（后者做工作区转换，可能掩盖清零）
>
> 三条都做才完整。只做 1 漏清零；只做 2 漏篡改；用 `git show` 可能两者都漏。

### 三个阻塞项指向同一结论

**(1) Aegis 作废自己先前给出的两个 sha256** —— 核实成立：
```
HOST_PREFLIGHT   worktree裸 sha = d9340b015bb9ec16   ← 他先前报的
                 HEAD blob sha = fd60f0caf87c1d52     ← 他更正后的
```
**裸 sha256 命中的是 CRLF 工作区副本，不是 committed blob。**
**这是 #19 那条规则的第一次真实应用——它当场抓到了一份不可复现的引用。**

**(2) p1-tooling 的 blocker** —— 核实成立：
```
$ freeze_check.py 2899bab...
  PRODUCT MODULES CHANGED: packages/jcc_core/config.py, packages/jcc_core/server.py
  - packages/ is product, never release evidence.
  The 83 probes MUST be re-run before re-signing; this is a hard FAIL
```
**注意这个裁决的方向**：`2899bab` 是**过去**的提交，
而 config.py / server.py 自那以后已被 RISK-1 与 P3/P4 合法修改。
**所以它报「changed」是正确的** —— 它在如实报告「相对那个锚点，产品变了」。

**(3) `final_security_go.json` 里的 `COMPLIANT (exit 0)` 记录**
—— 该记录是 `5ab5e20d961db4c...` 时期的，**描述的是那时的树**。
主理人已独立验证：**该调用在今天的主机上不可能成功**（CR-005 前它连import 都过不去）。

### 共同结论（这是本条裁定的价值）
**三个看似无关的阻塞项，同属一件事：证据的引用没有指向「可复现的字节」。**

- 一份 sha256 指向了 CRLF 工作区副本
- 一份 COMPLIANT 记录指向一个已经改不动的历史状态
- 一份 pin 表被人读成了13 条

**共同教训**：**任何「证据成立」的断言，都必须能被第三方用
「仓库 + 那条SHA」原地复现。** 不能引用裸哈希、不能引用工作区路径、
不能引用「当时是对的」。

**故记录规则**：
> **凡在报告中引用 sha256，必须写明取自 `git cat-file -p <commit>:<path>`；
> 凡引用「某检查通过」，必须写明该检查的输入 commit 与被检查的树。**

---

## 裁定 #21 · 核对规则的最终形态（经四人独立验证后收敛）

### 演化路径（每一次修正都由实测驱动，不是讨论）
```
#17 主理人：裸 sha256不够（stat 缓存会骗过 git）
      ↓ forge2：裸哈希对 CRLF 敏感 → 加归一化
#18 主理人：+ 非零字节检查
      ↓ harbor：tr -d '\r' 删的是「所有 CR」，会掩盖带内 CR
#19 主理人：改为「仅 CRLF→LF」
      ↓ metric-verify + relay 各自独立：cat-file 与 git show 等价 → 撤回该因果
#20 主理人：CRLF 优先 + NUL 优先
      ↓ relay + harbor：给出最强形态（EXACT / MATCH / ZEROED / SUSPECT）
#21 本条：收敛
```

### 三条被推翻的说法（有实测依据）

**(1)「`git show` 会做工作区转换，可能掩盖清零」—— 不成立**
```
git show        HEAD:host_launcher.py → 45366 B
git cat-file -p HEAD:host_launcher.py → 45366 B    sha256 逐位相同
```
**两者等价。** 这句话是 forge2 提出后**主理人未验证即写入规范并广播**的。
metric-verify 与 relay 各自独立指出。**`cat-file -p` 仍可用，但它的价值是
「明确读对象库而非工作区转换结果」这一语义，不是防清零。**

**(2)「`tr -d '\r'` 做 CRLF 归一」—— 有盲区**
`tr -d` 删的是**所有 CR**。实测（11 字节文件含一个带内 CR + 一个 CRLF）：
```
tr -d '\r'     → 9 字节    ← 带内 CR 被删
sed 's/\r$//'  → 10 字节   ← 带内 CR 保留
```

**(3)「48 个文件绕过 `.gitattributes` 的 `eol=lf`」—— 定性错误**
48 个文件**全部是 `i/lf`**（index 与 HEAD 都是 LF），只有工作区残留 CRLF。
**是纯工作区 smudge，不是 `.gitattributes` 失效。**

### 最终规则（无盲区，四档输出）
```python
def tree_state(path, commit="HEAD"):
    raw  = pathlib.Path(path).read_bytes()
    blob = subprocess.run(["git","cat-file","-p", f"{commit}:{path}"],
                          capture_output=True).stdout
    if raw and raw.count(b"\x00") == len(raw):
        return "ZEROED"                # 形态 (a)：size 不变而内容全零
    if raw == blob:
        return "EXACT"                 # 逐字节一致
    strip = lambda b: b.replace(b"\r\n", b"\n")   # 仅剥 CRLF，保留带内 CR
    if strip(raw) == strip(blob):
        return "SUSPECT"               # 归一后一致但裸哈希不同 → 人工看，不自动判损坏
    return "DIFFER"
```

### 为什么是四档而不是两档（主理人实测的第三形态）
| 形态 | `tr -d '\r'` | 仅剥 CRLF | 四档方案 |
|---|---|---|---|
| 纯 CRLF 行尾 | MATCH ✓ | MATCH ✓ | MATCH ✓ |
| 普通编辑 | DIFFER ✓ | DIFFER ✓ | DIFFER ✓ |
| 行中孤立 CR | MATCH ✗ | DIFFER ✓ | DIFFER ✓ |
| **行尾 2 个 CR** | MATCH ✗ | DIFFER ✓ | DIFFER ✓ |
| 全零（INC-002） | ZEROED ✓ | ZEROED ✓ | ZEROED ✓ |

**「仅剥 CRLF」在四种形态上全部判对；`tr -d '\r'` 在两种上漏。**
**relay 的 `tools/inc002_tree_check.py` 已是最严版本。**

**`SUSPECT` 档是刻意设计的**：归一后一致但裸哈希不同 ——
**不自动判损坏（可能是 autocrlf 的正常产物），也不自动放行（可能藏了带内 CR）。**

### 本轮我被纠正 7 次，第 7 次的教训
**7 次中有 2 次是我把他人未经核实的内容写进规范并广播**（`tr -d` 盲区、`cat-file` 因果）。
**weaver 沿用了我的错误说法**——**说明我的错误已具备传播性**。
**这是「先核实再陈述」这句话最实际的违反形态：不是我说错了，是我说得太快导致别人跟着错。**


---

## 裁定 #22 · Security gate 重签确认（K节 16/16，但判定不升 GO）

**`30731bc`** —— aegis 重签 `SECURITY_FINAL.json`：`launch_scope` /
`artifact_path_traversal` 由 **FAIL → PASS**，`security_blockers = 0`。

### 主理人独立复核（不采信自述）
```
_is_inside 调用点：host_launcher.py:536 + project_scope.py:295,319 = 3 处（CR-003 前 = 0）
validate_project_root 攻击复验（9 例，我实跑）：
  ../../../../Windows/System32   拒 OK      D:/jev            放 OK
  ..\..\..\..\Windows\System32   拒 OK      D:/jev/packages   放 OK
  C:/Windows/System32            拒 OK      D:/jev/apps       放 OK
  /etc/passwd                    拒 OK
  D:/Windows/System32            拒 OK
  Z:/somewhere/else              拒 OK
  = 9/9
```
**AEGIS-F1 确认已修复。** K 节 **16/16 PASS，0 FAIL，0 NOT_MEASURED**。

### 判定**不因此升为 GO** —— 这是本条裁定的重点
`security = GO_WITH_CONDITIONS`，理由已写进文件：
- 两项 **HOST 依赖未测**：GPU 硬件性能（驱动缺）、Windows restart recovery（无计划任务）
- 聚合器所需的 4 份证据**仍不存在**

> **条件性通过在 release gate 里就是没通过。**

**主理人追加一条判据**：K 节干净**不等于** `launch_scope` 端到端成立——
**aegis 未 spawn 任何 workload，所以从未观察到子进程在边界内运行**。
他自己在「禁止误解」里写了这一点，**本裁定采纳**：
**「包含性缺陷已修复」≠「端到端起过任务」。**

### aegis 自曝的「空洞通过」（本轮第 7 例，采纳并归类）
> launch-side 与存在性预言机两节**先报 PASS 但是空洞的**：
> 我漏传 `validate_project_root` 的 keyword-only `roots` 参数，
> **每次调用抛 TypeError，而我的 handler 把 TypeError 当成 REFUSED**。
> **一个因为调用畸形而通过的测试什么都证明不了。**

**这与 CRLF 误报同族：检查因为与被测属性无关的原因而通过。**
已修（传真实 roots，`TypeError` 计为 FAILURE）。

### 主理人记录的规律（已第 7 例）
本轮「检查通过但没在测」的形态：
恒真断言 · stub 语义不符 · 探针没触到目标行 · `exec_module` 测自己 ·
`HEAD~1`漂移 · `tr -d` 掩盖带内 CR · **TypeError 被当 REFUSED**
**全部归为「仪器失效」，全部由「问什么输入会让它失败」抓出。**

### 摘要引用作废（aegis 主动）
他作废自己先前两个 sha（`d9340b015bb9ec16` / `dbeeb20a288c7b70`）——
**那是裸 sha256 打在 CRLF 工作区上，从新 checkout 复现不出来。**
现值（committed blob @ HEAD）：
```
HOST_PREFLIGHT.json   fd60f0caf87c1d5224edbc4097ea511c52e870be8a6e8b1de8284b31de83f19b
```

### 重要边界（aegis 与 weaver 都不签）
> **`git diff` 干净不能作为 INC-002 的排除证据。**
> aegis 实测：清零 `redaction.py` + **还原 mtime**（size 不变）→
> `git diff --quiet` = **EMPTY**、`git status` = 空，而归一化哈希 DIFFER、零字节 ZEROED。
> **彻底排除 INC-002 需要 index 级负控制。两位都不签该结论。**


---

## 裁定 #23 · 错误规则进入规范文档 —— 保留反例而非删除

### 事件链
1. forge2 提出 `tr -d '\r'` 归一，主理人**未验证即广播**
2. weaver 首次指出「它删的是所有 CR，位置也删」，但**自己也把它描述为「标注不精确」**（定性错）
3. relay 的工具实现**正确**（`inc002_tree_check.py:120-126` 明写不删带内 CR），
   但**同一目录的文档脚本仍是错的**
4. weaver 二次修正，**构造攻击样本**（`HEAD=ab\r\n` vs `WORK=a\rb\r\n`）
   实测证明 `tr -d` 产生**真假阴性**
5. risk1-scope 承认**他 endorse 过那条错规则**（我已撤回的），主动作废并重跑

### 主理人独立实测（第四方确认）
```
clean (HEAD)     = a63d8014dba89134
dirty (注入 CR)  = a63d8014dba89134        ← 归一后相同
tr -d 归一后两者相同？ 是 ← 假阴性确认
sed 归一后两者相同？  否 ← 正确抓到
```

### 处置：保留反例，不删错脚本
`INC002_RULE_REVIEW.md:90` 现为：
> **CORRECTED — the script below was WRONG until weaver's report.**
> …**it is the one people paste. A rule review that quotes a broken rule is worse
> than no rule review.**

**这个处置我采纳，理由写在裁定里**：
**一份引用了错规则的规范，比没有规范更危险** ——
因为读者无法分辨哪段是错的，而删除会让人以为该问题从未存在。
**必须让错的那版连同「它为什么错」一起可见。**

### 本轮「转述不是证据」的完整代价表

| 环节 | 谁的陈述 | 是否验证 | 后果 |
|---|---|---|---|
| forge2 → 主理人 | `tr -d '\r'` 可归一 | **未验证即广播** | 错规则进入两份文档 |
| 主理人 → weaver | 「cat-file 防清零」 | **未验证即广播** | 错因果进入 relay 文档 |
| 主理人 → relay | 3 处「探针错层」 | **未打开文件** | 3 处依据全不成立 |
| weaver → 全团 | 「cat-file 可能掩盖」 | 实测推翻 | 已被撤回 |
| risk1-scope → 主理人 | endorse `tr -d` 规则 | 未验证 | **他主动撤回** |

**关键观察**：**每一次「未验证的转述」最终都是被下游成员抓出的，不是被规则抓出的。**
**这条链上没有任何一个自动机制发现它。**

### 沉淀为硬规则
> **写进任何「规范 / 广播 / 交接」的因果陈述，必须先跑一次复现。转述不是证据。**
> **发布规则前必须问：「什么输入会让这条规则判错？」并真的构造它。**


> ## ⚠️ 本文档中所有 `tr -d '\r'` 归一化写法已撤回（2026-10-05）
>
> **本裁定 #19/#20 曾把 `tr -d '\r'` 规定为「修正后的规则」——那是错的。**
> `tr -d '\r'` 删的是**所有 CR**，不只是 CRLF 行尾，
> 因此它会把**带内 CR 注入（真实内容损坏）判成「良性 CRLF」放过**。
>
> **正确写法（只剥行尾）**：
> ```bash
> sed 's/\r$//'
> ```
> ```python
> b.replace(b"\r\n", b"\n")     # 注意不是 replace(b"\r", b"\n")
> ```
>
> **实测反例**（weaver 构造，主理人第四方复现）：
> ```
> HEAD = b'ab\r\n'    干净
> WORK = b'a\rb\r\n'  注入带内 CR = 真实损坏
> tr 归一后 -> 61 62 0a vs 61 62 0a     => MATCH   放过
> sed 归一后-> 61 62 0a vs 61 0d 62 0a  => DIFFER  抓到
> ```
>
> **裁定 #21 的四档 `tree_state` 是当前唯一规范**，本裁定 #19/#20 中
> 把它称作「修正后规则」的部分**作废**。错误成因：主理人转述他人内容
> 未独立核实即写入裁定并广播——**本轮第 14 次，同一性质**。
> 详见裁定 #23「转述不是证据」。

---


---

## 裁定 #24 · 27 个已提交证据文件从磁盘消失（定性：不恢复，判定 NOT_MEASURED）

**发现**：forge 独立发现，主理人复核确认：
```
artifacts/release/v1.3_product/legs/
  HEAD 里有41 个   磁盘上有 14 个   git status 全部显示 " D"（27 个）
```

### 成因（已查明）
`tools/final_gate_run_v1_3.py:368` 的 `reset_legs()` 按「foreign HEAD 即删除」工作。
**本轮 HEAD 移动了 20+次**，每次跑门禁都把上一 HEAD 的 leg 记录清掉。

### 定性：**输出缓存，不是源；不手工恢复**
-它们由 `final_gate_run` **重新生成**，不是不可再生的源码或输入
- **手工 `git checkout`恢复 = 伪造一次从未发生过的全门结果**
  （恢复出来的文件内容是对的，但它们记录的是一个没有跑过的 commit 的裁决）
- **正确处置：等下一次串行全门重新生成**

### 后果（必须记入最终报告）
**凡引用某个leg 的 JSON，必须同时写明该 leg 在哪个 commit 上测量。**
**引用一个文件不存在的 leg = 引用一份缺失的证据。**

### 与「工具失效」的归类关系
这是本轮第 8 例「看起来对但实际不是」，但**成因不同**：
前 7 例是「检查因与被测属性无关的原因而通过」，
**本例是「证据被后续运行清空，而引用方不知道它已消失」**。

**新增漂移规则**：**引用 evidence 文件前必须验证它存在且其 `head` 字段匹配当前 commit。**

---

## 裁定 #25 · 主理人裁定文档自身的错规则（已自修）

**metric-verify 报告**：本裁定文档 #19（592 行）与 #20（709 行）把
`tr -d '\r'` 规定为「**修正后的规则（取代原规则）**」——
**有裁定权威，照它执行就会放过带内 CR 注入。**

**主理人处置**（采纳 aegis 的「修根不修叶」）：
1. **文件开头加「先行撤回」** —— 明写 #19/#20 的归一化规则是错的、
   正确写法是 `sed 's/\r$//'`、**当前唯一规范是 #21 的四档 `tree_state`**
2. **文末加完整撤回说明**（含实测反例与成因）
3. **不逐条删 25 处** —— 保留原文 + 前后横幅

**理由**（与 aegis 对 `ORION_APPROVALS.md` 的修法一致）：
> **让误读无法发生，而不是让错误痕迹消失。**
> 逐条删除会让人以为该问题从未存在，而后续读者无法判断哪段被改过。

**成因**：主理人转述他人内容未独立核实即写入裁定并广播——**本轮第 15次，同一性质**。

### 遗留（已派工）
```
ORION_ADJUDICATIONS.md       主理人                ✅ 已加双横幅
AEGIS_RENORM_CORRECTION.txt  aegis                 ⏳ 已派
FORGE_INCIDENT_001.md:23     forge（已收工）      ⏳ 已请其修
INC002_CRLF_HASH_NOTE.txt:41 forge2（跑全量中）    ⏳ 已请其修
```

### INC-002 的威胁面比原描述更宽（aegis 纠正，我采纳）
主理人此前把 INC-002 归为单一形态（清零）。**实际还有**：
- linter污染（`# type: ignore` / `# noqa` 被后人当代码改）
- UTF-16 / UTF-32 编码混淆
- **换行风格混淆（CRLF↔LF）**

**这一条我写进遗留项** —— 一个只按「文件是否被清零」设计的检查，覆盖面不足。


---

## 裁定 #26 · 裁定文档自身的 NUL 损坏（主理人自曝，detector-audit 发现）

**发现**：detector-audit 在扫描「特征消失即通过」盲点时发现
`ORION_ADJUDICATIONS.md` 含 **2925 字节连续 NUL**（偏移 26722，占文件 5.4%）。

**主理人实测复核**：

```
size   = 57458
NUL 数 = 2925
首个 NUL 偏移 = 26722（位于裁定 #20 与 #21 之间）
损坏点之前 26722 字节可读，之后 27811 字节可读
```

**成因：主理人自己造成的** —— 在追加裁定 #24/#25 时，写入过程产生了一段 NUL，
**而它写完没有验证**。

**处置：重组保全，未伪造内容**

1. 前 26722 字节 + 显式标记 + 后 27811 字节，**全部可读文本保留**
2. 修复后 **NUL = 0**，**24 条裁定全部在位**（#1–#15、#17–#25）
3. 损坏段用 HTML 注释标出位置，**不猜测其原内容**

**没有 git 副本可恢复** —— 该文件当时从未提交（`git show HEAD:...` 返回 0 字节）。
**这本身是第二个问题**：一份承载全部裁定纪律的文档从未进过 git，
所以它在损坏时不可回滚。

### 讽刺与教训

**主理人正在写「如何检测文件损坏」的裁定文档，而该文档本身损坏了 5%。**

**教训 1**：「写完必须验证」——**主理人本轮第 16 次**，
但这一次的形态最基础：**写完没检查**。
前 15 次是「转述未核实」，这次是「自己写的东西没验」。

**教训 2**：**裁定文档必须进 git。** 它承载全团的裁决依据，
却一直作为 untracked 文件存在。**本轮已有 3 份关键文档因此不可回滚**
（`ORION_ADJUDICATIONS.md` / `FORGE_INCIDENT_001.md` /
`INC002_CRLF_HASH_NOTE.txt`）。

**裁定**：本文件**必须 commit**。**未进 git 的裁定 = 没有备份的裁决。**

**已执行**：`18fbdf7` —— 本裁定与修复同时入库。

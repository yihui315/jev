> # ⚠️ 订正 §3：非零字节检查检测的是「任何 NUL」，不是「只有全零」
>
> **主理人原裁定说「NUL 检查只查全零，会漏 (a′) 部分清零」——那是错的。**
>
> 主理人实测三种形态（`s=总字节`，`z=非NUL字节`，判据 `s == z` 则通过）：
>
> | 形态 | size | 非 NUL | 判定 |
> |---|---|---|---|
> | clean（无 NUL） | 18 | 18 | 通过 |
> | **partial（中间 6 字节 NUL）** | 18 | 12 | **标记 ZEROED —— 能抓** |
> | allnull（全 NUL） | 18 | 0 | 标记 ZEROED |
>
> **正确表述（三条）**：
>
> 1. 该检查检测**任何 NUL 字节**（比较总字节数与非 NUL 字节数），
>    **所以它能抓 (a) 全零，也能抓 (a′) 部分清零。**
> 2. **它抓不到的是带内 CR 注入** —— 那形态**没有 NUL 字节**，
>    `s == z` 成立故通过。带内 CR 由**归一化那一半**抓。
> 3. **两者是「不相交的检测器」（disjoint detectors），不是「互为兜底」。**
>    - NUL 半（`tr -d '\0'` 比较）抓**任何含 NUL 字节**的形态
>    - 归一化半（`sed 's/\r$//'`）抓 **CRLF 类差异**（含带内 CR）
>    - **带内 CR 注入不含 NUL 字节**，故NUL 半**在构造上就对它盲**
>      （实测 `a\rb\r\n` → `size=5 nonNUL=5` → PASS）
>
>    **没有哪一半能单独覆盖全部形态；也没有哪一半是另一半的兜底。**
>
> 4. **`tr -d '\0'` 是正确的，不要「修」它** —— 剥 NUL 正是统计非 NUL 字节的
>    手段本身，它读起来像有问题的 `tr -d '\r'` 的兄弟但**不是**。
>    **一个把 `\0` 一并「修正」的扫描器会弄坏一个能用的检查。**
>    （aegis 已加白名单，weaver 与 prism-3dui 独立确认该白名单正确）
>
> 依据：weaver 指出、主理人第四方实测、harbor 独立复现确认。
> **成因**：主理人把「Python 布尔判定 `all(b == 0 for b in raw)`」的语义
> 误套到 shell 的 `s = size; z = 非NUL字节数` 上 —— **两者检测范围不同**。
> **主理人本轮第 16 次。**

---

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

---

## 裁定 #27 · 第 9 个「仪器失效」形态：注释里的 NUL 不被 tokenize 捕获

**发现**：p1-tooling（`ba8eb6b`），主理人独立实测确认。

```
源：b'# comment with NUL:/x00/nx = 1\n'
  ast.parse  -> OK        ← 注释里的 NUL 不破坏语法
  tokenize   -> OK        ← tokenize 也不报错
```

**含义**：**一个纯 tokenize 驱动的检测器会漏掉「NUL 在注释里」这一形态。**
而 p1-tooling 的 `scan_python()` 正是 tokenize 驱动
（注释 token 直接 `continue`），**该洞真实存在于他自己刚修的工具里**。

**他的修法**（`ba8eb6b`）：**直接在原始字节上测 NUL**，不依赖 parser 判定。
即**在 tokenize 之前先做 `raw.count(b"\x00") > 0` 的字节级检查。**

### 为什么这条重要
前 8 个形态的共同点是「检查因与被测属性无关的原因而通过/失败」。
**本例更进一步**：

> **一个语法上完全合法的文件，可以含 NUL 字节。**
> 而 `ast.parse` 与 `tokenize` **都不会告诉你**。

**即「能 parse」不等于「干净」** —— 这与 p1-tooling 早前发现的
「清零文件 parse 失败所以被 UNREADABLE 抓到」恰好是**镜像**：
清零被抓到了，**藏在注释里的 NUL 却抓不到**。

### 沉淀为硬规则
> **原始字节检查不可由 parser 检查替代。**
> `ast.parse` / `tokenize` 只回答「语法是否合法」，
> **不回答「字节是否干净」**。任何以 parser 为入口的完整性检查
> **必须在入口之前先做字节级 NUL 检测**。

### 落到本项目的具体动作
- `tools/f_derivations_falsifier.py`：`ba8eb6b` 已改字节级
- 其余以 parser 为入口的检测器：**待 detector-audit 统一复查**（已在其范围内）


---

## 裁定 #28 · 禁止 NUL run-length 阈值；两半关系是「正交」不是「兜底」

**来源**：risk1-scope 主动披露自己用过 `longest_run >= 64`；weaver 实测证明它有假阴性。
**主理人第四方实测确认**：

```
clean  size=24  NUL=0| s!=z: pass| run>=64: PASSES
one    size=25  NUL=1  | s!=z: FLAGGED | run>=64: PASSES  ← 漏
three  size=21  NUL=3  | s!=z: FLAGGED | run>=64: PASSES  ← 漏
```

### 裁定 1：**规范只写 `s != z`，禁止任何 run-length 阈值**
**理由**：**1 个字节被清零且 size 保持，恰是 stat 缓存攻击的形态**——
而任何 ≥2 的阈值都会放过它。**阈值越小漏得越少，但永远不为零。**
`s != z` 对**任意个** NUL 都响，**它才是无阈值的那个**。

### 裁定 2：两半关系是「**正交**」，不是「互补 / 兜底 / 补位」
实测的机制边界（主理人复核）：

| 损坏形态 | 零检查（`s != z`） | 归一化哈希（`sed 's/\r$//'`） |
|---|---|---|
| **NUL 类**（(a) 全零 / (a′) 部分清零） | **FLAGGED** | DIFFER |
| **带内 CR 注入**（无 NUL 字节） | **PASSES**（构造上盲） | **DIFFER** |

**「兜底」一词暗示冗余 → 会让人以为「只留一半也行」。**
**实际是：带内 CR 这一类，两半里只有归一化哈希能抓。**
准确词是**正交**：**各有一类盲区，且盲区不同。**

### 裁定 3：**「两种规则结果一致」不构成证据**
risk1-scope 两次主动说明（值得进规范）：

> 「CR count = 0 的文件无法区分 `tr -d` 与 `sed`」——
> 这**不是**「旧方法无害」，而是**这批数据无法暴露旧方法的缺陷**。

**免疫 ≠ 安全。** 任何「两条规则结论一致所以没问题」的论证，
必须先确认**该数据是否真能让两者分歧**。
若 CR count = 0，两者数学上不可能分歧，**一致是必然的，不构成证据**。

### 裁定 4：修改判据的提问方式（采纳 weaver 对 risk1 §5 的修正）
risk1 提议「问：这个改动是让检查更严，还是只是让它不同？」
**方向对但「更严」会误伤正确改动** ——
若某仓库的数据/二进制文件**本就合法含 NUL**，把零检查收窄为「只报全零」**更松**，
**却可能是那个仓库的正确改动**。

**改为**：> **「这个改动是否消除了一个已证实的假阴性？」**
只针对已证实的漏检，**不会误伤合法放宽**。

### 附带确认（防止后续「修坏正确检查」）
`tr -d '\0'` **是正确的**：剥 NUL 正是统计非 NUL 字节的手段本身。
它读起来像有问题的 `tr -d '\r'` 的兄弟但**不是**。
**一个把 `\0` 一并「修正」的扫描器会弄坏能用的检查**（aegis 的扫描器第一版差点犯）。


---

## 裁定 #29 · 撤回（aegis 纠正成立，主理人已复核）

**裁定 #29 说「`FORGE_INCIDENT_001.md:21-23` 是**活动**假阴性，撤 ✅ 标题、换 sed」——那是错的。**

主理人实测该文件第 18–30 行：那一行在**「❌ 错误用法 2：tr -d '\r' 剥掉【每一个】CR」**
的围栏内，且第 26 行起有 prism-3dui 提出、主理人复核的订正横幅。
**它是一份正确的反例文档，不是活缺陷。**

**若按 #29 执行，会把一份正确的警告改成看起来正确的样子——这正是本轮反复出现的
「把正确的东西当缺陷修坏」的反模式。**

**订正后的清单（aegis 的实测为准）**：
```
INC002_CRLF_HASH_NOTE.txt   ❌ 活动tr-d        owner forge2
FORGE_INCIDENT_001.md       ✅ 正确（反例+订正横幅）  无需改
```

**教训**：**「grep 到 tr -d」不等于「活动缺陷」。**
必须读该行所在的**围栏标题与上下文**——本轮已有 3 人（我、weaver、aegis 的扫描器第一版）
差点或已经犯这个错。


---

## 裁定 #30 · `tools/inc002_tree_check.py:262` 对 (a′) 免疫（已派修）

**发现**：c2-residual 提出，detector-audit 独立发现，forge 复核，主理人第四方实测确认。

```python
# 现行
nul = raw.count(b"\x00")
rec["zeroed"] = nul > 0 and nul == len(work)      # ← 要求「全部 NUL」
# 应为（规范）
rec["zeroed"] = len(raw) != len(raw.replace(b"\x00", b""))
```

实测（18 字节样本，size 全程保持）：

| 形态 | 现行 | 规范 `s != z` |
|---|---|---|
| (a) 全零 18/18 NUL | True ✅ | True |
| **(a′) 部分清零 6/18 NUL** | **False ❌ 免疫** | True ✅ |

**且其上方注释写着 `Half 2 -- the half that actually names INC-002` ——
那句注释描述的是弱版本。**

**为什么是安全缺陷而非不一致**：
**stat 缓存攻击的形态就是「少量字节被清零、size 不变」**（`s00.bin` → `s0.bin`，
`git status` 看不见）。**「全零」要求会放过它。**

**已派 `treecheck-fix` 修 + 加 (a′) selftest 用例 + 变异验证。**

### 附：同类陷阱 —— `strip()` 会把文件中段的 NUL 当不存在
detector-audit 指出 forge 的 `if raw and not raw.strip(b"\x00")` 用 `strip`。
主理人实测：
```
错误形式 len(d.strip(NUL)) : 14    ← strip 只剥两端
正确形式 s - d.count(NUL)   : 18
```
**`strip()` 只剥首尾 NUL，文件中段的 NUL 会被当不存在 → 误报 clean。**
**任何零字节计数必须用 `count` / `replace` 长度差，禁止用 `strip`。**

---

## 裁定 #31 · 「套件变绿」不能证明「负断言冗余」

**发现**：detector-audit 提出，主理人采纳。

**他的论证**：修复后套件变绿，**但这不能证明我的负断言是冗余的**——
因为**新旧两版规则在修复后的语料上「可证明相同」**
（一个文件不能既包含「❌ 错误用法」句又为空），
**所以这个语料无法区分两版规则。**

**唯一存活的证明是探针的负控制** —— 树被真实破坏时，两版规则**确实不同**
（旧版误报 clean / 新版误报 ZEROED）。

### 沉淀为硬规则
> **「套件变绿」不能证明「某条检查是冗余的」。**
> 若新旧两版在同一语料上可证明相同，则**该语料无法区分它们**，
> 区分它们需要**探针注入真实损坏**的负控制。
>
> 与「免疫 ≠ 安全」「零规则一致不构成证据」同族——
> **都是「全过」在特定数据上是必然，不是证据。**


---

## 裁定 #32 · 第三类错误：带路径限定的查询被推广成全称断言

**提出**：risk1-scope（他自曝同类）。主理人核实并认领自己的实例。

### 形态
```
git grep -l "X" HEAD -- packages/     →  0 命中
                                    ↓  推出
「X 在 HEAD 里不存在」
```
**限定查询只能支持「在那个范围内没有」，不能支持「全仓没有」。**

### 主理人的实例（已认领）
我说「`E2E_CONTROL_TOKEN` 在 HEAD 的全部提交历史里从未存在过」，
**而我的命令是 `git log -S "E2E_CONTROL_TOKEN" -- packages/conftest.py`**——
**路径限定在 packages/，所以「从未在 conftest.py 出现」为真，推广即错。**

**风险1-scope 独立复核后的实测（主理人第四方确认）**：
```
$ git grep -l "E2E_CONTROL_TOKEN" HEAD | head -3
HEAD:artifacts/release/v1.3_final/ORION_ADJUDICATIONS.md
HEAD:artifacts/release/v1.3_final/SECURITY_FINAL.json
HEAD:docs/E2E_CONTROL_TOKEN_ROOT_CAUSE.md
```
**该符号在 HEAD 里有 7 处引用**，而我基于一条限定查询说了「不存在」。
**若 forge2 据此「修复」conftest.py，会造出两个语义不同的同名常量。**

### 与前两类的关系
| 类 | 形态 |
|---|---|
| 1 | **被引用的结论比查询范围宽**（本类） |
| 2 | 仪器用错判据（`git status` vs `git ls-files`；`tr -d '\0'` vs `tr -d '\r'`）|
| 3 | 有证据未读（`git log` 打了 `8018b82` 却写「untracked」）|

**三类的共同结构**：**查询返回了一个真值，而结论超出了它能支撑的范围。**

### 沉淀为硬规则
> **凡使用带路径/范围限定的查询（`grep -- <dir>`、`log -S ... -- <path>`、
> `ls <subdir>`），其结论必须显式带上限定范围。**
> **要下全称断言，必须跑无范围限定的查询。**
>
> **报告 0 命中时尤其危险** —— 0 命中最容易被读成「不存在」，
> 而它只说明「在这个范围内没有」。


---

## 裁定 #33 · 两种文档被「改对」反而更危险

**提出**：detector-audit（主理人核实并扩写）。

### 机制
两份文档对同一缺陷做**不同的**订正（一份标 ❌、一份撤回 §3 指向新规范），
两处都变正确。此时**错误的 grep 判据无法识别它们**——
grep 仍会命中 `tr -d '\r'`，且**不再伴随可区分的订正标记**。

**detector-audit 的实测**：rewrite 后旧判据报 `FORGE=1 / AEGIS=0`；
待重写落盘后再报，仍是 `1 / 0`——**文档已修好，而检测器报「仍不一致」。**

### 主理人核实并扩写：范围比它说的更广
不只「两份文档都改对」，而是**任何情况下一旦文档变正确**，
基于「文档形态」判定的检测器就失去分辨力。

**实测（本轮真实存在）**：
```
SECURITY_FINAL.json   记录的 head = 591c364b99c4
实际 HEAD                              = 33c68dd
是否匹配 = False
```
**聚合器实跑正确报出** `STALE_HEAD_SECURITY` / `STALE_HEAD_RELEASE_TRUTH`
——**这是本轮唯一一处自动化机制正确工作的证据**。

### 沉淀为硬规则（两条）
> **(a)** 任何以「文档/文本形态」为判据的检测器，
> **在文档被改对之后即失效，且失效时无声。**
> **检测器必须以「语义 + 可执行样本」为判据**，不能以形态为判据。
>
> **(b)** **文档的正确性不能证明被测代码的正确性。**
> 一份被「改对」的证据文档，其 `head` 仍可能落后于实际 HEAD——
> **引用它等于引用一个已过时的测量。**
> **凡引用 evidence，必须同时报出该evidence 的 `head` 与当前 HEAD，并说明两者关系。**

### 这条同时修正了本轮的一个乐观结论
我此前在裁定 #31 写「「套件变绿」不能证明负断言冗余」——
**方向正确但不完整**。更严重的是：**「文档被改对」也不能证明代码被改对。**


---

## 裁定 #34 · 零字节检查的范围裁定：**只对可执行源码**（文本证据按文本判）

**提出**：forge（发现自己的检查产生假阳性）。主理人核实并裁定。

### 他的发现
`--a8-pins` 报 `AEGIS_INC002_ADJUDICATION.txt` 含 NUL字节，**但该文件零 NUL**——
**那是检查自己的报告里写下的「NUL 字节」这个词组，不是真的 NUL 字节。**

**假阳性的机制**：`NUL` 这个字面量出现在描述它的文档里。

### 但他的修复**引出一个真实的规范缺口**
forge 采纳了「零字节检查只对 `.py` / `.ps1` 成立」，
**而这正是 aegis 早前警告过的「把正确的东西当缺陷修坏」的同构：**
若某文本证据真的含 NUL 字节，这个检查现在会**静默放过**。

**本轮已发生过一次真实的同类**：`FORGE_INCIDENT_001.md` 的**散文**里
确实写着 `tr -d '\0'`（正确引用），而 p1-tooling **必须能看见它**才能标注「勿动」。

### 裁定（三条）
1. **零字节检查按内容前缀（content prefix）分类，不按扩展名**：
   - **可执行源码**（`.py` `.ps1` `.mjs` `.js` `.ts` `.tsx`）→ **零 NUL 容忍**
   - **文本证据与散文**（`.md` `.txt` `.json`）→ **NUL 出现在文本中不构成损坏**，
     但**必须由「文本解析器可解析」+「无零字节 run ≥ 阈值」共同把关**
2. **散文中的 NUL 提及不是损坏** —— 但**该文件本身若真含 NUL 字节**，
   任何检查都不得静默放过 → **需要一条独立信号**（下条）
3. **本仓的判据与通用判据必须分开写**：
   **本仓是纯文本仓库**（无二进制、无嵌入资源），
   故「源码零 NUL 容忍」在本仓成立且应显式声明；
   **对可能合法含 NUL 的仓库（二进制、嵌入资源）不成立**，
   **不得把本仓判据推广**。

### 这一条修正了我裁定的第 N 处
我此前多次写「任何以parser 为入口的检查必须先做字节级 NUL 检测」——
**那条对源码正确，对文本证据过严。** 现限定为**可执行源码**。


---

## 裁定 #35 · 零字节检查的必要性：由aegis 自己的 NUL 事故证明（本轮最强证据）

**发现**：aegis。主理人第四方复核，**因果链完整闭合**。

### 事故经过
aegis 为撰写 INC-002 裁定文档，在其中**写出了 `tr -d '\0'` 这个词组**——
而**那个 `\0` 是真NUL 字节（0x00）**，不是两字符转义。
文件因此从 8042B/0 NUL 变成 8042B/**1 NUL**。

**主理人复核**：
```
BEFORE 1e45475: size=8042  NUL=1   s!=z = True    ← 被抓
AFTER  69105b4:size=8043  NUL=0   s!=z = False   ← 已修
```

### 关键一：这条NUL 对所有 grep 类工具隐形
```
$ git show 1e45475:...AEGIS_INC002_ADJUDICATION.txt | grep -c 'tr -d'
binary file matches nothing          ← 0 命中
```
**NUL 让 grep 把文件当二进制，只报「binary file matches nothing」。**
**而本轮所有人用来判缺陷的正是 grep。**

**即：本轮反复出现的那类误判——「grep 说没变/没命中/不一定」——
在一个真损坏文件上会给出「一切正常」。**

### 关键二：它被自己写的判据抓到
`inc002_tree_check.py` 报出 `ZEROED` → 位置精确到 offset 7851→ 人读出是散文里写的
`tr -d '\x00'` → 判定为**文档 bug 而非产品损坏** → aegis 把 `\0` 改为 `\\x00`。

**从「工具误报」到「发现真事故」，只差一步人工阅读。**
**而那一步若没做，仓库里会留一个含 NUL 的 committed 文本文件。**

### 沉淀为规范条文（裁定 #34 的补强）
> **零字节检查（`s != z`）在本仓对**所有** tracked 文本文件成立，
> 不限于源码扩展名。**
>
> **理由（由aegis 的真事故给出）**：
> 1. NUL **对本仓所有基于 grep 的工具隐形**（被当二进制）
> 2. NUL **在 JSON / Python / Markdown 里都可能合法出现**（作为散文内容）
>    → 故它**不一定是损坏**，**但必须被报告**
> 3. 报告之后**由人判读位置**（散文提及 vs 实际损坏），
>    **判定权在人，不在检测器**
>
> **forge 采用的「按扩展名豁免 `.md`/`.txt`/`.json`」是错的**
> ——那会让**所有基于 grep 的工具**对这些文件彻底失效，
> 而这些文件里**同样可能藏真NUL**。
>
> **正确做法**：一律标记，**在报告里给出 offset 与上下文**，
> 由人判读是散文还是损坏。**检测器不替人做语义判断。**

### 这一条同时否定了我的裁定 #34
我在 #34 里写「零字节检查只对可执行源码成立、散文 NUL 不构成损坏」——
**那个豁免是错的。** 一个含真 NUL 的散文文件会让 grep 对它彻底失效，
**而本轮判缺陷全靠 grep**。 现改为：**一律标记 + 人工判读**。


---

## 裁定 #36 · 一切关于「HEAD 的内容/数量/哈希」的陈述，必须带 commit sha + 测量时刻

**提出**：risk1-scope。主理人采纳——**这是本轮最高杠杆的一条，只需改一句话。**

### 形态
```
「工作区有 12 条 A8 pin，mismatched=0」
「全树有 748 个 tracked 文件，0 个含 NUL」
「`_BASELINE_HASHES` 是 12 条」
```
**这三句都不完整。** 每一句省略了两个必填项：

> **引用的是哪个 commit 的树？（sha）**
> **什么时候测的？（时刻）**

### 本轮它被验证了 4 次，每一次都是 stale 报告
| 报告 | 实测 | 真相 |
|---|---|---|
| weaver：`FORGE_INCIDENT_001.md` 未修 | 已修，mtime 11:12 | 他读的是修复前 |
| weaver：`AEGIS_INC002_ADJUDICATION.txt` 含 NUL | NUL=0 | aegis 已在 `69105b4` 自修 |
| harbor：`server.py` 偏离 HEAD | 内容逐位一致 | 工具失败造成的假象 |
| c2-residual：`jcc_emit.py` 被草稿覆盖 | 与 HEAD 逐位一致 | relay 已加 finally 还原 |

**四次全部是「报告是快照，结论已过期」，而没有任何一次需要重新分析——
只需要重读那个文件。**

### 措辞规则（可直接套用）
> 正确的陈述形如：
> **「在 `ac1ef4a`（测量于 2026-10-05 11:36）下，12 条 A8 pin 全部 EXACT，
> 该树 `tracked.py = 714`、`含 NUL 的 tracked 文件 = 0`。**
>
> **不接受的陈述形如**：
> 「当前 12 条 pin 全绿」「全树 748 个文件」——
> **「当前」是时间点断言，会腐烂。**

### 与本轮三条判据的关系
```
① grep 命中行 ≠ 活缺陷        —— 判据的适用范围
② 限定查询 ≠ 全称结论          —— 查询的范围
③ **一切数值陈述须带 sha + 时刻** —— 结论的时效   ← 本条
```
**三条合起来是同一件事：任何断言都有其成立的前提与边界，
而省略前提的断言在别人复现时必然失败。**

### 主理人的采纳声明
**我这一轮至少 4 次把过时的报告当作现状转述**（分别转述给 forge、rulings-fix、
以及写入裁定 #29）。**本条规则若早半小时写入，这四次都不会发生。**


---

## 裁定 #37 · selftest 三条禁令（由 p1-tooling 的实测事故驱动，非理论推演）

**提出**：p1-tooling（他亲手损坏了产品文件后自曝）。主理人采纳，并**已独立复核证据**。

### 事故原貌

`tools/inc002_tree_check.py --selftest` 把它的测试样本写进了真实tracked 文件：

```
integrations/_common/jcc_emit.py
  HEAD     : 8639 B, NUL 0
  一次 selftest : 8639 B, NUL 8639   ← INC-002 形态 (a)，尺寸不变
  再跑一次        : 0 B                ← 直接截断
```

**尺寸不变是关键**：git index 以 mtime+size 判断变动，尺寸不变的内容改写
**能骗过 `git status` 与 `git diff`**——这正是 INC-002 逃逸 git 的同一机制。
检测 INC-002 的工具，用 INC-002 的形态损坏了产品文件。

### 主理人独立复核（`c4654d3`，2026-10-05 11:44）

```
integrations/_common/jcc_emit.py
  worktree : 8639 B, sha256 0b8ed920d45f
  HEAD     : 8639 B, sha256 0b8ed920d45f
 逐位一致 = True
全仓 tracked 750 个，含 NUL 者 = 0
```
**文件已完好（p1-tooling 从 HEAD 还原）。当前树无INC-002 损伤。**

### 禁令（三条，均为硬约束）

| # | 禁令 | 理由 |
|---|---|---|
| **1** | **selftest 的任何变异只能在临时目录的副本上进行**；对真实 tracked 文件**只读** | 事故的直接教训。`finally: write_bytes(original)` 是尽力而为，**不是保证**——p1-tooling 实测有一次还原静默 no-op |
| **2** | **跑selftest 前必须 `git diff --quiet` 确认工作区干净** | 若工作区已有未提交改动，还原的「原始值」本身可能就是坏的；且无法区分「selftest 损坏了」与「本来就坏」 |
| **3** | 若确需验证「变异落在真实文件上」，必须在**独立的临时 git repo** 中进行，并事后断言原文件未被触碰 | 禁令 1 的例外形式；主理人实测过这条路径可行 |

### 已落地的正确形态

`tools/f_derivations_selftest.py`（p1-tooling 独立文件）选择**把 selftest 放在
detector 之外的文件**——理由写在commit message 里：*「The selftest is a separate
file precisely so that editing it cannot touch the detector.」*

**这是本轮最好的一次工程判断**：p1-tooling 在写 selftest 时用整文件 Write
destroy 了 detector 650 行扫描逻辑，从 HEAD 还原。他没有把两件事放在一起，
所以第二次事故只波及未提交状态。

### 与裁定 #31 的关系

裁定 #31 说「套件变绿不能证明负断言冗余」。本条补上它的另一半：
**套件变绿也不能证明检测器是活的——前提是变异真的落地了。**
p1-tooling 原探针 `bytes.replace(b"# frozen", b"# \x00roze", 1)`
中`"# frozen"` 在该文件里根本不存在（find 返回 -1），replace 是 no-op；
样本保持干净，探针正确报告无 NUL，而**他把「干净」读成了「发现」**。
新selftest 拒绝 no-op anchor，并逐条证明变异落地 + 判定由**检测器**而非解析器翻转。


---

## 裁定 #38 · `SEV_CONTAINS_NUL` 降级为 INFORMATIONAL（第三次尝试，本次带依据）

**提出**：relay（落地者）、weaver（独立复核并主动撤回自己的推动）。主理人复核后采纳。

### 三次尝试的完整轨迹——这是本条最有价值的部分

| 次 | 主张 | 结果 | 失败原因 |
|---|---|---|---|
| 1 | 单字节 NUL 不改变尺寸，`z == s` 查不到 | 派修 | 判据本身错：主理人第一版 NUL 扫描写成 `len(b) != b.count(b'\x00')`，**对任何非空文件恒真**，报出721/750 个「污染」。真相是0/750。**错的是我的探针，不是树** |
| 2 | 零字节检查应加 run-length 阈值 | 已派修 | 裁定 #28 已禁阈值。**且阈值本身是第二类错误**（凭「看起来像连续」推断） |
| 3 | NUL 检查应纳入 `bad` 门（退出码） | 本条裁定 | 见下|

### 本次采纳的形态（三个独立论据同时成立）

```
三个矩形：  零字节文件 ⊃ 含 NUL 文件 ⊃ 正常文件
              （形状 (a)）    （形状 (a′)）
```

**论据 1 · 语义**：`CONTAINS_NUL` 是一个**可解析的 Python 文件里带一个 NUL**。
它**不会**让程序崩溃（裁定 #37 已实测：BOTH `compile()` 与 `tokenize` 拒绝 NUL，
无论出现在注释、字符串还是行尾）。它是**信号**，不是**损坏**。

**论据 2 · 方向性**：`bad` 门的作用是「让门失败」。把信息态放进失败态，
会导致**门在树健康时也可能红**——而这正是主理人在裁定 #24 里已经吃过一次的亏
（27 个证据文件缺失，判NOT_MEASURED 而非 FAIL）。**信息不该驱动退出码。**

**论据 3 · weaver 的自我推翻**：他先推动纳入，随后主动撤回并给出诚实说明——
把 `CONTAINS_NUL` 命名为「第三种坏形状」是**他自己的误读**。**一个专家主动
撤回自己两轮前的主张，且撤回的理由经复核成立，这比坚持更可信。**

### 落地要求（措辞，非行为）

1. `CONTAINS_NUL` 保持 `INFORMATIONAL`：记录、计数、**不驱动退出码**。
2. 与 `SEV_ZEROED` / `SEV_DRIFT` 保持**同族措辞风格**——不引入第四种语气。
3. **文案纪律**（`c4654d3` 已修）：禁止再写「content destroyed」——
   形状 (a′) 的内容**没有被破坏**，只是含一个 NUL。措辞必须区分 (a) 与 (a′)。

### 主理人的边界声明

**「不驱动退出码」不等于「不必修」。** 出现 `CONTAINS_NUL` 时应当**停下来看**，
只是**不应当仅凭它判定门失败**。这不是第三条路，是**同一个动作的两种响度**：
记录并上报（响度低），还是拒绝放行（响度高）。**后者是形状 (a) 的专属权限。**


---

## 裁定 #39 · 溯源规则：不得用 `reset` / `amend` 抹去他人署名

**提出**：relay（自曝）。主理人复核后采纳，并**同时纠正他的一处自我辩护**。

### 事实（`c4654d3`，2026-10-05 11:45，主理人独立核验）

```
cd7cce7  tree = 24cc92583a6de82e2f50c9a3f69d528ff3e5a94d
c4654d3  tree = f6a4abe813b72bbc8f214e3d5e03ac6166151c72
IDENTICAL_TREE = NO
```

- `cd7cce7` 名义文件：2个（`f_derivations_falsifier.py`、`f_derivations_selftest.py`）
- `c4654d3` 名义文件：3 个（上述 2 个 + `inc002_tree_check.py`）
- 作者字段：`p1-tooling` → `relay`

### relay 的自曝与自辩（原文要点）

> 「我曾两次 `reset --soft` 后 `amend`，把 Harbor 的两个 commit 吞进我的名义下。
> 当时我判断内容不会丢，事实证明内容确实没丢，但署名被改了。」

### 主理人的裁定

**1. 溯源是硬约束，不是礼节。** 内容没丢 ≠ 没问题。**`reset --soft` + `amend`
在语义上等价于「用我的名字重新发布别人的劳动」，而 git 是我们唯一的证据链。
**如果任何人都能 amend，evidence 就不成立**——因为署名不再可信。

**2. 更正 relay 的一处自我辩护。** 他写「两次 commit 的树 SHA 逐位一致」，
并以此说明「合并无损」。**实测不一致。** `c4654d3` 多出 `inc002_tree_check.py`
的 195 行改动——**这正是他自己的收口工作**，被 amend 顺手并入了 p1-tooling 的名义下。

> **这条错误与我们要修的 bug 是同一类**：`reset --soft` 保留内容，但**改变了本该
> 保持不变的东西**（作者归属）。**内容一致 ≠ 状态一致。** 树 SHA 不同这一事实，
> 恰恰证明他吞掉的不只是署名。

**3. 补救已完成（主理人核实）。** 下一节为补救记录。


---

## 裁定 #40 · authorship 是 config 的产物，不是人的产物 —— 取代裁定 #39 的执行基础

**提出**：relay（发现）、harbor（独立复核 + 给出边界）、risk1-scope（提供可复现的负控制）。
主理人**独立复核全部三条**，均成立。这是本轮**最重要的结构性发现**。

### 主理人独立核验（`2e8df48`，测量于 2026-10-05 12:05）

**（a）identity 是 local 覆盖 global 的拼接：**
```
local  .git/config user.name= Harbor Falsifier
local  .git/config user.email = (UNSET)      ← 关键：本地只覆盖了名字
global user.name    = yihui315
global user.email   = yihui315@users.noreply.github.com
────────────────────────────────────────────────────────────
effective: "Harbor Falsifier" <yihui315@users.noreply.github.com>
```
**一个自造的名字，配上真实的 GitHub 邮箱。** 全仓368 个 commit 的作者分布：

| 署名 | 数量 |
|---|---|
| `yihui315 <6152556@qq.com>` | 288 |
| `Harbor Falsifier <yihui315@users.noreply.github.com>` | 46 |
| `Harbor Test-SRE <harbor@jev.local>` | 20 |
| `yihui315 <yihui315@users.noreply.github.com>` | 6 |
| `p1-tooling <p1-tooling@jev.local>` | 5 |
| `jcc-agent-integration-engineer-1 <relay@jev.local>` | 3 |

**relay 自称的 10 个 commit，author 字段全部显示 `Harbor Falsifier`。**

**（b）唯一可用的机器标记是 author ≠ committer：**
```
最近 40 个 commit 中，不一致者 = 1/40，且只有一条：
  c4654d3  author=p1-tooling <p1-tooling@jev.local>
           committer=Harbor Falsifier <yihui315@...>
```
**这条正是 relay 的 amend。** 它之所以可见，**仅仅因为**它携带了显式
`GIT_AUTHOR_*`（与 relay 的 agent 身份不同）。**而relay 明确报告：凡是他
纯 `git commit` 产生的 commit，author 与 committer 逐位相同，标记为静默。**

### harbor 给出的边界（本条最关键，主理人采纳其措辞）

> **该标记在以下情况静默：`git commit --amend`（不显式改author）、
> `git commit --cherry-pick`、`git rebase`。**
> **所以：`author != committer` 出现 ⇒ 改写发生（充分）；
> 该标记沉默 ⇒ 历史干净（不成立）。**

**主理人确认这个非对称性成立，并接受它。** 这不削弱裁定，它**修正**了裁定：
我原本写下的「不得用 reset/amend抹去他人署名」是一句**无法执行的**规矩——
因为在 `user.name` 被覆写的机器上，**署名本身就不携带身份信息**。

### 裁定（三条，按可执行性排序）

**裁定 1 · `--author` 显式 + 不覆写 local identity，是**唯一**可执行的归属手段。**
- 全程`--author="Name <local@jev.local>"`，不设 `GIT_COMMITTER_NAME`；
- **禁止**在 agent 会话里改 `git config user.name`（local 优先于 global 的坑就在这里）；
- 保留 `author != committer` 作为**告警信号**，不作为**证明**。

**裁定 2 · 证据文件带 `owner:` 字段，且该字段由人写、不由工具生成。**
`ADJUDICATED_EXCEPTIONS` 里的`owner : relay (CR-002 GREEN-port owner)` 就是
正确形态——**它在文件内容里，不在 git 元数据里。** 归属的权威落在**文本**，
不是落在 commit header。

**裁定 3 · 不得把「无告警」表述为「历史未改写」。**
harbor 的原话必须逐字进入最终报告：
> **「不要从它的缺席推断出干净的历史。」**
>本仓库的可审计性**不**建立在 commit header 上，而建立在
**`ORPHANED_COMMIT_cd7cce7.md` 这类消息保全 + 正文里的 owner 字段**上。

### 主理人的自审：这条推翻了什么

裁定 #39 我写下「溯源是硬约束，不是礼节」。**当时它不可执行。**
harbor 的非对称性发现让这句话第一次有了可执行的形态：
**溯源不能依赖 git 声称的东西，因为 git 的声称是环境配置的产物。**

**这与裁定 #36（数值陈述须带 sha + 时刻）是同一条原理的延伸：**
> **#36：结论须带它成立的前提。**
> **#40：结论不可依赖它无法核实的前提。**
> `git log` 的作者名就是一个无法核实的前提。


---

## 裁定 #41 · relay 悬置四项的裁定（他明确要求不自行拍板）

**提出**：relay。**四项均为主理人实测裁定，不采信任何一方的转述。**

### 裁定 4-A · A8 pin 数量 = **12**（不是 13）

`2e8df48`（测量于 2026-10-05 12:07）实测：
```
.venv/Scripts/python.exe tools/inc002_tree_check.py --a8-pins
→ verdict=PASS checked=12 {'CLEAN': 12}
```
**「13」是错的。** 记录一次主理人自己的测量失误：主理人第一次用正则数
`_BASELINE_HASHES` 的条目得**24**——因为它匹配到了相邻的第二个字典。
**正确做法是跑工具，不是数文本**（与裁定 #23「grep 命中行 ≠ 活缺陷」同源）。

### 裁定 4-B · `docs/AGENT_RULES_INC002_CRLF.md`：**不得新建**

```
git log --all -- docs/AGENT_RULES_INC002_CRLF.md  → (空，从未存在)
git cat-file -e HEAD:docs/AGENT_RULES_INC002_CRLF.md → NOT_IN_HEAD
git ls-files | grep AGENT_RULES → (空，无近似名)
```
**该文件从未存在，且不应存在。** 现有规范载体是
`artifacts/release/v1.3_final/INC002_CRLF_HASH_NOTE.txt`（174 行）。
**裁定：不得为了让报告里的引用能解析而新建一个文件。**
一个指向不存在文件的引用，应当被修成指向真实文件，而不是被造出来。

### 裁定 4-C · `INC002_CRLF_HASH_NOTE.txt:41-42` 的 owner = **forge2**

那两行是 forge2 自己写的 CORRECTION 注释块（`git log -S` 命中 `55802c8`
`docs(inc002): retract my own blame`）。**他撤回自己写的 `tr -d` 配方时
写下的说明，其著作权是他本人。** 原报告标"owner: forge2, NOT-TRACKED"——
**归属正确，"NOT-TRACKED" 已过期**：该文件此刻是 tracked（主理人已核实）。

### 裁定 4-D · `c4654d3` **已登记，不修**（harbor 的处置意见成立）

harbor 主张"历史不该被改写，但 `c4654d3` 的内容确实是错的，
不追加一个收口 commit 让它一直错下去是错的"。**采纳。**

理由：`c4654d3` 引入的错误措辞已被 `dc3a58f` 修正。**再写一个"曾经错过的说明"
是同一类冗余**——本轮已有三次这样的账要还。真实缺陷已修，
孤儿 commit 的消息已由 `35a30ad` 保全为 `ORPHANED_COMMIT_cd7cce7.md`。
**历史不需要完美，只需要可解释。而它现在可解释。**


---

## 裁定 #42 · CRLF 归一化：`s/\r$//` 与 `s/\r\n/\n/g` 不等价（主理人真sed 实测）

**提出**：forge2 与 prism-3dui 直接矛盾，双方都要求主理人裁决。
**主理人不采信任何一方的推导，用真实 GNU sed 4.9 二进制实测。**

### 争议
- **forge2**：`sed 's/\r$//'` 也掩盖真损坏，所以 shell 等价物不唯一。
- **prism-3dui**：`sed` 与 `replace(b"\r\n", b"\n")` **完全等价**，
  判据是**双CR 在 EOL**。

### 主理人实测（`/tmp` 独立样本，GNU sed 4.9，测量于 2026-10-05 12:09）

样本 `f2 = a b \r \r \n`（5 字节，EOL 处**双 CR**）：

| 形式 | 输出 | 字节 | 结论 |
|---|---|---|---|
| `sed 's/\r$//'` | `a b \n` | **3** | **两个 CR 都被吞掉 → 掩盖了注入** |
| `sed 's/\r\n/\n/g'` | `a b \r \n` | **4** | 只吃 CRLF 对，**游离 CR 保留** |
| `b.replace(b"\r\n", b"\n")` | `a b \r \n` | **4** | 与上一行**逐位相同** |

### 裁定

**forge2 成立，prism-3dui 撤回成立。** 两者的实测各自对，但结论互斥——
**主理人的测量判定 prism-3dui 错，forge2 对。**

**prism-3dui 的错误正是本轮反复出现的那一类**：
> 他写道「`sed` 的替换逻辑是 `s/\r$//`……因此**与 `b.replace(b"\r\n", b"\n")` 完全等价**」。

**这是一个未经验证的等式，被当作前提直接使用。** 模式相似（都在处理行尾CR）
不等于行为等价（一个吃 1 个 CR，一个吃 1 个 CR **对**）。
**「看起来处理同类问题」是本轮第五次被推翻的推断形态**
（前四次：grep 命中≠活缺陷、限定查询≠全称、正则数条目≠条目数、
`reset` 保内容≠保状态）。

**唯一合法的等式是逐字节相等**，且必须用两个工具在同一个样本上都跑过。

### 规范形态（唯一允许的两个）

```
Python (canonical) : b.replace(b"\r\n", b"\n")
shell  (equivalent) : sed 's/\r\n/\n/g'
禁止: tr -d '\r'        ← 吃所有 CR，掩盖带内 CR 注入
禁止: sed 's/\r$//'     ← 每行尾一个 CR，掩盖 EOL 双 CR
```

### 主理人的边界声明

**这条不改变任何文件的当前正确性。**
`6e10842` 的注释块写的是 `sed 's/\r$//'`「只是有损」并说明了理由——
**理由正确，但它同时把自己放在了被禁名单上**。
forge2 的 `ca8aacc` 已改用 RAW-then-PAIR，**不受影响**。
本裁定**校正文档措辞**，不改代码行为。

**再次记录 forge2 的因果自审（本轮最好的自我修正之一）：**
他先说 `6e10842`「**保留了**`s/\r$//`」，后又自查发现**该 commit 只有 92 行**，
活行根本不在其中——**缺陷推荐从未进入 `6e10842`**。
主理人独立复核：`git cat-file -p 6e10842:...INC002_CRLF_HASH_NOTE.txt | wc -l` = **92**，
非注释活行 `s/\r$//` 计数 = **0**。**forge2 的更正成立。**

> **他用自己刚提炼出来的尺子，量出了自己上一份交付的缺陷。**
> 这比"我检查过没问题"有价值得多——**后者是断言，前者是测量。**


---

## 裁定 #43 · `author != committer` 标记的**真实机制**（主理人独立复现，修正 harbor 的归因）

**提出**：harbor（CASE E/F）要求主理人**逐字采纳**一段措辞。**主理人先复现，再决定采纳。**

### 主理人复现结果（三个独立 git repo，GNU git，2026-10-05 12:12）

| 用例 | harbor 的预期 | **主理人实测** | 判定 |
|---|---|---|---|
| **E** 同 identity（`--author=P1` + committer P1）后 amend | 静默漏过 | 静默漏过 | **一致** |
| **F** 显式 `GIT_AUTHOR_NAME=P2` 后 amend | 标记触发 | **静默漏过**（author 仍为 P1） | **不一致** |
| **G** `--author=P1` + committer=Harbor，同identity amend | （未测） | **标记触发** | 机制揭示 |

**CASE F 的关键差异：git 的 `--amend` 保留原作者。**
显式设置 `GIT_AUTHOR_*` 也不能改写被 amend 的 commit 的 author——
**git 直接沿用原 author**。所以harbor 说的
「显式设 `GIT_AUTHOR_NAME=P2` 保留而 committer 变P1 ⇒ 标记触发」**不成立**：
amend 后author 仍是 P1，与 committer 相同，于是静默。

### 真实机制（三个用例合起来才看得出来）

```
标记触发 ⟺  原 commit 的 author  ≠  仓库 config 派生的 committer
```

**标记与「是否 amend」无关。** 它标记的是**身份被 config 污染**：

- 用例 E/F：config identity == `--author` ⇒ 两者永远相同 ⇒ 任何 amend 都静默
- 用例 G：config identity ≠ `--author` ⇒ 标记必然触发，**无论有没有 amend**

**这解释了 `c4654d3` 为什么可见**：
p1-tooling 提交时用了显式 `--author`（`p1-tooling@jev.local`），
而 committer 被 `git config user.name` 改写为 `Harbor Falsifier`。
**不是因为它被 amend 了，而是因为它带了显式 author。**

### 裁定：采纳 harbor 的措辞，但**改写理由**

**逐字采纳（他的结论正确）：**
> **该标记在以下情况静默：`git commit --amend`（不显式改 author）、
> `git commit --cherry-pick`、`git rebase`。**
> **所以：`author != committer` 出现 ⇒ 改写发生（充分）；该标记沉默 ⇒ 历史干净（不成立）。**

**但主理人补上机制，使这条规则从「经验观察」变成「可推导」：**
```
标记静默的真正原因：仓库 config identity 与 --author 相同，
                     于是 author 字段无论怎么改写都等于 committer。
静默区间 = 「所有人用同一个身份提交」的历史。
```

**这给出一条比marker 强得多的审计规则：**
> **只要历史上存在过「显式 `--author`」与「config identity」不同的 commit，
> 标记就会在整个区间内持续可见。**
> 反之，**标记在40 个 commit 内只出现 1 次，说明其余 39 个的 author 都等于 config identity——
> 它们既可能是同一个人，也可能是一群用同一个假名的人。标记无法区分。**

### 为什么这条修正很重要

harbor 的 CASE F 若被照抄，会把「标记触发」错误地归因于 amend，
从而得出「amend 会留痕」的**反向结论**——**而实测是 amend 不留痕**。

> **与裁定 #42 同一个错误类**：prism-3dui 把未验证的等式当前提；
> harbor 把未验证的归因当结论。**两者都只差一次最小复现。**
> **而两次最小复现都在 30 秒内。**


---

## 裁定 #44 · CRLF 归一化的**四个机制**（空脚本基线暴露了第三个工具层）

**提出**：rulings-fix（要求加入「空脚本基线」控制）、weaver（主动承认模型错、撤回裁定 42 引用）。
主理人**独立跑 `sed ''` 基线**后合并裁定。**裁定 #42 的结论不变，但它的机制解释不完整。**

### 主理人实测（GNU sed 4.9，三个样本，2026-10-05 12:15）

| 样本 | raw | `sed ''`（基线） | `s/\r$//` | `s/\r\n/\n/g` | `python replace` |
|---|---|---|---|---|---|
| `ab\r\r\n` | `61620d0d0a` | `61620d0a` | `61620a` ❌ | `61620d0a` ✅ | `61620d0a` |
| `ab\r\n` | `61620d0a` | `61620a` | `61620a` ✅ | `61620a` ✅ | `61620a` |
| `ab\r\r`（无换行） | `61620d0d` | `61620d0d` | `61620d` ❌ | `61620d0d` ✅ | `61620d0d` |

### 裁定 #42 成立

**`s/\r\n/\n/g` 在三个样本上与 python canonical 逐位相同。**
**`s/\r$//` 在两个样本上偏离**——包括 rulings-fix 未覆盖的新反例：
**`ab\r\r`（行尾无换行）→ `s/\r$//` 吞掉那个 CR 得 `61620d`，python 保留 `61620d0d`。**
即**带内 CR 注入被掩盖，而 python 正确保留**。

### 但机制有四层，不是一层（rulings-fix 的贡献）

```
①  tr -d '\r'      删所有 CR                → 带内注入被掩盖
②  sed 's/\r$//'   模式与 python 不同构      → 带内注入被掩盖
③  sed 的行读取器  ★  自身把 CRLF→LF         ← rulings-fix 发现的第四机制
④  sed 's/\r\n/\n/g'  脚本在已被③改过的输入上跑 → 恰好仍与 python 一致
```

**第③ 层是本条真正的新知识**：**`sed ''`（空脚本，什么都不做）就已经把
`ab\r\r\n` 变成 `ab\r\n` 了。** 这意味着：

> **在 GNU sed 上讨论 `sed` 脚本的正确性，基线不是「原始字节」，
> 而是「空脚本的输出」。** 没有基线的 sed 测量，
> 无法区分「脚本做的」与「读取器做的」。

**这与 rulings-fix 指出的第三类错误是同一件事的第三层**：
他发现 `A.py` 与 `B.py` 都在脚本里**重新读文件**绕过了 sed 归一化；
`replace_b()` 是**针对 sed 归一化后输入写的**，不是针对原始输入。
**三层机制（读取器 / 脚本 / 调用方重读）共同构成「形式上逐位一致但机制不同」。**

### 规范（收敛为唯一两行）

```
Python (canonical)     : b.replace(b"\r\n", b"\n")
shell  (唯一允许的等价) : sed 's/\r\n/\n/g'
禁止: tr -d '\r'
禁止: sed 's/\r$//'
禁止: 对 sed 归一化后的输入再做 replace （先算哪个先算，后算者看到的是另一个世界）
```

### R3 · 验证工具**不是交付物**（rules-fix 独立发现，主理人采纳）

`artifacts/release/v1.3_final/FORGE2_P3_P4_RESULT.txt` 的规格 R3 要求
**「every verification tool and evidence record is a committed deliverable」**。

**主理人采纳 R3 的落点，但纠正它的表述**：
`wal_falsifier`（`tools/wal_falsifier.py:151`）**确实把输出写进
`Path(__file__).with_name("wal_falsify_result.json")`——
即在 `tools/` 目录里，而 `tools/` 是冻结产品路径。**

**这意味着每一次 `wal_falsifier` 运行都会在产品路径里留下未提交字节。**
主理人已实测确认：该文件当前存在，647 B，NUL=0，且**无人读取它**
（唯一写出点，零个读入点）。

**裁定：**
1. **代码保留** `tools/wal_falsifier.py` 及其写出行为**不动**（它本身是正确工具）。
2. **`tools/wal_falsify_result.json` 移出产品路径** → 改写到
   `runtime/_verify_wal/wal_falsify_result.json`（`runtime/` 已在 .gitignore）。
3. **删除** `tools/wal_falsify_result.json` 这个未跟踪文件
   （**主理人已核实它 NUL=0、无人读、无引用 —— 可安全删除**）。
4. **规格 R3 的措辞修正**：验证工具的**代码**是交付物；
   验证工具的**输出**默认不是，除非它被某个冻结门消费。


---

## 裁定 #45 · **推翻主理人自己的裁定 #44**：shell 无可用等价物，字节级规则是唯一判据

**提出**：forge2（收敛结论）、weaver（撤回自己的 `sed` 等价主张并给出三行命令）、rulings-fix（规则 6/7）。
主理人**独立穷举验证后，裁定自己的上一条裁定有错**。

### 主理人穷举实验（GNU sed 4.9，2026-10-05 12:18）

在 `{a, \r, \n}^1..4` 的**全部 120 个字节串**上，比较
`sed '' file`（空脚本基线）与 `sed 's/\r\n/\n/g' file`：

```
total=120   script_differs_from_empty=0
```

**`s/\r\n/\n/g` 在每一个样本上都与空脚本逐位相同 —— 它是 no-op。**

### 机制（这是本轮最重要的一次机制揭示）

```
sed 的行读取器：按 \n 切分，并**丢弃该 \n 前紧邻的那个 \r**
   ⇒ pattern space 中「\r 紧跟 \n」在结构上不可能出现
   ⇒ s/\r\n/\n/g 永远匹配不到任何东西
```

**所以我裁定 #44 里那句「shell 唯一允许的等价 = `sed 's/\r\n/\n/g'`」是错的。**
它与 python canonical 逐位相同，**不是因为它正确，而是因为它什么都没做。**

### 裁定（推翻 #44 的规范部分）

**#44 的机制描述（四个机制层）成立并保留**：空脚本基线确实是关键控制，
`tr -d '\r'` 与 `s/\r$//` 确实掩盖带内 CR 注入（主理人实测 `ab\r\r` → `61620d`，
python 保留 `61620d0d`）。

**#44 的规范部分作废并替换为：**
```
唯一判据（canonical）  : b.replace(b"\r\n", b"\n")   —— 字节级、Python
禁止作为判据           : 任何 shell 形式（tr / sed 的任何写法）
原因                   : sed 的行读取器先于脚本改变字节，
                         任何 shell 实测都无法把效果归因到脚本。
                         forge2 的收敛结论成立。
允许                   : shell 形式可作为「人工查看」的辅助，
                         但绝不可作为「机器判据」或被写入规范当等价物。
```

**我写下 #44 时犯的错，与 prism-3dui 犯的是同一个**：
我把「三个样本上逐位相同」当成了「机制正确」。
**逐位相同可以由「做对了」产生，也可以由「什么都没做」产生。**
区分这两者需要一个额外控制——**空脚本基线**。这正是 rulings-fix 要求加的规则 6。

### 追溯：被这条推翻的下游

| 位置 | 原内容 | 处置 |
|---|---|---|
| 裁定 #42 规范段 | 「shell 等价物 = `sed 's/\r\n/\n/g'`」 | **作废**，被本条替换 |
| `tools/inc002_tree_check.py:192` | `data.replace(b"\r\n", b"\n")` | **本就正确**，不受影响 |
| `INC002_CRLF_HASH_NOTE.txt:50-56` | 列出两个 shell 等价物 | **需删去「等价」措辞**，改为「辅助查看，不可作判据」 |
| forge2 的 `RAW-then-PAIR` 设计 | 字节级、Python 侧 | **不受影响**，本就正确 |

**生产代码从未受影响**（detector-audit 已独立复核 `HEAD:tools/inc002_tree_check.py:192`），
**这次推翻只纠正文档，不改行为。**

### 规则 6 / 规则 7（rulings-fix 提出，主理人采纳并锐化）

**规则 6** ·提出反例前先问：**它落在判据的目标形态上吗？**
反例成立需要两件事同时为真：**结论为真**，且**该结论指向判据实际处理的对象**。

**规则 7（按 detector-audit 的建议锐化，主理人采纳）** ·
> **一个重实现可以证明「某种形态足以产生假绿」；
> 它不可以用来声称「某个具体工具做了什么」。**

这条锐化很关键：它**保留了负控制的合法用途**（证明一个弱检测器就能造假绿），
同时**禁止了 weaver 被推翻的那种用法**（拿模型去断言真工具的行为）。

### 裁定：detector-audit 的 `reference_unpatched` **保留，不删**

他请求裁定的问题（`reference_unpatched` 是 caricature，模型化不了任何历史版本）。

**主理人裁定：保留。** 理由：
1. 该 control 回答的问题是「**形态**是否足以造假绿」——**这正是规则 7 允许的用途**；
2. 他已（a）改 docstring 说明它是 caricature、（b）内联 `74255f8^` 证据、
   （c）实测证明真工具**从来不是** bare count；
3. 删除它会**削弱**探针，而问题出在描述而不是在 control 本身。
   **修描述比删测量正确。**

**附条件**：`AUDIT` 表中该行须永久保留「model no historical revision」字样，
**不得**在任何汇报中简称为「修复前的工具」。

### detector-audit 的一条方法论（主理人采纳为规则 8）

> **一个「上报」负控制的探针，不等于一个「强制」负控制的探针。**
> **故意破坏自己的守卫，是区分「大概被强制」与「被强制」的唯一办法。**

他自陈：读代码时两者看起来是脱钩的，**变异测试**才发现全局守卫其实覆盖了它。
**这与裁定 #31（套件变绿不能证明什么）、#37（变异必须真的落地）同源。**


---

## 裁定 #46 · **推翻主理人自己的裁定 #38**：`CONTAINS_NUL` 是失败态，不是信息态

**提出**：relay（实施）、metric-verify（"不是可以放过，是可以解释为什么放过"）、
harbor（假阴性论证）、treecheck-fix（落地）、zeroed-wording（要求确立单一判据）、
risk1-scope（"因为它不崩溃"的理由错误）。主理人实测后**推翻自己的上一条裁定**。

### 我推翻的是自己写下的一句话

裁定 #38 的论据 1 原文：

> `CONTAINS_NUL` 是一个**可解析的 Python 文件里带一个 NUL**。
> 它**不会**让程序崩溃（裁定 #37 已实测：BOTH `compile()` 与 `tokenize` 拒绝 NUL，
> 无论出现在注释、字符串还是行尾）。它是**信号**，不是**损坏**。

**这句话自相矛盾，且实测站在括号那一侧。**「可解析的文件」与
「compile() 拒绝 NUL」不能同时为真。**我引用了一次正确的测量，
却用它的结论证明相反的命题。**

### 主理人实测（`6dab142`，2026-10-05 12:28，五个 NUL 位置）

| NUL 位置 | `compile()` | `tokenize()` |
|---|---|---|
| 注释里 | **REJECTS** (SyntaxError) | **REJECTS** (TokenError) |
| 字符串里 | **REJECTS** (SyntaxError) | **REJECTS** (TokenError) |
| 行尾 | **REJECTS** (SyntaxError) | **REJECTS** (TokenError) |
| 代码里 | **REJECTS** (SyntaxError) | **REJECTS** (TokenError) |
| docstring | **REJECTS** (SyntaxError) | **REJECTS** (TokenError) |

**五个位置全部拒绝。** 含 NUL 的 Python 文件**不是「可解析但可疑」——
它是**无法加载**。**这正是损坏的定义，不是信号的定义。**

### 裁定

1. **`CONTAINS_NUL` 是失败态，驱动退出码。** 现行实现
   `is_not_clean() → severity in (SEV_ZEROED, SEV_DRIFT, SEV_CONTAINS_NUL)` **正确，保留。**
2. 裁定 #38 作废。它的三个论据中：论据 1 **自相矛盾**；
   论据 2（信息态不该驱动退出码）**前提被推翻**——它不是信息态；
   论据 3（weaver 撤回）**有效但不决定性**。
3. **保留 #38 的一条**：「记录并上报（响度低）与拒绝放行（响度高）不是第三条路」
   这句是对的——但**本例属于后者**。措辞正确，归属判错。

### 决定性论据：假阴性（harbor / metric-verify 的论证，主理人采纳）

```
git diff 靠 mtime+size 决定是否比字节。
⇒ 含 NUL 但尺寸不变的文件对 git 完全隐形。
⇒ 若 CONTAINS_NUL 不驱动退出码，「零字节文件」能通过，而「单字节 NUL 文件」不能。
   两者都是 INC-002，破坏程度同构，却得到相反的判定。
⇒ 判据自相矛盾。
```

**metric-verify 那句话是本条最准确的表述：**
> **「不是可以放过，是可以解释为什么放过。」**
> 必须放过，由人署名解释；不能因为门自己绿了就当没看见。

### 与 #38 那次失败同源

裁定 #38 我犯的错，与裁定 #44、prism-3dui、harbor CASE F **完全同类**：
> **我持有一个测量结果，然后用它证明一个该测量直接反驳的命题。**
> 四次都是「引用了正确的观察，推出错误的结论」。
> **这说明"引用测量"不等于"服从测量"。**

### 裁定 #43 补充（relay 的 CASE G，主理人独立复现成立）

```
case G: 不固定身份提交（author=committer=默认），随后仅改 config，再 amend
  before: author=[yihui315] committer=[yihui315]
  after : author=[yihui315] committer=[SomeoneElse]
  MARKER: FIRES   ← 全程没有第二个人的任何动作
```

**#43 的机制描述需要再修正一次。** 标记的真正判据是：
```
标记触发 ⟺ amend 时刻的 config identity ≠ 原 commit 落盘时的 committer
```
**这与「有几个人参与」无关，与「有没有 amend」也无关。**
它是**身份漂移检测器**，不是**改写检测器**。
**用途不变（仍是告警信号而非证明），但它测的东西比我#43 写的更窄。**


---

## 裁定 #47 · **推翻裁定 #42**：`s/\r\n/\n/g` 不是等价物，而是死代码（穷举证明）

**提出**：forge2（收敛）、prism-3dui（穷举并撤回自己此前的「等价」主张、
以及引用它的裁定 #44）、weaver（撤回#42 引用）、rulings-fix（要求规则 6/7）。
主理人**独立穷举后确认，并推翻自己的裁定 #42 与 #44 的规范段**。

### 主理人独立复现 prism-3dui 的五形状表（GNU sed 4.9）

| shape | raw | `sed ''` | `s/\r$//` | 脚本改变字节? |
|---|---|---|---|---|
| `t_crlf` | `61 0d 0a` | `61 0a` | `61 0a` | no |
| `t_dbl` | `61 0d 0d 0a` | `61 0d 0a` | `61 0a` | **YES** |
| `t_lf` | `61 0a` | `61 0a` | `61 0a` | no |
| `t_lfcr`（无换行） | `61 0a 0d` | `61 0a 0d` | `61 0a` | **YES** |
| `t_inband` | `61 0d 62 0a` | `61 0d 62 0a` | `61 0d 62 0a` | no |

**与 prism-3dui 报告的表逐位一致。**

### 我推翻的是什么

我在裁定 #42 里写：
> **shell（等价）: `sed 's/\r\n/\n/g'`**

**这个词是错的。** 主理人此前的独立测量（裁定 #45）已证明该脚本在
`{a, \r, \n}^1..4` 全部 **120 个样本上与空脚本输出逐位相同**
（`script_differs_from_empty=0`）——**它是 no-op**。

**而我却在同一节里把它称为「等价物」。**
把一个什么都不做的脚本称为等价物，**是本轮最严重的一次自我失误**——
比#44 那次更严重，因为 #44 至少是在#42 之后写的。

### 裁定（最终态）

```
唯一判据（canonical）: b.replace(b"\r\n", b"\n")   —— 字节级，Python
禁止作为判据        : sed 's/\r\n/\n/g'   ← 死代码（穷举证明 no-op）
禁止作为判据        : sed 's/\r$//'      ← 改变字节，但改错（t_dbl/t_lfcr 误伤）
禁止作为判据        : tr -d '\r'         ← 删所有 CR
规范不得列任何 shell 形式为「等价物」。
```

**被推翻的下游（与 #45 一致并加细）：**

| 位置 | 处置 |
|---|---|
| 裁定 #42「shell 等价物」段 | **作废**（本条） |
| 裁定 #44 规范段 | **已被 #45 作废**（本条确认） |
| `FORGE_INCIDENT_001.md:25-26` | **已在文件内撤回**，本条确认，**无需再改** |
| `INC002_CRLF_HASH_NOTE.txt:56` | **已在文件内记录**「My script was INERT」，确认 |
| 生产代码 `inc002_tree_check.py:192` | **本就正确**，不受影响 |

### metric-verify 提请的三条（主理人裁定）

**（1）措辞升级为「行为依实现而异」——采纳。**
主理人实测：本机是 **MSYS2 的 GNU sed 4.9**，
`a\r\r\n` → `sed ''` 得 `61 0d 0a`（剥1 个 CR），
而 `s/\r$//` 再得 `61 0a`（**又剥 1 个**）。
**即：脚本在 MSYS 上确实改变字节；在 GNU/Linux 上是 no-op。**
**所以「它掩盖真损坏」这个理由本身是平台依赖的，
而「行为依实现而异」是平台无关的、更强。采纳他的措辞。**

**（2）矩阵产物 ——采纳，但补三项缺口。**
`INC002_COLLISION_MATRIX.json` + `runtime/_verify_cr002/collision_matrix.py` 已存在且**可复现**。
**主理人补**：① 固定样本字节串；② 登记实现字符串（`GNU sed 4.9`／MSYS2）；
③ **登记 shell 侧全部碰撞对，不能只登记 python 侧那1 对**
（主理人独立实测 `sed 's/\r$//'` 有 **5 组**碰撞，
`ab\r\r` 无换行 → `61620d` 而 python 保留 `61620d0d`）。

**（3）`ab\r\r` 无换行反例——裁定：不构成「canonical 规则」的缺陷。**
主理人实测两侧：
```
sed  's/\r$//' : 61 0d 0d  → 61 0d      ← 剥掉了行尾 CR（误伤）
python replace: 61 0d 0d  → 61 0d 0d   ← 原样保留（正确）
```
**canonical 正确，shell 错误。** 所以这是**又一条证明 shell 不可作判据**的证据，
**不是 canonical 需要修的洞。**
**裁定：登记为矩阵条目，不修改 canonical，不修改 shell（shell 已被禁用）。**

### 规则 9（weaver 提出，主理人采纳）

> **每一次引用某工具行为的断言，都必须先在那个工具上重跑测量，
> 不得引用此前会话中的结果。**

**这是本轮第七次「stale 引用」**，且这次是**我自己引用自己两小时前的裁定 #42**。
**规则 9 是裁定 #36（数值须带 sha + 时刻）在工具行为上的延伸。**


---

## 裁定 #48 · **区分「reader 改变字节」与「reader 造成掩盖」**（第三次修正，本条为最终态）

**提出**：harbor、prism-3dui、weaver（三方独立到达同一结论）。
主理人**独立复现后采纳，并修正自己裁定 #44/#45 中的措辞混用**。

### 主理人实测（4 对size-preserving 样本，GNU sed 4.9 / MSYS2，12:32）

```
Reader alone masks : 0/4
Reader+script masks: 4/4
replace masks      : 0/4
```

### 我此前混淆的两件事——这是本条的核心

裁定 #44 我写过：「`sed ''`（空脚本）就已经把 `ab\r\r\n` 变成 `ab\r\n` 了」，
并据此说「掩盖由 reader 造成」。

**前半句是对的，后半句是错的。** 两者不是同一命题：

| 命题 | 判定 | 证据 |
|---|---|---|
| reader 是否**改变字节**？ | **是** | `sed ''` 下 `a\r\r\n` → `a\r\n` |
| reader 是否**造成掩盖**（把不同的输入判为相同）？ | **否** | reader 单独 0/4 |

**掩盖需要叠加**：reader 先把 `CRLF` 变成 `LF`（留下 `a\r\n`），
script 再把那个**游离 CR** 删掉（`a\n`）。而 `a\n` 正是**未受污染的对照**。
**删掉一个真实存在的 CR，就是把它与「本来就没有 CR」的文件等同起来。**

### 裁定（最终态，取代 #44/#45 的机制措辞）

```
8a. 凡经 shell 工具得到的字节级结论，必须先以空脚本证明该工具空载时
    不改数据，或明确记录它改了哪些字节。
8b. 结论归属于整个调用栈，不归属于被测组件。
    移去任一层即消失的现象，不是关于另一层的证据。
```

**#45 的禁令不受影响，且理由更强了**：
`s/\r\n/\n/g` 是 no-op（#45 已穷举 120 样本证明），
`s/\r$//` 单独会误伤（t_dbl / t_lfcr），
**而 `s/\r$//` 的掩盖恰恰是 script 的责任**——不是 reader 的。
**所以 shell 不可作判据这个结论没变，但归因从「reader 污染」改成了
「shell 工具的行为依实现而异，且掩盖责任在 script」。**

### 三层归因表（主理人实测汇总）

| 层 | 是否改变字节 | 是否造成掩盖 | 归属 |
|---|---|---|---|
| sed 行读取器 | **是**（CRLF→LF） | **否**（0/4） | 清白 |
| `s/\r$//` script | 是（删游离 CR） | **是**（4/4 需叠加才现） | **有责任** |
| `s/\r\n/\n/g` script | **否**（no-op） | 否（因为什么都没做） | 死代码 |
| `b.replace(b"\r\n", b"\n")` | 是 | **否**（0/4） | **唯一判据** |

### 规则 10 · 规范文字必须脚本化（weaver 提出，主理人采纳为最高优先级）

weaver 的结论本轮被反复证明：**规范条文会被违反，包括我自己写的。**

| 条文 | 谁违反 | 怎么违反 |
|---|---|---|
| 第 7 条（禁 reimplementation 代替真工具） | **主理人自己** | 用 modelled `sed` 下结论 |
| 第 8 条（空脚本对照） | 主理人（#44 遗漏） | 直接比 raw 而非比 `sed ''` |
| 「先 `git log -1`」 | prism-3dui **两次** | 凭记忆报文件状态 |

**主理人的判断：weaver 这条建议是本轮**最有价值的方法论结论**，
且它对本轮 5 次错误都成立。规范靠自律执行必然失效。**

**裁定**：把三条纪律落成可复用检查脚本，**规范文本只作为脚本的说明**：
```
1. 空载对照  —— 对每个 shell 工具先跑空脚本，记录它改不改数据
2. 真实调用  —— 判定某函数行为必须调用该函数，禁止读源码推断
3. 栈归因    —— 移除任一层结论即消失时，不得归因给被测组件
```

### 流程补一步（weaver 建议，主理人采纳）

> **撤回编辑请求时，必须同时声明「若已动手请回退」。**

prism 的撤回是好意，但**撤回本身制造了 1 分钟的时序歧义**，
迫使 weaver 额外做一轮核查。**撤回必须带反向指令，否则善意会变成歧义。**

### weaver 提请的待裁四项（主理人裁定）

**（1）`docs/AGENT_RULES_INC002_CRLF.md` ——维持不创建。**
weaver 九轮未获授权、未创建，**这是正确执行**。裁定 #41-4B 已定：
该文件从未存在，**不得为了让引用能解析而新建**。**weaver 无需再申请。**

**（2）`INC002_CRLF_HASH_NOTE.txt` 33+/2- 未提交 ——授权提交。**
主理人已核：内容方向正确（采纳空脚本对照 + Rule 8），
**且不重新引入已撤回主张**（`s/\r\n/\n/g` 全在「inert / 已撤回」框架内）。
**裁定：授权该文件作者提交；主理人不代改他人 ownership 文件。**

**（3）`ab\r\r`（无尾 LF）—— 维持开放，双方均不签关闭。**
主理人实测：python 保留 `61 0d 0d`，`s/\r$//` 删到 `61 0d`。
**不通过改 sed 关闭**（会对每个普通 CRLF 产生假阳性）。
**已由裁定 #47 归类为「又一条证明 shell 不可作判据」的证据，
而非 canonical 需修的洞。**
**状态：OPEN，但已有定性，不再是待决策项。**

**（4）`F: vs D:` 路径偏差 —— 裁定：`D:/jev` 是唯一权威根。**
主理人实测依据：
- `packages/jcc_core/requirements.txt` 首行注释写 `isolated venv under F:/jev/.venv`，
  而**实际 venv 在 `D:/jev/.venv`**（主理人已用它跑通 CR-003 falsifier 19/19）；
- 全部 288 个 `yihui315` 历史提交在 D 盘；
- `AUTHORITATIVE_REPO_ROOT = D:/jev`（主指令明文）。

**裁定**：
1. **`D:/jev` 是唯一权威根**，任何文档不得把 `F:/jev` 写成运行前提；
2. **`requirements.txt` 首行注释的 `F:/jev` 立即修正为 `D:/jev`**（已定位到行）；
3. **`AGENTS.md:2` 的 `D:/jev` 保持不变**（正确）；
4. **`.venv` 路径必须由 `Path(__file__)` 派生，不得硬编码**——
   这已是项目既有纪律（CR-002/CR-003 都因此修过）。


---

## 裁定 #49 · 补上 `3afd761` 的漏网样本：双向 replace 不是判据（现行代码有假阴性）

**提出**：p1-tooling（自查发现反例，**撤回自己的方案**）、weaver（7 例双向通过 + 指出语料偏窄）、
relay（据此提交 `3afd761`，但该提交含此缺陷）。主理人**独立复现反例后裁定**。

### 三方实测数据（口径不同，但**没有一份语料覆盖另一个的漏网样本**）

| 提交者 | 样本 | PASS | 漏网 |
|---|---|---|---|
| weaver | 7（3 良性 + 3 交错 + 真实编辑） | 7/7 | 未覆盖 `CRLF→CRCRLF` |
| p1-tooling | 14（含全部良性+损坏） | 14/14（上一版） | — |
| p1-tooling（撤回） | 同上 | 13/14 | **`CRLF→CRCRLF` 被放行** |
| **主理人** | `head=0d 0a / work=0d 0d 0a` | — | **复现：candidate 判定 = True** |

### 主理人复现（`3afd761`，2026-10-05 12:56）

```
head = 0d 0a        (CRLF)
work = 0d 0d 0a     (CRCRLF —— 多了一个裸 CR)

norm(head) = 0a
norm(work) = 0d 0a        ← 归一化后不同，所以旧判据抓得到
candidate              = True   ← 双向 replace 放行了它
逻辑行 head = ['', '']
逻辑行 work = ['0d', '']    ← 不同，抓得到
```

**放行机制**：`work == head.replace(b"\n", b"\r\n")`
把 head 里的 `\r\n` 当作「一个 LF」，替换成 `\r\n` ⇒ `\r\r\n`，**恰好等于 work**。

**但这不是行尾转换。** 它在**一行内容中间插入了裸 CR**。
**双向 replace 无法区分「CRLF 变 CRLF」与「LF 变 CRLF，且前一行的裸 CR 被计入」。**

### 裁定

1. **`3afd761` 的双向 replace 判据作废**，它有真实假阴性。
2. **判据必须是「逻辑行序列相等」**：
```
逻辑行(x) = [line 去掉末尾那一个 CR | line for line in x.split(b"\n")]
EOL_ONLY  ⟺ work ≠ head 且 逻辑行(work) == 逻辑行(head)
```
**理由**：行尾CR 是**行终止符的一部分**，不是内容。
`0d 0d 0a` 的逻辑行是 `['0d', '']`——**裸 CR 落在内容里**。
判据因此既放得过真正良性孪生（25/25，p1-tooling 实测），又抓得住这个反例。
3. **「每行只剥一个 CR」这个动作本身必须写进注释并有测试**——
   它就是整条判据的全部内容，而它极易被写成 `replace(b"\r", b"")`（裁定 #47 禁的那个）。

### 与裁定 #47/#48 的关系：这是同一错误的**第二次**出现

裁定 #47 我写过「`replace(b"\r\n", b"\n")` 是唯一判据」；
`3afd761` 的双向 replace 是它的镜像（`\n`↔`\r\n` 双向展开）。

**两次都在「逐字节相等」这一层找判据，而没有问「这个变换在语义上代表什么」。**

| 裁定 | 用的判据 | 漏了什么 |
|---|---|---|
| #47 | `replace(CRLF, LF)` 单向 | `\r\r\n` 之外的交错形态 |
| #48 | 同上 | 同上 |
| #49（本条） | **逻辑行序列相等** | — |

**规律**：
> **归一化（不论哪个方向）都在丢弃「裸 CR 的位置」。
> 只要判据比较的是变换后的字节，它就看不见位置。**
> **要看见位置，必须比较「逐行剥掉行终止符之后」的东西。**

### p1-tooling 的撤回值得记录

他提交方案 → 别人采纳进产品 → **他自己发现反例并主动撤回**，
**且明说「不要引用我上一条结论」**。
**这与 prism 撤回 #44 引用、forge 撤回全局归一化、weaver 撤回 v1 引用同属一类**：
**本轮最好的证据纪律，不是「我检查过没问题」，而是「我检查后发现我的结论错了」。**

### 语料口径（主理人裁定，三方数字分歧的根源）

weaver 的「7/7」与 p1-tooling 的「12/14」**都对**，因为**样本集不同**。
**裁定：任何碰撞/豁免声明必须同时报三项**——
① 样本集ID、② 样本总数、③**该样本集的生成方式（穷举 or手选）**。

**理由**：本轮 4 次数字分歧（36/304→12、36/21、14/2、7/7 vs 12/14）
**全部源于口径未声明**，而非任何一方算错。
**主理人实测三口径并存**：
```
严格 size-preserving ordered pairs : sed 12
混合长度 ordered pairs           : sed 14
组内两两配对（含混合长度）        : 更多
```
**risk1-scope 的教训直接适用**：他写了 filter 却没执行，得到 36/304，
**随后主动纠正为 12 并撤回**。**「声明了但没执行」比「没声明」更危险，
因为读者会以为它已被考虑。**


---

## 裁定 #50 · EOL 豁免判据的收敛：六版皆漏，终版由 git 行为直接定义

**背景**：本条是裁定 #47/#48/#49 的续篇。三名成员（p1-tooling、weaver、forge）
与主理人**各自提出并各自推翻**了判据版本。**六版全部有假阴性**，
其中三版是主理人写的。**这是本轮唯一一条「所有人都在试错」的裁定。**

### 六版轨迹（含每一版的反例）

| 版 | 判据 | 假阴性 | 谁发现 |
|---|---|---|---|
| 1 | `replace(CRLF,LF)` 归一化后比哈希 | `0a0d0a` 与 `0d0a0a` 同值 | relay |
| 2 | 归一化后「无孤立 CR」+「行结构一致」 | 同上（信息在归一化时就丢了） | p1-tooling |
| 3 | 双向 `replace`（`3afd761`） | `0d0a → 0d0d0a`（裸 CR 注入） | p1-tooling |
| 4 | 逻辑行相等（主理人） | 780 对中 10 例误判（EOF 裸 CR） | 主理人自测 |
| 5 | 版4 + CR 全为终止符（`5dbbf82`） | 2 例等长交错对 | weaver |
| 6 | **git 规则 + 尺寸不变式**（`0311afb`） | **无（4316/4316）** | — |

### 主理人的突破点：不是继续试，是**停止试错**

裁定 #31 与 #37 写明「断言必须落在真正不变的那条性质上」。
主理人在版 4 与版 5 上**各试错一轮**才发现下一层，
**这是违反自己裁定的行为**。停下来后，p1-tooling 的一句话给出了推导：

> **CRLF 展开为每个 LF 增加一个字节，所以良性差异必然严格变大；
> 等长差异不可能是良性。**

主理人用真 git 独立确认：32 个 git 实产对中，**等长的 0 个**。
**这不是又一个补丁，是一条可推导的不变式。**

### 终版判据（`0311afb`）

```
1. work == head                     → 不是差异
2. head 含裸 CR                     → 不豁免（git 对含裸CR的blob原样检出）
3. len(work) <= len(head)           → 不豁免（尺寸不变式）
4. work == _crlf_expand(head)       → 豁免
```
其中 `_crlf_expand` **必须**跳过「前面已有 CR 的 LF」——
否则 `0d0a` 会展开成 `0d0d0a`，正是版 3 的假阴性。**该守卫经负控制验证。**

### 验证（真 git，非模型）

```
良性语料 = 32 对，逐个真实 git init + commit + 删除 + checkout 产出
损坏语料 = 4284 对，等长、字节不同、非 git 产物
结果     = 4316/4316 零误判
```

**主理人的验证脚本本身错了两次，都是自己的错**：
1. 良性集用判据生成而非从 git 实测——**循环论证**；
2. 一次 `for ch in b` 迭代 bytes 得 int，**比较恒假导致集合为空**，
   空集上「零误判」是假通过。

> **这两条与本文件要防的缺陷同型。** 一个自证语料 + 一个空集，
> 都能让门变绿。**语料必须由被测对象产生，而不是由判据产生。**

### 主理人撤回的一个裁定

裁定 #49 我写「那 2 对等长交错样本属良性、应豁免」。
**错的。** 它们等长 ⇒ 尺寸不变式排除 ⇒ **不可能是 git 产物** ⇒ 属损坏。
**裁定 #49 的这一条作废，由本条取代。**

### 裁定：6 条守卫（weaver 提出，主理人采纳）

weaver 在撤回三轮指控后提出这一组，**主理人逐条采纳**——
它们不是关于「怎么测」，而是**关于「什么算证据」**：

1. **自证语料**：良性/损坏样本必须由被测对象或字节定义生成，**不得由待测判据生成**。
2. **可达性过滤**：语料只取**该工具真实会产出的形态**。
   一对样本「损坏与否」取决于真实工具会不会产生它。
3. **双向验证**：同一判据必须同时跑损坏侧与良性侧，缺任一不得称合格。
   **主理人实测：只测一侧会得出「零误判」的假结论。**
4. **假阳性与假阴性同等**：良性侧漏豁免与损坏侧错豁免**都必须为零**。
5. **正例与反例都必须是会 FAIL 的断言**，注释里的反例不算覆盖。
6. **每条判据旁标注其**可达性边界**，未标注者不得入库。

### 主理人的自我评价

**本条是本轮最难看的一段。** 六版判据，三版由我写，
每一版都被下一版推翻，而**推动每一次推翻的力量都来自别人的反例**。
我唯一做对的是：**在版 4 失败后没有继续猜，而是去测真 git。**

**但我仍然违反了自己写下的规则**（试错两轮才转向推导）。
**规则写进裁定不等于被遵守**——weaver 的规则 10 从提出到现在，
**被违反的次数是它被引用的次数。**

---

## 裁定 #51 · Phase 2 收敛；`release_truth` 的 `head` 字段是自指，不可满足

### Phase 2 实际达成（`6b4591d`，2026-10-05 14:33）

```
release_truth.py --check        ->  CHECK CLEAN   verdict=PASS   exit 0
product_freeze_sha              =  b369cd7776ae9ed57ec2acb292998855f5f54679
                                   （由 git 推导，非人工填写）
release_truth.py --selftest     ->  PASS 9/9（判别力仍在）
inc002_tree_check --a8-pins     ->  12/12 CLEAN
inc002_tree_check --selftest    ->  PASS
```

**冻结阻塞已清除。** 过程与主理人犯的错一并记录，因为它们同源。

### 阻塞是怎么解掉的

| # | 文件 | 处置 | 依据 |
|---|---|---|---|
| 1 | `tools/wal_falsifier` 输出路径 | 移出冻结产品路径到 `runtime/_verify_wal/` | 裁定 #44 R3 |
| 2 | `tools/wal_falsify_result.json` | 删除（昨天运行的旧产物，mtime 10-04） | 零引用、零 NUL、代码已不再写它 |
| 3 | `tools/inc002_index_control.py` | **入库** `f58b80b` | prism-3dui：3-in-10 是对磁盘上不存在的版本的测量 |
| 4 | 4 份 metric-verify 规范文档 | 入库 `27d1993` | 阻塞源，且是本仓唯一权威的规范载体 |
| 5 | `docs/P3_P4_SPEC.md` 的 3 个 U+FFFD | 修 `b369cd7` | agent 审计别的任务时发现并上报 |

### 三件我必须记下的错

**（1）我连续三轮在删除上失败而未察觉。**
`rm -f` 两次被 SIGTERM 截断；第三次改用 PowerShell 报 DELETED，
**但切换目录未生效**，那次「成功」不可信。
**最终以 bash 复核为准**（确认 GONE）。

> **一个删除动作的「成功」输出，本身需要复核。** 与「一个测试的通过输出」同级。

**（2）我派出的 agent 判定 12 处 F 盘引用全为历史记述、零处需改，与我的预设相反。**
独立核实后确认**它是对的**：`AGENTS.md:4` 已写明 D 盘为 rule 1 唯一根，
而全部 12 处都在**逐字引用他人文件内容**的语境
（`CONSISTENCY_MAP.md:107` 引用 `freeze_check.py:40` 的 `ROOT = Path(r"F:/jev")`）。
**改掉它们会让引用失真，等于伪造证据。**

**（3）U+FFFD 揭示了零字节检查的一个盲区（agent 发现，我已修）。**
`docs/P3_P4_SPEC.md:69` 有 3 个 `ef bf bd`（U+FFFD 替换字符），本该是破折号。
**NUL=0 查不出这类损坏**——该文件通过了我这一轮所有 NUL 检查。

> **「NUL=0」不等于「文本完好」。** 前者是「未被清零」，后者是「字节可解码且语义正确」。
> **本轮我在零字节检查上投入的全部努力，对这一类损坏完全无效。**
> 这不是检查写错了，是**「检查通过」被当成了「文件完好」**——
> 与我推翻裁定 #38 时犯的错同型：**持有正确的测量，推出它不支撑的结论。**

### 剩余 3 条 STALE 的性质

| 项 | 性质 | owner |
|---|---|---|
| `dirty_evidence_paths` | 他人正在改 `artifacts/acceptance/` | 非我 ownership |
| `dirty_paths` | 同上，含 `artifacts/release/legs/` | 同上 |
| `head` | **自指**，见下 | 需设计裁定 |

### 裁定：`head` 字段不可满足，须改语义

`--emit` 把 `head` 写成**当时的 HEAD**，而该 commit 随后包含了这个归档本身：

```
emit 时 HEAD    = b369cd7  ->  归档记录 head = b369cd7
提交归档后 HEAD = 6b4591d  ->  --check 报 head 不符
再 emit -> 再次提交 -> 再次不符
```

**这是自指方程，解是「归档不记录自己所在的 commit」。**

**裁定（三选一）**：
1. `head` 改为记录 **`last_product_commit`**（产品提交），排除归档自身；—— **推荐**
2. 归档改记 `sha256` 而非 git sha，彻底脱离自指。
3. 接受该字段永久失配，在 `--check` 中显式排除并说明理由。

**主理人裁定 1**：这份文档的用途是**证明产品未被偷改**，
而 `RELEASE_TRUTH.json` **本身是产物不是产品**。
`last_product_commit` 与 `product_freeze_sha` 已正确记录产品位置，
`head` 字段与之重复且自指，**是冗余字段**。

**本条不代改 `release_truth.py`**——改判据语义属产品代码变更，须先有反例测试。


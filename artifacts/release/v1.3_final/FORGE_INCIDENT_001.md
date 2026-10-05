# Forge 工作区冲突事件 · INC-001（并发进程回滚 CR-002 安全修复）

> ## ⚠️ SUPERSEDED — 第 0 节的归一化规则已再次订正（2026-10-05，rulings-fix）
>
> **本文曾是本轮INC-002 的事件原点**：第 0 节把 `tr -d '\r'` 标为「✅ 正确用法」，
> 后续多份文档从这一行派生。**那一行是错的。**
>
> **为什么错**：`tr -d '\r'` 删除**每一个** CR 字节，不只是 CRLF 里的那个，
> 因此**带内 CR 注入**（真实内容损坏，且不含 NUL）归一化后与干净文件**哈希相同**：
> ```
> HEAD(clean) = 61 62 0d 0a      ("ab\r\n")
> WORK(inject)= 61 0d 62 0d 0a   ("a\rb\r\n")   <- 行内多了一个 CR
> tr -d '\r'   : MATCH   <- 掩盖了真实损坏（假阴性）
> ```
>
> **正确写法**（裁定 #21 的规范：只剥 CRLF，保留带内 CR）：
> ```
> python: b.replace(b"\r\n", b"\n")   <- 注意不是 b.replace(b"\r", b"")
> shell : sed 's/\r$//'               <- 见下方「我自己的第二次撤回」
> ```
>
> ### ⚠️ 我自己的第二次撤回（2026-10-05，rulings-fix）—— 上一版订正也是错的
>
> 上一版订正把 `tr -d '\r'` 换成了 `sed 's/\r\n/\n/g'`，并断言
> 「`sed 's/\r$//'` 仍会漏报行尾双CR」。**这个断言是错的，错在我。**
>
> **我的脚本是惰性的。** 本机实测：
> ```
> printf 'ab\r\r\n' > p.bin
> sed ''             p.bin | od -An -tx1   ->  61 62 0d 0a
> sed 's/\r\n/\n/g'  p.bin | od -An -tx1   ->  61 62 0d 0a   （完全相同）
> ```
> 产生规范字节的是 **GNU sed 的行读取器**（本MSYS build 会把行尾 CRLF 的 CR 去掉），
> **不是我的脚本**。脚本本身从未生效。
>
> **为什么它在 POSIX 上必然失效**：sed 的pattern space **不含该行自己的换行符**，
> 所以两字节序列 CRLF 永远不会出现在pattern space 里，`s/\r\n/\n/g` **不可能匹配**。
> 在任何 POSIX sed 上它都是 no-op，会把**每一个普通 CRLF 文件**都报成 DIFFER。
> （POSIX 那一半来自sed 文档语义推导，**未实机执行**：本机 wsl.exe 被安全策略拦截，
> 故标记 UNVERIFIED。）
>
> **「行尾双CR 漏报」也是同一个读取器造成的假象**：它已经去掉一个 CR，
> 于是 `s/\r$//` 又去掉**第二个**。证据 —— 3 个 CR 进去，剩 1 个出来
> （正确行为应剩 2 个）：
> ```
> printf 'ab\r\r\r\n' | sed 's/\r$//' | tr -cd '\r' | wc -c   ->  1
> ```
>
> **本文件统一回到 `sed 's/\r$//'`，即prism-3dui 最初的形式** —— 那个判断是对的。
> 我原来的「9/9 一致」是**读取器的功劳，不是脚本的功劳**；把功劳记给脚本是错的。
>
> **仍然成立的开放条件**：本机 MSYS 读取器 + `s/\r$//` 会叠加剥掉最多两个 CR，
> 所以 `ab\r\r\n` 这一形态在本机被判 MATCH，而 python 规范判 DIFFER。
> 要在本机抓住该形态，**不要换sed 脚本**，用 python 形式或
> `tools/inc002_tree_check.py`（按字节读，与宿主无关）。
> **owner: team-lead**，作为未闭合条件跟踪。
>
> **被撤回的原文一律保留并标注，不删除** —— 删除会让后来者以为该问题从未存在。
> 规范正本在 `ORION_ADJUDICATIONS.md` 裁定 #21，**本文不另立规范**。

> 本文件是**取证记录**，不是推责。目的是让主理人拿到可复核的事实与哈希，
> 并明确当前 `D:\jev` 工作区里 CR-002 修复**是否在位**。
> 记录者：Forge。未经主理人裁决前，不得据此删除任何一方的产物。

---

## 0. 【必读】本文所有哈希的读法 —— 裸哈希，需先归一化行尾

**本文中的每一个 sha256 都是裸 `sha256sum` 的输出，而本仓库
`core.autocrlf=true`，工作区文件含 CRLF。**

因此：

```bash
# ❌ 错误用法 1：裸哈希对行尾敏感，会把未改动的文件报成"变了"
sha256sum packages/jcc_core/run.py
git show HEAD:packages/jcc_core/run.py | sha256sum     # 两者必然不同

# ❌ 错误用法 2：tr -d '\r' 剥掉【每一个】CR，包括行内注入的那个
tr -d '\r' < packages/jcc_core/run.py | sha256sum
git cat-file -p HEAD:packages/jcc_core/run.py | tr -d '\r' | sha256sum
```

> ⚠️ **【2026-10-05 订正，prism-3dui 提出并复现】上面标 ❌ 的第 2 条是错的用法，
> 我曾把它写成 ✅"正确用法"。`tr -d '\r'` 删除**每一个** CR 字节，
> 因此**带内 CR 注入会被洗成与干净文件相同的字节**。实测（prism 的样本，我复现确认）：
> ```
> HEAD(clean) = 61 62 0d 0a      ("ab\r\n")
> WORK(inject)= 61 0d 62 0d 0a   ("a\rb\r\n")  <- 行内多了一个 CR
> tr -d '\r'   : MATCH  <-- 掩盖了真实的内容损坏（假阴性）
> sed 's/\r$//': DIFFER <-- 抓到（本文件采用此形式）
> ```
>
> ⚠️ **【2026-10-05 更正，rulings-fix】** 我一度把本节的 `sed 's/\r$//'` 换成
> `sed 's/\r\n/\n/g'` 并声称前者对**行尾两个 CR** 仍漏报。**该断言已撤回：错在我。**
> 那个 CRLF 形式是**惰性脚本**，本机见到的规范字节来自 sed 的行读取器而非脚本；
> 在 POSIX sed 上它不可能匹配（pattern space 不含行尾换行符），会成为 no-op。
> 本节与正文 recipe 均已回到 `sed 's/\r$//'`。详见开头 SUPERSEDED 横幅。

**正确用法：先试 RAW 比对，只在 RAW 不同且确属行尾差异时才归一化**

```bash
# 半 1：RAW —— 无损，不需要任何归一化论证，也不会被行内 CR 骗过
rh=$(git cat-file -p "HEAD:$f" | sha256sum | cut -d' ' -f1)
rw=$(sha256sum "$f" | cut -d' ' -f1)
[ "$rh" = "$rw" ] && echo "IDENTICAL (raw)"        # 强结论：逐字节相同
# 仅当 RAW DIFFER 时，才用【只去 CRLF 的 CR】的归一化来分辨是 smudge 还是真损坏
# 用 sed 's/\r$//'（只去行尾 CR），不要用 tr -d '\r'（删掉每一个 CR）
[ "$(git cat-file -p "HEAD:$f" | sed 's/\r$//' | sha256sum | cut -d' ' -f1)" \
= "$(sed 's/\r$//' < "$f" | sha256sum | cut -d' ' -f1)" ] \
  && echo "EOL-ONLY (CRLF smudge, 无内容损坏)" || echo "REAL DIFFER"
```

**为什么 RAW 优先**：RAW 相等是**逐字节相同**的强结论，不需要任何"归一化是安全的"
论证，而且**抓得住带内 CR 注入**。`tr -d '\r'` 做不到后者——它把注入的 CR 也删了。
**先 RAW、再 sed 行尾归一化，两步都不丢信息。**

> ⚠️ **【可证伪性声明，weaver 提出并经我实测确认】这条订正在本树上无法被验证。**
> 判定"假阴性已消除"需要两问：(a) 改动是否消除了**已证实**的假阴性；
> (b) 该假阴性**能否在目标数据上被复现**。**本树 (b) 不成立**——实测：
>
> ```
> host_launcher.py   CR=989 LF=989   tr == sed  IDENTICAL
> run.py             CR=0   LF=450   tr == sed  IDENTICAL
> config.py          CR=0   LF=405   tr == sed  IDENTICAL
> config/jcc.yaml    CR=0   LF=113   tr == sed  IDENTICAL
> JccCommon.ps1      CR=0   LF=1054  tr == sed  IDENTICAL
> ```
>
> **本树不存在孤立 CR**（每个 CR 都属于一个 CRLF 对），所以 `tr` 与 `sed` **恒等**。
> 因此"我改成了更严的形式"在这批数据上**不可证伪**——它靠的是 prism 的
> **合成样本**（`61 0d 62 0d 0a`）成立，**不是**靠本树的实测。
> **若只在本树上验证，这次订正既无法证明也无法反驳。**
> 这不影响订正本身（合成样本已证 `tr` 有损），但**必须说清它靠什么成立**：
> 靠**一般性论证 + 合成反例**，不靠**本树的观测**。


**为什么必须这样读**（实测，见 §2.4）：`host_launcher.py` 裸哈希
`7b1ba0cb…` vs HEAD `26d9cd51…`，但 `git diff` 为空，**RAW 相等不成立**，
而 `sed 's/\r$//'` 归一化后两者**完全相同**——内容逐字节未变，只有行尾不同。
**是裸哈希误报，不是文件损坏。**


> 这一条由 forge2 提出并独立复现，我已核实。**它同时命中了我自己这份文档**：
> 我最初用裸哈希判断"文件回到原始值"，虽然该结论另有多文件同时一致 + `git diff`
> 交叉支撑、**未被 CRLF 误导**，但**证据的表达形式是脆的**。

### 三个有损变换，一个共同根因（prism-3dui 归纳，rulings-fix 首发，forge 核实）

> 这一节记录**本轮 INC-002 规则的整个失败史**，
> 因为**四个版本、三位作者**，全都以同一种方式失败过。
> 留着它比留着任何一次单独的订正更有用。

**一句话根因：把一个会丢弃某一类字节的变换，当成了相等性判据。**
于是**这个检查无法报告它本该抓的那类缺陷**。

| 版本 | 变换 | 丢掉了什么 | 后果 |
|---|---|---|---|
| 1（forge2 指出） | `tr -d '\r'` | **全部** CR | 带内 CR 注入被洗成干净文件 → 假阴性 |
| 2（prism 推荐后撤回） | `sed 's/\r$//'` | 每行**除最后一个**外的 CR | `ab\r\r\n` 折成干净文件 → 假阴性 |
| 3（relay / aegis 指出） | 零检查的**文案** | —— | 把"含 NUL"说成"内容被摧毁"，诱导抢救完好文件 |
| 4（当前） | **无变换**（RAW）或**只去行尾 CR** `sed 's/\r$//'` | 什么都不丢 | —— |

**为什么第 1、2 版看起来都"合理"**：
它们的作者（包括我）都先确认了它**能通过**正确样本，
**然后就把它当成了正确的**——**没有构造一个它会失败的样本**。
我自己的判据是后来才补上的：
> **给出一个"检测手段"时，必须构造一个该手段会失败的样本，并证明它失败。**

`tr -d` 在纯 CRLF 树上是对的；`s/\r$//` 在"一行只有一个 CR"时是对的。
**它们不是"错在实现"，是"错在未表征自己的损失面"**——
aegis 的原话是 **"right by luck, not by correctness"（靠运气对，不是靠正确对）**。

**这一节也是我全部失误的归并**：§2.3、§2.4、§0 的每一次订正，
本质都在补同一个洞——**我给出的是一个看起来可复核的形式，
而没问它能不能测出它声称要测的东西。**

### 两半各抓一类，不是互相补位（2026-10-04 订正）

> **本节订正我此前的一处错误说法。** 我一度把非零字节检查描述成
> "只查全零、会漏 (a′)"。**这是错的，已由 weaver 提出、aegis 纠正，我独立复现确认。**
> `s=$(wc -c < "$f"); z=$(tr -d '\0' < "$f" | wc -c)` 比较的是
> **总字节数**与**非 NUL 字节数**——只要文件里出现**任何一个** NUL，`z < s` 即被标记。
> 它检测的是**"含 NUL"，不是"全零"**。
>
> 实测（18 字节三组，与 team-lead 规范 §3 的 shell 形式一致）：
> ```
> clean = b'line1\nline2\nline3\n'                (18 B)  -> s=18 z=18  PASS
> half  = b'line1\n' + b'\x00'*6 + b'line3\n'      (18 B)  -> s=18 z=12  ZEROED
> allnull = b'\x00'*18                             (18 B)  -> s=18 z=0   ZEROED
> ```
> **(a′) 并没有被漏掉。** 零检查是**强信号**。
>
> 正确的分工：
>
> | 形态 | 归一化哈希 | 非零字节检查 |
> |---|---|---|
> | (a) 全清零、尺寸不变 | DIFFER | **标记** |
> | **(a′) 部分清零、尺寸不变** | DIFFER | **标记** |
> | 带内 CR 注入 | **DIFFER** | 不标记（**此形态无 NUL**） |
> | 纯 CRLF | MATCH（正确放过） | 通过（正确） |
>
> **带内 CR 注入是该检查唯一抓不到的形态**，因为那种损坏根本不含 NUL。
> **两半各抓一类：NUL 类归零检查，CR 类归一化哈希。**
>
> 另注：`tr -d '\0'` 是**正确**写法（剥 NUL 正是为统计非空字节），
> 不要当缺陷去"修"——aegis 的扫描器第一版误判过一次，加白名单后才正确。
> **差点把一个正确的检查"修坏"，与本轮主题是同一个反模式。**

> 保留裸哈希原文不改写，是为了让原始记录可审计；读的人必须知道要归一化。

**两半都要做，缺一不可**（与 forge2 指出的一致）：

```bash
# (1) 归一化后比对内容；(2) 独立核对非零字节——归一化哈希不点名"内容被清零"
s=$(wc -c < "$f"); z=$(tr -d '\0' < "$f" | wc -c)
[ "$s" = "$z" ] || echo "ZEROED $f"
```

**§2.4 已实测**：本文涉及的三个文件非零字节检查全部 OK。

---

## 1. 事件

CR-002（GREEN 4778 fail-open）修复完成后约 5 分钟，工作区内**我修改过的文件被移走**，
随后 `packages/jcc_core/config.py`、`packages/jcc_core/run.py` 被**回滚到 HEAD 形态**
（即 CR-002 修复消失，fail-open 重新打开）。

## 2. 关键取证事实

### 2.1 产物被"隔离"而非删除 —— 可完整恢复

`tools/release_truth.py` 与 `tools/cr002_green_port_falsifier.py` 曾从
`tools/` 消失，后在下列目录找到（**未被删除**）：

```
D:\jev\artifacts\release\v1.3_final\contamination_forensics\tools\release_truth.py
D:\jev\artifacts\release\v1.3_final\contamination_forensics\tools\cr002_green_port_falsifier.py
```

隔离目录共 7 个文件（mtime 2026-10-04 17:40:35 ~ 17:41:02），
sha256（隔离时刻实测）：

| 文件 | sha256 |
|---|---|
| `tools/release_truth.py` | `32c0c0f508f4d939a20b9dc3f2f34a0b4602ae77c9344269aaa81fa40c6302e0` |
| `tools/cr002_green_port_falsifier.py`（初版） | `cd8946ceba3bdf1afe307206464d7b6b7e0298742a9c222c99b911d79e008148` |
| `packages/jcc_core/config.py` | `3f1f1655b6f35c405b31c6089fe96753f25ad9eaeb63c6a0c52f0d0f56ca72e3` |
| `packages/jcc_core/run.py` | `3c1766c361597e747a7b8ef72c142b4f0fb5493a76a5548849153ba9474375cb` |
| `config/.env.example` | `12bc5e0756c8fcc0e6c22ca135de22e43d53c62c2792099380d2b255fa384ac3` |
| `integrations/_common/jcc_settings.py` | `49b76cbcd26185d00ea147f1282ced4f43ca8873b96d45baf0e499c66917308a` |
| `packages/jcc_core/tests/test_relocation_derivations.py` | `88b34b8cd4d9b85dbafa8fa57346fa57e9405ed868a6ac763a670bcde487d3d9` |

**注意**：`integrations/_common/jcc_settings.py` 与
`packages/jcc_core/tests/test_relocation_derivations.py` **不是我写的**
（前者是仓库既有 relocation 改动的一部分，后者是他人新增测试）。
这说明隔离动作**并非针对我**，而是某个更宽口径的"污染取证"扫描。

### 2.2 回滚至少发生两次

| 时刻 | 现象 |
|---|---|
| ~17:40–17:41 | 我的 5 个文件被移入 `contamination_forensics/` |
| ~17:47 | 我从隔离目录恢复（哈希逐一致），CR-002 复验 **9/9 PASS** |
| **18:03:57** | `config.py` / `run.py` mtime 再次变为 HEAD 形态；`ImportError: cannot import name 'is_green_config'` 实测复现 |
| **18:04:49** | **Harbor 提交 `11fc2be`**（CR-004 continuous port watcher） |
| ~18:06 | 我再次从隔离目录恢复，哈希复验一致 |

### 2.2.1 【更正】根因：与 commit 并行的 checkout/restore 竞态，**不是**未知进程

**本节更正 2.2 中"非隔离脚本所为、是另一个未知机制"的初步推断。**

我在 18:04 之前观测到 `git log -1` 仍是 `05715da`、reflog 无新提交，据此推断第二次回滚
"不是 commit 造成的"。**该推断随后被推翻**：`git log` 现显示期间新增 3 个提交，
HEAD 已由 `05715da` 前进到 `11fc2be`：

```
11fc2be feat(cr004): continuous port watcher -- a bounded-interval GREEN-untouched claim  (18:04:49)
e0d678c docs(integrations): the user-facing READMEs stop naming a drive that is gone
01cfb6a fix(relocation): the four agent integrations derive their repo root too
```

**18:03:57 我的改动被还原，18:04:49 Harbor 提交，相差 52 秒。**
Harbor 的 commit message 自身记录了他在 CR-002 review 期间的观察：
"During the CR-002 review the port state changed BETWEEN two samples three seconds apart"。

结论：CR-002（我）与 CR-004（Harbor）并行推进，期间发生了一次基于旧 HEAD 的
checkout/restore 类动作，冲掉了我的未提交改动。**与 17:40 的 forensics 隔离是两件独立的事**
（后者更早，且早于所有 commit）。

> 教训：我在证据不足时给出了"未知机制"的结论。正确做法是等一个提交窗口再断言，
> 或明确标注"截至 18:04 的观测"。已在正文保留原推断以供复核。

### 2.2.2 当前状态：修复是**未提交的工作区改动**，HEAD 中没有

| 项 | HEAD (`11fc2be`) | 工作区 |
|---|---|---|
| `config.py` `DEFAULT_CONFIG_PATH` | `config/jcc.yaml`（**fail-open 仍在**） | `config/jcc-v1.3.yaml`（修复在位） |
| `run.py` `check_green_port_ownership` | **不存在** | 存在 |
| `tools/release_truth.py` | **不在 HEAD** | 在 |
| `tools/cr002_green_port_falsifier.py` | **不在 HEAD** | 在 |

`git diff --stat` 实测：`config/.env.example +11 / config.py +95 / run.py +65`。
Forge 未 commit（避免与他人 commit 竞态，且未获授权 push）。
**推论：任何人再执行一次 checkout/restore/stash，CR-002 会第三次消失。**

### 2.3 当前工作区状态（最后一次实测）

```
config.py : DEFAULT_CONFIG_PATH = str(REPO_ROOT / "config" / "jcc-v1.3.yaml")   # 修复在位
            GREEN_PROTECTED_PORT = 4778 / GREEN_CONFIG_PATH / is_green_config     # 在位
run.py    : check_green_port_ownership 定义 + main() 调用                          # 在位
.env.example : CR-002 说明在位
```

`git diff --stat`（最后一次实测）：
```
config/.env.example | 11 +++++++++--   (1 file changed, 9 insertions, 2 deletions)
```
即 **`config.py` / `run.py` 当前与 HEAD 无 diff** —— 这一点必须由主理人复核，
因为它同时意味着两件事之一：(a) 修复已不在工作区；(b) 或已被某进程 commit。
`git log -1` 仍为 `05715da`，reflog 无新提交，**故判定为 (a)：修复不在工作区**。
若我此条结论有误，请以 `git log` / `git show` 为准。

## 2.3 第三次"回滚" —— 【已撤回：误判】

> **本节原记录"第三次回滚 / tracked 文件被从 HEAD 还原"，经逐项核对为误判，特此撤回。**
>
> Forge 当时据 `git diff --stat` 为空推断"改动被还原"，并已据此发出告警。
> **该推断错误**：HEAD（`88ff8a8`）本身不含 CR-002 改动，
> 所以"工作区有我的改动"与"diff 为空"**不能互推**——我把不成立的推论当成了证据。
>
> 逐项复核（实测）：
> ```
> config.py:50  DEFAULT_CONFIG_PATH = str(REPO_ROOT / "config" / "jcc-v1.3.yaml")  <- 在位
> run.py        check_green_port_ownership x4                                       <- 在位
> sha256 config.py = 3f1f1655…  run.py = 3c1766c3…   （= 恢复后的值，全程未变）
> ```
> **CR-002 始终未被回滚。** 同一时刻出现的
> `EXISTING_CONFIG_AND_SERVER_SUITES_STILL_PASS` = HOLE（32 errors），
> 源于 Forge 自己并发跑两批 pytest 抢同一批临时目录；
> 单独干净复跑为 **32 passed, 2 warnings in 20.28s**。
>
> **保留本节（而非删除）的原因**：这是一次被主动撤回的错误结论。
> 抹除它会让后来者以为 Forge 从未犯过此类错误。
> jcc-handoff-protocol 要求 UNVERIFIED 不得被改写成 VERIFIED，
> 同样要求**被推翻的结论不得被静默抹除**。
>
> **教训（已内化）**：两次误判同源——**证据不足即下结论**。
> 第一次推断"未知机制"（实为 commit 竞态，见 2.2.1），第二次推断"又一次回滚"（本次误判）。
> 凡涉及"他人动作"的结论，必须先核对哈希与时间戳再陈述。

### 2.3.1 仍然成立的风险：修复在位，但不在 git 里

第 1、2 次回滚是**真实的**（那两次核对了哈希，确认回到原始值），
机制分别是"被移入隔离区"与"被还原"。故当前虽为"在位"，
但作为**未提交工作区改动**，一次 `git checkout .` 仍会清掉它。

其**隐蔽性**更值得注意：CR-002 消失后，A8 pin 会因哈希回到原始值而**变绿**——
即**安全修复的丢失反而让门禁显示通过**。这比 fail-open 更难察觉。

### 2.3.2 GREEN 正在 4778 上运行（此项仍然有效，只读观测）

```
netstat: TCP 127.0.0.1:4778  0.0.0.0:0  LISTENING  21088
runtime/jcc/jcc.ownership.json: created_by=scripts/start.ps1  port=4778
runtime/jcc/JCC-marker: pid=21088
```

GREEN 经其正常 launcher 存活，是**主理人裁决 #1 期望的正面结果**。
但它使 CR-002 的保护对象从理论变为现实：修复一旦不在工作区，
裸跑 `python -m jcc_core.run` 就会抢占 4778，而产品与 GREEN 的 `/api/health` 都返回 200。
**Forge 全程未对 GREEN 做 signal/stop/kill**（主指令 §0.2 禁止），仅只读观测。

### 2.3.3 已采取的保全措施（不依赖任何裁决，仍然有效）

`artifacts/release/v1.3_final/CR002_GREEN_PORT_FIX.patch`
（13406 bytes / 268 行；`git apply --check --reverse` 已验证与当前工作区**逐一致**）。
即使工作区被清空，该 patch 可**一键重放**恢复修复，不依赖 Forge 会话存活。

重放方式：
```powershell
cd D:\jev
git apply artifacts\release\v1.3_final\CR002_GREEN_PORT_FIX.patch
.\.venv\Scripts\python.exe tools\cr002_green_port_falsifier.py   # 期望 9/9, exit 0
```

### 2.3.4 第二次误判：把偶发环境噪声"升级"成仓库级缺陷（已撤回）
Forge 曾报告：`tools/conftest.py:29` 用固定 basetemp 且 `exist_ok=False`，
**任何不带 `--basetemp` 的 pytest 运行都会全量 `FileExistsError`**，
并建议团队据此改 conftest。**该诊断经复核为错误。**

1. **代码读错**：实际为 `root.mkdir(parents=True, exist_ok=True)`，
   且注释明写 `# an explicit --basetemp wins`。Forge 未回读文件即断言 `exist_ok=False`。
2. **实测反证**（不带 `--basetemp`，本机）：
   ```
   test_config_failclosed.py                        -> 9 passed in 2.78s
   test_config_failclosed.py + test_server_surface.py -> 32 passed, 2 warnings in 18.13s
   ```
   全部通过。`tempfile.gettempdir()` 本机解析到
   `D:\jev\.workbuddy\scratch\host_temp`，其下的 `jev-pytest` 目录存在且健康。

**真实成因**：Forge 自己并发运行两批 pytest 争用同一棵 basetemp，
pytest 的编号目录清理撞上另一个进程，致每个测试在 fixture 阶段抛 `FileExistsError`。
即 Forge 最初的判断（并发争用）是对的，**随后那次"改口"才是错的**。

`cr002_green_port_falsifier.py` 中"每次使用唯一 `--basetemp`"的改动予以保留
（隔离 harness 属良好实践），但**记录为习惯而非修复**。

**教训（与 2.2.1、2.3 同源）**：三次误判均为"证据不足即下结论"。
第 3 次危害最大——把只出现一次、且由自己制造条件的现象，包装成需要全团队改代码的发现。
**若被采纳，即为不存在的问题改动测试基础设施。**

### 2.4 CRLF 归一化复核（2026-10-04，forge2 提出，Forge 独立核实）

**背景**：forge2 指出 INC-002 的核对规则若用裸 `sha256sum` 会**误报**——
`core.autocrlf=true` 下未改动的文件裸哈希也变。Forge 独立复现：

```
core.autocrlf = true

裸哈希：
  host_launcher.py   git show -> 26d9cd51e4458837   worktree -> 7b1ba0cb5b70040c
  config.py          git show -> <不同>              worktree -> <不同>
  run.py             git show -> <不同>              worktree -> <不同>
  git diff -- <上述文件>                                          -> 全部 EMPTY

归一化后（【sed 's/\r$//'，只去行尾 CR】——原写作 tr -d '\r'，见 §0 SUPERSEDED 横幅）：
  host_launcher.py   MATCH      config.py   MATCH      run.py   MATCH

**【2026-10-05 补测】RAW 比对（无任何变换）——这才是决定性的那一半：**
  host_launcher.py   DIFFER   <- RAW 不等，确属行尾差异，归一化是必要的
  config.py          MATCH    <- RAW 逐字节相同
  run.py             MATCH    <- RAW 逐字节相同

**这改变了我原结论的强度。** `run.py` / `config.py` **无需归一化即 RAW 相同**，
所以"这两个文件与 HEAD 逐字节一致"是**强结论**，不依赖任何归一化论证，
也就不受 `tr -d '\r'` 掩盖带内 CR 的缺陷影响。
只有 `host_launcher.py` 需要归一化，且它用的是 `sed 's/\r$//'`（能抓行内 CR），
**不是** `tr -d '\r'`。

非零字节（独立于哈希，验"含 NUL"——含任意一个 NUL 即标记，非只查全零）：
  OK size=46355 host_launcher.py   OK size=20585 run.py   OK size=18407 config.py
```

⚠️ **这项检查覆盖不到"带内 CR 注入"**——那种损坏不含 NUL。
CR 类损坏归归一化哈希管（见 §0 的分工表）。**两半各抓一类，不是互相补位。**

**结论**：本文 §2.1 的哈希**必须按 §0 归一化后读**。
**Forge 已审计自己发布过的全部哈希类断言，未发现因 CRLF 而误报的结论**：

| 断言 | 用的哈希 | 是否受影响 |
|---|---|---|
| A8 pin 判定 | `test_p3_acceptance_gate.py::_sha`（**本身就折 CRLF→LF**） | 否 |
| "三个文件回到原始值" | 裸哈希，但另有多文件同时一致 + `git diff` 交叉 | 否（结论未受影响） |
| "恢复后哈希逐一致" | 裸哈希 | 见下 |

**"恢复后逐一致"的复核（归一化后）**：
```
config.py / run.py    归一化后 DIFFER
  └─ 原因已查明：此后被【合法修改】（引号绕过修复 + getaddrinfo 服务名解析），非损坏
.env.example          归一化后 MATCH
```

**这三条非零字节检查全部通过**，故本文涉及文件均**未发生 INC-002 式清零**。

## 3. 我做过的动作（全部可逆）

1. 从隔离目录 `cp -p` 恢复 5 个文件，**未删除隔离目录任何内容**。
2. 未 commit、未 push、未 force、未 `git checkout`/`git clean`、未改 `docs/`、未改历史 evidence。
3. 每次恢复后用 sha256 与隔离时刻哈希**逐一致**才继续。
4. 发现自己的初步结论（"未知机制"）被后续证据推翻时，**在本文档内显式标注更正**，未静默改写。

## 4. 我**没有**做的事（刻意不做）

- **没有**去"修绿" A8 pin。`config.py` / `run.py` 是 A8 冻结钉住的产物，
  其哈希变化被 A8 正确捕获并 FAIL。钉子的规则明文要求
  "Re-baseline ONLY in the same commit that freezes the SHA"，
  所以重基线属于 **D 阶段 freeze commit**，不属于我。
- **没有**为绕过回滚而把文件设只读或加锁。那会污染下一次扫描的判断，
  并把一个协作问题变成一个更隐蔽的故障。

## 5. 待主理人裁决

1. `contamination_forensics/` 这次隔离的**判据是什么**？
   若判据是"工作区存在未提交改动即隔离"，则任何并行 agent 的在制品都会被清掉，
   多 agent 并行将无法进行 —— 需要改成按路径/按 owner 判定。
2. 18:03 的第二次回滚是**谁**触发的？（reflog 无提交，故非 commit；非隔离脚本。）
3. CR-002 修复应由谁落地？若允许我落地，我需要知道**何时写入不会被再次回滚**，
   否则任何复验结论都不稳定。
4. `integrations/_common/jcc_settings.py` 与
   `packages/jcc_core/tests/test_relocation_derivations.py` 被一并隔离，
   **其原 owner 需要被告知**，因为这不是它们的错。

## 6. 复现命令

```powershell
cd D:\jev
# 当前 config.py 指向
Select-String -Path packages\jcc_core\config.py -Pattern 'DEFAULT_CONFIG_PATH = '
# guard 是否存在
Select-String -Path packages\jcc_core\run.py -Pattern 'check_green_port_ownership'
# 隔离区是否完整
Get-ChildItem -Recurse artifacts\release\v1.3_final\contamination_forensics
# 复验（真实运行，非自述）
env CODEBUDDY_SAFE_DELETE_ENABLED=0 .\.venv\Scripts\python.exe tools\cr002_green_port_falsifier.py
```

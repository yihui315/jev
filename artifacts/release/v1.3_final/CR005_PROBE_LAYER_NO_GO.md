# CR-005 · 三处"探针断言错层"指控 — 复核不成立，**NO-GO（不修）**

Owner: relay (jcc-agent-integration-engineer)
Date: 2026-10-05
Status: **NO-GO — 依据不成立，故意不修。已撤回。**

这不是"未处理"。这是**一次已完成复核后的"不修"裁定**。任何人接手时
**不要**把它当成待办去做。

---

## 1. 指控内容

team-lead 转述三条"真实缺陷"，要求在 `tools/` 建带内探针修复：

| # | 指控位置 | 指控内容 |
|---|---|---|
| 1 | `tools/v1_3_aggregate_falsifiers.py:90` | probe name 指向 facade |
| 2 | `tools/v1_3_sandbox_canary_sweep.py:45,65` | 扫包级目录 |
| 3 | `tools/tests/test_sre_signer.py:36` | probe 引用集成实现自身 |

## 2. 复核结果：三条**全部不成立**

### (1) `v1_3_aggregate_falsifiers.py` — 不成立

```
:29  GATE_PATH = ROOT / "tools" / "final_gate_run_v1_3.py"
:32  spec = importlib.util.spec_from_file_location("jcc_gate", GATE_PATH)
```
它加载的是**门禁本体**，不是 facade。指控的"probe name 指向 facade"为**转述失真**。

### (2) `v1_3_sandbox_canary_sweep.py` — 不成立

`:45` = `WHY IT IS A TRACKED TOOL`（docstring 标题行）
`:65` = `Usage::`（docstring 内）
实际扫描逻辑在 `:153  names = sorted(os.listdir(root))` —— **只扫单层 root**，
不扫包级目录。

### (3) `test_sre_signer.py` — 不成立 + 路径错

- `tools/tests/test_sre_signer.py` **不存在**（`ls` 报错）
- 真实路径：`tools/continuation/test_sre_signer.py`
- 其 `:34-36`：
  ```python
  spec = importlib.util.spec_from_file_location("gen", GEN)   # GEN = tools/generate_final_sre_go.py
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  ```
  这是**测试加载被测实现**，是测试的正常写法。所谓"引用自身"不成立。

## 3. team-lead 已撤回该指控

三条均已由 team-lead 实测确认不成立并撤回，并记录为其本轮第 8 次
"转述未核实即引用"。

## 4. 为什么不修

若照着不成立的描述去建探针，release 证据里会出现**"已修复 X"，而 X 从未存在**。
这比不修更坏：它把一个假缺陷固化进交付链，且会让真实缺陷的排查被虚假项稀释。

**本次未创建任何探针文件，未修改那三个文件中的任何一个。**

## 5. 若后续仍有人报"探针断言错层"

请先提供**可复现的具体断言**，三要素缺一不可：
1. 哪一行断言
2. 它断言的对象是哪个"错层"对象
3. 期望值 vs 实测值

给齐后 relay 可立即执行（历史响应：同一轮内完成复核+实现+双向实测+提交）。

## 6. 复现命令

```bash
cd D:\jev
sed -n '29,36p' tools/v1_3_aggregate_falsifiers.py      # GATE_PATH 指向门禁本体
sed -n '45p;65p' tools/v1_3_sandbox_canary_sweep.py     # docstring 行
sed -n '153p'       tools/v1_3_sandbox_canary_sweep.py  # 实际扫描：os.listdir(root)
ls tools/tests/test_sre_signer.py                       # 不存在
sed -n '34,36p' tools/continuation/test_sre_signer.py   # 加载被测实现
```

## 7. 证据完整性

本裁定**未改动任何被指控文件**，故无回滚需求；`git status` 中
这三个文件均无本次产生的变更。本轮亦未执行
`git checkout` / `git restore` / `git stash`。

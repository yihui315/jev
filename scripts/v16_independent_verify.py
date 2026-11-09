#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JEV V1.6 独立 QA 校验脚本（只读 / 可重复执行 / 跨平台）。

模式：
  --mode baseline  只跑 4 项基线完整性检查（不依赖任何 V1.6 新文件）：
      1 V1.5 旧测试对 bfb3206 逐字节 sha256
      2 旧断言总数 = 95（运行时 evidence 主判据 + 静态计数辅判据）
      3 冻结引擎 15 文件 tree sha256
      4 paper_equity_snapshots DDL 对基线逐字节一致
      不得出现 PENDING；全部 PASS -> exit 0；有 FAIL -> exit 1。
  --mode full      默认，跑契约全部 9 项；全 PASS -> 0；有 FAIL -> 1；仅 PENDING 无 FAIL -> 2。

边界：
  * 独立 QA 产出，不实现业务代码，不修改任何被检查文件，不运行任何测试套件。
  * 仅 Python 3 标准库；仓库根由 Path(__file__).resolve().parents[1] 推导，
    无硬编码盘符，Ubuntu / GitHub Actions runner 可用；所有 git 调用用 `git -C <root>`。
  * 浅克隆降级：找不到基线对象 bfb3206 时，依赖基线的检查标 FAIL 并写明原因，绝不标 PASS。
  * 逐项独立 try/except：单项异常不影响其他项；三态 PASS / FAIL / PENDING。
  * 证据里绝不写入任何凭据明文；敏感串命中只记录「文件 / 行号 / 模式名」。

用法：
  python3 scripts/v16_independent_verify.py --mode baseline
  python3 scripts/v16_independent_verify.py --mode full
  python3 scripts/v16_independent_verify.py                 # 等价于 --mode full
输出：
  <repo>/reports/evidence/v16/independent_verify.json
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

# ----------------------------------------------------------------------------
# 仓库根：由脚本自身位置推导（scripts/v16_independent_verify.py -> parents[1]）
# ----------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = str(REPO_ROOT).replace("\\", "/")   # 统一正斜杠；Linux 上原样，Windows git -C 亦接受
BASE = "bfb3206"  # 基线提交 bfb320603367b2e8ec7b01f96864312f63ad2e6a
APP = "apps/equity_collector_v45"
APP_ABS = ROOT + "/" + APP

# 契约/任务里的 "server/engine/" 是简写；git pathspec 相对 repo 根，真实冻结
# 引擎位于 apps/command_center_3d/server/engine/（10 个 _lib/*.mjs + 5 个
# migrations/*.sql = 15 文件）。
ENGINE_REL = "apps/command_center_3d/server/engine/"
ENGINE_TREE_SHA256 = "aec5d1b7cc26db50736d355e7ef14e066fae3f2c2a376d94637c9a977008a9ad"
ENGINE_FILE_COUNT = 15

OUT_JSON = ROOT + "/reports/evidence/v16/independent_verify.json"

# 检查 1 / 2：V1.5 旧测试文件精确清单（不使用通配符，避免吞掉未知文件）
T_TESTS = [
    APP + "/tests/t0_market_probe.mjs",
    APP + "/tests/t1_multi_cycle.mjs",
    APP + "/tests/t2_idempotency.mjs",
    APP + "/tests/t3_outage_recovery.mjs",
    APP + "/tests/t4_account_isolation.mjs",
    APP + "/tests/t5_replay_readonly.mjs",
    APP + "/tests/t6_session_failclosed.mjs",
    APP + "/tests/t7_static_audit.mjs",
]
LEGACY_TESTS = [
    APP + "/tests/run-all.mjs",
    APP + "/tests/lib/d1-shim.mjs",
    APP + "/tests/lib/harness.mjs",
] + T_TESTS

# 检查 2：运行时断言判定口径（team-lead 裁定：95 是运行时 checks 总数，不是静态 R.check 数）
# 优先读当前 runner 本轮产出；CI baseline 步骤在 V1.5 套件之前运行，该文件可能尚未生成，
# 故回落读 V1.5 归档（被 git 跟踪、checkout 后必存在）。两处结构与数值一致（8/8 PASS, 95）。
CURRENT_RUN_EVIDENCE = APP + "/evidence/summary.json"
RUNTIME_EVIDENCE_SOURCES = [
    CURRENT_RUN_EVIDENCE,
    "reports/evidence/equity/local/summary.json",
]
EXPECTED_RUNTIME = {
    "overall": "PASS",
    "counts": {"total": 8, "pass": 8, "fail": 0},
    "assertions": 95,
}

# 检查 5：契约 §1.2 的 8 个 v2 列
V16_COLUMNS = [
    "exchange", "market", "price_ts_source", "network_context",
    "venue", "failure_class", "mark_contracts_json", "market_partition_key",
]
# 契约 §1.2 中明确带 CHECK 的列（venue / mark_contracts_json 仅 NOT NULL + DEFAULT）
KEY_CHECK_COLUMNS = [
    "exchange", "market", "price_ts_source", "network_context",
    "failure_class", "market_partition_key",
]

# 检查 9：契约 §4.2 的 8 组新测试 + runner
NEW_TESTS = [
    "v16_t1_field_contract.mjs",
    "v16_t2_idempotency_fields.mjs",
    "v16_t3_failure_classes.mjs",
    "v16_t7_schema_contract.mjs",
    "v16_m1_m7_mixing.mjs",
    "v16_u1_u5_units.mjs",
    "v16_migration.mjs",
    "v16_negative_mixed_cycle.mjs",
    "run-all.mjs",
]
# 契约 §4.2 未写明完整前缀，两个候选根都检查（与 V1.5 测试同目录 / repo 根）
NEW_TEST_ROOTS = [APP + "/tests/v16", "tests/v16"]

# 检查 6：敏感串模式（命中即 FAIL；detail 只记文件/行号/模式名，不记明文）
SENSITIVE_PATTERNS = [
    ("github_pat_classic", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("github_pat_fine_grained", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("paper_ledger_secret", re.compile(r"PAPER_LEDGER_SECRET")),
    ("api_key_assignment", re.compile(r"\bapi[_-]?key\s*=", re.I)),
    ("cloudflare_token_assignment",
     re.compile(r"(?:CLOUDFLARE|CF)_[A-Z_]*TOKEN\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{20,}")),
    ("bearer_token_literal", re.compile(r"Bearer\s+[A-Za-z0-9_\-]{24,}", re.I)),
]
# 仅精确排除 QA 自身 3 个产出文件（脚本内含检测规则字面量，非凭据；报告/证据为 QA 输出）。
# 不排除 reports/evidence/v16/ 下工程师产出的 V1.6 测试 evidence：它们属于 V1.6 新增文件，须纳入扫描。
QA_SELF_EXACT = {
    "scripts/v16_independent_verify.py",
    "reports/V16_INDEPENDENT_QA.md",
    "reports/evidence/v16/independent_verify.json",
}

# 运行期全局（由 main 设置）
MODE = "full"
BASELINE_AVAILABLE = None


# ----------------------------------------------------------------------------
# 通用辅助
# ----------------------------------------------------------------------------
def git_bytes(*args):
    return subprocess.check_output(["git", "-C", ROOT, *args])


def git_text(*args):
    return subprocess.check_output(["git", "-C", ROOT, *args], text=True)


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def read_bytes(rel):
    with open(ROOT + "/" + rel, "rb") as fh:
        return fh.read()


def is_qa_self(p):
    return p.replace("\\", "/") in QA_SELF_EXACT


def baseline_available():
    """浅克隆降级探测：基线 commit 对象是否可取。"""
    r = subprocess.run(
        ["git", "-C", ROOT, "cat-file", "-e", BASE + "^{commit}"],
        capture_output=True,
    )
    return r.returncode == 0


def baseline_fail_detail():
    """浅克隆降级：找不到基线对象时，依赖基线的检查一律 FAIL，绝不标 PASS。"""
    return {
        "baseline_object_available": False,
        "brief": "浅克隆降级：找不到基线对象 bfb3206，无法取基线字节",
        "error": (
            "git cat-file -e bfb3206^{commit} 失败（浅克隆 / fetch-depth 不足）；"
            "依赖 git show bfb3206:<path> 的检查按降级规则标 FAIL，绝不标 PASS。"
            "请在 CI 使用 actions/checkout 的 fetch-depth: 0 后重跑。"
        ),
    }


def strip_comments(src):
    """与 T7 stripComments 等价：剥离 // 与 /* */ 注释，但保留字符串内容
    （含 URL 里的 https:// 的 //，避免误删导致漏报 host 字面量）。"""
    out = []
    i = 0
    n = len(src)
    state = "code"
    while i < n:
        c = src[i]
        nx = src[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nx == "/":
                state = "line"; i += 2; continue
            if c == "/" and nx == "*":
                state = "block"; i += 2; continue
            if c in "\"'`":
                state = "str"; out.append(c); i += 1; continue
            out.append(c); i += 1
        elif state == "line":
            if c == "\n":
                state = "code"; out.append("\n")
            i += 1
        elif state == "block":
            if c == "*" and nx == "/":
                state = "code"; i += 2; continue
            i += 1
        else:  # str
            if c == "\\":
                out.append(c)
                if i + 1 < n:
                    out.append(src[i + 1])
                i += 2; continue
            if c in "\"'`":
                state = "code"; out.append(c); i += 1; continue
            out.append(c); i += 1
    return "".join(out)


def src_top_level():
    """src 顶层 *.mjs（不含 vendor 子目录）；返回 (dir, [文件名])。"""
    d = APP_ABS + "/src"
    if not os.path.isdir(d):
        return d, []
    files = sorted(
        f for f in os.listdir(d)
        if f.endswith(".mjs") and os.path.isfile(d + "/" + f)
    )
    return d, files


def src_code_only():
    d, files = src_top_level()
    joined = "\n".join(
        open(d + "/" + f, "r", encoding="utf-8", errors="replace").read()
        for f in files
    )
    return files, strip_comments(joined)


def extract_ddl(data, table):
    """抽取 CREATE TABLE ... <table> (...); 整块（逐字节，含 IF NOT EXISTS）。"""
    if isinstance(table, str):
        table = table.encode()
    pat = rb"CREATE\s+TABLE[^;]*?\b" + re.escape(table) + rb"\b[^;]*?\);"
    m = re.search(pat, data, re.S | re.I)
    return m.group(0) if m else None


# ----------------------------------------------------------------------------
# 检查 1：V1.5 旧测试逐字节不变（对 bfb3206 取 sha256 锚定）
# ----------------------------------------------------------------------------
def check1():
    if not BASELINE_AVAILABLE:
        return "FAIL", baseline_fail_detail()
    detail = {
        "baseline": BASE,
        "method": "逐文件: git show bfb3206:<path> 的 blob 字节 vs 当前工作树文件字节, 比较 sha256",
        "files": [],
    }
    missing = []
    equal_n = 0
    for rel in LEGACY_TESTS:
        abs_p = ROOT + "/" + rel
        if not os.path.isfile(abs_p):
            detail["files"].append({"path": rel, "status": "MISSING"})
            missing.append(rel)
            continue
        try:
            base_b = git_bytes("show", BASE + ":" + rel)
        except subprocess.CalledProcessError:
            detail["files"].append({"path": rel, "status": "BASELINE_BLOB_MISSING"})
            missing.append(rel)
            continue
        cur_b = read_bytes(rel)
        eq = base_b == cur_b
        detail["files"].append({
            "path": rel,
            "baseline_sha256": sha256_bytes(base_b),
            "current_sha256": sha256_bytes(cur_b),
            "equal": eq,
        })
        if eq:
            equal_n += 1
    detail["compared"] = len(LEGACY_TESTS) - len(missing)
    detail["equal"] = equal_n
    detail["brief"] = f"{equal_n}/{len(LEGACY_TESTS)} 个旧测试文件与基线逐字节一致"
    if missing:
        detail["missing"] = missing
        return "PENDING", detail   # full 模式保留 PENDING；baseline 模式由 main 转 FAIL
    return ("PASS" if equal_n == len(LEGACY_TESTS) else "FAIL"), detail


# ----------------------------------------------------------------------------
# 检查 2：旧断言总数 = 95（主判据：运行时 evidence；辅判据：静态计数与基线相等）
#   team-lead 裁定：95 是 RESULT_JSON 运行时 checks 总数，不是静态 R.check( 数。
#   两个判据都满足才 PASS；任一不满足标 FAIL 并在 detail 写清差异。
# ----------------------------------------------------------------------------
def count_rcheck(text):
    # \s* 覆盖 "R.check (" 与 "R.check\n(" 等跨行写法；多行调用的参数换行不影响本计数
    return len(re.findall(r"\bR\.check\s*\(", text))


def check2():
    if not BASELINE_AVAILABLE:
        return "FAIL", baseline_fail_detail()
    detail = {
        "method": (
            "主判据: 读 V1.5 运行时 evidence summary.json，要求 overall=PASS、"
            "counts={total:8,pass:8,fail:0}、sum(len(results[].checks))=95；"
            "辅判据: 静态 R.check( 计数与 bfb3206 基线逐文件相等（配合检查 1 的逐字节 sha256）。"
            "两个判据都满足才 PASS，任一不满足 FAIL。"
        ),
        "expected_runtime": EXPECTED_RUNTIME,
        "runtime_evidence_sources": RUNTIME_EVIDENCE_SOURCES,
        "note": (
            "95 是运行时 RESULT_JSON checks 总数；源码静态 R.check( 仅 92 处，"
            "差额来自 t5_replay_readonly.mjs 的 for 循环（1 个静态调用按 4 个 "
            "ENGINE_ANCHOR_PATHS key 动态生成 4 条断言，故 92-1+4=95）。"
            "本项不再把「与 95 不等属口径差异」写成 PASS。"
        ),
    }

    # ---- 主判据：运行时 evidence ----
    main_ok = False
    used = None
    attempts = []
    for src in RUNTIME_EVIDENCE_SOURCES:
        p = ROOT + "/" + src
        if not os.path.isfile(p):
            attempts.append({"source": src, "present": False})
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError) as e:
            attempts.append({"source": src, "present": True, "parse_error": str(e)})
            continue
        rs = d.get("results", []) or []
        n = sum(len(x.get("checks") or []) for x in rs)
        overall = d.get("overall")
        counts = d.get("counts") or {}
        ok = (
            overall == EXPECTED_RUNTIME["overall"]
            and counts.get("total") == EXPECTED_RUNTIME["counts"]["total"]
            and counts.get("pass") == EXPECTED_RUNTIME["counts"]["pass"]
            and counts.get("fail") == EXPECTED_RUNTIME["counts"]["fail"]
            and n == EXPECTED_RUNTIME["assertions"]
        )
        attempts.append({
            "source": src, "present": True,
            "overall": overall, "counts": counts, "assertions": n,
            "per_group": [(x.get("id"), len(x.get("checks") or [])) for x in rs],
            "criterion_pass": ok,
        })
        if ok:
            used, main_ok = src, True
            break
    detail["runtime_evidence_attempts"] = attempts
    detail["runtime_evidence_source"] = used
    detail["runtime_evidence_is_current_run"] = (used == CURRENT_RUN_EVIDENCE)
    detail["runtime_main_criterion_pass"] = main_ok
    if used and used != CURRENT_RUN_EVIDENCE:
        detail["runtime_evidence_caveat"] = (
            "当前 runner 的本轮 evidence 未提供，回落至 V1.5 归档（被 git 跟踪）。"
            "归档只能证明基线断言数量/措辞，不能证明本轮源码的运行时 8/8 PASS；"
            "本轮运行时结果由 CI 的 V1.5 套件步骤及阶段 2 独立重跑验证。"
        )

    # ---- 辅判据：静态 R.check( 与基线相等 ----
    static_per_file = []
    cur_total = 0
    base_total = 0
    static_ok = True
    for rel in T_TESTS:
        if not os.path.isfile(ROOT + "/" + rel):
            static_per_file.append({"path": rel, "status": "MISSING"})
            static_ok = False
            continue
        cur = count_rcheck(read_bytes(rel).decode("utf-8", "replace"))
        try:
            base = count_rcheck(git_bytes("show", BASE + ":" + rel).decode("utf-8", "replace"))
        except subprocess.CalledProcessError:
            base = None
        eq = (base is not None and cur == base)
        static_ok = static_ok and eq
        static_per_file.append({"path": rel, "current": cur, "baseline": base, "equal": eq})
        cur_total += cur
        base_total += (base or 0)
    detail["static_calls_current"] = cur_total
    detail["static_calls_baseline"] = base_total
    detail["static_per_file"] = static_per_file
    detail["static_aux_criterion_pass"] = static_ok

    status = "PASS" if (main_ok and static_ok) else "FAIL"
    last = attempts[-1] if attempts else {}
    detail["brief"] = (
        f"主判据运行时 evidence={'通过' if main_ok else '未通过'}"
        f"（来源 {used or '无'}，断言 {last.get('assertions', '?')}，overall={last.get('overall', '?')}）；"
        f"辅判据静态 R.check( {cur_total} vs 基线 {base_total}={'相等' if static_ok else '不等'}；"
        f"{'两个判据都满足 -> PASS' if status == 'PASS' else '任一不满足 -> FAIL'}"
    )
    return status, detail


# ----------------------------------------------------------------------------
# 检查 3：冻结引擎零改动（tree sha256 + 文件数）
# ----------------------------------------------------------------------------
def check3():
    out = git_text("ls-tree", "-r", "HEAD", "--", ENGINE_REL)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    blob = "\n".join(lines)  # 末尾不带换行；与锚点算法一致
    tree_sha = sha256_bytes(blob.encode("utf-8"))
    n = len(lines)
    detail = {
        "engine_path": ENGINE_REL,
        "baseline_object_available": BASELINE_AVAILABLE,
        "note": (
            "契约/任务的 server/engine/ 为简写；git pathspec 相对 repo 根，真实冻结引擎位于该路径。"
            "本项用 HEAD 的 ls-tree 与锚点比对，不依赖 git show 基线对象。"
        ),
        "algorithm": (
            "git ls-tree -r HEAD -- <engine> 的原始行（完整 repo 路径，格式 mode type sha\\tpath），"
            "过滤空行后用 \\n 连接且末尾不带换行，取 utf-8 字节算 sha256"
        ),
        "file_count": n,
        "expected_file_count": ENGINE_FILE_COUNT,
        "tree_sha256": tree_sha,
        "expected_tree_sha256": ENGINE_TREE_SHA256,
        "files": [ln.split("\t", 1)[1] for ln in lines if "\t" in ln],
    }
    detail["brief"] = f"{n} 文件, tree_sha256={tree_sha[:12]}… 与锚点{'一致' if tree_sha == ENGINE_TREE_SHA256 else '不一致'}"
    if n == 0:
        detail["missing"] = ENGINE_REL
        return "PENDING", detail
    return ("PASS" if (tree_sha == ENGINE_TREE_SHA256 and n == ENGINE_FILE_COUNT) else "FAIL"), detail


# ----------------------------------------------------------------------------
# 检查 4：冻结权益快照表 DDL 不变
#   baseline 模式：两方必需（当前 schema.sql 块 vs 基线 schema.sql 块）；0001 可选，
#                  存在时额外验证「0001 全文逐字节 == 基线 schema.sql 全文」并做三方比对。
#   full 模式：要求 0001 存在并做三方比对；0001 缺失 -> PENDING。
# ----------------------------------------------------------------------------
def check4():
    if not BASELINE_AVAILABLE:
        return "FAIL", baseline_fail_detail()
    rel_0001 = APP + "/migrations/0001_v45_base.sql"
    rel_schema = APP + "/schema.sql"
    detail = {
        "table": "paper_equity_snapshots",
        "mode": MODE,
        "method": "抽取 CREATE TABLE ... paper_equity_snapshots (...); 整块, 逐字节比较 sha256",
        "sources": {},
    }
    cur = read_bytes(rel_schema) if os.path.isfile(ROOT + "/" + rel_schema) else None
    if cur is None:
        detail["missing"] = [rel_schema]
        detail["brief"] = "schema.sql 缺失，无法做 DDL 比对"
        return "FAIL", detail
    base = git_bytes("show", BASE + ":" + rel_schema)
    d_cur = extract_ddl(cur, b"paper_equity_snapshots")
    d_base = extract_ddl(base, b"paper_equity_snapshots")
    two_way = (d_cur is not None and d_base is not None and d_cur == d_base)
    entry = {
        "current_schema.sql": {"present": True, "ddl_sha256": sha256_bytes(d_cur) if d_cur else None},
        "baseline_schema.sql": {"present": True, "ddl_sha256": sha256_bytes(d_base) if d_base else None},
        "two_way_equal": two_way,
    }

    has_0001 = os.path.isfile(ROOT + "/" + rel_0001)
    if has_0001:
        b_0001 = read_bytes(rel_0001)
        d_0001 = extract_ddl(b_0001, b"paper_equity_snapshots")
        full_byte_equal = (b_0001 == base)  # 契约 §1.1：0001 是 bfb3206:schema.sql 的逐字节复制
        three_way = two_way and d_0001 is not None and d_0001 == d_base == d_cur
        entry["0001_v45_base.sql"] = {"present": True, "ddl_sha256": sha256_bytes(d_0001) if d_0001 else None}
        detail["sources"] = entry
        detail["0001_full_byte_equal_to_baseline_schema"] = full_byte_equal
        detail["three_way_equal"] = three_way
        detail["brief"] = (
            f"三方 DDL {'逐字节一致' if three_way else '不一致'}; "
            f"0001 全文==基线 schema.sql 全文={full_byte_equal}"
        )
        return ("PASS" if (three_way and full_byte_equal) else "FAIL"), detail

    entry["0001_v45_base.sql"] = {"present": False, "ddl_sha256": None}
    detail["sources"] = entry
    if MODE == "baseline":
        detail["0001_optional"] = (
            "baseline 模式不依赖 V1.6 新文件：0001 未提供，按两方"
            "（当前 schema.sql 块 vs 基线 schema.sql 块）判定"
        )
        detail["brief"] = f"0001 未提供（baseline 可选）；两方 DDL {'逐字节一致' if two_way else '不一致'}"
        return ("PASS" if two_way else "FAIL"), detail
    detail["missing"] = [rel_0001]
    detail["three_way_equal"] = None
    detail["brief"] = (
        f"0001 缺失，三方比对未验证；已可比对的两方（当前 vs 基线 schema.sql）"
        f"{'逐字节一致' if two_way else '不一致'}"
    )
    return "PENDING", detail


# ----------------------------------------------------------------------------
# 检查 5：新列与约束存在（v45_snapshot_provenance 的 8 列 / CHECK / NOT NULL）
# ----------------------------------------------------------------------------
def check5():
    if not BASELINE_AVAILABLE:
        return "FAIL", baseline_fail_detail()
    rel_schema = APP + "/schema.sql"
    detail = {
        "columns_required": V16_COLUMNS,
        "key_check_columns": KEY_CHECK_COLUMNS,
        "note": "仅当 schema.sql 的 provenance DDL 相对基线已被改造时才做实质校验；若仍是 V1.5 基线态则标 PENDING（未验证）。",
    }
    if not os.path.isfile(ROOT + "/" + rel_schema):
        detail["missing"] = [rel_schema]
        return "PENDING", detail
    cur_ddl = extract_ddl(read_bytes(rel_schema), b"v45_snapshot_provenance")
    base_ddl = extract_ddl(git_bytes("show", BASE + ":" + rel_schema), b"v45_snapshot_provenance")
    if cur_ddl is None or base_ddl is None:
        detail["error"] = "无法从 schema.sql 抽取 v45_snapshot_provenance 的 CREATE TABLE 块"
        return "FAIL", detail
    if cur_ddl == base_ddl:
        detail["unchanged_vs_baseline"] = True
        detail["brief"] = "schema.sql 的 provenance DDL 仍与基线逐字节一致：v2 列改造（§1.2）尚未交付"
        detail["missing_deliverable"] = "apps/equity_collector_v45/schema.sql 的 v2 provenance 改造"
        return "PENDING", detail

    text = cur_ddl.decode("utf-8", "replace")
    cols = {}
    for col in V16_COLUMNS:
        m = re.search(r"^\s*" + re.escape(col) + r"\b[^\n]*", text, re.M)
        line = m.group(0) if m else ""
        cols[col] = {
            "present": bool(line),
            "not_null": "NOT NULL" in line.upper(),
            "has_check": "CHECK" in line.upper(),
        }
    detail["columns"] = cols
    all_present = all(c["present"] for c in cols.values())
    all_not_null = all(c["not_null"] for c in cols.values())
    key_check_ok = all(cols[c]["has_check"] for c in KEY_CHECK_COLUMNS)
    detail["all_present"] = all_present
    detail["all_not_null"] = all_not_null
    detail["key_check_ok"] = key_check_ok
    detail["brief"] = f"8 列存在={all_present}, 全部 NOT NULL={all_not_null}, 关键列 CHECK={key_check_ok}"
    return ("PASS" if (all_present and all_not_null and key_check_ok) else "FAIL"), detail


# ----------------------------------------------------------------------------
# 检查 6：敏感串扫描（V1.6 新增/修改文件，命中即 FAIL）
# ----------------------------------------------------------------------------
def check6():
    if not BASELINE_AVAILABLE:
        return "FAIL", baseline_fail_detail()
    changed = set()
    for args in (
        ("diff", "--name-only", BASE, "HEAD"),
        ("diff", "--cached", "--name-only", BASE),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        try:
            for ln in git_text(*args).splitlines():
                p = ln.strip().replace("\\", "/")
                if p and not is_qa_self(p):
                    changed.add(p)
        except subprocess.CalledProcessError:
            pass
    excluded = sorted(p for p in QA_SELF_EXACT)
    detail = {
        "patterns": [name for name, _ in SENSITIVE_PATTERNS],
        "excluded_qa_self_outputs": excluded,
        "v16_changed_files": sorted(changed),
        "hits": [],
        "note": "命中只记录 文件/行号/模式名，绝不写入匹配到的明文。脚本内含检测规则字面量（非凭据），已排除 QA 自身产出。",
    }
    if not changed:
        detail["brief"] = "V1.6 新增/修改文件集合为空（工作区与基线一致），暂无可扫描对象，未验证"
        detail["phase2_requirement"] = "工程师交付后必须重扫新增/修改文件"
        return "PENDING", detail

    hits = []
    for p in sorted(changed):
        abs_p = ROOT + "/" + p
        if not os.path.isfile(abs_p):
            continue
        try:
            text = open(abs_p, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            for name, rx in SENSITIVE_PATTERNS:
                if rx.search(line):
                    hits.append({"file": p, "line": ln, "pattern": name})
    detail["hits"] = hits
    detail["scanned_files"] = sorted(changed)
    detail["brief"] = f"扫描 {len(changed)} 个 V1.6 新增/修改文件, 命中 {len(hits)} 处"
    return ("FAIL" if hits else "PASS"), detail


# ----------------------------------------------------------------------------
# 检查 7：src 顶层禁止 Global 主机名（剥离注释后）
# ----------------------------------------------------------------------------
def check7():
    d, files = src_top_level()
    if not files:
        return "PENDING", {"missing": d, "brief": "src 顶层无 *.mjs 可扫"}
    code = src_code_only()[1]
    forbidden = "data-api.binance.vision" in code
    primary = '"api.binance.us"' in code
    raw_occurrences = []
    for f in files:
        for ln, line in enumerate(open(d + "/" + f, "r", encoding="utf-8", errors="replace"), 1):
            if "data-api.binance.vision" in line:
                raw_occurrences.append({"file": f, "line": ln})
    detail = {
        "src_top_level_files": files,
        "vendor_excluded": True,
        "strip_comments": "与 T7 等价：保留字符串内容（含 URL 的 //），剥离 // 与 /* */ 注释",
        "forbidden_host_literal_in_executable_code": forbidden,
        "primary_host_quoted_literal_present": primary,
        "forbidden_host_raw_occurrences": raw_occurrences,
        "note": "契约 H4：src 顶层可执行代码不得出现 data-api.binance.vision 字面量；仅 allowlist api.binance.us。",
    }
    detail["brief"] = (
        f"可执行代码中 data-api.binance.vision={'出现' if forbidden else '未出现'}; "
        f'\"api.binance.us\" 字面量={"存在" if primary else "缺失"}'
    )
    return ("PASS" if (not forbidden and primary) else "FAIL"), detail


# ----------------------------------------------------------------------------
# 检查 8：无 console / 无凭据引用 / 写入仍为 INSERT OR IGNORE（对齐 H6 / T7）
# ----------------------------------------------------------------------------
def check8():
    d, files = src_top_level()
    if not files:
        return "PENDING", {"missing": d, "brief": "src 顶层无 *.mjs 可扫"}
    code = src_code_only()[1]
    console = bool(re.search(r"\bconsole\.(log|info|warn|error)\s*\(", code))
    cred = bool(re.search(r"PAPER_LEDGER_SECRET|JEV_PREVIEW_PASSWORD|api[_-]?key|bearer\s+token", code, re.I))
    or_ignore_n = len(re.findall(r"INSERT\s+OR\s+IGNORE\s+INTO", code, re.I))
    bare = re.findall(r"INSERT\s+(?!OR\s+IGNORE\s+)INTO\s+[A-Za-z_`\"]", code, re.I)
    insert_snippets = re.findall(r"INSERT[^;\n`]{0,80}INTO[^;\n`]{0,50}", code)
    ok = (not console) and (not cred) and (or_ignore_n >= 2) and (len(bare) == 0)
    detail = {
        "src_top_level_files": files,
        "console_call_present": console,
        "credential_reference_present": cred,
        "insert_or_ignore_count": or_ignore_n,
        "bare_insert_into_writes": bare,
        "insert_snippets_in_code": insert_snippets,
        "note": (
            "对齐契约 H6 与 T7：无 console.*、无凭据引用；所有写入为 INSERT OR IGNORE INTO。"
            "compute.mjs 的 replace(/^INSERT INTO/, 'INSERT OR IGNORE INTO') 是正则字面量（非裸写 SQL），"
            "通过 'INSERT INTO 后紧跟表名' 的判定排除，不计为裸写。"
        ),
    }
    detail["brief"] = (
        f"console={'有' if console else '无'}, 凭据引用={'有' if cred else '无'}, "
        f"INSERT OR IGNORE INTO x{or_ignore_n}, 裸 INSERT INTO 写={len(bare)}"
    )
    return ("PASS" if ok else "FAIL"), detail


# ----------------------------------------------------------------------------
# 检查 9：新测试文件存在性（契约 §4.2 8 组 + run-all）
# ----------------------------------------------------------------------------
def check9():
    detail = {
        "candidate_roots": NEW_TEST_ROOTS,
        "expected_files": NEW_TESTS,
        "per_root": {},
        "note": "契约 §4.2 未写明完整前缀，两个候选根都检查；只在某一个根下全部齐全才判 PASS。",
    }
    complete_at = None
    for root in NEW_TEST_ROOTS:
        present, missing = [], []
        for f in NEW_TESTS:
            (present if os.path.isfile(ROOT + "/" + root + "/" + f) else missing).append(f)
        detail["per_root"][root] = {"present": present, "missing": missing}
        if not missing and complete_at is None:
            complete_at = root
    if complete_at:
        detail["complete_at"] = complete_at
        detail["brief"] = f"{len(NEW_TESTS)} 个新测试文件在 {complete_at} 下齐全"
        return "PASS", detail
    missing_paths = [
        root + "/" + f
        for root in NEW_TEST_ROOTS
        for f in detail["per_root"][root]["missing"]
    ]
    detail["missing_paths"] = missing_paths
    detail["brief"] = f"8 组 tests/v16/*.mjs + run-all.mjs 尚未交付（缺失路径 {len(missing_paths)} 条）"
    return "PENDING", detail


# ----------------------------------------------------------------------------
# 编排
# ----------------------------------------------------------------------------
CHECKS = [
    ("1", "V1.5 旧测试逐字节不变（H1）", check1),
    ("2", "旧断言总数 = 95（H3，运行时 evidence + 静态计数双判据）", check2),
    ("3", "冻结引擎零改动（H2，tree sha256 + 15 文件）", check3),
    ("4", "冻结权益快照表 DDL 对基线逐字节一致（H2）", check4),
    ("5", "v2 新列与 CHECK/NOT NULL 约束存在（§1.2）", check5),
    ("6", "V1.6 新增/修改文件敏感串扫描（H7）", check6),
    ("7", "src 顶层禁止 Global 主机名（H4）", check7),
    ("8", "无 console / 无凭据引用 / INSERT OR IGNORE（H6）", check8),
    ("9", "契约 §4.2 新测试文件齐全（§4.2）", check9),
]
BASELINE_CIDS = {"1", "2", "3", "4"}


def main():
    global MODE, BASELINE_AVAILABLE
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="JEV V1.6 独立 QA 校验（只读）"
    )
    ap.add_argument(
        "--mode", choices=["baseline", "full"], default="full",
        help="baseline=仅 4 项基线完整性(无 PENDING, CI 门禁); full=全部 9 项(默认)",
    )
    args = ap.parse_args()
    MODE = args.mode
    BASELINE_AVAILABLE = baseline_available()

    selected = [c for c in CHECKS if c[0] in BASELINE_CIDS] if MODE == "baseline" else CHECKS

    results = []
    for cid, name, fn in selected:
        try:
            status, detail = fn()
            if status not in ("PASS", "FAIL", "PENDING"):
                status, detail = "FAIL", {"error": "非法状态: " + str(status), "detail": detail}
        except Exception as e:
            status = "FAIL"
            detail = {
                "error": type(e).__name__ + ": " + str(e),
                "traceback_tail": traceback.format_exc().splitlines()[-4:],
                "brief": "检查自身抛出异常",
            }
        results.append({
            "check": cid + ". " + name,
            "pass": status,
            "detail": detail,
        })

    # baseline 模式：禁止 PENDING（CI 会把 exit!=0 判红），且浅克隆时全部 FAIL（绝不误放绿灯）
    if MODE == "baseline":
        for r in results:
            if r["pass"] == "PENDING":
                r["pass"] = "FAIL"
                r["detail"]["baseline_mode_no_pending"] = (
                    "baseline 模式禁止 PENDING（否则 CI 会被误判），转为 FAIL"
                )
            if not BASELINE_AVAILABLE:
                r["pass"] = "FAIL"
                r["detail"]["shallow_clone_degraded"] = (
                    "浅克隆降级：找不到基线对象 bfb3206，baseline 模式 4 项一律 FAIL，绝不标 PASS"
                )
                r["detail"]["brief"] = "浅克隆降级：找不到基线对象 bfb3206，本项标 FAIL（绝不标 PASS）"
        counts = {s: sum(1 for r in results if r["pass"] == s) for s in ("PASS", "FAIL", "PENDING")}
        exit_code = 0 if counts["FAIL"] == 0 and counts["PASS"] == len(selected) else 1
    else:
        counts = {s: sum(1 for r in results if r["pass"] == s) for s in ("PASS", "FAIL", "PENDING")}
        if counts["FAIL"] > 0:
            exit_code = 1
        elif counts["PENDING"] > 0:
            exit_code = 2
        else:
            exit_code = 0

    doc = {
        "tool": "v16_independent_verify.py",
        "role": "独立 QA（只读，不实现代码，不替工程师签字）",
        "phase": "阶段 1 — 基线完整性 + 静态契约检查（中间态快照）",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": MODE,
        "root": ROOT,
        "root_derivation": "Path(__file__).resolve().parents[1]（无硬编码盘符）",
        "baseline": BASE,
        "baseline_object_available": BASELINE_AVAILABLE,
        "exit_code_semantics": {
            "baseline": "全 PASS -> 0；有 FAIL（含 PENDING 转换）-> 1；不得出现 PENDING",
            "full": "全 PASS -> 0；有 FAIL -> 1；仅 PENDING 无 FAIL -> 2",
        },
        "legend": {
            "PASS": "该检查已验证通过",
            "FAIL": "该检查已验证且违反契约（含浅克隆降级）",
            "PENDING": "依赖的交付物尚未存在/尚未改造，未验证（不是 PASS，也不是违规判定；baseline 模式禁止出现）",
        },
        "summary": {"PASS": counts["PASS"], "FAIL": counts["FAIL"],
                    "PENDING": counts["PENDING"], "exit_code": exit_code},
        "checks": results,
    }

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    print(f"=== JEV V1.6 独立校验（mode={MODE}）===")
    for r in results:
        d = r["detail"] if isinstance(r["detail"], dict) else {}
        print(f"[{r['pass']:<7}] {r['check']}")
        print(f"          {d.get('brief', '')}")
    print(f"合计: PASS={counts['PASS']}  FAIL={counts['FAIL']}  PENDING={counts['PENDING']}")
    print(f"退出码: {exit_code}")
    print(f"JSON: {OUT_JSON}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

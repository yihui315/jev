#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H7 敏感串扫描规则的正向 / 负向单元测试（仅 Python 标准库）。

运行：
    python3 scripts/tests/test_v16_h7_secret_scan.py -v
或：
    python3 -m unittest discover -s scripts/tests -p "test_v16_h7*.py" -v

边界：
  * 本文件不含任何真实凭据字面量。所有「真实凭据」负向样例在运行时合成，
    避免 H7 对本测试文件做 whole-file 扫描时自己命中。
  * 正向：env 读取、测试占位表达式、短占位串、模板插值、非字面量表达式豁免。
  * 负向：真实硬编码凭据与带前缀 token 字面量仍然 FAIL。
  * 完整性：H7 未被全局关闭（QA_SELF_EXACT 不变、check6 仍注册、模式集非空）。
"""

import importlib.util
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "v16_independent_verify",
    _REPO_ROOT / "scripts" / "v16_independent_verify.py",
)
v16 = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = v16
_SPEC.loader.exec_module(v16)


def _secret(n=40):
    """运行时合成高熵串；源码中不落地任何真实凭据字面量。"""
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(alphabet[(i * 13 + 7) % len(alphabet)] for i in range(n))


def _assign(varname, value):
    """运行时拼出 `<var> = "<value>";`，源码行本身不触发扫描规则。"""
    return "const " + varname + " = " + '"' + value + '";'


class H7PositiveExemptions(unittest.TestCase):
    """正向：这些是引用 / 占位，不是硬编码真实凭据，必须放行。"""

    def test_environment_variable_read_is_not_a_secret(self):
        # 复现 real-worm.mjs:17 的误报
        line = "  const " + "apiKey" + " = env.REAL_WORM_API_KEY;"
        self.assertEqual(v16.scan_line_for_secrets(line), [])

    def test_phase_d_repeat_placeholder_is_not_a_secret(self):
        # 复现 phase-d-verify.mjs:712 的误报
        line = ("  const env = { PAPER_DB: makeMemLedgerDb(), "
                "PAPER_LEDGER_SECRET: 'x'.repeat(40) };")
        self.assertEqual(v16.scan_line_for_secrets(line), [])

    def test_process_env_getenv_and_injected_config_are_reads(self):
        lines = [
            "const token = process.env.JEV_PREVIEW_TOKEN;",
            "const apiKey = getenv('REAL_WORM_API_KEY');",
            "const SECRET = config.get('paper_ledger_secret');",
            "const password = os.environ['DB_PASSWORD'];",
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(v16.scan_line_for_secrets(line), [])

    def test_short_placeholder_literals(self):
        for lit in ("test", "placeholder", "changeme", "${API_KEY}",
                    "<set-in-cd>", "", "xxxxxxxxxxxxxxxxxxxxxxxx"):
            with self.subTest(lit=lit):
                self.assertEqual(v16.scan_line_for_secrets(_assign("apiKey", lit)), [])

    def test_non_literal_expressions(self):
        lines = [
            "const apiKey = buildKey();",
            "const token = ctx.token;",
            "PAPER_LEDGER_SECRET: makeTestSecret()",
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(v16.scan_line_for_secrets(line), [])

    def test_explicit_fixture_markers_are_placeholders(self):
        # 仓库既有夹具：显式写了「不是真实密钥」/「最小长度填充」的假值
        lines = [
            'const env = { OPENAI_API_KEY: "unit-test-not-a-real-key-abc" };',
            'const RB_SECRET = "rb-secret-min-16-bytes-long";',
            'const IDP_SECRET = "idp-audit-secret-min-16-bytes-long-0001";',
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(v16.scan_line_for_secrets(line), [])

    def test_non_credential_key_names_are_not_matched(self):
        # 证明赋值匹配面没有被扩大到任意含 KEY 的标识符
        lines = [
            'const STORAGE_KEY = "jev.cockpit.layout.v2";',
            'market_partition_key: "Binance Global spot|spot",',
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(v16.scan_line_for_secrets(line), [])

    def test_classify_rhs_matrix(self):
        self.assertEqual(v16.classify_rhs("env.X"), "env_read")
        self.assertEqual(v16.classify_rhs("process.env.X"), "env_read")
        self.assertEqual(v16.classify_rhs("'x'.repeat(40)"), "non_literal")
        self.assertEqual(v16.classify_rhs('"test"'), "placeholder")
        self.assertEqual(v16.classify_rhs('"unit-test-not-a-real-key-abc"'), "placeholder")
        self.assertEqual(v16.classify_rhs('"' + _secret(40) + '"'), "real_secret")


class H7NegativeStillFails(unittest.TestCase):
    """负向：真实硬编码凭据必须 FAIL，不得因修复而被放过。"""

    def test_hardcoded_api_key_fails(self):
        hits = v16.scan_line_for_secrets(_assign("apiKey", _secret(40)))
        self.assertTrue(
            hits,
            "真实硬编码 apiKey 必须被 H7 拦截",
        )

    def test_hardcoded_paper_ledger_secret_fails(self):
        line = "PAPER_LEDGER_SECRET: " + '"' + _secret(40) + '"'
        hits = v16.scan_line_for_secrets(line)
        self.assertTrue(
            hits,
            "真实硬编码 PAPER_LEDGER_SECRET 必须被 H7 拦截",
        )

    def test_hardcoded_cloudflare_token_fails(self):
        line = "CLOUDFLARE_TOKEN = " + '"' + _secret(32) + '"'
        hits = v16.scan_line_for_secrets(line)
        self.assertTrue(
            hits,
            "真实硬编码 CLOUDFLARE_TOKEN 必须被 H7 拦截",
        )

    def test_all_six_original_patterns_still_fail(self):
        """原契约 6 个模式名逐一验证：真实凭据/token 字面量仍被拦截。"""
        cases = [
            ("github_pat_classic", "ghp_" + _secret(24)),
            ("github_pat_fine_grained", "github_pat_" + _secret(24)),
            ("bearer_token_literal", "Authorization: Bearer " + _secret(30)),
            ("api_key_assignment", _assign("apiKey", _secret(40))),
            ("paper_ledger_secret", "PAPER_LEDGER_SECRET: " + '"' + _secret(40) + '"'),
            ("cloudflare_token_assignment", "CLOUDFLARE_TOKEN = " + '"' + _secret(32) + '"'),
        ]
        for pattern, line in cases:
            with self.subTest(pattern=pattern):
                hits = v16.scan_line_for_secrets(line)
                self.assertTrue(
                    any(h["pattern"] == pattern for h in hits),
                    pattern + " 必须仍被 H7 拦截",
                )

    def test_real_secret_is_not_exempted_by_any_placeholder(self):
        counts = {}
        hits = v16.scan_line_for_secrets(_assign("apiKey", _secret(48)), counts)
        self.assertTrue(hits)
        self.assertEqual(counts.get("real_secret"), 1)


class H7GateIntegrity(unittest.TestCase):
    """证明 H7 未被全局关闭 / 未被宽泛白名单稀释。"""

    def test_qa_self_exclusions_not_widened(self):
        self.assertEqual(v16.QA_SELF_EXACT, {
            "scripts/v16_independent_verify.py",
            "reports/V16_INDEPENDENT_QA.md",
            "reports/evidence/v16/independent_verify.json",
        })

    def test_check6_still_registered_in_full_gate(self):
        ids = [c[0] for c in v16.CHECKS]
        self.assertEqual(len(ids), 9)
        self.assertIn("6", ids)

    def test_original_six_pattern_names_preserved(self):
        self.assertEqual(v16.SENSITIVE_PATTERNS, [
            "github_pat_classic",
            "github_pat_fine_grained",
            "paper_ledger_secret",
            "api_key_assignment",
            "cloudflare_token_assignment",
            "bearer_token_literal",
        ])

    def test_classification_is_non_vacuous(self):
        """四种分类都在真实行上出现，规则确实在逐行分类而非一律放行。"""
        counts = {}
        lines = [
            "const apiKey = env.REAL_WORM_API_KEY;",
            "PAPER_LEDGER_SECRET: 'x'.repeat(40)",
            'const apiKey = "test";',
            _assign("apiKey", _secret(40)),
        ]
        for line in lines:
            v16.scan_line_for_secrets(line, counts)
        for verdict in ("env_read", "non_literal", "placeholder", "real_secret"):
            self.assertIn(verdict, counts, verdict + " 必须出现在分类计数中")


if __name__ == "__main__":
    unittest.main(verbosity=2)

# Collection failure: `E2E_CONTROL_TOKEN` — root cause

Owner: prism-3dui. Branch `release/jcc-v1.3-final`, base HEAD `590ecf3`.

## Verdict in one line

The symbol was never missing from the repository. Two **uncommitted**
working-tree copies of `tests/e2e/*` had been dropped into
`packages/jcc_core/tests/`, where `from conftest import E2E_CONTROL_TOKEN`
resolves to `packages/conftest.py` (which has never defined it) instead of
`tests/e2e/conftest.py` (which does). The fix is to restore the two files to
their committed state — not to add the constant.

## The two facts that decide it

1. **The committed files never imported it.**
   ```
   $ git show HEAD:packages/jcc_core/tests/test_domain_counts_v1_3.py | grep E2E_CONTROL_TOKEN
   (no output)
   ```
   So the breakage was introduced by an uncommitted edit, not by anything in
   history. `git log` confirms the file was last touched by `13d3849`
   ("v1.3.3-wave9.3"), long before RISK-1.

2. **The symbol exists — in the other tree.**
   `tests/e2e/conftest.py:108` defines `E2E_CONTROL_TOKEN`, alongside the
   `api_server` fixture every one of these tests takes as a parameter. The
   e2e originals at `tests/e2e/test_domain_counts_v1_3.py` and
   `tests/e2e/test_deliverables_v1_3.py` are the SAME tests, byte-identical
   for deliverables and one line apart for counts.

## Why "just add the constant to conftest" is wrong — measured

Adding `E2E_CONTROL_TOKEN` to `packages/conftest.py` does not fix anything; it
changes the failure mode. Measured, then reverted:

```
6 errors in 0.90s
E   fixture 'api_server' not found
E   available fixtures: _isolate_jcc_spool, anyio_backend, capfd, ...
```

pytest resolves fixtures by **directory ancestry**, not by import. A fixture
defined in `tests/e2e/conftest.py` is simply not visible to a test living in
`packages/jcc_core/tests/`. So the "fix" converts one collection error into six
fixture errors, and would additionally require re-exporting the whole e2e server
harness into `packages/` — duplicating a fixture that `tools/tests/
test_v1_1_test_isolation.py` already pins by path (`E2E_CONFTEST =
REPO / "tests" / "e2e" / "conftest.py"`).

## Why restoring is not "deleting assertions"

The copies were **duplicates of tests that already exist and run** in
`tests/e2e/`. Restoring loses no coverage. It *recovers* coverage: the
uncommitted copy had overwritten 7 unit tests in `test_domain_counts_v1_3.py`
that exist nowhere else, including three that pin real invariants —

| test | what it uniquely pins |
|---|---|
| `test_the_ceiling_is_a_real_documented_number` | `MAX_DOMAIN_LIST_LIMIT` is a decision, not an accident (>=1000, <=100_000) |
| `test_counts_is_uncapped_while_the_list_read_is_bounded` | the whole point of Wave 9.3: counts 7, list clamps to 5 |
| `test_counts_of_archived_projects_do_not_inflate_the_active_count` | active and archived are independent counters |

The e2e rewrite dropped all three. It replaced them with HTTP round-trips that
cannot assert them (a live `/counts` read has no way to pin the *constant*).

## Measured after restore

```
$ pytest packages/jcc_core/tests/ --collect-only -q
860 tests collected in 2.29s

$ pytest packages/jcc_core/tests/test_domain_counts_v1_3.py \
           packages/jcc_core/tests/test_deliverables_v1_3.py -q
22 passed in 55.89s

$ pytest packages/ tests/ --collect-only -q
1261 tests collected in 3.89s     # no basename collision between the trees
```

Env: `CODEBUDDY_SAFE_DELETE_ENABLED=0`, `PYTHONPATH=D:/jev/packages`,
`.venv/Scripts/python.exe -m pytest -p no:cacheprovider`.

## Also found: the copy reintroduced a drive literal

The copied `test_domain_counts_v1_3.py` seeded a project with
`root_path=r"F:\\jev"` — on line 63, replacing the e2e original's
`r"C:\some\checkout"`. That is the fifth `F:\jev` incident class, in a file
whose own committed version never had it. The restored version constructs
`DomainStore` directly and does not need a path literal at all.

## NOT DONE / out of scope — needs an owner

`tests/e2e/test_domain_counts_v1_3.py` has **3 failing tests** on current HEAD,
and this is a *separate* regression from RISK-1's write-side validation
(`590ecf3`), not from the collection bug:

```
FAILED tests/e2e/test_domain_counts_v1_3.py::test_counts_matches_the_real_domain
FAILED tests/e2e/test_domain_counts_v1_3.py::test_a_truncated_list_is_DETECTABLE
FAILED tests/e2e/test_domain_counts_v1_3.py::test_counts_reports_archived_projects_separately
E   AssertionError: (422, {'detail': {'code': 'PROJECT_ROOT_OUT_OF_SCOPE', ...
```

The test POSTs `root_path="C:\some\checkout"`, which RISK-1 now correctly
refuses (it is outside `effective_allowed_roots()`, which derives
`DEFAULT_PROJECT_SCOPE_BASE` when the e2e config sets no `launcher` section —
`tests/e2e/conftest.py::_write_config` writes none).

**The guard is right; the test's fixture is stale.** The fix is to give the e2e
config an explicit in-scope `launcher.project_scope_base` (a tmp dir) and seed
projects under it — not to weaken the validator. `tests/e2e/**` was not in my
assignment, so I did not touch it.

`tests/e2e/test_deliverables_v1_3.py` is unaffected: **11 passed** (it creates
projects with no `root_path`).

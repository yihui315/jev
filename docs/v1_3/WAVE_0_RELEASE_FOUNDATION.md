# JCC v1.3 Wave 0 — Release Foundation

**Owner:** Orion (architecture / provenance)
**Status:** LANDED — non-self-referential manifest model in place, 19 regression tests green
**Freeze anchor:** `8f0447a` (product freeze for v1.3 release foundation)

## 1. The defect this retires

The v1.2 manifest (`tools/emit_v1_2_release_manifest.py`) set
`release_sha = git rev-parse HEAD` and its `--check` re-ran `build()` — which
reads the **current** HEAD — and compared the result to the stored document.
This is a self-referential invariant:

> the moment any commit lands after the manifest is written, HEAD moves,
> `release_sha` changes, and `--check` can never be CLEAN again — **even if the
> commit only adds evidence**.

The only way to obtain a CLEAN verdict was the ritual
`checkout tag → --emit (do NOT commit) → --check CLEAN → discard → checkout -`.
That proves nothing about the tagged tree. It proves that a document the checker
**just wrote** matches the state the checker **just read**.

## 2. The new model — three anchors, one direction of travel

| Anchor | Meaning | Mutability |
|--------|---------|------------|
| `PRODUCT_FREEZE_SHA` | commit at which product bytes were last allowed to change | **immutable** |
| `EVIDENCE_HEAD` | commit carrying the latest evidence / release metadata | advances freely |
| `TAG_HEAD` | commit the release tag points at | must descend from the freeze |

The manifest **certifies `PRODUCT_FREEZE_SHA`**. Product hashes are read with
`git cat-file blob <PRODUCT_FREEZE_SHA>:<path>` — i.e. **at the freeze commit,
not at HEAD**. Advancing HEAD therefore does not change the verdict.

### The check is directional, never equality-based

1. `PRODUCT_FREEZE_SHA` **is an ancestor of** `EVIDENCE_HEAD`.
2. Every product artifact's bytes **at the freeze commit** match the manifest.
3. Every path changed in `freeze..evidence` **and** `freeze..tag` is inside the
   release-metadata whitelist. **A single product-file diff is a hard FAIL.**
4. `TAG_HEAD` is on the freeze line (descendant, no product drift).

⇒ `git checkout <final-tag> && manifest --check` is **CLEAN by construction**.
The checker never writes the manifest, so the retired
re-emit → check → discard hand-wave is neither needed nor possible.

## 3. Path classification (fail-closed)

Every path is classified as **product** or **release-metadata**, and the default
is **product**:

- `RELEASE_METADATA_ALLOW`: `artifacts/release/**`, `artifacts/acceptance/**`,
  `docs/v1_3/**`, `docs/release/**`.
- `PRODUCT_DENY` (checked first, wins): `packages/**`, `apps/**`, `facades/**`,
  `scripts/**`, `config/**`, `fixtures/**`, `tools/**`, `integrations/**`,
  `adapters/**`, `policies/**`, `evals/**`, `tests/**`.
- Anything matching neither is **product**.

`*` does not cross `/`; `**` does.

## 4. Measured proof (this is the point of the wave)

| Step | Action | HEAD | `--check` |
|------|--------|------|-----------|
| a | emit manifest at freeze | `8f0447a` | CLEAN |
| b | **evidence-only** commit (manifest + sidecar) | `9654e10` | **CLEAN — no re-emit** |
| c | tag evidence head, re-check | `9654e10` | CLEAN |

Step (b) is the whole point: HEAD moved, and the check stayed CLEAN **without**
re-emitting the manifest. Under the v1.2 model (b) was structurally impossible.

## 5. Regression tests — `tools/tests/test_release_manifest_v1_3.py` (19 passed)

Each of T1–T7 is a falsifier that would pass on a broken implementation:

| id | claim |
|----|-------|
| T1 | tag checkout after evidence-only commits = CLEAN, no re-emit |
| T2 | product-code change after freeze = **FAIL** (committed + tagged variants) |
| T3 | freeze outside the evidence ancestry = **FAIL** |
| T4 | 40-hex git SHA-1 in a sha256 field is **refused** |
| T5 | five evidence-only commits = still CLEAN |
| T6 | product/evidence path classifier, incl. fail-closed default |
| T7 | the checker **never writes** the manifest (digest + mtime unchanged) |

Tests run against real throwaway git repositories created in a temp dir, so the
ancestry and diff logic is exercised for real, not mocked.

## 6. Files

- `tools/jcc_release_manifest.py` — engine (classifier, freeze-commit hashing,
  directional `check_manifest`; never writes).
- `tools/emit_v1_3_release_manifest.py` — v1.3 emitter/checker over the engine.
- `tools/tests/test_release_manifest_v1_3.py` — 19 regression tests.

## 7. Rollback point

`jcc-v1.3-wave0-rollback-before` → parent of the Wave 0 commit.

# JEV COMMAND CENTER v1.1 — Release Re-Baseline Record

**Classification:** RELEASE-BASELINE-OUTDATED correction (NOT a product failure).
**Reason recorded:** JCC v1.1 intentional product evolution.
**Author:** Orion (release owner), FINAL-BASELINE re-baseline 2026-09-28.
**Rule honored:** no gate threshold widened, no FAIL reclassified to DEGRADED, no fail-open.

## 1. Freeze pointers

| Role | SHA | Note |
|------|-----|------|
| Old release freeze (stale) | `5ab5e20d961db4c9807f578195ae8530dea2f7d9` | Predated the two v1.1 product moves |
| New v1.1 release freeze | `4925bb4e917c274c127878b58c2367b272310591` | Commit that re-anchored the A8 freeze note |
| Source HEAD (v1.1 product state) | `067c54a6ebd3b5a23d0cfefb6bba02a8f1290bf2` | Clean tree before re-baseline began |
| Rollback SHA | `5ab5e20d961db4c9807f578195ae8530dea2f7d9` | The prior freeze; `git revert`/reset target if needed |

## 2. Why PROJECT_COMPLETE was false (root cause)

`final_gate_run.py --full` reported `PROJECT_COMPLETE=false` with
`failed=[E-05,E-06,E-07,F-04,F-05,P-02]`. Every failing item traced to ONE root cause:
the three final-acceptance legs (E-05/E-06/E-07) and the release manifest (P-02) were
signed/computed against the **stale freeze `5ab5e20d`**, while the v1.1 product had
**legally moved** past it. `RELEASE_FREEZE` still pointed at `5ab5e20d`, and the
manifest was last regenerated at the old freeze. No product byte was wrong; the
*baseline* was outdated.

## 3. Change classification (FINAL-BASELINE-2)

`git diff --name-only 5ab5e20d..HEAD -- packages config apps facades scripts tools`
→ **82 paths, 0 UNKNOWN / 0 UNEXPLAINED**.

| Class | Count | Disposition |
|-------|-------|-------------|
| PRODUCT_CHANGE | 21 | v1.1 product evolution (new provider/control/supervisor modules + core.py/server.py moves). All expected. |
| TEST_CHANGE | 31 | New/updated test files. Evidence. |
| EVIDENCE_CHANGE (scripts) | 10 | Release/ops tooling. Evidence. |
| EVIDENCE_CHANGE (tooling) | 20 | Release/ops tooling. Evidence. |
| UNKNOWN | 0 | — (would have stopped the re-baseline) |

Of the 12 A8-pinned production files, **only 2 moved** since the old freeze; all 12
match the A8 `_BASELINE_HASHES` at current HEAD (recomputed and confirmed 12/12).

## 4. A8 pinned-file old → new sha256 (FINAL-BASELINE-1/3)

Only `core.py` and `server.py` changed between the old freeze and the new freeze.
The A8 pin already carries the new hashes; the manifest was merely never regenerated.

| Path | old (5ab5e20d) | new (HEAD / new freeze) | Why |
|------|----------------|--------------------------|-----|
| `packages/jcc_core/core.py` | `72fc0f99f2eef7510d54f556e1355eac7a4c0a7ac390737de149e21f9358c14e` | `93ee369fc5029363c20dfffd68eba4932c2c120057387fe1cd48c61ad5496aae` | §21 S-02c persistence-boundary redaction fix (`mask_secret_key_values()` before `scrub_value_secrets()`) + v1.1 provider/control integration. |
| `packages/jcc_core/server.py` | `9bca4bd4a499d1d658a9bd32a74127be6c7b3eaae6d5757e1f0681d5fb4b7cce` | `392059e698615ca92fe73b5b7228c0e30470ba2aeec43394b5c2b911efd4b90b` | §7D `/api/health` provider-mode contract (`provider_mode`/`provider_verified`/`provider_activation_state`/`provider_requested_mode`/`provider_is_real`) + API-backed v1.1 surfaces. |
| other 10 of 12 | unchanged | unchanged | — |

`A8_FROZEN_AT` was appended with a re-anchor note. `_BASELINE_HASHES` was **not**
modified (all 12 already matched current bytes); the A8 self-check still passes
(current bytes PASS, single-byte tamper FAIL).

## 5. Remediation applied (FINAL-BASELINE-4/5)

1. `RELEASE_FREEZE` in `artifacts/acceptance/final_gate_run.py` re-pointed to
   `4925bb4e…`.
2. `tools/generate_final_sre_go.py` `RELEASE_FREEZE` re-pointed in lockstep (the
   generator must agree with the gate on where a leg may be signed).
3. `scripts/emit_release_evidence.py` regenerated `artifacts/release/manifest.json`
   (current v1.1 hashes) + `manifest.previous.json` + `manifest.sha256` + EV11a
   sidecar. `--check` → **CLEAN / 25 items reconciled (exit 0)**.
4. E-05/E-06/E-07 legs re-signed at `4925bb4e…` (genuine new signatures, not copies;
   `reanchored_at` + `reanchor` record the move; verdicts unchanged: GO WITH
   CONDITIONS / GO / GO).
5. `tools/v1_1_security_gate.py` (§25) re-run at HEAD → **GO, high=0, 9/9**.

## 6. States (unchanged, legal)

`REAL_JEV=PENDING_EXTERNAL_ACTIVATION`, `MOCK=ACTIVE`, `REPLAY/DISABLED=VERIFIED`,
`Actuation=False`, `Live Shadow=PENDING_LIVE_SHADOW_GATE`. GREEN (127.0.0.1:4778,
frozen v1.0.1) was **not** stopped/restarted/killed and its DB was **not** written.

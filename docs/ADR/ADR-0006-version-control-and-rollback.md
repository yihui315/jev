# ADR-0006 — Version Control and Rollback Strategy

- **Status:** Proposed
- **Date:** 2026-09-24
- **Owner:** Atlas (jcc-protocol-architect)
- **Supersedes:** none
- **Superseded by:** none

## Context

The core contract requires that **all phases have rollback evidence** (#10), and the P0 gate requires a documented rollback strategy. The PHASE A audit established (**VERIFIED**) that:
- `F:\jev` has **no VCS** (no `.git` at root).
- `F:\jev\jev-cascade` also has **no nested `.git`**.

With no pre-existing VCS, rollback must be **artifact-snapshot based** by default, with an optional local git for change tracking that explicitly protects the legacy reference.

> Team-lead guidance for this ADR notes the new `git init` must "not break the nested jev-cascade .git." The VERIFIED current state shows no such nested `.git` exists; the design below honors that intent (and any future nested git) via `.gitignore` exclusion.

## Decision

1. **Artifact-snapshot baseline (primary rollback mechanism).** Each phase emits immutable, hashed snapshots under `F:\jev\artifacts\{reports,traces,acceptance}`. Rollback = restore the prior snapshot + controlled restart. This satisfies "rollback evidence" without any external VCS.
2. **Local VCS (secondary, change tracking).** `git init` at `F:\jev` root (NEW), with a `.gitignore` that **EXCLUDES**:
   - `jev-cascade/` (legacy reference — never tracked),
   - `.workbuddy/`, `_bootstrap/`,
   - `node_modules/`, `runtime/`, `*.db`, `artifacts/`, `build/`, `dist/`, `*.log`,
   - secret/credential files: `.env`, `.env.*`, `*.key`, `*.pem`, `*.pfx`, `*.p12`, `*.keystore`, `*.jks`, `credentials*`, `auth.json`, `.sandbox-secrets`, `id_rsa`, `id_ed25519`.
   *Rationale:* artifact-snapshot/trace/acceptance under `artifacts/` are the PRIMARY rollback mechanism and generated output — the secondary VCS tracks source + ADRs only, so `artifacts/` is excluded.
   **No remote, NO push** (core contract: no `git push` in P0/P1).
3. **Nested-git safety.** Current VERIFIED state: no `.git` inside `jev-cascade`. The `/jev-cascade/` ignore rule ensures git never scans it. If a nested `.git` is introduced in `jev-cascade` later, the parent repo naturally treats it as a separate repository and will not corrupt it; the ignore rule also prevents accidental `git add`.
4. **Stop / restart / rollback guarding.** A `JCC-marker` file + PID lock under `F:\jev\runtime\` prevents concurrent/orphan runs and enables clean rollback to the last-known-good snapshot.

## Consequences

- **Positive:** Rollback evidence satisfied without external infrastructure; local git gives phase-gated change history; legacy reference fully insulated.
- **Negative / cost:** Snapshot discipline must be enforced (Harbor acceptance gate); local git is non-shared, so history lives only on this machine.
- **Constraint preserved:** No `git push`; `jev-cascade` untouched; rollback evidence present for every phase.

## Verification labels

- No VCS at `F:\jev` root: **VERIFIED** (PHASE A audit).
- No nested `.git` in `jev-cascade`: **VERIFIED** (PHASE A audit).
- "Do not break nested jev-cascade .git" intent: honored via `.gitignore` exclusion + natural git nested-repo handling.

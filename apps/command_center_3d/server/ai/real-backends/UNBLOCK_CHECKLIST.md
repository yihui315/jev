# JEV — External-Authorization Unblock Checklist

This document maps every **external-authorization blocker** that currently prevents
a production security PASS / real trading / real AI-cost accounting. Until each item
is satisfied, the corresponding phase stays at **MOCK-ONLY** and must NOT be claimed
as production-ready.

> Status legend: 🔴 BLOCKED (not provided) · 🟡 PARTIAL · 🟢 READY
> All items are currently 🔴 unless the main provides credentials / budget / access.

---

## 1. Real IdP (Phase B — C2 per-user identity + MFA) 🔴

**Why:** `server/ai/phase-b/identity.mjs` is a Mock HMAC IdP. Production needs a real
identity provider with real MFA/AMR so a server-trusted actor cannot be forged.

**Action for main:** provide a real OIDC/SAML IdP and set:

| Env var | Meaning |
|---|---|
| `REAL_IDP_ISSUER_URL` | IdP issuer (e.g. `https://your-tenant.auth0.com`) |
| `REAL_IDP_JWKS_URL` | JWKS endpoint for signature verification |
| `REAL_IDP_AUDIENCE` | expected `aud` claim |
| `REAL_IDP_REQUIRED_AMR` | comma list, default `mfa` |

**Drop-in adapter:** `server/ai/real-backends/real-idp.mjs` (`RealIdP`). It is
FAIL-CLOSED: unconfigured ⇒ throws `NotConfiguredError`; configured ⇒ `verifyToken()`
performs live JWKS fetch + RS256/ES256 signature check + iss/aud/exp + MFA amr
assertion. `buildTrustedIdentity()` returns the same `{ ok, actor, claims }` shape as
the mock (async). No code change to `phase-b/identity.mjs` is needed — swap the
import.

**Acceptance (post-unblock):** Aegis must re-run against the real IdP; a forged /
expired / non-MFA token MUST be denied; a valid MFA token MUST yield a trusted actor.

---

## 2. Real Cloudflare D1 (Phase B — D1 cross-instance ledger) 🔴

**Why:** `server/ai/phase-b/ledger.mjs` simulates D1 with an in-memory `Map`.
Production needs a real, durable, UNIQUE-constrained D1 so nonce consumption and
revocations survive restarts and are consistent across worker instances.

**Action for main:** create a D1 instance and set:

| Env var | Meaning |
|---|---|
| `REAL_D1_ACCOUNT_ID` | Cloudflare account id |
| `REAL_D1_DATABASE_ID` | D1 database id |
| `REAL_D1_API_TOKEN` | D1 API token (scoped, locked-down) |

**Schema to apply (idempotent):** see `REAL_D1_SCHEMA` export in
`server/ai/real-backends/real-d1.mjs` — creates `auth_nonces` (PK UNIQUE nonce),
`auth_revocations` (PK UNIQUE grant_id), `audit_anchors` (autoincrement seq).

**Drop-in adapter:** `RealD1Ledger` — atomic `INSERT OR IGNORE`; `changes === 0` ⇒
`REPLAY`. The contract test proves this logic against a SQL-semantics fake.

**Acceptance (post-unblock):** F-19 recovery drill on the real D1; 200-way concurrent
distinct-nonce consume all succeed; cross-instance replay of the same nonce denied.

---

## 3. Real external WORM anchor (Phase B — F-19 audit anchor) 🔴

**Why:** `server/ai/phase-b/audit-anchor.mjs` is an in-process append-only mock.
Production needs an EXTERNAL write-once store so the audit chain tip is independently
verifiable and tamper-evident.

**Action for main:** provision an external WORM (RFC3161 timestamping service,
object-lock cold store, or a dedicated append-only ledger) and set:

| Env var | Meaning |
|---|---|
| `REAL_WORM_ENDPOINT` | WORM service endpoint |
| `REAL_WORM_API_KEY` | WORM service API key |

**Drop-in adapter:** `RealWormAnchor` — `anchor()` appends; `verifyOnStartup()`
compares recomputed tip to last external anchor, mismatch ⇒ fail-closed.

**Acceptance (post-unblock):** startup recovery against the real anchor; tampered /
rolled-back local chain MUST fail closed.

---

## 4. Real Provider models (Phase C — provider gateway) 🔴

**Why:** `server/ai/provider-gateway/` is fully mocked (`connectivity:false`,
model targets `UNVERIFIED`). Real model performance is **NOT MEASURED**.

**Action for main:** provide provider API keys / budget and flip `connected=true`
per provider. The gateway already isolates provider calls behind a capability policy;
no architectural change required.

**Acceptance (post-unblock):** real latency/error/cost measured over ≥100 calls;
comparison matrix computed on real outputs, not mock labels.

---

## 5. Real trading venue + market data (Phase D — trade loop) 🔴

**Why:** `scripts/tests/phase-d/phase-d-verify.mjs` is all mock. Real matching /
latency / slippage / fill is **NOT MEASURED**.

**Action for main:** provide a broker/exchange sandbox (paper API) + market-data
feed. Keep `ENABLE_REAL_TRADING=false` / `allowBuySell=false` until Phase E/F
Supervisor gates pass.

**Acceptance (post-unblock):** Paper (not live) fills verified; event ledger
idempotency + replay validated against the real venue's ack stream.

---

## 6. Real CI Run ID (Phase A — production gate evidence) 🔴

**Why:** The 215-case gate was run locally; the authoritative `real GitHub Actions
Run ID` is `NOT_RUN`. A production PASS requires CI PASS on a real run.

**Action for main:** run the gate in CI and record the run id in
`reports/evidence/v16/independent_verify.json`.

---

## 7. Paid API budget (Phase G — commercial cost accounting) 🔴

**Why:** Real AI cost must be measured for the net-profit verdict.

**Action for main:** allocate a budget + metering; the Supervisor persistence layer
(`server/ai/supervisor/persistence.mjs`) already records per-run AI cost slots.

---

## Aggregate gate (Phase E/F Supervisor)

`server/ai/supervisor/gate.mjs` + `realBackendsAvailable()` require ALL of the above
(or at minimum #1–#3 + #6) before any continuous run (Shadow 7d / Paper 30d) may
start. With default/empty config the Supervisor returns `started:false` with **9
blocking reasons** and starts NO timers. 🔴 until unblocked.

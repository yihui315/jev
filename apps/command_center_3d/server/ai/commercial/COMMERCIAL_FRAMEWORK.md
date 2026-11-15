# JEV V2.0 Phase G — Commercial Framework (Skeleton, Fail-Closed)

> **STATUS: GATED. NO commercial verdict is possible today.** This module exists to
> make the commercial GO/CONDITIONAL GO/NO-GO decision *mechanically honest* — it
> refuses to emit any verdict on non-real data, so a passing test suite or code
> completion can never be mistaken for a profit proof.

## 1. Net-profit formula (single source of truth)

```
net = grossTradingPnl - fees - slippage - aiCost - opsCost
```

Implemented in `net-profit.mjs::computeNetProfit`. **Fail-closed**: throws
`REFUSES to compute on non-real data` unless `dataSource === "REAL"`. Synthetic,
mock, or fixture figures are never netted into a commercial claim.

## 2. Verdict gates (`verdict.mjs::evaluateCommercialVerdict`)

| Gate | Requirement | Failure verdict |
|---|---|---|
| Data is REAL | `dataSource === "REAL"` | **NO-GO** (hard, `DATA_NOT_REAL`) |
| Valid samples | `validSamples >= 100` | NO-GO (`INSUFFICIENT_VALID_SAMPLES`) |
| Out-of-sample | `outOfSampleSamples >= 30` | NO-GO (`INSUFFICIENT_OUT_OF_SAMPLE`) |
| AI cost measured | `aiCostMeasured === true` | NO-GO (`AI_COST_NOT_MEASURED`) |
| Risk control automated | `riskControlAutomated === true` | NO-GO (`RISK_CONTROL_NOT_AUTOMATED`) |
| Independent audit signed | `independentAuditSigned === true` | **CONDITIONAL GO** (`INDEPENDENT_AUDIT_PENDING`) |

- The first four gates are **proof-fundamentals**: missing any of them ⇒ `NO-GO`.
- If all proof-fundamentals pass but the independent audit is unsigned ⇒
  `CONDITIONAL GO`.
- Only when *all six* pass ⇒ `GO`.

Verdict space is exactly `{GO, CONDITIONAL GO, NO-GO}` — no other value is emitted.

## 3. Current honest state (2026-11-13)

| Requirement | Status |
|---|---|
| Real measured trading PnL | **NOT MEASURED** (B/C/D are MOCK-ONLY; E/F not yet run) |
| ≥100 valid samples | 0 |
| ≥30 out-of-sample samples | 0 |
| Real AI cost accounting | **NOT MEASURED** |
| Automated risk control (production) | mock-only, not production-ratified |
| Independent audit sign-off | Pending (depends on real data) |

**Current verdict: `NO-GO` (DATA_NOT_REAL).** No part of this framework has been
fed real data. The 10 commercial tests pass against *real-shaped but explicitly
labeled* inputs — that proves the machinery, not any profitability.

## 4. What unblocks Phase G

Per `UNBLOCK_CHECKLIST.md`, Phase G becomes reachable only after:
1. B/C/D promoted past MOCK via real IdP / Cloudflare D1 / external WORM / paid-API
   budget (real-backends adapters are drop-in, fail-closed).
2. Phase E (7-day Shadow) + Phase F (30-day Paper) run continuously on **real
   market data** for **≥37 real calendar days** (uncompressible), with real AI
   cost metered per run.
3. A real ledger of ≥100 valid samples (≥30 out-of-sample) is produced and an
   independent auditor signs off.

Until then this module is a **contract**, not a result.

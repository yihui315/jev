# DAY1_INDEPENDENT_QA.md — Independent QA (DAY-1.2)

## Checklist
- [x] Real AI inference verified (MiniMax M3 HTTP 200, choices+usage) — Provider connectivity evidence.
- [x] API envelope parsed from choices[0].message.content (not outer envelope).
- [x] Thinking block stripped and distinguished from final decision.
- [x] Truncated / non-JSON responses flagged INVALID_RESPONSE (no false BUY/SELL).
- [x] HOLD/BUY/SELL originate ONLY from model output.
- [x] Every model decision passes server risk gate BEFORE reaching frozen engine.
- [x] Every fill produced by frozen Paper Engine (applyFill/walkBook), not AI self-filled.
- [x] DAY-1.2 limits enforced: ≤200 notional, ≤6 orders, ≤20% exposure, 1%/2% breakers.
- [x] No real funds; ENABLE_REAL_TRADING false.
- [x] Synthetic feed explicitly UNKNOWN; not presented as live.
- [x] No key output; only last4 (sk-cp-…PZow).
- [ ] LIVE market path (edge reachability) — BLOCKED in sandbox; SHADOW only.

## Findings / risks
- Market data is synthetic; P&L is NOT a verified net profit (per spec, withheld).
- Proxy blocks api.binance.us (000/502); need edge or egress host for LIVE.
- Cost metering is an ESTIMATE (assumed rate); real USD not reliably metered.

# DAY1_REAL_AI_PAPER_REPORT.md — Real-AI Paper Trading (DAY-1.2)

> Real AI inference: MiniMax M3 (verified CONNECTED). Fills: frozen Paper Engine (paper-server.mjs, read-only import). Market: SHADOW (synthetic, NOT real-market return).

## Run state: SHADOW (synthetic; NOT live)

## Real AI inference (MiniMax M3)
- Calls attempted: 40
- Successful (ok): 34
- Invalid responses (INVALID_RESPONSE / envelope): 6
- Thinking-tag present (distinguished & stripped): 37 calls
- Total tokens: 27017 (prompt 17497 / completion 9520)
- Est. API cost (ASSUMED rate, ESTIMATE only): $0.0330 / cap $5

## Action distribution (from REAL model output)
- BUY: 2  SELL: 1  HOLD: 31

## Server risk gate (DAY-1.2, before engine)
- Approved: 34  Rejected: 0
- Rejection breakdown: {}

## Paper orders & fills (frozen engine)
- Orders executed: 3 (cap 6)
- Fills: 3
- Engine rejections: 0 

## P&L (SHADOW / synthetic — NOT a verified real-market return)
- Final equity: 10001.34 USDT (start 10000)
- Total return: 0.01% (SYNTHETIC, recorded data)
- Max drawdown: 0.03%
- Completed round-trips: 1  Win rate: 100.0% (recomputed: only completed BUY→SELL)
- Avg realized per round-trip: 0.3683 USDT
- Total fees: 0.1764 USDT

## Win-rate correction (per主理人指令)
- Prior DAY-1 probe reported "100% win (3/3)" from 2 BUYs + 1 SELL. Corrected: only 1 round-trip(s) actually closed; the 2nd BUY remained open at run end (unrealized). Win rate here = 100.0% over 1 closed trade(s).

## Compare vs baselines
- vs HOLD (cash): 0.01% (this synthetic run) — NOT a market-validated comparison.
- vs rule strategy: not materially different on 40 synthetic cycles; see equity reconciliation.

## Audit trail (redacted)
- Provider key: sk-cp-…PZow (MINIMAX_API_KEY) — never printed.
- Paper Order IDs: O-f9210864-8239-4053-9cc1-4e7788b60efe, O-cb62dc01-a7f3-4e77-a707-1d3214b59ae3, O-e24f696e-af9b-4746-8971-344a009f00c3
- Git SHA: 7a0f9b8 (local; push blocked by proxy — CI not yet run)

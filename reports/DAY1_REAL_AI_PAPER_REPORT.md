# DAY1_REAL_AI_PAPER_REPORT.md — Real-AI Paper Trading (DAY-1.2)

> Real AI inference: MiniMax M3 (verified CONNECTED). Fills: frozen Paper Engine (paper-server.mjs, read-only import). Market: SHADOW (synthetic, NOT real-market return).

## Run state: SHADOW (synthetic; NOT live)

## Real AI inference (MiniMax M3)
- Calls attempted: 40
- Successful (ok): 39
- Invalid responses (INVALID_RESPONSE / envelope): 1
- Thinking-tag present (distinguished & stripped): 39 calls
- Total tokens: 26616 (prompt 18495 / completion 8121)
- Est. API cost (ASSUMED rate, ESTIMATE only): $0.0310 / cap $5

## Action distribution (from REAL model output)
- BUY: 3  SELL: 2  HOLD: 34

## Server risk gate (DAY-1.2, before engine)
- Approved: 39  Rejected: 0
- Rejection breakdown: {}

## Paper orders & fills (frozen engine)
- Orders executed: 5 (cap 6)
- Fills: 5
- Engine rejections: 0 

## P&L (SHADOW / synthetic — NOT a verified real-market return)
- Final equity: 10002.13 USDT (start 10000)
- Total return: 0.02% (SYNTHETIC, recorded data)
- Max drawdown: 0.05%
- Completed round-trips: 2  Win rate: 100.0% (recomputed: only completed BUY→SELL)
- Avg realized per round-trip: 0.5060 USDT
- Total fees: 0.3508 USDT

## Win-rate correction (per主理人指令)
- Prior DAY-1 probe reported "100% win (3/3)" from 2 BUYs + 1 SELL. Corrected: only 2 round-trip(s) actually closed; the 2nd BUY remained open at run end (unrealized). Win rate here = 100.0% over 2 closed trade(s).

## Compare vs baselines
- vs HOLD (cash): 0.02% (this synthetic run) — NOT a market-validated comparison.
- vs rule strategy: not materially different on 40 synthetic cycles; see equity reconciliation.

## Audit trail (redacted)
- Provider key: sk-cp-…PZow (MINIMAX_API_KEY) — never printed.
- Paper Order IDs: O-ba642805-86b2-4a3e-9e09-6c7c710c8bcd, O-3c46bdde-b6dd-4c8e-b1b1-132f43d944cc, O-9f777319-d6db-4b80-b935-83b46232a7a1, O-6bcff253-5333-43aa-a098-051e1d734c9a, O-1bc48db5-b86b-47c6-b437-e22ca4bd12b0
- Git SHA: 811f4fb7f2f0 (push blocked by proxy — CI not yet run)

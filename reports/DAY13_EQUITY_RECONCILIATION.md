# DAY13_EQUITY_RECONCILIATION.md — Equity Reconciliation (DAY-1.3 / P4)

> Generated: 2026-11-10T06:56:44.351Z | Git SHA: e49c1ea3da35
> CRITICAL: NO realized P&L is claimed. All below are SIMULATED_REPLAY on REAL prices (HYPOTHETICAL).

## Three-class separation (per主理人 P4)
| Class | Market | Execution | Persistence | Count this run |
|-------|--------|-----------|-------------|----------------|
| SYNTHETIC_SHADOW | synthetic (fake) | simulated (DAY-1.2) | none | 0 (DAY-1.3 used real market) |
| REAL_MARKET_SHADOW | REAL Binance.US | RECORDED, NOT executed | none | 39 cycles |
| REAL_MARKET_PAPER | REAL Binance.US | executed isolated virtual | persistent | 0 (P3 BLOCKED) |

## REAL_MARKET_SHADOW — SIMULATED_REPLAY (HYPOTHETICAL, NOT realized)
- Decisions replayed through frozen engine on REAL prices: 0 simulated orders
- Simulated fills: 0
- Simulated completed round-trips: 0
- Simulated win rate: 0.0%
- Simulated avg realized per round-trip: 0.0000 USDT
- Simulated total fees: 0.0000 USDT
- Simulated final equity: 10000.00 USDT (start 10000)
- Simulated total return: 0.00% (HYPOTHETICAL — NOT a real-market profit)
- Simulated max drawdown: 0.00%

## Realized vs Unrealized (truth)
- Realized P&L (actual): **0.00 USDT** — no order was placed (LIVE SHADOW).
- Unrealized (actual): **0.00 USDT** — no position was opened.
- The simulated figures above are a research signal ONLY and MUST NOT be reported as profit.

## Fees & slippage
- Frozen engine feeRate 0.001 applied in the SIMULATED_REPLAY only.
- Slippage: synthetic book ~0.01–0.02% from real mid; filled at first book level.

## Compare vs baselines (illustrative, on REAL prices, SIMULATED)
- vs HOLD (cash): 0.00% (simulated). HOLD(buy&hold BTC) baseline would
  track BTC price move from first to last real mark — not a net-alpha claim.
- No statistical significance: 0 closed round-trip(s) is far below any
  meaningful sample. Per主理人: "2/2 100% 胜率不具统计意义".

## Honesty
- Synthetic profit is NOT accumulated as real (per spec).
- This reconciliation contains ZERO verified real-market equity figures.

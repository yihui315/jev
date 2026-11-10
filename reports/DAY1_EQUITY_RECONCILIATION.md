# DAY1_EQUITY_RECONCILIATION.md — Equity Reconciliation (DAY-1.2)

## Reconciliation: frozen-engine account vs harness bookkeeping
- Frozen engine final cash: 9900.98
- Frozen engine realized PnL: 1.01
- Frozen engine positions: {"BTCUSDT":{"qty":"0.00142000","avgPrice":"70443.19163380","reservedQty":"0"}}
- Harness final equity (cash + position*mark): 10002.13
- Frozen engine summary equity: 10002.13148506
- MATCH: YES

## Realized vs unrealized
- Realized PnL: 1.01 USDT
- Unrealized (open position): 1.12 USDT

## Fees & slippage (estimate)
- Total fees: 0.3508 USDT (frozen engine feeRate 0.001)
- Slippage: synthetic book ~0.01–0.02% from mid; filled at first book level.

## Synthetic vs real separation
- This reconciliation is on SYNTHETIC prices (SHADOW). It does NOT constitute a verified real-market equity figure.
- A separate LIVE reconciliation must be produced when Binance.US is reachable (edge or egress host).

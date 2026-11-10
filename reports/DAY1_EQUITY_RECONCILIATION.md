# DAY1_EQUITY_RECONCILIATION.md — Equity Reconciliation (DAY-1.2)

## Reconciliation: frozen-engine account vs harness bookkeeping
- Frozen engine final cash: 9925.12
- Frozen engine realized PnL: 0.37
- Frozen engine positions: {"BTCUSDT":{"qty":"0.00107000","avgPrice":"70323.15178505","reservedQty":"0"}}
- Harness final equity (cash + position*mark): 10001.34
- Frozen engine summary equity: 10001.34033634
- MATCH: YES

## Realized vs unrealized
- Realized PnL: 0.37 USDT
- Unrealized (open position): 0.97 USDT

## Fees & slippage (estimate)
- Total fees: 0.1764 USDT (frozen engine feeRate 0.001)
- Slippage: synthetic book ~0.01–0.02% from mid; filled at first book level.

## Synthetic vs real separation
- This reconciliation is on SYNTHETIC prices (SHADOW). It does NOT constitute a verified real-market equity figure.
- A separate LIVE reconciliation must be produced when Binance.US is reachable (edge or egress host).

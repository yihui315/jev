# DAY-1 SHADOW Probe — MiniMax M3 (single model)

> **DISCLOSURE**: MiniMax M3 inference is REAL. Market data is **RECORDED/SYNTHETIC** (live exchange egress blocked in this sandbox: coingecko/binance/okx = 000). This is NOT a live-market evaluation. No real funds; virtual 10,000 USDT paper account. NOT a three-model comparison — only MiniMax M3 is callable.

## TL;DR
- Model: **MiniMax M3** (providerId=minimax) — real calls, verified CONNECTED.
- Cycles simulated: 24. Trades executed: 3. Halted: no.
- Final equity: 10000.62 USDT (start 10000). Total return: 0.01% (SIMULATED, recorded data).
- Max drawdown: 0.02%. Win rate: 100.0%. Sharpe-proxy: 0.00.
- LLM cost: 15940 tokens (prompt 9954 / completion 5986); soft cap US$5 — not breached.

## DAY-1 Hard Limits Applied
| Limit | Value | Enforced |
|---|---|---|
| Virtual capital | 10000 USDT | yes (paper) |
| Max notional/trade | 200 USDT | yes (rejected out-of-range) |
| Max trades | 6 | yes |
| Daily-loss breaker | 1% | yes |
| Drawdown breaker | 2% | yes |
| Leverage | forbidden | yes |
| Short | forbidden | yes |
| Call budget | US$5 | yes (tracked) |

## BLOCKED providers (not evaluated)
- **qwen**: DASHSCOPE_API_KEY (sk-sp-…XxMA, the "阿里云百炼 CLI" key) returns 401 on DashScope — dead/invalid. (The working MiniMax key sk-cp-…PZow is wired ONLY to the minimax provider, not qwen.)
- **deepseek**: no DEEPSEEK_API_KEY
- **openrouter**: no OPENROUTER_API_KEY
- **openai**: egress blocked in sandbox (000)
- **ollama**: no local Ollama
- **Note (key attribution, re-verified)**: sk-sp-…XxMA (the original "阿里云百炼 CLI" key) is dead for BOTH MiniMax and DashScope (HTTP 401). The only working real key in this environment is **sk-cp-…PZow** (MiniMax M3), wired as `MINIMAX_API_KEY` (and `DASHSCOPE_API_KEY_BACKUP`). The prior "sk-sp- works / sk-cp- dead" claim was inverted and is retracted.

## Decision Log (real MiniMax M3 output)
| cyc | price | action | exec | notional | conf | equity | callOk | reason |
|---|---|---|---|---|---|---|---|---|
| 0 | 68184.5 | HOLD | NONE | 1 | 0.2 | 10000 | true | No recent price data or momentum signal; insufficient edge. |
| 1 | 68448.57 | HOLD | NONE | 1 | 0.12 | 10000 | true | No reliable momentum signal or clear mean-reversion edge; insufficient direction |
| 2 | 68682.17 | BUY | BUY | 100 | 0.62 | 10000 | true | Recent prices show a steady upward breakout with BTC at 68682.17, supporting mod |
| 3 | 68358.68 | HOLD | NONE | 1 | 0.58 | 9999.53 | true | Existing long remains above the first recent price; the pullback is modest and m |
| 4 | 68639.87 | HOLD | NONE | 1 | 0.55 | 9999.94 | true | Mixed recent momentum and no reliable trend signal; retaining the existing BTC p |
| 5 | 68953.63 | HOLD | NONE | 1 | 0.58 | 10000.4 | true | Price is above the recent range with mild upward momentum, but signals are incom |
| 6 | 68793.5 | HOLD | NONE | 1 | 0.64 | 10000.16 | true | Positive momentum and recent higher highs support holding the existing long; no  |
| 7 | 69143.77 | HOLD | NONE | 1 | 0.62 | 10000.67 | true | Modest positive momentum, but no strong reversal or breakout edge to justify clo |
| 8 | 69145.86 | HOLD | NONE | 1 | 0.62 | 10000.68 | true | Positive momentum and recent higher highs support holding the existing long; no  |
| 9 | 69214.38 | HOLD | NONE | 1 | 0.76 | 10000.77 | true | Positive momentum and rising recent prices show no clear reversal signal; mainta |
| 10 | 68942.42 | HOLD | NONE | 1 | 0.64 | 10000.38 | true | Existing long remains open; positive momentum provides no clear reversal or prof |
| 11 | 69250.54 | HOLD | NONE | 1 | 0.75 | 10000.83 | true | Momentum is essentially flat, so there is no clear reversal or continuation edge |
| 12 | 69449.06 | HOLD | NONE | 1 | 0.62 | 10001.12 | true | Existing long remains aligned with modest positive momentum; no clear reversal s |
| 13 | 69765.7 | HOLD | NONE | 1 | 0.64 | 10001.58 | true | Existing long remains aligned with positive 6-step momentum and rising recent pr |
| 14 | 69699.56 | HOLD | NONE | 101.48 | 0.66 | 10001.48 | true | Positive momentum and rising recent closes support maintaining the existing long |
| 15 | 69364.21 | HOLD | NONE | 1 | 0.64 | 10000.99 | true | Positive momentum remains intact after a modest pullback from recent highs, so t |
| 16 | 68981.81 | SELL | SELL | 100.44 | 0.72 | 10000.44 | true | Price rejected the recent 69765.70 high and reversed toward the 68942 area, sign |
| 17 | 69213.82 | HOLD | NONE | 50 | 0.62 | 10000.44 | true | Mild negative momentum with no clear reversal trigger or sufficient mean-reversi |
| 18 | 69616.91 | BUY | BUY | 75 | 0.58 | 10000.44 | true | Price rebounded 0.58% from the recent low while overall momentum remains nearly  |
| 19 | 70006.06 | HOLD | NONE | 1 | 0.56 | 10000.86 | true | Mild negative momentum conflicts with the recent rebound toward 70006; no clear  |
| 20 | 69939.15 | HOLD | NONE | 1 | 0.62 | 10000.78 | true | Positive momentum offers no clear reversal signal, but the existing long positio |
| 21 | 69846.32 | HOLD | NONE | 1 | 0.64 | 10000.68 | true | Positive momentum and recovery support the existing long; no clear reversal or p |
| 22 | 69948.04 | HOLD | NONE | 1 | 0.62 | 10000.79 | true | Positive momentum remains, but the pullback from 70006 has not confirmed a rever |
| 23 | 69790.36 | HOLD | NONE | 1 | 0.64 | 10000.62 | true | Positive momentum remains intact with no clear reversal signal; maintain the exi |

## Honesty Notes
- Real-model performance claim: ONLY MiniMax M3 contributed real inference. The P&L is on RECORDED/SYNTHETIC prices, so it is NOT a market-validated result.
- To reach the original DAY-1 three-model comparison, supply DEEPSEEK_API_KEY + OPENROUTER_API_KEY (both proxy-reachable); openai egress is blocked here.
- Re-run on a host with market egress to replace the recorded sample with live quotes; the harness reads a market feed if reachable.
- Generated 2026-11-10T00:18:54.856Z by day1-shadow-minimax.mjs. Secret values not logged (last4 only where needed).
# JEV Cloudflare Pages — staging deployment (REAL Paper Trading backend)

This branch deploys the **real JEV Paper Trading backend** to Cloudflare Pages
Functions + D1. It is staging only: `ENABLE_REAL_TRADING=false` (red line),
preview-auth protected, and never connects to real-money trading.

Source: `feat/jev-cloudflare-staging`. The frozen Night Researcher paper engine is
vendored under `apps/command_center_3d/server/engine/_lib/` (verbatim, sha256
verified) and the 5 migrations under `server/engine/migrations/` are applied to
the `jev-paper-staging` D1 database. The Railway Docker path (`cloud/`,
`server/trading_api.mjs`) is preserved as an alternative deployment but is NOT
used by this Pages workflow.

GitHub Actions: `.github/workflows/jev-cloudflare-staging.yml`.

GitHub repository secrets required (Settings > Secrets and variables > Actions;
never place values in source code):
- `CLOUDFLARE_API_TOKEN` — least-privilege Pages write token
- `CLOUDFLARE_ACCOUNT_ID`
- `JEV_PREVIEW_USER` — nonempty ASCII username
- `JEV_PREVIEW_PASSWORD` — >=20 random characters
- `JEV_PAPER_LEDGER_SECRET` — >=20 random characters (ledger signing secret)

The workflow fails closed when any credential is missing. It uses Direct Upload,
reuses the existing Pages project `jev-cloud-staging` (creating it only if 404;
production branch = `feat/jev-cloudflare-staging`), sets production secrets
(`JEV_PREVIEW_USER`, `JEV_PREVIEW_PASSWORD`, `PAPER_LEDGER_SECRET`,
`ENABLE_REAL_TRADING=false`), uploads the Vite `dist` directory plus Cloudflare
Pages Functions, and verifies:
1. anonymous `/trading.html` => 401
2. authenticated `/trading.html` => 200
3. authenticated `/api/jev/paper/account` => 200 (real backend live on D1)

Honesty notes (no fabrication):
- **Real market data is restored via a gateway transport adapter.** The frozen
  engine requests `data-api.binance.vision` (measured 403 from the CF edge;
  Binance global hosts return 451). The gateway injects the engine's NATIVE
  `env.__fetch` hook (`dbFetch(env) => env.__fetch || fetch`) to repoint those
  requests to `api.binance.us` (measured 200). Engine source and D1 ledger are
  byte-for-byte unmodified; upstream response bodies pass through unchanged —
  real prices, no cache, no synthesis, no re-scoring.
- `api.binance.us` is **Binance.US SPOT**, a DIFFERENT venue from the Binance.com
  global book. Only `BTCUSDT` / `ETHUSDT` exist there. `ORDIUSDT` /
  `1000SATSUSDT` are filtered from the multi-symbol ticker query and their
  orders are **honestly rejected** (`MARKET_HTTP_400`) — never faked.
- `paper_equity_snapshots` has **0 rows (NO_DATA)**: the frozen engine does not
  write that table and the V45 equity collector is not deployed here. Real-time
  equity IS computed from verified marks in `paper/account` (`markQuality:
  verified`). Do not confuse the two. See the acceptance report for the blocker.
- `engine_modified=false`, `real_money=false`, `ENABLE_REAL_TRADING=false`.
- Account isolation (including audit counts), ledger persistence and restart
  recovery are verified against `jev-paper-staging` D1. See
  `JEV_CLOUDFLARE_STAGING_ACCEPTANCE.md`.

Cloudflare Pages is public by default. This staging uses a fail-closed Pages
middleware (`functions/_middleware.js`) that requires Basic authentication over
HTTPS. For wider access, configure Cloudflare Access before involving external
testers.

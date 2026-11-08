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
- Market marks are currently UNAVAILABLE — Cloudflare edge egress to
  `data-api.binance.vision` is HTTP 403 (Binance blocks cloud egress IPs). MARKET
  orders are honestly rejected when no mark is available; no synthetic fills.
- `engine_modified=false`, `real_money=false` are asserted by the gateway.
- Account isolation, ledger persistence and restart recovery are verified against
  the `jev-paper-staging` D1 database. See `JEV_CLOUDFLARE_STAGING_ACCEPTANCE.md`.

Cloudflare Pages is public by default. This staging uses a fail-closed Pages
middleware (`functions/_middleware.js`) that requires Basic authentication over
HTTPS. For wider access, configure Cloudflare Access before involving external
testers.

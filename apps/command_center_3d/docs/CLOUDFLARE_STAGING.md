# JEV Cloudflare Pages — staging deployment

This branch is an isolated **UI-only** Cloudflare Pages preview. It is NOT the full
Paper Trading backend. It does not connect to real-money trading.

Source: `feat/jev-cloudflare-staging`, based on immutable JEV Staging revision
`65d48008ed120513b8a94f1a58fee8339912df53`.
The Railway Docker / frozen engine files are preserved but **not deployed** to Pages.

GitHub Actions: `.github/workflows/jev-cloudflare-staging.yml`.

GitHub repository secrets required on the trusted branch (configure in GitHub
Settings > Secrets and variables > Actions; never place values in source code):
- `CLOUDFLARE_API_TOKEN` — least-privilege Pages write token
- `CLOUDFLARE_ACCOUNT_ID`
- `JEV_PREVIEW_USER` — nonempty ASCII username
- `JEV_PREVIEW_PASSWORD` — >=20 random characters

The workflow fails closed when any credential is missing. It uses Direct Upload,
creates a project named `jev-trading-desk-staging-yihui315` if not present,
sets Cloudflare **preview** secrets, uploads the Vite `dist` directory plus
Cloudflare Pages Functions, and verifies:
1. anonymous `/trading.html` => 401
2. authenticated `/trading.html` => 200
3. authenticated `/api/jev/paper/account` => 503 BLOCKED

The frontend always shows a prominent UI ONLY / no Paper API banner.
Real service integration requires a separate D1 staging database, verified
Workers/Pages Functions port of the frozen Night Researcher paper engine,
migration validation, session isolation and live order E2E. Never point at
Night Researcher production D1 or fake fills or AI inference.

Cloudflare Pages is public by default. This preview uses a fail-closed Pages
middleware that requires Basic authentication over HTTPS. For wider access,
configure Cloudflare Access before involving external testers.

Do not merge into release/master as part of preview deployment.

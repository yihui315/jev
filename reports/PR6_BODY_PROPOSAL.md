# PR #6 — 建议描述文案（Night Sync 2026-11-10）

> 状态：本环境无法直连 `api.github.com`（代理仅放行 `github.com` git smart-HTTP），且 GitHub 集成令牌为只读（MCP `update_pull_request` 返回 403）。故 PR #6 的**实时描述**未能自动更新；以下文案已就绪，待具备 `api.github.com` 写权限 / 可写令牌时粘贴至 PR #6 描述（保持 Draft、不改 Title）。
>
> 权威夜间交付物 `V16_OVERNIGHT_FINAL_REPORT.md` 与 `V16_NIGHT_TASK_STATUS.json` 已提交并推送至 `feature/v16-market-provenance-hardening`，PR #6 的文件 diff 已包含，等价于证据已在 PR 中。

---

## JEV V1.6 — Dual Track: Market Provenance Hardening + AI Paper Trading

**DRAFT — do not merge.** Base: `release/jcc-v1.3-final` (V1.5 addendum). PR #5 untouched. PR #6 HEAD: `b102ca7`.

### Track A — Market Provenance Hardening (VERIFIED, independent QA GO)
- Migration 0001 (byte-identical baseline) + 0002 (8 provenance columns, CHECK, UNKNOWN/HISTORY_UNPROVEN backfill, 35 cols).
- market-guard 7+1 fail-closed; QA2-D1 fix: unknown network context never yields VERIFIED.
- V1.5 frozen suites 8/8, 95 assertions, byte-identical.
- V1.6 new suites 8/8, 104 assertions (US+Global mixing rejected; sentinel 9.9e13 cannot pollute US peak; fault injection; mutation check).
- Isolated cloud E2E: 10 cycles, 34 snapshots, 24/24 PASS; replay 10/10.
- Independent QA phases 1+2 (GO WITH CONDITIONS, real fail-open found) + D1-fix delta PASS => overall GO.

### Track B — AI Paper Trading
- server/ai/: unified provider adapter (openai / openai-compatible / ollama, SSRF guard), decision schema v1, independent risk gate, decision store + attribution, deterministic mock.
- 0006_ai_decisions.sql (ai_decisions / decision_order_links / ai_market_contexts), outside engine migrations.
- Gateway /api/jev/ai/* behind ENABLE_REAL_TRADING red line + Basic Auth (red-line line numbers unchanged).
- **Mock Contract Tests 120/120 PASS, network_calls=0** (upgraded 83 -> 120; now includes real frozen-engine E2E, 37 checks). **Real Provider BLOCKED — no AI credentials.**
- Strategy benchmark honestly NO_DATA (mock never counts as performance).

### Night Sync 2026-11-10 — P0 / P1 / P2
- **SHA**: head `b102ca7`; last CI-verified `bc2a5cb`; core engine-E2E commit `7dbc2f8`. Branch fast-forward pushed (no force, no Release, PR stays Draft).
- **CI Run IDs (`bc2a5cb`, all SUCCESS)**:
  - `v16-market-provenance.yml`: PR `42712800491` + push `42712789106` => V1.5 8/8/95, V1.6 8/8/104, AI Mock 120/120 (network_calls=0), frozen-engine integrity + sensitive-string scan 0 hits.
  - `ci.yml` `42712789223`: Typecheck/Build/Unit SUCCESS; Real Binance probe (non-blocking) SUCCESS.
  - `Cloud Staging Docker Runtime Gate` `42712800228`: smoke SUCCESS.
- **Mock results**: 120/120 (incl 37 real-engine E2E), network_calls=0, exit 0.
- **Real Provider**: BLOCKED. P2 audit — runtime.mjs 6 secretRefs (OPENAI/MINIMAX/DASHSCOPE/DEEPSEEK/OPENROUTER/OLLAMA_API_KEY) ALL NOT SET; local Ollama unreachable; platform tokens + agent LLM excluded (ChatGPT-login != OpenAI API Key). No real inference; no fabricated data.
- **Orders-Fills (offline E2E)**: engine produced real order/fill/link — BUY market fill, SELL-after-BUY partial + realized PnL, LIMIT fill-on-tick, LIMIT cancel, idempotent re-submit, sell-no-position reject (409 + CN code), HOLD not executable, non-whitelist/stale-market rejects. 37/37 PASS. **Offline / mock-AI + synthetic market; NOT real funds.**
- **Market data**: synthetic for E2E (`synthetic_fetch_calls=44`, `REAL_FETCH_ATTEMPTS=0` fail-closed); CI ran non-blocking Real Binance probe (success, probe-only).
- **D1 & Equity**: commitState reads `changes===1` via D1 shim; paper_equity_snapshots DDL frozen/byte-identical; equity-snapshot path validated.
- **Unresolved BLOCKED**: Real Provider (no creds); PHASE_8 Track-B independent QA cross-review; PHASE_7 frontend; PHASE_10 long-trading prep; strategy benchmark NO_DATA.
- **Next phase**: Overall V1.6 GO NOT declared — gated by Real Provider BLOCKED + Track-B QA cross-review. Track A may enter Staging-review discussion.

### Gates (unchanged)
- Frozen engine 15 files sha `aec5d1b7...` untouched; paper_equity_snapshots DDL frozen.
- ENABLE_REAL_TRADING=false; Staging D1 NO-GO; production/auto-trading NO-GO; PR #5 untouched; no force push.

**Draft 仅供审阅，请勿合并。**

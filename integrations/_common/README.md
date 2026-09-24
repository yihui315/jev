# JCC agent integration — shared transport (`integrations/_common`)

Shared, shell-agnostic transport used by all four agent installers. Nothing here
writes to any agent config; installers do that (see each `integrations/<agent>/`).

## Components

| File | Role |
| --- | --- |
| `jcc_emit.py` | Reads a native agent event → adapter `normalize()` → AEP envelope → delivers it (in-process / loopback HTTP `/api/assess` / disk spool). Always exits 0 so a JCC outage never blocks the agent. |
| `jcc_receiver.py` | Hardened **inbound** webhook receiver (Hermes / any signed sender): HMAC-SHA256 + timestamp window + `delivery_id` replay cache + body size limit + per-source session isolation. |
| `jcc_settings.py` | Python config-merge engine for JSON settings hooks (WorkBuddy / Claude): timestamped backup, parse-validate, **preserve user hooks**, JCC-artifact marker set (`jcc_emit.py`, `jcc-claude-hook.cmd`, `jcc-codex-notify`, `jcc_receiver.py` — see `JCC_ARTIFACTS`), idempotent, byte-safe reversible (newlines never translated). |

## Delivery paths (`jcc_emit`)

1. **in-process** — `emit_event(agent, raw, engine=...)` (used by the Hermes hook; no network).
2. **loopback HTTP** — `POST http://127.0.0.1:4778/api/assess` with `X-JCC-Token`
   (`JCC_CONTROL_TOKEN`). Token-gated; loopback only.
3. **spool** — `~/.jcc/spool/<agent>.jsonl` on failure. *Unidentifiable* events
   (no `session_id`) are spooled with `kind:"unidentifiable"` and are **never ingested**.

## Environment

| Var | Default | Used by |
| --- | --- | --- |
| `JCC_ROOT` | `F:/jev` | all |
| `JCC_CONTROL_TOKEN` | *(unset)* | emit (HTTP) |
| `JCC_CORE_URL` | `http://127.0.0.1:4778` | emit (HTTP) |
| `JCC_SPOOL_DIR` | `~/.jcc/spool` | emit |
| `JCC_MODE` | `loopback` | Hermes handler (`signed` for HMAC webhook) |
| `JCC_HOOKS_URL` / `JCC_WEBHOOK_SECRET` | *(unset)* | Hermes signed mode / receiver |

## Receiver contract (Hermes inbound)

Headers: `X-JCC-Agent`, `X-JCC-Timestamp`, `X-JCC-Delivery-Id`, `X-JCC-Signature: sha256=<hex>`.
Signature = `HMAC_SHA256(secret, f"{timestamp}.{delivery_id}.{agent}." + raw_body)`.

> **Trust boundary (important):** all four agents share **one** webhook secret
> (`JCC_WEBHOOK_SECRET`). The HMAC therefore proves only that a sender knows the
> shared secret — it does **not** make per-agent identity a cryptographic
> boundary: the `X-JCC-Agent` header is self-declared by the sender. What bounds
> cross-agent influence is the per-source **session isolation** (`session_isolation`
> rejection) plus the hash-chained audit tape, not the signature. Treat agent
> identity as untrusted until per-agent keys (or asymmetric signing) are
> introduced.

Rejections (fail-closed, reason only — never the body/secret): `payload_too_large`,
`missing_header`, `timestamp_out_of_window`, `bad_signature`, `replay`, `bad_json`,
`unsupported_agent`, `session_isolation`, `unidentifiable`.

## Manual run

```powershell
# normalize a native event and print the AEP envelope (no side effects)
F:\jev\.venv\Scripts\python.exe F:\jev\integrations\_common\jcc_emit.py --agent claude --stdin-json <event.json> --dry-run
```

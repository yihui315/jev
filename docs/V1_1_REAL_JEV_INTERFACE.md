# Real Jev Interface — v1.1 verification record

> **GENERATED FILE — do not edit by hand.** Produced by
> `tools/v1_1_emit_real_jev_verification.py`; every fact below is read from
> `packages/jcc_core/providers/real.py`. Edit the card, re-run the generator,
> and commit both together. `--check` fails if this file and the code disagree.

## 1. Verdict

| | |
|---|---|
| Provider | `real` |
| Provider mode | `REAL` |
| Provider verified | `False` |
| Activation state | **`PENDING_EXTERNAL_ACTIVATION`** |
| Real-Jev decision | **`PENDING_EXTERNAL_ACTIVATION`** |
| Credential present | `False` (env `JEV_API_KEY`) |
| Transport implemented | `False` |
| Actuation enabled | `False` |
| Interface card digest (sha256) | `fe576fb0e74fdbedce78080b2fa7270f2199f55c231317910136c80a26fc7041` |

> **Provenance is deliberately NOT in this file.** The generation timestamp, the
> repo head and the dirty flag are recorded in the JSON companion. They are left
> out here so this document has exactly ONE byte form: a document that changes
> every time it is generated cannot be checked for staleness at all.
> `tools/v1_1_emit_real_jev_verification.py --check` compares it byte for byte.

**`PENDING_EXTERNAL_ACTIVATION` is a lawful end state, not a failure.** The
software is complete; what is missing lives outside this repository.

## 2. What stops real Jev from scoring

- `transport_not_implemented`
- `credential_absent(JEV_API_KEY)`
- `interface_field_critical_unverified:endpoint_path`
- `interface_field_critical_unverified:http_method`
- `interface_field_critical_unverified:api_version`
- `interface_field_critical_unverified:auth_header_syntax`
- `interface_field_critical_unverified:model_identifier`
- `interface_field_critical_unverified:request_schema`
- `interface_field_critical_unverified:response_schema`
- `interface_field_critical_unverified:timeout_contract`
- `interface_field_critical_unverified:error_schema`
- `interface_field_critical_unverified:idempotency_semantics`

The interface card reports 10 critical field(s) as UNVERIFIED:

- `endpoint_path`
- `http_method`
- `api_version`
- `auth_header_syntax`
- `model_identifier`
- `request_schema`
- `response_schema`
- `timeout_contract`
- `error_schema`
- `idempotency_semantics`

## 3. Field-level verification (the anti-"UNVERIFIED blob" rule)

Nothing here is marked UNVERIFIED at whole-provider granularity. Every field
carries its own status and its own authority, so a reader can see exactly which
facts are established and which are not.

| field | value | status | authority | last_verified_at | critical |
|---|---|---|---|---|---|
| `vendor` | `TypeSafe AI` | **VERIFIED** | `OFFICIAL_PUBLIC` | 2026-09-27 | no |
| `model_family` | `System One Models` | **VERIFIED** | `OFFICIAL_PUBLIC` | 2026-09-27 | no |
| `public_model_name` | `Jev` | **VERIFIED** | `OFFICIAL_PUBLIC` | 2026-09-27 | no |
| `public_availability` | `Early Access` | **VERIFIED** | `OFFICIAL_PUBLIC` | 2026-09-27 | no |
| `api_host` | `api.typesafe.ai` | **VERIFIED** | `OFFICIAL_PUBLIC` | 2026-09-27 | no |
| `web_console_host` | `console.typesafe.ai` | **VERIFIED** | `OFFICIAL_PUBLIC` | 2026-09-27 | no |
| `credential_mechanism` | `API key` | **VERIFIED** | `OFFICIAL_PUBLIC` | 2026-09-27 | yes |
| `input_pricing` | `USD 0.042 / 1M input tokens` | **VERIFIED** | `OFFICIAL_PUBLIC` | 2026-09-27 | no |
| `output_pricing` | `free / unmetered` | **VERIFIED** | `OFFICIAL_PUBLIC` | 2026-09-27 | no |
| `output_paradigm` | `typed decisions + probabilities / confidence` | **VERIFIED** | `OFFICIAL_PUBLIC` | 2026-09-27 | yes |
| `official_api_status` | `operational` | **VERIFIED** | `OFFICIAL_PUBLIC` | 2026-09-27 | no |
| `endpoint_path` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | yes |
| `http_method` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | yes |
| `api_version` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | yes |
| `auth_header_syntax` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | yes |
| `model_identifier` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | yes |
| `request_schema` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | yes |
| `response_schema` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | yes |
| `timeout_contract` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | yes |
| `error_schema` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | yes |
| `idempotency_semantics` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | yes |
| `batch_semantics` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | no |
| `retry_contract` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | no |
| `rate_limit_headers` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | no |
| `usage_response_fields` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | no |
| `billing_response_fields` | **UNKNOWN** | UNVERIFIED | `UNKNOWN` | - | no |

## 4. Authority model (§7B)

Only these may promote a field to VERIFIED:
`MEASURED`, `OFFICIAL_ACCOUNT_DOC`, `OFFICIAL_PUBLIC`.

`LOCAL_CONFIG` proves only that *this project was once configured that way*.
`ASSUMED` is a guess. Neither can ever back a field that the live provider sends.

## 5. Explicitly NOT guessed

The verified public host `api.typesafe.ai` was **not** expanded into a guessed
route. The following remain UNKNOWN and no default was invented for them:

- exact API route
- HTTP method
- API version
- Authorization header syntax
- model identifier
- request JSON schema
- response JSON schema
- batch semantics
- timeout contract
- retry contract
- error schema
- rate-limit headers
- usage response fields
- billing / credit response fields
- idempotency semantics

## 6. Credential discovery (§7A)

| item | result |
|---|---|
| `JEV_API_KEY` | `ABSENT` |
| git-tracked secret scan | `CLEAN` |

Presence only. No credential value is read, stored, or published anywhere in
this document or its JSON companion.

## 7. Real API smoke (§7E)

| gate | state |
|---|---|
| credential present | `False` |
| exact endpoint verified | `False` |
| request schema verified | `False` |
| transport implemented | `False` |
| **smoke permitted** | **`False`** |

Requests issued: **0**.

§7E permits a real smoke ONLY when the credential is present AND the exact endpoint AND the request schema are VERIFIED. Closed gate(s): `credential_present`, `exact_endpoint_verified`, `request_schema_verified`, `transport_implemented`. No request was issued. Zero calls also means zero cost and zero data exposure.

## 8. How to activate real Jev (a reviewed change, not a config tweak)

1. Obtain the interface facts from an authoritative source (official docs, or an
   account-holder document) and record them on the card with authority
   `OFFICIAL_PUBLIC` / `OFFICIAL_ACCOUNT_DOC`, **or** measure them
   (`MEASURED`) — never `ASSUMED`.
2. Provide the credential in `JEV_API_KEY` (never in the repo).
3. Run the §7E smoke: at most 1 normal request plus, only if needed, 1 negative
   auth/schema request. No retry loop.
4. Only then flip `TRANSPORT_IMPLEMENTED`, record the smoke evidence, and
   re-baseline whichever pinned artifacts moved.

Steps 1-2 alone do not activate anything: `TRANSPORT_IMPLEMENTED` is the second
lock and it is a code change with its own review.

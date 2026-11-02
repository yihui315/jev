# Real Jev Provider Interface Card — SUPERSEDED, see the generated document

> **Do not read this file for the interface.** It is kept only as a signpost so
> that a reader who lands here is not misled.

## The canonical documents

| Purpose | Path |
|---|---|
| Human-readable interface + verification record | `docs/V1_1_REAL_JEV_INTERFACE.md` |
| Machine-readable, field-level record | `artifacts/release/V1_1_REAL_JEV_VERIFICATION.json` |
| The single source of truth (code) | `packages/jcc_core/providers/real.py` |

Both files are **generated** from the interface card in code by
`tools/v1_1_emit_real_jev_verification.py`. Nothing in them is hand-written, so
they cannot go stale relative to the code without `--check` failing.

## Why this file was reduced to a stub

An earlier draft of this card listed *assumed* request shapes — a decide path
(`/v1/jev/{model}/decide`), an `Authorization: Bearer …` header form, and a
`application/json` content type — as if they were the contract that the v1.1
line works against.

They were guesses. None of them is published, measured, or confirmed by any
source consulted in §7. §7C forbids inventing a plausible request from a
verified host name, and the specific danger of leaving those shapes on disk is
that a later reader — human or agent — treats a guess in a document as a fact,
because it *looks* like a fact.

So the guesses were withdrawn rather than annotated. The current position is
recorded, field by field with its authority, in the generated documents above.
Today that position is: the public host exists, and the path, method, version,
auth-header syntax, model identifier, and request/response schemas are all
`UNKNOWN`.

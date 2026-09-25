# ADR-0011 — Attribution of pre-F1 `directives` rows (the M1 backfill rule)

- **Status**: DECIDED (protocol ruling; **the code half is still unshipped** — see *Who writes what*)
- **Owner**: atlas-2 (protocol; `docs/ADR`)
- **Date**: 2026-09-25
- **Extends**: ADR-0008 (D-1…D-41, and the F1/F2 attribution gates landed in `b29d587`),
  ADR-0009 (EV-4 byte-level provenance, EV-11 evidence must be reproducible)
- **Closes the protocol half of**: `docs/ADR/KNOWN_DEVIATIONS.md` **KD-004**
- **Informs**: `packages/jcc_core/state_store.py` (`directives.origin`, F1/F2), the A8 pin set
  (`state_store.py` is one of the 12), the release manifest (ADR-0009)

---

## Decision

A `directives` row written before F1/F2 (`b29d587`) has no `origin`. It is attributed by exactly
one operation, **M1**, which is **derivation, not assignment**:

**M1 — attribute a pre-F1 row, or leave it refused. There is no third option.**

1. **Only `origin IS NULL` rows are touched.** A row that already carries an origin is never
   rewritten, whatever its id prefix suggests. (A stored origin is *evidence of a decision already
   made*; re-deriving it would overwrite the one artefact that distinguishes a pre-F1 row from a
   post-F1 one.)
2. **The value comes from the row's own id, through the function F1 uses.** M1 writes
   `state_store.directive_plane_of(directive_id)` — the same lookup `resolve_directive_origin` (F1)
   calls — so M1 and F1 cannot disagree about what an id shape means. `dir_…` → `engine`,
   `shadow_…` → `supervisor`. Not a heuristic over string prefixes: the table
   (`_ORIGIN_OF_PREFIX`) is the one F1 already pins.
3. **Unattributable shapes are not attributed.** If `directive_plane_of(directive_id) is None` — no
   prefix, or a bare prefix (`"dir_"`, `"shadow_"`) that names no event — the row **stays NULL**,
   stays refused by the F2 gate, and is listed in M1's report. **`"engine"` is not the fallback.**
4. **M1 writes a census report** (databases visited, rows, NULL-priors, attributed, refused, still
   NULL) and **a database with a non-empty "refused" list is not admissible as release evidence**
   under ADR-0009. The report is a run artefact, not a recomputable one, so per EV-11a it travels as
   a sidecar next to the evidence and is never manifest-hashed as if it were deterministic.

**Rejected from the proposal under review:** the third clause, "unrecognised prefix → backfill
`engine`, marked best-effort". §*Why not the default* sets out the reasons and the replacement.

---

## Why not the default

Four arguments, in the order they are decisive.

**1. It contradicts F1 in the same commit.** `state_store.py` states the rule for attribution
twice, in the language of a refusal: *"Anything else -- including the empty string and `None` --
is REFUSED, not defaulted: an unattributed row is exactly the thing that let two planes overwrite
each other unnoticed (#82), and **a default would make the attribution disappear the moment a
caller forgets it**."* The proposed default is that sentence with the caller removed: it manufactures
attribution for a row where none was recorded, and does so permanently and invisibly. Whatever one
thinks of a default in a *caller*, a default in a *migration* is strictly worse, because a migration
is code that is expected to run exactly once, unattended, over data it has never seen.

**2. A name we do not have is not evidence of a writer.** `directive_plane_of` returns `None` for
an unrecognised shape *precisely because* there is nothing in the id that could distinguish the two
planes — which is the definition of the #82 defect. Stamping such a row `engine` makes it
byte-for-byte indistinguishable from a genuine engine row. The defect #82 was about does not
disappear; it becomes unreportable, because it now lives in rows that carry a positive attribution.

**3. No liveness is bought by it.** The objection to refusing is "then that database is unusable". It is
not: F2 refuses to rewrite a NULL-prior row, and nothing else in the store is blocked. Writes under
other `directive_id`s continue to succeed; a session keeps working; only the row whose provenance we
do not know stays refused — and refusing it is the entire point of F2. The availability cost of the
fail-closed form is zero, so there is no trade to make.

**4. The measured corpus would never have used it, and the unmeasured corpus is the one that
matters.** Census below: **1 648** pre-F1 rows exist in the tree, of which **1 452** `dir_…` and
**196** `shadow_…` — **zero** of unrecognised shape. The derived rule is therefore *total* on
everything measurable (1648/1648) and needs no operator decisions at all. The default would fire
**0 times**. Adopting a provenance-fabricating rule so that it can fire on corpus we have never
inspected inverts the burden: the shape of this decision should be set by the failure mode we cannot
measure, not by a zero we can.

**The one in-tree pin that already names this.** `packages/jcc_core/tests/test_directive_identity.py::
test_f2_the_independently_load_bearing_case_a_stored_origin_that_contradicts_its_prefix` says the
nail must stay red after M1, and goes green — as a **regression signal** — if *"the migration rewrote
a stored origin on the strength of its prefix -- i.e. it **fabricated provenance** for a row whose
provenance is not recoverable from the id alone"*. (Honest scoping: the nail's row is non-NULL, so
neither rule touches it and it is not what decides the question — the pin matters because it
*enumerates fabrication as a regression class*, and that is the principle M1 extends to the shape the
pin does not enumerate.) A default is that same step, applied one shape wider, and nothing in the
pin distinguishes the two.

---

## Evidence behind the ruling

All measured on `F:\jev` by census, not argued:

| claim | how it was measured | status |
|---|---|---|
| every `jcc.db` in the tree predates F1/F2 | walk of all **1 077** `jcc.db`: **1 077** have a `directives` table *without* an `origin` column, **0** have one | VERIFIED (this ADR's census) |
| pre-F1 corpus is not empty | those 1 077 databases hold **1 648** `directives` rows, in **336** of them | VERIFIED |
| every measured pre-F1 row is prefix-attributable | shape split of the 1 648: **1 452** `dir_…`, **196** `shadow_…`, **0** unrecognised, **0** bare prefix | VERIFIED |
| no database has ever been written *after* F1/F2 | newest `jcc.db` mtime **2026-09-25 12:40:25** vs commit `b29d587` **12:58:47**; a fresh in-process `StateStore` does carry `origin` in `PRAGMA table_info` | VERIFIED |
| nothing outside the tree holds a corpus | `~/.jcc` subtree: **0** sqlite files | VERIFIED (empty result, reported as one) |
| F1 refuses an unrecognised shape regardless of origin | source: `state_store.py:142-152` (`directive_plane_of` → `None`), `resolve_directive_origin` double refusal | VERIFIED (source read) |
| the migration itself exists in no code today | grep for `migration` / `backfill` / `migrate` over `packages/`: no implementation, only the test-nail discussion above | VERIFIED |

**Caveat, stated as UNVERIFIED**: the census covers this working tree. The databases live under the
pytest basetemp roots (`.workbuddy/`, `runtime/pytest-tmp/`) and are test artefacts, not a production
corpus — 1 648 rows is a *sample* of pre-F1 shape, not a census of the field. A corpus outside the
tree has never been measured, and *that* is why the rule is fail-closed rather than permissive. If
someone later measures a corpus containing unattributable rows, M1 refuses them and reports them;
the operator decides, per row, with the report in hand. That is the whole purpose of clause 4.

---

## When this rule may disappear with the data

The rule is withdrawable; **F1 and F2 are not**. F1/F2 guard every future write and stay forever.
M1 only ever existed to give a corpus a story about where it came from, so it dies when the corpus
does. All three conditions must hold, and all three are *measured*, not asserted:

1. **No certified row is unattributed.** A census (the same walk that produced the table above, plus
   the migrated databases) is part of the acceptance gate and reports `origin IS NULL` rows = 0 for
   every store admitted to the release evidence set.
2. **Every pre-F1 corpus is exhausted.** Each one is either migrated under M1 with a report whose
   "refused" list is empty, or proven to contain zero rows by measurement.
3. **Nothing cites a derived value as evidence.** No artefact in the release set, and no assertion in
   any doc, treats a migrated `origin` as proof of attribution for a row the migration did not itself
   write. A value that was *derived* is a statement about an id; calling it a statement about a
   writer is precisely what this ADR forbids.

When all three hold, M1's code and this document are dead together with the corpus, and the
"`directive_plane_of` returned `None`" branch can be tightened into a plain refusal. Until then the
branch is the only thing standing between an unattributed row and a fabricated one.

---

## `origin` carries three readings. The field does not split.

Ruling: **keep one column through P4 freeze; pin the three readings separately.** Splitting is not
the risk; a derivation that breaks the invariant between the readings is.

| reading | what it means | pinned by |
|---|---|---|
| **R-A — validity (F1)** | the writer of this row, and only that writer may have written an id of this shape | `resolve_directive_origin` (`_DIRECTIVE_ORIGINS` × `_ORIGIN_OF_PREFIX`) |
| **R-B — gate (F2)** | the value compared against the *stored* prior before an overwrite is allowed | `add_directive`'s NULL/other-origin branch |
| **R-C — plane partition (S-3)** | which plane's surface a row belongs to, used by the sentinel's per-plane counts | `sentinel.directive_plane` / `test_p4_directive_origin_pins.py` |

R-A and R-B are the *same* reading read at two moments — R-B asks "is the stored value what the
caller claims", which is only answerable against R-A. R-C is the load-bearing one, and it is sound
**only while `origin ↔ id prefix` is a bijection**: the sentinel groups rows by origin and assumes
that grouping recovers the plane. M1 preserves that bijection by construction (clause 2 derives
through `directive_plane_of`); **the rejected default would break it** — one `engine`-stamped row
among the supervisor's would be counted on the wrong surface, and nothing would report it. So the
argument against the default is not only about honesty; it is what keeps R-C true.

**Hard rule for the future consumer.** `budget_snapshot` and every counter derived from `directives`
partition events by `directive_event_key()` — event identity, as it does today
(`state_store.py:736`). `origin` may **filter** a surface (per-plane checks, as A2 does) and may
**never group** as the definition of an event. If a future requirement genuinely needs a fourth sense
— "who observed this" versus "who issued it" — that is a new column, a new ADR, and its own re-anchor
of the A8 pin for `state_store.py`. Not a rename, and not a debug value re-entered after the freeze.

**Why not split now.** `packages/jcc_core/state_store.py` is one of the 12 A8 pins, so any column
change inside the freeze window re-opens the re-baseline that ADR-0010 GA-3 forbids as a precaution
and that the lead's own traceability chain (release-gate §6 item 15) deliberately keeps on one
unbroken chain of approvals. Against that cost: **zero measured demand**. Both existing consumers
want the same value, and the corpus that would motivate a second field is measured at **0**
unattributable rows. Prefer the cheap, pinned version now; split when something asks for it with
numbers attached.

---

## Do not

* Do not add a default origin to M1, in any form, including "engine, best-effort" or "unknown".
* Do not rewrite a stored, non-NULL origin on the strength of an id prefix.
* Do not cite a migrated `origin` as evidence of attribution in the release set.
* Do not split the column before the freeze. If you need to, bring the numbers.
* Do not fence a pre-F1 row by deleting it. A refused row is data; the report is the remedy.

---

## Who writes what

This ruling is protocol only. Nothing in `packages/` is changed here.

* **M1's implementation** belongs to the owner of `packages/jcc_core/state_store.py` (forge, per
  `b29d587`), next to F1/F2, and must implement clauses 1-4 exactly.
* Its falsifier belongs beside the existing nails in
  `packages/jcc_core/tests/test_directive_identity.py`: (a) an unrecognised-shape row is written with
  `origin` still NULL; (b) it is listed in M1's report; (c) a stored non-NULL origin is never
  rewritten; (d) the contradiction nail above stays red.
* The census that backs this ADR should be re-runnable from `docs/` — the method is the one in the
  evidence table, and a rerun is what condition 1 will be measured with.

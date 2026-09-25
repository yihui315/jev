# ADR-0011 — Attribution of pre-F1 `directives` rows (the M1 backfill rule)

- **Status**: DECIDED, and **implemented** — `state_store.backfill_directive_origins` landed in
  `973217f` (2026-09-25) implementing clauses 1-4 as written below. KD-004 stays open regardless, by
  this ADR's own rule: no implementer closes it without a new ruling, and the withdrawal conditions
  have not been measured against a released corpus.
- **Owner**: atlas-2 (protocol; `docs/ADR`)
- **Date**: 2026-09-25
- **Extends**: ADR-0008 (D-1…D-41, and the F1/F2 attribution gates landed in `b29d587`),
  ADR-0009 (EV-4 byte-level provenance, EV-11 evidence must be reproducible)
- **Closes the protocol half of**: `docs/ADR/KNOWN_DEVIATIONS.md` **KD-004**
- **Corrected 2026-09-25**: the corpus measurement quoted in the first version ("0 unrecognised rows")
  was refuted by the lead's independent census. The decision did not change and does not rest on it;
  §*Evidence* records the correction and the withdrawn argument, so that nobody later quotes the old
  number as support.
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
byte-for-byte indistinguishable from a genuine engine row. Whatever #82 was about is not undone by
relabelling it; it becomes unreportable, because it now lives in rows that carry a positive attribution.

**3. No liveness is bought by it.** The objection to refusing is "then that database is unusable". It is
not: F2 refuses to rewrite a NULL-prior row, and nothing else in the store is blocked. Writes under
other `directive_id`s continue to succeed; a session keeps working; only the row whose provenance we
do not know stays refused — and refusing it is the entire point of F2. The availability cost of the
fail-closed form is zero, so there is no trade to make.

**4. *(Withdrawn — do not cite this ADR as saying "it has never happened".)*** An earlier draft
argued here that the default would never fire, because the measured corpus held zero unrecognised
rows, and that the decision could therefore rest on that. **That argument is withdrawn.** It is false,
and it is the kind of false comfort that makes a rule look untriggerable and therefore skippable: a
future reader would see "would fire 0 times" and stop reading at the part that matters. The
measurement behind it was also method-bound — a census restricted to files *named* `jcc.db` misses the
other `*.db` stores the tree holds — and the lead's independent run refuted it outright. The corpus
**does** contain non-derivable rows. **The decision rests on 1-3 alone, and that is enough:** none of
those three depends on how many such rows there are, including the zero case.

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
| the tree holds stores under names other than `jcc.db` | `*.db` walk: **1 116** files vs **1 077** named `jcc.db`; the extra 39 are `m_*.db`, `live.db`, `det.db`, `_lead_verify.db`, `shadow.db`, `inert_tokens.db` … | VERIFIED |
| **non-derivable rows exist** | `*.db` walk at **2026-09-25 13:18:53**: **1 110** pre-F1 stores, **1 666** rows, **14** not derivable — `dir0` ×7, `esc0` ×7 | VERIFIED (mine and the lead's independent run agree on the class; the lead's run counted ~54) |
| the non-derivable rows are short synthetic ids | the observed shapes are `dir0`, `esc0` — none is of the `dir_<session>_<event>` / `shadow_<session>_<event>` form a real row carries | VERIFIED |
| the count is time- and method-dependent | three runs inside one hour: **1 648** rows (`jcc.db` walk), **1 666** (`*.db` walk), **1 706** (lead's `*.db` run) — teammates' test runs were in flight throughout | VERIFIED |
| every pre-F1 store predates F1/F2 | of those 1 110 stores, **1 110** have a `directives` table *without* an `origin` column and **0** have one | VERIFIED |
| no database has ever been written *after* F1/F2 | newest `jcc.db` mtime **2026-09-25 12:40:25** vs commit `b29d587` **12:58:47**; a fresh in-process `StateStore` does carry `origin` in `PRAGMA table_info`. Unchanged across re-runs | VERIFIED |
| nothing outside the tree holds a corpus | `~/.jcc` subtree: **0** sqlite files | VERIFIED (empty result, reported as one) |
| F1 refuses an unrecognised shape regardless of origin | source: `state_store.py:142-152` (`directive_plane_of` → `None`), `resolve_directive_origin` double refusal | VERIFIED (source read) |
| M1 exists as code and does what this ADR says | `973217f` → `state_store.backfill_directive_origins`: NULL priors only, derived through `directive_plane_of`, unrecognised shapes refused and listed, census report written. No import, no `__init__`, no schema upgrade, no `add_directive` call reaches it — operator-invoked, one database per call, `databases_visited` so a corpus walk concatenates reports | VERIFIED (forge's implementation and its two RELEASE falsifiers; I read the commit and did **not** re-run them) |
| a relaxed rule does go red | forge's falsifier: making `directive_plane_of` fall back to `engine` for unknown shapes turns the M1 nails red; stripping the `AND origin IS NULL` guard turns the clause-1 nail **and** the contradiction nail red. `state_store.py` sha256 unchanged across both passes | VERIFIED (forge's measurement, method stated, not re-run by me) |

**Correction recorded on 2026-09-25 (lead's refutation, accepted).** The first version of this
evidence table asserted **0** unrecognised rows out of 1 648. That was wrong, and it was wrong for a
reproducible reason worth writing down: the census matched the filename `jcc.db`, which silently
excluded every other `*.db` store in the tree — the ones that carry the non-derivable rows. The
standing method note is therefore **match `*.db`, never a filename**, and re-run with the timestamp
attached, because the corpus is being written concurrently by other people's test runs and any count
is a snapshot, not a fact about the product.

**Residual scope of the risk, restated after the correction.** Non-derivable rows are known to exist;
every one observed so far sits on a short synthetic id from a test fixture. The **remaining** unknown
is narrower than it was: *a row that is real data but whose id is not of the `dir_`/`shadow_` +
event shape*. That is the case to keep watching, and clause 4 (refuse + report) is what makes it
visible when it appears instead of silently accepted.

**Still UNVERIFIED**: the census covers this working tree, and the stores live under the pytest
basetemp roots (`.workbuddy/`, `runtime/pytest-tmp/`) — they are test artefacts, not a production
corpus. A corpus outside the tree has never been measured, and *that* is why the rule is fail-closed
rather than permissive. When a new corpus arrives, the rerun method above is what closes this.

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
3. **Nothing in the release set cites a derived value as attribution — worded so narrow it cannot be
   read loosely.** Precisely, and this is the clause that actually kills M1 together with the corpus:
   a migrated `origin` may be recorded **only** as *"attribution derived by M1 from this row's id on
   &lt;date&gt;"* — a statement about what the id says and who ran M1 — and **never** as *"the writer of
   this row was &lt;origin&gt;"*. No gate assertion, no acceptance criterion, no ADR and no report may
   treat a migrated value as evidence of the writer. A migrated row's provenance is the migration;
   that is the entire content of the value. Anything that would need the stronger statement above is
   exactly the thing clause 3 of M1 refuses to produce.

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
unbroken chain of approvals — and the chain is currently **mid-flight**: six rows are already
re-anchored with their `old` values recorded while the `new` values still wait on the next superseding
commit. So every further edit to that file is another round trip through the freeze window. Against
that cost: no consumer has asked for a second field. Both existing consumers want the same value, and
the only corpus that could motivate a split is measured in **tens of rows, all of them synthetic
fixture ids**. Prefer the cheap, pinned version now; split when something asks for it with a real
corpus attached.

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
  `b29d587`) and **has landed**: `backfill_directive_origins` (`973217f`), clauses 1-4, and
  deliberately not an automatic side effect — a migration runs once, unattended, over data it has
  never seen, so it is invoked by an operator against an explicit path and leaves the report as the
  record that it ran. One database per call; `databases_visited` exists so a corpus walk concatenates
  reports rather than inventing a second format.
* **One owner per nail — no duplicate M1 falsifiers (agreed with the lead 2026-09-25).** The four
  falsifiers live in `packages/jcc_core/tests/test_directive_identity.py` and **only** there: (a) an
  unrecognised-shape row is written with `origin` still NULL; (b) it is listed in M1's report; (c) a
  stored non-NULL origin is never rewritten; (d) the contradiction nail above stays red.
  `packages/supervisor/tests/test_p4_directive_origin_pins.py` is **closed** to new M1 nails: it pins
  the *write* (S-1…S-4, landed in `b29d587`), not the migration, and a second copy of the same
  assertion in a second package is precisely how a pin ends up edited by the person who needs it to
  pass. The already-landed contradiction nail
  (`test_f2_the_independently_load_bearing_case_a_stored_origin_that_contradicts_its_prefix`) is the
  sole owner of "fabricated provenance must stay red". I add no test here — `docs/` only.
* The census that backs this ADR should be re-runnable from `docs/` — the method is the one in the
  evidence table, **matched on `*.db` and stamped with the run time**, and a rerun is what condition 1
  will be measured with.

**Re-anchoring cost, now that the A8 pins have moved.** `state_store.py` is one of the 12 A8 pins and
its old→new record is already mid-chain: six rows re-anchored with their `old` values recorded, the
`new` values still pending the next superseding commit. That is the concrete reason this ADR settles
the attribution question in prose now, rather than leaving the column to grow a second and third
meaning and discover later which of them the pin was measuring.

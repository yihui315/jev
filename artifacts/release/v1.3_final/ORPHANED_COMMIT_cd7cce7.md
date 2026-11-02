Orphaned commit record: cd7cce7

WHAT HAPPENED
-------------
My commit `cd7cce7` ("fix(falsifier): correct a fabricated justification; add
mutation-lands selftest") is no longer reachable from HEAD:

    git merge-base --is-ancestor cd7cce7 HEAD   ->  NOT reachable
    git merge-base --is-ancestor c4654d3 HEAD   ->  reachable

Nothing was lost except the commit and its message. Verified by content hash,
not by assertion:

    tools/f_derivations_selftest.py    cd7cce7 923eb7f3508455af 7660 B
                                       c4654d3 923eb7f3508455af 7660 B  identical
    tools/f_derivations_falsifier.py   cd7cce7 dfd489cfc2bfde32 32496 B
                                       c4654d3 dfd489cfc2bfde32 32496 B  identical

`c4654d3` ("fix(inc002): the NUL label said \"content destroyed\" for a file
with one NUL") carries both files and is reachable. So the CODE is intact.

The correction itself is also already in reachable history, independently:
`de17a8d` states that "p1-tooling's original probe anchored on b'# frozen',
which does not occur in the file (find returns -1), so the replace was a no-op
and he read 'clean' as 'finding'. Rejected no-op anchors now." So the finding
that the orphaned message documents is not absent from the record -- it is
recorded in a commit that is reachable, authored by someone else.

This note exists so the message text itself is not lost, since a fabricated
justification being corrected is worth more as a record than the bug it
describes. Recovered read-only via `git show -s --format=%B cd7cce7`; no
history was rewritten to produce this file, and cd7cce7 was not touched.

VERBATIM MESSAGE OF cd7cce7
---------------------------
fix(falsifier): correct a fabricated justification; add mutation-lands selftest

The NUL check added in ba8eb6b carried a justification that was not true. The
comment claimed: "a NUL placed inside a COMMENT leaves the file perfectly valid
Python -- it compiles, tokenize is happy", and used that to argue the gate was
blind to that form.

It was false. The probe behind it ran
    bytes.replace(b"# frozen", b"# \x00roze", 1)
against this very file, and "# frozen" does not occur here (find returns -1),
so the replace was a no-op. The sample stayed clean, the probe correctly
reported no NUL, and I read that clean result as the finding instead of as the
no-op it was. Measured properly, BOTH compile() and tokenize reject a NUL
wherever it appears -- comment, string, or end of line -- so the parse-failure
path would have caught most placements on its own, and the claimed hole did not
exist.

The explicit NUL check stays, for honest reasons now stated in the comment:
it asserts the invariant instead of inheriting it from whichever parser stage
happens to choke, so a future CPython that tolerated a NUL could not silently
un-fix the gate. That is defence in depth, not a hole being closed.

New tools/f_derivations_selftest.py guards the quieter failure: a probe that
mutates a sample and then concludes from the result while the mutation never
happened. It refuses no-op anchors, proves each mutation landed, and checks the
detector -- not the parser -- is what changes the verdict. 8/8 pass, including
the honesty check that tokenize also rejects the NUL, so the comment above
cannot drift back into a story.

Writing the selftest I first used a whole-file Write on the detector and
destroyed 650 lines of scanning logic. Restored from HEAD in the same minute and
verified byte-identical; the detector was never committed in that state. The
selftest is a separate file precisely so that editing it cannot touch the
detector.

No behaviour change to the detector. Negative control still red->green; real
tree still PASS (exit 0) with zero unreadable and zero NUL-bearing tracked
files.

AUTHORSHIP NOTE (identity collision)
------------------------------------
`tools/f_derivations_falsifier.py` was added by commit 3941cad, authored by
`Harbor Falsifier <yihui315@users.noreply.github.com>`, although I wrote the
file. Three identities are active in this repository and two of them contain
"Harbor":

    Harbor Falsifier <yihui315@users.noreply.github.com>   <- committed my file
    Harbor Test-SRE   <harbor@jev.local>
    p1-tooling        <p1-tooling@jev.local>               <- me

A near-match on a name, repeated until it hardened into fact, is how
`INC002_RULE_REVIEW.md` came to be attributed to relay four times. Flagged by
Harbor Test-SRE, who is not `Harbor Falsifier` and wrote none of the text above.
Recorded here so the next reader does not have to re-derive it.

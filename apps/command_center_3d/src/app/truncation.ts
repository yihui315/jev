/**
 * Wave 9.3 — "is this list the whole answer?"
 *
 * WHY THIS MODULE EXISTS (measured, 2026-09-29). The product asked the bounded
 * list reads for 200 rows and rendered whatever came back as the domain. With
 * 500 missions in the store the Missions surface and the operational 3D plane
 * both showed 200 of them, and nothing on screen distinguished that from a
 * workspace that genuinely has 200. The first instrument that could see the
 * shortfall was the real-browser plane gate, which counts the plane's draw calls
 * and compares them with the domain — i.e. a measurement found a defect that
 * every existing gate was structurally blind to.
 *
 * This is the same discipline the attention badge already states: "we could not
 * ask" and "there are none" are different facts, and a silent zero is how an
 * alert gets missed. A silent *truncation* is how a user concludes the product
 * lost their work.
 *
 * The rule, in one line: a partial list must never be indistinguishable from a
 * complete one, and "unknown" must never be rendered as "complete".
 */

export type TruncationState =
  /** We have a total and the list is the whole of it. */
  | { kind: "complete"; shown: number; total: number }
  /** We have a total and the list is shorter than it. */
  | { kind: "truncated"; shown: number; total: number; hidden: number }
  /** We could not read the total. This is NOT "complete". */
  | { kind: "unknown"; shown: number }
  /** The bound was reached and there is nothing left to prove it by. */
  | { kind: "at-ceiling"; shown: number; ceiling: number };

/**
 * Classify a list read against the domain totals.
 *
 * - `total === null` means the counts read failed. That is `unknown`, never
 *   `complete`: the product must not claim completeness it cannot show.
 * - `shown >= ceiling` with no total is `at-ceiling`: the bound was reached, so
 *   the list MIGHT be short even though we cannot say by how much. Reporting
 *   this as complete is the original defect.
 */
export function classifyTruncation(
  shown: number,
  total: number | null,
  ceiling: number,
): TruncationState {
  if (total === null) {
    return shown >= ceiling
      ? { kind: "at-ceiling", shown, ceiling }
      : { kind: "unknown", shown };
  }
  if (shown < total) {
    return { kind: "truncated", shown, total, hidden: total - shown };
  }
  return { kind: "complete", shown, total };
}

/**
 * A one-line, human-readable disclosure — or `null` when there is nothing to
 * disclose. Returning `null` for the complete case is deliberate: an honest
 * "showing all 12" on every screen trains the user to ignore the line that
 * matters when there are 1,000.
 */
export function truncationNote(
  state: TruncationState,
  noun: { singular: string; plural: string },
): string | null {
  const word = (n: number) => (n === 1 ? noun.singular : noun.plural);
  switch (state.kind) {
    case "complete":
      return null;
    case "truncated":
      return `Showing ${state.shown.toLocaleString()} of ${state.total.toLocaleString()} ${word(state.total)} — ${state.hidden.toLocaleString()} not listed`;
    case "at-ceiling":
      return `Showing the first ${state.shown.toLocaleString()} ${word(state.shown)} (the ${state.ceiling.toLocaleString()}-item limit was reached; there may be more)`;
    case "unknown":
      return `Showing ${state.shown.toLocaleString()} ${word(state.shown)} — the total could not be read, so this list may be incomplete`;
  }
}

/** Convenience for a caller that has the numbers but not the classification. */
export function truncationNoteFor(
  shown: number,
  total: number | null,
  ceiling: number,
  noun: { singular: string; plural: string },
): string | null {
  return truncationNote(classifyTruncation(shown, total, ceiling), noun);
}

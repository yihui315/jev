/**
 * The truncation classifier is the piece that makes "we only showed you some of
 * it" sayable, so its failure modes are exactly the ones the product must not
 * have. Each test below names the real screen it protects.
 */
import { describe, expect, it } from "vitest";
import { classifyTruncation, truncationNote, truncationNoteFor } from "./truncation";

const MISSION = { singular: "mission", plural: "missions" };

describe("classifyTruncation", () => {
  it("a list shorter than the domain is TRUNCATED, with the hidden count", () => {
    // The measured defect: 500 missions in the store, 200 rows requested.
    const s = classifyTruncation(200, 500, 1000);
    expect(s.kind).toBe("truncated");
    expect(s).toMatchObject({ shown: 200, total: 500, hidden: 300 });
  });

  it("a list equal to the domain is COMPLETE", () => {
    expect(classifyTruncation(500, 500, 1000).kind).toBe("complete");
  });

  it("an empty domain is COMPLETE, not unknown", () => {
    // "there are none" and "we could not ask" are different facts.
    expect(classifyTruncation(0, 0, 1000).kind).toBe("complete");
  });

  it("a missing total is UNKNOWN -- never COMPLETE", () => {
    // This is the whole point: a failed counts read must not license the UI to
    // present a short list as the whole domain.
    const s = classifyTruncation(3, null, 1000);
    expect(s.kind).toBe("unknown");
    expect(s.kind).not.toBe("complete");
  });

  it("hitting the ceiling with no total is AT-CEILING, not COMPLETE", () => {
    // 200 rows == the old hardcoded request: we cannot prove there are only 200,
    // so we must not say so.
    const s = classifyTruncation(1000, null, 1000);
    expect(s.kind).toBe("at-ceiling");
    expect(s).toMatchObject({ shown: 1000, ceiling: 1000 });
  });

  it("a list LONGER than the reported total is not claimed as truncated", () => {
    // Defensive: a race between the two reads must not produce a negative
    // "hidden" count that renders as nonsense.
    const s = classifyTruncation(7, 5, 1000);
    expect(s.kind).toBe("complete");
  });
});

describe("truncationNote", () => {
  it("says nothing when the list is complete", () => {
    // A note on every screen becomes furniture; the line must stay meaningful.
    expect(truncationNote(classifyTruncation(12, 12, 1000), MISSION)).toBeNull();
  });

  it("names the shown, the total and the hidden count", () => {
    const note = truncationNote(classifyTruncation(200, 500, 1000), MISSION);
    expect(note).toContain("200");
    expect(note).toContain("500");
    expect(note).toContain("300 not listed");
  });

  it("distinguishes 'could not read the total' from 'there are more'", () => {
    const unknown = truncationNote(classifyTruncation(4, null, 1000), MISSION);
    const ceiling = truncationNote(classifyTruncation(1000, null, 1000), MISSION);
    expect(unknown).toContain("could not be read");
    expect(unknown).toContain("may be incomplete");
    expect(ceiling).toContain("limit was reached");
    expect(unknown).not.toBe(ceiling);
  });

  it("gets the singular/plural right", () => {
    expect(truncationNote(classifyTruncation(1, null, 1000), MISSION))
      .toContain("1 mission ");
    expect(truncationNote(classifyTruncation(2, null, 1000), MISSION))
      .toContain("2 missions");
  });

  it("truncationNoteFor agrees with the two-step form", () => {
    expect(truncationNoteFor(200, 500, 1000, MISSION))
      .toBe(truncationNote(classifyTruncation(200, 500, 1000), MISSION));
    expect(truncationNoteFor(5, 5, 1000, MISSION)).toBeNull();
  });
});

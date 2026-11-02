import { describe, it, expect } from "vitest";
import {
  allowedRootsFrom,
  isAbsolutePath,
  isWithinAllowedRoots,
  partitionProjects,
  suggestedRoot,
  verdictFromServerCode,
  verifyProjectRoot,
  VERDICT_HEADLINE,
  VERDICT_TITLE,
} from "./projectRoot";
import type { Project, ProjectScope } from "./domainApi";

/**
 * The workspace boundary, tested on the real values this host publishes.
 *
 * THE TWO PROPERTIES THAT MATTER
 * -----------------------------
 * 1. A root outside `allowed_project_roots` is REFUSED — including the
 *    `F:\jev` residue that predates the move to `D:\jev`.
 * 2. Refusal is produced by JUDGEMENT, never by REWRITING the path. A test that
 *    only checked "does F:jev become D:jev" would pass a bypass; these assert
 *    that the verdict is a refusal and the path is returned UNCHANGED.
 */
const proj = (root: string, id = "p1", name = "P"): Project => ({
  project_id: id,
  name,
  root_path: root,
  description: "",
  created_at: 0,
  updated_at: 0,
  archived: false,
});

/** The boundary exactly as `GET /api/v1/domain/home` publishes it. */
const REAL_SCOPE: ProjectScope = {
  schema: "jcc/v1.3-project-scope/1",
  allowed_project_roots: ["D:\\jev"],
  reason_codes: [
    "PROJECT_ROOT_OUT_OF_SCOPE",
    "PROJECT_ROOT_MISSING",
    "PROJECT_ROOT_NOT_ABSOLUTE",
    "PROJECT_ROOT_NOT_A_DIRECTORY",
  ],
  fail_closed: true,
  stored_value: "",
};

const ALLOWED = allowedRootsFrom(REAL_SCOPE);

describe("allowed roots from the server", () => {
  it("reads the real boundary, not a hardcoded one", () => {
    expect(ALLOWED).toEqual(["D:\\jev"]);
  });

  it("distinguishes 'no scope read' (null) from 'empty allow-list' ([])", () => {
    // These are different facts and both matter: null = we could not find out,
    // [] = the server refuses everything. Neither is a pass.
    expect(allowedRootsFrom(null)).toBeNull();
    expect(allowedRootsFrom(undefined)).toBeNull();
    expect(allowedRootsFrom({ ...REAL_SCOPE, allowed_project_roots: [] })).toEqual([]);
    // An empty-string entry is not a root.
    expect(allowedRootsFrom({ ...REAL_SCOPE, allowed_project_roots: ["  "] })).toEqual([]);
  });
});

describe("isWithinAllowedRoots", () => {
  it("accepts the root itself and paths nested under it", () => {
    expect(isWithinAllowedRoots("D:\\jev", ALLOWED!)).toBe(true);
    expect(isWithinAllowedRoots("d:\\jev\\", ALLOWED!)).toBe(true); // trailing sep
    expect(isWithinAllowedRoots("D:\\jev\\apps\\ui", ALLOWED!)).toBe(true);
    expect(isWithinAllowedRoots("D:\\jev\\.workbuddy", ALLOWED!)).toBe(true);
  });

  it("rejects the old F:\\jev residue", () => {
    expect(isWithinAllowedRoots("F:\\jev", ALLOWED!)).toBe(false);
    expect(isWithinAllowedRoots("F:\\jev\\.workbuddy", ALLOWED!)).toBe(false);
  });

  it("rejects a SIBLING whose name merely starts with the allowed root", () => {
    // The case a plain `startsWith` would wave through. `D:\jevold` is not
    // inside `D:\jev`, and a boundary that accepted it would be no boundary.
    expect(isWithinAllowedRoots("D:\\jevold", ALLOWED!)).toBe(false);
    expect(isWithinAllowedRoots("D:\\jev-other\\app", ALLOWED!)).toBe(false);
  });

  it("rejects an unrelated drive and an empty path", () => {
    expect(isWithinAllowedRoots("C:\\Users\\x", ALLOWED!)).toBe(false);
    expect(isWithinAllowedRoots("", ALLOWED!)).toBe(false);
  });
});

describe("isAbsolutePath", () => {
  it("accepts drive and UNC roots", () => {
    expect(isAbsolutePath("D:\\jev")).toBe(true);
    expect(isAbsolutePath("d:/jev")).toBe(true);
    expect(isAbsolutePath("\\\\server\\share")).toBe(true);
  });

  it("rejects relative fragments", () => {
    expect(isAbsolutePath("jev")).toBe(false);
    expect(isAbsolutePath(".\\jev")).toBe(false);
    expect(isAbsolutePath("")).toBe(false);
  });
});

describe("verifyProjectRoot", () => {
  it("accepts a project inside the real boundary", () => {
    const v = verifyProjectRoot(proj("D:\\jev"), ALLOWED);
    expect(v.ok).toBe(true);
    // The ACCEPTED path is returned verbatim — no normalisation, no rewriting.
    expect(v.ok && v.root).toBe("D:\\jev");
  });

  it("refuses F:\\jev with the out-of-scope verdict, path UNCHANGED", () => {
    const v = verifyProjectRoot(proj("F:\\jev\\.workbuddy"), ALLOWED);
    expect(v.ok).toBe(false);
    expect(!v.ok && v.code).toBe("ROOT_OUT_OF_SCOPE");
    // The load-bearing assertion: nothing anywhere rewrote F: to D:.
    // `root_path` is still F:, and the verdict carries no substitute path.
    expect(JSON.stringify(v)).not.toContain("D:\\\\jev");
  });

  it("refuses when the boundary could not be read, without claiming out-of-scope", () => {
    // We cannot certify the root, so we must not invent either verdict.
    const v = verifyProjectRoot(proj("F:\\jev"), null);
    expect(v.ok).toBe(false);
    expect(!v.ok && v.code).toBe("BOUNDARY_UNKNOWN");
  });

  it("refuses a relative fragment and a missing root", () => {
    const rel = verifyProjectRoot(proj("jev\\apps"), ALLOWED);
    expect(!rel.ok && rel.code).toBe("ROOT_NOT_ABSOLUTE");

    const missing = verifyProjectRoot(proj(""), ALLOWED);
    expect(!missing.ok && missing.code).toBe("ROOT_MISSING");

    const none = verifyProjectRoot(null, ALLOWED);
    expect(!none.ok && none.code).toBe("ROOT_MISSING");
  });
});

describe("suggestedRoot", () => {
  it("suggests exactly what the server published", () => {
    expect(suggestedRoot(["D:\\jev"])).toBe("D:\\jev");
    // No hardcoded D: fallback: if the server widens or moves the boundary, this
    // follows it, and an empty list offers nothing rather than guessing.
    expect(suggestedRoot(["C:\\work", "E:\\work"])).toBe("C:\\work");
    expect(suggestedRoot([])).toBeNull();
    expect(suggestedRoot(null)).toBeNull();
  });
});

describe("partitionProjects", () => {
  it("splits by the boundary and labels the blocked ones", () => {
    const { usable, blocked } = partitionProjects(
      [proj("D:\\jev", "ok1", "IN SCOPE"), proj("F:\\jev\\.workbuddy", "bad1", "OUT OF SCOPE")],
      ALLOWED,
    );
    expect(usable.map((p) => p.project_id)).toEqual(["ok1"]);
    expect(blocked.map((b) => b.project.project_id)).toEqual(["bad1"]);
    expect(blocked[0].verdict.ok).toBe(false);
  });

  it("blocks EVERYTHING when the server published an empty allow-list", () => {
    // fail-closed: an empty list is a refusal, not a pass.
    const { usable, blocked } = partitionProjects([proj("D:\\jev")], []);
    expect(usable).toHaveLength(0);
    expect(blocked).toHaveLength(1);
  });
});

describe("verdict copy", () => {
  it("gives every code a plain headline with no JSON and no code names", () => {
    for (const code of ["ROOT_OUT_OF_SCOPE", "ROOT_MISSING", "BOUNDARY_UNKNOWN", "ROOT_NOT_ABSOLUTE"] as const) {
      const headline = VERDICT_HEADLINE[code];
      const title = VERDICT_TITLE[code];
      expect(headline.length).toBeGreaterThan(0);
      expect(title.length).toBeGreaterThan(0);
      // The out-of-scope card must read as a sentence, not as a server payload.
      expect(headline).not.toContain("PROJECT_ROOT");
      expect(headline).not.toContain("{");
      expect(headline).not.toContain("code");
    }
    expect(VERDICT_HEADLINE.ROOT_OUT_OF_SCOPE).toBe("当前项目目录不在 JEV 允许的工作区范围内。");
    expect(VERDICT_TITLE.ROOT_OUT_OF_SCOPE).toBe("无法开始任务");
  });

  it("maps a server code to a known verdict, and never falls through to raw JSON", () => {
    expect(verdictFromServerCode("PROJECT_ROOT_OUT_OF_SCOPE")).toBe("ROOT_OUT_OF_SCOPE");
    expect(verdictFromServerCode("PROJECT_ROOT_MISSING")).toBe("ROOT_MISSING");
    expect(verdictFromServerCode("PROJECT_ROOT_NOT_ABSOLUTE")).toBe("ROOT_NOT_ABSOLUTE");
    // An unknown / future code still resolves to a card. Returning null here
    // would send the caller back to printing the JSON body.
    expect(verdictFromServerCode("NO_PROJECT_ROOTS_CONFIGURED")).toBe("ROOT_OUT_OF_SCOPE");
    expect(verdictFromServerCode("SOMETHING_NEW")).toBe("ROOT_OUT_OF_SCOPE");
  });
});

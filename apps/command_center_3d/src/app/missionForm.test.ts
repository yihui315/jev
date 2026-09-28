import { describe, it, expect } from "vitest";
import {
  EMPTY_MISSION_FORM,
  LAUNCH_SEQUENCE,
  PERMISSION_CATALOG,
  formatPermissions,
  launchSequenceOk,
  parsePermissions,
  validateMissionForm,
  type MissionFormValues,
} from "./missionForm";

/**
 * Mission Studio validation is the gate between "the operator typed something"
 * and "the server was asked to create a mission". A false ACCEPT here creates a
 * junk mission; a false REJECT blocks real work. Both are tested.
 */

function form(over: Partial<MissionFormValues> = {}): MissionFormValues {
  return { ...EMPTY_MISSION_FORM, title: "Do the thing", projectId: "prj_1", ...over };
}

describe("mission form validation", () => {
  it("rejects an empty form with an error per required field", () => {
    const { errors, payload } = validateMissionForm({ ...EMPTY_MISSION_FORM });
    expect(payload).toBeNull();
    expect(errors.title).toBeTruthy();
    expect(errors.projectId).toBeTruthy();
  });

  it("accepts a minimal valid form and normalises whitespace", () => {
    const { errors, payload } = validateMissionForm(
      form({ title: "  Trim me  ", projectId: " prj_9 ", agent: "workbuddy" }),
    );
    expect(errors).toEqual({});
    expect(payload).not.toBeNull();
    expect(payload!.title).toBe("Trim me");
    expect(payload!.project_id).toBe("prj_9");
    expect(payload!.agent).toBe("workbuddy");
  });

  it("treats a blank budget as 0 (unbudgeted)", () => {
    const { payload } = validateMissionForm(form({ budgetUnits: "" }));
    expect(payload!.budget_units).toBe(0);
    const ws = validateMissionForm(form({ budgetUnits: "   " }));
    expect(ws.payload!.budget_units).toBe(0);
  });

  it("REFUSES a non-numeric budget instead of silently using 0", () => {
    for (const bad of ["ten", "1,5", "abc", "-1"]) {
      const { errors, payload } = validateMissionForm(form({ budgetUnits: bad }));
      expect(payload).toBeNull();
      expect(errors.budgetUnits).toBeTruthy();
    }
  });

  it("accepts a decimal budget", () => {
    const { payload } = validateMissionForm(form({ budgetUnits: "12.5" }));
    expect(payload!.budget_units).toBe(12.5);
  });

  it("parses permissions from commas and newlines, de-duplicated and sorted", () => {
    expect(parsePermissions("fs.write, fs.read\nfs.read,  test.run ")).toEqual([
      "fs.read",
      "fs.write",
      "test.run",
    ]);
    expect(parsePermissions("")).toEqual([]);
    expect(formatPermissions(["fs.write", "fs.read"])).toBe("fs.read, fs.write");
  });

  it("emits null for an absent connection ref, not an empty string", () => {
    const { payload } = validateMissionForm(form({ connectionRef: "   " }));
    expect(payload!.connection_ref).toBeNull();
    const withRef = validateMissionForm(form({ connectionRef: "con_1" }));
    expect(withRef.payload!.connection_ref).toBe("con_1");
  });

  it("carries the permissions array into the payload", () => {
    const { payload } = validateMissionForm(form({ permissionsText: "fs.read, test.run" }));
    expect(payload!.permissions).toEqual(["fs.read", "test.run"]);
  });

  it("exposes a permission catalogue that is itself parseable", () => {
    const parsed = parsePermissions(PERMISSION_CATALOG.join(", "));
    expect(parsed).toEqual([...PERMISSION_CATALOG].sort());
  });
});

describe("launch sequence", () => {
  it("declares the handshake the server enforces (DRAFT->RUNNING is not it)", () => {
    expect(LAUNCH_SEQUENCE).toEqual(["READY", "STARTING", "RUNNING"]);
    expect(LAUNCH_SEQUENCE).not.toContain("DRAFT");
  });

  it("accepts only the exact, ordered sequence", () => {
    expect(launchSequenceOk(["READY", "STARTING", "RUNNING"])).toBe(true);
    // out of order
    expect(launchSequenceOk(["STARTING", "READY", "RUNNING"])).toBe(false);
    // truncated (a step was skipped)
    expect(launchSequenceOk(["READY", "RUNNING"])).toBe(false);
    // short-circuited straight to RUNNING
    expect(launchSequenceOk(["RUNNING"])).toBe(false);
    // extra step
    expect(launchSequenceOk(["READY", "STARTING", "RUNNING", "COMPLETED"])).toBe(false);
    expect(launchSequenceOk([])).toBe(false);
  });
});

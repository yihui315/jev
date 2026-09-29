import { describe, it, expect } from "vitest";
import {
  MISSION_STATUSES,
  attendanceKindsForTest,
  attentionLabel,
  attentionTone,
  artifactTone,
  approvalTone,
  capabilityTone,
  connectionTone,
  isControlVerified,
  missionStatusColor,
  missionTone,
  toneColor,
  toneVariant,
} from "./status";

/**
 * The status vocabulary is where the product's honesty rule is enforced:
 * a state we cannot observe must never render as a healthy one. These tests
 * are the falsifier for that rule.
 */

const HEALTHY_TONES = new Set(["ok", "live"]);

describe("status vocabulary", () => {
  it("covers every declared mission status", () => {
    for (const s of MISSION_STATUSES) {
      const tone = missionTone(s);
      expect(tone).toBeTruthy();
    }
  });

  it("marks only genuinely-good mission states as green", () => {
    // Green is reserved for "this is going well". RUNNING is live-green
    // (work is progressing); COMPLETED is ok-green. Nothing else may be green.
    const green = MISSION_STATUSES.filter((s) => HEALTHY_TONES.has(missionTone(s)));
    expect(green.sort()).toEqual(["COMPLETED", "RUNNING"]);
    // The states that mean "a human must act" are amber/red, never green.
    expect(missionTone("NEEDS_ATTENTION")).toBe("warn");
    expect(missionTone("PAUSED")).toBe("warn");
    expect(missionTone("FAILED")).toBe("danger");
    expect(missionTone("CANCELLED")).toBe("unknown");
    expect(missionTone("DRAFT")).toBe("unknown");
  });

  it("maps an unknown mission status to unknown, never to a healthy tone", () => {
    const tone = missionTone("SOMETHING_WE_DID_NOT_MODEL");
    expect(tone).toBe("unknown");
    expect(HEALTHY_TONES.has(tone)).toBe(false);
  });

  it("never renders UNKNOWN connection state as green", () => {
    expect(connectionTone("UNKNOWN")).toBe("unknown");
    expect(HEALTHY_TONES.has(connectionTone("UNKNOWN"))).toBe(false);
    expect(connectionTone("NOT_A_STATE")).toBe("unknown");
  });

  it("treats only CONTROL_VERIFIED as control-verified", () => {
    expect(isControlVerified("CONTROL_VERIFIED")).toBe(true);
    for (const k of [
      "UNKNOWN",
      "TELEMETRY_ONLY",
      "ADAPTER_CONTRACT_LIVE",
      "UNAVAILABLE",
      "",
      "control_verified", // wrong case is a different value, not a match
    ]) {
      expect(isControlVerified(k)).toBe(false);
    }
  });

  it("never tones a non-control capability as green", () => {
    expect(HEALTHY_TONES.has(capabilityTone("TELEMETRY_ONLY"))).toBe(false);
    expect(HEALTHY_TONES.has(capabilityTone("ADAPTER_CONTRACT_LIVE"))).toBe(false);
    expect(HEALTHY_TONES.has(capabilityTone("UNAVAILABLE"))).toBe(false);
    expect(HEALTHY_TONES.has(capabilityTone("UNKNOWN"))).toBe(false);
    expect(capabilityTone("CONTROL_VERIFIED")).toBe("ok");
  });

  it("tones attention kinds, defaulting to unknown", () => {
    expect(attentionTone("APPROVAL_REQUIRED")).toBe("warn");
    expect(attentionTone("AGENT_STUCK")).toBe("danger");
    expect(attentionTone("DELIVERABLE_READY")).toBe("ok");
    expect(attentionTone("WHATEVER")).toBe("unknown");
  });

  it("labels attention kinds and falls back to the raw value", () => {
    expect(attentionLabel("APPROVAL_REQUIRED")).toBe("Approval required");
    expect(attentionLabel("MADE_UP")).toBe("MADE_UP");
  });

  it("tones artifacts and approvals, defaulting to unknown", () => {
    expect(artifactTone("VERIFIED")).toBe("ok");
    expect(artifactTone("REJECTED")).toBe("danger");
    expect(artifactTone("WHO_KNOWS")).toBe("unknown");
    expect(approvalTone("PENDING")).toBe("warn");
    expect(approvalTone("APPROVED")).toBe("ok");
    expect(approvalTone("WHO_KNOWS")).toBe("unknown");
  });

  it("maps every tone to a badge variant with no default fallthrough surprise", () => {
    for (const t of ["ok", "live", "warn", "danger", "info", "unknown"] as const) {
      expect(toneVariant(t)).toBe(t);
    }
  });

  it("exposes the full attention taxonomy", () => {
    expect(attendanceKindsForTest().length).toBe(7);
  });

  // --- Wave 6: 3D colours follow the same honesty rule as the DOM ---
  it("gives every tone a distinct colour and never paints unknown green", () => {
    const tones = ["ok", "live", "warn", "danger", "info", "unknown"] as const;
    const colours = tones.map(toneColor);
    expect(new Set(colours).size).toBe(tones.length); // no two tones share a colour
    expect(toneColor("unknown")).not.toBe(toneColor("ok"));
  });

  it("colours a mission by its status tone (only COMPLETED is the ok colour)", () => {
    expect(missionStatusColor("COMPLETED")).toBe(toneColor("ok"));
    expect(missionStatusColor("FAILED")).toBe(toneColor("danger"));
    expect(missionStatusColor("NEEDS_ATTENTION")).toBe(toneColor("warn"));
    // An unknown status must map to the unknown colour, never the ok colour.
    expect(missionStatusColor("SOMETHING_NEW")).toBe(toneColor("unknown"));
    expect(missionStatusColor("SOMETHING_NEW")).not.toBe(toneColor("ok"));
  });
});

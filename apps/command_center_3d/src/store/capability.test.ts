import { describe, it, expect } from "vitest";
import {
  actionStatus,
  isActuable,
  blockReason,
  hasEndpoint,
} from "./capability";
import type { CapabilityMatrix } from "./sceneMap";

const matrix: CapabilityMatrix = {
  actuationCapabilities: ["pause", "steer", "retry"],
  actuationSummary: { actuable: [], actuableCapabilityCount: 0 },
  agents: {
    workbuddy: {
      adapterSelfStatus: "CONNECTED",
      adapterState: "UNVERIFIED",
      capabilities: {
        pause: { status: "UNVERIFIED", reason: "dry-run only" },
        steer: { status: "VERIFIED", reason: "exercised" },
        retry: { status: "UNSUPPORTED", reason: "no surface" },
      },
    },
    claude: {
      adapterSelfStatus: "CONNECTED",
      adapterState: "UNVERIFIED",
      capabilities: {
        pause: { status: "UNVERIFIED", reason: "unverified" },
      },
    },
  },
};

describe("capability gating", () => {
  it("treats VERIFIED as actuable, others as blocked", () => {
    expect(isActuable(matrix, "workbuddy", "steer")).toBe(true);
    expect(isActuable(matrix, "workbuddy", "pause")).toBe(false);
    expect(isActuable(matrix, "workbuddy", "retry")).toBe(false);
  });

  it("returns the matrix's own reason for a blocked action", () => {
    expect(blockReason(matrix, "workbuddy", "pause")).toBe("dry-run only");
    expect(blockReason(matrix, "workbuddy", "retry")).toBe("no surface");
    expect(blockReason(matrix, "claude", "pause")).toBe("unverified");
  });

  it("degrades honestly when the matrix/agent/action is unknown", () => {
    expect(actionStatus(null, "x", "pause")).toBe("UNKNOWN");
    expect(actionStatus(matrix, "ghost", "pause")).toBe("UNKNOWN");
    expect(actionStatus(matrix, "workbuddy", "cancel")).toBe("UNKNOWN");
    expect(blockReason(null, "x", "pause")).toMatch(/not loaded/);
    expect(blockReason(matrix, "ghost", "pause")).toMatch(/not present/);
  });

  it("only exposes endpoints the v1.2 server actually serves", () => {
    expect(hasEndpoint("pause")).toBe(true);
    expect(hasEndpoint("steer")).toBe(true);
    expect(hasEndpoint("retry")).toBe(true);
    expect(hasEndpoint("verify")).toBe(false);
  });
});

import { describe, expect, it } from "vitest";
import type { SceneState } from "../store/sceneMap";
import { summarizeShadow } from "./shadowView";

function sceneWithShadow(shadow: SceneState["shadow"]): SceneState {
  return { shadow } as unknown as SceneState;
}

describe("Live Shadow UI falsifier (Wave 8)", () => {
  it("renders an UNKNOWN shadow as 'unknown' with no invented numbers", () => {
    const v = summarizeShadow(
      sceneWithShadow({ source: "UNKNOWN", data: null, reason: "no live telemetry" }),
    );
    expect(v.state).toBe("unknown");
    // The falsifier: an unknown gap must NOT carry a count the UI could show.
    expect("liveRecordCount" in v).toBe(false);
  });

  it("renders a LIVE shadow with its real counts (never coerced from the gap)", () => {
    const v = summarizeShadow(
      sceneWithShadow({
        source: "LIVE",
        data: {
          live_record_count: 8,
          distinct_agent_count: 4,
          gate: "LIVE_ONLY",
          note: "supervision over live telemetry only",
        },
      }),
    );
    expect(v.state).toBe("live");
    if (v.state === "live") {
      expect(v.liveRecordCount).toBe(8);
      expect(v.distinctAgentCount).toBe(4);
      expect(v.gate).toBe("LIVE_ONLY");
    }
  });

  it("does not fabricate a LIVE summary when data is missing", () => {
    const v = summarizeShadow(sceneWithShadow({ source: "LIVE", data: null }));
    expect(v.state).toBe("unknown");
  });
});

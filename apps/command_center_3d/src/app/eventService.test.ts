import { describe, it, expect } from "vitest";
import { describeTransition, formatClock, linesFromTimeline } from "./eventService";
import { overallHealth, HEALTH_LABEL, type StatusProbe } from "../app/TopStatus";
import { agentStateFor } from "./SettingsView";

/**
 * The activity feed is the part of the product most able to lie: a plausible
 * timeline is easy to synthesise and impossible for a user to audit. So these
 * tests assert the opposite — every line must trace back to a server fact, and
 * the header's green dot must be earned by every probe at once.
 */

describe("describeTransition", () => {
  it("prefers the server's own reason when it carries one", () => {
    expect(describeTransition("DRAFT", "READY", "user approved the plan")).toBe(
      "user approved the plan",
    );
  });

  it("falls back to a readable sentence for a known status", () => {
    expect(describeTransition("DRAFT", "COMPLETED", "")).toBe("已完成");
    expect(describeTransition("STARTING", "RUNNING", "")).toBe("任务开始执行");
  });

  it("never swallows an unrecognised status", () => {
    // A status the frontend does not know is still a real event; naming it is
    // more honest than hiding it.
    expect(describeTransition("X", "SOMETHING_NEW", "")).toContain("SOMETHING_NEW");
  });
});

describe("linesFromTimeline", () => {
  it("projects the server's own transition history, in order", () => {
    const lines = linesFromTimeline([
      { seq: 1, timestamp: 100, from_status: null, to_status: "DRAFT", reason: "" },
      { seq: 2, timestamp: 200, from_status: "DRAFT", to_status: "RUNNING", reason: "" },
    ]);
    expect(lines).toHaveLength(2);
    // Ids come from the server's sequence, so React keys are stable and a
    // re-read does not duplicate lines.
    expect(lines[0].id).toBe("tl-1");
    expect(lines[1].id).toBe("tl-2");
    expect(lines[1].at).toBe(200);
  });

  it("returns nothing for an empty history rather than inventing entries", () => {
    expect(linesFromTimeline([])).toEqual([]);
  });
});

describe("formatClock", () => {
  it("renders a placeholder for a stamp the server never set", () => {
    // 0 is what a missing timestamp looks like on the wire; it must not become
    // 1970 or a bogus current time.
    expect(formatClock(0)).toBe("--:--:--");
    expect(formatClock(NaN)).toBe("--:--:--");
    expect(formatClock(-1)).toBe("--:--:--");
  });

  it("renders a real stamp as a wall clock", () => {
    expect(formatClock(1_700_000_000)).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});

describe("overallHealth", () => {
  const probe = (state: StatusProbe["state"]): StatusProbe => ({
    label: "x",
    state,
    detail: "",
  });

  it("is healthy only when every probe passed", () => {
    expect(overallHealth([probe("ok"), probe("ok")])).toBe("ok");
  });

  it("degrades when any probe failed", () => {
    expect(overallHealth([probe("ok"), probe("bad")])).toBe("degraded");
  });

  it("is UNKNOWN — never healthy — when a probe could not answer", () => {
    // The load-bearing case: four working probes and one silent one must not
    // render as 系统正常, because the silent one may be the important one.
    expect(overallHealth([probe("ok"), probe("ok"), probe("ok"), probe("unknown")])).toBe(
      "unknown",
    );
  });

  it("is unknown when there is nothing to report", () => {
    expect(overallHealth([])).toBe("unknown");
  });

  it("never labels an unknown or degraded system as healthy", () => {
    expect(HEALTH_LABEL.ok).toBe("系统正常");
    expect(HEALTH_LABEL.degraded).toBe("部分异常");
    expect(HEALTH_LABEL.unknown).toBe("状态未知");
  });
});

describe("agentStateFor", () => {
  it("uses only the three words the product promises", () => {
    expect(agentStateFor("CONNECTED")).toBe("ready");
    expect(agentStateFor("DEGRADED")).toBe("needs-login");
    expect(agentStateFor("UNVERIFIED")).toBe("needs-login");
    expect(agentStateFor("DISCONNECTED")).toBe("offline");
    expect(agentStateFor("UNAVAILABLE")).toBe("offline");
  });

  it("maps an unrecognised health to unknown, never to ready", () => {
    // A dead agent must not be able to render as a green 可用.
    expect(agentStateFor("SOMETHING_ELSE")).toBe("unknown");
    expect(agentStateFor("")).toBe("unknown");
    expect(agentStateFor("UNKNOWN")).toBe("unknown");
  });
});

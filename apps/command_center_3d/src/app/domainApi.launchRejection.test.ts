import { describe, it, expect } from "vitest";
import { humanizeLaunchRejection } from "./domainApi";

describe("humanizeLaunchRejection", () => {
  it("maps WORKBUDDY_SESSION_SCOPE_MISMATCH to readable text with both cwds", () => {
    const err = JSON.stringify({
      code: "WORKBUDDY_SESSION_SCOPE_MISMATCH",
      detail:
        "requested cwd='D:\\jev' has no live WorkBuddy session; the only live " +
        "session is scoped to cwd='C:\\Users\\Administrator\\WorkBuddy\\jev'",
      requested_cwd: "D:\\jev",
      captured_cwd: "C:\\Users\\Administrator\\WorkBuddy\\jev",
    });
    const out = humanizeLaunchRejection(err)!;
    expect(out).toContain("D:\\jev");
    expect(out).toContain("C:\\Users\\Administrator\\WorkBuddy\\jev");
    expect(out).toContain("原生启动");
    // The raw machine code must never leak to the operator.
    expect(out).not.toContain("WORKBUDDY_SESSION_SCOPE_MISMATCH");
  });

  it("maps WORKBUDDY_NATIVE_NOT_STARTED to a spawn-focused line", () => {
    const err = JSON.stringify({
      code: "WORKBUDDY_NATIVE_NOT_STARTED",
      detail: "spawn exited 1",
    });
    expect(humanizeLaunchRejection(err)).toContain("原生启动已尝试但未成功");
  });

  it("maps the no-session variants to a start-WorkBuddy hint", () => {
    expect(
      humanizeLaunchRejection(JSON.stringify({ code: "no_live_workbuddy_session" })),
    ).toContain("未发现任何活 WorkBuddy 会话");
    expect(
      humanizeLaunchRejection(JSON.stringify({ code: "no_workbuddy_session" })),
    ).toContain("没有任何 WorkBuddy 会话记录");
  });

  it("returns null for non-JSON, null, and unknown codes", () => {
    expect(humanizeLaunchRejection("plain text")).toBeNull();
    expect(humanizeLaunchRejection(null)).toBeNull();
    expect(
      humanizeLaunchRejection(JSON.stringify({ code: "SOMETHING_ELSE", detail: "x" })),
    ).toBeNull();
  });
});

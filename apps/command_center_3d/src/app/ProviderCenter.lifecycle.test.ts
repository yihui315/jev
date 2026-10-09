import { describe, expect, it } from "vitest";
import {
  initialLoadState,
  isActivelyLoading,
  loadReducer,
  type LoadAction,
  type LoadState,
} from "./ProviderCenter";

/**
 * The provider-list lifecycle, tested without a DOM.
 *
 * The product was stuck on 「正在读取 Provider…」 with a fetch that had already
 * resolved, because `cards === null` stood for not-started, loading AND failed
 * at once — so there was no state a resolved fetch could move to that differed
 * from loading. The reducer makes the invariant structural instead: `loading`
 * exists only between `begin` and one of `resolve` / `fail`, so no sequence of
 * actions can end in loading with no request behind it.
 */

const card = (id: string) => ({
  id,
  display_name: id,
  purpose: "",
  region: "cn",
  state: "READY",
  display_state: "CONNECTED",
  is_jev: false,
  base_url: "",
  default_model: "m",
  models: ["m"],
  plan_types: [],
});

const run = (actions: Parameters<typeof loadReducer>[1][]): LoadState =>
  actions.reduce(loadReducer, initialLoadState);

describe("loadReducer", () => {
  it("starts idle, not loading", () => {
    // A fresh mount must not claim to be loading: that is the state the product
    // was stuck in, and it is reached here only via an explicit `begin`.
    expect(initialLoadState.status).toBe("idle");
    expect(isActivelyLoading(initialLoadState)).toBe(false);
  });

  it("begin puts it in loading", () => {
    const s = run([{ type: "begin" }]);
    expect(s.status).toBe("loading");
    expect(isActivelyLoading(s)).toBe(true);
  });

  it("resolve reaches success and is no longer loading", () => {
    const s = run([{ type: "begin" }, { type: "resolve", cards: [card("minimax")] }]);
    expect(s.status).toBe("success");
    expect(isActivelyLoading(s)).toBe(false);
    expect(s.cards).toHaveLength(1);
    expect(s.error).toBeNull();
  });

  it("a failed response is error, never loading", () => {
    // The core regression: an HTTP failure used to leave the spinner up forever
    // because there was no error branch at all.
    const s = run([{ type: "begin" }, { type: "fail", error: "network down" }]);
    expect(s.status).toBe("error");
    expect(isActivelyLoading(s)).toBe(false);
    expect(s.error).toBe("network down");
  });

  it("a thrown error is also error, never loading", () => {
    const s = run([{ type: "begin" }, { type: "fail", error: "boom" }]);
    expect(s.status).toBe("error");
    expect(isActivelyLoading(s)).toBe(false);
  });

  it("a failed refresh keeps the cards the operator was reading", () => {
    // Blanking a populated list on a transient failure loses information the
    // operator already had.
    const s = run([
      { type: "begin" },
      { type: "resolve", cards: [card("minimax")] },
      { type: "begin" },
      { type: "fail", error: "timeout" },
    ]);
    expect(s.status).toBe("error");
    expect(s.cards).toHaveLength(1);
  });

  it("retry clears the previous error", () => {
    // Otherwise the error text from a failed attempt survives a successful retry.
    const s = run([
      { type: "begin" },
      { type: "fail", error: "boom" },
      { type: "begin" },
      { type: "resolve", cards: [card("deepseek")] },
    ]);
    expect(s.status).toBe("success");
    expect(s.error).toBeNull();
    expect(s.cards[0].id).toBe("deepseek");
  });

  it("loading cannot be reached without a begin", () => {
    // The structural form of the invariant: no action other than `begin`
    // produces `loading`, so a stale component cannot render a spinner.
    // Typed as `LoadAction[]` so the literals are checked against the union
    // rather than widened to `readonly []` by `as const`.
    const others: LoadAction[] = [
      { type: "resolve", cards: [] },
      { type: "fail", error: "x" },
    ];
    for (const action of others) {
      expect(loadReducer(initialLoadState, action).status).not.toBe("loading");
    }
  });

  it("an empty provider list is success, not an error", () => {
    // Zero providers is a real answer from the backend. Rendering it as a
    // failure would be a second way to misreport the server.
    const s = run([{ type: "begin" }, { type: "resolve", cards: [] }]);
    expect(s.status).toBe("success");
    expect(s.cards).toEqual([]);
    expect(s.error).toBeNull();
  });

  it("repeated retries do not accumulate state", () => {
    let s = initialLoadState;
    for (let i = 0; i < 5; i++) {
      s = loadReducer(s, { type: "begin" });
      s = loadReducer(s, { type: "resolve", cards: [card("minimax")] });
    }
    expect(s.cards).toHaveLength(1);
    expect(s.status).toBe("success");
  });
});

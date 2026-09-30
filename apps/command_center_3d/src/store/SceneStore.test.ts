/**
 * SceneStore transport lifecycle (STEP 5).
 *
 * WHY THIS FILE EXISTS
 * The stream lifecycle had NO test, and it shipped with two measured defects:
 *
 *  1. `RECONNECTING` was unreachable. `onerror` set it, then `startPolling` set
 *     `POLLING` in the same tick, so a dropped stream was indistinguishable from
 *     "polls were configured as the transport". The one state the operator is
 *     supposed to see during an outage never reached the screen, while the
 *     contract still says it does ("if it cannot recover we switch to polling").
 *
 *  2. The poller leaked. `onopen` restored `LIVE` but never cleared the 5 s
 *     interval, so a page that had blinked once kept issuing duplicate `load()`
 *     requests forever while displaying a green transport.
 *
 * Both are the same defect class this project keeps catching: the UI states a
 * fact it is not actually able to observe. So the assertions here are written
 * against the STATE and against FETCH COUNTS -- not against the presence of a
 * `set({connection: ...})` call, which is what the buggy version had.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SceneStore } from "./SceneStore";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static reset() {
    FakeEventSource.instances = [];
  }
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, Set<(ev: unknown) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, cb: (ev: unknown) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(cb);
  }
  close() {
    this.closed = true;
  }
  emitOpen() {
    this.onopen?.();
  }
  emitError() {
    this.onerror?.();
  }
  emit(type: string, payload: unknown) {
    for (const cb of this.listeners.get(type) ?? []) {
      cb({ data: JSON.stringify(payload) } as unknown as MessageEvent);
    }
  }
}

const BASE = "http://127.0.0.1:4779";

let fetchCalls = 0;

beforeEach(() => {
  fetchCalls = 0;
  FakeEventSource.reset();
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", FakeEventSource);
  // ok:false pushes load() onto the bridge path, which is enough: these tests
  // are about the TIMER and the STATE, not about what a scene document contains.
  vi.stubGlobal("fetch", vi.fn(async () => {
    fetchCalls += 1;
    return { ok: false, json: async () => ({}) } as unknown as Response;
  }));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("SceneStore SSE lifecycle", () => {
  it("opens the product's own stream route", () => {
    const store = new SceneStore();
    store.connectStream(BASE);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toBe(`${BASE}/api/v1/events/stream`);
    expect(store.getSnapshot().connection).toBe("CONNECTING");
  });

  it("reports LIVE once the stream opens", () => {
    const store = new SceneStore();
    store.connectStream(BASE);
    FakeEventSource.instances[0].emitOpen();
    expect(store.getSnapshot().connection).toBe("LIVE");
  });

  it("shows RECONNECTING while the stream is down -- not LIVE, not POLLING", () => {
    // The falsifier for defect 1: an outage must be VISIBLE as an outage. A
    // store that reports POLLING here has collapsed "the stream died" into "the
    // transport was configured differently", and a store that reports LIVE is
    // simply lying to the operator.
    const store = new SceneStore();
    store.connectStream(BASE);
    FakeEventSource.instances[0].emitOpen();
    FakeEventSource.instances[0].emitError();
    expect(store.getSnapshot().connection).toBe("RECONNECTING");
  });

  it("keeps polling underneath the outage so the world does not freeze", () => {
    const store = new SceneStore();
    store.connectStream(BASE);
    FakeEventSource.instances[0].emitError();
    const before = fetchCalls;
    vi.advanceTimersByTime(5000);
    expect(fetchCalls).toBeGreaterThan(before);
    // ...and the operator still sees the degraded state, not LIVE.
    expect(store.getSnapshot().connection).toBe("RECONNECTING");
  });

  it("stops the poller when the stream recovers", () => {
    // The falsifier for defect 2: recovery must not leave a timer behind.
    const store = new SceneStore();
    store.connectStream(BASE);
    const es = FakeEventSource.instances[0];
    es.emitError();
    vi.advanceTimersByTime(5000);
    expect(fetchCalls).toBeGreaterThan(0);

    es.emitOpen();
    expect(store.getSnapshot().connection).toBe("LIVE");
    const afterRecovery = fetchCalls;
    vi.advanceTimersByTime(60000); // 12 poll intervals
    expect(fetchCalls).toBe(afterRecovery);
  });

  it("falls back to POLLING only when no stream can be created at all", () => {
    vi.stubGlobal("EventSource", class {
      constructor() {
        throw new Error("EventSource is not available");
      }
    });
    const store = new SceneStore();
    store.connectStream(BASE);
    expect(store.getSnapshot().connection).toBe("POLLING");
    const before = fetchCalls;
    vi.advanceTimersByTime(5000);
    expect(fetchCalls).toBeGreaterThan(before);
  });

  it("disconnect stops everything and reports OFFLINE", () => {
    const store = new SceneStore();
    store.connectStream(BASE);
    const es = FakeEventSource.instances[0];
    es.emitError();
    vi.advanceTimersByTime(5000);
    store.disconnect();
    expect(es.closed).toBe(true);
    expect(store.getSnapshot().connection).toBe("OFFLINE");
    const after = fetchCalls;
    vi.advanceTimersByTime(60000);
    expect(fetchCalls).toBe(after);
  });

  it("applies a typed frame as a timeline event and survives a malformed one", () => {
    const store = new SceneStore();
    store.connectStream(BASE);
    const es = FakeEventSource.instances[0];
    es.emit("session.created", {
      event_id: "ev-1",
      data: { session_id: "s-1", agent: "workbuddy" },
    });
    const events = store.getSnapshot().events;
    expect(events[0]?.eventId).toBe("ev-1");
    expect(events[0]?.type).toBe("session.created");

    // A malformed frame must not throw and must not kill the connection.
    expect(() => es.emit("session.created", "not-an-object")).not.toThrow();
    expect(store.getSnapshot().connection).toBe("CONNECTING");
  });
});

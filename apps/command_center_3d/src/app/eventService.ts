/**
 * eventService — the live event feed for ONE task.
 *
 * WHY A SEPARATE CHANNEL
 * ----------------------
 * The app already holds a global SSE connection (`SceneStore`) for the 3D
 * scene. That stream carries engine telemetry, not the per-task narrative a
 * person actually reads. So this module opens its own EventSource per task view
 * and translates raw frames into plain-language activity lines.
 *
 * NO FAKE TIMELINE — THE RULE THIS MODULE EXISTS TO ENFORCE
 * ---------------------------------------------------------
 * The spec forbids inventing a progress narrative. So:
 *
 *  - Every line here originates from a real backend event or a real
 *    `missionTimeline` row. There is no code path that manufactures a step.
 *  - A frame we cannot parse is DROPPED, not smoothed over. A gap in the feed
 *    must be visible as a gap, because a plausible-looking invented line is
 *    worse than an empty list.
 *  - Reconnection is explicit in the returned state, so the UI can say
 *    「连接已断开，正在重连」 instead of pretending the feed is current.
 *  - Timestamps come from the server. The browser clock is never used to label
 *    an event, only to measure how stale the connection is.
 */
import { useCallback, useEffect, useRef, useState } from "react";

/** The connection state a task view can be in. */
export type FeedState = "connecting" | "live" | "reconnecting" | "offline";

/** One readable activity line. */
export interface ActivityLine {
  /** Stable key for React; derived from the server's own identity fields. */
  id: string;
  /** Unix seconds as reported by the server. */
  at: number;
  /** The plain-language text. */
  text: string;
  /** Optional second line: the raw reason string, kept for the expert view. */
  detail?: string;
}

export interface ActivityFeed {
  lines: ActivityLine[];
  state: FeedState;
  /** How many raw frames arrived, including ones we could not read. */
  received: number;
  /** The reason the stream is not live, when it is not. */
  error: string | null;
  /** Drop the accumulated lines (used when switching tasks). */
  reset: () => void;
}

/** `HH:MM:SS` in the viewer's locale, from a server Unix-seconds stamp. */
export function formatClock(at: number): string {
  if (!Number.isFinite(at) || at <= 0) return "--:--:--";
  const d = new Date(at * 1000);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString(undefined, {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/**
 * Turn a backend status transition into a sentence a person can read.
 *
 * `from` → `to` is the server's own record, so the line is a fact about the
 * task, not a guess about progress. Unknown transitions still produce a line
 * (labelled with the raw status) because hiding a real event is worse than
 * showing a technical one.
 */
export function describeTransition(
  from: string | null,
  to: string,
  reason: string,
): string {
  const FROM: Record<string, string> = {
    DRAFT: "任务已创建",
    READY: "准备就绪",
    STARTING: "开始执行",
    RUNNING: "正在执行",
    VERIFYING: "正在验证",
    COMPLETED: "已完成",
    FAILED: "执行失败",
    CANCELLED: "已取消",
    PAUSED: "已暂停",
    NEEDS_ATTENTION: "等待你的确认",
  };
  if (reason && reason.trim() && !/^task started|^launching/.test(reason)) {
    return reason.trim();
  }
  if (to === "RUNNING" && from && from !== "RUNNING") return "任务开始执行";
  return FROM[to] ?? `状态变更为 ${to}`;
}

/**
 * Subscribe to the live event stream while a task view is open.
 *
 * `staticLines` (the mission's own transition history) is accepted so the feed
 * has content the moment it opens — the stream only carries what happens from
 * now on, and a task that finished an hour ago has no live frames to show.
 */
export function useActivityFeed(
  apiBase: string,
  missionId: string | null,
  staticLines: ActivityLine[],
): ActivityFeed {
  const [lines, setLines] = useState<ActivityLine[]>(staticLines);
  const [state, setState] = useState<FeedState>("connecting");
  const [received, setReceived] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef<number | null>(null);
  const attemptRef = useRef(0);

  // Static history is the baseline; a new task resets the whole feed.
  useEffect(() => {
    setLines(staticLines);
    setReceived(0);
    attemptRef.current = 0;
  }, [missionId, staticLines]);

  const close = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    if (retryRef.current !== null) {
      window.clearTimeout(retryRef.current);
      retryRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!missionId) {
      setState("offline");
      return;
    }

    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      setState(attemptRef.current === 0 ? "connecting" : "reconnecting");

      let es: EventSource;
      try {
        es = new EventSource(`${apiBase}/api/v1/events/stream`);
      } catch (e) {
        setState("offline");
        setError(e instanceof Error ? e.message : String(e));
        return;
      }
      esRef.current = es;

      es.onopen = () => {
        if (cancelled) return;
        attemptRef.current = 0;
        setState("live");
        setError(null);
      };

      // The stream is typed per event name, so every listener is registered for
      // the names the server actually publishes. `message` is also handled for a
      // default-typed frame.
      const handle = (raw: string, kind: string) => {
        if (cancelled) return;
        setReceived((n) => n + 1);
        let frame: Record<string, unknown>;
        try {
          frame = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          // An unparseable frame is dropped, not guessed at. See the module note.
          return;
        }
        const data = (frame.data ?? frame) as Record<string, unknown>;
        const mid = typeof data.mission_id === "string" ? data.mission_id : null;
        // Only this task's frames belong in this feed.
        if (mid && mid !== missionId) return;

        const to = typeof data.to_status === "string" ? data.to_status : null;
        const from = typeof data.from_status === "string" ? data.from_status : null;
        const reason = typeof data.reason === "string" ? data.reason : "";
        const at = typeof data.timestamp === "number" ? data.timestamp : 0;

        if (!to) return;
        const id = `${at}-${from ?? "x"}-${to}-${String(frame.event_id ?? kind)}`;
        setLines((prev) => {
          if (prev.some((l) => l.id === id)) return prev;
          return [
            ...prev,
            { id, at, text: describeTransition(from, to, reason), detail: reason || undefined },
          ];
        });
      };

      for (const type of [
        "mission.status",
        "mission",
        "aep",
        "agent",
        "tool",
        "artifact",
        "message",
      ]) {
        es.addEventListener(type, (ev) => handle((ev as MessageEvent).data, type));
      }
      es.onmessage = (ev) => handle(ev.data, "message");

      es.onerror = () => {
        if (cancelled) return;
        es.close();
        esRef.current = null;
        setState("reconnecting");
        // Bounded backoff. The UI is already showing 「正在重连」, so this only
        // has to be patient, not clever.
        attemptRef.current += 1;
        const wait = Math.min(1000 * 2 ** Math.min(attemptRef.current, 4), 15000);
        retryRef.current = window.setTimeout(connect, wait);
      };
    };

    connect();
    return () => {
      cancelled = true;
      close();
    };
  }, [apiBase, missionId, close]);

  return {
    lines,
    state,
    received,
    error,
    reset: () => setLines(staticLines),
  };
}

/**
 * Build the initial activity lines from a mission's transition history.
 *
 * The domain API exposes `GET …/missions/{id}/timeline`, which is the server's
 * own record of what happened. That — and nothing invented — is what a task page
 * shows before any live frame arrives.
 */
export function linesFromTimeline(
  entries: Array<{ seq: number; timestamp: number; from_status: string | null; to_status: string; reason: string }>,
): ActivityLine[] {
  return entries.map((e) => ({
    id: `tl-${e.seq}`,
    at: e.timestamp,
    text: describeTransition(e.from_status, e.to_status, e.reason),
    detail: e.reason || undefined,
  }));
}

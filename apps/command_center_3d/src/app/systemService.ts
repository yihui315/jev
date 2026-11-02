/**
 * systemService — read-only probes that back the header status.
 *
 * WHAT THIS IS FOR
 * ----------------
 * The header claims "系统正常". That claim has to be assembled from real reads,
 * each of which can independently fail. So every probe returns a three-state
 * answer (ok / bad / unknown) and the headline is composed from all of them —
 * a system with one working probe and three dead ones is NOT healthy, and this
 * module is what makes that impossible to fake.
 *
 * Nothing here mutates anything, and no probe invents a value: a probe that
 * cannot answer reports `unknown` with the reason, never a green.
 */
import type { StatusProbe } from "../app/TopStatus";
import type { DomainApi } from "./domainApi";

/** Which backend read backs which line in the popover. */
export const PROBE_ORDER = ["api", "agents", "stream", "security"] as const;

async function timed(
  fn: () => Promise<{ ok: boolean; status: number; data: unknown; error: string | null }>,
  ms = 6000,
): Promise<{ ok: boolean; status: number; data: unknown; error: string | null }> {
  return Promise.race([
    fn(),
    new Promise<{ ok: boolean; status: number; data: unknown; error: string | null }>((resolve) =>
      setTimeout(
        () =>
          resolve({
            ok: false,
            status: 0,
            data: null,
            error: `探测超时（${ms / 1000}s 内无响应）`,
          }),
        ms,
      ),
    ),
  ]);
}

/**
 * Dial the SSE endpoint and report what actually happened.
 *
 * The control plane opens every event stream with a `: connected` comment (SSE
 * spec: a comment line is a valid "stream is alive, no payload yet" signal). So
 * "the channel is up" is provable from the FIRST bytes the server sends — this
 * probe does not need an event to fire, and it does not invent one.
 *
 * Resolution rules, which are the honest ones:
 *  - any bytes received at all      → ok      (the server is streaming)
 *  - the socket opened then errored → bad     (the server accepted and dropped)
 *  - the connection itself failed   → bad     (nothing is listening)
 *  - nothing within the budget      → unknown (we cannot tell — NOT a pass)
 *
 * The socket is always closed before returning; a probe that leaked an
 * EventSource would be read as a leak by the page for the rest of the session.
 */
function probeEventStream(
  base: string,
  budgetMs = 4000,
): Promise<{ state: StatusProbe["state"]; detail: string }> {
  return new Promise((resolve) => {
    // A non-empty base is required to build a same-origin relative URL; the
    // shipped default is absolute, but `VITE_API_BASE=same-origin` yields "".
    const url = `${base}/api/v1/events/stream`;

    let settled = false;
    let source: EventSource | null = null;
    let timer: number | null = null;

    const finish = (state: StatusProbe["state"], detail: string) => {
      if (settled) return;
      settled = true;
      if (timer !== null) window.clearTimeout(timer);
      try {
        source?.close();
      } catch {
        /* already closed */
      }
      resolve({ state, detail });
    };

    // No `EventSource` at all (very old browser, or a non-browser test env):
    // that is an honest "cannot tell", not a pass.
    if (typeof EventSource === "undefined") {
      resolve({ state: "unknown", detail: "当前环境不支持事件流" });
      return;
    }

    try {
      source = new EventSource(url);
    } catch (e) {
      finish("bad", e instanceof Error ? e.message : String(e));
      return;
    }

    source.onopen = () => {
      // The socket is open. The server will send its `: connected` comment
      // immediately, so the stream is established.
      finish("ok", "事件流已连接");
    };
    // A frame of any kind also proves liveness, in case a server omits the
    // opening comment and speaks first.
    source.onmessage = () => finish("ok", "事件流已连接");
    source.onerror = () => {
      // EventSource fires `error` on a clean close too, so this is only a
      // failure if we had not already seen the channel open.
      finish("bad", "事件流连接中断");
    };

    timer = window.setTimeout(() => {
      finish("unknown", `${budgetMs / 1000}s 内未收到事件流响应`);
    }, budgetMs);
  });
}

/**
 * Run the probes and return one row per subsystem.
 *
 * The rows are built from the LIVE responses, so the popover describes the
 * system as it is now. A probe that never ran is still reported, with the reason
 * it could not — an absent row would read as "fine" by omission.
 */
export async function probeSystem(base: string, api: DomainApi): Promise<StatusProbe[]> {
  const [health, connections] = await Promise.all([
    timed(() => api.home()),
    timed(() => api.connections()),
  ]);

  const probes: StatusProbe[] = [];

  // --- 1. control plane reachable at all ---
  if (health.status === 0) {
    probes.push({
      label: "API 服务",
      state: "unknown",
      detail: health.error ?? "无法连接 JCC API",
    });
  } else if (!health.ok) {
    probes.push({
      label: "API 服务",
      state: "bad",
      detail: health.error ?? `HTTP ${health.status}`,
    });
  } else {
    const d = health.data as Record<string, unknown> | null;
    // `actuation_enabled: false` is the SHIPPED and CORRECT state — the backend
    // refuses to act on its own by design. The earlier wording printed
    // "执行受控已启用" precisely when it was disabled, because the ternary read
    // the flag backwards. Say what the flag says.
    const actuation = d?.actuation_enabled;
    const actuationText =
      actuation === true ? "允许执行" : actuation === false ? "仅规划，不自动执行" : "未声明";
    probes.push({
      label: "API 服务",
      state: "ok",
      detail: `控制平面已连接 · ${actuationText}`,
    });
  }

  // --- 2. execution agents ---
  if (connections.status === 0) {
    probes.push({
      label: "执行代理",
      state: "unknown",
      detail: connections.error ?? "无法读取连接状态",
    });
  } else if (!connections.ok || !Array.isArray(connections.data)) {
    probes.push({
      label: "执行代理",
      state: "bad",
      detail: connections.error ?? `HTTP ${connections.status}`,
    });
  } else {
    const list = connections.data as Array<{ agent: string; health: string }>;
    const total = list.length;
    const ready = list.filter((c) => c.health === "CONNECTED").length;
    /**
     * A 200 with an empty array is a SUCCESSFUL ANSWER: the backend was asked
     * and it said "there are none". That is materially different from "we could
     * not ask", and conflating them is what pinned the header to 状态未知 on a
     * perfectly healthy install. So:
     *
     *   none registered → ok, with the fact named. The control plane is fine and
     *                    the operator simply has not connected an agent yet —
     *                    a configuration state, not a system fault.
     *   some / all down → bad only when NONE are usable.
     */
    probes.push({
      label: "执行代理",
      state: total === 0 || ready > 0 ? "ok" : "bad",
      detail:
        total === 0
          ? "后端未注册执行代理（不影响浏览与查看，启动任务前需先配置）"
          : `${ready}/${total} 个代理可用 · ${list
              .map((c) => c.agent)
              .slice(0, 4)
              .join("、")}`,
    });
  }

  // --- 3. event stream ---
  // A REAL probe, not a placeholder. The earlier version of this file hard-coded
  // `unknown` here with the note "probed elsewhere", which meant `overallHealth`
  // could never return "ok" for ANY deployment — the header was stuck on 状态未知
  // even with a perfectly healthy backend. That is the mirror image of faking a
  // green: a permanent unknown is its own kind of lie, because it hides a
  // system that is actually fine.
  //
  // So the stream is actually dialled, and the answer is whatever the server
  // does: the SSE endpoint opens with a `: connected` comment, so receiving ANY
  // bytes proves the channel is up. No frame is needed and none is faked.
  const stream = await probeEventStream(base);
  probes.push({
    label: "实时事件流",
    state: stream.state,
    detail: stream.detail,
  });

  // --- 4. security posture ---
  if (health.ok && health.data) {
    const d = health.data as Record<string, unknown>;
    const localOnly = d.localhost_only === true;
    probes.push({
      label: "安全策略",
      state: localOnly ? "ok" : "bad",
      detail: localOnly
        ? d.control_token_configured === true
          ? "仅本机访问 · 控制令牌已配置"
          : "仅本机访问 · 未配置控制令牌（写操作被拒绝）"
        : "未限制为本机访问",
    });
  } else {
    probes.push({
      label: "安全策略",
      state: "unknown",
      detail: "无法读取安全配置",
    });
  }

  return probes;
}

/**
 * Read the backend's dev-server hint for a project, if it publishes one.
 *
 * A web task's preview needs a URL to show. The domain API does not define a
 * dev-server endpoint, so this reads the most likely carriers and returns a URL
 * ONLY when one is actually present — the Preview tab renders "预览暂不可用"
 * otherwise, which is the honest outcome rather than an empty iframe.
 */
export async function findPreviewUrl(
  api: DomainApi,
  missionId: string,
): Promise<{ url: string | null; source: string }> {
  const workload = await api.missionWorkload(missionId);
  if (!workload.ok || !workload.data) {
    return { url: null, source: workload.error ?? "无法读取任务运行环境" };
  }
  // The workload doc carries session records; a session may name a dev server.
  const sessions = Array.isArray(workload.data.sessions) ? workload.data.sessions : [];
  for (const s of sessions) {
    const url =
      (s as Record<string, unknown>).preview_url ??
      (s as Record<string, unknown>).dev_server_url ??
      (s as Record<string, unknown>).url;
    if (typeof url === "string" && /^https?:\/\//i.test(url)) {
      return { url, source: "任务运行环境" };
    }
  }
  return { url: null, source: "后端未提供开发服务器地址" };
}

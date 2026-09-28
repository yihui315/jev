import { useEffect, useState, useCallback } from "react";
import { AgentCard } from "../components/AgentCard";
import { JevProviderCard } from "../components/JevProviderCard";
import { OnboardingWizard } from "../components/OnboardingWizard";

// The connection centre reads through the same API base as the shell. It is
// passed in via props from App so there is exactly ONE base in the product;
// the module-level fallback exists only for a standalone import in tests.
const FALLBACK_API_BASE: string =
  (import.meta as unknown as { env?: { VITE_API_BASE?: string } }).env?.VITE_API_BASE ||
  "http://127.0.0.1:4779";

// Read-only API base used when an unauthenticated GET suffices.
async function httpGet(path: string, base: string = FALLBACK_API_BASE):
    Promise<{ ok: boolean; status: number; body: any }> {
  try {
    const r = await fetch(base + path, { method: "GET" });
    let body: any = null;
    try { body = await r.json(); } catch { body = null; }
    return { ok: r.ok, status: r.status, body };
  } catch (e) {
    return { ok: false, status: 0, body: { error: String(e) } };
  }
}

// Token-gated mutating call. The token is held in memory (never persisted) and
// supplied by the operator through the top-bar control-token field.
async function httpPost(path: string, body: any, token: string | null,
                        base: string = FALLBACK_API_BASE):
    Promise<{ ok: boolean; status: number; body: any }> {
  try {
    const r = await fetch(base + path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { "X-JCC-Token": token } : {}),
      },
      body: JSON.stringify(body),
    });
    let b: any = null;
    try { b = await r.json(); } catch { b = null; }
    return { ok: r.ok, status: r.status, body: b };
  } catch (e) {
    return { ok: false, status: 0, body: { error: String(e) } };
  }
}

export interface ConnectionSnapshot {
  agent: string;
  display_name: string;
  connection_type: string;
  connection_state: string;
  telemetry_state: string;
  aep_state: string;
  adapter_state: string;
  control_capability: string;
  last_event_at: number | null;
  last_event_summary: string | null;
  last_session_id: string | null;
  version: string | null;
  config: Record<string, any>;
  last_tested_at: number | null;
  last_test_steps: any[];
  notes: string[];
}

const STATE_COLORS: Record<string, string> = {
  CONNECTED: "#7ee2a0",
  DEGRADED: "#fbbf24",
  DISCONNECTED: "#60a5fa",
  UNVERIFIED: "#9fb3c8",
  UNAVAILABLE: "#f87171",
  // UNKNOWN is NEVER green — that's the spec's hard rule.
  UNKNOWN: "#6b7c8f",
};

export function stateColor(state: string): string {
  return STATE_COLORS[state] ?? "#6b7c8f";
}

const SUPPORTED_CONNECTION_TYPES = ["LOCAL_CLI", "LOCAL_PROCESS", "API", "MCP"];

const ORDER = ["workbuddy", "claude", "codex", "hermes"];

export function ConnectionsView({ scene, apiBase, controlToken }: {
  scene: any; apiBase: string; controlToken: string | null;
}) {
  const [snapshots, setSnapshots] = useState<ConnectionSnapshot[]>([]);
  const [jevSnap, setJevSnap] = useState<ConnectionSnapshot | null>(null);
  const [onboarding, setOnboarding] = useState<any>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLastError(null);
    const [connR, jevR, onbR] = await Promise.all([
      httpGet("/api/connections", apiBase),
      httpGet("/api/connections/jev", apiBase),
      httpGet("/api/onboarding/state", apiBase),
    ]);
    if (!connR.ok) { setLastError("connections: HTTP " + connR.status); return; }
    const all: ConnectionSnapshot[] = connR.body?.agents ?? [];
    setSnapshots(all.filter(a => ORDER.includes(a.agent)));
    setJevSnap(jevR.ok ? jevR.body : null);
    setOnboarding(onbR.ok ? onbR.body : null);
  }, [apiBase]);

  useEffect(() => { void refresh(); }, [refresh]);

  const runAction = useCallback(async (agent: string, action: string, payload: any) => {
    setBusy(`${agent}/${action}`);
    setLastError(null);
    let r;
    if (action === "test") r = await httpPost(`/api/connections/${agent}/test`, payload ?? {}, controlToken, apiBase);
    else if (action === "configure") r = await httpPost(`/api/connections/${agent}/configure`, payload ?? {}, controlToken, apiBase);
    else if (action === "preview-disconnect") r = await httpPost(`/api/connections/${agent}/preview-disconnect`, payload ?? {}, controlToken, apiBase);
    else if (action === "disconnect") r = await httpPost(`/api/connections/${agent}/disconnect`, payload ?? {}, controlToken, apiBase);
    else if (action === "sessions") r = await httpPost(`/api/connections/${agent}/sessions`, payload ?? {}, controlToken, apiBase);
    else if (action === "validate-jev") r = await httpPost(`/api/connections/jev/validate`, payload ?? {}, controlToken, apiBase);
    else if (action === "activate-jev") r = await httpPost(`/api/connections/jev/activate`, payload ?? {}, controlToken, apiBase);
    else if (action === "onboard-advance") r = await httpPost(`/api/onboarding/advance`, payload ?? {}, controlToken, apiBase);
    else { setBusy(null); return; }
    if (!r.ok) {
      setLastError(`${agent} ${action}: HTTP ${r.status} ${JSON.stringify(r.body).slice(0, 240)}`);
    }
    setBusy(null);
    await refresh();
  }, [controlToken, refresh, apiBase]);

  return (
    <div style={{
      flex: 1, minHeight: 0, overflowY: "auto", padding: 16,
      color: "#d6dde6", background: "#0e1116",
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 20, color: "#7ee2a0" }}>AI CONNECTION CENTER</h1>
          <div style={{ color: "#6b7c8f", fontSize: 11, marginTop: 4 }}>
            v1.3 · loopback-only · API {apiBase || "same-origin"} · control token {controlToken ? "✓ set" : "✗ not set — Configure / Test / Disconnect will fail-closed"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button onClick={() => void refresh()}
            style={{ background: "#1f2a36", color: "#9fb3c8", border: "1px solid #2a313c", borderRadius: 4, padding: "4px 10px", cursor: "pointer", font: "inherit" }}>
            ↻ Refresh
          </button>
        </div>
      </div>

      {lastError && (
        <div style={{ padding: 10, marginBottom: 12, background: "#2a1a1a", border: "1px solid #5a2a2a", color: "#f87171", fontSize: 12, borderRadius: 4 }}>
          ⚠ {lastError}
        </div>
      )}

      {onboarding && (
        <OnboardingWizard onboarding={onboarding} onAdvance={(step) => runAction("onboarding", "onboard-advance", { step })} />
      )}

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))",
        gap: 14,
      }}>
        {snapshots.map(s => (
          <AgentCard
            key={s.agent}
            snap={s}
            supportedTypes={SUPPORTED_CONNECTION_TYPES}
            stateColor={stateColor(s.connection_state)}
            busy={busy === `${s.agent}/test` || busy === `${s.agent}/configure`
                  || busy === `${s.agent}/preview-disconnect` || busy === `${s.agent}/disconnect`
                  || busy === `${s.agent}/sessions`}
            onTest={(payload) => runAction(s.agent, "test", { config: payload })}
            onConfigure={(payload) => runAction(s.agent, "configure", { config: payload })}
            onPreviewDisconnect={() => runAction(s.agent, "preview-disconnect", {})}
            onDisconnect={(previewToken) => runAction(s.agent, "disconnect", { preview_token: previewToken })}
            onListSessions={() => runAction(s.agent, "sessions", {})}
          />
        ))}
        {jevSnap && (
          <JevProviderCard
            snap={jevSnap}
            stateColor={stateColor(jevSnap.connection_state)}
            busy={busy === "jev/validate-jev" || busy === "jev/activate-jev"}
            controlToken={controlToken}
            onValidate={(payload) => runAction("jev", "validate-jev", payload)}
            onActivate={() => runAction("jev", "activate-jev", {})}
          />
        )}
      </div>

      <div style={{ marginTop: 24, fontSize: 11, color: "#6b7c8f" }}>
        Honest rules baked into this surface (v1.2.1):
        <ul style={{ marginTop: 6, paddingLeft: 18 }}>
          <li><b>UNKNOWN</b> is dim grey — never green.</li>
          <li>Secrets are NEVER sent to the browser, NEVER stored in DB, NEVER logged.</li>
          <li>Disconnect requires a Preview first — it never kills a host process.</li>
          <li>Jev activate is gated on (endpoint + request_schema + credential name) all present AND the credential env var present in the host env.</li>
          <li>Validate makes NO paid request; only the env-var presence + URL format are checked.</li>
          <li>WorkBuddy is the host IDE/CLI — auto-launch is NOT possible. Real launch command shown.</li>
        </ul>
      </div>
    </div>
  );
}
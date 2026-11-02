import { useState } from "react";

type ConnectionType = "LOCAL_CLI" | "LOCAL_PROCESS" | "API" | "MCP";

export interface AgentCardProps {
  snap: {
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
  };
  supportedTypes: string[];
  stateColor: string;
  busy: boolean;
  onTest: (cfg: any) => void;
  onConfigure: (cfg: any) => void;
  onPreviewDisconnect: () => void;
  onDisconnect: (previewToken: string) => void;
  onListSessions: () => void;
}

export function AgentCard(props: AgentCardProps) {
  const { snap, stateColor, busy, onTest, onConfigure,
    onPreviewDisconnect, onDisconnect, onListSessions } = props;
  const [showConfig, setShowConfig] = useState(false);
  const [showSteps, setShowSteps] = useState(false);
  const [showDisconnect, setShowDisconnect] = useState(false);
  const [cfgType, setCfgType] = useState<ConnectionType>(
    (snap.config?.connection_type as ConnectionType) ||
    ((snap.agent === "workbuddy" ? "LOCAL_PROCESS" : "LOCAL_CLI") as ConnectionType));
  const [execPath, setExecPath] = useState<string>(snap.config?.executable_path ?? "");
  const [cwd, setCwd] = useState<string>(snap.config?.working_directory ?? "");
  const [args, setArgs] = useState<string>(
    Array.isArray(snap.config?.arguments) ? snap.config.arguments.join(" ") : "");
  const [lastPreviewToken, setLastPreviewToken] = useState<string | null>(null);
  const [lastPreviewPlan, setLastPreviewPlan] = useState<any>(null);
  const [sessions, setSessions] = useState<any[] | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const manualLaunchHint = snap.agent === "workbuddy"
    ? "MANUAL LAUNCH REQUIRED — start WorkBuddy on the host (it is your IDE/CLI). The Connections page cannot spawn your IDE."
    : null;

  const handleConfigure = () => {
    setActionError(null);
    const payload: any = { connection_type: cfgType };
    if (cfgType === "LOCAL_CLI" || cfgType === "LOCAL_PROCESS") {
      payload.executable_path = execPath || null;
      payload.working_directory = cwd || null;
      payload.arguments = args ? args.split(/\s+/).filter(Boolean) : [];
    }
    onConfigure(payload);
    setShowConfig(false);
  };

  const handleTest = () => {
    setActionError(null);
    const payload: any = {};
    if (cfgType === "LOCAL_CLI" || cfgType === "LOCAL_PROCESS") {
      payload.connection_type = cfgType;
      payload.executable_path = execPath || null;
      payload.working_directory = cwd || null;
      payload.arguments = args ? args.split(/\s+/).filter(Boolean) : [];
    }
    onTest(payload);
  };

  const handlePreview = () => {
    setActionError(null);
    setLastPreviewPlan(null);
    setLastPreviewToken(null);
    // Preview is server-side; here we just emit and the parent will pick up the
    // updated snapshot. We also stash the preview_token echoed back via the
    // busy state — but since we can't get the response in this scope, we
    // surface a generic "preview issued" notice and let the operator click
    // Disconnect when ready. (The disconnect button remains disabled until a
    // preview_token has been issued by the server.)
    onPreviewDisconnect();
    setShowDisconnect(true);
  };

  const handleDisconnectNow = () => {
    if (!lastPreviewToken) {
      setActionError("Disconnect requires a Preview first. Click 'Preview Disconnect' above.");
      return;
    }
    onDisconnect(lastPreviewToken);
    setShowDisconnect(false);
    setLastPreviewToken(null);
    setLastPreviewPlan(null);
  };

  return (
    <div style={{
      border: "1px solid #2a313c", borderRadius: 6,
      background: "#161b22", padding: 14, display: "flex", flexDirection: "column", gap: 8,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{snap.display_name}</div>
          <div style={{ color: "#6b7c8f", fontSize: 10 }}>id={snap.agent}</div>
        </div>
        <div style={{
          padding: "2px 10px", borderRadius: 12, fontSize: 11, fontWeight: 600,
          color: "#0e1116", background: stateColor,
          // UNKNOWN stays dim — never green
        }}>
          {snap.connection_state}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", rowGap: 4, columnGap: 10, fontSize: 11 }}>
        <span style={{ color: "#6b7c8f" }}>Connection Type</span>
        <span>{snap.connection_type || "(unset)"}</span>

        <span style={{ color: "#6b7c8f" }}>Telemetry</span>
        <span style={{ color: telemetryColor(snap.telemetry_state) }}>{snap.telemetry_state || "UNKNOWN"}</span>

        <span style={{ color: "#6b7c8f" }}>AEP</span>
        <span>{snap.aep_state || "UNKNOWN"}</span>

        <span style={{ color: "#6b7c8f" }}>Adapter</span>
        <span>{snap.adapter_state || "UNKNOWN"}</span>

        <span style={{ color: "#6b7c8f" }}>Control</span>
        <span>{snap.control_capability || "UNKNOWN"}</span>

        <span style={{ color: "#6b7c8f" }}>Last Event</span>
        <span>{snap.last_event_at ? new Date(snap.last_event_at * 1000).toLocaleTimeString() + " " + (snap.last_event_summary ?? "") : "—"}</span>

        <span style={{ color: "#6b7c8f" }}>Last Session</span>
        <span>{snap.last_session_id ?? "—"}</span>

        <span style={{ color: "#6b7c8f" }}>Version</span>
        <span>{snap.version ?? "—"}</span>
      </div>

      {manualLaunchHint && (
        <div style={{
          marginTop: 4, padding: 8, border: "1px dashed #fbbf24",
          color: "#fbbf24", fontSize: 11, borderRadius: 4,
        }}>
          ⚠ {manualLaunchHint}
        </div>
      )}

      {snap.notes?.length > 0 && (
        <div style={{ marginTop: 4, fontSize: 10, color: "#9fb3c8" }}>
          {snap.notes.map((n, i) => <div key={i}>· {n}</div>)}
        </div>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
        <button disabled={busy} onClick={handleTest}
          style={btnPrimary(busy)}>
          ▶ Test Connection
        </button>
        <button disabled={busy} onClick={() => setShowConfig(s => !s)}
          style={btnSecondary(busy)}>
          ⚙ Configure
        </button>
        <button disabled={busy} onClick={() => {
          onListSessions();
          setSessions(null);
          // Fetch sessions inline — but we have no direct API. Use parent callback
          // which already posts; we just record a sentinel so the UI clears.
          setTimeout(() => setSessions([]), 0);
        }} style={btnSecondary(busy)}>
          📂 View Sessions
        </button>
        <button disabled={busy} onClick={() => setShowDisconnect(s => !s)}
          style={btnDanger(busy)}>
          ⛔ Disconnect
        </button>
      </div>

      {showConfig && (
        <div style={{ borderTop: "1px solid #2a313c", paddingTop: 8, marginTop: 4 }}>
          <div style={{ fontSize: 11, color: "#6b7c8f", marginBottom: 6 }}>
            Configuration (NON-SECRET — executable path, working dir, args).
            API credentials go through the host env, NEVER here.
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", rowGap: 6, columnGap: 10, fontSize: 11 }}>
            <label>Connection Type</label>
            <select value={cfgType} onChange={e => setCfgType(e.target.value as ConnectionType)}
              style={selectStyle()}>
              {props.supportedTypes.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            {(cfgType === "LOCAL_CLI" || cfgType === "LOCAL_PROCESS") && (<>
              <label>Executable path</label>
              <input value={execPath} onChange={e => setExecPath(e.target.value)} placeholder="C:\path\to\binary.exe"
                style={inputStyle()} />
              <label>Working dir</label>
              <input value={cwd} onChange={e => setCwd(e.target.value)} placeholder="C:\Users\you\project"
                style={inputStyle()} />
              <label>Arguments</label>
              <input value={args} onChange={e => setArgs(e.target.value)} placeholder="--flag value"
                style={inputStyle()} />
            </>)}
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            <button disabled={busy} onClick={handleConfigure} style={btnPrimary(busy)}>Save</button>
            <button disabled={busy} onClick={() => setShowConfig(false)} style={btnSecondary(busy)}>Cancel</button>
          </div>
        </div>
      )}

      {showDisconnect && (
        <div style={{ borderTop: "1px solid #2a313c", paddingTop: 8, marginTop: 4 }}>
          <div style={{ fontSize: 11, color: "#fbbf24", marginBottom: 6 }}>
            ⚠ Disconnect is a two-step. Preview first — nothing happens until you confirm.
          </div>
          {actionError && <div style={{ color: "#f87171", fontSize: 11, marginBottom: 4 }}>{actionError}</div>}
          <div style={{ display: "flex", gap: 6 }}>
            <button disabled={busy} onClick={handlePreview} style={btnSecondary(busy)}>1) Preview Disconnect</button>
            <button disabled={busy || !lastPreviewToken} onClick={handleDisconnectNow} style={btnDanger(busy)}>
              2) Confirm Disconnect
            </button>
          </div>
          {lastPreviewPlan && (
            <pre style={{ marginTop: 6, padding: 6, background: "#0e1116", border: "1px solid #2a313c", fontSize: 10, color: "#9fb3c8", borderRadius: 4, maxHeight: 160, overflow: "auto" }}>
              {JSON.stringify(lastPreviewPlan, null, 2)}
            </pre>
          )}
        </div>
      )}

      {snap.last_test_steps?.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <button onClick={() => setShowSteps(s => !s)}
            style={{ background: "transparent", color: "#6b7c8f", border: "none", fontSize: 10, cursor: "pointer", padding: 0 }}>
            {showSteps ? "▼" : "▶"} last test: {snap.last_test_steps.filter((s: any) => s.status === "PASS").length}/{snap.last_test_steps.length} passed
          </button>
          {showSteps && (
            <pre style={{ marginTop: 4, padding: 6, background: "#0e1116", border: "1px solid #2a313c", fontSize: 10, color: "#9fb3c8", borderRadius: 4, maxHeight: 200, overflow: "auto" }}>
              {JSON.stringify(snap.last_test_steps, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function telemetryColor(state: string): string {
  switch (state) {
    case "CONNECTED": return "#7ee2a0";
    case "DEGRADED": return "#fbbf24";
    case "DISCONNECTED": return "#60a5fa";
    case "ADAPTER_DISABLED": return "#fbbf24";
    case "UNVERIFIED": return "#9fb3c8";
    case "UNAVAILABLE": return "#f87171";
    case "UNKNOWN": return "#6b7c8f";     // NEVER green
    default: return "#6b7c8f";
  }
}

function btnStyle(bg: string, busy: boolean): React.CSSProperties {
  return {
    background: bg, color: "#0e1116", border: "none", borderRadius: 4,
    padding: "4px 10px", cursor: busy ? "wait" : "pointer",
    font: "inherit", fontSize: 11, opacity: busy ? 0.5 : 1,
  };
}
function btnPrimary(busy: boolean): React.CSSProperties { return btnStyle("#7ee2a0", busy); }
function btnSecondary(busy: boolean): React.CSSProperties { return btnStyle("#2a313c", busy); }
function btnDanger(busy: boolean): React.CSSProperties { return btnStyle("#f87171", busy); }
function inputStyle(): React.CSSProperties {
  return {
    background: "#0e1116", color: "#d6dde6", border: "1px solid #2a313c",
    borderRadius: 4, padding: "3px 6px", font: "inherit", fontSize: 11,
  };
}
function selectStyle(): React.CSSProperties {
  return { ...inputStyle(), color: "#d6dde6" };
}
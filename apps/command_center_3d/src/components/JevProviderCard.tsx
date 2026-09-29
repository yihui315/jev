import { useState } from "react";
import { envApiBase, resolveApiBase } from "../lib/apiBase";

export interface JevProviderCardProps {
  snap: any;
  stateColor: string;
  busy: boolean;
  controlToken: string | null;
  onValidate: (payload: { endpoint: string; request_schema: string; api_key_env: string }) => void;
  onActivate: () => void;
}

type Result =
  | { kind: "idle" }
  | { kind: "validating" }
  | { kind: "valid_no_credential"; plan: any }
  | { kind: "valid_with_credential"; plan: any }
  | { kind: "invalid"; reason: string }
  | { kind: "activate_pending"; plan: any };

export function JevProviderCard(props: JevProviderCardProps) {
  const { snap, stateColor, busy, controlToken, onValidate, onActivate } = props;
  const [endpoint, setEndpoint] = useState<string>("https://api.typesafe.ai");
  const [requestSchema, setRequestSchema] = useState<string>("jcc/v1/score");
  const [apiKeyEnv, setApiKeyEnv] = useState<string>("JEV_API_KEY");
  const [showSetup, setShowSetup] = useState<boolean>(false);
  const [result, setResult] = useState<Result>({ kind: "idle" });

  const handleValidate = () => {
    setResult({ kind: "validating" });
    // The parent posts and refreshes. We do an inline fetch here so we can
    // surface the response directly without round-tripping via the parent's
    // busy state.
    void (async () => {
      const ctrlToken = controlToken;
      try {
        const r = await fetch(resolveApiBase(envApiBase()) + "/api/connections/jev/validate", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(ctrlToken ? { "X-JCC-Token": ctrlToken } : {}),
          },
          body: JSON.stringify({ endpoint, request_schema: requestSchema, api_key_env: apiKeyEnv }),
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) {
          setResult({ kind: "invalid", reason: JSON.stringify(body).slice(0, 300) });
          return;
        }
        if (body.host_environment_required) {
          setResult({ kind: "valid_no_credential", plan: body });
          setShowSetup(true);
        } else if (body.credential_present_in_host_env) {
          setResult({ kind: "valid_with_credential", plan: body });
        } else {
          setResult({ kind: "invalid", reason: body.reason || "validation rejected" });
        }
      } catch (e) {
        setResult({ kind: "invalid", reason: String(e) });
      }
    })();
  };

  const handleActivate = () => {
    void (async () => {
      const ctrlToken = controlToken;
      try {
        const r = await fetch(resolveApiBase(envApiBase()) + "/api/connections/jev/activate", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(ctrlToken ? { "X-JCC-Token": ctrlToken } : {}),
          },
          body: JSON.stringify({}),
        });
        const body = await r.json().catch(() => ({}));
        if (r.ok && body.activated === false && body.state === "PENDING_EXTERNAL_ACTIVATION") {
          setResult({ kind: "activate_pending", plan: body });
        } else if (!r.ok) {
          setResult({ kind: "invalid", reason: JSON.stringify(body).slice(0, 300) });
        }
      } catch (e) {
        setResult({ kind: "invalid", reason: String(e) });
      }
    })();
  };

  const validNoCred = result.kind === "valid_no_credential";
  const validWithCred = result.kind === "valid_with_credential";

  return (
    <div style={{
      border: "1px solid #2a313c", borderRadius: 6, background: "#161b22",
      padding: 14, display: "flex", flexDirection: "column", gap: 8,
      gridColumn: "span 2",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Jev Provider</div>
          <div style={{ color: "#6b7c8f", fontSize: 10 }}>id=jev · the scorer that drives risk / directive decisions</div>
        </div>
        <div style={{
          padding: "2px 10px", borderRadius: 12, fontSize: 11, fontWeight: 600,
          color: "#0e1116", background: stateColor,
        }}>{snap.connection_state}</div>
      </div>

      <div style={{ fontSize: 11, color: "#9fb3c8" }}>
        Current = <b style={{ color: "#7ee2a0" }}>MOCK</b>. Real Jev = <b style={{ color: "#fbbf24" }}>PENDING_EXTERNAL_ACTIVATION</b>.
        Activate is gated on all three of (endpoint, request_schema, credential) being valid AND the credential
        env var being present on the host. The UI never sees the credential value.
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", rowGap: 6, columnGap: 10, fontSize: 11 }}>
        <label>Endpoint</label>
        <input value={endpoint} onChange={e => setEndpoint(e.target.value)} style={inp()} />

        <label>Request schema</label>
        <input value={requestSchema} onChange={e => setRequestSchema(e.target.value)} style={inp()} />

        <label>Credential env-var name</label>
        <input
          type="password"
          autoComplete="off"
          value={apiKeyEnv}
          onChange={e => setApiKeyEnv(e.target.value.toUpperCase())}
          style={inp()}
        />
      </div>

      <div style={{ fontSize: 10, color: "#6b7c8f" }}>
        Note: the env-var NAME is fine to send to the browser. The env-var VALUE
        lives only on the host and is never sent over the wire, never stored in
        DB, never logged, never echoed back in any response.
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
        <button disabled={busy || result.kind === "validating"} onClick={handleValidate}
          style={btnPrimary(busy || result.kind === "validating")}>
          ✓ Validate Only (no paid request)
        </button>
        <button
          disabled={busy || !(validWithCred)}
          onClick={handleActivate}
          style={btnSecondary(busy || !(validWithCred))}
          title={validWithCred ? "" : "Validate first with credential present in host env"}
        >
          ⚡ Configure Real Jev (Activate)
        </button>
      </div>

      {result.kind === "invalid" && (
        <div style={{ padding: 8, background: "#2a1a1a", border: "1px solid #5a2a2a", color: "#f87171", fontSize: 11, borderRadius: 4 }}>
          ✗ {result.reason}
        </div>
      )}

      {result.kind === "valid_no_credential" && (
        <div style={{ padding: 8, background: "#1a2a2a", border: "1px solid #2a5a5a", color: "#7ee2a0", fontSize: 11, borderRadius: 4 }}>
          <b>HOST ENVIRONMENT REQUIRED</b> — the credential env var is not set
          on this host yet. Run the command shown (the UI never sees the value),
          then click Validate again.
        </div>
      )}

      {showSetup && (result.kind === "valid_no_credential") && (
        <div>
          <div style={{ fontSize: 11, color: "#6b7c8f", marginBottom: 4 }}>
            PowerShell (run on the host, NOT in this browser):
          </div>
          <pre style={{ margin: 0, padding: 8, background: "#0e1116", border: "1px solid #2a313c",
            fontSize: 11, color: "#7ee2a0", borderRadius: 4, whiteSpace: "pre-wrap" }}>
            {(result.plan?.setup_command_powershell ?? "").replace(/<your-real-jev-key>/g, "<your-key-here>")}
          </pre>
          <div style={{ fontSize: 10, color: "#6b7c8f", marginTop: 4 }}>
            The placeholder above is rendered by the UI; the server NEVER echoes
            the secret value back. The real value stays in the host environment.
          </div>
        </div>
      )}

      {result.kind === "activate_pending" && (
        <div style={{ padding: 8, background: "#2a2a1a", border: "1px solid #5a5a2a", color: "#fbbf24", fontSize: 11, borderRadius: 4 }}>
          <b>PENDING_EXTERNAL_ACTIVATION</b> — the operator must run the
          command below on the host and restart the v1.2 service. No paid
          request was made.
        </div>
      )}

      {result.kind === "activate_pending" && (
        <div>
          <div style={{ fontSize: 11, color: "#6b7c8f", marginBottom: 4 }}>
            PowerShell (host restart required):
          </div>
          <pre style={{ margin: 0, padding: 8, background: "#0e1116", border: "1px solid #2a313c",
            fontSize: 11, color: "#fbbf24", borderRadius: 4, whiteSpace: "pre-wrap" }}>
            {result.plan?.operator_command_powershell ?? ""}
          </pre>
        </div>
      )}
    </div>
  );
}

function inp(): React.CSSProperties {
  return {
    background: "#0e1116", color: "#d6dde6", border: "1px solid #2a313c",
    borderRadius: 4, padding: "3px 6px", font: "inherit", fontSize: 11,
  };
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
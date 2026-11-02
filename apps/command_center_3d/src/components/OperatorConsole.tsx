import type { SceneState } from "../store/sceneMap";
import { sceneStore } from "../store/SceneStore";
import {
  actionStatus,
  blockReason,
  hasEndpoint,
  isActuable,
} from "../store/capability";

/**
 * Operator Console (Wave 6).
 *
 * Capability-aware: an action is enabled for an agent only if the released
 * capability matrix marks it VERIFIED/ACTUABLE AND the v1.2 server exposes an
 * endpoint. Everything else is BLOCKED with the matrix's own reason — no fake
 * enabled buttons. The control token is memory-only (typed here, never
 * persisted). A "send" emits an INERT, requires_human directive; actuation is
 * disabled in this build, so policy is shown to be NOT executable.
 */
export function OperatorConsole({ scene }: { scene: SceneState }) {
  const targetAgent = scene.agentFilter ?? scene.sessions.find((s) => s.id === scene.selectedSessionId)?.agent ?? null;
  const actions = scene.capabilities?.actuationCapabilities ?? ["pause", "steer", "retry"];
  const pending = scene.pendingAction;
  const policy = scene.core?.policyVersion ?? "UNKNOWN";

  return (
    <div style={{ padding: 8, fontSize: 12, borderTop: "1px solid #2a313c" }}>
      <h3 style={{ fontSize: 12, margin: "0 0 6px", color: "#8aa0b4" }}>Operator Console</h3>

      <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
        {(["view", "operate"] as const).map((m) => (
          <button
            key={m}
            onClick={() => sceneStore.setOperatorMode(m)}
            style={{
              background: scene.operatorMode === m ? "#1f2a36" : "transparent",
              color: scene.operatorMode === m ? "#7ee2a0" : "#9fb3c8",
              border: "1px solid #2a313c",
              borderRadius: 4,
              padding: "2px 8px",
              cursor: "pointer",
              font: "inherit",
              textTransform: "capitalize",
            }}
          >
            {m}
          </button>
        ))}
      </div>

      <input
        type="password"
        placeholder="control token (memory-only)"
        value={scene.controlToken}
        onChange={(e) => sceneStore.setControlToken(e.target.value)}
        style={{
          width: "100%",
          boxSizing: "border-box",
          background: "#0e1116",
          color: "#d6dde6",
          border: "1px solid #2a313c",
          borderRadius: 4,
          padding: "3px 6px",
          font: "inherit",
          fontSize: 11,
          marginBottom: 6,
        }}
      />

      {!targetAgent && (
        <p style={{ fontSize: 11, color: "#8aa0b4" }}>select an agent (filter) to control it</p>
      )}

      {targetAgent && (
        <>
          <div style={{ color: "#9fb3c8", marginBottom: 4 }}>target: {targetAgent}</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {actions.map((a) => {
              const actuable = isActuable(scene.capabilities, targetAgent, a);
              const endpoint = hasEndpoint(a);
              const enabled = scene.operatorMode === "operate" && actuable && endpoint;
              const reason = !actuable
                ? blockReason(scene.capabilities, targetAgent, a)
                : !endpoint
                  ? `no v1.2 endpoint for ${a}`
                  : scene.operatorMode !== "operate"
                    ? "switch to OPERATE"
                    : "";
              return (
                <button
                  key={a}
                  disabled={!enabled}
                  title={reason || `status: ${actionStatus(scene.capabilities, targetAgent, a)}`}
                  onClick={() => sceneStore.selectAction(targetAgent, a)}
                  style={{
                    background: enabled ? "#16241c" : "transparent",
                    color: enabled ? "#7ee2a0" : "#5b6b7d",
                    border: "1px solid #2a313c",
                    borderRadius: 4,
                    padding: "2px 6px",
                    cursor: enabled ? "pointer" : "not-allowed",
                    font: "inherit",
                    fontSize: 11,
                    textTransform: "capitalize",
                  }}
                >
                  {a}
                </button>
              );
            })}
          </div>
        </>
      )}

      {pending && (
        <div style={{ marginTop: 8, border: "1px solid #2a313c", borderRadius: 4, padding: 6 }}>
          <div style={{ color: "#cbd5e1", marginBottom: 4 }}>
            Control Preview — directive (INERT)
          </div>
          <pre style={{ fontSize: 10, color: "#9fb3c8", margin: 0, whiteSpace: "pre-wrap" }}>
{JSON.stringify(
  {
    action: pending.action,
    target_agent: pending.agent,
    target_session: scene.selectedSessionId ?? "manual",
    target_actuator: "noop",
    policy_version: policy,
    executable: false,
    requires_human: true,
  },
  null,
  2,
)}
          </pre>
          <div style={{ fontSize: 10, color: "#fbbf24", marginTop: 4 }}>
            policy ≠ executable: actuation disabled in v1.2 — this only records a directive.
          </div>
          <button
            onClick={() => void sceneStore.sendControl()}
            style={{
              marginTop: 6,
              background: "#1f2a36",
              color: "#7ee2a0",
              border: "1px solid #2a313c",
              borderRadius: 4,
              padding: "3px 10px",
              cursor: "pointer",
              font: "inherit",
            }}
          >
            Send directive
          </button>
          <button
            onClick={() => sceneStore.clearAction()}
            style={{ marginTop: 6, marginLeft: 6, background: "transparent", color: "#9fb3c8", border: "none", cursor: "pointer", font: "inherit" }}
          >
            cancel
          </button>
        </div>
      )}

      {scene.lastControlResult && (
        <div
          style={{
            marginTop: 6,
            fontSize: 11,
            color: scene.lastControlResult.ok ? "#7ee2a0" : "#f87171",
          }}
        >
          {scene.lastControlResult.ok ? "OK" : "BLOCKED"} · HTTP {scene.lastControlResult.status}
          <div style={{ color: "#9fb3c8", marginTop: 2 }}>{scene.lastControlResult.detail}</div>
        </div>
      )}
    </div>
  );
}

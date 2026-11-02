/**
 * Capability gating — pure helpers over the released CapabilityMatrix.
 *
 * Discipline (v1.2): the operator console NEVER enables an action an agent
 * cannot actually perform. The matrix is the released source of truth for what
 * is VERIFIED; until the live shadow gate opens, every agent capability in the
 * v1.1 matrix is UNVERIFIED / UNSUPPORTED, so every control is BLOCKED with the
 * matrix's own reason. We surface that reason rather than faking an enabled
 * button.
 */
import type { CapabilityMatrix } from "./sceneMap";

/** Statuses that mean "the operator may actually drive this". */
const ACTUABLE_STATUSES = new Set(["VERIFIED", "ACTUABLE"]);

/** Control endpoints exposed by the v1.2 server (token-gated, inert). */
export const CONTROL_ENDPOINT: Record<string, string> = {
  pause: "/api/pause",
  steer: "/api/steer",
  retry: "/api/retry",
  // verify / cancel / session_message / approval have no v1.2 endpoint yet.
};

export function actionStatus(
  matrix: CapabilityMatrix | null,
  agent: string,
  action: string,
): string {
  const cap = matrix?.agents?.[agent]?.capabilities?.[action];
  return cap?.status ?? "UNKNOWN";
}

export function isActuable(
  matrix: CapabilityMatrix | null,
  agent: string,
  action: string,
): boolean {
  return ACTUABLE_STATUSES.has(actionStatus(matrix, agent, action));
}

export function blockReason(
  matrix: CapabilityMatrix | null,
  agent: string,
  action: string,
): string {
  if (!matrix) return "capability matrix not loaded (UNKNOWN)";
  const ag = matrix.agents?.[agent];
  if (!ag) return `agent ${agent} not present in capability matrix`;
  const cap = ag.capabilities?.[action];
  if (!cap) return `action ${action} not declared for ${agent}`;
  if (isActuable(matrix, agent, action)) return "";
  return cap.reason ?? `status ${cap.status} (not VERIFIED)`;
}

/** True if the v1.2 server exposes a control endpoint for this action. */
export function hasEndpoint(action: string): boolean {
  return action in CONTROL_ENDPOINT;
}

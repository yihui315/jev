/**
 * status — the shared vocabulary for every state the product displays.
 *
 * ONE module decides what colour a mission status, a connection health or an
 * attention kind gets. If a component picks its own colour, the honesty rule
 * ("UNKNOWN is never green") becomes unenforceable, because there is no single
 * place to audit. Everything here is pure, so it is unit-tested directly.
 *
 * Rule, stated once: a state the system could not observe maps to
 * `unknown` — dim grey. It never maps to `ok` / `live`.
 */
import type { BadgeProps } from "../ui/Badge";

export type StatusTone =
  | "ok"
  | "live"
  | "warn"
  | "danger"
  | "info"
  | "unknown";

/** Mission lifecycle (mirrors `jcc_core.domain.MissionStatus`). */
export const MISSION_STATUSES = [
  "DRAFT",
  "READY",
  "STARTING",
  "RUNNING",
  "NEEDS_ATTENTION",
  "PAUSED",
  "VERIFYING",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
] as const;

export type MissionStatus = (typeof MISSION_STATUSES)[number];

const MISSION_TONE: Record<MissionStatus, StatusTone> = {
  DRAFT: "unknown",
  READY: "info",
  STARTING: "info",
  RUNNING: "live",
  NEEDS_ATTENTION: "warn",
  PAUSED: "warn",
  VERIFYING: "info",
  COMPLETED: "ok",
  FAILED: "danger",
  CANCELLED: "unknown",
};

export function missionTone(s: string): StatusTone {
  return MISSION_TONE[s as MissionStatus] ?? "unknown";
}

/** Attention kinds (mirrors `jcc_core.domain.AttentionKind`). */
export const ATTENTION_KINDS = [
  "APPROVAL_REQUIRED",
  "AGENT_STUCK",
  "POLICY_ESCALATION",
  "VERIFICATION_REQUIRED",
  "BUDGET_WARNING",
  "CONNECTION_LOST",
  "DELIVERABLE_READY",
] as const;

export type AttentionKind = (typeof ATTENTION_KINDS)[number];

const ATTENTION_TONE: Record<AttentionKind, StatusTone> = {
  APPROVAL_REQUIRED: "warn",
  AGENT_STUCK: "danger",
  POLICY_ESCALATION: "warn",
  VERIFICATION_REQUIRED: "info",
  BUDGET_WARNING: "warn",
  CONNECTION_LOST: "danger",
  DELIVERABLE_READY: "ok",
};

export function attentionTone(kind: string): StatusTone {
  return ATTENTION_TONE[kind as AttentionKind] ?? "unknown";
}

/** Human label for an attention kind — used in the inbox and timeline. */
const ATTENTION_LABEL: Record<AttentionKind, string> = {
  APPROVAL_REQUIRED: "Approval required",
  AGENT_STUCK: "Agent stuck",
  POLICY_ESCALATION: "Policy escalation",
  VERIFICATION_REQUIRED: "Verification required",
  BUDGET_WARNING: "Budget warning",
  CONNECTION_LOST: "Connection lost",
  DELIVERABLE_READY: "Deliverable ready",
};

export function attentionLabel(kind: string): string {
  return ATTENTION_LABEL[kind as AttentionKind] ?? kind;
}

/**
 * Test seam: the declared attention taxonomy. Exposed as a function so the
 * unit test asserts the *enum length* rather than duplicating the list, which
 * would let an added kind escape coverage.
 */
export function attendanceKindsForTest(): readonly string[] {
  return ATTENTION_KINDS;
}

/**
 * Connection / capability health.
 *
 * `CONTROL_VERIFIED` is the only "we can actually act through this" state.
 * `ADAPTER_CONTRACT_LIVE` and `TELEMETRY_ONLY` mean we can SEE, not act — they
 * must never be rendered like a verified control path.
 */
export const CONNECTION_STATES = [
  "CONNECTED",
  "DEGRADED",
  "DISCONNECTED",
  "UNVERIFIED",
  "UNAVAILABLE",
  "UNKNOWN",
] as const;

export const CAPABILITY_KINDS = [
  "UNKNOWN",
  "TELEMETRY_ONLY",
  "ADAPTER_CONTRACT_LIVE",
  "CONTROL_VERIFIED",
  "UNAVAILABLE",
] as const;

export type CapabilityKind = (typeof CAPABILITY_KINDS)[number];

const CONNECTION_TONE: Record<string, StatusTone> = {
  CONNECTED: "ok",
  DEGRADED: "warn",
  DISCONNECTED: "info",
  UNVERIFIED: "unknown",
  UNAVAILABLE: "danger",
  // UNKNOWN is grey — never green.
  UNKNOWN: "unknown",
};

export function connectionTone(state: string): StatusTone {
  return CONNECTION_TONE[state] ?? "unknown";
}

const CAPABILITY_TONE: Record<CapabilityKind, StatusTone> = {
  CONTROL_VERIFIED: "ok",
  ADAPTER_CONTRACT_LIVE: "info",
  TELEMETRY_ONLY: "info",
  UNAVAILABLE: "danger",
  UNKNOWN: "unknown",
};

export function capabilityTone(kind: string): StatusTone {
  return CAPABILITY_TONE[kind as CapabilityKind] ?? "unknown";
}

/** Only CONTROL_VERIFIED permits effectful action. Everything else is blocked. */
export function isControlVerified(kind: string): boolean {
  return kind === "CONTROL_VERIFIED";
}

/** Artifact status vocabulary (mirrors `jcc_core.domain.ArtifactStatus`). */
const ARTIFACT_TONE: Record<string, StatusTone> = {
  PRODUCED: "info",
  VERIFIED: "ok",
  REJECTED: "danger",
  SUPERSEDED: "unknown",
};

export function artifactTone(status: string): StatusTone {
  return ARTIFACT_TONE[status] ?? "unknown";
}

/** Approval states (mirrors `jcc_core.domain.ApprovalState`). */
const APPROVAL_TONE: Record<string, StatusTone> = {
  PENDING: "warn",
  APPROVED: "ok",
  REJECTED: "danger",
  EXPIRED: "unknown",
};

export function approvalTone(state: string): StatusTone {
  return APPROVAL_TONE[state] ?? "unknown";
}

/**
 * Map a tone to a Badge variant.
 *
 * Tone and variant are 1:1 today; keeping the mapping explicit means a future
 * tone cannot silently fall through to the default (green-ish) badge.
 */
export function toneVariant(tone: StatusTone): NonNullable<BadgeProps["variant"]> {
  switch (tone) {
    case "ok":
      return "ok";
    case "live":
      return "live";
    case "warn":
      return "warn";
    case "danger":
      return "danger";
    case "info":
      return "info";
    default:
      return "unknown";
  }
}

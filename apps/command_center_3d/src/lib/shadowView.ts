/**
 * Live Shadow UI view-model (Wave 8 — Live Shadow UI Gate falsifier).
 *
 * This is the SINGLE function the 3D world / table / inspector call to decide
 * what the shadow badge renders. It is the anti-fabrication boundary at the UI
 * layer: an UNKNOWN shadow section (the engine had no live telemetry to
 * supervise) renders as "unknown" with no numbers, NEVER as a computed/empty
 * clean summary. A real LIVE summary renders its counts. Keeping this pure and
 * unit-tested means the UI cannot silently dress an unknown gap as verified.
 */
import type { SceneState } from "../store/sceneMap";

export type ShadowView =
  | {
      state: "live";
      liveRecordCount: number;
      distinctAgentCount: number;
      gate: string;
      note: string;
    }
  | {
      state: "unknown";
      note: string;
    };

export function summarizeShadow(scene: SceneState): ShadowView {
  const section = scene.shadow;
  if (section && section.source === "LIVE" && section.data && typeof section.data === "object") {
    const d = section.data as {
      live_record_count?: number;
      distinct_agent_count?: number;
      gate?: string;
      note?: string;
    };
    return {
      state: "live",
      liveRecordCount: Number(d.live_record_count ?? 0),
      distinctAgentCount: Number(d.distinct_agent_count ?? 0),
      gate: d.gate ?? "LIVE_ONLY",
      note: d.note ?? "",
    };
  }
  // UNKNOWN (or any non-LIVE source) is rendered honestly as "unknown".
  // Crucially we do NOT invent counts from a null/empty gap.
  return {
    state: "unknown",
    note:
      (section?.reason as string | undefined) ??
      "shadow surface has no live telemetry to supervise",
  };
}

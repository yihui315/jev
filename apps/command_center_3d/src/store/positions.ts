/**
 * Deterministic layout helpers for the 3D Session World.
 *
 * Positions MUST be stable across renders and across reloads: the same agent id
 * always lands at the same angle, so the world does not "reshuffle" every time
 * the scene projection refreshes (which would make the operator lose track of
 * where an agent lives). We derive the angle from a stable hash of the agent id
 * spread by the golden angle — this gives a well-distributed, order-independent
 * placement even as the agent/cluster count changes.
 */

/** FNV-1a hash → unsigned 32-bit int, stable across runs. */
export function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** Stable angle in [0, 2π) for an agent id. Order-independent. */
export function agentAngle(id: string): number {
  return (hashString(id) % 1000) / 1000 * Math.PI * 2;
}

/** Radius of the agent-cluster ring around the central CORE. */
export const CLUSTER_RADIUS = 3.4;

/** Radius of a session satellite around its owning agent cluster. */
export const SATELLITE_RADIUS = 1.15;

/** Position of an agent cluster center on the ring. */
export function agentClusterPosition(id: string): [number, number, number] {
  const a = agentAngle(id);
  return [Math.cos(a) * CLUSTER_RADIUS, 0, Math.sin(a) * CLUSTER_RADIUS];
}

/** Deterministic satellite offset for the j-th of n sessions of an agent. */
export function satellitePosition(
  agentId: string,
  index: number,
  count: number,
): [number, number, number] {
  const base = agentClusterPosition(agentId);
  if (count <= 1) {
    return [base[0], 0.45, base[2] + SATELLITE_RADIUS];
  }
  const a = (index / count) * Math.PI * 2;
  return [
    base[0] + Math.cos(a) * SATELLITE_RADIUS,
    0.45,
    base[2] + Math.sin(a) * SATELLITE_RADIUS,
  ];
}

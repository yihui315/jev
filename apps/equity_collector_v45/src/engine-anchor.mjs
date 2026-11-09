/**
 * Audit anchor for the FROZEN engine. These sha256 values were measured from
 * the read-only reference files at D:/jev-cloud-staging/... on 2026-11-09.
 *
 * The collector NEVER modifies these files. T5 re-reads the frozen files from
 * disk, recomputes their sha256 and asserts they still match these anchors,
 * proving (a) the engine stayed byte-for-byte frozen and (b) the vendor copies
 * in src/vendor/ are byte-identical to the frozen pure modules that the
 * collector's computation reuses.
 *
 * ENGINE_SHA256 = sha256 of the canonical JSON of ENGINE_ANCHORS (computed at
 * runtime by computeEngineSha256() in hash.mjs). It is recorded on EVERY
 * provenance row so any snapshot can be tied back to this exact engine state.
 */
export const ENGINE_ANCHORS = {
  "server/engine/_lib/decimal.mjs":
    "5b32d4bd9e03ed57f7cd2cf04f645ef53b9d44baa05d23f2e5a5a42ad8ae6e4d",
  "server/engine/_lib/equity-snapshot.mjs":
    "d1c784f8e1508db7ffb9f75f0ed87064f9072ce30e4a2e35d5dcc6698b513e74",
  "server/engine/_lib/paper-server.mjs":
    "f9ac6e142446ef2aa8deb928f2b129eb2d42f4227d1bc22469191087312e4d74",
  "server/engine/migrations/0005_equity_snapshots.sql":
    "7d3f5f48b9e9c7c06817e5b329865388df48eec394ae1be2e4a95c816c95bbaa",
};

// Absolute paths of the frozen files (read-only). Used ONLY by the local test
// harness to verify the anchors; the deployed Worker never touches D:/ paths.
export const ENGINE_ANCHOR_PATHS = {
  "server/engine/_lib/decimal.mjs":
    "D:/jev-cloud-staging/apps/command_center_3d/server/engine/_lib/decimal.mjs",
  "server/engine/_lib/equity-snapshot.mjs":
    "D:/jev-cloud-staging/apps/command_center_3d/server/engine/_lib/equity-snapshot.mjs",
  "server/engine/_lib/paper-server.mjs":
    "D:/jev-cloud-staging/apps/command_center_3d/server/engine/_lib/paper-server.mjs",
  "server/engine/migrations/0005_equity_snapshots.sql":
    "D:/jev-cloud-staging/apps/command_center_3d/server/engine/migrations/0005_equity_snapshots.sql",
};

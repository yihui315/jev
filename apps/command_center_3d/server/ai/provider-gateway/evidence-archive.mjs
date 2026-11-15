// JEV Phase C — Decision-evidence archive.
//
// MOCK-ONLY. Append-only, in-memory archive of every gateway decision. Each
// record stores: model id, input hash, output (the normalized mock output),
// latency, cost, timestamp, plus routing context (policy, switch reason, circuit
// state). Provides query helpers and a JSON serializer for offline audit.
// Nothing here is sent anywhere; serialize() is for local inspection only.

export function createEvidenceArchive() {
  const items = [];
  let seq = 0;

  function add(entry) {
    seq += 1;
    const id = "ev-" + String(seq).padStart(8, "0") + "-" + (entry.inputHash || "").slice(0, 8);
    const record = Object.freeze(
      Object.assign({ evidenceId: id }, entry)
    );
    items.push(record);
    return id;
  }

  return {
    add,
    get(id) {
      return items.find((x) => x.evidenceId === id) || null;
    },
    byRequest(requestId) {
      return items.filter((x) => x.requestId === requestId);
    },
    byModel(modelId) {
      return items.filter((x) => x.modelId === modelId);
    },
    byProvider(providerId) {
      return items.filter((x) => x.providerId === providerId);
    },
    all() {
      return items.slice();
    },
    count() {
      return items.length;
    },
    serialize() {
      return JSON.stringify(
        { schema: "jev/provider-gateway/evidence-archive/v1", count: items.length, items },
        null,
        2
      );
    },
  };
}

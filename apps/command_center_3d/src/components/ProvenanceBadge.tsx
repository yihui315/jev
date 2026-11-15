/**
 * ProvenanceBadge — P7 "no fabricated state" honesty guard.
 *
 * A value may be labelled REAL only when its provenance positively proves it.
 * A missing, partial, null or contradictory provenance NEVER defaults to REAL:
 * it resolves to NOT_MEASURED (or FIXTURE when a fixture/mock marker is set).
 * This component renders no data of its own; it only labels provenance.
 */
export type ProvenanceState = "REAL" | "FIXTURE" | "NOT_MEASURED";

export interface Provenance {
  /** true ONLY when measured from a real, non-mock source. */
  isReal?: boolean | null;
  /** e.g. "live" | "fixture" | "backfill". */
  dataMode?: string | null;
  /** e.g. "live" | "mock" | "replay". */
  captureMode?: string | null;
}

/**
 * Fail-closed resolver. REAL is returned ONLY with complete, positive,
 * non-contradictory evidence. Missing provenance => NOT_MEASURED.
 */
export function resolveProvenanceState(provenance?: Provenance | null): ProvenanceState {
  if (!provenance) return "NOT_MEASURED";

  const { isReal, dataMode, captureMode } = provenance;
  const fixtureMarked =
    /fixture|mock/i.test(String(dataMode ?? "")) ||
    /fixture|mock/i.test(String(captureMode ?? ""));
  if (fixtureMarked || isReal === false) return "FIXTURE";

  const realEvidenceComplete =
    isReal === true &&
    typeof dataMode === "string" && dataMode.length > 0 &&
    typeof captureMode === "string" && captureMode.length > 0;
  if (!realEvidenceComplete) return "NOT_MEASURED";

  return "REAL";
}

// ---- Provider fields: THREE distinct fields, never conflated ----------------
/**
 * GET /api/jev/ai/providers list verdict: BLOCKED | UNVERIFIED only — it never
 * claims a connection. A single provider's `status` is a CREDENTIAL fact
 * (NOT_CONFIGURED / CONFIGURED / CONFIGURED_LOCAL_ONLY / NOT_CONNECTED), NOT
 * connectivity: CONFIGURED means the secret reference resolved, nothing more.
 * CONNECTED is ONLY the verdict of POST /api/jev/ai/providers/test after a real
 * minimal-inference HTTP 200 with content. CONNECTED is therefore NOT a global
 * forbidden word: hiding a genuinely verified probe would be dishonest too.
 */
export type ProviderVerdict = "BLOCKED" | "UNVERIFIED";
export type ProviderStatus =
  | "NOT_CONFIGURED" | "CONFIGURED" | "CONFIGURED_LOCAL_ONLY" | "NOT_CONNECTED";
export type TestProviderStatus = "CONNECTED" | "BLOCKED" | "FAILED";

export interface TestProviderEvidence {
  response_sha256?: string;
  http_status?: number;
  fetched_at?: string;
}

/**
 * CONNECTED must be produced ONLY by real probe evidence. A fixture / mock /
 * missing-credential / incomplete-evidence state can NEVER produce CONNECTED
 * (it resolves to BLOCKED or FAILED). STALE_VERIFIED and CONFIGURED are not
 * accepted here at all — they are different fields, not connectivity.
 */
export function resolveTestProviderStatus(input: {
  fixture?: boolean;
  hasCredentials?: boolean;
  httpOk?: boolean;
  hasContent?: boolean;
  evidence?: TestProviderEvidence;
}): TestProviderStatus {
  if (input.fixture || !input.hasCredentials) return "BLOCKED";
  const evidenceComplete = Boolean(
    input.evidence &&
    input.evidence.response_sha256 &&
    typeof input.evidence.http_status === "number" &&
    input.evidence.fetched_at,
  );
  if (input.httpOk && input.hasContent && evidenceComplete) return "CONNECTED";
  return "FAILED";
}

/**
 * "A real-looking provider shown while the card is unverified" must RAISE an
 * alarm, not a green state (control_ui app.py:146-151 unverified / :160-162
 * needs_action). USE_MOCK alone is never used as a real-state claim here.
 */
export function providerNeedsAction(input: {
  verdict: ProviderVerdict;
  status: ProviderStatus;
  testStatus: TestProviderStatus;
  cardVerified: boolean;
}): boolean {
  const claimsReal =
    input.status === "CONFIGURED" ||
    input.status === "CONFIGURED_LOCAL_ONLY" ||
    input.testStatus === "CONNECTED";
  return claimsReal && !input.cardVerified;
}

const PRESENTATION: Record<ProvenanceState, { label: string; tone: string; title: string }> = {
  REAL: {
    label: "REAL",
    tone: "border-primary/40 bg-primary/10 text-primary",
    title: "已验证的真实数据（provenance 明确 isReal=true 且非 fixture/mock 采集）",
  },
  FIXTURE: {
    label: "FIXTURE",
    tone: "border-status-warn/40 bg-status-warn/10 text-status-warn",
    title: "模拟 / fixture 数据，不是真实交易或真实行情",
  },
  NOT_MEASURED: {
    label: "NOT_MEASURED",
    tone: "border-border bg-card text-muted-foreground",
    title: "未测得：缺少 provenance 或证据不完整，绝不默认为 REAL",
  },
};

/**
 * Render one of exactly three states. When `provenance` is omitted the badge
 * MUST show NOT_MEASURED — never REAL.
 */
export function ProvenanceBadge({
  provenance,
  className = "",
}: {
  provenance?: Provenance | null;
  className?: string;
}) {
  const state = resolveProvenanceState(provenance);
  const p = PRESENTATION[state];
  return (
    <span
      data-provenance-state={state}
      title={p.title}
      className={
        "inline-flex items-center gap-1 rounded border px-2 py-0.5 t-meta font-medium " +
        p.tone +
        " " +
        className
      }
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {p.label}
    </span>
  );
}

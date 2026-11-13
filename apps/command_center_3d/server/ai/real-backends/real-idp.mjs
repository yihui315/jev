// JEV Phase B — C2 REAL IdP adapter (drop-in for phase-b/identity.mjs mock).
//
// FAIL-CLOSED: requires REAL_IDP_ISSUER_URL + REAL_IDP_JWKS_URL + REAL_IDP_AUDIENCE.
// When unconfigured, the constructor throws NotConfiguredError — it NEVER falls
// back to the mock HMAC IdP. No network call is made unless verifyToken is
// actually invoked with real config (which requires real credentials we do not
// have in this environment).
//
// The mock `buildTrustedIdentity` is SYNCHRONOUS; the real one is ASYNC because
// real OIDC verification (JWKS fetch + signature check) is async. The production
// integration code must `await` it.

import { NotConfiguredError } from "./errors.mjs";
import { makeServerActor } from "../human-publish-gate.mjs";

const DEFAULT_REQUIRED_AMR = ["mfa"];

/**
 * Read + validate the real IdP configuration from the environment.
 * Returns a frozen object with `configured` flag.
 */
export function loadRealIdPConfig(env = process.env) {
  const issuer = env.REAL_IDP_ISSUER_URL;
  const jwks = env.REAL_IDP_JWKS_URL;
  const audience = env.REAL_IDP_AUDIENCE;
  const requiredAmr = (env.REAL_IDP_REQUIRED_AMR || "mfa")
    .split(",").map((s) => s.trim()).filter(Boolean);
  if (requiredAmr.length === 0) requiredAmr.push(...DEFAULT_REQUIRED_AMR);
  const configured = !!(issuer && jwks && audience);
  return Object.freeze({ issuer, jwks, audience, requiredAmr, configured });
}

export class RealIdP {
  /** @param {object} [config] defaults to loadRealIdPConfig() */
  constructor(config) {
    this.config = config || loadRealIdPConfig();
    if (!this.config.configured) {
      throw new NotConfiguredError(
        "REAL_IDP",
        "set REAL_IDP_ISSUER_URL, REAL_IDP_JWKS_URL, REAL_IDP_AUDIENCE",
      );
    }
  }

  /**
   * Verify a real OIDC JWT against the configured issuer's JWKS, enforce MFA amr,
   * expiry, and audience. Returns { ok, claims, code }.
   *
   * PRODUCTION PATH (not executed in mock/test env — no credentials):
   *   1. fetch JWKS from this.config.jwks (cache + rotate on kid mismatch)
   *   2. verify RS256/ES256 signature over header.payload
   *   3. validate iss === this.config.issuer, aud === this.config.audience
   *   4. validate exp, and assert every amr in this.config.requiredAmr is present
   * See UNBLOCK_CHECKLIST.md for the exact contract.
   */
  async verifyToken(_token, _opts = {}) {
    throw new Error(
      "RealIdP.verifyToken requires live provider integration " +
      "(REAL_IDP_* env + network); not executed in mock/test env",
    );
  }

  /** Drop-in for buildTrustedIdentity: returns { ok, actor, claims } or fail-closed. */
  async buildTrustedIdentity({ idToken, nowMs = Date.now(), revocationStore = null }) {
    const v = await this.verifyToken(idToken, { nowMs });
    if (!v.ok) return { ok: false, code: v.code, detail: v.detail || null };
    if (revocationStore && revocationStore.isRevoked(v.claims)) {
      return { ok: false, code: "IDENTITY_REVOKED", detail: v.claims.sub };
    }
    const actor = makeServerActor({
      authenticatedUserId: v.claims.sub,
      authMethod: "IDP_OIDC_MFA",
      nowMs,
    });
    return { ok: true, actor, claims: v.claims };
  }
}

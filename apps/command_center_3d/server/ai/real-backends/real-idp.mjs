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

import crypto from "node:crypto";
import { NotConfiguredError } from "./errors.mjs";
import { makeServerActor } from "../human-publish-gate.mjs";

// ---- base64url helpers (JWT-safe) -----------------------------------------
function b64urlEncode(input) {
  return Buffer.from(input).toString("base64url");
}
function b64urlDecode(str) {
  return Buffer.from(str, "base64url");
}

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
    // fetch is injectable so the verification path is unit-testable offline.
    // In production it defaults to global fetch (real network egress).
    this._fetch = this.config.fetchImpl || globalThis.fetch?.bind(globalThis);
    this._jwksCache = null; // { kid, jwk }
  }

  /**
   * Verify a real OIDC JWT against the configured issuer's JWKS, enforce MFA amr,
   * expiry, and audience. Returns { ok, claims, code }.
   *
   * PRODUCTION PATH (flip-ready; requires REAL_IDP_* env + network access to the
   * real IdP's JWKS endpoint):
   *   1. decode header/payload (reject malformed)
   *   2. require alg ∈ {RS256, ES256}
   *   3. fetch + cache JWKS by kid (rotate on kid mismatch), import public key
   *   4. crypto.verify signature over `${header}.${payload}`
   *   5. validate iss === config.issuer, aud includes config.audience,
   *      exp > now, and every amr in config.requiredAmr present
   * See UNBLOCK_CHECKLIST.md for the exact contract.
   *
   * FAIL-CLOSED: any missing/invalid step returns { ok:false, code } — it NEVER
   * returns a trusted actor on a verification failure.
   */
  async verifyToken(token, opts = {}) {
    const nowMs = typeof opts.nowMs === "number" ? opts.nowMs : Date.now();

    // 1. structural decode
    if (typeof token !== "string" || token.split(".").length !== 3) {
      return { ok: false, code: "MALFORMED_TOKEN" };
    }
    const [hB64, pB64, sB64] = token.split(".");
    let header, payload;
    try {
      header = JSON.parse(b64urlDecode(hB64).toString("utf8"));
      payload = JSON.parse(b64urlDecode(pB64).toString("utf8"));
    } catch {
      return { ok: false, code: "MALFORMED_TOKEN" };
    }

    // 2. algorithm allow-list (no alg confusion / none attacks)
    const alg = header && header.alg;
    if (alg !== "RS256" && alg !== "ES256") {
      return { ok: false, code: "UNSUPPORTED_ALG", detail: alg };
    }

    // 3. JWKS fetch + key import (cached by kid; rotate on mismatch)
    let jwk;
    try {
      jwk = await this._getJwk(header.kid);
    } catch (e) {
      return { ok: false, code: "JWKS_UNAVAILABLE", detail: String(e && e.message || e) };
    }
    let pubKey;
    try {
      pubKey = crypto.createPublicKey({ key: jwk, format: "jwk" });
    } catch (e) {
      return { ok: false, code: "JWK_IMPORT_FAILED", detail: String(e && e.message || e) };
    }

    // 4. signature verification
    const signingInput = `${hB64}.${pB64}`;
    let valid;
    try {
      valid = crypto.verify("sha256", Buffer.from(signingInput), pubKey, b64urlDecode(sB64));
    } catch (e) {
      return { ok: false, code: "SIGNATURE_VERIFY_ERROR", detail: String(e && e.message || e) };
    }
    if (!valid) return { ok: false, code: "SIGNATURE_INVALID" };

    // 5. claims validation
    if (payload.iss !== this.config.issuer) {
      return { ok: false, code: "ISS_MISMATCH", detail: { expected: this.config.issuer, got: payload.iss } };
    }
    const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
    if (!aud.includes(this.config.audience)) {
      return { ok: false, code: "AUD_MISMATCH", detail: { expected: this.config.audience, got: payload.aud } };
    }
    if (typeof payload.exp !== "number") {
      return { ok: false, code: "EXP_MISSING" };
    }
    if (payload.exp * 1000 <= nowMs) {
      return { ok: false, code: "EXP_EXPIRED", detail: { exp: payload.exp * 1000, nowMs } };
    }
    const amr = Array.isArray(payload.amr) ? payload.amr : [];
    const missing = this.config.requiredAmr.filter((a) => !amr.includes(a));
    if (missing.length) {
      return { ok: false, code: "AMR_MISSING", detail: { missing } };
    }

    return { ok: true, claims: payload, code: "IDP_VERIFIED" };
  }

  /** Fetch + cache the signing key for `kid`. Rotates the cache on mismatch. */
  async _getJwk(kid) {
    if (this._jwksCache && this._jwksCache.kid === kid) return this._jwksCache.jwk;
    if (typeof this._fetch !== "function") {
      throw new Error("REAL_IDP requires fetch (no fetchImpl and global fetch unavailable)");
    }
    const res = await this._fetch(this.config.jwks);
    if (!res || !res.ok) {
      throw new Error("JWKS_FETCH_FAILED status=" + (res && res.status));
    }
    const doc = await res.json();
    const key = (doc.keys || []).find(
      (k) => k.kid === kid && (k.use === "sig" || !k.use) && (k.kty === "RSA" || k.kty === "EC"),
    );
    if (!key) throw new Error("JWKS_KEY_NOT_FOUND kid=" + kid);
    this._jwksCache = { kid, jwk: key };
    return key;
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

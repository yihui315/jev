// JEV — real-backends shared errors.
//
// `NotConfiguredError` is the single fail-closed signal used by every real
// backend adapter. When a real backend is NOT configured (missing credentials /
// env), the adapter MUST throw this instead of silently returning a mock result.
// This is what prevents "mock in production" accidents.

export class NotConfiguredError extends Error {
  /**
   * @param {string} backend  one of REAL_IDP | REAL_D1 | REAL_WORM
   * @param {string} hint     the exact env vars the caller must set
   */
  constructor(backend, hint) {
    super(`backend ${backend} not configured: ${hint}`);
    this.name = "NotConfiguredError";
    this.backend = backend;
    this.hint = hint;
  }
}

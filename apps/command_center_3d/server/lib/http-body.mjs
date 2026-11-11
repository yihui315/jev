// http-body.mjs — byte-safe, length-capped request body readers.
//
// WHY THIS MODULE EXISTS
// ----------------------
// The first version of the localhost facade read a body like this
// (server/trading_api.mjs, pre-fix lines 151-157):
//
//     function readBody(req) {
//       return new Promise((resolve) => {
//         let s = "";
//         req.on("data", (c) => { s += c; });   // c is a Buffer
//         req.on("end", () => resolve(s));
//       });
//     }
//
// `c` is a Buffer, so `s += c` calls Buffer#toString() SEPARATELY FOR EVERY
// CHUNK. A multi-byte UTF-8 character that straddles a chunk boundary is decoded
// as two U+FFFD replacement characters, and those re-encode to MORE bytes than
// the original. Measured on this repo: "你" (3 bytes) split 2+1 came back as
// 2x U+FFFD = 6 bytes, lossless=false.
//
// That made it a correctness defect, not a nuisance:
//   * any non-ASCII payload (Chinese note, emoji) is silently rewritten when it
//     happens to straddle a segment boundary — green in unit tests, an
//     occasional 400 in production;
//   * every scheme that hashes the raw body fails 100% of the time. The frozen
//     engine's own service-auth.mjs `signServiceBody` is sha256 over the raw
//     body; a rewritten body can never match, and the fingerprint is
//     "intermittent, unreproducible, nothing in the logs".
//
// TWO SEPARATE DEFECTS, TWO SEPARATE FIXES
//   1. decode once, at the end          -> keep Buffers, concat, toString("utf8")
//   2. never buffer without a bound     -> count BYTES while reading, stop early
//
// The cap is in BYTES, never in string length: one CJK character is 3 bytes but
// length 1, so a `text.length > max` test lets a 3x oversized body through.
//
// WHAT THIS MODULE CANNOT FIX
//   A string API is lossy for INVALID UTF-8 no matter who decodes it. If a body
//   must ever be verified byte-for-byte (HMAC / service auth), hash the `bytes`
//   returned here — never the `text`.
//
// ORDERING NOTE FOR CALLERS (413)
//   On oversize the reader stops consuming and rejects; it does NOT destroy the
//   socket, because destroying before the response is flushed turns a useful
//   "413 + max_bytes" into an unexplained ECONNRESET. The caller must send the
//   413 first and destroy afterwards (see `res.end(payload, () => req.destroy())`
//   in trading_api.mjs). Both halves are asserted by server/tests/http-body-read.mjs.

export const DEFAULT_MAX_BODY_BYTES = 64 * 1024;

/** Body exceeded the byte cap. `status` is what the caller should send. */
export class BodyTooLargeError extends Error {
  constructor(maxBytes, meta = {}) {
    super(`BODY_TOO_LARGE: body exceeds ${maxBytes} bytes`);
    this.name = "BodyTooLargeError";
    this.code = "BODY_TOO_LARGE";
    this.status = 413;
    this.maxBytes = maxBytes;
    this.declaredBytes = Number.isFinite(meta.declared) ? meta.declared : null;
  }
}

/** The client went away before the body completed. Never retryable, never a 413. */
export class BodyAbortedError extends Error {
  constructor(reason) {
    super(`BODY_ABORTED: ${reason}`);
    this.name = "BodyAbortedError";
    this.code = "BODY_ABORTED";
    this.status = 400;
  }
}

/**
 * Resolve the cap. Explicit option wins, then JCC_MAX_BODY_BYTES, then default.
 * `process` is probed with typeof because this module is imported by a Worker
 * bundle too, where `process` only exists if nodejs_compat shimmed it.
 */
export function resolveMaxBodyBytes(explicit) {
  if (Number.isFinite(explicit) && explicit > 0) return Math.floor(explicit);
  const env = typeof process !== "undefined" && process && process.env ? process.env : null;
  const fromEnv = Number(env && env.JCC_MAX_BODY_BYTES ? env.JCC_MAX_BODY_BYTES : 0);
  if (Number.isFinite(fromEnv) && fromEnv > 0) return Math.floor(fromEnv);
  return DEFAULT_MAX_BODY_BYTES;
}

/**
 * Read a node:http IncomingMessage to bytes, decoding nothing.
 *
 * @returns {Promise<Buffer>} raw body bytes (empty Buffer when there is no body)
 * @throws  {BodyTooLargeError} total bytes exceeded maxBytes (stops consuming)
 * @throws  {BodyAbortedError}  the request errored or the socket closed early
 */
export function readNodeBodyBytes(req, options = {}) {
  const maxBytes = resolveMaxBodyBytes(options.maxBytes);

  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    let settled = false;

    function cleanup() {
      req.removeListener("data", onData);
      req.removeListener("end", onEnd);
      req.removeListener("error", onError);
      req.removeListener("aborted", onAborted);
      req.removeListener("close", onClose);
    }
    function settle(fn, value) {
      if (settled) return;
      settled = true;
      cleanup();
      fn(value);
    }

    function onData(chunk) {
      if (settled) return;
      const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      total += buf.length;
      if (total > maxBytes) {
        // Drop what we already hold: the point of the cap is to bound memory,
        // and a rejected body is never read again.
        chunks.length = 0;
        settle(reject, new BodyTooLargeError(maxBytes));
        return;
      }
      chunks.push(buf);
    }
    function onEnd() {
      if (settled) return;
      // Exactly one decode, over the full byte sequence.
      settle(resolve, Buffer.concat(chunks, total));
    }
    function onError(err) {
      settle(reject, new BodyAbortedError("request error: " + ((err && (err.code || err.message)) || "unknown")));
    }
    function onAborted() {
      settle(reject, new BodyAbortedError("request aborted by client"));
    }
    function onClose() {
      if (settled) return;
      // 'close' also fires after a COMPLETE body. `req.complete` is the only
      // reliable discriminator; treating every close as an abort would fail
      // every successful request on some node versions.
      if (req.complete) { onEnd(); return; }
      settle(reject, new BodyAbortedError("socket closed before the body completed"));
    }

    req.on("data", onData);
    req.on("end", onEnd);
    req.on("error", onError);
    req.on("aborted", onAborted);
    req.on("close", onClose);
  });
}

/** Convenience: raw bytes decoded once, at the end. */
export async function readNodeBody(req, options = {}) {
  const bytes = await readNodeBodyBytes(req, options);
  return bytes.toString("utf8");
}

function concatByteArrays(parts, total) {
  if (parts.length === 1) return parts[0];
  const out = new Uint8Array(total);
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.byteLength; }
  return out;
}

// ---------------------------------------------------------------------------
// Delivering a rejection to a client that is still sending
// ---------------------------------------------------------------------------
//
// A 413 is answered while the client is usually STILL SENDING the body. Two
// implementations lose the diagnostic, both measured on real sockets:
//
//   1. `res.end(payload); req.destroy()`  -> the response is discarded by RST.
//   2. `res.end(payload, () => req.destroy())` -> still loses it, because node's
//      own `finish` handler (`destroySoon`, registered before ours) closes the
//      socket while unread request bytes remain, and closing a socket with
//      unread received data makes the kernel send RST instead of FIN.
//
// So the response must be WRITTEN ONLY AFTER the request has been drained.
// Draining discards (`onDiscard` retains nothing), which keeps memory O(1); the
// deadline is what bounds a client that never finishes, and only that stalling
// case ends in a destroy.
export const DRAIN_AFTER_ERROR_MS = 2000;

export function drainThenRespond(req, respond) {
  const onDiscard = () => {};
  let timer = null;
  let called = false;

  function cleanup() {
    if (timer) clearTimeout(timer);
    req.removeListener("data", onDiscard);
    req.removeListener("end", run);
    req.removeListener("error", run);
    req.removeListener("close", run);
  }
  function run() {
    if (called) return;
    called = true;
    cleanup();
    respond();
  }

  // Nothing left to read: answer now, so `destroySoon` finds an empty receive
  // queue and closes with FIN.
  if (req.complete) { run(); return; }

  timer = setTimeout(run, DRAIN_AFTER_ERROR_MS);
  if (typeof timer.unref === "function") timer.unref();

  req.on("data", onDiscard);
  req.on("end", run);
  req.on("error", run);
  req.on("close", run);
}

/**
 * Read a fetch-style Request (Workers / undici) with a streaming byte cap.
 *
 * Two-stage bound, because they close different holes:
 *   1. a truthful `content-length` over the cap is rejected BEFORE any read —
 *      a declared 10 MB body never gets allocated;
 *   2. the running byte count is still enforced while streaming, because a
 *      chunked body (or a lying content-length) declares nothing.
 *
 * Only stage 2 alone would still allocate first; only stage 1 alone is
 * trivially bypassed by omitting the header. Both are asserted by
 * server/tests/http-body-read.mjs.
 *
 * @returns {Promise<{bytes: Uint8Array, text: string}>}
 */
export async function readFetchBody(request, options = {}) {
  const maxBytes = resolveMaxBodyBytes(options.maxBytes);

  const declaredRaw = request && request.headers && typeof request.headers.get === "function"
    ? request.headers.get("content-length")
    : null;
  // "absent" and "0" are different facts; a missing header must not be reported
  // as a declared length of 0.
  const declared = declaredRaw === null || declaredRaw === undefined || String(declaredRaw).trim() === ""
    ? null
    : Number(declaredRaw);
  if (declared !== null && Number.isFinite(declared) && declared > maxBytes) {
    throw new BodyTooLargeError(maxBytes, { declared });
  }

  const body = request ? request.body : null;

  if (body && typeof body.getReader === "function") {
    const reader = body.getReader();
    const parts = [];
    let total = 0;
    try {
      for (;;) {
        const step = await reader.read();
        if (step.done) break;
        const value = step.value;
        const buf = value instanceof Uint8Array ? value : new Uint8Array(value);
        total += buf.byteLength;
        if (total > maxBytes) {
          try { await reader.cancel(); } catch (_) { /* already torn down */ }
          throw new BodyTooLargeError(maxBytes, { declared });
        }
        parts.push(buf);
      }
    } catch (err) {
      if (err && err.code === "BODY_TOO_LARGE") throw err;
      throw new BodyAbortedError("request stream error: " + ((err && err.message) || "unknown"));
    }
    const bytes = concatByteArrays(parts, total);
    return { bytes, text: new TextDecoder("utf-8").decode(bytes) };
  }

  // No readable stream (already-buffered Request, or a stub). Fall back to the
  // whole-body read, then enforce the same cap on the true byte length.
  const whole = new Uint8Array(await request.arrayBuffer());
  if (whole.byteLength > maxBytes) throw new BodyTooLargeError(maxBytes, { declared });
  return { bytes: whole, text: new TextDecoder("utf-8").decode(whole) };
}

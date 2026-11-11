#!/usr/bin/env node
/**
 * http-body-read.mjs — byte fidelity + byte cap for BOTH request-body facades.
 *
 * WHAT IS BEING GUARDED
 * ---------------------
 * The node facade used to read a body as
 *     let s = ""; req.on("data", (c) => { s += c; });
 * `c` is a Buffer, so every chunk was decoded SEPARATELY. A multi-byte UTF-8
 * character split across a chunk boundary became U+FFFD U+FFFD and re-encoded
 * to MORE bytes than the original (measured: "你" 3 bytes split 2+1 -> 6 bytes).
 * So the body was not merely re-encoded, it was rewritten, and its length
 * changed. Consequences: any non-ASCII payload was silently corrupted, and any
 * signature over the raw body (the frozen engine's service-auth signServiceBody
 * = sha256 over rawBody) could never match — an "intermittent, unreproducible,
 * no-log-evidence" failure.
 *
 * CASES 2/3 AND CASE 4 ARE THE POINT
 * ----------------------------------
 * Cases 2 and 3 pin chunk boundaries EXACTLY (deterministic stream, no TCP), so
 * they are not flaky. Case 4 runs the OLD implementation on the SAME input and
 * asserts it FAILS the same assertion. That is a permanent negative control: if
 * someone ever "simplifies" the reader back to per-chunk decoding, case 4 still
 * proves the test can see it, and cases 2/3 go red.
 *
 * Cases 8/9 use a real socket, because a deterministic stream cannot prove that
 * the 413 is actually flushed before the socket dies (that ordering is asserted
 * statically in case 15 as well).
 *
 * Exit 0 = all cases pass. Exit 1 = at least one failed.
 * Summary line: RESULT PASS=<n> FAIL=<n>   (parsed by gate-assertion-floor.mjs)
 */
import { createServer } from "node:http";
import { connect } from "node:net";
import { Readable } from "node:stream";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert";

import {
  readNodeBodyBytes, readFetchBody, drainThenRespond, BodyTooLargeError, BodyAbortedError,
} from "../lib/http-body.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));   // .../server/tests
const SERVER_DIR = dirname(HERE);                       // .../server
const APP = dirname(SERVER_DIR);                        // .../command_center_3d

let PASS = 0;
let FAIL = 0;
const failures = [];

async function check(name, fn) {
  try {
    await fn();
    PASS++;
    console.log(`PASS  ${name}`);
  } catch (err) {
    FAIL++;
    failures.push({ name, error: err });
    console.log(`FAIL  ${name}`);
    console.log(`      ${String(err && err.message ? err.message : err).split("\n").join("\n      ")}`);
  }
}

// A node-style request whose chunk boundaries are EXACT: what we push is what
// the reader receives. No TCP, no coalescing, therefore no flakiness.
function nodeReqOf(chunks, { complete = true } = {}) {
  const r = new Readable({ read() {} });
  r.complete = complete;          // what IncomingMessage exposes
  setImmediate(() => {
    for (const c of chunks) r.push(Buffer.isBuffer(c) ? c : Buffer.from(c, "utf8"));
    if (complete) r.push(null);
  });
  return r;
}

/**
 * VERBATIM copy of the pre-fix reader (server/trading_api.mjs lines 151-157 at
 * the time of the fix). Kept here on purpose as a negative control: it must FAIL
 * the byte-fidelity assertions. Do not "fix" it.
 */
function legacyReadNodeBody(req) {
  return new Promise((resolvePromise) => {
    let s = "";
    req.on("data", (c) => { s += c; });
    req.on("end", () => resolvePromise(s));
  });
}

/** fetch-style Request stub: controlled body stream + controlled headers. */
function fetchRequestStub({ chunks = [], contentLength = null, onRead = null }) {
  let i = 0;
  const body = {
    getReader() {
      return {
        async read() {
          if (onRead) onRead();
          if (i >= chunks.length) return { done: true, value: undefined };
          const value = chunks[i++];
          return { done: false, value: value instanceof Uint8Array ? value : new TextEncoder().encode(value) };
        },
        async cancel() { return undefined; },
      };
    },
  };
  const headers = { get: (k) => (String(k).toLowerCase() === "content-length" && contentLength !== null ? String(contentLength) : null) };
  return { headers, body };
}

function stripLineComments(src) {
  return src
    .split("\n")
    .map((ln) => {
      const at = ln.indexOf("//");
      if (at === -1) return ln;
      // do not treat "http://" / "https://" as a comment start
      if (at > 0 && ln[at - 1] === ":") return ln;
      return ln.slice(0, at);
    })
    .join("\n");
}

const CJK = "备";                                    // 3 bytes E5 A4 87 (U+5907)
const ROCKET = "\u{1F680}";                          // 4 bytes
const MIXED = `{"note":"中文备注${ROCKET}","n":1}`;   // ASCII + CJK + astral

// ============================================================================
// node:http facade — byte fidelity
// ============================================================================
await check("1 node: multi-byte body arriving as one chunk is byte-identical", async () => {
  const orig = Buffer.from(MIXED, "utf8");
  const got = await readNodeBodyBytes(nodeReqOf([orig]));
  assert.equal(got.length, orig.length, `byte length changed: ${got.length} != ${orig.length}`);
  assert.ok(got.equals(orig), "bytes differ from the original");
  assert.equal(got.toString("utf8"), MIXED, "text differs from the original");
});

await check("2 node: a multi-byte character split across two chunks survives (the original bug)", async () => {
  const orig = Buffer.from(MIXED, "utf8");
  const at = orig.indexOf(Buffer.from(CJK, "utf8")) + 1;   // straight through the middle of "备"
  const got = await readNodeBodyBytes(nodeReqOf([orig.subarray(0, at), orig.subarray(at)]));
  assert.ok(got.equals(orig), "bytes differ after a mid-character split");
  assert.equal(got.toString("utf8"), MIXED);
  assert.ok(!got.toString("utf8").includes("\uFFFD"), "replacement character present — per-chunk decoding is back");
});

await check("3 node: one byte per chunk is still byte-identical", async () => {
  const orig = Buffer.from(MIXED, "utf8");
  const chunks = [];
  for (let i = 0; i < orig.length; i++) chunks.push(orig.subarray(i, i + 1));
  const got = await readNodeBodyBytes(nodeReqOf(chunks));
  assert.ok(got.equals(orig), `1-byte chunks corrupted the body (${got.length} != ${orig.length})`);
});

await check("4 node: NEGATIVE CONTROL — the pre-fix reader fails case 2 (the assertions have teeth)", async () => {
  const orig = Buffer.from(MIXED, "utf8");
  const at = orig.indexOf(Buffer.from(CJK, "utf8")) + 1;
  const legacyText = await legacyReadNodeBody(nodeReqOf([orig.subarray(0, at), orig.subarray(at)]));
  const legacyBytes = Buffer.from(legacyText, "utf8");
  const lossless = legacyBytes.equals(orig);
  assert.equal(lossless, false,
    "the legacy reader was expected to corrupt the body; if this passes, the fidelity tests cannot detect the bug");
  assert.ok(legacyText.includes("\uFFFD"), "expected replacement characters from the legacy reader");
  assert.ok(legacyBytes.length > orig.length,
    `expected the legacy reader to GROW the body; got ${legacyBytes.length} vs ${orig.length}`);
  // And the fixed reader on the identical input does not:
  const fixedBytes = await readNodeBodyBytes(nodeReqOf([orig.subarray(0, at), orig.subarray(at)]));
  assert.ok(fixedBytes.equals(orig), "fixed reader must be lossless on the input the legacy reader corrupts");
});

// ============================================================================
// node:http facade — byte cap
// ============================================================================
await check("5 node: a body over the cap is rejected with BODY_TOO_LARGE/413", async () => {
  const body = Buffer.alloc(100, 0x41);
  let caught = null;
  try { await readNodeBodyBytes(nodeReqOf([body]), { maxBytes: 32 }); } catch (e) { caught = e; }
  assert.ok(caught instanceof BodyTooLargeError, `expected BodyTooLargeError, got ${caught && caught.name}`);
  assert.equal(caught.code, "BODY_TOO_LARGE");
  assert.equal(caught.status, 413);
  assert.equal(caught.maxBytes, 32);
});

await check("6 node: the cap counts BYTES, not characters (a .length check would pass this body)", async () => {
  const body = Buffer.from(CJK.repeat(20), "utf8");          // 60 bytes, text.length 20
  assert.equal(body.length, 60);
  assert.ok("备".repeat(20).length < 40, "precondition: character length is under the cap");
  let caught = null;
  try { await readNodeBodyBytes(nodeReqOf([body]), { maxBytes: 40 }); } catch (e) { caught = e; }
  assert.ok(caught instanceof BodyTooLargeError,
    "a cap measured in characters would let a 60-byte / 20-character body through");
});

await check("7 node: a client that goes away mid-body settles instead of hanging", async () => {
  const req = nodeReqOf([Buffer.from('{"note":"x', "utf8")], { complete: false });
  const settled = readNodeBodyBytes(req, { maxBytes: 1024 });
  setImmediate(() => req.destroy());
  let caught = null;
  const guard = new Promise((r) => setTimeout(() => r("TIMEOUT"), 2000));
  try { await Promise.race([settled.then(() => "RESOLVED").catch((e) => { caught = e; return "REJECTED"; }), guard]).then((v) => { if (v === "TIMEOUT") throw new Error("reader never settled after the client aborted (the original code hung here)"); }); }
  catch (e) { throw e; }
  assert.ok(caught instanceof BodyAbortedError, `expected BodyAbortedError, got ${caught && caught.name}`);
  assert.equal(caught.code, "BODY_ABORTED");
});

// ============================================================================
// fetch / Workers facade — byte fidelity + streaming cap
// ============================================================================
await check("8 fetch: a body split mid-character is byte-exact (decoded once, from bytes)", async () => {
  const orig = Buffer.from(MIXED, "utf8");
  const at = orig.indexOf(Buffer.from(ROCKET, "utf8")) + 2;   // inside the 4-byte astral char
  const read = await readFetchBody(fetchRequestStub({
    chunks: [new Uint8Array(orig.subarray(0, at)), new Uint8Array(orig.subarray(at))],
  }));
  assert.ok(Buffer.from(read.bytes).equals(orig), "bytes differ after a mid-character split");
  assert.equal(read.text, MIXED);
  assert.ok(!read.text.includes("\uFFFD"), "replacement character present");
});

await check("9 fetch: a REAL Request with a stream body round-trips byte-exactly", async () => {
  const orig = Buffer.from(MIXED, "utf8");
  const stream = new ReadableStream({
    start(controller) {
      const at = orig.indexOf(Buffer.from(CJK, "utf8")) + 1;
      controller.enqueue(new Uint8Array(orig.subarray(0, at)));
      controller.enqueue(new Uint8Array(orig.subarray(at)));
      controller.close();
    },
  });
  const request = new Request("http://127.0.0.1/api/jev/x", { method: "POST", body: stream, duplex: "half" });
  const read = await readFetchBody(request, { maxBytes: 4096 });
  assert.ok(Buffer.from(read.bytes).equals(orig), "real Request body bytes differ");
  assert.equal(read.text, MIXED);
});

await check("10 fetch: a chunked body over the cap is rejected while streaming (no content-length declared)", async () => {
  // 4 x 16 bytes = 64 bytes, cap 40 -> must trip on the 3rd chunk.
  const chunks = [new Uint8Array(16).fill(0x41), new Uint8Array(16).fill(0x41), new Uint8Array(16).fill(0x41)];
  let caught = null;
  try { await readFetchBody(fetchRequestStub({ chunks, contentLength: null }), { maxBytes: 40 }); } catch (e) { caught = e; }
  assert.ok(caught instanceof BodyTooLargeError, `expected BodyTooLargeError, got ${caught && caught.name}`);
  assert.equal(caught.declaredBytes, null, "this case must exercise the streaming bound, not the declared one");
});

await check("11 fetch: a declared content-length over the cap is rejected BEFORE any read", async () => {
  let reads = 0;
  const req = fetchRequestStub({
    chunks: [new Uint8Array(8)],
    contentLength: 10 * 1024 * 1024,
    onRead: () => { reads++; },
  });
  let caught = null;
  try { await readFetchBody(req, { maxBytes: 4096 }); } catch (e) { caught = e; }
  assert.ok(caught instanceof BodyTooLargeError, `expected BodyTooLargeError, got ${caught && caught.name}`);
  assert.equal(reads, 0, "the body stream was read even though the declared length already exceeded the cap");
  assert.equal(caught.declaredBytes, 10 * 1024 * 1024);
});

await check("12 fetch: the cap counts BYTES, not characters", async () => {
  const body = new TextEncoder().encode(CJK.repeat(20));      // 60 bytes, 20 characters
  const chunks = [body.subarray(0, 45), body.subarray(45)];
  let caught = null;
  try { await readFetchBody(fetchRequestStub({ chunks }), { maxBytes: 40 }); } catch (e) { caught = e; }
  assert.ok(caught instanceof BodyTooLargeError,
    "a cap measured in characters would let a 60-byte / 20-character body through");
});

// ============================================================================
// wiring: the facades must actually USE the reader (a fix nobody calls is not a fix)
// ============================================================================
await check("13 wiring: node facade uses the byte reader and keeps no per-chunk accumulator", async () => {
  const src = stripLineComments(readFileSync(join(SERVER_DIR, "trading_api.mjs"), "utf8"));
  assert.ok(/readNodeBodyBytes\s*\(/.test(src), "trading_api.mjs does not call readNodeBodyBytes()");
  assert.ok(!/\bs\s*\+=\s*c\b/.test(src), "per-chunk string concatenation is back in trading_api.mjs");
  assert.ok(!/let\s+s\s*=\s*""/.test(src), "the accumulating-string body reader is back in trading_api.mjs");
  assert.ok(/bodyText,\s*bodyBytes\s*\)/.test(src), "the original body bytes are not forwarded to buildEngineRequest()");
});

await check("14 wiring: Workers facade uses the capped fetch reader and no un-capped request.text()", async () => {
  const src = stripLineComments(readFileSync(join(APP, "functions", "api", "jev", "[[path]].js"), "utf8"));
  assert.ok(/readFetchBody\s*\(/.test(src), "[[path]].js does not call readFetchBody()");
  assert.ok(!/request\.text\s*\(\s*\)/.test(src), "request.text() (un-capped) is back in [[path]].js");
  assert.ok(/bodyText,\s*bodyBytes\s*\)/.test(src), "the original body bytes are not forwarded to buildEngineRequest()");
});

await check("15 wiring: the 413 is flushed BEFORE anything is torn down", async () => {
  const src = stripLineComments(readFileSync(join(SERVER_DIR, "trading_api.mjs"), "utf8"));
  // Destroying first turns a useful "413 + max_bytes" into an unexplained reset,
  // which is exactly the failure mode that makes this class of bug unreportable.
  // Case 17 is the empirical proof; this is the regression guard.
  // DRAIN first, ANSWER second. Both other orders lose the 413 to RST on a real
  // socket (case 17 fails intermittently without this ordering).
  assert.ok(/drainThenRespond\(req,\s*\(\)\s*=>\s*res\.end\(payload\)\)/.test(src),
    "trading_api.mjs no longer drains BEFORE answering the 413");
  assert.ok(!/res\.end\(payload,/.test(src),
    "answering the 413 before draining is back (node's destroySoon turns it into RST)");
  assert.ok(/sendBodyError\s*\(\s*req,\s*res,\s*err\s*\)/.test(src),
    "sendBodyError() is no longer called from the body-read catch block");
  assert.ok(/if\s*\(\s*!response\s*\)\s*return;/.test(src),
    "the already-responded sentinel is not honoured (writing to a closed res throws)");
});

// ============================================================================
// real socket: 413 delivery and end-to-end fidelity through the same function
// ============================================================================
function rawPost(port, bodyChunks, { chunkDelayMs = 0 } = {}) {
  return new Promise((resolvePromise, rejectPromise) => {
    const sock = connect(port, "127.0.0.1", () => {
      const head = [
        "POST /api/jev/echo HTTP/1.1",
        "Host: 127.0.0.1",
        "Content-Type: application/json",
        "Content-Length: " + Buffer.concat(bodyChunks.map((c) => Buffer.from(c))).length,
        "Connection: close",
        "", "",
      ].join("\r\n");
      sock.write(head);
      let i = 0;
      const pump = () => {
        if (i >= bodyChunks.length) return;
        sock.write(Buffer.from(bodyChunks[i++]));
        if (i < bodyChunks.length) setTimeout(pump, chunkDelayMs);
      };
      pump();
    });
    let raw = "";
    sock.on("data", (d) => { raw += d.toString("latin1"); });
    sock.on("error", rejectPromise);
    sock.on("close", () => resolvePromise(raw));
  });
}

async function withServer(handler, fn) {
  const server = createServer(handler);
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const port = server.address().port;
  try { return await fn(port); } finally { await new Promise((r) => server.close(r)); }
}

await check("16 socket: a multi-byte body survives real TCP, end to end", async () => {
  const orig = Buffer.from(MIXED, "utf8");
  const at = orig.indexOf(Buffer.from(CJK, "utf8")) + 1;
  const seen = await withServer(async (req, res) => {
    const bytes = await readNodeBodyBytes(req, { maxBytes: 4096 });
    res.statusCode = 200;
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ same: bytes.equals(orig), recv: bytes.length, orig: orig.length }));
  }, async (port) => {
    const raw = await rawPost(port, [orig.subarray(0, at), orig.subarray(at)], { chunkDelayMs: 15 });
    return raw;
  });
  const body = seen.slice(seen.indexOf("\r\n\r\n") + 4);
  const parsed = JSON.parse(body);
  assert.equal(parsed.same, true, `body differed over a real socket (${parsed.recv} != ${parsed.orig})`);
  assert.equal(parsed.recv, orig.length);
});

await check("17 socket: an oversized body gets a real 413 with a parseable code", async () => {
  const oversize = Buffer.alloc(4096, 0x41);
  const seen = await withServer(async (req, res) => {
    try {
      await readNodeBodyBytes(req, { maxBytes: 256 });
      res.statusCode = 200; res.end('{"ok":true}');
    } catch (err) {
      // EXACTLY the production order (trading_api.mjs sendBodyError).
      const payload = JSON.stringify({ ok: false, code: err.code, status: err.status, max_bytes: err.maxBytes });
      res.statusCode = err.status || 400;
      res.setHeader("content-type", "application/json; charset=utf-8");
      res.setHeader("connection", "close");
      drainThenRespond(req, () => res.end(payload));
    }
  }, async (port) => rawPost(port, [oversize.subarray(0, 1024), oversize.subarray(1024)]));
  assert.ok(/^HTTP\/1\.1 413 /.test(seen), `expected a 413 status line, got: ${seen.split("\r\n")[0]}`);
  const body = seen.slice(seen.indexOf("\r\n\r\n") + 4);
  const parsed = JSON.parse(body);
  assert.equal(parsed.code, "BODY_TOO_LARGE");
  assert.equal(parsed.max_bytes, 256);
});

// ============================================================================
console.log("");
if (FAIL > 0) {
  console.log("FAILURES:");
  for (const f of failures) console.log(`  - ${f.name}: ${f.error && f.error.message}`);
}
console.log(`RESULT PASS=${PASS} FAIL=${FAIL}`);
if (FAIL > 0) process.exitCode = 1;

// Tiny assertion + suite harness. No test framework dependency.

export function createSuite(id, title) {
  const checks = [];
  return {
    id, title, checks,
    async check(name, fn) {
      try {
        const detail = await fn();
        checks.push({ name, pass: true, detail: detail === undefined ? null : detail });
      } catch (err) {
        checks.push({ name, pass: false, detail: String((err && err.message) || err) });
      }
    },
  };
}

export function assert(cond, message) {
  if (!cond) throw new Error(message || "assertion failed");
}

export function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    throw new Error((message || "assertEqual") + ": expected " + JSON.stringify(expected) + " got " + JSON.stringify(actual));
  }
}

export function assertDeep(actual, expected, message) {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) throw new Error((message || "assertDeep") + ": expected " + b + " got " + a);
}

export async function assertRejects(fn, code, message) {
  let resolved = false;
  try { await fn(); resolved = true; } catch (err) {
    if (code && err.code !== code) throw new Error((message || "assertRejects") + ": expected code " + code + " got " + String(err.code || err.message));
    return err;
  }
  if (resolved) throw new Error((message || "assertRejects") + ": expected a rejection but the call resolved");
  return null;
}

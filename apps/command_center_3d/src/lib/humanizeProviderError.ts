/**
 * humanizeProviderError — one source of truth for turning raw backend
 * provider / decision error text into a sentence a person can act on.
 *
 * The raw text is a server contract. It can carry Python state enums
 * (`AUTH_MISSING`, `REAL_AUTH_MISSING`), HTTP status bodies, and — on a
 * regional block — a bytes-repr of the response body like
 * `b'{"title":"...not available in your region..."}'`. Dumping that verbatim
 * shows a Python artifact, not a diagnosis.
 *
 * Two callers share this:
 *  - ProviderCenter (test-connection `detail`)
 *  - App (Jev auto-select `decision.reason`, which wraps the same text as
 *    "Jev did not return a usable decision: <raw>")
 *
 * Returns "" for shapes it does not recognise, so a caller can fall through to
 * its own more specific branches instead of losing information.
 */
export function humanizeProviderError(raw: string | null | undefined): string {
  const d = String(raw ?? "").trim();
  if (!d) return "";

  // Upstream geo-block (HTTP 451). The raw text carries a bytes-repr of the
  // response body — a Python artifact, not something a person can act on. It is
  // NOT a local or config fault: the vendor refuses this region at the edge.
  if (/HTTP 451|not available in your region|b'\{/i.test(d)) {
    return "该服务在你所在地区不可用（HTTP 451 · 上游地域限制）。这是服务商按地区封锁，非本机或配置问题；JEV 自动选择依赖它，暂时无法挑选执行器，请手动选择一位执行器后再开始执行（其余功能不受影响）。";
  }

  // "Jev did not return a usable decision: <raw>" wraps the underlying cause;
  // unwrap so the branches below still match the inner text.
  const inner = /usable decision:\s*(.+)$/i.exec(d);
  const p = inner ? inner[1].trim() : d;

  if (/REAL_AUTH_MISSING|no credential|has no credential/i.test(p)) {
    return "TypeSafe Jev 尚未配置凭据（REAL_AUTH_MISSING），无法参与执行器选择。请在 Provider 设置中配置后重试，或手动选择执行器。";
  }
  if (/NOT_CONFIGURED|未配置 API Key/i.test(p)) {
    return "尚未配置 API Key。请在 Provider 设置中粘贴 Key 后再使用 JEV 自动选择。";
  }
  if (/AUTH_FAILED|HTTP 401|HTTP 403|Invalid or missing/i.test(p)) {
    return "API Key 无效或已失效，请重新配置。";
  }
  if (/rate.?limit|HTTP 429/i.test(p)) {
    return "请求被限流，请稍后重试。";
  }
  if (/HTTP 5\d\d/i.test(p)) {
    return "服务端返回错误，Provider 暂时不可用。请稍后重试。";
  }
  if (/HTTP 404/i.test(p)) {
    return "该端点不存在。可在高级设置中确认 Base URL 与模型 ID。";
  }
  if (
    /transport failure|timed out|Connection refused|Name or service|getaddrinfo|CERTIFICATE_VERIFY_FAILED|SSL/i.test(
      p,
    )
  ) {
    // Network / TLS failure reaching the vendor. On this host the API process
    // inherits HTTPS_PROXY (a localhost MITM proxy) which re-signs TLS with an
    // untrusted cert, so Python rejects it — a local/proxy artifact, not a JEV
    // defect. Say so and point at the working alternative (manual pick).
    return "无法连接到 JEV 模型服务（网络 / SSL 错误）。请检查本机网络或代理设置；或手动选择一位执行器后再开始执行。";
  }

  return "";
}

# JCC Codex telemetry installer (P2) - SAFE / OPT-IN
#
# Codex status on this host is DEGRADED:
#   * observation via rollout jsonl is VERIFIED (session_id present);
#   * push via `notify` is UNVERIFIED and the user ALREADY owns the `notify` key
#     (codex-computer-use.exe) -> this installer does NOT modify config.toml by
#     default. App Server / realtime steering stays DISABLED (P0-P2).
#
# What it does by default: prints the probe result + how to run the observer.
# With -EnableNotify it opts into chaining the notify (requires -Force to
# overwrite a user-owned notify) — backup + sidecar make it fully reversible.
#
# Exit codes: 0 = ok, 1 = failure, 2 = refused (user notify present).
param(
  [string]$JccRoot = "F:\jev",
  [switch]$EnableNotify,
  [switch]$Force
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log  = Join-Path $here "install.log"
function Log($m) { $line = "$(Get-Date -Format o) $m"; Write-Host $line; Add-Content -LiteralPath $log -Value $line -Encoding UTF8 }

try {
  $py = Join-Path $JccRoot ".venv\Scripts\python.exe"
  $cfg = Join-Path $env:USERPROFILE ".codex\config.toml"
  $cfgPy = Join-Path $JccRoot "integrations\codex\jcc_codex_config.py"
  $wrapper = Join-Path $JccRoot "integrations\codex\jcc-codex-notify.cmd"
  $observer = Join-Path $JccRoot "integrations\codex\codex_rollout_observer.py"
  Log "JCC Codex installer start (JccRoot=$JccRoot, EnableNotify=$EnableNotify, Force=$Force)"

  if (-not (Test-Path -LiteralPath $py)) { throw "python not found: $py" }

  # Always: report probe + observer availability (read-only).
  if (Test-Path -LiteralPath $observer) { Log "observer available: $observer" } else { Log "WARN observer missing: $observer" }

  if (-not $EnableNotify) {
    Log "DEFAULT (no config change): Codex notify is NOT touched."
    Log "  Observation path: python `"$observer`" --once"
    Log "  Codex status stays DEGRADED (push UNVERIFIED; user-owned notify preserved)."
    exit 0
  }

  if (-not (Test-Path -LiteralPath $cfg)) { throw "codex config.toml not found: $cfg" }
  if (-not (Test-Path -LiteralPath $cfgPy)) { throw "missing: $cfgPy" }

  $forceArg = @()
  if ($Force) { $forceArg = @("--force") }
  $out = & $py $cfgPy install --config $cfg --wrapper $wrapper @forceArg 2>&1
  $code = $LASTEXITCODE
  $out | ForEach-Object { Log $_ }
  if ($code -eq 2) { Log "REFUSED: user notify present. Re-run with -Force to chain."; exit 2 }
  if ($code -ne 0) { throw "codex notify install failed (exit $code)" }
  Log "Codex notify installed (chained). Rollback: run uninstall.ps1"
  exit 0
}
catch {
  Log "ERROR: $($_.Exception.Message)"
  exit 1
}

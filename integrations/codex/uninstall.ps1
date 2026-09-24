# JCC Codex telemetry uninstaller (P2)
#
# Removes ONLY the JCC-chained `notify` entry from config.toml and restores the
# original user notify from the sidecar (if any). Backs up before writing.
#
# Exit codes: 0 = ok, 1 = failure.
param(
  [string]$JccRoot = "F:\jev"
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log  = Join-Path $here "uninstall.log"
function Log($m) { $line = "$(Get-Date -Format o) $m"; Write-Host $line; Add-Content -LiteralPath $log -Value $line -Encoding UTF8 }

try {
  $py = Join-Path $JccRoot ".venv\Scripts\python.exe"
  $cfg = Join-Path $env:USERPROFILE ".codex\config.toml"
  $cfgPy = Join-Path $JccRoot "integrations\codex\jcc_codex_config.py"
  if (-not (Test-Path -LiteralPath $py)) { throw "python not found: $py" }
  if (-not (Test-Path -LiteralPath $cfg)) { Log "no config.toml; nothing to do."; exit 0 }
  $out = & $py $cfgPy uninstall --config $cfg 2>&1
  $code = $LASTEXITCODE
  $out | ForEach-Object { Log $_ }
  if ($code -ne 0) { throw "codex notify uninstall failed (exit $code)" }
  Log "JCC Codex uninstall done."
  exit 0
}
catch {
  Log "ERROR: $($_.Exception.Message)"
  exit 1
}

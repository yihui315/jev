# JCC Hermes telemetry hook uninstaller (P2)
#
# Removes ONLY the JCC-owned hook directory (~/.hermes/hooks/jcc-telemetry),
# after verifying its HOOK.yaml declares name: jcc-telemetry, and moves it to a
# timestamped backup (never a hard delete).
#
# Exit codes: 0 = ok, 1 = failure.
param(
  [switch]$Purge
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log  = Join-Path $here "uninstall.log"
function Log($m) { $line = "$(Get-Date -Format o) $m"; Write-Host $line; Add-Content -LiteralPath $log -Value $line -Encoding UTF8 }

try {
  $hookDir = Join-Path $env:USERPROFILE ".hermes\hooks\jcc-telemetry"
  if (-not (Test-Path -LiteralPath $hookDir)) { Log "hook dir absent; nothing to do."; exit 0 }

  $manifest = Join-Path $hookDir "HOOK.yaml"
  if (Test-Path -LiteralPath $manifest) {
    $txt = Get-Content -LiteralPath $manifest -Raw
    if ($txt -notmatch "name:\s*jcc-telemetry") { throw "refusing to remove: HOOK.yaml is not JCC-owned" }
  } else {
    throw "refusing to remove: HOOK.yaml missing (ownership unverifiable)"
  }

  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $bak = "$hookDir.jcc-backup-$stamp"
  Move-Item -LiteralPath $hookDir -Destination $bak -Force
  Log "moved $hookDir -> $bak"
  if ($Purge) {
    Remove-Item -LiteralPath $bak -Recurse -Force
    Log "purged backup $bak"
  }
  Log "JCC Hermes uninstall done."
  exit 0
}
catch {
  Log "ERROR: $($_.Exception.Message)"
  exit 1
}

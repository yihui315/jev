# JCC Claude Code telemetry hook uninstaller (P2)
#
# Removes ONLY JCC-owned hook entries (marker: JCC wrapper / jcc_emit.py). User
# hooks are preserved. -RestoreBackup rolls the whole file back to the newest JCC
# backup.
#
# Exit codes: 0 = ok, 1 = failure.
param(
  [string]$JccRoot = "",
  [switch]$RestoreBackup
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
# Derived from this script's own location, not a hardcoded drive letter.
$repoRoot = Split-Path -Parent (Split-Path -Parent $here)
if ([string]::IsNullOrWhiteSpace($JccRoot)) { $JccRoot = $repoRoot }
$log  = Join-Path $here "uninstall.log"
function Log($m) { $line = "$(Get-Date -Format o) $m"; Write-Host $line; Add-Content -LiteralPath $log -Value $line -Encoding UTF8 }

try {
  $py = Join-Path $JccRoot ".venv\Scripts\python.exe"
  $settingsPy = Join-Path $JccRoot "integrations\_common\jcc_settings.py"
  if (-not (Test-Path -LiteralPath $py)) { throw "python not found: $py" }
  $target = Join-Path $env:USERPROFILE ".claude\settings.json"
  Log "uninstalling JCC hooks from: $target"
  $args = @($settingsPy, "uninstall", "--agent", "claude", "--settings", $target)
  if ($RestoreBackup) { $args += "--restore-backup" }
  $out = & $py @args 2>&1
  $code = $LASTEXITCODE
  $out | ForEach-Object { Log $_ }
  if ($code -ne 0) { throw "uninstall failed (exit $code)" }
  Log "JCC Claude uninstall done."
  exit 0
}
catch {
  Log "ERROR: $($_.Exception.Message)"
  exit 1
}

# JCC WorkBuddy telemetry hook uninstaller (P2)
#
# Removes ONLY JCC-owned hook entries (identified by the "jcc_emit.py" marker)
# from the effective settings.json. Pre-existing user hooks are preserved and a
# timestamped backup is written before the change. Use -RestoreBackup to roll the
# whole file back to the newest JCC backup.
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
  if (-not (Test-Path -LiteralPath $settingsPy)) { throw "missing: $settingsPy" }

  $cfgDir = $env:CODEBUDDY_CONFIG_DIR
  if (-not $cfgDir) { $cfgDir = $env:WORKBUDDY_CONFIG_DIR }
  $candidates = @()
  if ($cfgDir) { $candidates += (Join-Path $cfgDir "settings.json") }
  $candidates += (Join-Path $env:USERPROFILE ".workbuddy-ai\settings.json")
  $candidates += (Join-Path $env:USERPROFILE ".codebuddy\settings.json")
  $candidates += (Join-Path $env:USERPROFILE ".workbuddy\settings.json")
  $target = $null
  foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { $target = $c; break } }
  if (-not $target) { $target = Join-Path $env:USERPROFILE ".workbuddy\settings.json" }
  Log "uninstalling JCC hooks from: $target"

  $args = @($settingsPy, "uninstall", "--agent", "workbuddy", "--settings", $target)
  if ($RestoreBackup) { $args += "--restore-backup" }
  $out = & $py @args 2>&1
  $code = $LASTEXITCODE
  $out | ForEach-Object { Log $_ }
  if ($code -ne 0) { throw "uninstall failed (exit $code)" }
  Log "JCC WorkBuddy uninstall done."
  exit 0
}
catch {
  Log "ERROR: $($_.Exception.Message)"
  exit 1
}

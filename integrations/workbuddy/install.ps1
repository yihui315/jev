# JCC WorkBuddy telemetry hook installer (P2)
#
# Installs telemetry-only hooks into the EFFECTIVE CodeBuddy/WorkBuddy user
# settings.json. It NEVER hooks PermissionRequest (WorkBuddy's native GUI
# permission flow is left untouched), backs up before writing, validates JSON,
# and is reversible via uninstall.ps1.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File install.ps1 -JccRoot F:\jev
#
# Exit codes: 0 = ok, 1 = failure (fail-closed).
param(
  [string]$JccRoot = "F:\jev",
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log  = Join-Path $here "install.log"
function Log($m) { $line = "$(Get-Date -Format o) $m"; Write-Host $line; Add-Content -LiteralPath $log -Value $line -Encoding UTF8 }

try {
  $py = Join-Path $JccRoot ".venv\Scripts\python.exe"
  $settingsPy = Join-Path $JccRoot "integrations\_common\jcc_settings.py"
  $emitPy = Join-Path $JccRoot "integrations\_common\jcc_emit.py"
  Log "JCC WorkBuddy installer start (JccRoot=$JccRoot, DryRun=$DryRun)"

  foreach ($p in @($py, $settingsPy, $emitPy)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "required file missing: $p" }
  }

  # Resolve the EFFECTIVE settings path (CODEBUDDY_CONFIG_DIR overrides ~/.codebuddy).
  $cfgDir = $env:CODEBUDDY_CONFIG_DIR
  if (-not $cfgDir) { $cfgDir = $env:WORKBUDDY_CONFIG_DIR }
  $candidates = @()
  if ($cfgDir) { $candidates += (Join-Path $cfgDir "settings.json") }
  $candidates += (Join-Path $env:USERPROFILE ".workbuddy-ai\settings.json")
  $candidates += (Join-Path $env:USERPROFILE ".codebuddy\settings.json")
  $candidates += (Join-Path $env:USERPROFILE ".workbuddy\settings.json")

  $target = $null
  foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { $target = $c; break } }
  if (-not $target) {
    if (Test-Path -LiteralPath (Join-Path $env:USERPROFILE ".workbuddy")) {
      $target = Join-Path $env:USERPROFILE ".workbuddy\settings.json"
    } elseif ($cfgDir) {
      $target = Join-Path $cfgDir "settings.json"
    } else {
      $target = Join-Path $env:USERPROFILE ".codebuddy\settings.json"
    }
    Log "WARN no existing settings.json found; will create: $target"
  }
  Log "effective settings.json: $target"

  if ($DryRun) {
    Log "DRY-RUN: would merge telemetry hooks into $target (backup first). No change written."
    exit 0
  }

  $out = & $py $settingsPy install --agent workbuddy --settings $target --jcc-root $JccRoot 2>&1
  $code = $LASTEXITCODE
  $out | ForEach-Object { Log $_ }
  if ($code -ne 0) { throw "jcc_settings.py install failed (exit $code)" }

  $st = & $py $settingsPy status --agent workbuddy --settings $target 2>&1
  $st | ForEach-Object { Log $_ }
  Log "JCC WorkBuddy installer done."
  exit 0
}
catch {
  Log "ERROR: $($_.Exception.Message)"
  exit 1
}

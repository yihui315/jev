# JCC Claude Code telemetry hook installer (P2)
#
# Merges telemetry-only hooks into ~/.claude/settings.json. Any EXISTING user
# hooks are PRESERVED — JCC entries carry a separate marker ("jcc_emit.py") so
# uninstall never touches user config. Backup + JSON validation on every write.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-JccRoot F:\jev] [-DryRun]
# Exit codes: 0 = ok, 1 = failure.
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
  $wrapper = Join-Path $JccRoot "integrations\claude\jcc-claude-hook.cmd"
  $target = Join-Path $env:USERPROFILE ".claude\settings.json"
  Log "JCC Claude installer start (JccRoot=$JccRoot, DryRun=$DryRun, target=$target)"

  foreach ($p in @($py, $settingsPy, $emitPy, $wrapper)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "required file missing: $p" }
  }

  if ($DryRun) {
    Log "DRY-RUN: would merge telemetry hooks into $target (backup + preserve user hooks). No change written."
    exit 0
  }

  $out = & $py $settingsPy install --agent claude --settings $target --jcc-root $JccRoot 2>&1
  $code = $LASTEXITCODE
  $out | ForEach-Object { Log $_ }
  if ($code -ne 0) { throw "jcc_settings.py install failed (exit $code)" }

  $st = & $py $settingsPy status --agent claude --settings $target 2>&1
  $st | ForEach-Object { Log $_ }
  Log "JCC Claude installer done."
  exit 0
}
catch {
  Log "ERROR: $($_.Exception.Message)"
  exit 1
}

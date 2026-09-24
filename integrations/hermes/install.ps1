# JCC Hermes telemetry hook installer (P2)
#
# Installs the OFFICIAL Hermes hook: ~/.hermes/hooks/jcc-telemetry/ with
# HOOK.yaml + handler.py. Hermes loads hooks from this directory at gateway
# startup (gateway/hooks.py). The handler forwards lifecycle events to JCC and
# is telemetry-only (never returns a decision, never mutates agent behaviour).
#
# Reversible: uninstall.ps1 removes (and backs up) only this hook directory.
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
  $hooksRoot = Join-Path $env:USERPROFILE ".hermes\hooks"
  $hookDir   = Join-Path $hooksRoot "jcc-telemetry"
  $hookYaml  = Join-Path $here "HOOK.yaml.tpl"
  $handler   = Join-Path $here "handler.py.tpl"
  Log "JCC Hermes installer start (JccRoot=$JccRoot, DryRun=$DryRun, hookDir=$hookDir)"

  foreach ($p in @($hookYaml, $handler)) { if (-not (Test-Path -LiteralPath $p)) { throw "missing template: $p" } }

  $cfg = Join-Path $env:USERPROFILE ".hermes\config.yaml"
  if (-not (Test-Path -LiteralPath $cfg)) { Log "WARN ~/.hermes/config.yaml not found; installing hook anyway (Hermes absent => hook inert)" }

  if ($DryRun) {
    Log "DRY-RUN: would create $hookDir with HOOK.yaml + handler.py. No change written."
    exit 0
  }

  New-Item -ItemType Directory -Force -Path $hookDir | Out-Null

  # Backup any pre-existing (non-JCC) files in the target dir.
  foreach ($name in @("HOOK.yaml", "handler.py")) {
    $dest = Join-Path $hookDir $name
    if (Test-Path -LiteralPath $dest) {
      $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
      Copy-Item -LiteralPath $dest -Destination "$dest.jcc-backup-$stamp" -Force
      Log "backed up existing $name -> $name.jcc-backup-$stamp"
    }
  }

  Copy-Item -LiteralPath $hookYaml  -Destination (Join-Path $hookDir "HOOK.yaml")  -Force
  Copy-Item -LiteralPath $handler   -Destination (Join-Path $hookDir "handler.py") -Force

  # Validate the manifest is parsable YAML with the JCC marker name.
  $txt = Get-Content -LiteralPath (Join-Path $hookDir "HOOK.yaml") -Raw
  if ($txt -notmatch "name:\s*jcc-telemetry") { throw "HOOK.yaml validation failed" }
  if (-not (Test-Path -LiteralPath (Join-Path $hookDir "handler.py"))) { throw "handler.py missing after install" }

  Log "installed Hermes hook: $hookDir"
  Log "NOTE: Hermes reads hooks at gateway startup; restart the gateway to activate."
  Log "JCC Hermes installer done."
  exit 0
}
catch {
  Log "ERROR: $($_.Exception.Message)"
  exit 1
}

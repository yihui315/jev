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
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-JccRoot D:\jev] [-DryRun]
# Exit codes: 0 = ok, 1 = failure.
param(
  [string]$JccRoot = "",
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
# $JccRoot defaults to the checkout this script lives in (integrations/hermes ->
# repo root), not to a hardcoded drive letter. It is substituted into the
# handler.py template below, because the installed handler lives OUTSIDE this
# checkout and cannot derive the root from its own __file__.
$repoRoot = Split-Path -Parent (Split-Path -Parent $here)
if ([string]::IsNullOrWhiteSpace($JccRoot)) { $JccRoot = $repoRoot }
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

  # The handler is RENDERED, not copied verbatim: it is installed OUTSIDE this
  # checkout, so it cannot derive the repo root from __file__ and relies on the
  # __JCC_ROOT__ token being substituted here. Copy-then-substitute would leave a
  # literal token in a live file, so read -> substitute -> write, and fail closed
  # if the token is somehow absent (a renamed template must not install silently).
  $handlerText = Get-Content -LiteralPath $handler -Raw
  if ($handlerText -notmatch "__JCC_ROOT__") { throw "handler template lost its __JCC_ROOT__ token; refusing to install an unrendered handler" }
  # Backslashes are escaped for the Python string literal the template holds.
  $pyRootLiteral = $JccRoot.Replace("\", "\\")
  $handlerText = $handlerText.Replace("__JCC_ROOT__", $pyRootLiteral)
  $installedHandler = Join-Path $hookDir "handler.py"
  # WriteAllText with an explicit UTF8 (no BOM) so Python reads it byte-exactly.
  [System.IO.File]::WriteAllText($installedHandler, $handlerText, (New-Object System.Text.UTF8Encoding($false)))

  # Validate the manifest is parsable YAML with the JCC marker name.
  $txt = Get-Content -LiteralPath (Join-Path $hookDir "HOOK.yaml") -Raw
  if ($txt -notmatch "name:\s*jcc-telemetry") { throw "HOOK.yaml validation failed" }
  if (-not (Test-Path -LiteralPath (Join-Path $hookDir "handler.py"))) { throw "handler.py missing after install" }
  # The rendered handler must no longer contain the token, and the root it was
  # rendered with must actually contain the emitter.
  $rendered = Get-Content -LiteralPath (Join-Path $hookDir "handler.py") -Raw
  if ($rendered -match "__JCC_ROOT__") { throw "handler.py still contains __JCC_ROOT__ after rendering" }
  if (-not (Test-Path -LiteralPath (Join-Path $JccRoot "integrations\_common\jcc_emit.py"))) {
    throw "rendered JccRoot does not contain the emitter: $JccRoot"
  }

  Log "installed Hermes hook: $hookDir"
  Log "NOTE: Hermes reads hooks at gateway startup; restart the gateway to activate."
  Log "JCC Hermes installer done."
  exit 0
}
catch {
  Log "ERROR: $($_.Exception.Message)"
  exit 1
}

<#
    JCC v1.2 VISUAL COMMAND CENTER -- front-end test + typecheck stabiliser.

    Runs the Visual UI's own tests (vitest) and typecheck (tsc --noEmit) in a
    HERMETIC temp dir so the run never pollutes the system TEMP and never races
    with other tool runs on this host. This is the "flake normalisation" step:
    the test environment is made deterministic (fixed TEMP/TMP, no watch, single
    reporter) so a red run means a real failure, not a temp collision.

    The prod build is NOT a test target here -- it is verified separately by
    scripts/jcc-v1.2-up.ps1 (serve_visual.py health) and by the presence of
    dist/. This script covers the SRC-level tests only.

    Usage:
      .\scripts\test-visual-ui.ps1            # typecheck + vitest run
      .\scripts\test-visual-ui.ps1 -TypecheckOnly
      .\scripts\test-visual-ui.ps1 -TestOnly

    Exit: 0 = all green, 1 = typecheck failed, 2 = tests failed, 3 = prereq missing.
#>

[CmdletBinding()]
param(
    [switch]$TypecheckOnly,
    [switch]$TestOnly
)

$ErrorActionPreference = 'Stop'

$Root  = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$UiDir = Join-Path (Join-Path $Root 'apps') 'command_center_3d'

# --- locate node/npm ------------------------------------------------------- #
function Find-Node {
    $managed = 'C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-3'
    $node = Join-Path $managed 'node.exe'
    if (Test-Path -LiteralPath $node) { return $managed }
    $c = Get-Command 'node' -ErrorAction SilentlyContinue
    if ($c) {
        $d = Split-Path -Parent $c.Source
        if (Test-Path -LiteralPath (Join-Path $d 'node.exe')) { return $d }
    }
    return ''
}
$nodeDir = Find-Node
if (-not $nodeDir) { Write-Output 'REFUSED: node not found (managed 22.22.2-3 or on PATH).'; exit 3 }
$npm = Join-Path $nodeDir 'npm.cmd'
if (-not (Test-Path -LiteralPath $npm)) { $npm = Join-Path $nodeDir 'npm' }

if (-not (Test-Path -LiteralPath $UiDir)) { Write-Output ('REFUSED: ' + $UiDir + ' missing.'); exit 3 }

$env:PATH = $nodeDir + ';' + $env:PATH

# --- hermetic temp --------------------------------------------------------- #
$testTemp = Join-Path (Join-Path $Root 'runtime\jcc-v1.2') 'test-temp'
New-Item -ItemType Directory -Force -Path $testTemp | Out-Null
$env:TEMP = $testTemp
$env:TMP  = $testTemp
# Some toolchains also read TMPDIR / USERPROFILE temp; cover the common ones.
$env:TMPDIR = $testTemp

Write-Output 'JCC v1.2 Visual UI test (hermetic TEMP)'
Write-Output ('ui dir   : ' + $UiDir)
Write-Output ('node dir : ' + $nodeDir)
Write-Output ('test TEMP: ' + $testTemp)

# --- ensure deps (idempotent) --------------------------------------------- #
if (-not (Test-Path -LiteralPath (Join-Path $UiDir 'node_modules'))) {
    Write-Output 'node_modules missing -- npm ci'
    & cmd.exe /c ("cd /d ""{0}"" && ""{1}"" ci" -f $UiDir, $npm) 2>&1 | ForEach-Object { Write-Output $_ }
}

$overall = 0

# --- typecheck ------------------------------------------------------------- #
if (-not $TestOnly) {
    Write-Output ''
    Write-Output '=== tsc --noEmit (typecheck) ==='
    $tc = cmd.exe /c ("cd /d ""{0}"" && ""{1}"" run typecheck" -f $UiDir, $npm)
    $tc | ForEach-Object { Write-Output $_ }
    $tcExit = $LASTEXITCODE
    if ($tcExit -ne 0) {
        Write-Output ('TYPECHECK FAILED (exit ' + $tcExit + ')')
        $overall = 1
    } else {
        Write-Output 'TYPECHECK OK'
    }
}

# --- vitest run ------------------------------------------------------------ #
if (-not $TypecheckOnly) {
    Write-Output ''
    Write-Output '=== vitest run ==='
    # --no-watch (run already implies this), --reporter=basic for stable parse,
    # --pool=forks keeps each test file isolated (deterministic, no shared state).
    $vt = cmd.exe /c ("cd /d ""{0}"" && ""{1}"" run test -- --reporter=basic --pool=forks" -f $UiDir, $npm)
    $vt | ForEach-Object { Write-Output $_ }
    $vtExit = $LASTEXITCODE
    if ($vtExit -ne 0) {
        Write-Output ('TESTS FAILED (exit ' + $vtExit + ')')
        if ($overall -eq 0) { $overall = 2 }
    } else {
        Write-Output 'TESTS OK'
    }
}

Write-Output ''
if ($overall -eq 0) {
    Write-Output 'RESULT: GREEN (typecheck + tests pass in hermetic TEMP)'
} else {
    Write-Output ('RESULT: FAIL (code ' + $overall + ') -- a red run here is a REAL failure, not a temp collision')
}
exit $overall

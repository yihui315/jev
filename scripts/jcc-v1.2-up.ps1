<#
    JCC v1.2 VISUAL COMMAND CENTER -- unified start (flow A-K, W-01-v12).

    ONE orchestrator that brings the whole v1.2 surface up and verifies it,
    reusing the SAME fail-closed child scripts a human would run. It never
    re-implements ownership logic -- it calls start-jcc-v1.2.ps1,
    start-jcc-v1.2-visual-ui.ps1, mark-jcc-v1.2-runtime.ps1 and
    status-jcc-v1.2.ps1 in sequence, so the ownership rules cannot be bypassed
    by a "convenient" path.

    GREEN 4778 (frozen v1.0.1) is checked for protection only -- never started,
    stopped, or touched. If GREEN is disturbed this script still refuses to do
    anything harmful and reports the violation.

    Flow:
      A. prerequisite gate (python / venv / config)
      B. reconcile ports (refuse foreign holds on 4779 / 4780)
      C. start API control plane   (4779)  -> start-jcc-v1.2.ps1
      D. start Visual UI           (4780)  -> start-jcc-v1.2-visual-ui.ps1
      E. mark live runtime                 -> mark-jcc-v1.2-runtime.ps1
      F. health wait (re-probe both endpoints)
      G. GREEN protection check    (4778 must stay UP + untouched)
      H. runtime DB present
      I. compose status            -> status-jcc-v1.2.ps1
      J. summary + next-hints
      K. exit code (0 only when API + UI UP, GREEN protected)

    Exit: 0 = all up, 3 = refused (foreign/prereq), 4 = a half failed to come up.
#>

[CmdletBinding()]
param(
    [int]$ApiPort = 0,
    [int]$UiPort = 4780,
    [int]$HealthTimeoutSec = 90
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccV12Paths -Root $Root
$python = Get-JccPython -Root $Root

$apiPortResolved = $ApiPort
if ($apiPortResolved -le 0) { $apiPortResolved = Get-JccV12Port -Root $Root -Python $python }
$greenPort = 4778

Write-Output 'JCC v1.2 unified start (flow A-K, W-01-v12)'
Write-Output ('repo root : ' + $Root)
Write-Output ('timestamp : ' + (Get-Date).ToString('o'))
Write-Output ('api port  : ' + $apiPortResolved)
Write-Output ('ui port   : ' + $UiPort)
Write-Output ('green     : ' + $greenPort + ' (frozen v1.0.1 -- checked, never touched)')

# --- A. prerequisite gate ------------------------------------------------- #
Write-Output ''
Write-Output 'A. prerequisite gate'
if (-not $python) { Write-Output 'REFUSED: no Python interpreter.'; exit 3 }
if (-not (Test-JccPython -Python $python)) { Write-Output ('REFUSED: ' + $python + ' does not run.'); exit 3 }
if (-not (Test-Path -LiteralPath $Paths.Config)) { Write-Output ('REFUSED: config ' + $Paths.Config + ' missing.'); exit 3 }
$serveScript = Join-Path (Join-Path $Root 'apps') 'command_center_3d\serve_visual.py'
if (-not (Test-Path -LiteralPath $serveScript)) {
    Write-Output ('REFUSED: ' + $serveScript + ' missing (build: cd apps\command_center_3d; npm ci; npm run build).')
    exit 3
}
Write-Output '   OK (python + config + serve_visual.py present)'

# --- B. reconcile ports (refuse foreign holds) ----------------------------- #
Write-Output ''
Write-Output 'B. reconcile ports (refuse foreign holds)'
foreach ($p in @($apiPortResolved, $UiPort)) {
    $o = Get-JccPortOwner -Port $p
    if ($o.Pid -ne 0) {
        # A listener exists. If it is provably ours (marker + cmdline), it is fine;
        # otherwise refuse before doing anything.
        $info = Get-JccProcessInfo -ProcessId $o.Pid
        $cl = [string]$info.CommandLine
        $ours = (($p -eq $apiPortResolved) -and ($cl -like '*-m jcc_core.run*')) -or `
               (($p -eq $UiPort) -and ($cl -like '*serve_visual.py*'))
        if (-not $ours) {
            Write-Output ('REFUSED: port ' + $p + ' held by FOREIGN pid ' + $o.Pid + ': ' + $cl)
            Write-Output '         Leave it alone; do not start v1.2 on top of it.'
            exit 3
        }
        Write-Output ('   port ' + $p + ': already ours (pid ' + $o.Pid + ') -- will verify, not restart')
    } else {
        Write-Output ('   port ' + $p + ': free')
    }
}

# --- C. start API ---------------------------------------------------------- #
Write-Output ''
Write-Output 'C. start API control plane (4779)'
$cApi = Invoke-JccScript -Root $Root -Name 'start-jcc-v1.2.ps1' -ScriptArgs @('-Port', ([string]$apiPortResolved), '-HealthTimeoutSec', ([string]$HealthTimeoutSec))
foreach ($l in $cApi.Lines) { Write-Output $l }
Write-Output ('   start-jcc-v1.2.ps1 exit: ' + $cApi.Exit)
if ($cApi.Exit -ne 0) { Write-Output 'REFUSED: API did not come up (exit ' + $cApi.Exit + '). Aborting unified start.'; exit 4 }

# --- D. start Visual UI ---------------------------------------------------- #
Write-Output ''
Write-Output 'D. start Visual UI (4780)'
$cUi = Invoke-JccScript -Root $Root -Name 'start-jcc-v1.2-visual-ui.ps1' -ScriptArgs @('-Port', ([string]$UiPort))
foreach ($l in $cUi.Lines) { Write-Output $l }
Write-Output ('   start-jcc-v1.2-visual-ui.ps1 exit: ' + $cUi.Exit)
if ($cUi.Exit -ne 0) { Write-Output 'REFUSED: Visual UI did not come up (exit ' + $cUi.Exit + '). API is up; fix the UI and re-run.'; exit 4 }

# --- E. mark live runtime ------------------------------------------------- #
Write-Output ''
Write-Output 'E. mark live runtime (ownership markers)'
$cMark = Invoke-JccScript -Root $Root -Name 'mark-jcc-v1.2-runtime.ps1'
foreach ($l in $cMark.Lines) { Write-Output $l }
Write-Output ('   mark-jcc-v1.2-runtime.ps1 exit: ' + $cMark.Exit)

# --- F. health wait (re-probe) --------------------------------------------- #
Write-Output ''
Write-Output 'F. health wait (re-probe both endpoints)'
$apiHealth = Invoke-JccHealth -Url ('http://127.0.0.1:' + $apiPortResolved + '/api/health') -TimeoutMs 3000
$uiHealth  = Invoke-JccHealth -Url ('http://127.0.0.1:' + $UiPort + '/health') -TimeoutMs 3000
Write-Output ('   API 4779 /api/health : ' + $(if ($apiHealth.Ok) { 'HTTP ' + $apiHealth.StatusCode } else { 'DOWN (' + $apiHealth.Error + ')' }))
Write-Output ('   UI  4780 /health     : ' + $(if ($uiHealth.Ok) { 'HTTP ' + $uiHealth.StatusCode } else { 'DOWN (' + $uiHealth.Error + ')' }))

# --- G. GREEN protection check --------------------------------------------- #
Write-Output ''
Write-Output 'G. GREEN protection check (4778 must stay UP + untouched)'
$gOwner = Get-JccPortOwner -Port $greenPort
$greenState = 'UNKNOWN'
$greenPid = $gOwner.Pid
if ($greenPid -ne 0) {
    $gInfo = Get-JccProcessInfo -ProcessId $greenPid
    $gCmd = [string]$gInfo.CommandLine
    $gMarkerPath = Join-Path (Join-Path $Root 'runtime\jcc') 'jcc.ownership.json'
    $gMarkerPresent = Test-Path -LiteralPath $gMarkerPath
    if (($gCmd -like '*-m jcc_core.run*') -and $gMarkerPresent) {
        $greenState = 'UP (protected, untouched)'
    } else {
        $greenState = 'UP but UNEXPECTED -- VIOLATION'
        Write-Output ('   !! GREEN_PROTECTION_VIOLATION: 4778 answers but cmdline/marker unexpected: ' + $gCmd)
    }
} else {
    $greenState = 'DOWN -- VIOLATION'
    Write-Output '   !! GREEN_PROTECTION_VIOLATION: frozen GREEN 4778 is not listening.'
}
Write-Output ('   GREEN 4778 : ' + $greenState + ' (pid ' + $greenPid + ')')

# --- H. runtime DB present ------------------------------------------------- #
Write-Output ''
Write-Output 'H. runtime DB present'
$dbPath = Join-Path $Paths.Runtime 'jcc.db'
$dbPresent = Test-Path -LiteralPath $dbPath
Write-Output ('   ' + $dbPath + ' -> ' + $(if ($dbPresent) { 'present' } else { 'absent (will be created on first start)' }))

# --- I. compose status ----------------------------------------------------- #
Write-Output ''
Write-Output 'I. compose status'
$cStatus = Invoke-JccScript -Root $Root -Name 'status-jcc-v1.2.ps1'
foreach ($l in $cStatus.Lines) { Write-Output $l }

# --- J/K. summary + exit --------------------------------------------------- #
Write-Output ''
Write-Output 'J. summary'
$apiUp = $apiHealth.Ok
$uiUp  = $uiHealth.Ok
$greenOk = ($greenState -eq 'UP (protected, untouched)')
Write-Output ('   API up=' + $apiUp + '  UI up=' + $uiUp + '  GREEN protected=' + $greenOk)

if (-not $greenOk) {
    Write-Output 'RESULT: DEGRADED -- GREEN protection VIOLATION (investigate before any further action)'
    exit 4
}
if ($apiUp -and $uiUp) {
    Write-Output 'RESULT: UP (v1.2) -- GREEN protected'
    Write-Output 'K. next: .\scripts\status-jcc-v1.2.ps1 | .\scripts\stop-jcc-v1.2.ps1 | .\scripts\stop-jcc-v1.2-visual-ui.ps1'
    exit 0
}
Write-Output 'RESULT: DEGRADED -- API or UI not verified'
exit 4

<#
    JCC v1.2 VISUAL COMMAND CENTER -- combined service status (W-05-v12).

    Reports the FULL v1.2 surface, not just a single port:
      * API control plane  (127.0.0.1:4779, python -m jcc_core.run, config\jcc-v1.2.yaml)
      * Visual UI          (127.0.0.1:4780, apps\command_center_3d\serve_visual.py)
      * runtime DB         (runtime\jcc-v1.2\jcc.db)
      * provider / actuation from the live /api/health payload
      * GREEN protection   (127.0.0.1:4778 must stay UP and untouched)

    Every "this is ours" claim is verified through Test-JccOwnership: marker +
    pid + command line + port owner + ancestry must agree. A port that answers
    but is not provably ours is reported as foreign and never signaled.

    Exit code: 0 = UP, 2 = DEGRADED, 1 = DOWN (or unverified).
#>

[CmdletBinding()]
param(
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccV12Paths -Root $Root

Write-Output 'JCC v1.2 status (W-05-v12)'
Write-Output ('repo root   : ' + $Root)
$now = Get-Date
Write-Output ('timestamp   : ' + $now.ToString('o'))

$python = Get-JccPython -Root $Root
$apiPort = $Port
if ($apiPort -le 0) { $apiPort = Get-JccV12Port -Root $Root -Python $python }
$uiPort = 4780   # canonical v1.2 Visual UI (static SPA served by serve_visual.py)
$greenPort = 4778

$warnings = New-Object System.Collections.Generic.List[string]

# ---------------------------------------------------------------------------
# GREEN protection (must stay UP + untouched; we never modify it)
# ---------------------------------------------------------------------------
$green = @{ State = 'UNKNOWN'; Pid = 0; Detail = '' }
try {
    $greenOwner = Get-JccPortOwner -Port $greenPort
    $green.Pid = $greenOwner.Pid
    if ($greenOwner.Pid -ne 0) {
        $gInfo = Get-JccProcessInfo -ProcessId $greenOwner.Pid
        $greenCmd = [string]$gInfo.CommandLine
        $greenMarkerPath = Join-Path (Join-Path $Root 'runtime\jcc') 'jcc.ownership.json'
        $greenMarkerPresent = Test-Path -LiteralPath $greenMarkerPath
        $greenUntouched = ($greenCmd -like '*-m jcc_core.run*') -and $greenMarkerPresent
        if ($greenUntouched) {
            $green.State = 'UP (protected, untouched)'
        } else {
            $green.State = 'UP but UNEXPECTED command line'
            $warnings.Add('GREEN_PROTECTION_WARNING: GREEN 4778 is answering but its command line / marker is unexpected: ' + $greenCmd)
        }
    } else {
        $green.State = 'DOWN'
        $warnings.Add('GREEN_PROTECTION_VIOLATION: frozen GREEN service on 4778 is NOT listening -- this must never happen. Investigate before any further action.')
    }
} catch {
    $green.State = 'UNKNOWN'
    $warnings.Add('GREEN_PROTECTION_WARNING: could not probe GREEN 4778: ' + $_.Exception.Message)
}
Write-Output ('  GREEN 4778  : ' + $green.State + '  (pid ' + $green.Pid + ', frozen v1.0.1 -- never touched)')

# ---------------------------------------------------------------------------
# API control plane (4779)
# ---------------------------------------------------------------------------
$apiHealthUrl = 'http://127.0.0.1:' + $apiPort + '/api/health'
$apiOwner = Get-JccPortOwner -Port $apiPort
$apiMarker = Get-JccMarker -Paths $Paths
$apiHealth = Invoke-JccHealth -Url $apiHealthUrl -TimeoutMs 3000
$apiOwn = Test-JccOwnership -Paths $Paths -Port $apiPort -Root $Root

$apiUp = $false
$apiSummary = '-'
$provider = '-'
$actuation = '-'
$policy = '-'
$wal = '-'
if ($apiHealth.Ok) {
    $hj = Read-JccHealthJson -Body $apiHealth.Body
    if ($hj) {
        $provider = [string]$hj.jev_provider
        $actuation = [string]$hj.actuation_enabled
        $policy = [string]$hj.policy_version
        $wal = [string]$hj.wal
        $eps = 0
        if ($hj.open_get_endpoints) { $eps = @($hj.open_get_endpoints).Count }
        $apiSummary = ('status=' + $hj.status + ' provider=' + $provider +
                       ' actuation=' + $actuation + ' policy=' + $policy +
                       ' wal=' + $wal + ' open_get_endpoints=' + $eps)
    } else {
        $apiSummary = ('non-JSON body: ' + $apiHealth.Body)
    }
    $apiUp = $apiOwn.Ok
} elseif ($apiHealth.Error) {
    $apiSummary = $apiHealth.Error
}

if ($apiHealth.Ok -and -not $apiOwn.Ok) {
    $warnings.Add('API_OWNERSHIP_WARNING: 4779 answers but ownership was NOT verified: ' + $apiOwn.Reason + ' -- treat as foreign, do NOT stop it with stop-jcc-v1.2.ps1.')
    $apiUp = $false
}

# ---------------------------------------------------------------------------
# Visual UI (4780)
# ---------------------------------------------------------------------------
$uiHealthUrl = 'http://127.0.0.1:' + $uiPort + '/health'
$uiOwner = Get-JccPortOwner -Port $uiPort
$uiUp = $false
$uiSummary = '-'
if ($uiOwner.Pid -ne 0) {
    $uInfo = Get-JccProcessInfo -ProcessId $uiOwner.Pid
    $uCmd = [string]$uInfo.CommandLine
    $uiCmdOk = ($uCmd -like '*serve_visual.py*') -or ($uCmd -like '*command_center_3d*')
    if (-not $uiCmdOk) {
        $warnings.Add('UI_OWNERSHIP_WARNING: 4780 is held by pid ' + $uiOwner.Pid + ' but its command line is not the v1.2 Visual UI server: ' + $uCmd)
    }
    $uiHealth = Invoke-JccHealth -Url $uiHealthUrl -TimeoutMs 3000
    if ($uiHealth.Ok -and $uiCmdOk) {
        $uiUp = $true
        $uiSummary = ('HTTP ' + $uiHealth.StatusCode + ' from ' + $uiHealthUrl)
    } elseif ($uiHealth.Ok) {
        $uiSummary = ('answers (HTTP ' + $uiHealth.StatusCode + ') but command line unverified')
    } else {
        $uiSummary = ('port owner pid ' + $uiOwner.Pid + ' but no HTTP on ' + $uiHealthUrl + ': ' + $uiHealth.Error)
    }
} else {
    $uiSummary = 'nobody (port 4780 free)'
}

# ---------------------------------------------------------------------------
# runtime DB
# ---------------------------------------------------------------------------
$dbPath = Join-Path $Paths.Runtime 'jcc.db'
$dbExists = Test-Path -LiteralPath $dbPath
$dbState = if ($dbExists) { 'present' } else { 'absent (will be created on first start)' }
if (-not $dbExists) { $warnings.Add('RUNTIME_DB_NOTE: ' + $dbPath + ' not yet present.') }

# ---------------------------------------------------------------------------
# compose
# ---------------------------------------------------------------------------
Write-Output ('')
Write-Output ('  API  4779   : ' + ($(if ($apiUp) { 'UP' } else { 'DOWN' })) + '  (v1.2, isolated from GREEN 4778)')
Write-Output ('    owner pid  : ' + $apiOwner.Pid + '  (via ' + $apiOwner.Method + ')')
Write-Output ('    health     : ' + $apiSummary)
Write-Output ('    provider   : ' + $provider + '   actuation=' + $actuation + '   policy=' + $policy + '   wal=' + $wal)
Write-Output ('    db         : ' + $dbPath + ' -> ' + $dbState)
Write-Output ('  UI   4780   : ' + ($(if ($uiUp) { 'UP' } else { 'DOWN' })) + '  (Visual Command Center SPA)')
Write-Output ('    owner pid  : ' + $uiOwner.Pid + '  (via ' + $uiOwner.Method + ')')
Write-Output ('    health     : ' + $uiSummary)
Write-Output ('    config     : ' + $Paths.Config)
Write-Output ('    marker     : ' + $Paths.MarkerFile + ' -> ' + ($(if ($apiMarker) { 'present' } else { 'absent' })))
Write-Output ('    pid file   : ' + $Paths.PidFile + ' -> ' + ($(if (Test-Path -LiteralPath $Paths.PidFile) { 'present' } else { 'absent' })))

$result = 'DOWN'
$exitCode = 1
if ($apiUp) {
    if ($uiUp) {
        $result = 'UP'
        $exitCode = 0
    } else {
        $result = 'DEGRADED'
        $exitCode = 2
        $warnings.Add('RESULT_DEGRADED: API is UP but the Visual UI on 4780 is not verified -- the command center is only partially available.')
    }
}

Write-Output ('')
foreach ($w in @($warnings)) {
    Write-Output ('  ' + $w)
}
if (@($warnings).Count -gt 0) { Write-Output ('') }
Write-Output ('  RESULT: ' + $result + ' (v1.2)')
Write-Output ('    start api : .\scripts\start-jcc-v1.2.ps1')
Write-Output ('    start ui  : .\scripts\start-jcc-v1.2-visual-ui.ps1')
Write-Output ('    stop all  : .\scripts\stop-jcc-v1.2.ps1')
exit $exitCode

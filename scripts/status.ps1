<#
    JCC service status (W-05). Windows PowerShell 5.1 ONLY.

    Prints one table row: service state, pid, port, uptime, health summary.
    Reads the same ownership marker that stop.ps1 verifies against, so "up" here
    means "the process WE started is answering", not "something answers".

    Exit code: 0 when the verified service is up and healthy, non-zero otherwise.
#>

[CmdletBinding()]
param(
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccPaths -Root $Root

Write-Output ('JCC status (W-05)')
Write-Output ('repo root   : ' + $Root)
Write-Output ('timestamp   : ' + (Get-Date).ToString('o'))

$python = Get-JccPython -Root $Root
$resolvedPort = $Port
if ($resolvedPort -le 0) {
    $resolvedPort = Get-JccPort -Root $Root -Python $python
}
$healthUrl = 'http://127.0.0.1:' + $resolvedPort + '/api/health'

$marker  = Get-JccMarker -Paths $Paths
$pidFilePid = Get-JccPidFromFile -Paths $Paths
$owner   = Get-JccPortOwner -Port $resolvedPort
$health  = Invoke-JccHealth -Url $healthUrl -TimeoutMs 3000

$state = 'DOWN'
$pidShown = '-'
$uptime = '-'
$healthSummary = '-'
$detail = ''

if ($health.Ok) {
    $state = 'UP'
    $hj = Read-JccHealthJson -Body $health.Body
    if ($hj) {
        $eps = 0
        if ($hj.open_get_endpoints) { $eps = @($hj.open_get_endpoints).Count }
        $healthSummary = ('status=' + $hj.status +
                          ' provider=' + $hj.jev_provider +
                          ' actuation=' + $hj.actuation_enabled +
                          ' policy=' + $hj.policy_version +
                          ' wal=' + $hj.wal +
                          ' open_get_endpoints=' + $eps)
    } else {
        $healthSummary = ('non-JSON body: ' + $health.Body)
    }
    $detail = ('HTTP ' + $health.StatusCode + ' from ' + $healthUrl)
} else {
    if ($health.Error) { $healthSummary = $health.Error }
    $detail = ('no HTTP response on ' + $healthUrl)
}

if ($owner.Pid -ne 0) {
    $pinfo = Get-JccProcessInfo -ProcessId $owner.Pid
    $stateOwner = 'pid ' + $owner.Pid + ' (owner of port ' + $resolvedPort + ' via ' + $owner.Method + ')'
} else {
    $pinfo = $null
    $stateOwner = 'nobody (port ' + $resolvedPort + ' free)'
}

$pidFilePresent = 'absent'
if (Test-Path -LiteralPath $Paths.PidFile) { $pidFilePresent = 'present' }
$markerPresent = 'absent'
if (Test-Path -LiteralPath $Paths.MarkerFile) { $markerPresent = 'present' }

if ($pidFilePid -gt 0) {
    $pidShown = ([string]$pidFilePid)
    $info = Get-JccProcessInfo -ProcessId $pidFilePid
    if ($info.CreationDate) {
        try {
            $created = [datetime]::Parse($info.CreationDate)
            $uptime = (Get-JccElapsed -Since $created)
        } catch {
            $uptime = '-'
        }
    }
    if ($info.CommandLine) {
        Write-Output ('command line: ' + $info.CommandLine)
    }
} elseif ($marker -and $marker.pid) {
    $pidShown = ('recorded ' + $marker.pid + ' (not running)')
}

if ($uptime -eq '-' -and $marker -and $marker.start_time_local) {
    try {
        $st = [datetime]$marker.start_time_local
        $uptime = (Get-JccElapsed -Since $st)
    } catch { }
}

Write-Output ('')
Write-Output ('  service      : ' + $state)
Write-Output ('  pid          : ' + $pidShown)
Write-Output ('  port         : ' + $resolvedPort + ' (bound to 127.0.0.1 only)')
Write-Output ('  port owner   : ' + $stateOwner)
Write-Output ('  uptime       : ' + $uptime)
Write-Output ('  health       : ' + $healthSummary)
Write-Output ('  pid file     : ' + $Paths.PidFile + ' -> ' + $pidFilePresent)
Write-Output ('  marker       : ' + $Paths.MarkerFile + ' -> ' + $markerPresent)
Write-Output ('  detail       : ' + $detail)
Write-Output ('')
Write-Output ('  health url   : ' + $healthUrl)
Write-Output ('')

if ($state -eq 'UP') {
    $own = Test-JccOwnership -Paths $Paths -Port $resolvedPort -Root $Root
    if (-not $own.Ok) {
        Write-Output ('WARNING: answering, but ownership was not verified: ' + $own.Reason)
        Write-Output ('         treat the listener as unowned and do NOT stop it with stop.ps1.')
        exit 2
    }
    Write-Output ('RESULT: UP (verified: pid, ownership marker, command line and port owner agree)')
    exit 0
}

Write-Output ('RESULT: DOWN')
Write-Output ('         start it with: .\scripts\start.ps1')
exit 1

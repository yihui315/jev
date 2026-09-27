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
# ONE instant for this entire report.
#
# MEASURED DEFECT (2026-09-27): the timestamp used to be printed here while the
# uptime was computed much later -- after Get-JccPort below spawns a Python
# process -- so the two lines came from instants up to 63 SECONDS apart. A reader
# comparing them would compute an uptime that cannot be true for the timestamp
# printed beside it, which is exactly the "impossible uptime" that was reported.
# Every time-derived value in this report now comes from $now.
$now = Get-Date
Write-Output ('timestamp   : ' + $now.ToString('o'))

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

$uptimeReading = $null
$warnings = @()
if ($pidFilePid -gt 0) {
    $pidShown = ([string]$pidFilePid)
    $info = Get-JccProcessInfo -ProcessId $pidFilePid
    if ($info.CommandLine) {
        Write-Output ('command line: ' + $info.CommandLine)
    }

    # AUTHORITATIVE: a property of the live process itself.
    $procStart = $null
    try {
        $procStart = (Get-Process -Id $pidFilePid -ErrorAction Stop).StartTime
    } catch {
        $procStart = $null
    }

    # CROSS-CHECK ONLY. Get-JccProcessInfo stringifies the CIM datetime, which
    # drops the sub-second part and the offset, so this is a corroboration, never
    # the value that gets printed when the process can be asked directly.
    $cimCreated = $null
    if ($info.CreationDate) {
        try { $cimCreated = [datetime]::Parse($info.CreationDate) } catch { $cimCreated = $null }
    }

    # PROVENANCE ONLY -- the marker proves IDENTITY ("this is the process we
    # started"), and is deliberately not allowed to answer for uptime.
    $markerStart = $null
    if ($marker -and $marker.start_time_local) {
        try { $markerStart = [datetime]$marker.start_time_local } catch { $markerStart = $null }
    }

    $uptimeReading = Get-JccUptimeReading -ProcessStart $procStart `
        -CimCreation $cimCreated -MarkerStart $markerStart -Now $now
    $uptime = $uptimeReading.Uptime
    $warnings = @($uptimeReading.Warnings)
} elseif ($marker -and $marker.pid) {
    $pidShown = ('recorded ' + $marker.pid + ' (not running)')
}

$launcherShown = '-'
if ($marker -and $marker.launcher_pid) { $launcherShown = ([string]$marker.launcher_pid) }

Write-Output ('')
Write-Output ('  service      : ' + $state)
Write-Output ('  pid          : ' + $pidShown + '   (service pid: holds the socket)')
Write-Output ('  launcher pid : ' + $launcherShown + '   (the process start.ps1 spawned)')
Write-Output ('  port         : ' + $resolvedPort + ' (bound to 127.0.0.1 only)')
Write-Output ('  port owner   : ' + $stateOwner)
$uptimeSuffix = ''
if ($uptimeReading) { $uptimeSuffix = '   (source: ' + $uptimeReading.Source + ')' }
Write-Output ('  uptime       : ' + $uptime + $uptimeSuffix)
Write-Output ('  health       : ' + $healthSummary)
Write-Output ('  pid file     : ' + $Paths.PidFile + ' -> ' + $pidFilePresent)
Write-Output ('  marker       : ' + $Paths.MarkerFile + ' -> ' + $markerPresent)
Write-Output ('  detail       : ' + $detail)
Write-Output ('')
if ($uptimeReading) {
    Write-Output ('  uptime provenance (the marker proves identity, never uptime):')
    Write-Output ('    process  : ' + $uptimeReading.ProcessStartIso + '   <- authoritative')
    Write-Output ('    cim      : ' + $uptimeReading.CimCreationIso + '   <- cross-check')
    Write-Output ('    marker   : ' + $uptimeReading.MarkerStartIso + '   <- provenance only')
    Write-Output ('')
}
foreach ($w in @($warnings)) {
    Write-Output ('  ' + $w)
}
if (@($warnings).Count -gt 0) {
    Write-Output ('  (a warning is an observation about the clocks, not about the service: it never changes UP/DOWN)')
    Write-Output ('')
}
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

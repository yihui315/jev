<#
    JCC service restart (W-04). Windows PowerShell 5.1 ONLY.

    stop.ps1 (verified, may refuse and exit non-zero) -> start.ps1 (verified).
    Both halves are executed as the SAME child scripts a human would run, so the
    ownership rules cannot be bypassed by a "convenient" restart path: an unknown
    or foreign process is never touched, and a failed stop never starts anything.

    Exit code: the exit code of whichever half failed, 0 only when the service is
    verified up and healthy afterwards. Start.ps1 returning 0 is NOT enough, so a
    service that is not verified afterwards exits 6 (post-condition failed), with
    the same evidence scripts/status.ps1 would print for a DOWN.
#>

[CmdletBinding()]
param(
    [int]$Port = 0,
    [int]$HealthTimeoutSec = 90
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccPaths -Root $Root

Write-Output ('JCC restart (W-04)')
Write-Output ('repo root : ' + $Root)
Write-Output ('timestamp : ' + (Get-Date).ToString('o'))

$resolvedPort = $Port
if ($resolvedPort -le 0) {
    $p = Get-JccPython -Root $Root
    $resolvedPort = Get-JccPort -Root $Root -Python $p
}
Write-Output ('port      : ' + $resolvedPort)
Write-Output ('')

# --- pre-flight: never start into (or on top of) a foreign hold ---------- #
$owner = Get-JccPortOwner -Port $resolvedPort
$pidFilePid = Get-JccPidFromFile -Paths $Paths
if ($owner.Pid -ne 0 -and $owner.Pid -ne $pidFilePid) {
    $info = Get-JccProcessInfo -ProcessId $owner.Pid
    Write-Output ('REFUSED before any stop/start: port ' + $resolvedPort + ' is LISTENING, owned by pid ' +
        $owner.Pid + ' via ' + $owner.Method + '.')
    Write-Output ('  command line: ' + $info.CommandLine)
    Write-Output ('  That process is not ours; it is never signalled. Resolve the holder, re-run.')
    exit 3
}

# --- half 1: stop -------------------------------------------------------- #
Write-Output ('=== stop.ps1 ===')
$stopRes = Invoke-JccScript -Root $Root -Name 'stop.ps1' -ScriptArgs @('-Port', ([string]$resolvedPort))
foreach ($l in $stopRes.Lines) { Write-Output $l }
Write-Output ('stop.ps1 exit code: ' + $stopRes.Exit)
Write-Output ('')

if ($stopRes.Exit -ne 0) {
    Write-Output ('REFUSED: stop.ps1 refused (exit ' + $stopRes.Exit + '). start.ps1 was NOT run.')
    Write-Output ('         Nothing was signalled by restart.ps1 either.')
    exit $stopRes.Exit
}

$waitUntil = (Get-Date).AddSeconds(3)
while ((Get-Date) -lt $waitUntil) {
    $probe = Invoke-JccHealth -Url ('http://127.0.0.1:' + $resolvedPort + '/api/health') -TimeoutMs 800
    if (-not $probe.Ok) { break }
    Start-Sleep -Milliseconds 300
}

# --- half 2: start ------------------------------------------------------- #
Write-Output ('=== start.ps1 ===')
$startRes = Invoke-JccScript -Root $Root -Name 'start.ps1' -ScriptArgs @('-Port', ([string]$resolvedPort), '-HealthTimeoutSec', ([string]$HealthTimeoutSec))
foreach ($l in $startRes.Lines) { Write-Output $l }
$code = $startRes.Exit
Write-Output ('')

if ($code -ne 0) {
    Write-Output ('RESTART FAILED: start.ps1 exit ' + $code + '. The service is DOWN.')
    exit $code
}

# --- post-condition: the exit code of restart.ps1 is EARNED here ---------- #
# start.ps1 exits 0 the moment it has written the pid file and the ownership
# marker; it cannot know what the service does afterwards (measured on this
# host: a service that start.ps1 brought up died again while restart.ps1 was
# still printing RESTART OK, and restart.ps1 never noticed). So neither the
# return code of start.ps1 nor the zero of restart.ps1 says anything about the
# service. From here on the ONLY way out with exit 0 is the same evidence
# scripts/status.ps1 demands when it prints UP: the pid file pid, the port owner
# and a live process are the same pid, that process answers /api/health, and
# Test-JccOwnership agrees. Anything less is a failure reported exactly the way
# status.ps1 reports a DOWN; nothing is signalled by this check.

$postHealthUrl = 'http://127.0.0.1:' + $resolvedPort + '/api/health'
$postAttempts = 0
$postWhy = @()

while ($true) {
    $postAttempts++

    $h = Invoke-JccHealth -Url $postHealthUrl -TimeoutMs 3000
    $postPid = Get-JccPidFromFile -Paths $Paths
    $owner = Get-JccPortOwner -Port $resolvedPort
    $postInfo = Get-JccProcessInfo -ProcessId $postPid
    $postOwn = Test-JccOwnership -Paths $Paths -Port $resolvedPort -Root $Root

    $why = New-Object System.Collections.Generic.List[string]
    if (-not $h.Ok) { $why.Add('no HTTP 200 on ' + $postHealthUrl + ': ' + $h.Error) }
    if ($postPid -le 0) { $why.Add('no service pid recorded in ' + $Paths.PidFile) }
    if ($owner.Pid -eq 0) {
        $why.Add('nothing is LISTENING on port ' + $resolvedPort)
    } elseif ($postPid -gt 0 -and $owner.Pid -ne $postPid) {
        $why.Add('port ' + $resolvedPort + ' is LISTENING, owned by pid ' + $owner.Pid +
                 ' (' + $owner.Method + '), but ' + $Paths.PidFile + ' records pid ' + $postPid)
    }
    if ($postPid -gt 0 -and -not $postInfo.Alive) {
        $why.Add('the recorded service pid ' + $postPid + ' is not running')
    }
    if (-not $postOwn.Ok) { $why.Add('ownership not verified: ' + $postOwn.Reason) }

    if ($why.Count -eq 0) {
        Write-Output ('post-condition: pid file ' + $postPid +
                      ' == port owner ' + $owner.Pid + ' (' + $owner.Method + '), HTTP ' +
                      $h.StatusCode + ' from ' + $postHealthUrl)
        break
    }

    if ($postAttempts -ge 6) {
        $postWhy = @($why)
        break
    }
    Start-Sleep -Seconds 1
}

if ($postWhy.Count -gt 0) {
    Write-Output ('')
    Write-Output ('RESTART FAILED: start.ps1 exited 0, but the post-condition below does not hold.')
    Write-Output ('  checked     : ' + $postHealthUrl + ' (pid file, port owner, live process, ownership)')
    Write-Output ('  pid file    : ' + $Paths.PidFile + ' -> ' + $postPid)
    Write-Output ('  marker      : ' + $Paths.MarkerFile)
    Write-Output ('  port owner  : ' + $resolvedPort + ' -> ' + $owner.Pid + ' (' + $owner.Method + ')')
    Write-Output ('  health      : ' + $h.Error)
    Write-Output ('  attempts    : ' + $postAttempts + ' over ~' + $postAttempts + 's')
    foreach ($w in $postWhy) { Write-Output ('  why         : ' + $w) }
    Write-Output ('  The service was NOT verified up. Nothing is signalled by restart.ps1.')
    Write-Output ('RESULT: DOWN')
    Write-Output ('         start it with: .\scripts\start.ps1')
    exit 6
}

Write-Output ('RESTART OK')
Write-Output ('next      : .\scripts\status.ps1')
exit 0

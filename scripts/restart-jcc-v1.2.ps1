<#
    JCC v1.2 VISUAL COMMAND CENTER -- service restart (W-04-v12).

    stop-jcc-v1.2.ps1 (verified, may refuse and exit non-zero) ->
    start-jcc-v1.2.ps1 (verified). Both halves are executed as the SAME child
    scripts a human would run, so the ownership rules cannot be bypassed by a
    "convenient" restart path: an unknown or foreign process is never touched,
    and a failed stop never starts anything. This is the v1.2 surface only;
    GREEN 4778 is never examined.

    Exit code: the exit code of whichever half failed, 0 only when the v1.2
    service is verified up and healthy afterwards.
#>

[CmdletBinding()]
param(
    [int]$Port = 0,
    [int]$HealthTimeoutSec = 90
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccV12Paths -Root $Root

Write-Output 'JCC v1.2 restart (W-04-v12)'
Write-Output ('repo root : ' + $Root)
Write-Output ('timestamp : ' + (Get-Date).ToString('o'))

$resolvedPort = $Port
if ($resolvedPort -le 0) {
    $p = Get-JccPython -Root $Root
    $resolvedPort = Get-JccV12Port -Root $Root -Python $p
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
Write-Output '=== stop-jcc-v1.2.ps1 ==='
$stopRes = Invoke-JccScript -Root $Root -Name 'stop-jcc-v1.2.ps1' -ScriptArgs @('-Port', ([string]$resolvedPort))
foreach ($l in $stopRes.Lines) { Write-Output $l }
Write-Output ('stop-jcc-v1.2.ps1 exit code: ' + $stopRes.Exit)
Write-Output ('')

if ($stopRes.Exit -ne 0) {
    Write-Output ('REFUSED: stop-jcc-v1.2.ps1 refused (exit ' + $stopRes.Exit + '). start-jcc-v1.2.ps1 was NOT run.')
    Write-Output ('         Nothing was signalled by restart-jcc-v1.2.ps1 either.')

    $stalePid = Get-JccPidFromFile -Paths $Paths
    $staleOwner = Get-JccPortOwner -Port $resolvedPort
    $staleInfo = Get-JccProcessInfo -ProcessId $stalePid
    if ($stalePid -gt 0 -and -not $staleInfo.Alive -and $staleOwner.Pid -eq 0) {
        Write-Output ('  cause      : stop-jcc-v1.2.ps1 refused because ' + $Paths.PidFile + ' records pid ' +
            $stalePid + ', which is not running, and nothing is LISTENING on port ' + $resolvedPort + '.')
        Write-Output ('               That is the residue of a service that died without these')
        Write-Output ('               scripts signalling it (out-of-band kill, host reboot, or a')
        Write-Output ('               process that exited on its own).')
        Write-Output ('  confirm    : netstat -ano | findstr :' + $resolvedPort +
                      '  -> expect no LISTENING line')
        Write-Output ('  remedy     : once you have confirmed pid ' + $stalePid + ' is really gone,')
        Write-Output ('               delete ' + $Paths.PidFile + ' and ' + $Paths.MarkerFile +
                      ' and re-run this script.')
        Write-Output ('               Nothing is signalled by restart-jcc-v1.2.ps1 now or after that.')
    }
    exit $stopRes.Exit
}

$waitUntil = (Get-Date).AddSeconds(3)
while ((Get-Date) -lt $waitUntil) {
    $probe = Invoke-JccHealth -Url ('http://127.0.0.1:' + $resolvedPort + '/api/health') -TimeoutMs 800
    if (-not $probe.Ok) { break }
    Start-Sleep -Milliseconds 300
}

# --- half 2: start ------------------------------------------------------- #
Write-Output '=== start-jcc-v1.2.ps1 ==='
$startRes = Invoke-JccScript -Root $Root -Name 'start-jcc-v1.2.ps1' -ScriptArgs @('-Port', ([string]$resolvedPort), '-HealthTimeoutSec', ([string]$HealthTimeoutSec))
foreach ($l in $startRes.Lines) { Write-Output $l }
$code = $startRes.Exit
Write-Output ('')

if ($code -ne 0) {
    Write-Output ('RESTART FAILED: start-jcc-v1.2.ps1 exit ' + $code + '. The v1.2 service is DOWN.')
    exit $code
}

# --- post-condition: the exit code of restart-jcc-v1.2.ps1 is EARNED here # #
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
    Write-Output ('RESTART FAILED: start-jcc-v1.2.ps1 exited 0, but the post-condition below does not hold.')
    Write-Output ('  checked     : ' + $postHealthUrl + ' (pid file, port owner, live process, ownership)')
    Write-Output ('  pid file    : ' + $Paths.PidFile + ' -> ' + $postPid)
    Write-Output ('  marker      : ' + $Paths.MarkerFile)
    Write-Output ('  port owner  : ' + $resolvedPort + ' -> ' + $owner.Pid + ' (' + $owner.Method + ')')
    Write-Output ('  health      : ' + $h.Error)
    Write-Output ('  attempts    : ' + $postAttempts + ' over ~' + $postAttempts + 's')
    foreach ($w in $postWhy) { Write-Output ('  why         : ' + $w) }
    Write-Output ('  The v1.2 service was NOT verified up. Nothing is signalled by restart-jcc-v1.2.ps1.')
    Write-Output 'RESULT: DOWN (v1.2)'
    Write-Output ('         start it with: .\scripts\start-jcc-v1.2.ps1')
    exit 6
}

Write-Output 'RESTART OK (v1.2)'
Write-Output ('next      : .\scripts\status-jcc-v1.2.ps1')
exit 0

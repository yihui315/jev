<#
    JCC service restart (W-04). Windows PowerShell 5.1 ONLY.

    stop.ps1 (verified, may refuse and exit non-zero) -> start.ps1 (verified).
    Both halves are executed as the SAME child scripts a human would run, so the
    ownership rules cannot be bypassed by a "convenient" restart path: an unknown
    or foreign process is never touched, and a failed stop never starts anything.

    Exit code: the exit code of whichever half failed, 0 only when the service is
    verified up and healthy afterwards.
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

Write-Output ('RESTART OK')
Write-Output ('next      : .\scripts\status.ps1')
exit 0

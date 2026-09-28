<#
    JCC v1.2 VISUAL COMMAND CENTER -- service stop (W-03-v12).

    RULE (hard): this script NEVER signals a process it did not start and cannot
    positively identify. Before anything happens it must satisfy ALL of:
        * runtime\jcc-v1.2\jcc-v1.2.pid exists,
        * runtime\jcc-v1.2\jcc-v1.2.ownership.json exists and agrees with that pid,
        * the process is alive,
        * its command line is "python -m jcc_core.run" from THIS repo's venv,
        * the listening owner of the v1.2 port (4779) is either that pid or nobody.
    If any of those fail the script prints a refusal and exits non-zero. It also
    refuses outright when the v1.2 port is held by a foreign process.

    This operates ONLY on the v1.2 runtime surface (runtime\jcc-v1.2). The GREEN
    service on 127.0.0.1:4778 is never examined and never touched.

    Exit codes: 0 = stopped (or already stopped), 2 = refused (would not signal),
    3 = foreign port hold / missing prerequisite, 4 = stop did not complete.
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [int]$Port = 0,
    [int]$GracefulSeconds = 20,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccV12Paths -Root $Root

Write-Output 'JCC v1.2 stop (W-03-v12)'
Write-Output ('repo root : ' + $Root)
Write-Output ('timestamp : ' + (Get-Date).ToString('o'))

$python = Get-JccPython -Root $Root
if (-not $python) { $python = '' }
if (-not $Port -or $Port -le 0) {
    $resolvedPort = Get-JccV12Port -Root $Root -Python $python
} else {
    $resolvedPort = $Port
}
Write-Output ('port      : ' + $resolvedPort)

New-JccEnsureDir -Path $Paths.Runtime | Out-Null

$marker = Get-JccMarker -Paths $Paths
$pidFilePid = Get-JccPidFromFile -Paths $Paths

# --- case 1: nothing was ever recorded and nothing is listening --------- #
if ($pidFilePid -le 0) {
    $owner = Get-JccPortOwner -Port $resolvedPort
    if ($owner.Pid -ne 0) {
        $info = Get-JccProcessInfo -ProcessId $owner.Pid
        Write-Output ('')
        Write-Output ('REFUSED: no PID file of ours, but port ' + $resolvedPort + ' is LISTENING,')
        Write-Output ('         owned by pid ' + $owner.Pid + ' via ' + $owner.Method + '.')
        Write-Output ('         command line: ' + $info.CommandLine)
        Write-Output ('         That process is not ours (no PID file, no ownership marker) so it is')
        Write-Output ('         never signalled. Identify and stop it yourself, then re-run.')
        exit 3
    }
    Write-Output ('')
    Write-Output ('NOT RUNNING: no PID file (' + $Paths.PidFile + ') and no listener on port ' + $resolvedPort + '.')
    Write-Output ('Nothing to stop (this is the v1.2 instance; GREEN 4778 is untouched).')
    if ($marker) {
        Write-Output ('A stale ownership marker exists; leaving it in place untouched (read-only).')
    }
    exit 0
}

# --- case 2: a recorded pid exists -- verify before touching anything ---- #
$own = Test-JccOwnership -Paths $Paths -Port $resolvedPort -Root $Root

if (-not $own.Ok) {
    $info = Get-JccProcessInfo -ProcessId $pidFilePid
    Write-Output ('')
    Write-Output ('REFUSED: not signalled. Verification failed for pid ' + $pidFilePid + '.')
    Write-Output ('         reason: ' + $own.Reason)
    if ($info.CommandLine) {
        Write-Output ('         actual command line: ' + $info.CommandLine)
    }
    if ($info.Alive) {
        Write-Output ('         process state  : alive')
    } else {
        Write-Output ('         process state  : not running')
    }
    Write-Output ('         Fix the cause (or clear runtime\jcc-v1.2\jcc-v1.2.pid and jcc-v1.2.ownership.json')
    Write-Output ('         yourself after confirming the pid) and re-run.')
    exit 2
}

$info = Get-JccProcessInfo -ProcessId $pidFilePid
$startRef = $null
if ($marker.start_time_local) {
    try { $startRef = [datetime]$marker.start_time_local } catch { $startRef = $null }
}
Write-Output ('')
Write-Output ('verified target (all checks passed):')
Write-Output ('  pid             : ' + $own.Pid)
Write-Output ('  command line    : ' + $info.CommandLine)
Write-Output ('  process created : ' + $info.CreationDate)
Write-Output ('  started (marker): ' + $marker.start_time)
Write-Output ('  port owner pid  : ' + $own.PortOwnerPid + ' (' + $own.PortOwnerMethod + ')')
if ($startRef) { Write-Output ('  uptime          : ' + (Get-JccElapsed -Since $startRef)) }

if ($DryRun) {
    Write-Output ('')
    Write-Output ('DRY RUN: would stop pid ' + $own.Pid + '. Nothing was signalled.')
    exit 0
}

if (-not $PSCmdlet.ShouldProcess(('pid ' + $own.Pid), 'stop the verified JCC v1.2 service process')) {
    Write-Output ('')
    Write-Output ('stop aborted by -WhatIf / user confirmation. Nothing was signalled.')
    exit 0
}

# --- the actual (single, verified) signal -------------------------------- #
$launcherPid = 0
if ($marker.launcher_pid) { $launcherPid = [int]$marker.launcher_pid }

Write-Output ('')
Write-Output ('stopping v1.2 service pid ' + $own.Pid + ' ...')
try {
    Stop-Process -Id $own.Pid -Force -ErrorAction Stop
} catch {
    Write-Output ('')
    Write-Output ('FAILED: Stop-Process on the verified pid ' + $own.Pid + ' threw: ' + $_.Exception.Message)
    Write-Output ('It has NOT been kill-looped; re-run this script to retry.')
    exit 4
}

$deadline = (Get-Date).AddSeconds($GracefulSeconds)
$exited = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
    if (-not (Test-JccProcessAlive -ProcessId $own.Pid)) { $exited = $true; break }
}

if (-not $exited) {
    Write-Output ('')
    Write-Output ('FAILED: pid ' + $own.Pid + ' is still alive after ' + $GracefulSeconds + 's. Re-run stop-jcc-v1.2.ps1.')
    exit 4
}

Write-Output ('v1.2 service pid ' + $own.Pid + ' exited.')

# The launcher (the process start-jcc-v1.2.ps1 spawned) is only touched while
# it is still alive AND is the pid recorded by OUR marker.
if ($launcherPid -gt 0 -and $launcherPid -ne $own.Pid -and (Test-JccProcessAlive -ProcessId $launcherPid)) {
    Write-Output ('stopping launcher pid ' + $launcherPid + ' ...')
    try {
        Stop-Process -Id $launcherPid -Force -ErrorAction Stop
    } catch {
        Write-Output ('WARNING: Stop-Process on launcher pid ' + $launcherPid + ' threw: ' + $_.Exception.Message)
    }
}

# Only now clean up OUR OWN bookkeeping, and only because the process is gone
# and no other pid answers on the port.
$ownerAfter = Get-JccPortOwner -Port $resolvedPort
if ($ownerAfter.Pid -ne 0 -and $ownerAfter.Pid -ne $own.Pid) {
    Write-Output ('NOTE: port ' + $resolvedPort + ' is now owned by a different pid (' + $ownerAfter.Pid + ');')
    Write-Output ('      PID/marker files were left in place for inspection.')
    exit 0
}
Remove-Item -LiteralPath $Paths.PidFile -Force -ErrorAction SilentlyContinue | Out-Null
Remove-Item -LiteralPath $Paths.MarkerFile -Force -ErrorAction SilentlyContinue | Out-Null
Write-Output ('cleaned up ' + $Paths.PidFile + ' and ' + $Paths.MarkerFile)

Write-Output ('')
Write-Output ('STOPPED (v1.2)')
Write-Output ('next      : .\scripts\status-jcc-v1.2.ps1')
exit 0

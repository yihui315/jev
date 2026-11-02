<#
    JCC v1.2 VISUAL COMMAND CENTER -- Visual UI stop (W-03b-v12).

    RULE (hard): this script NEVER signals a process it did not start and cannot
    positively identify. Before anything happens it must satisfy ALL of:
        * runtime\jcc-v1.2\visual-ui.pid exists,
        * runtime\jcc-v1.2\visual-ui.ownership.json exists and agrees with that pid,
        * the process is alive,
        * its command line is "serve_visual.py" from THIS repo,
        * the listening owner of port 4780 is either that pid or nobody.
    If any of those fail the script prints a refusal and exits non-zero. It also
    refuses outright when port 4780 is held by a foreign process.

    This operates ONLY on the v1.2 Visual UI surface. The v1.2 API (4779) and the
    GREEN service (4778) are never examined and never touched.

    Exit codes: 0 = stopped (or already stopped), 2 = refused (would not signal),
    3 = foreign port hold / missing prerequisite, 4 = stop did not complete.
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [int]$Port = 4780,
    [int]$GracefulSeconds = 20,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccV12Paths -Root $Root

$PidFile    = Join-Path $Paths.Runtime 'visual-ui.pid'
$MarkerFile = Join-Path $Paths.Runtime 'visual-ui.ownership.json'

Write-Output 'JCC v1.2 Visual UI stop (W-03b-v12)'
Write-Output ('repo root : ' + $Root)
Write-Output ('timestamp : ' + (Get-Date).ToString('o'))
Write-Output ('port      : ' + $Port)

New-JccEnsureDir -Path $Paths.Runtime | Out-Null

$marker = $null
if (Test-Path -LiteralPath $MarkerFile) {
    try { $marker = (Get-Content -LiteralPath $MarkerFile -Raw -ErrorAction Stop | ConvertFrom-Json) } catch { $marker = $null }
}
$pidFilePid = 0
if (Test-Path -LiteralPath $PidFile) {
    $v = ((Get-Content -LiteralPath $PidFile -Raw -ErrorAction SilentlyContinue) -replace '\s+','').Trim()
    if ($v -match '^\d+$') { $pidFilePid = [int]$v }
}

# --- case 1: nothing recorded and nothing listening ----------------------- #
if ($pidFilePid -le 0) {
    $owner = Get-JccPortOwner -Port $Port
    if ($owner.Pid -ne 0) {
        $info = Get-JccProcessInfo -ProcessId $owner.Pid
        Write-Output ''
        Write-Output ('REFUSED: no PID file of ours, but port ' + $Port + ' is LISTENING, owned by pid ' + $owner.Pid + ' via ' + $owner.Method + '.')
        Write-Output ('         command line: ' + $info.CommandLine)
        Write-Output '         That process is not ours (no PID file, no ownership marker) so it is never signalled.'
        Write-Output '         Identify and stop it yourself, then re-run.'
        exit 3
    }
    Write-Output ''
    Write-Output ('NOT RUNNING: no PID file (' + $PidFile + ') and no listener on port ' + $Port + '.')
    Write-Output 'Nothing to stop (Visual UI only; GREEN 4778 and API 4779 untouched).'
    if ($marker) { Write-Output 'A stale ownership marker exists; leaving it in place untouched (read-only).' }
    exit 0
}

# --- case 2: a recorded pid exists -- verify before touching -------------- #
if (-not $marker) {
    Write-Output ''
    Write-Output ('REFUSED: pid file names ' + $pidFilePid + ' but the ownership marker is missing. Not signalled.')
    Write-Output ('         Resolve manually, then delete ' + $PidFile + ' and re-run.')
    exit 2
}
if ([int]$marker.pid -ne $pidFilePid) {
    Write-Output ('REFUSED: pid file (' + $pidFilePid + ') and marker pid (' + $marker.pid + ') disagree. Not signalled.')
    exit 2
}

$info = Get-JccProcessInfo -ProcessId $pidFilePid
if (-not $info.Alive) {
    Write-Output ''
    Write-Output ('NOT RUNNING: recorded pid ' + $pidFilePid + ' is not alive. Cleaning stale bookkeeping.')
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $MarkerFile -Force -ErrorAction SilentlyContinue
    exit 0
}
if ([string]$info.CommandLine -notlike '*serve_visual.py*') {
    Write-Output ''
    Write-Output ('REFUSED: pid ' + $pidFilePid + ' is alive but its command line is not the Visual UI server:')
    Write-Output ('         ' + $info.CommandLine)
    Write-Output '         Not signalled. Resolve manually.'
    exit 2
}

$owner = Get-JccPortOwner -Port $Port
if ($owner.Pid -ne 0 -and $owner.Pid -ne $pidFilePid) {
    Write-Output ''
    Write-Output ('REFUSED: port ' + $Port + ' is LISTENING, owned by FOREIGN pid ' + $owner.Pid + ' (' + $owner.Method + ').')
    Write-Output ('         Our recorded pid ' + $pidFilePid + ' is not the port owner. Not signalled.')
    exit 3
}

Write-Output ''
Write-Output 'verified target (all checks passed):'
Write-Output ('  pid             : ' + $pidFilePid)
Write-Output ('  command line    : ' + $info.CommandLine)
Write-Output ('  port owner pid  : ' + $owner.Pid + ' (' + $owner.Method + ')')

if ($DryRun) {
    Write-Output ''
    Write-Output ('DRY RUN: would stop pid ' + $pidFilePid + '. Nothing was signalled.')
    exit 0
}

if (-not $PSCmdlet.ShouldProcess(('pid ' + $pidFilePid), 'stop the verified JCC v1.2 Visual UI process')) {
    Write-Output ''
    Write-Output 'stop aborted by -WhatIf / user confirmation. Nothing was signalled.'
    exit 0
}

Write-Output ''
Write-Output ('stopping v1.2 Visual UI pid ' + $pidFilePid + ' ...')
try {
    Stop-Process -Id $pidFilePid -Force -ErrorAction Stop
} catch {
    Write-Output ('FAILED: Stop-Process on ' + $pidFilePid + ' threw: ' + $_.Exception.Message)
    exit 4
}

$deadline = (Get-Date).AddSeconds($GracefulSeconds)
$exited = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
    if (-not (Test-JccProcessAlive -ProcessId $pidFilePid)) { $exited = $true; break }
}
if (-not $exited) {
    Write-Output ('FAILED: pid ' + $pidFilePid + ' still alive after ' + $GracefulSeconds + 's. Re-run.')
    exit 4
}
Write-Output ('v1.2 Visual UI pid ' + $pidFilePid + ' exited.')

$ownerAfter = Get-JccPortOwner -Port $Port
if ($ownerAfter.Pid -ne 0 -and $ownerAfter.Pid -ne $pidFilePid) {
    Write-Output ('NOTE: port ' + $Port + ' now owned by a different pid (' + $ownerAfter.Pid + '); PID/marker left for inspection.')
    exit 0
}
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $MarkerFile -Force -ErrorAction SilentlyContinue
Write-Output ('cleaned up ' + $PidFile + ' and ' + $MarkerFile)

Write-Output ''
Write-Output 'STOPPED (v1.2 Visual UI)'
Write-Output 'next      : .\scripts\start-jcc-v1.2-visual-ui.ps1   |   .\scripts\status-jcc-v1.2.ps1'
exit 0

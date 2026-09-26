# Report on the JCC continuation watchdog and, more usefully, on the controller
# itself: whether the pipeline is complete, which step it is on, and whether the
# last run left a repair request behind.

$ErrorActionPreference = 'SilentlyContinue'

$ProjectRoot = 'F:\jev'
$StatePath = Join-Path $ProjectRoot '.workbuddy\jcc_execution_state.json'
$RepairPath = Join-Path $ProjectRoot '.workbuddy\repair_required.json'
$ControllerLock = Join-Path $ProjectRoot '.workbuddy\locks\jcc-continuation.lock.json'
$RuntimeLock = Join-Path $ProjectRoot '.workbuddy\locks\runtime-4778.lock.json'

function Read-Json([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json) }
    catch { return $null }
}

function Test-PidAlive([int]$Pid) {
    if ($Pid -le 0) { return $false }
    try { return ((Get-Process -Id $Pid -ErrorAction Stop | Measure-Object).Count -gt 0) }
    catch { return $false }
}

$task = Get-ScheduledTask -TaskName 'JEV-JCC-Continuation-Watchdog' -ErrorAction SilentlyContinue
Write-Output "Scheduled task : $(if ($task) { 'registered' } else { 'NOT registered' })"
if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName 'JEV-JCC-Continuation-Watchdog'
    Write-Output ("  last run      : {0}  result {1}" -f $info.LastRunTime, $info.LastTaskResult)
}

$state = Read-Json $StatePath
if (-not $state) {
    Write-Output 'Controller state: no state file yet'
    exit 0
}

Write-Output ('Controller step : ' + $state.current_step)
Write-Output ('Project complete: ' + $state.project_complete)
Write-Output ('Steps completed : ' + ($state.steps_completed -join ', '))
Write-Output ('Head / freeze   : ' + $state.head_sha + '  /  ' + $state.scripts_freeze_sha)
if ($state.last_transition) {
    Write-Output ('Last transition: {0} {1} -> {2}'
        -f $state.last_transition.step, $state.last_transition.event,
        $state.last_transition.next)
}
if ($state.last_error) {
    Write-Output ('Last error      : [{0}] {1}' -f $state.last_error.kind, $state.last_error.message)
}

$lock = Read-Json $ControllerLock
if ($lock -and (Test-PidAlive ([int]$lock.pid))) {
    Write-Output ('Controller      : running (pid {0})' -f $lock.pid)
} elseif ($lock) {
    Write-Output ('Controller      : no live instance; stale lock for pid {0}' -f $lock.pid)
} else {
    Write-Output 'Controller      : not running'
}

$rl = Read-Json $RuntimeLock
if ($rl) {
    Write-Output ('Runtime lock    : pid {0} (alive={1}) expires {2}'
        -f $rl.pid, (Test-PidAlive ([int]$rl.pid)), $rl.expires_at)
} else {
    Write-Output 'Runtime lock    : not held'
}

if (Test-Path -LiteralPath $RepairPath) {
    Write-Output ''
    Write-Output 'REPAIR REQUIRED:'
    Get-Content -LiteralPath $RepairPath -Raw
}

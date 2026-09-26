# Report the JCC continuation watchdog's real registration and liveness state.
#
# Reads the task definition back from the Task Scheduler service and the
# controller lock back from disk -- never from the install record alone. The
# record says what was INTENDED; the scheduler and the lock say what IS.

[CmdletBinding()]
param(
    [switch]$Json
)

$ErrorActionPreference = 'Continue'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'lib\JccWatchdogCommon.ps1')

$Root     = Get-JccRoot
$TaskName = 'JEV-JCC-Continuation-Watchdog'
$Record   = Join-Path $Root '.workbuddy\scheduled\continuation-watchdog-install.json'
$StatePath= Join-Path $Root '.workbuddy\jcc_execution_state.json'
$LockPath = Join-Path $Root '.workbuddy\locks\jcc-continuation.lock.json'
$AuditLog = Join-Path $Root '.workbuddy\scheduled\logs\continuation-watchdog.jsonl'
$HoldPath = Join-Path $Root '.workbuddy\operator_hold.json'

$task   = Get-JccTaskReport -TaskName $TaskName
$record = Read-JccJson -Path $Record
$state  = Read-JccJson -Path $StatePath
$lock   = Read-JccJson -Path $LockPath
$hold   = Read-JccJson -Path $HoldPath

$lockAlive = $false
$lockPid = Get-JccPropInt -Object $lock -Name 'pid'
if ($lockPid -gt 0) {
    $lockAlive = Test-JccPidAlive -ProcessId $lockPid
}

# The hold is reported with the SAME expiry rule the watchdog applies, so this
# command cannot say "held" about a hold the watchdog would ignore.
$holdInForce = $false
$holdUntil = Get-JccProp -Object $hold -Name 'expires_at'
if ((Get-JccProp -Object $hold -Name 'active') -eq $true -and $holdUntil) {
    try { $holdInForce = ([datetime]::Parse([string]$holdUntil) -gt (Get-Date)) } catch { $holdInForce = $false }
}

$ticks = 0
$lastTick = $null
if (Test-Path -LiteralPath $AuditLog) {
    $lines = @(Get-Content -LiteralPath $AuditLog -ErrorAction SilentlyContinue)
    $ticks = $lines.Count
    if ($ticks -gt 0) { $lastTick = $lines[$ticks - 1] }
}

$payload = [pscustomobject]@{
    task_name            = $TaskName
    task_exists          = $task.exists
    task_enabled         = $task.enabled
    task_state           = $task.state
    task_identity        = $task.identity
    task_last_result     = $task.last_result
    task_last_run        = $task.last_run
    task_next_run        = $task.next_run
    installed_principal  = $(if ($record) { $record.principal } else { $null })
    installed_at         = $(if ($record) { $record.installed_at } else { $null })
    controller_pid       = $lockPid
    controller_alive     = $lockAlive
    controller_expires   = $(if ($lock) { $lock.expires_at } else { $null })
    current_step         = $(if ($state) { $state.current_step } else { $null })
    last_completed_step  = $(if ($state) { $state.last_completed_step } else { $null })
    runtime_state        = $(if ($state) { $state.runtime_state } else { $null })
    project_run          = $(if ($state) { $state.project_run } else { $null })
    attempt              = $(if ($state) { $state.attempt } else { $null })
    last_error           = $(if ($state) { $state.last_error } else { $null })
    watchdog_tick_count  = $ticks
    watchdog_last_tick   = $lastTick
    operator_hold        = $holdInForce
    operator_hold_until  = $(if ($holdUntil) { [string]$holdUntil } else { $null })
    operator_hold_reason = $(if ($holdInForce) { Get-JccProp -Object $hold -Name 'reason' } else { $null })
}

if ($Json) {
    $payload | ConvertTo-Json -Depth 8
    exit 0
}

Write-Output "== continuation watchdog =="
Write-Output ("task              : {0}" -f $payload.task_name)
Write-Output ("registered        : {0}  enabled={1}  state={2}" -f $payload.task_exists, $payload.task_enabled, $payload.task_state)
Write-Output ("task identity     : {0}" -f $payload.task_identity)
Write-Output ("install principal : {0}  (at {1})" -f $payload.installed_principal, $payload.installed_at)
Write-Output ("last run          : {0}  result={1}" -f $payload.task_last_run, $payload.task_last_result)
Write-Output ("next run          : {0}" -f $payload.task_next_run)
Write-Output ""
Write-Output "== controller =="
Write-Output ("pid               : {0}  alive={1}" -f $payload.controller_pid, $payload.controller_alive)
Write-Output ("lock expires      : {0}" -f $payload.controller_expires)
Write-Output ("current step      : {0}" -f $payload.current_step)
Write-Output ("last completed    : {0}" -f $payload.last_completed_step)
Write-Output ("runtime state     : {0}" -f $payload.runtime_state)
Write-Output ("project_run       : {0}" -f $payload.project_run)
Write-Output ("attempt           : {0}" -f $payload.attempt)
Write-Output ("last error        : {0}" -f ($payload.last_error | ConvertTo-Json -Compress))
Write-Output ""
Write-Output ("operator hold     : {0}{1}" -f $payload.operator_hold,
    $(if ($payload.operator_hold) { ("  (until {0}: {1})" -f $payload.operator_hold_until, $payload.operator_hold_reason) } else { "" }))
Write-Output ""
Write-Output ("watchdog ticks    : {0}" -f $payload.watchdog_tick_count)
Write-Output ("last tick         : {0}" -f $payload.watchdog_last_tick)

if (-not $payload.task_exists) { exit 2 }
if ($payload.controller_alive) { exit 0 }
if ($payload.runtime_state -eq 'COMPLETE' -and $payload.project_run -eq 'PASS') { exit 0 }
exit 3

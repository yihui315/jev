# Report the JCC service watchdog's real registration and recent behaviour.
#
# Reads the task back from the Task Scheduler service, the restart budget back
# from disk and the last ticks back from the audit log -- the registration record
# says what was intended, not what is.

[CmdletBinding()]
param(
    [switch]$Json
)

$ErrorActionPreference = 'Continue'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'lib\JccWatchdogCommon.ps1')

$Root      = Get-JccRoot
$TaskName  = 'JEV-JCC-Service-Watchdog'
$Record    = Join-Path $Root '.workbuddy\scheduled\service-watchdog-install.json'
$AuditLog  = Join-Path $Root '.workbuddy\scheduled\logs\service-watchdog.jsonl'
$Budget    = Join-Path $Root '.workbuddy\scheduled\service-watchdog-state.json'
$Release   = Join-Path $Root '.workbuddy\release_state.json'
$Maint     = Join-Path $Root '.workbuddy\maintenance.json'
$Ownership = Join-Path $Root 'runtime\jcc\jcc.ownership.json'

$task    = Get-JccTaskReport -TaskName $TaskName
$record  = Read-JccJson -Path $Record
$budget  = Read-JccJson -Path $Budget
$release = Read-JccJson -Path $Release
$maint   = Read-JccJson -Path $Maint
$own     = Read-JccJson -Path $Ownership

$ticks = 0
$lastTick = $null
if (Test-Path -LiteralPath $AuditLog) {
    $lines = @(Get-Content -LiteralPath $AuditLog -ErrorAction SilentlyContinue)
    $ticks = $lines.Count
    if ($ticks -gt 0) { $lastTick = $lines[$ticks - 1] }
}

$healthStatus = 0
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:4778/api/health' -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    $healthStatus = $r.StatusCode
} catch { $healthStatus = 0 }

$listenerPid = 0
try {
    $c = Get-NetTCPConnection -LocalPort 4778 -State Listen -ErrorAction Stop | Select-Object -First 1
    if ($c) { $listenerPid = [int]$c.OwningProcess }
} catch { $listenerPid = 0 }

$payload = [pscustomobject]@{
    task_name           = $TaskName
    task_exists         = $task.exists
    task_enabled        = $task.enabled
    task_state          = $task.state
    task_identity       = $task.identity
    task_last_result    = $task.last_result
    task_last_run       = $task.last_run
    task_next_run       = $task.next_run
    installed_principal = $(if ($record) { $record.principal } else { $null })
    installed_at        = $(if ($record) { $record.installed_at } else { $null })
    release_state       = $(if ($release) { $release.state } else { $null })
    maintenance_active  = $(if ($maint) { $maint.active } else { $false })
    maintenance_reason  = $(if ($maint) { $maint.reason } else { $null })
    health_status       = $healthStatus
    listener_pid        = $listenerPid
    ownership_pid       = $(if ($own) { $own.service_pid } else { $null })
    ownership_service   = $(if ($own) { $own.service } else { $null })
    restarts_this_hour  = $(if ($budget) { $budget.restart_count } else { 0 })
    budget_window_start = $(if ($budget) { $budget.window_start } else { $null })
    watchdog_tick_count = $ticks
    watchdog_last_tick  = $lastTick
}

if ($Json) { $payload | ConvertTo-Json -Depth 8; exit 0 }

Write-Output "== service watchdog =="
Write-Output ("task              : {0}" -f $payload.task_name)
Write-Output ("registered        : {0}  enabled={1}  state={2}" -f $payload.task_exists, $payload.task_enabled, $payload.task_state)
Write-Output ("task identity     : {0}" -f $payload.task_identity)
Write-Output ("install principal : {0}  (at {1})" -f $payload.installed_principal, $payload.installed_at)
Write-Output ("last run          : {0}  result={1}" -f $payload.task_last_run, $payload.task_last_result)
Write-Output ("next run          : {0}" -f $payload.task_next_run)
Write-Output ""
Write-Output "== gating =="
Write-Output ("release state     : {0}   (monitoring only when READY)" -f $payload.release_state)
Write-Output ("maintenance       : {0}  {1}" -f $payload.maintenance_active, $payload.maintenance_reason)
Write-Output ""
Write-Output "== service =="
Write-Output ("health            : {0}" -f $payload.health_status)
Write-Output ("listener pid      : {0}" -f $payload.listener_pid)
Write-Output ("ownership marker  : service={0} pid={1}" -f $payload.ownership_service, $payload.ownership_pid)
Write-Output ("restarts this hour: {0}  (window from {1})" -f $payload.restarts_this_hour, $payload.budget_window_start)
Write-Output ""
Write-Output ("watchdog ticks    : {0}" -f $payload.watchdog_tick_count)
Write-Output ("last tick         : {0}" -f $payload.watchdog_last_tick)

if (-not $payload.task_exists) { exit 2 }
exit 0

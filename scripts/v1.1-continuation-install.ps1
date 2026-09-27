# Register JEV-JCC-v1.1-Continuation -- the v1.1 continuation watchdog.
#
# WHY THIS IS THE SAME SCRIPT AS JEV-JCC-Continuation-Watchdog, NOT A NEW ONE.
# scripts\continuation-watchdog.ps1 is the audited implementation of exactly what
# a continuation watchdog must do, and it already satisfies every requirement:
#
#   * liveness is pid IDENTITY + HEARTBEAT FRESHNESS (Test-JccLeaseLive), not a
#     fixed `expires_at` TTL. A fixed TTL is the defect that was measured here:
#     a healthy controller in a 41-minute step read as stale, the watchdog
#     started a SECOND controller, and the two generations then ran duplicate
#     soak drivers whose stop/start windows overlapped -- a fight that can never
#     converge. The heartbeat only ADDS a freshness signal and never removes the
#     old fallback, which is what makes this safe to install mid-flight.
#   * a long task ATTACHES the existing run: the controller is started with
#     `run --resume` and reads current_step / last_completed_step from
#     .workbuddy\jcc_execution_state.json, so a reboot resumes instead of
#     restarting.
#   * duplicate prevention is an atomic election lock (Enter-JccElectionLock)
#     PLUS a re-read of the controller lock UNDER the election, so neither the
#     same-instant race nor the merely-close one can produce two controllers.
#   * it refuses to capture the controller's stdout -- a pipe whose reader exits
#     breaks the child, and a pump that waits on it means the tick never returns
#     and the next tick is skipped. (The same inheritance rule that made the
#     GREEN watchdog's `>>` redirect self-locking.)
#
# A second implementation would be a second set of liveness bugs. The task NAME
# is what the v1.1 release needs; the MECHANISM is shared.
#
# IT IS INERT UNTIL v1.1 HAS ITS OWN STATE, AND THAT IS RECORDED RATHER THAN
# HIDDEN. Rule 1 of the shared script reads
# .workbuddy\jcc_execution_state.json and returns NOOP while it says
# runtime_state=COMPLETE with project_run=PASS -- measured: 947 consecutive
# NOOPs for exactly that reason. Resetting that file would re-run the whole
# FINAL pipeline (including a ~41-minute soak that stops and starts 4778), which
# is forbidden tonight: P0 is a stable GREEN on 4778 and nothing may break it for
# v1.1 completeness. So this task is installed and enabled as the v1.1
# continuation channel, and it activates by itself the moment a v1.1 run
# establishes a non-COMPLETE state.
#
# PRINCIPAL: SYSTEM when the host allows it, otherwise the current user at
# Highest -- and WHICH ONE WAS USED IS RECORDED, because the two are not
# equivalent (a per-user task does not exist until that user logs on).

[CmdletBinding()]
param(
    [int]$RepetitionMinutes = 2
)

$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'lib\JccWatchdogCommon.ps1')

$TaskName = 'JEV-JCC-v1.1-Continuation'
$Script   = Join-Path (Get-JccRoot) 'scripts\continuation-watchdog.ps1'
$Record   = Join-Path (Get-JccRoot) '.workbuddy\scheduled\v1.1-continuation-install.json'

if (-not (Test-Path -LiteralPath $Script -PathType Leaf)) {
    Write-Output "install: ERROR continuation watchdog script missing at $Script"
    exit 1
}

$result = New-JccScheduledTask -TaskName $TaskName -ScriptPath $Script `
    -ScriptArguments @() `
    -TriggerRepetition @('AtLogOn', 'AtStartup', 'Every') `
    -RepetitionMinutes $RepetitionMinutes -PreferSystem

if (-not $result.ok) {
    Write-Output "install: ERROR could not register $TaskName"
    exit 1
}

$report = Get-JccTaskReport -TaskName $TaskName

# Fail-closed: a task that redirects its output would leak an inheritable handle
# into the detached controller it launches, and lock its own log against every
# later tick. Assert it here, where a human is watching.
$action = ([string]$report.command + ' ' + [string]$report.arguments)
if ($action -match '>>' -or $action -match '2>&1') {
    Write-Output "install: ERROR $TaskName was registered WITH a redirection: $action"
    exit 1
}

Write-JccJson -Path $Record -Object ([pscustomobject]@{
    task_name        = $TaskName
    script           = $Script
    mechanism        = 'shared with JEV-JCC-Continuation-Watchdog (audited: lease identity + heartbeat, election lock, --resume attach)'
    triggers         = @('AtLogOn', 'AtStartup', ("Every {0} min" -f $RepetitionMinutes))
    principal        = $result.identity
    logon_type       = $result.logon_type
    run_level        = $result.run_level
    requested_system = $true
    state            = $report.state
    enabled          = $report.enabled
    verified         = $report.certain
    oracle           = $report.oracle
    verified_args    = $report.arguments
    verified_repetition = $report.repetition
    inert_until      = 'a v1.1 run establishes a non-COMPLETE .workbuddy\jcc_execution_state.json; while that file says runtime_state=COMPLETE + project_run=PASS the shared script Rule 1 returns NOOP'
    installed_at     = (Get-Date).ToString('o')
    installed_by     = ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
})

Write-Output ("install: {0} registered" -f $TaskName)
Write-Output ("  principal : {0} ({1}, {2})" -f $result.identity, $result.logon_type, $result.run_level)
Write-Output ("  verified  : state={0} enabled={1} repetition={2} oracle={3}" -f `
    $report.state, $report.enabled, $report.repetition, $report.oracle)
Write-Output ("  record    : {0}" -f $Record)
exit 0

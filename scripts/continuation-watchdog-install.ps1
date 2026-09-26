# Register the JCC continuation watchdog as a Windows Scheduled Task.
#
# TASK: JEV-JCC-Continuation-Watchdog
# TRIGGERS: AtLogOn, plus a repetition every 2 minutes.
# PRINCIPAL: SYSTEM when the host permits it, otherwise the current user at
#            RunLevel Highest. WHICH ONE WAS ACTUALLY USED IS RECORDED to
#            .workbuddy\scheduled\continuation-watchdog-install.json -- the two
#            are not equivalent (a per-user task does not exist until that user
#            logs on) and a claim of "runs as SYSTEM" that was never verified is
#            exactly the kind of provenance this project refuses.
#
# The repetition trigger is not decoration: AtLogOn alone would leave a dead
# controller dead for the whole session, which is the failure this watchdog
# exists to prevent.

[CmdletBinding()]
param(
    [int]$RepetitionMinutes = 2
)

$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'lib\JccWatchdogCommon.ps1')

$TaskName = 'JEV-JCC-Continuation-Watchdog'
$Script   = Join-Path (Get-JccRoot) 'scripts\continuation-watchdog.ps1'
$Record   = Join-Path (Get-JccRoot) '.workbuddy\scheduled\continuation-watchdog-install.json'

if (-not (Test-Path -LiteralPath $Script -PathType Leaf)) {
    Write-Output "install: ERROR watchdog script missing at $Script"
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
$obj = [pscustomobject]@{
    task_name        = $TaskName
    script           = $Script
    triggers         = @('AtLogOn', 'AtStartup', ("Every {0} min" -f $RepetitionMinutes))
    principal        = $result.identity
    logon_type       = $result.logon_type
    run_level        = $result.run_level
    requested_system = $true
    state            = $report.state
    enabled          = $report.enabled
    installed_at     = (Get-Date).ToString('o')
    installed_by     = ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
}
Write-JccJson -Path $Record -Object $obj

Write-Output ("install: {0} registered" -f $TaskName)
Write-Output ("  principal : {0} ({1}, {2})" -f $result.identity, $result.logon_type, $result.run_level)
Write-Output ("  state     : {0} enabled={1}" -f $report.state, $report.enabled)
Write-Output ("  record    : {0}" -f $Record)
exit 0

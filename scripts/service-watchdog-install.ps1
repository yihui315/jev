# Register the JCC service watchdog as a Windows Scheduled Task.
#
# TASK: JEV-JCC-Service-Watchdog
# TRIGGERS: AtStartup, AtLogOn, plus a repetition every 2 minutes.
# PRINCIPAL: SYSTEM when possible, else the current user at RunLevel Highest;
#            the identity actually used is recorded, not assumed.
#
# AtStartup matters here more than for the continuation watchdog: the host has a
# documented history of bugchecks and hard hangs, and after one of those the
# service must come back without anyone logging in.

[CmdletBinding()]
param(
    [int]$RepetitionMinutes = 2
)

$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'lib\JccWatchdogCommon.ps1')

$TaskName = 'JEV-JCC-Service-Watchdog'
$Script   = Join-Path (Get-JccRoot) 'scripts\service-watchdog.ps1'
$Record   = Join-Path (Get-JccRoot) '.workbuddy\scheduled\service-watchdog-install.json'

if (-not (Test-Path -LiteralPath $Script -PathType Leaf)) {
    Write-Output "install: ERROR watchdog script missing at $Script"
    exit 1
}

$result = New-JccScheduledTask -TaskName $TaskName -ScriptPath $Script `
    -ScriptArguments @() `
    -TriggerRepetition @('AtStartup', 'AtLogOn', 'Every') `
    -RepetitionMinutes $RepetitionMinutes -PreferSystem

if (-not $result.ok) {
    Write-Output "install: ERROR could not register $TaskName"
    exit 1
}

$report = Get-JccTaskReport -TaskName $TaskName
Write-JccJson -Path $Record -Object ([pscustomobject]@{
    task_name        = $TaskName
    script           = $Script
    triggers         = @('AtStartup', 'AtLogOn', ("Every {0} min" -f $RepetitionMinutes))
    principal        = $result.identity
    logon_type       = $result.logon_type
    run_level        = $result.run_level
    requested_system = $true
    state            = $report.state
    enabled          = $report.enabled
    installed_at     = (Get-Date).ToString('o')
    installed_by     = ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
})

Write-Output ("install: {0} registered" -f $TaskName)
Write-Output ("  principal : {0} ({1}, {2})" -f $result.identity, $result.logon_type, $result.run_level)
Write-Output ("  state     : {0} enabled={1}" -f $report.state, $report.enabled)
Write-Output ("  record    : {0}" -f $Record)
exit 0

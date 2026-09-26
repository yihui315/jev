# Remove the JCC continuation watchdog scheduled task.
#
# Deliberately does NOT kill a running controller. Unregistering the watchdog
# stops future ticks; a controller already running is a separate, live process
# that someone may still be relying on, and killing it here would make
# "uninstall the watchdog" silently mean "abandon the pipeline mid-step".

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'lib\JccWatchdogCommon.ps1')

$TaskName = 'JEV-JCC-Continuation-Watchdog'
$Record   = Join-Path (Get-JccRoot) '.workbuddy\scheduled\continuation-watchdog-install.json'

Import-Module ScheduledTasks -ErrorAction Stop

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Output ("uninstall: {0} is not registered; nothing to do" -f $TaskName)
} else {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Output ("uninstall: {0} unregistered" -f $TaskName)
}
if (Test-Path -LiteralPath $Record) {
    Remove-Item -LiteralPath $Record -Force -ErrorAction SilentlyContinue
    Write-Output ("uninstall: removed install record {0}" -f $Record)
}

$lockPath = Join-Path (Get-JccRoot) '.workbuddy\locks\jcc-continuation.lock.json'
$lock = Read-JccJson -Path $lockPath
# Get-JccPropInt, not $lock.pid: under StrictMode 2.0 a missing property is a
# terminating error, and $ErrorActionPreference is Stop at the top of this file,
# so a lock written by an older schema would abort the uninstall halfway --
# leaving the task unregistered but the record and the note unprinted.
$lockPid = Get-JccPropInt -Object $lock -Name 'pid'
if ($lockPid -gt 0 -and (Test-JccPidAlive -ProcessId $lockPid)) {
    Write-Output ("uninstall: NOTE a controller is still live (pid {0}) and was left running" -f $lockPid)
}
exit 0

# Remove the JCC service watchdog scheduled task.
#
# Like its continuation counterpart, this leaves the service itself alone.
# Unregistering a watchdog means "stop restarting it", not "stop it".

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'lib\JccWatchdogCommon.ps1')

$TaskName = 'JEV-JCC-Service-Watchdog'
$Record   = Join-Path (Get-JccRoot) '.workbuddy\scheduled\service-watchdog-install.json'

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
exit 0

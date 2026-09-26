# Install the JCC continuation watchdog as a Windows scheduled task.
#
#   JEV-JCC-Continuation-Watchdog
#   triggers: every 2 minutes, and AtStartup
#   action:   powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass
#                   -File tools\continuation-watchdog.ps1
#
# SAFETY: before creating or replacing anything, an existing task with this name
# is EXPORTED to .workbuddy\scheduled\ so it can be restored. A task with this
# name that this script did not create is exported FIRST and only replaced after
# the operator has the export in hand -- an unknown task is never overwritten.

$ErrorActionPreference = 'Stop'

$TaskName = 'JEV-JCC-Continuation-Watchdog'
$ProjectRoot = 'F:\jev'
$ExportDir = Join-Path $ProjectRoot '.workbuddy\scheduled'
$ExportPath = Join-Path $ExportDir "$TaskName.existing.xml"

$Action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument (@(
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $ProjectRoot 'tools\continuation-watchdog.ps1'))
    ) -Join ' '

$Triggers = @(
    New-ScheduledTaskTrigger -AtStartup,
    (New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 2) -Once)
)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($existing) {
    # Never silently replace. Export first, then tell the operator it is there.
    if (-not (Test-Path -LiteralPath $ExportDir)) {
        New-Item -ItemType Directory -Path $ExportDir -Force | Out-Null
    }
    $exported = $false
    try {
        $existing | Export-ScheduledTask -Path $ExportPath
        $exported = $true
    }
    catch {
        Write-Output "WARNING: could not export the existing task: $($_.Exception.Message)"
    }
    Write-Output "Existing task '$TaskName' found."
    if ($exported) {
        Write-Output "Exported to $ExportPath -- restore it with:"
        Write-Output "  schtasks /delete /tn '$TaskName' /f"
        Write-Output "  schtasks /tn '$TaskName' /ru SYSTEM /xml '$ExportPath' /f"
    }
    Write-Output 'Replacing it now with the watchdog definition.'
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName `
    -Action $Action -Trigger $Triggers -Settings $Settings `
    -Description 'Starts the JCC continuation controller if it is not already running.' `
    -RunLevel Highest -User SYSTEM | Out-Null

Write-Output "Installed '$TaskName'."
Write-Output 'Verify with: powershell -File tools\continuation-watchdog-status.ps1'

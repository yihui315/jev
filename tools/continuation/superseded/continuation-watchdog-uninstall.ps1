# Remove the JCC continuation watchdog scheduled task.
#
# A copy of the task is exported first if one exists, so removal is reversible.

$ErrorActionPreference = 'Stop'

$TaskName = 'JEV-JCC-Continuation-Watchdog'
$ProjectRoot = 'F:\jev'
$ExportDir = Join-Path $ProjectRoot '.workbuddy\scheduled'
$ExportPath = Join-Path $ExportDir "$TaskName.removed.xml"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Output "Task '$TaskName' is not registered; nothing to remove."
    exit 0
}

if (-not (Test-Path -LiteralPath $ExportDir)) {
    New-Item -ItemType Directory -Path $ExportDir -Force | Out-Null
}
try {
    $existing | Export-ScheduledTask -Path $ExportPath
    Write-Output "Exported the task to $ExportPath before removing it."
}
catch {
    Write-Output "WARNING: could not export before removing: $($_.Exception.Message)"
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Output "Removed '$TaskName'."

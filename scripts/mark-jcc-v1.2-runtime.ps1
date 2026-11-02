<#
    JCC v1.2 VISUAL COMMAND CENTER -- write ownership markers for a LIVE runtime.

    When the v1.2 services are launched as DURABLE detached processes (e.g. via
    the agent background runner or a Windows scheduled task), the short-lived
    start-*.ps1 launchers exit before the child service, so they cannot write the
    marker. This script writes the pid file + ownership marker for the process
    that is CURRENTLY holding each port, after positively identifying it (command
    line must match the expected service start line). Fail-closed: if the port
    holder is not provably ours, nothing is written and the script exits non-zero.

    Idempotent: safe to re-run; it only ever writes the live port owner's pid.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccV12Paths -Root $Root
$python = Get-JccPython -Root $Root

function Write-ApiMarker {
    $port = (Get-JccV12Port -Root $Root -Python $python)
    $owner = Get-JccPortOwner -Port $port
    if ($owner.Pid -le 0) { Write-Output ('API: no listener on ' + $port); return $false }
    $info = Get-JccProcessInfo -ProcessId $owner.Pid
    if ([string]$info.CommandLine -notlike '*-m jcc_core.run*') {
        Write-Output ('API: port ' + $port + ' holder is not our service: ' + $info.CommandLine); return $false
    }
    Set-Content -LiteralPath $Paths.PidFile -Value ([string]$owner.Pid) -Encoding ASCII
    Write-JccOwnershipMarker -Paths $Paths -Port $port -PythonExe $python -Root $Root `
        -LauncherPid 0 -ServicePid $owner.Pid `
        -Config $Paths.Config -CreatedBy 'scripts/mark-jcc-v1.2-runtime.ps1' | Out-Null
    Write-Output ('API: marker written for service pid ' + $owner.Pid + ' on port ' + $port)
    return $true
}

function Write-UiMarker {
    $port = 4780
    $PidFile = Join-Path $Paths.Runtime 'visual-ui.pid'
    $MarkerFile = Join-Path $Paths.Runtime 'visual-ui.ownership.json'
    $owner = Get-JccPortOwner -Port $port
    if ($owner.Pid -le 0) { Write-Output ('UI: no listener on ' + $port); return $false }
    $info = Get-JccProcessInfo -ProcessId $owner.Pid
    if ([string]$info.CommandLine -notlike '*serve_visual.py*') {
        Write-Output ('UI: port ' + $port + ' holder is not our Visual UI: ' + $info.CommandLine); return $false
    }
    Set-Content -LiteralPath $PidFile -Value ([string]$owner.Pid) -Encoding ASCII
    $marker = [ordered]@{
        service      = 'jcc-v1.2-visual-ui'
        pid          = $owner.Pid
        service_pid  = $owner.Pid
        launcher_pid = 0
        start_time   = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        start_time_local = (Get-Date).ToString('o')
        command_line = [string]$info.CommandLine
        port         = $port
        host         = '127.0.0.1'
        created_by   = 'scripts/mark-jcc-v1.2-runtime.ps1'
        platform     = 'windows'
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($MarkerFile, ($marker | ConvertTo-Json -Depth 6), $utf8NoBom)
    Write-Output ('UI: marker written for service pid ' + $owner.Pid + ' on port ' + $port)
    return $true
}

$apiOk = Write-ApiMarker
$uiOk  = Write-UiMarker
if ($apiOk -and $uiOk) { exit 0 } else { exit 1 }

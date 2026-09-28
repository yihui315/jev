<#
    JCC v1.2 VISUAL COMMAND CENTER -- Visual UI start (W-02b-v12).

    Launches the production Visual UI static server (apps\command_center_3d\
    serve_visual.py) on 127.0.0.1:4780. This is the ONLY supported way to serve
    the Visual UI in production (never the Vite dev server). It writes its own
    pid + ownership marker so stop-jcc-v1.2-visual-ui.ps1 can fail-closed verify
    and signal it. The v1.2 API (4779) is a SEPARATE process; this script never
    touches it or GREEN (4778).

    Exit codes: 0 = up, 3 = refused (foreign/phantom), 4 = health timeout.
#>

[CmdletBinding()]
param(
    [int]$Port = 4780,
    [int]$HealthTimeoutSec = 60
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccV12Paths -Root $Root

$UiDir = Join-Path (Join-Path $Root 'apps') 'command_center_3d'
$ServeScript = Join-Path $UiDir 'serve_visual.py'
$PidFile = Join-Path $Paths.Runtime 'visual-ui.pid'
$MarkerFile = Join-Path $Paths.Runtime 'visual-ui.ownership.json'

Write-Output 'JCC v1.2 Visual UI start (W-02b-v12)'
Write-Output ('repo root : ' + $Root)
if (-not (Test-Path -LiteralPath $ServeScript)) {
    Write-Output ('REFUSED: ' + $ServeScript + ' missing (build it: cd apps\command_center_3d; npm ci; npm run build).')
    exit 3
}

$python = Get-JccPython -Root $Root
if (-not $python) { Write-Output 'REFUSED: no Python interpreter.'; exit 3 }
if (-not (Test-JccPython -Python $python)) { Write-Output ('REFUSED: ' + $python + ' does not run.'); exit 3 }

$healthUrl = 'http://127.0.0.1:' + $Port + '/health'
Write-Output ('port      : ' + $Port)
Write-Output ('serve     : ' + $ServeScript)

New-JccEnsureDir -Path $Paths.Runtime | Out-Null

# already up?
$health = Invoke-JccHealth -Url $healthUrl -TimeoutMs 3000
if ($health.Ok) {
    $owner = Get-JccPortOwner -Port $Port
    if ($owner.Pid -ne 0) {
        $info = Get-JccProcessInfo -ProcessId $owner.Pid
        if ([string]$info.CommandLine -like '*serve_visual.py*') {
            Write-Output ('ALREADY RUNNING (verified) -- pid ' + $owner.Pid)
            exit 0
        }
    }
    Write-Output ('REFUSED: ' + $healthUrl + ' answers but is not our Visual UI server. Leave it alone.')
    exit 3
}

$owner = Get-JccPortOwner -Port $Port
if ($owner.Pid -ne 0) {
    $info = Get-JccProcessInfo -ProcessId $owner.Pid
    Write-Output ('REFUSED: port ' + $Port + ' held by foreign pid ' + $owner.Pid + ': ' + $info.CommandLine)
    exit 3
}

$stalePid = 0
if (Test-Path -LiteralPath $PidFile) {
    $v = (Get-Content -LiteralPath $PidFile -Raw -ErrorAction SilentlyContinue).Trim()
    if ($v -match '^\d+$') { $stalePid = [int]$v }
}
if ($stalePid -gt 0 -and (Test-JccProcessAlive -ProcessId $stalePid)) {
    Write-Output ('REFUSED: stale pid file names live pid ' + $stalePid + '; resolve manually.')
    exit 3
} elseif ($stalePid -gt 0) {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

$stdoutLog = Join-Path $Paths.Runtime 'visual-ui.log'
$stderrLog = Join-Path $Paths.Runtime 'visual-ui.err.log'

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = (Join-Path $env:SystemRoot 'system32\cmd.exe')
$psi.Arguments = '/c "' + $python + ' "' + $ServeScript + '" --host 127.0.0.1 --port ' + $Port + ' > "' + $stdoutLog + '" 2> "' + $stderrLog + '"'
$psi.WorkingDirectory = $Root
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
[void]$proc.Start()
$launcherPid = $proc.Id
Write-Output ('launcher pid: ' + $launcherPid)

$deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $h = Invoke-JccHealth -Url $healthUrl -TimeoutMs 2000
    if ($h.Ok) { $healthy = $true; break }
    if (-not (Test-JccProcessAlive -ProcessId $launcherPid)) {
        Write-Output ('process exited before healthy. stderr tail:')
        foreach ($l in (Get-JccTail -Lines (Get-Content -LiteralPath $stderrLog -ErrorAction SilentlyContinue) -Count 12)) { Write-Output ('  ' + $l) }
        exit 4
    }
}
if (-not $healthy) {
    Write-Output ('FAILED: /health did not answer within ' + $HealthTimeoutSec + 's.')
    exit 4
}

$ownerNow = Get-JccPortOwner -Port $Port
if ($ownerNow.Pid -le 0) { Write-Output 'REFUSED: came up but no listener found.'; exit 3 }
$sInfo = Get-JccProcessInfo -ProcessId $ownerNow.Pid
if ([string]$sInfo.CommandLine -notlike '*serve_visual.py*') {
    Write-Output ('REFUSED: port holder is not our Visual UI server: ' + $sInfo.CommandLine)
    exit 3
}
$servicePid = $ownerNow.Pid

Set-Content -LiteralPath $PidFile -Value ([string]$servicePid) -Encoding ASCII
$marker = [ordered]@{
    service     = 'jcc-v1.2-visual-ui'
    pid         = $servicePid
    service_pid = $servicePid
    launcher_pid= $launcherPid
    start_time  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    start_time_local = (Get-Date).ToString('o')
    command_line= ([string]$sInfo.CommandLine)
    port        = $Port
    host        = '127.0.0.1'
    created_by  = 'scripts/start-jcc-v1.2-visual-ui.ps1'
    platform    = 'windows'
}
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($MarkerFile, ($marker | ConvertTo-Json -Depth 6), $utf8NoBom)

Write-Output ('')
Write-Output ('UP (v1.2 Visual UI, isolated from GREEN 4778)')
Write-Output ('service pid : ' + $servicePid + '   (holds 127.0.0.1:' + $Port + ')')
Write-Output ('launcher pid: ' + $launcherPid)
Write-Output ('health url : ' + $healthUrl)
Write-Output ('pid file   : ' + $PidFile)
Write-Output ('marker     : ' + $MarkerFile)
exit 0

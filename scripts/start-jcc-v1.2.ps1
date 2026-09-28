<#
    JCC v1.2 VISUAL COMMAND CENTER -- service start (W-02-v12).

    Starts a SECOND, fully isolated JCC Core instance that serves the v1.2
    Visual Command Center (apps/command_center_3d). It binds 127.0.0.1:4779
    (config\jcc-v1.2.yaml -> server.port) and uses its OWN runtime dir and
    SQLite database (runtime\jcc-v1.2), so the frozen GREEN service on
    127.0.0.1:4778 is NEVER touched -- not its port, not its pid/marker, not
    its database, not its process.

    This script reuses the SAME ownership contract as scripts\start.ps1 via
    lib\JccCommon.ps1 (Test-JccOwnership / Write-JccOwnershipMarker /
    Get-JccPortOwner / Invoke-JccHealth), pointed at the v1.2 paths. The
    fail-closed rule is identical: a process this script did not start and
    cannot positively identify is never signalled.

    Exit codes: 0 = up and healthy, 3 = refused (foreign port hold / phantom
    marker / missing prerequisite), 4 = started but health timeout.
#>

[CmdletBinding()]
param(
    [int]$Port = 0,
    [int]$HealthTimeoutSec = 90
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccV12Paths -Root $Root

Write-Output 'JCC v1.2 start (W-02-v12)'
Write-Output ('repo root : ' + $Root)
Write-Output ('timestamp : ' + (Get-Date).ToString('o'))

# --- prerequisite gate -------------------------------------------------- #
$python = Get-JccPython -Root $Root
if (-not $python) {
    Write-Output ('')
    Write-Output ('REFUSED: no Python interpreter (.venv missing and nothing usable on PATH).')
    Write-Output ('         Run  .\scripts\bootstrap.ps1  first.')
    exit 3
}
if (-not (Test-JccPython -Python $python)) {
    Write-Output ('')
    Write-Output ('REFUSED: interpreter ' + $python + ' does not run.')
    exit 3
}
if (-not (Test-Path -LiteralPath $Paths.Config)) {
    Write-Output ('')
    Write-Output ('REFUSED: config ' + $Paths.Config + ' is missing.')
    exit 3
}

$resolvedPort = $Port
if ($resolvedPort -le 0) {
    $resolvedPort = Get-JccV12Port -Root $Root -Python $python
}
$baseUrl = 'http://127.0.0.1:' + $resolvedPort
$healthUrl = $baseUrl + '/api/health'
Write-Output ('config    : ' + $Paths.Config)
Write-Output ('port      : ' + $resolvedPort + '  (url ' + $healthUrl + ')')

New-JccEnsureDir -Path $Paths.Runtime | Out-Null

# --- already up? -------------------------------------------------------- #
$health = Invoke-JccHealth -Url $healthUrl -TimeoutMs 3000
if ($health.Ok) {
    $pidFile = Get-JccPidFromFile -Paths $Paths
    $own = Test-JccOwnership -Paths $Paths -Port $resolvedPort -Root $Root
    if ($own.Ok) {
        $h = Read-JccHealthJson -Body $health.Body
        $hv = ''
        if ($h -and $h.status) { $hv = [string]$h.status }
        Write-Output ('')
        Write-Output ('ALREADY RUNNING -- verified, not restarted.')
        Write-Output ('pid     : ' + $own.Pid)
        Write-Output ('health  : ' + $hv + ' (' + $health.StatusCode + ')')
        Write-Output ('url     : ' + $healthUrl)
        exit 0
    }
    Write-Output ('')
    Write-Output ('REFUSED: HTTP ' + $health.StatusCode + ' on ' + $healthUrl + ' but no usable ownership marker.')
    Write-Output ('         ' + $own.Reason)
    Write-Output ('         The listener is NOT ours; leave it alone. If you started it with')
    Write-Output ('         scripts\start-jcc-v1.2.ps1 in this repo and the marker was lost, remove')
    Write-Output ('         runtime\jcc-v1.2\jcc-v1.2.pid and runtime\jcc-v1.2\jcc-v1.2.ownership.json by hand only')
    Write-Output ('         after confirming with  netstat -ano | findstr :' + $resolvedPort + '  which pid holds it.')
    exit 3
}

# --- foreign hold? (on the v1.2 port only) ------------------------------ #
$owner = Get-JccPortOwner -Port $resolvedPort
if ($owner.Pid -ne 0) {
    $info = Get-JccProcessInfo -ProcessId $owner.Pid
    Write-Output ('')
    Write-Output ('REFUSED: port ' + $resolvedPort + ' is already LISTENING, owned by pid ' + $owner.Pid + ' via ' + $owner.Method + '.')
    Write-Output ('         command line: ' + $info.CommandLine)
    Write-Output ('         This is not the process this script started, so it is never signalled.')
    Write-Output ('         Nothing was started. Resolve the holder yourself, then re-run.')
    exit 3
}

# --- clean up files that can only be stale (never a live process) ------- #
$stalePid = Get-JccPidFromFile -Paths $Paths
if ($stalePid -gt 0) {
    $alive = Test-JccProcessAlive -ProcessId $stalePid
    if ($alive) {
        $info = Get-JccProcessInfo -ProcessId $stalePid
        Write-Output ('')
        Write-Output ('REFUSED: runtime\jcc-v1.2\jcc-v1.2.pid names pid ' + $stalePid + ', which is still alive:')
        Write-Output ('         ' + $info.CommandLine)
        Write-Output ('         That process was not positively verified as ours, so it is not signalled.')
        exit 3
    }
    Write-Output ('stale pid file found for pid ' + $stalePid + ' (dead) -- overwriting')
    Remove-Item -LiteralPath $Paths.PidFile -Force -ErrorAction SilentlyContinue
}

# --- launch ------------------------------------------------------------- #
$stdoutLog = $Paths.ServerLog
$stderrLog = $Paths.ServerErrLog

$packagesPath = Join-Path $Root 'packages'
$curPyPath = [string]$env:PYTHONPATH
if ($curPyPath -and ($curPyPath -notlike ('*' + $packagesPath + '*'))) {
    $env:PYTHONPATH = $packagesPath + ';' + $curPyPath
} elseif ($curPyPath) {
    # already contains it
} else {
    $env:PYTHONPATH = $packagesPath
}
$env:JCC_CONFIG = $Paths.Config
Write-Output ('PYTHONPATH: ' + $env:PYTHONPATH)
Write-Output ('JCC_CONFIG: ' + $env:JCC_CONFIG)

Remove-Item -LiteralPath $stdoutLog -ErrorAction SilentlyContinue | Out-Null
Remove-Item -LiteralPath $stderrLog -ErrorAction SilentlyContinue | Out-Null

Write-Output ('')
Write-Output ('starting: "' + $python + '" -m jcc_core.run   (cwd ' + $Root + ')')

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = (Join-Path $env:SystemRoot 'system32\cmd.exe')
$psi.Arguments = '/c "' + $python + ' -m jcc_core.run > "' + $stdoutLog + '" 2> "' + $stderrLog + '"'
$psi.WorkingDirectory = $Root
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
[void]$proc.Start()
$launcherPid = $proc.Id
Write-Output ('launcher pid: ' + $launcherPid)
Write-Output ('logs      : ' + $stdoutLog + ', ' + $stderrLog)

# --- wait for /api/health ----------------------------------------------- #
Write-Output ('')
Write-Output ('waiting for ' + $healthUrl + ' (timeout ' + $HealthTimeoutSec + 's) ...')

$deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
$lastFail = ''
$healthy = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $h = Invoke-JccHealth -Url $healthUrl -TimeoutMs 2000
    if ($h.Ok) {
        $healthy = $true
        break
    }
    if ($h.Error) { $lastFail = $h.Error }
    if (-not (Test-JccProcessAlive -ProcessId $launcherPid)) {
        Write-Output ('')
        $innerExit = ''
        try { if ($proc.HasExited) { $innerExit = [string]$proc.ExitCode } } catch { }
        Write-Output ('the service process (launcher pid ' + $launcherPid + ') exited before it went healthy.')
        Write-Output ('exit code inside the process: ' + $innerExit)
        Write-Output ('stderr tail:')
        $tail = Get-JccTail -Lines (Get-Content -LiteralPath $stderrLog -ErrorAction SilentlyContinue) -Count 12
        foreach ($l in $tail) { Write-Output ('  ' + $l) }
        Write-Output ('')
        Write-Output ('FAILED: the service stopped during startup. It has NOT been restarted.')
        exit 4
    }
}

if (-not $healthy) {
    Write-Output ('')
    Write-Output ('FAILED: health endpoint did not answer within ' + $HealthTimeoutSec + 's.')
    Write-Output ('last probe: ' + $lastFail)
    $owner2 = Get-JccPortOwner -Port $resolvedPort
    Write-Output ('port ' + $resolvedPort + ' owner pid: ' + $owner2.Pid + ' (' + $owner2.Method + ')')
    Write-Output ('stderr tail:')
    $tail = Get-JccTail -Lines (Get-Content -LiteralPath $stderrLog -ErrorAction SilentlyContinue) -Count 15
    foreach ($l in $tail) { Write-Output ('  ' + $l) }
    Write-Output ('')
    Write-Output ('The process started by this script is left running ONLY because it may still')
    Write-Output ('be recovering; it has not been signalled. Check the logs, then run')
    Write-Output ('.\scripts\stop-jcc-v1.2.ps1 to shut it down.')
    exit 4
}

# --- resolve the SERVICE pid (the process that holds the socket) --------- #
$ownerNow = Get-JccPortOwner -Port $resolvedPort
$servicePid = 0
$noService = 'no LISTENING socket on port ' + $resolvedPort
if ($ownerNow.Pid -gt 0) {
    $sInfo = Get-JccProcessInfo -ProcessId $ownerNow.Pid
    $sNorm = (Get-JccNormCmd -Text ([string]$sInfo.CommandLine))
    if ($sNorm -like '*-m jcc_core.run*') {
        $servicePid = $ownerNow.Pid
        $noService = ''
    } else {
        $noService = 'the holder of port ' + $resolvedPort + ' is pid ' + $ownerNow.Pid +
                     ', whose command line is "' + $sInfo.CommandLine + '" -- not a JCC service'
    }
}
if ($servicePid -le 0) {
    Write-Output ('')
    Write-Output ('REFUSED: the service came up on the port but could not be positively')
    Write-Output ('         identified as ours. ' + $noService)
    Write-Output ('         It has NOT been signalled. Confirm with')
    Write-Output ('         netstat -ano | findstr :' + $resolvedPort + ' and re-run this script.')
    Write-Output ('         A current marker exists at ' + $Paths.MarkerFile + ' (pid ' + $launcherPid + ').')
    exit 3
}

$descent = Test-JccDescent -ServicePid $servicePid `
                           -WantCommandLine ('{0} -m jcc_core.run' -f $python) `
                           -WantModule 'jcc_core.run' -AncestorPid $launcherPid
if (-not $descent.Ok) {
    Write-Output ('')
    Write-Output ('REFUSED: service pid ' + $servicePid + ' is the port holder, but the ownership')
    Write-Output ('         chain does not prove it is ours: ' + $descent.Reason)
    Write-Output ('         chain: ' + (($descent.Chain | ForEach-Object { [string]$_ }) -join ' <- '))
    Write-Output ('         It has NOT been signalled. Re-run this script after resolving it.')
    exit 3
}

Set-Content -LiteralPath $Paths.PidFile -Value ([string]$servicePid) -Encoding ASCII
Write-JccOwnershipMarker -Paths $Paths -Port $resolvedPort -PythonExe $python -Root $Root `
                         -LauncherPid $launcherPid -ServicePid $servicePid `
                         -Config $Paths.Config -CreatedBy 'scripts/start-jcc-v1.2.ps1' | Out-Null

$body = Invoke-JccHealth -Url $healthUrl -TimeoutMs 3000
$hj = Read-JccHealthJson -Body $body.Body
$summary = ''
if ($hj) {
    $summary = ('status=' + $hj.status +
                ' provider=' + $hj.jev_provider +
                ' actuation=' + $hj.actuation_enabled +
                ' policy=' + $hj.policy_version +
                ' wal=' + $hj.wal)
}

Write-Output ('')
Write-Output ('UP (v1.2, isolated from GREEN 4778)')
Write-Output ('service pid : ' + $servicePid + '   (holds 127.0.0.1:' + $resolvedPort + ')')
Write-Output ('launcher pid: ' + $launcherPid)
Write-Output ('ownership   : ' + $descent.Reason)
Write-Output ('port      : ' + $resolvedPort + ' (127.0.0.1 only)')
Write-Output ('health    : ' + $summary)
Write-Output ('health url: ' + $healthUrl)
Write-Output ('pid file  : ' + $Paths.PidFile)
Write-Output ('marker    : ' + $Paths.MarkerFile)
Write-Output ('db        : ' + $Paths.Config + ' -> runtime\jcc-v1.2\jcc-v1.2.db')
Write-Output ('next      : .\scripts\status-jcc-v1.2.ps1   |   .\scripts\stop-jcc-v1.2.ps1')
exit 0

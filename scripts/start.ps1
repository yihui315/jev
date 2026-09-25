<#
    JCC service start (W-02). Windows PowerShell 5.1 ONLY.

    Starts the ONE real JCC service process:
        <repo>\.venv\Scripts\python.exe -m jcc_core.run
    which binds 127.0.0.1:<port> (config/jcc.yaml -> server.port, normally 4778)
    and serves GET /api/health. packages/supervisor is a LIBRARY wired into the
    core engine -- it is NOT a second process and is never started here.

    Ownership model: runtime\jcc\jcc.pid + runtime\jcc\jcc.ownership.json.
    The pid file names the SERVICE process -- the one that actually holds the
    127.0.0.1:<port> socket, which on Windows is the CHILD of the process this
    script launches. The launcher pid is kept separately in the ownership marker
    as "launcher_pid". A process we did not start, or one whose identity cannot
    be verified, is never signalled -- start.ps1 refuses and exits non-zero.

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
$Paths = Get-JccPaths -Root $Root

Write-Output 'JCC start (W-02)'
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
    $resolvedPort = Get-JccPort -Root $Root -Python $python
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
    Write-Output ('         scripts\start.ps1 in this repo and the marker was lost, remove')
    Write-Output ('         runtime\jcc\jcc.pid and runtime\jcc\jcc.ownership.json by hand only')
    Write-Output ('         after confirming with  netstat -ano | findstr :' + $resolvedPort + '  which pid holds it.')
    exit 3
}

# --- foreign hold? ------------------------------------------------------ #
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
        Write-Output ('REFUSED: runtime\jcc\jcc.pid names pid ' + $stalePid + ', which is still alive:')
        Write-Output ('         ' + $info.CommandLine)
        Write-Output ('         That process was not positively verified as ours, so it is not signalled.')
        exit 3
    }
    Write-Output ('stale pid file found for pid ' + $stalePid + ' (dead) -- overwriting')
    Remove-Item -LiteralPath $Paths.PidFile -Force -ErrorAction SilentlyContinue
}

# --- launch ------------------------------------------------------------- #
# The child's stdout/stderr go STRAIGHT into files: cmd.exe opens the two log
# files and the service inherits those handles, so the service -- not this
# script -- owns the descriptors. There is no pipe, hence no OutputDataReceived
# event, no BeginOutputReadLine, and no buffer that could fill up and stall the
# service. "python -m jcc_core.run" needs the package directory on PYTHONPATH
# before run.py executes (run.py's own sys.path bootstrap never runs under -m),
# so it is set HERE, in this process, and inherited by the child.
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

# '>' truncates: each start begins with a fresh server.log / server.err.log, so
# the stderr tail printed on failure can only ever be THIS run's output. There
# is no rotation step and no reader -- the previous content is gone, by design.
Remove-Item -LiteralPath $stdoutLog -ErrorAction SilentlyContinue | Out-Null
Remove-Item -LiteralPath $stderrLog -ErrorAction SilentlyContinue | Out-Null

Write-Output ('')
Write-Output ('starting: "' + $python + '" -m jcc_core.run   (cwd ' + $Root + ')')

# The child's stdout/stderr are handed straight to the two log files: cmd.exe
# opens them and the service inherits them, so the SERVICE -- not this script --
# owns the descriptors. No pipe, hence no OutputDataReceived event, no
# BeginOutputReadLine, and no buffer that could fill up and stall the service.
# "python -m jcc_core.run" needs the package directory on PYTHONPATH BEFORE
# run.py executes (run.py's own sys.path bootstrap never runs under -m), so it is
# set HERE, in this process, and inherited by the child.
# NOTE: System.Diagnostics.Process cannot do this on Windows PowerShell 5.1:
# EnvironmentVariables reads $null on first access (measured: NullArray), and
# RedirectStandardOutput builds pipes that this script would have to drain --
# the OutputDataReceived drain does not fire there at all.
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
    Write-Output ('.\scripts\stop.ps1 to shut it down.')
    exit 4
}

# --- resolve the SERVICE pid (the process that holds the socket) --------- #
# The launcher only owns the redirected log handles; on Windows the listening
# socket belongs to its child. jcc.pid therefore records the SERVICE pid, and
# the marker carries the launcher pid alongside it.
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
                         -LauncherPid $launcherPid -ServicePid $servicePid | Out-Null

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
Write-Output ('UP')
Write-Output ('service pid : ' + $servicePid + '   (holds 127.0.0.1:' + $resolvedPort + ')')
Write-Output ('launcher pid: ' + $launcherPid)
Write-Output ('ownership   : ' + $descent.Reason)
Write-Output ('port      : ' + $resolvedPort + ' (127.0.0.1 only)')
Write-Output ('health    : ' + $summary)
Write-Output ('health url: ' + $healthUrl)
Write-Output ('pid file  : ' + $Paths.PidFile)
Write-Output ('marker    : ' + $Paths.MarkerFile)
Write-Output ('next      : .\scripts\status.ps1   |   .\scripts\stop.ps1')
exit 0

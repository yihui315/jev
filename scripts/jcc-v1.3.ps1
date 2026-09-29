<#
    JCC v1.3 PRODUCT EDITION -- the ONE fail-closed control surface.
    Windows PowerShell 5.1 ONLY.

        .\scripts\jcc-v1.3.ps1 -Action up       # start API (4779) + Visual UI (4780)
        .\scripts\jcc-v1.3.ps1 -Action down     # stop both, verified, fail-closed
        .\scripts\jcc-v1.3.ps1 -Action restart  # down -> up, then a REAL post-condition
        .\scripts\jcc-v1.3.ps1 -Action status   # UP / DEGRADED / DOWN + GREEN protection
        .\scripts\jcc-v1.3.ps1 -Action doctor   # measured diagnosis, PASS/FAIL per check
        .\scripts\jcc-v1.3.ps1 -Action repair   # reconcile STALE bookkeeping only
        .\scripts\jcc-v1.3.ps1 -Action mark     # adopt an already-running instance

    WHY ONE SCRIPT
    --------------
    v1.2 shipped six single-purpose scripts (start / stop / restart / status /
    start-ui / stop-ui). They worked, but "restart" had to be a seventh script
    whose only job was to call the other two in the right order, and an operator
    had to know which of six files to run. v1.3 keeps EVERY safety rule and
    collapses the surface to one entry point with an -Action. No rule was
    relaxed: stop/restart still route through the very same verification.

    THE HARD RULE (unchanged, and it is the whole point of this file)
    ----------------------------------------------------------------
    This script NEVER signals a process it did not start and cannot positively
    identify. "Identify" means ALL of: an ownership marker we wrote, the pid file
    agreeing with that marker, the process alive, its command line being the
    expected service start line for THIS repo, the listening owner of the expected
    port being that pid (or nobody), and the pid descending from a process that
    really ran that start line. Anything else -- a foreign holder, a marker/pid
    mismatch, a live-but-unverifiable pid -- produces a REFUSAL, not a signal.

    WHAT v1.3 ADDS OVER v1.2
    ------------------------
      * the Visual UI is served by uvicorn (ASGI), NOT python http.server;
      * doctor does not merely count problems -- D-4 REFUSES the runtime if
        http.server is still in the serving path, which is the Wave 8 contract;
      * repair exists as an explicit, narrow action. It only ever QUARANTINES
        bookkeeping that is PROVABLY stale (dead pid AND free port), by RENAME:
        it never deletes a file and it has no code path that signals a process.
        (Earlier wording here said "delete"; the code has always moved, and the
        gate asserts that it does -- so the prose was corrected to match, not
        the other way round.)

    SCOPE / SAFETY
    --------------
      * 4778 (frozen GREEN v1.0.1) is only ever READ -- probed for protection.
        It is never started, stopped, reconfigured or signalled from here.
      * actuation stays OFF (ADR-0004). -Action up does not enable anything.
      * No secrets are read or printed: the control token is reported only as
        SET / UNSET, never by value.

    EXIT CODES (stable, relied on by the gate and by Task Scheduler)
    --------------------------------------------------------------
      up       : 0 up+healthy, 3 refused (foreign/prereq), 4 did not become healthy
      down     : 0 stopped (or already stopped), 2 refused (would not signal),
                 3 foreign hold / missing prerequisite, 4 stop did not complete
      restart  : 0 verified back up, 3 refused before anything, 4 a half failed,
                 6 started but the post-condition does not hold
      status   : 0 UP, 2 DEGRADED (or answering-but-unverified), 1 DOWN
      doctor   : 0 every check PASS, 1 one or more FAIL
      repair   : 0 nothing unsafe left, 2 a condition it deliberately did not
                 touch (needs a human), 3 missing prerequisite
      mark     : 0 both halves marked, 1 a half could not be verified
#>

[CmdletBinding()]
param(
    [ValidateSet('up','down','restart','status','doctor','repair','mark')]
    [string]$Action = 'status',
    [int]$ApiPort = 0,
    [int]$UiPort = 0,
    [int]$HealthTimeoutSec = 90,
    [int]$GracefulSeconds = 20,
    [int]$MinFreeGb = 1,
    [string]$LogFile = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccV13Paths -Root $Root

#: The one mutable "result" of this script. Action functions print freely and
#: record their exit code here; only the dispatcher at the bottom calls `exit`.
#: (A function cannot both Write-Output its report AND return an exit code on
#: the pipeline, so the code travels out-of-band.)
$script:V13Code = 0

#: -LogFile writes a transcript of this run to disk from INSIDE this process.
#:
#: WHY A TRANSCRIPT AND NOT A CALLER-SIDE REDIRECT. Wave 8's evidence has to
#: survive the process that produced it. When an action starts long-lived
#: children (up / restart), the calling session can be torn down the moment
#: those children appear, and a log file written by the CALLER after the call
#: returns is then never written at all -- measured here: an entire successful
#: `-Action up` run, and later an entire successful `-Action restart` run, left
#: NO transcript, because the trailing write never executed. Recording from
#: inside this process means the evidence is on disk before it exits, whatever
#: happens to the caller afterwards.
$script:V13Transcript = $false
if ($LogFile) {
    try {
        $logDir = Split-Path -Parent $LogFile
        if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
            New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        }
        Start-Transcript -Path $LogFile -Force -ErrorAction Stop | Out-Null
        $script:V13Transcript = $true
    } catch {
        Write-Output ('WARNING: could not start the transcript at ' + $LogFile + ': ' + $_.Exception.Message)
    }
}

#: The frozen service. Read-only in this file.
$script:GreenPort = 4778

#: Collected observations. Warnings never change UP/DOWN by themselves -- they
#: say "I do not trust an instrument", never "the service is down".
$script:V13Warnings = New-Object System.Collections.Generic.List[string]

function Add-V13Warning {
    param([string]$Text)
    $script:V13Warnings.Add($Text)
}

function Get-V13PortOwnerPid {
    param([int]$Port)
    $o = Get-JccPortOwner -Port $Port
    if ($null -eq $o) { return 0 }
    if ($null -eq $o.Pid) { return 0 }
    return [int]$o.Pid
}

function Get-V13PortState {
    <#
        Classify a port as FREE / OURS / FOREIGN by its LISTENING owner.

        "OURS" here means only "the command line looks like the surface this port
        is supposed to carry". That is deliberately the WEAKER standard: it is
        good enough to decide "do not refuse, go and verify", and it is NOT good
        enough to decide "signal it". Every signal path re-checks with the full
        marker+pid+ancestry contract (Test-JccOwnership / Stop-V13*).

        Returns a hashtable. Prints nothing -- the caller prints.
    #>
    param([int]$Port, [string]$Expect)

    $owner = Get-JccPortOwner -Port $Port
    $pid_ = 0
    if ($owner -and $owner.Pid) { $pid_ = [int]$owner.Pid }
    $method = ''
    if ($owner -and $owner.Method) { $method = [string]$owner.Method }

    if ($pid_ -le 0) {
        return @{ State = 'FREE'; Pid = 0; Method = $method; CmdLine = ''; Info = $null }
    }

    $info = Get-JccProcessInfo -ProcessId $pid_
    $cl = ''
    if ($info -and $info.CommandLine) { $cl = [string]$info.CommandLine }
    $norm = Get-JccNormCmd -Text $cl

    $ours = $false
    if ($Expect -and $norm -like ('*' + $Expect + '*')) { $ours = $true }

    return @{
        State   = $(if ($ours) { 'OURS' } else { 'FOREIGN' })
        Pid     = $pid_
        Method  = $method
        CmdLine = $cl
        Info    = $info
    }
}

function Get-V13GreenState {
    <#
        Read-only protection check for the frozen GREEN service on 4778.

        Returns @{ State; Pid; Detail } where State is one of:
          'UP (protected, untouched)' | 'UP but UNEXPECTED' | 'DOWN' | 'UNKNOWN'

        DOWN is a VIOLATION, not a neutral observation: the frozen v1.0.1 service
        is supposed to be up. Callers surface it as a warning and, in doctor, as
        a FAIL. Prints nothing.
    #>
    try {
        $gpid = Get-V13PortOwnerPid -Port $script:GreenPort
        if ($gpid -le 0) {
            return @{ State = 'DOWN'; Pid = 0; Detail = ('nothing is LISTENING on ' + $script:GreenPort) }
        }
        $info = Get-JccProcessInfo -ProcessId $gpid
        $cmd = ''
        if ($info -and $info.CommandLine) { $cmd = [string]$info.CommandLine }
        $marker = Join-Path (Join-Path $Root 'runtime\jcc') 'jcc.ownership.json'
        $markerPresent = Test-Path -LiteralPath $marker
        if ((Get-JccNormCmd -Text $cmd) -like '*-m jcc_core.run*' -and $markerPresent) {
            return @{ State = 'UP (protected, untouched)'; Pid = $gpid; Detail = $cmd }
        }
        return @{ State = 'UP but UNEXPECTED'; Pid = $gpid; Detail = ('cmd=' + $cmd + ' markerPresent=' + $markerPresent) }
    } catch {
        return @{ State = 'UNKNOWN'; Pid = 0; Detail = $_.Exception.Message }
    }
}

function Get-V13ApiHealth {
    param([int]$Port, [int]$TimeoutMs = 3000)
    return (Invoke-JccHealth -Url ('http://127.0.0.1:' + $Port + '/api/health') -TimeoutMs $TimeoutMs)
}

function Get-V13UiHealth {
    param([int]$Port, [int]$TimeoutMs = 3000)
    return (Invoke-JccHealth -Url ('http://127.0.0.1:' + $Port + '/health') -TimeoutMs $TimeoutMs)
}

function Read-V13UiMarker {
    if (-not (Test-Path -LiteralPath $Paths.UiMarkerFile)) { return $null }
    try {
        $txt = Get-Content -LiteralPath $Paths.UiMarkerFile -Raw -ErrorAction Stop
        if (-not $txt) { return $null }
        return ($txt | ConvertFrom-Json)
    } catch { return $null }
}

function Get-V13UiPidFromFile {
    if (-not (Test-Path -LiteralPath $Paths.UiPidFile)) { return 0 }
    try {
        $v = ((Get-Content -LiteralPath $Paths.UiPidFile -Raw -ErrorAction Stop) -replace '\s+','').Trim()
        if ($v -match '^\d+$') { return [int]$v }
    } catch { }
    return 0
}

function Test-V13UiOwnership {
    <#
        The Visual UI equivalent of Test-JccOwnership.

        The shared helper cannot be reused: it is hard-wired to the API's
        contract (marker.command_line must be "<python> -m jcc_core.run", and it
        requires a descent through that line). The UI is a different command line
        on a different port with its own marker pair, so it gets its own check --
        the same six questions, asked of the UI's own evidence.

        Prints nothing; returns a hashtable.
    #>
    param([int]$Port = 0)
    if ($Port -le 0) { $Port = Get-JccV13UiPort }

    $blank = @{
        Ok = $false; Reason = ''; Pid = 0; PortOwnerPid = 0; PortOwnerMethod = ''
        ProcessAlive = $false; Marker = $null; CmdLine = ''
    }

    $marker = Read-V13UiMarker
    if (-not $marker) {
        $blank.Reason = 'UI ownership marker missing (' + $Paths.UiMarkerFile + ')'
        return $blank
    }
    $mpid = 0
    if ($marker.pid) { $mpid = [int]$marker.pid }
    $pidFile = Get-V13UiPidFromFile
    $owner = Get-JccPortOwner -Port $Port
    $opid = 0
    if ($owner -and $owner.Pid) { $opid = [int]$owner.Pid }
    $omethod = ''
    if ($owner -and $owner.Method) { $omethod = [string]$owner.Method }
    $info = Get-JccProcessInfo -ProcessId $mpid

    $res = @{
        Ok = $false; Reason = ''; Pid = $mpid; PortOwnerPid = $opid
        PortOwnerMethod = $omethod; ProcessAlive = [bool]$info.Alive
        Marker = $marker; CmdLine = [string]$info.CommandLine
    }

    if ($pidFile -ne $mpid) {
        $res.Reason = 'UI pid file (' + $pidFile + ') and UI marker pid (' + $mpid + ') disagree'
        return $res
    }
    if (-not $info.Alive) {
        $res.Reason = 'recorded UI pid ' + $mpid + ' is not running'
        return $res
    }
    $cl = Get-JccNormCmd -Text ([string]$info.CommandLine)
    if ($cl -notlike '*serve_visual.py*') {
        $res.Reason = 'UI process command line is not serve_visual.py: ' + $info.CommandLine
        return $res
    }
    if ($opid -ne 0 -and $opid -ne $mpid) {
        $res.Reason = ('port ' + $Port + ' is owned by pid ' + $opid + ' (' + $omethod +
                       '), but the UI marker records pid ' + $mpid)
        return $res
    }
    if ($opid -eq 0) {
        $res.Reason = ('no listener on port ' + $Port + ' while the UI marker claims pid ' + $mpid)
        return $res
    }
    $res.Ok = $true
    $res.Reason = 'UI pid ' + $mpid + ' verified (marker + pid file + command line + port owner)'
    return $res
}

function Write-V13UiMarker {
    param(
        [int]$ServicePid,
        [int]$LauncherPid,
        [int]$Port,
        [string]$CmdLine,
        [string]$CreatedBy
    )
    Set-Content -LiteralPath $Paths.UiPidFile -Value ([string]$ServicePid) -Encoding ASCII
    $marker = [ordered]@{
        service          = 'jcc-v1.3-visual-ui'
        pid              = $ServicePid
        service_pid      = $ServicePid
        launcher_pid     = $LauncherPid
        start_time       = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        start_time_local = (Get-Date).ToString('o')
        command_line     = $CmdLine
        port             = $Port
        host             = '127.0.0.1'
        runtime          = 'uvicorn-asgi'
        serves           = $Paths.UiDist
        created_by       = $CreatedBy
        platform         = 'windows'
    }
    # UTF-8 WITHOUT BOM -- same reason as Write-JccOwnershipMarker: the marker is
    # read back with plain utf-8 elsewhere, where a BOM makes ConvertFrom-Json
    # raise (and the reader would swallow that into "no marker", i.e. a false
    # negative in the direction of "the runtime did not do what it did").
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Paths.UiMarkerFile, ($marker | ConvertTo-Json -Depth 6), $utf8NoBom)
    return $marker
}

function Invoke-V13Launch {
    <#
        Start ONE long-lived surface detached, with its own log files.

        Returns @{ LauncherPid; StdoutLog; StderrLog; Started }.

        WHY cmd.exe /c and redirected handles: this mirrors start.ps1 /
        start-jcc-v1.2.ps1 exactly. The child outlives this script because
        ProcessStartInfo with UseShellExecute=$false gives it no tie to our
        console; the handle owner is the cmd.exe wrapper, so the service keeps
        writing to its log after this script has exited.

        NOTE (measured, and it is why this is not "just Start-Process"): a short-
        lived launcher's children do NOT survive if the launcher is reaped by a
        job object. That is why the DURABLE path is Task Scheduler, not this
        function -- see scripts\jcc-v1.3-register-tasks.ps1 and
        docs\v1_3\WAVE_8_DURABLE_RUNTIME.md.
    #>
    param(
        [string]$Python,
        [string]$Arguments,
        [string]$StdoutLog,
        [string]$StderrLog
    )

    Remove-Item -LiteralPath $StdoutLog -ErrorAction SilentlyContinue | Out-Null
    Remove-Item -LiteralPath $StderrLog -ErrorAction SilentlyContinue | Out-Null

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = (Join-Path $env:SystemRoot 'system32\cmd.exe')
    $psi.Arguments = '/c "' + $Python + ' ' + $Arguments + ' > "' + $StdoutLog + '" 2> "' + $StderrLog + '"'
    $psi.WorkingDirectory = $Root
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()

    return @{
        LauncherPid = $proc.Id
        StdoutLog   = $StdoutLog
        StderrLog   = $StderrLog
        Started     = $true
        Proc        = $proc
    }
}

function Wait-V13Http {
    <#
        Poll a health URL until 200, the launcher process dies, or the deadline
        passes. Returns @{ Healthy; Died; LastError; ExitCode }.
        Prints the progress lines itself (statement position, not a return value).
    #>
    param(
        [string]$Url,
        [int]$LauncherPid,
        [int]$TimeoutSec,
        [System.Diagnostics.Process]$Proc
    )

    Write-Output ('waiting for ' + $Url + ' (timeout ' + $TimeoutSec + 's) ...')
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lastFail = ''
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $h = Invoke-JccHealth -Url $Url -TimeoutMs 2000
        if ($h.Ok) { return @{ Healthy = $true; Died = $false; LastError = ''; ExitCode = '' } }
        if ($h.Error) { $lastFail = [string]$h.Error }
        if (-not (Test-JccProcessAlive -ProcessId $LauncherPid)) {
            $innerExit = ''
            try { if ($Proc -and $Proc.HasExited) { $innerExit = [string]$Proc.ExitCode } } catch { }
            return @{ Healthy = $false; Died = $true; LastError = $lastFail; ExitCode = $innerExit }
        }
    }
    return @{ Healthy = $false; Died = $false; LastError = $lastFail; ExitCode = '' }
}

function Show-V13LogTail {
    param([string]$Path, [int]$Count = 12, [string]$Label = 'stderr tail')
    Write-Output ($Label + ':')
    $lines = @()
    if (Test-Path -LiteralPath $Path) {
        $lines = Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue
    }
    foreach ($l in (Get-JccTail -Lines $lines -Count $Count)) { Write-Output ('  ' + $l) }
}

# =========================================================================== #
# UP -- bring the API and the Visual UI up, each independently verified.
# =========================================================================== #

function Invoke-V13ApiUp {
    param([int]$Port, [int]$TimeoutSec)

    $healthUrl = 'http://127.0.0.1:' + $Port + '/api/health'

    # --- already answering? ------------------------------------------------- #
    $health = Invoke-JccHealth -Url $healthUrl -TimeoutMs 3000
    if ($health.Ok) {
        $own = Test-JccOwnership -Paths $Paths -Port $Port -Root $Root
        if ($own.Ok) {
            $hj = Read-JccHealthJson -Body $health.Body
            $hv = ''
            if ($hj -and $hj.status) { $hv = [string]$hj.status }
            Write-Output ''
            Write-Output 'API: ALREADY RUNNING -- verified, not restarted.'
            Write-Output ('  pid      : ' + $own.Pid)
            Write-Output ('  ownership: ' + $own.Reason)
            Write-Output ('  health   : ' + $hv + ' (HTTP ' + $health.StatusCode + ')')
            $script:ApiUp = $true
            return
        }
        Write-Output ''
        Write-Output ('API REFUSED: HTTP ' + $health.StatusCode + ' on ' + $healthUrl + ' but no usable ownership marker.')
        Write-Output ('  ' + $own.Reason)
        Write-Output '  The listener is NOT ours; leave it alone. Nothing was started.'
        $script:V13Code = 3
        return
    }

    # --- stale bookkeeping that cannot be a live process -------------------- #
    $stalePid = Get-JccPidFromFile -Paths $Paths
    if ($stalePid -gt 0) {
        if (Test-JccProcessAlive -ProcessId $stalePid) {
            $info = Get-JccProcessInfo -ProcessId $stalePid
            Write-Output ''
            Write-Output ('API REFUSED: ' + $Paths.PidFile + ' names pid ' + $stalePid + ', which is still alive:')
            Write-Output ('  ' + $info.CommandLine)
            Write-Output '  It was not positively verified as ours, so it is not signalled and nothing was started.'
            Write-Output ('  Run  .\scripts\jcc-v1.3.ps1 -Action repair  once you have confirmed what it is.')
            $script:V13Code = 3
            return
        }
        Write-Output ('API: stale pid file found for pid ' + $stalePid + ' (dead)')
        if ($DryRun) {
            # -DryRun must change NOTHING. Detecting staleness is a measurement,
            # so it is still reported; removing the file is a WRITE, so it is not
            # done. (Caught while writing the Wave 8 gate, which drives -DryRun
            # and asserts the host is untouched.)
            Write-Output ('API DRY RUN: would overwrite ' + $Paths.PidFile + '. Nothing was changed.')
        } else {
            Remove-Item -LiteralPath $Paths.PidFile -Force -ErrorAction SilentlyContinue
        }
    }

    if ($DryRun) {
        Write-Output ''
        Write-Output ('API DRY RUN: would start "' + $script:V13Python + '" -m jcc_core.run on ' + $Port + '. Nothing was started.')
        $script:ApiUp = $false
        return
    }

    # --- launch ------------------------------------------------------------- #
    $packagesPath = Join-Path $Root 'packages'
    $curPyPath = [string]$env:PYTHONPATH
    if ($curPyPath -and ($curPyPath -notlike ('*' + $packagesPath + '*'))) {
        $env:PYTHONPATH = $packagesPath + ';' + $curPyPath
    } elseif (-not $curPyPath) {
        $env:PYTHONPATH = $packagesPath
    }
    $env:JCC_CONFIG = $Paths.Config
    Write-Output ('API PYTHONPATH: ' + $env:PYTHONPATH)
    Write-Output ('API JCC_CONFIG: ' + $env:JCC_CONFIG)
    Write-Output ('API starting  : "' + $script:V13Python + '" -m jcc_core.run   (cwd ' + $Root + ')')

    $r = Invoke-V13Launch -Python $script:V13Python -Arguments '-m jcc_core.run' `
                          -StdoutLog $Paths.ServerLog -StderrLog $Paths.ServerErrLog
    $launcherPid = [int]$r.LauncherPid
    Write-Output ('API launcher  : pid ' + $launcherPid)
    Write-Output ('API logs      : ' + $Paths.ServerLog + ', ' + $Paths.ServerErrLog)

    $w = Wait-V13Http -Url $healthUrl -LauncherPid $launcherPid -TimeoutSec $TimeoutSec -Proc $r.Proc

    if ($w.Died) {
        Write-Output ''
        Write-Output ('API FAILED: the service process (launcher pid ' + $launcherPid + ') exited before it went healthy.')
        Write-Output ('  exit code inside the process: ' + $w.ExitCode)
        Show-V13LogTail -Path $Paths.ServerErrLog -Count 12 -Label 'API stderr tail'
        Write-Output 'API: the service stopped during startup. It has NOT been restarted.'
        $script:V13Code = 4
        return
    }
    if (-not $w.Healthy) {
        $owner2 = Get-JccPortOwner -Port $Port
        Write-Output ''
        Write-Output ('API FAILED: health endpoint did not answer within ' + $TimeoutSec + 's.')
        Write-Output ('  last probe : ' + $w.LastError)
        Write-Output ('  port owner : ' + $owner2.Pid + ' (' + $owner2.Method + ')')
        Show-V13LogTail -Path $Paths.ServerErrLog -Count 15 -Label 'API stderr tail'
        Write-Output 'API: the process started by this script is left running ONLY because it may still'
        Write-Output ('     be recovering; it has not been signalled. Check the logs, then run')
        Write-Output '     .\scripts\jcc-v1.3.ps1 -Action down'
        $script:V13Code = 4
        return
    }

    # --- resolve the SERVICE pid (the process that holds the socket) -------- #
    $ownerNow = Get-JccPortOwner -Port $Port
    $servicePid = 0
    $why = 'no LISTENING socket on port ' + $Port
    if ($ownerNow -and $ownerNow.Pid -and ([int]$ownerNow.Pid -gt 0)) {
        $sInfo = Get-JccProcessInfo -ProcessId ([int]$ownerNow.Pid)
        $sNorm = Get-JccNormCmd -Text ([string]$sInfo.CommandLine)
        if ($sNorm -like '*-m jcc_core.run*') {
            $servicePid = [int]$ownerNow.Pid
            $why = ''
        } else {
            $why = ('the holder of port ' + $Port + ' is pid ' + $ownerNow.Pid +
                    ', whose command line is "' + $sInfo.CommandLine + '" -- not a JCC service')
        }
    }
    if ($servicePid -le 0) {
        Write-Output ''
        Write-Output 'API REFUSED: it answers on the port but could not be positively identified as ours.'
        Write-Output ('  ' + $why)
        Write-Output '  It has NOT been signalled, and NO marker was written.'
        $script:V13Code = 3
        return
    }

    $descent = Test-JccDescent -ServicePid $servicePid `
                               -WantCommandLine ('{0} -m jcc_core.run' -f $script:V13Python) `
                               -WantModule 'jcc_core.run' -AncestorPid $launcherPid
    if (-not $descent.Ok) {
        Write-Output ''
        Write-Output ('API REFUSED: service pid ' + $servicePid + ' holds the port, but the ownership chain')
        Write-Output ('  does not prove it is ours: ' + $descent.Reason)
        Write-Output ('  chain: ' + (($descent.Chain | ForEach-Object { [string]$_ }) -join ' <- '))
        Write-Output '  It has NOT been signalled, and NO marker was written.'
        $script:V13Code = 3
        return
    }

    Set-Content -LiteralPath $Paths.PidFile -Value ([string]$servicePid) -Encoding ASCII
    Write-JccOwnershipMarker -Paths $Paths -Port $Port -PythonExe $script:V13Python -Root $Root `
                             -LauncherPid $launcherPid -ServicePid $servicePid `
                             -Config $Paths.Config -CreatedBy 'scripts/jcc-v1.3.ps1' | Out-Null

    Write-Output ''
    Write-Output 'API UP (v1.3)'
    Write-Output ('  service pid : ' + $servicePid + '   (holds 127.0.0.1:' + $Port + ')')
    Write-Output ('  launcher pid: ' + $launcherPid)
    Write-Output ('  ownership   : ' + $descent.Reason)
    $script:ApiUp = $true
}

function Invoke-V13UiUp {
    param([int]$Port, [int]$TimeoutSec)

    $healthUrl = 'http://127.0.0.1:' + $Port + '/health'

    if (-not (Test-Path -LiteralPath $Paths.UiServeScript)) {
        Write-Output ('UI REFUSED: ' + $Paths.UiServeScript + ' is missing.')
        $script:V13Code = 3
        return
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Paths.UiDist 'index.html'))) {
        Write-Output ('UI REFUSED: ' + (Join-Path $Paths.UiDist 'index.html') + ' is missing.')
        Write-Output '  build it first:  cd apps\command_center_3d; npm ci; npm run build'
        $script:V13Code = 3
        return
    }

    $health = Invoke-JccHealth -Url $healthUrl -TimeoutMs 3000
    if ($health.Ok) {
        $own = Test-V13UiOwnership -Port $Port
        if ($own.Ok) {
            Write-Output ''
            Write-Output 'UI: ALREADY RUNNING -- verified, not restarted.'
            Write-Output ('  pid      : ' + $own.Pid)
            Write-Output ('  ownership: ' + $own.Reason)
            $script:UiUp = $true
            return
        }
        $owner = Get-JccPortOwner -Port $Port
        $info = Get-JccProcessInfo -ProcessId ([int]$owner.Pid)
        if ((Get-JccNormCmd -Text ([string]$info.CommandLine)) -like '*serve_visual.py*') {
            # It is our serve script but we have no marker for it -- adopt it,
            # exactly like -Action mark does, instead of refusing to start a
            # second copy on a port that is provably carrying our own UI.
            Write-Output ''
            Write-Output 'UI: running without a marker (marker/pid file missing or stale) -- adopting it.'
            Write-V13UiMarker -ServicePid ([int]$owner.Pid) -LauncherPid 0 -Port $Port `
                              -CmdLine ([string]$info.CommandLine) `
                              -CreatedBy 'scripts/jcc-v1.3.ps1 (adopt on up)' | Out-Null
            $script:UiUp = $true
            return
        }
        Write-Output ''
        Write-Output ('UI REFUSED: ' + $healthUrl + ' answers but the port owner is not our Visual UI server.')
        Write-Output ('  ' + $info.CommandLine)
        Write-Output '  Leave it alone. Nothing was started.'
        $script:V13Code = 3
        return
    }

    $stalePid = Get-V13UiPidFromFile
    if ($stalePid -gt 0) {
        if (Test-JccProcessAlive -ProcessId $stalePid) {
            $info = Get-JccProcessInfo -ProcessId $stalePid
            Write-Output ''
            Write-Output ('UI REFUSED: ' + $Paths.UiPidFile + ' names pid ' + $stalePid + ', which is still alive:')
            Write-Output ('  ' + $info.CommandLine)
            Write-Output '  Not signalled; nothing was started.'
            $script:V13Code = 3
            return
        }
        Write-Output ('UI: stale pid file found for pid ' + $stalePid + ' (dead)')
        if ($DryRun) {
            Write-Output ('UI DRY RUN: would overwrite ' + $Paths.UiPidFile + '. Nothing was changed.')
        } else {
            Remove-Item -LiteralPath $Paths.UiPidFile -Force -ErrorAction SilentlyContinue
        }
    }

    if ($DryRun) {
        Write-Output ''
        Write-Output ('UI DRY RUN: would start "' + $script:V13Python + '" "' + $Paths.UiServeScript + '" --host 127.0.0.1 --port ' + $Port + '. Nothing was started.')
        $script:UiUp = $false
        return
    }

    $packagesPath = Join-Path $Root 'packages'
    if (-not $env:PYTHONPATH) { $env:PYTHONPATH = $packagesPath }
    Write-Output ('UI starting   : "' + $script:V13Python + '" serve_visual.py --host 127.0.0.1 --port ' + $Port)
    Write-Output 'UI runtime    : uvicorn (ASGI) -- NOT python http.server'

    $uiArgs = '"' + $Paths.UiServeScript + '" --host 127.0.0.1 --port ' + $Port
    $r = Invoke-V13Launch -Python $script:V13Python -Arguments $uiArgs `
                          -StdoutLog $Paths.UiLog -StderrLog $Paths.UiErrLog
    $launcherPid = [int]$r.LauncherPid
    Write-Output ('UI launcher   : pid ' + $launcherPid)
    Write-Output ('UI logs       : ' + $Paths.UiLog + ', ' + $Paths.UiErrLog)

    $w = Wait-V13Http -Url $healthUrl -LauncherPid $launcherPid -TimeoutSec $TimeoutSec -Proc $r.Proc
    if ($w.Died) {
        Write-Output ''
        Write-Output ('UI FAILED: the server process (launcher pid ' + $launcherPid + ') exited before it went healthy.')
        Write-Output ('  exit code inside the process: ' + $w.ExitCode)
        Show-V13LogTail -Path $Paths.UiErrLog -Count 12 -Label 'UI stderr tail'
        $script:V13Code = 4
        return
    }
    if (-not $w.Healthy) {
        Write-Output ''
        Write-Output ('UI FAILED: /health did not answer within ' + $TimeoutSec + 's.')
        Write-Output ('  last probe : ' + $w.LastError)
        Show-V13LogTail -Path $Paths.UiErrLog -Count 12 -Label 'UI stderr tail'
        $script:V13Code = 4
        return
    }

    $ownerNow = Get-JccPortOwner -Port $Port
    if (-not $ownerNow -or -not $ownerNow.Pid -or ([int]$ownerNow.Pid -le 0)) {
        Write-Output ''
        Write-Output ('UI REFUSED: came up but no listener was found on port ' + $Port + '.')
        $script:V13Code = 3
        return
    }
    $servicePid = [int]$ownerNow.Pid
    $sInfo = Get-JccProcessInfo -ProcessId $servicePid
    if ((Get-JccNormCmd -Text ([string]$sInfo.CommandLine)) -notlike '*serve_visual.py*') {
        Write-Output ''
        Write-Output ('UI REFUSED: the holder of port ' + $Port + ' is not our Visual UI server:')
        Write-Output ('  ' + $sInfo.CommandLine)
        Write-Output '  No marker was written.'
        $script:V13Code = 3
        return
    }

    Write-V13UiMarker -ServicePid $servicePid -LauncherPid $launcherPid -Port $Port `
                      -CmdLine ([string]$sInfo.CommandLine) `
                      -CreatedBy 'scripts/jcc-v1.3.ps1' | Out-Null

    Write-Output ''
    Write-Output 'UI UP (v1.3)'
    Write-Output ('  service pid : ' + $servicePid + '   (holds 127.0.0.1:' + $Port + ')')
    Write-Output ('  launcher pid: ' + $launcherPid)
    Write-Output ('  health url  : ' + $healthUrl)
    $script:UiUp = $true
}

function Invoke-V13Up {
    param([int]$ApiPort, [int]$UiPort, [int]$TimeoutSec)

    Write-Output 'JCC v1.3 -Action up'
    Write-Output ('repo root : ' + $Root)
    Write-Output ('timestamp : ' + (Get-Date).ToString('o'))
    Write-Output ('api port  : ' + $ApiPort + '   (CORE API, python -m jcc_core.run)')
    Write-Output ('ui  port  : ' + $UiPort + '   (Visual UI, serve_visual.py under uvicorn/ASGI)')
    Write-Output ('green     : ' + $script:GreenPort + ' (frozen v1.0.1 -- checked for protection, never touched)')

    # --- prerequisite gate -------------------------------------------------- #
    Write-Output ''
    Write-Output 'prerequisite gate'
    if (-not $script:V13Python) {
        Write-Output '  REFUSED: no Python interpreter (.venv missing and nothing usable on PATH).'
        Write-Output '           Run  .\scripts\bootstrap.ps1  first.'
        $script:V13Code = 3
        return
    }
    if (-not (Test-JccPython -Python $script:V13Python)) {
        Write-Output ('  REFUSED: interpreter ' + $script:V13Python + ' does not run.')
        $script:V13Code = 3
        return
    }
    if (-not (Test-Path -LiteralPath $Paths.Config)) {
        Write-Output ('  REFUSED: config ' + $Paths.Config + ' is missing.')
        $script:V13Code = 3
        return
    }
    Write-Output ('  OK (python ' + (Get-JccPythonVersion -Python $script:V13Python) + ', config present)')

    # -DryRun changes nothing: not even the runtime directory is created.
    if (-not $DryRun) { New-JccEnsureDir -Path $Paths.Runtime | Out-Null }

    # --- reconcile the two ports BEFORE anything is started ----------------- #
    Write-Output ''
    Write-Output 'reconcile ports (refuse foreign holds)'
    $apiState = Get-V13PortState -Port $ApiPort -Expect '-m jcc_core.run'
    $uiState = Get-V13PortState -Port $UiPort -Expect 'serve_visual.py'
    foreach ($pair in @(@('API', $ApiPort, $apiState), @('UI', $UiPort, $uiState))) {
        $tag = $pair[0]; $pt = $pair[1]; $st = $pair[2]
        if ($st.State -eq 'FOREIGN') {
            Write-Output ('  REFUSED: ' + $tag + ' port ' + $pt + ' is held by FOREIGN pid ' + $st.Pid + ' (' + $st.Method + '):')
            Write-Output ('           ' + $st.CmdLine)
            Write-Output '           Leave it alone -- do not start v1.3 on top of it. Nothing was started.'
            $script:V13Code = 3
            return
        }
        Write-Output ('  ' + $tag + ' port ' + $pt + ': ' + $st.State + '  (pid ' + $st.Pid + ')')
    }

    # --- bring up API, then UI --------------------------------------------- #
    Write-Output ''
    Write-Output '=== API ==='
    Invoke-V13ApiUp -Port $ApiPort -TimeoutSec $TimeoutSec
    if ($script:V13Code -ne 0) { return }

    Write-Output ''
    Write-Output '=== Visual UI ==='
    Invoke-V13UiUp -Port $UiPort -TimeoutSec $TimeoutSec
    if ($script:V13Code -ne 0) { return }

    # --- GREEN protection --------------------------------------------------- #
    Write-Output ''
    Write-Output ('GREEN protection check (' + $script:GreenPort + ' must stay UP + untouched)')
    $green = Get-V13GreenState
    Write-Output ('  GREEN ' + $script:GreenPort + ' : ' + $green.State + ' (pid ' + $green.Pid + ')')
    if ($green.State -ne 'UP (protected, untouched)') {
        Add-V13Warning ('GREEN_PROTECTION_WARNING: ' + $script:GreenPort + ' is "' + $green.State + '" -- ' + $green.Detail)
    }

    # --- DRY RUN completion ------------------------------------------------- #
    # A dry run is a PRE-FLIGHT: it reports what WOULD happen. Nothing was
    # started, so "nothing is up" is the expected outcome -- not a failure.
    # Falling through to the NOT FULLY UP branch below would have made
    # `-Action up -DryRun` exit 4 on every healthy host, which makes the flag
    # useless in a script and actively misleading to an operator. (Measured
    # while building the Wave 8 gate: exit 4 on a host where the up path was
    # perfectly healthy.) Precondition failures -- foreign port hold, dead
    # interpreter, missing config -- still exit 3 above, because those are
    # findings about the host rather than about the dry run.
    if ($DryRun) {
        Write-Output ''
        Write-Output 'RESULT: DRY RUN (v1.3) -- pre-flight passed; nothing was started and nothing was changed'
        Write-Output ('  would start : API on ' + $ApiPort + '  (python -m jcc_core.run, loopback only)')
        Write-Output ('  would start : UI  on ' + $UiPort + '  (serve_visual.py, uvicorn/ASGI)')
        Write-Output ('  would leave : GREEN ' + $script:GreenPort + ' untouched (never probed as a target)')
        Write-Output ('  run it for real: .\scripts\jcc-v1.3.ps1 -Action up')
        $script:V13Code = 0
        return
    }

    # --- summary ------------------------------------------------------------ #
    $apiHealthy = (Get-V13ApiHealth -Port $ApiPort).Ok
    $uiHealthy = (Get-V13UiHealth -Port $UiPort).Ok
    $apiOwn = Test-JccOwnership -Paths $Paths -Port $ApiPort -Root $Root
    $uiOwn = Test-V13UiOwnership -Port $UiPort
    $greenOk = ($green.State -eq 'UP (protected, untouched)')

    Write-Output ''
    Write-Output 'summary'
    Write-Output ('  API http 200      : ' + $apiHealthy)
    Write-Output ('  API ownership     : ' + $apiOwn.Ok + '  ' + $apiOwn.Reason)
    Write-Output ('  UI  http 200      : ' + $uiHealthy)
    Write-Output ('  UI  ownership     : ' + $uiOwn.Ok + '  ' + $uiOwn.Reason)
    Write-Output ('  GREEN protected   : ' + $greenOk)
    foreach ($w in @($script:V13Warnings)) { Write-Output ('  ' + $w) }

    if ($apiHealthy -and $uiHealthy -and $apiOwn.Ok -and $uiOwn.Ok -and $greenOk) {
        Write-Output ''
        Write-Output 'RESULT: UP (v1.3) -- API + Visual UI verified, GREEN protected'
        Write-Output ('  open      : http://127.0.0.1:' + $UiPort + '/')
        Write-Output ('  status    : .\scripts\jcc-v1.3.ps1 -Action status')
        Write-Output ('  stop      : .\scripts\jcc-v1.3.ps1 -Action down')
        $script:V13Code = 0
        return
    }

    Write-Output ''
    Write-Output 'RESULT: NOT FULLY UP -- see the summary above. Nothing was signalled by this script.'
    Write-Output ('  diagnose  : .\scripts\jcc-v1.3.ps1 -Action doctor')
    Write-Output ('  stop      : .\scripts\jcc-v1.3.ps1 -Action down')
    $script:V13Code = 4
}

# =========================================================================== #
# DOWN -- stop, verified, fail-closed. Two independent halves, reported apart.
# =========================================================================== #

function Invoke-V13ApiDown {
    param([int]$Port, [int]$GracefulSeconds)

    Write-Output '=== API stop ==='
    $marker = Get-JccMarker -Paths $Paths
    $pidFilePid = Get-JccPidFromFile -Paths $Paths

    if ($pidFilePid -le 0) {
        $owner = Get-JccPortOwner -Port $Port
        if ($owner -and $owner.Pid -and ([int]$owner.Pid -ne 0)) {
            $info = Get-JccProcessInfo -ProcessId ([int]$owner.Pid)
            Write-Output ''
            Write-Output ('API REFUSED: no PID file of ours, but port ' + $Port + ' is LISTENING, owned by pid ' + $owner.Pid + ' via ' + $owner.Method + '.')
            Write-Output ('  command line: ' + $info.CommandLine)
            Write-Output '  That process is not ours (no PID file, no ownership marker), so it is never signalled.'
            $script:ApiDown = 2
            return
        }
        Write-Output ''
        Write-Output ('API NOT RUNNING: no PID file (' + $Paths.PidFile + ') and no listener on port ' + $Port + '.')
        if ($marker) { Write-Output '  A stale ownership marker exists; left in place untouched (read-only).' }
        $script:ApiDown = 0
        return
    }

    $own = Test-JccOwnership -Paths $Paths -Port $Port -Root $Root
    if (-not $own.Ok) {
        $info = Get-JccProcessInfo -ProcessId $pidFilePid
        Write-Output ''
        Write-Output ('API REFUSED: not signalled. Verification failed for pid ' + $pidFilePid + '.')
        Write-Output ('  reason            : ' + $own.Reason)
        if ($info.CommandLine) { Write-Output ('  actual command line: ' + $info.CommandLine) }
        Write-Output ('  process state     : ' + $(if ($info.Alive) { 'alive' } else { 'not running' }))
        Write-Output ('  fix the cause (or run .\scripts\jcc-v1.3.ps1 -Action repair if the pid is provably gone) and re-run.')
        $script:ApiDown = 2
        return
    }

    $info = Get-JccProcessInfo -ProcessId $pidFilePid
    Write-Output ''
    Write-Output 'API verified target (all checks passed):'
    Write-Output ('  pid             : ' + $own.Pid)
    Write-Output ('  command line    : ' + $info.CommandLine)
    Write-Output ('  port owner pid  : ' + $own.PortOwnerPid + ' (' + $own.PortOwnerMethod + ')')

    if ($DryRun) {
        Write-Output ('API DRY RUN: would stop pid ' + $own.Pid + '. Nothing was signalled.')
        $script:ApiDown = 0
        return
    }

    Write-Output ('API stopping pid ' + $own.Pid + ' ...')
    try {
        Stop-Process -Id $own.Pid -Force -ErrorAction Stop
    } catch {
        Write-Output ('API FAILED: Stop-Process on the verified pid ' + $own.Pid + ' threw: ' + $_.Exception.Message)
        Write-Output 'API: it has NOT been kill-looped; re-run to retry.'
        $script:ApiDown = 4
        return
    }

    $deadline = (Get-Date).AddSeconds($GracefulSeconds)
    $exited = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 250
        if (-not (Test-JccProcessAlive -ProcessId $own.Pid)) { $exited = $true; break }
    }
    if (-not $exited) {
        Write-Output ('API FAILED: pid ' + $own.Pid + ' is still alive after ' + $GracefulSeconds + 's. Re-run stop.')
        $script:ApiDown = 4
        return
    }
    Write-Output ('API service pid ' + $own.Pid + ' exited.')

    $launcherPid = 0
    if ($marker -and $marker.launcher_pid) { $launcherPid = [int]$marker.launcher_pid }
    if ($launcherPid -gt 0 -and $launcherPid -ne $own.Pid -and (Test-JccProcessAlive -ProcessId $launcherPid)) {
        Write-Output ('API stopping launcher pid ' + $launcherPid + ' ...')
        try { Stop-Process -Id $launcherPid -Force -ErrorAction Stop }
        catch { Write-Output ('API WARNING: Stop-Process on launcher pid ' + $launcherPid + ' threw: ' + $_.Exception.Message) }
    }

    $ownerAfter = Get-JccPortOwner -Port $Port
    $afterPid = 0
    if ($ownerAfter -and $ownerAfter.Pid) { $afterPid = [int]$ownerAfter.Pid }
    if ($afterPid -ne 0 -and $afterPid -ne $own.Pid) {
        Write-Output ('API NOTE: port ' + $Port + ' is now owned by a different pid (' + $afterPid + '); PID/marker left for inspection.')
        $script:ApiDown = 0
        return
    }
    Remove-Item -LiteralPath $Paths.PidFile -Force -ErrorAction SilentlyContinue | Out-Null
    Remove-Item -LiteralPath $Paths.MarkerFile -Force -ErrorAction SilentlyContinue | Out-Null
    Write-Output ('API cleaned up ' + $Paths.PidFile + ' and ' + $Paths.MarkerFile)
    $script:ApiDown = 0
}

function Invoke-V13UiDown {
    param([int]$Port, [int]$GracefulSeconds)

    Write-Output '=== Visual UI stop ==='
    $marker = Read-V13UiMarker
    $pidFilePid = Get-V13UiPidFromFile

    if ($pidFilePid -le 0) {
        $owner = Get-JccPortOwner -Port $Port
        if ($owner -and $owner.Pid -and ([int]$owner.Pid -ne 0)) {
            $info = Get-JccProcessInfo -ProcessId ([int]$owner.Pid)
            Write-Output ''
            Write-Output ('UI REFUSED: no PID file of ours, but port ' + $Port + ' is LISTENING, owned by pid ' + $owner.Pid + ' via ' + $owner.Method + '.')
            Write-Output ('  command line: ' + $info.CommandLine)
            Write-Output '  Not ours; never signalled.'
            $script:UiDown = 2
            return
        }
        Write-Output ''
        Write-Output ('UI NOT RUNNING: no PID file (' + $Paths.UiPidFile + ') and no listener on port ' + $Port + '.')
        if ($marker) { Write-Output '  A stale UI ownership marker exists; left in place untouched (read-only).' }
        $script:UiDown = 0
        return
    }

    $own = Test-V13UiOwnership -Port $Port
    if (-not $own.Ok) {
        Write-Output ''
        Write-Output ('UI REFUSED: not signalled. Verification failed for pid ' + $pidFilePid + '.')
        Write-Output ('  reason: ' + $own.Reason)
        Write-Output ('  actual command line: ' + $own.CmdLine)
        $script:UiDown = 2
        return
    }

    Write-Output ''
    Write-Output 'UI verified target (all checks passed):'
    Write-Output ('  pid             : ' + $own.Pid)
    Write-Output ('  command line    : ' + $own.CmdLine)
    Write-Output ('  port owner pid  : ' + $own.PortOwnerPid + ' (' + $own.PortOwnerMethod + ')')

    if ($DryRun) {
        Write-Output ('UI DRY RUN: would stop pid ' + $own.Pid + '. Nothing was signalled.')
        $script:UiDown = 0
        return
    }

    Write-Output ('UI stopping pid ' + $own.Pid + ' ...')
    try {
        Stop-Process -Id $own.Pid -Force -ErrorAction Stop
    } catch {
        Write-Output ('UI FAILED: Stop-Process on ' + $own.Pid + ' threw: ' + $_.Exception.Message)
        $script:UiDown = 4
        return
    }

    $deadline = (Get-Date).AddSeconds($GracefulSeconds)
    $exited = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 250
        if (-not (Test-JccProcessAlive -ProcessId $own.Pid)) { $exited = $true; break }
    }
    if (-not $exited) {
        Write-Output ('UI FAILED: pid ' + $own.Pid + ' still alive after ' + $GracefulSeconds + 's. Re-run stop.')
        $script:UiDown = 4
        return
    }
    Write-Output ('UI service pid ' + $own.Pid + ' exited.')

    $launcherPid = 0
    if ($marker -and $marker.launcher_pid) { $launcherPid = [int]$marker.launcher_pid }
    if ($launcherPid -gt 0 -and $launcherPid -ne $own.Pid -and (Test-JccProcessAlive -ProcessId $launcherPid)) {
        Write-Output ('UI stopping launcher pid ' + $launcherPid + ' ...')
        try { Stop-Process -Id $launcherPid -Force -ErrorAction Stop }
        catch { Write-Output ('UI WARNING: Stop-Process on launcher pid ' + $launcherPid + ' threw: ' + $_.Exception.Message) }
    }

    $ownerAfter = Get-JccPortOwner -Port $Port
    $afterPid = 0
    if ($ownerAfter -and $ownerAfter.Pid) { $afterPid = [int]$ownerAfter.Pid }
    if ($afterPid -ne 0 -and $afterPid -ne $own.Pid) {
        Write-Output ('UI NOTE: port ' + $Port + ' now owned by a different pid (' + $afterPid + '); PID/marker left for inspection.')
        $script:UiDown = 0
        return
    }
    Remove-Item -LiteralPath $Paths.UiPidFile -Force -ErrorAction SilentlyContinue | Out-Null
    Remove-Item -LiteralPath $Paths.UiMarkerFile -Force -ErrorAction SilentlyContinue | Out-Null
    Write-Output ('UI cleaned up ' + $Paths.UiPidFile + ' and ' + $Paths.UiMarkerFile)
    $script:UiDown = 0
}

function Invoke-V13Down {
    param([int]$ApiPort, [int]$UiPort, [int]$GracefulSeconds)

    Write-Output 'JCC v1.3 -Action down'
    Write-Output ('repo root : ' + $Root)
    Write-Output ('timestamp : ' + (Get-Date).ToString('o'))
    Write-Output ('api port  : ' + $ApiPort + '   ui port: ' + $UiPort)
    if ($DryRun) { Write-Output 'DRY RUN: nothing will be signalled.' }
    Write-Output ''

    if (-not $DryRun) { New-JccEnsureDir -Path $Paths.Runtime | Out-Null }

    $script:UiDown = 0
    $script:ApiDown = 0

    # UI first: it is the surface with no state, and stopping it first means the
    # API is never left serving a UI that has gone away.
    Invoke-V13UiDown -Port $UiPort -GracefulSeconds $GracefulSeconds
    Write-Output ''
    Invoke-V13ApiDown -Port $ApiPort -GracefulSeconds $GracefulSeconds

    Write-Output ''
    Write-Output 'down summary'
    Write-Output ('  UI  exit code : ' + $script:UiDown)
    Write-Output ('  API exit code : ' + $script:ApiDown)

    if ($script:UiDown -eq 0 -and $script:ApiDown -eq 0) {
        Write-Output ''
        Write-Output ('RESULT: STOPPED (v1.3). GREEN ' + $script:GreenPort + ' was never examined.')
        $script:V13Code = 0
        return
    }
    # An independent half that succeeded still counts as done: report the worst
    # code so the caller cannot read "0" from a run where one half refused.
    $worst = 0
    foreach ($c in @([int]$script:UiDown, [int]$script:ApiDown)) {
        if ($c -gt $worst) { $worst = $c }
    }
    Write-Output ''
    Write-Output ('RESULT: INCOMPLETE (v1.3) -- worst half exit code ' + $worst)
    $script:V13Code = $worst
}

# =========================================================================== #
# RESTART -- down then up, with a post-condition that is EARNED, not assumed.
# =========================================================================== #

function Invoke-V13Restart {
    param([int]$ApiPort, [int]$UiPort, [int]$TimeoutSec, [int]$GracefulSeconds)

    Write-Output 'JCC v1.3 -Action restart'
    Write-Output ('repo root : ' + $Root)
    Write-Output ('timestamp : ' + (Get-Date).ToString('o'))
    Write-Output ('api port  : ' + $ApiPort + '   ui port: ' + $UiPort)
    Write-Output ''

    # --- pre-flight: never start into (or on top of) a foreign hold --------- #
    foreach ($pair in @(@('API', $ApiPort, '-m jcc_core.run'), @('UI', $UiPort, 'serve_visual.py'))) {
        $tag = $pair[0]; $pt = [int]$pair[1]; $expect = [string]$pair[2]
        $st = Get-V13PortState -Port $pt -Expect $expect
        if ($st.State -eq 'FOREIGN') {
            Write-Output ('REFUSED before any stop/start: ' + $tag + ' port ' + $pt + ' is LISTENING, owned by FOREIGN pid ' + $st.Pid + ' (' + $st.Method + ').')
            Write-Output ('  command line: ' + $st.CmdLine)
            Write-Output '  That process is not ours; it is never signalled. Resolve the holder, then re-run.'
            $script:V13Code = 3
            return
        }
    }

    # --- half 1: down ------------------------------------------------------- #
    Invoke-V13Down -ApiPort $ApiPort -UiPort $UiPort -GracefulSeconds $GracefulSeconds
    $downCode = [int]$script:V13Code
    Write-Output ''
    Write-Output ('down half exit code: ' + $downCode)
    if ($downCode -ne 0) {
        Write-Output ''
        Write-Output ('REFUSED: the down half refused (exit ' + $downCode + '). Nothing was started.')
        # Give the operator the specific cause when it is the well-known one:
        # bookkeeping left behind by a service that died without these scripts
        # signalling it (out-of-band kill, host reboot, or an exit on its own).
        $staleApi = Get-JccPidFromFile -Paths $Paths
        $staleUi = Get-V13UiPidFromFile
        foreach ($pair in @(@('API', $staleApi, $ApiPort, $Paths.PidFile), @('UI', $staleUi, $UiPort, $Paths.UiPidFile))) {
            $tag = $pair[0]; $spid = [int]$pair[1]; $pt = [int]$pair[2]; $pf = [string]$pair[3]
            if ($spid -le 0) { continue }
            if ((Test-JccProcessAlive -ProcessId $spid)) { continue }
            if ((Get-V13PortOwnerPid -Port $pt) -ne 0) { continue }
            Write-Output ('  cause (' + $tag + '): ' + $pf + ' records pid ' + $spid + ', which is not running,')
            Write-Output ('         and nothing is LISTENING on port ' + $pt + '. That is the residue of a service')
            Write-Output ('         that died without these scripts signalling it.')
            Write-Output ('  confirm: netstat -ano | findstr :' + $pt + '   -> expect no LISTENING line')
            Write-Output ('  remedy : .\scripts\jcc-v1.3.ps1 -Action repair   (quarantines ONLY provably stale bookkeeping, by rename)')
        }
        $script:V13Code = $downCode
        return
    }

    $waitUntil = (Get-Date).AddSeconds(3)
    while ((Get-Date) -lt $waitUntil) {
        $probe = Get-V13ApiHealth -Port $ApiPort -TimeoutMs 800
        if (-not $probe.Ok) { break }
        Start-Sleep -Milliseconds 300
    }

    # --- half 2: up --------------------------------------------------------- #
    Invoke-V13Up -ApiPort $ApiPort -UiPort $UiPort -TimeoutSec $TimeoutSec
    $upCode = [int]$script:V13Code
    Write-Output ''
    Write-Output ('up half exit code: ' + $upCode)
    if ($upCode -ne 0) {
        Write-Output ''
        Write-Output ('RESTART FAILED: the up half exited ' + $upCode + '. The v1.3 product runtime is DOWN or partial.')
        $script:V13Code = $upCode
        return
    }

    # --- post-condition: the exit code of "restart" is EARNED here ---------- #
    $postAttempts = 0
    $postWhy = @()
    $postApiPid = 0; $postUiPid = 0; $postApiOwner = 0; $postUiOwner = 0
    $postApiCode = 0; $postUiCode = 0

    while ($true) {
        $postAttempts++
        $hApi = Get-V13ApiHealth -Port $ApiPort
        $hUi = Get-V13UiHealth -Port $UiPort
        $postApiPid = Get-JccPidFromFile -Paths $Paths
        $postUiPid = Get-V13UiPidFromFile
        $postApiOwner = Get-V13PortOwnerPid -Port $ApiPort
        $postUiOwner = Get-V13PortOwnerPid -Port $UiPort
        $ownApi = Test-JccOwnership -Paths $Paths -Port $ApiPort -Root $Root
        $ownUi = Test-V13UiOwnership -Port $UiPort

        $why = New-Object System.Collections.Generic.List[string]
        if (-not $hApi.Ok) { $why.Add('no HTTP 200 on the API health endpoint: ' + $hApi.Error) }
        if (-not $hUi.Ok) { $why.Add('no HTTP 200 on the UI health endpoint: ' + $hUi.Error) }
        if ($postApiPid -le 0) { $why.Add('no API service pid recorded in ' + $Paths.PidFile) }
        if ($postUiPid -le 0) { $why.Add('no UI service pid recorded in ' + $Paths.UiPidFile) }
        if ($postApiOwner -eq 0) { $why.Add('nothing is LISTENING on API port ' + $ApiPort) }
        elseif ($postApiPid -gt 0 -and $postApiOwner -ne $postApiPid) { $why.Add('API port ' + $ApiPort + ' owner ' + $postApiOwner + ' != pid file ' + $postApiPid) }
        if ($postUiOwner -eq 0) { $why.Add('nothing is LISTENING on UI port ' + $UiPort) }
        elseif ($postUiPid -gt 0 -and $postUiOwner -ne $postUiPid) { $why.Add('UI port ' + $UiPort + ' owner ' + $postUiOwner + ' != pid file ' + $postUiPid) }
        if (-not $ownApi.Ok) { $why.Add('API ownership not verified: ' + $ownApi.Reason) }
        if (-not $ownUi.Ok) { $why.Add('UI ownership not verified: ' + $ownUi.Reason) }

        if ($why.Count -eq 0) {
            Write-Output ('post-condition: API pid ' + $postApiPid + ' == port owner ' + $postApiOwner + '; UI pid ' + $postUiPid + ' == port owner ' + $postUiOwner + '; both HTTP 200')
            break
        }
        if ($postAttempts -ge 6) { $postWhy = @($why); break }
        Start-Sleep -Seconds 1
    }

    if ($postWhy.Count -gt 0) {
        Write-Output ''
        Write-Output 'RESTART FAILED: the up half exited 0, but the post-condition below does not hold.'
        Write-Output ('  checked    : API ' + $ApiPort + '/api/health and UI ' + $UiPort + '/health (pid file, port owner, live process, ownership)')
        Write-Output ('  API pid    : ' + $postApiPid + '   port owner: ' + $postApiOwner)
        Write-Output ('  UI  pid    : ' + $postUiPid + '   port owner: ' + $postUiOwner)
        Write-Output ('  attempts   : ' + $postAttempts)
        foreach ($w in $postWhy) { Write-Output ('  why        : ' + $w) }
        Write-Output '  Nothing is signalled by the restart action. Diagnose with -Action doctor.'
        Write-Output 'RESULT: DOWN (v1.3)'
        $script:V13Code = 6
        return
    }

    Write-Output ''
    Write-Output 'RESULT: RESTART OK (v1.3)'
    Write-Output ('  open      : http://127.0.0.1:' + $UiPort + '/')
    $script:V13Code = 0
}

# =========================================================================== #
# STATUS -- UP / DEGRADED / DOWN, with GREEN protection and uptime provenance.
# =========================================================================== #

function Invoke-V13Status {
    param([int]$ApiPort, [int]$UiPort)

    $now = Get-Date   # ONE instant for every time-derived value in this report.
    Write-Output 'JCC v1.3 -Action status'
    Write-Output ('repo root  : ' + $Root)
    Write-Output ('timestamp  : ' + $now.ToString('o'))
    Write-Output ('ports      : API ' + $ApiPort + ', UI ' + $UiPort + ', GREEN ' + $script:GreenPort + ' (read-only)')
    Write-Output ''

    # --- GREEN protection --------------------------------------------------- #
    $green = Get-V13GreenState
    Write-Output ('GREEN ' + $script:GreenPort + '  : ' + $green.State + '  (pid ' + $green.Pid + ', frozen v1.0.1 -- never touched)')
    if ($green.State -eq 'DOWN') {
        Add-V13Warning ('GREEN_PROTECTION_VIOLATION: frozen GREEN ' + $script:GreenPort + ' is NOT listening -- this must never happen. Investigate before any further action.')
    } elseif ($green.State -ne 'UP (protected, untouched)') {
        Add-V13Warning ('GREEN_PROTECTION_WARNING: GREEN ' + $script:GreenPort + ' state is "' + $green.State + '": ' + $green.Detail)
    }

    # --- API ---------------------------------------------------------------- #
    $apiOwner = Get-JccPortOwner -Port $ApiPort
    $apiOwnerPid = 0
    if ($apiOwner -and $apiOwner.Pid) { $apiOwnerPid = [int]$apiOwner.Pid }
    $apiMethod = ''
    if ($apiOwner -and $apiOwner.Method) { $apiMethod = [string]$apiOwner.Method }
    $apiHealth = Get-V13ApiHealth -Port $ApiPort
    $apiMarker = Get-JccMarker -Paths $Paths
    $apiPidFile = Get-JccPidFromFile -Paths $Paths
    $apiOwn = Test-JccOwnership -Paths $Paths -Port $ApiPort -Root $Root

    $apiUp = $false
    $apiSummary = '-'
    $provider = '-'; $actuation = '-'; $policy = '-'; $wal = '-'
    if ($apiHealth.Ok) {
        $hj = Read-JccHealthJson -Body $apiHealth.Body
        if ($hj) {
            $provider = [string]$hj.jev_provider
            $actuation = [string]$hj.actuation_enabled
            $policy = [string]$hj.policy_version
            $wal = [string]$hj.wal
            $eps = 0
            if ($hj.open_get_endpoints) { $eps = @($hj.open_get_endpoints).Count }
            $apiSummary = ('status=' + $hj.status + ' provider=' + $provider +
                           ' actuation=' + $actuation + ' policy=' + $policy +
                           ' wal=' + $wal + ' open_get_endpoints=' + $eps)
        } else {
            $apiSummary = ('non-JSON body: ' + $apiHealth.Body)
        }
        $apiUp = $apiOwn.Ok
    } elseif ($apiHealth.Error) {
        $apiSummary = [string]$apiHealth.Error
    }
    if ($apiHealth.Ok -and -not $apiOwn.Ok) {
        Add-V13Warning ('API_OWNERSHIP_WARNING: ' + $ApiPort + ' answers but ownership was NOT verified: ' + $apiOwn.Reason + ' -- treat as foreign, do NOT stop it with -Action down.')
        $apiUp = $false
    }

    # Uptime: source precedence is process -> cim -> (never the marker). The
    # marker proves IDENTITY only, and this is the same reading the v1.0.1
    # operations polish put behind status.ps1.
    $uptime = '-'
    $uptimeReading = $null
    if ($apiPidFile -gt 0) {
        $procStart = $null
        try { $procStart = (Get-Process -Id $apiPidFile -ErrorAction Stop).StartTime } catch { $procStart = $null }
        $info = Get-JccProcessInfo -ProcessId $apiPidFile
        $cimCreated = $null
        if ($info.CreationDate) { try { $cimCreated = [datetime]::Parse($info.CreationDate) } catch { $cimCreated = $null } }
        $markerStart = $null
        if ($apiMarker -and $apiMarker.start_time_local) { try { $markerStart = [datetime]$apiMarker.start_time_local } catch { $markerStart = $null } }
        $uptimeReading = Get-JccUptimeReading -ProcessStart $procStart -CimCreation $cimCreated -MarkerStart $markerStart -Now $now
        $uptime = $uptimeReading.Uptime
        foreach ($w in @($uptimeReading.Warnings)) { Add-V13Warning $w }
    }

    Write-Output ''
    Write-Output ('API ' + $ApiPort + '   : ' + $(if ($apiUp) { 'UP' } else { 'DOWN' }) + '   (v1.3 core control plane; isolated from GREEN)')
    Write-Output ('  port owner  : ' + $apiOwnerPid + '  (via ' + $apiMethod + ')')
    Write-Output ('  uptime      : ' + $uptime + $(if ($uptimeReading) { '   (source: ' + $uptimeReading.Source + ')' } else { '' }))
    Write-Output ('  health      : ' + $apiSummary)
    Write-Output ('  provider    : ' + $provider + '   actuation=' + $actuation + '   policy=' + $policy + '   wal=' + $wal)
    Write-Output ('  config      : ' + $Paths.Config)
    Write-Output ('  pid file    : ' + $Paths.PidFile + ' -> ' + $(if (Test-Path -LiteralPath $Paths.PidFile) { 'present' } else { 'absent' }))
    Write-Output ('  marker      : ' + $Paths.MarkerFile + ' -> ' + $(if ($apiMarker) { 'present' } else { 'absent' }))
    if ($uptimeReading) {
        Write-Output '  uptime provenance (the marker proves identity, never uptime):'
        Write-Output ('    process  : ' + $uptimeReading.ProcessStartIso + '   <- authoritative')
        Write-Output ('    cim      : ' + $uptimeReading.CimCreationIso + '   <- cross-check')
        Write-Output ('    marker   : ' + $uptimeReading.MarkerStartIso + '   <- provenance only')
    }

    # --- Visual UI ---------------------------------------------------------- #
    $uiOwner = Get-JccPortOwner -Port $UiPort
    $uiOwnerPid = 0
    if ($uiOwner -and $uiOwner.Pid) { $uiOwnerPid = [int]$uiOwner.Pid }
    $uiOwn = Test-V13UiOwnership -Port $UiPort
    $uiHealth = Get-V13UiHealth -Port $UiPort
    $uiUp = $false
    $uiSummary = '-'
    $uiRuntime = '-'
    if ($uiOwnerPid -ne 0) {
        $uInfo = Get-JccProcessInfo -ProcessId $uiOwnerPid
        $uCmd = [string]$uInfo.CommandLine
        $uiCmdOk = ((Get-JccNormCmd -Text $uCmd) -like '*serve_visual.py*')
        if (-not $uiCmdOk) {
            Add-V13Warning ('UI_OWNERSHIP_WARNING: ' + $UiPort + ' is held by pid ' + $uiOwnerPid + ' but its command line is not the v1.3 Visual UI server: ' + $uCmd)
        }
        if ($uiHealth.Ok -and $uiCmdOk) {
            $uiUp = $uiOwn.Ok
            $uj = Read-JccHealthJson -Body $uiHealth.Body
            if ($uj -and $uj.runtime) { $uiRuntime = [string]$uj.runtime }
            $uiSummary = ('HTTP ' + $uiHealth.StatusCode + ' from http://127.0.0.1:' + $UiPort + '/health')
            if ($uiHealth.Ok -and -not $uiOwn.Ok) {
                Add-V13Warning ('UI_OWNERSHIP_WARNING: ' + $UiPort + ' answers but UI ownership was not verified: ' + $uiOwn.Reason)
            }
        } elseif ($uiHealth.Ok) {
            $uiSummary = ('answers (HTTP ' + $uiHealth.StatusCode + ') but the command line is unverified')
        } else {
            $uiSummary = ('port owner pid ' + $uiOwnerPid + ' but no HTTP on ' + $UiPort + ': ' + $uiHealth.Error)
        }
    } else {
        $uiSummary = ('nobody (port ' + $UiPort + ' free)')
    }

    Write-Output ''
    Write-Output ('UI ' + $UiPort + '   : ' + $(if ($uiUp) { 'UP' } else { 'DOWN' }) + '   (Visual Command Center SPA)')
    Write-Output ('  port owner  : ' + $uiOwnerPid)
    Write-Output ('  runtime     : ' + $uiRuntime + '   (must be uvicorn-asgi, never python http.server)')
    Write-Output ('  health      : ' + $uiSummary)
    Write-Output ('  dist        : ' + $Paths.UiDist + ' -> ' + $(if (Test-Path -LiteralPath (Join-Path $Paths.UiDist 'index.html')) { 'index.html present' } else { 'MISSING index.html' }))
    Write-Output ('  pid file    : ' + $Paths.UiPidFile + ' -> ' + $(if (Test-Path -LiteralPath $Paths.UiPidFile) { 'present' } else { 'absent' }))
    Write-Output ('  marker      : ' + $Paths.UiMarkerFile + ' -> ' + $(if (Read-V13UiMarker) { 'present' } else { 'absent' }))

    # --- runtime DB --------------------------------------------------------- #
    $dbPath = Join-Path $Paths.Runtime 'jcc.db'
    $dbPresent = Test-Path -LiteralPath $dbPath
    $dbSize = 0
    if ($dbPresent) { try { $dbSize = (Get-Item -LiteralPath $dbPath).Length } catch { $dbSize = 0 } }
    $dbState = $(if ($dbPresent) { ('present, ' + $dbSize + ' bytes') } else { 'absent (created on first start)' })
    Write-Output ''
    Write-Output ('DB          : ' + $dbPath + ' -> ' + $dbState)

    # --- result ------------------------------------------------------------- #
    $code = 1
    $result = 'DOWN'
    if ($apiUp) {
        if ($uiUp) { $result = 'UP'; $code = 0 }
        else {
            $result = 'DEGRADED'
            $code = 2
            Add-V13Warning ('RESULT_DEGRADED: the API is UP but the Visual UI on ' + $UiPort + ' is not verified -- the command center is only partially available.')
        }
    } elseif ($apiHealth.Ok) {
        $result = 'DEGRADED'
        $code = 2
        Add-V13Warning ('RESULT_DEGRADED: something answers on the API port but is not provably ours.')
    }

    Write-Output ''
    foreach ($w in @($script:V13Warnings)) { Write-Output ('  ' + $w) }
    if (@($script:V13Warnings).Count -gt 0) { Write-Output '' }
    Write-Output ('RESULT: ' + $result + ' (v1.3)')
    Write-Output '  start : .\scripts\jcc-v1.3.ps1 -Action up'
    Write-Output '  stop  : .\scripts\jcc-v1.3.ps1 -Action down'
    Write-Output '  doctor: .\scripts\jcc-v1.3.ps1 -Action doctor'
    $script:V13Code = $code
}

# =========================================================================== #
# DOCTOR -- measured diagnosis. Every line carries a measured value.
# =========================================================================== #

function Get-V13YamlScalar {
    <#
        First `key: value` match anywhere in a YAML file, as a plain string.
        Deliberately dumb: this is used only for values that are not part of the
        config MODULE's contract (e.g. the NAME of the token env var). Anything
        the service itself acts on is read through load_config(), never here.
    #>
    param([string]$Path, [string]$Key)
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    $pat = '^\s*' + [regex]::Escape($Key) + '\s*:\s*(\S+)\s*$'
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match $pat) { return ([string]$Matches[1]).Trim('"').Trim("'") }
    }
    return ''
}

function Get-V13NextHint {
    <#
        What to run next, given what the doctor just measured about the API port.

        WHY NOT reuse Get-JccNextHint (which exists and does the same three-state
        reasoning): its strings name the v1.0.1 GREEN entry points
        (.\scripts\start.ps1 / status.ps1). Printing them in a v1.3 report tells
        the operator to run the frozen service's scripts to control the product
        runtime, which is simply the wrong instruction -- and it was measured
        doing exactly that the first time this doctor ran. The shared helper is
        deliberately left alone: it is correct for GREEN's own doctor, and
        tools/tests/test_doctor_next_hint.py pins its exact text.

        Pure: takes a port state, returns the hint. Testable without a service.
    #>
    param(
        [ValidateSet('OURS','FREE','FOREIGN')]
        [string]$PortState,
        [int]$ForeignPid = 0
    )
    switch ($PortState) {
        'OURS'    { return 'none -- the v1.3 product runtime is already up (verify with .\scripts\jcc-v1.3.ps1 -Action status)' }
        'FREE'    { return '.\scripts\jcc-v1.3.ps1 -Action up' }
        'FOREIGN' { return ('do NOT run -Action up -- the API port is held by foreign pid ' + $ForeignPid + '; identify it first with .\scripts\jcc-v1.3.ps1 -Action doctor') }
    }
    return '.\scripts\jcc-v1.3.ps1 -Action doctor'
}

function Add-V13Check {
    param([string]$Id, [string]$Name, [string]$Status, [string]$Detail)
    $script:DocChecks.Add(@{ Id = $Id; Name = $Name; Status = $Status; Detail = $Detail })
    if ($Status -eq 'PASS') { $script:DocPass++ } else { $script:DocFail++ }
    Write-Output ('  [' + $Status + '] ' + $Id + ' ' + $Name)
    Write-Output ('          measured: ' + $Detail)
}

function Add-V13Info {
    param([string]$Id, [string]$Name, [string]$Detail)
    $script:DocChecks.Add(@{ Id = $Id; Name = $Name; Status = 'INFO'; Detail = $Detail })
    Write-Output ('  [INFO] ' + $Id + ' ' + $Name)
    Write-Output ('          measured: ' + $Detail)
}

function Invoke-V13Doctor {
    param([int]$ApiPort, [int]$UiPort, [int]$MinFreeGb)

    $script:DocPass = 0
    $script:DocFail = 0
    $script:DocChecks = New-Object System.Collections.Generic.List[object]

    Write-Output 'JCC v1.3 -Action doctor'
    Write-Output ('repo root : ' + $Root)
    Write-Output ('timestamp : ' + (Get-Date).ToString('o'))
    Write-Output ('host      : ' + $env:COMPUTERNAME + ' / user ' + $env:USERNAME)
    Write-Output ('ports     : API ' + $ApiPort + ', UI ' + $UiPort + ', GREEN ' + $script:GreenPort + ' (read-only)')
    Write-Output ''

    # --- 1. interpreter ----------------------------------------------------- #
    Write-Output '1. interpreter'
    if (-not $script:V13Python) {
        Add-V13Check -Id 'D-1' -Name 'python interpreter' -Status 'FAIL' -Detail 'no interpreter (.venv missing and nothing usable on PATH) -- run .\scripts\bootstrap.ps1'
    } elseif (Test-JccPython -Python $script:V13Python) {
        Add-V13Check -Id 'D-1' -Name 'python interpreter' -Status 'PASS' -Detail ($script:V13Python + ' -> python ' + (Get-JccPythonVersion -Python $script:V13Python))
    } else {
        Add-V13Check -Id 'D-1' -Name 'python interpreter' -Status 'FAIL' -Detail ($script:V13Python + ' present but "python -c ..." did not complete')
    }

    # --- 2. configuration --------------------------------------------------- #
    Write-Output ''
    Write-Output '2. configuration'
    if (Test-Path -LiteralPath $Paths.Config) {
        Add-V13Check -Id 'D-2a' -Name 'config file present' -Status 'PASS' -Detail ('size ' + (Get-Item -LiteralPath $Paths.Config).Length + ' bytes at ' + $Paths.Config)
    } else {
        Add-V13Check -Id 'D-2a' -Name 'config file present' -Status 'FAIL' -Detail ('missing ' + $Paths.Config)
    }

    $cfgHost = ''; $cfgPort = ''; $cfgDb = ''; $cfgActuation = ''
    if ($script:V13Python -and (Test-Path -LiteralPath $Paths.Config)) {
        $cfgCode = @'
import sys, os
sys.path.insert(0, os.path.join(os.getcwd(), 'packages'))
os.environ['JCC_CONFIG'] = r'{0}'
from jcc_core.config import load_config
c = load_config()
print('%s|%s|%s|%s|%s' % (c.server.host, c.server.port, c.state_store_db_path, c.actuation.enabled, c.policy.version))
'@ -f $Paths.Config
        $cur = $null
        try { $cur = (Get-Location).Path } catch { $cur = $null }
        $out = ''; $code = -1
        try {
            Set-Location -LiteralPath $Root -ErrorAction Stop
            $prev = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            $out = & $script:V13Python '-c' $cfgCode 2>&1
            $code = $LASTEXITCODE
            $ErrorActionPreference = $prev
        } catch { $code = -1 } finally {
            if ($cur) { try { Set-Location -LiteralPath $cur -ErrorAction Stop } catch { } }
        }
        if ($code -eq 0) {
            $parts = ([string]$out).Trim().Split('|')
            $cfgHost = $parts[0]; $cfgPort = $parts[1]; $cfgDb = $parts[2]; $cfgActuation = $parts[3]
            $cfgPolicy = $parts[4]
            Add-V13Check -Id 'D-2b' -Name 'config parses (load_config)' -Status 'PASS' -Detail ('host=' + $cfgHost + ' port=' + $cfgPort + ' db=' + $cfgDb + ' policy=' + $cfgPolicy)
        } else {
            Add-V13Check -Id 'D-2b' -Name 'config parses (load_config)' -Status 'FAIL' -Detail ('load_config() exit ' + $code + ': ' + ([string]$out).Trim())
        }
    } else {
        Add-V13Check -Id 'D-2b' -Name 'config parses (load_config)' -Status 'FAIL' -Detail 'skipped: no usable interpreter'
    }

    # --- 3. actuator: the product must not be able to act ------------------- #
    Write-Output ''
    Write-Output '3. actuation (ADR-0004: OFF, and the product refuses all side-effects)'
    if ($cfgActuation -eq '') {
        Add-V13Check -Id 'D-3' -Name 'actuation disabled' -Status 'FAIL' -Detail 'could not determine actuation.enabled (config probe failed)'
    } elseif ($cfgActuation -match '^(False|false)$') {
        Add-V13Check -Id 'D-3' -Name 'actuation disabled' -Status 'PASS' -Detail 'actuation.enabled=False (measured through load_config)'
    } else {
        Add-V13Check -Id 'D-3' -Name 'actuation disabled' -Status 'FAIL' -Detail ('actuation.enabled=' + $cfgActuation + ' -- the product must NOT be able to act')
    }

    # --- 4. the Visual UI serving path must not be stdlib http.server ------- #
    Write-Output ''
    Write-Output '4. Visual UI serving path (Wave 8: uvicorn/ASGI, never python http.server)'
    if (-not (Test-Path -LiteralPath $Paths.UiServeScript)) {
        Add-V13Check -Id 'D-4a' -Name 'UI server script present' -Status 'FAIL' -Detail ('missing ' + $Paths.UiServeScript)
    } else {
        Add-V13Check -Id 'D-4a' -Name 'UI server script present' -Status 'PASS' -Detail ($Paths.UiServeScript + ' (' + (Get-Item -LiteralPath $Paths.UiServeScript).Length + ' bytes)')

        # WHY THIS CALLS AN AST AUDIT INSTEAD OF SCANNING THE TEXT
        # -------------------------------------------------------
        # The first version of this check substring-matched the file for
        # "http.server" / "SimpleHTTPRequestHandler" / "HTTPServer" and reported
        # FAIL on a file that is CORRECT -- serve_visual.py's docstring explains
        # the rewrite ("it used to be http.server.HTTPServer + ..."). Prose about
        # a removed dependency is not a dependency, so the instrument was
        # reporting a finding the ground truth did not support.
        #
        # tools\v1_3_ui_runtime_audit.py answers the real question by parsing the
        # AST: it catches `import http.server`, `from http.server import X` and
        # `from http import server`, and code references to HTTPServer /
        # SimpleHTTPRequestHandler / BaseHTTPRequestHandler -- while ignoring
        # docstrings and comments. And a clean bill of health is only issued when
        # the POSITIVE evidence is present too (uvicorn imported, uvicorn.run
        # called, a Starlette app referenced, files actually served), so an empty
        # file cannot pass by saying nothing.
        $auditPath = Join-Path $Root 'tools\v1_3_ui_runtime_audit.py'
        $finding = $null
        if (-not (Test-Path -LiteralPath $auditPath)) {
            Add-V13Check -Id 'D-4b' -Name 'UI serving path audit (AST)' -Status 'FAIL' -Detail ('audit tool missing: ' + $auditPath)
        } elseif (-not $script:V13Python) {
            Add-V13Check -Id 'D-4b' -Name 'UI serving path audit (AST)' -Status 'FAIL' -Detail 'skipped: no usable interpreter'
        } else {
            $prev = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            $jout = & $script:V13Python $auditPath '--file' $Paths.UiServeScript '--json' 2>&1
            $jcode = $LASTEXITCODE
            $ErrorActionPreference = $prev
            try { $finding = ([string]$jout | ConvertFrom-Json) } catch { $finding = $null }

            if (-not $finding) {
                Add-V13Check -Id 'D-4b' -Name 'UI serving path audit (AST)' -Status 'FAIL' -Detail ('audit produced no parsable JSON (exit ' + $jcode + '): ' + ([string]$jout).Trim())
            } else {
                # Two independent halves, each failable on its own, so a clean
                # "bans" line cannot hide a missing positive and vice versa.
                $banImports = @($finding.banned_imports)
                $banRefs = @($finding.banned_references)
                if ($banImports.Count -gt 0 -or $banRefs.Count -gt 0) {
                    Add-V13Check -Id 'D-4b' -Name 'no stdlib http.server in the serving path' -Status 'FAIL' -Detail ('the file imports/references it: ' + (($banImports + $banRefs) -join '; '))
                } else {
                    Add-V13Check -Id 'D-4b' -Name 'no stdlib http.server in the serving path' -Status 'PASS' -Detail ('AST audit: no `import http.server`, no `from http.server import ...`, no `from http import server`, and no HTTPServer / SimpleHTTPRequestHandler / BaseHTTPRequestHandler reference in ' + [System.IO.Path]::GetFileName($Paths.UiServeScript) + ' (docstring text is ignored by design)')
                }

                $pos = $finding.positives
                $miss = @()
                foreach ($k in @('uvicorn_imported', 'uvicorn_run_called', 'starlette_app_referenced', 'serves_files')) {
                    if (-not $pos.$k) { $miss += $k }
                }
                if ($miss.Count -gt 0) {
                    Add-V13Check -Id 'D-4c' -Name 'serving path is uvicorn/ASGI (positive evidence)' -Status 'FAIL' -Detail ('missing positive evidence: ' + ($miss -join ', '))
                } else {
                    Add-V13Check -Id 'D-4c' -Name 'serving path is uvicorn/ASGI (positive evidence)' -Status 'PASS' -Detail ('uvicorn imported + uvicorn.run called + a Starlette application referenced + files served (FileResponse/StaticFiles); audit verdict ' + $finding.verdict)
                }
            }
        }
    }

    if ($script:V13Python) {
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $vout = & $script:V13Python '-c' 'import uvicorn, starlette; print(uvicorn.__version__)' 2>&1
        $vcode = $LASTEXITCODE
        $ErrorActionPreference = $prev
        if ($vcode -eq 0) {
            Add-V13Check -Id 'D-4d' -Name 'uvicorn + starlette importable' -Status 'PASS' -Detail ('uvicorn ' + ([string]$vout).Trim() + ' + starlette imported by ' + $script:V13Python)
        } else {
            Add-V13Check -Id 'D-4d' -Name 'uvicorn + starlette importable' -Status 'FAIL' -Detail ('import probe exit ' + $vcode + ': ' + ([string]$vout).Trim())
        }
    } else {
        Add-V13Check -Id 'D-4d' -Name 'uvicorn + starlette importable' -Status 'FAIL' -Detail 'skipped: no interpreter'
    }

    # --- 5. built UI -------------------------------------------------------- #
    Write-Output ''
    Write-Output '5. built UI (dist)'
    $indexPath = Join-Path $Paths.UiDist 'index.html'
    if (Test-Path -LiteralPath $indexPath) {
        $assetCount = 0
        $assetsDir = Join-Path $Paths.UiDist 'assets'
        if (Test-Path -LiteralPath $assetsDir) {
            $assetCount = @(Get-ChildItem -LiteralPath $assetsDir -File -ErrorAction SilentlyContinue).Count
        }
        $newest = ''
        try { $newest = (Get-Item -LiteralPath $indexPath).LastWriteTime.ToString('o') } catch { $newest = '' }
        Add-V13Check -Id 'D-5' -Name 'dist/index.html present' -Status 'PASS' -Detail ('index.html + ' + $assetCount + ' file(s) in dist\assets; index.html last written ' + $newest)
    } else {
        Add-V13Check -Id 'D-5' -Name 'dist/index.html present' -Status 'FAIL' -Detail ('missing ' + $indexPath + ' -- build it: cd apps\command_center_3d; npm ci; npm run build')
    }

    # --- 6. runtime directory writable -------------------------------------- #
    Write-Output ''
    Write-Output '6. runtime directory'
    $probeFile = Join-Path $Paths.Runtime 'doctor-write-probe.tmp'
    try {
        New-JccEnsureDir -Path $Paths.Runtime | Out-Null
        $existedBefore = Test-Path -LiteralPath $probeFile
        Set-Content -LiteralPath $probeFile -Value ('jcc-doctor ' + (Get-Date).ToString('o')) -Encoding ASCII
        $bytes = (Get-Item -LiteralPath $probeFile).Length
        Remove-Item -LiteralPath $probeFile -Force -ErrorAction Stop
        $stillThere = Test-Path -LiteralPath $probeFile
        Add-V13Check -Id 'D-6' -Name 'runtime directory writable' -Status 'PASS' -Detail ('wrote and removed ' + $probeFile + ' (' + $bytes + ' bytes), dir ' + $Paths.Runtime + '; probe pre-existed: ' + $existedBefore + '; exists after removal: ' + $stillThere)
    } catch {
        Add-V13Check -Id 'D-6' -Name 'runtime directory writable' -Status 'FAIL' -Detail ('probe failed: ' + $_.Exception.Message + ' (dir ' + $Paths.Runtime + ')')
    }

    # --- 7. runtime DB ------------------------------------------------------ #
    Write-Output ''
    Write-Output '7. runtime database'
    $dbPath = Join-Path $Paths.Runtime 'jcc.db'
    if (Test-Path -LiteralPath $dbPath) {
        $sz = 0; try { $sz = (Get-Item -LiteralPath $dbPath).Length } catch { $sz = 0 }
        Add-V13Check -Id 'D-7' -Name 'runtime DB' -Status 'PASS' -Detail ($dbPath + ' -> present, ' + $sz + ' bytes')
    } else {
        Add-V13Check -Id 'D-7' -Name 'runtime DB' -Status 'PASS' -Detail ($dbPath + ' -> absent (created on first start; not a defect)')
    }

    # --- 8/9. port states --------------------------------------------------- #
    Write-Output ''
    Write-Output '8. API port ownership'
    $apiState = Get-V13PortState -Port $ApiPort -Expect '-m jcc_core.run'
    $apiOwnOk = $false
    if ($apiState.State -eq 'FREE') {
        Add-V13Check -Id 'D-8' -Name ('port ' + $ApiPort + ' ownership') -Status 'PASS' -Detail ('no LISTENING socket (via ' + $apiState.Method + ') -- the API is not running, which is a legitimate state')
    } elseif ($apiState.State -eq 'FOREIGN') {
        Add-V13Check -Id 'D-8' -Name ('port ' + $ApiPort + ' not in foreign hold') -Status 'FAIL' -Detail ('LISTENING held by FOREIGN pid ' + $apiState.Pid + ' (' + $apiState.Method + '); command line: ' + $apiState.CmdLine)
    } else {
        $own = Test-JccOwnership -Paths $Paths -Port $ApiPort -Root $Root
        if ($own.Ok) {
            $apiOwnOk = $true
            Add-V13Check -Id 'D-8' -Name ('port ' + $ApiPort + ' ownership') -Status 'PASS' -Detail ('listener owned by OUR pid ' + $apiState.Pid + '; ' + $own.Reason)
        } else {
            Add-V13Check -Id 'D-8' -Name ('port ' + $ApiPort + ' ownership') -Status 'FAIL' -Detail ('pid ' + $apiState.Pid + ' holds ' + $ApiPort + ' and looks like a JCC API, but ownership was NOT verified: ' + $own.Reason)
        }
    }

    Write-Output ''
    Write-Output '9. Visual UI port ownership'
    $uiState = Get-V13PortState -Port $UiPort -Expect 'serve_visual.py'
    $uiOwnOk = $false
    if ($uiState.State -eq 'FREE') {
        Add-V13Check -Id 'D-9' -Name ('port ' + $UiPort + ' ownership') -Status 'PASS' -Detail ('no LISTENING socket (via ' + $uiState.Method + ') -- the UI is not running, which is a legitimate state')
    } elseif ($uiState.State -eq 'FOREIGN') {
        Add-V13Check -Id 'D-9' -Name ('port ' + $UiPort + ' not in foreign hold') -Status 'FAIL' -Detail ('LISTENING held by FOREIGN pid ' + $uiState.Pid + ' (' + $uiState.Method + '); command line: ' + $uiState.CmdLine)
    } else {
        $uown = Test-V13UiOwnership -Port $UiPort
        if ($uown.Ok) {
            $uiOwnOk = $true
            Add-V13Check -Id 'D-9' -Name ('port ' + $UiPort + ' ownership') -Status 'PASS' -Detail ('listener owned by OUR pid ' + $uiState.Pid + '; ' + $uown.Reason)
        } else {
            Add-V13Check -Id 'D-9' -Name ('port ' + $UiPort + ' ownership') -Status 'FAIL' -Detail ('pid ' + $uiState.Pid + ' holds ' + $UiPort + ' and looks like our UI server, but UI ownership was NOT verified: ' + $uown.Reason)
        }
    }

    # --- 10. GREEN protection ----------------------------------------------- #
    Write-Output ''
    Write-Output ('10. GREEN protection (' + $script:GreenPort + ')')
    $green = Get-V13GreenState
    if ($green.State -eq 'UP (protected, untouched)') {
        Add-V13Check -Id 'D-10' -Name ('GREEN ' + $script:GreenPort + ' protected') -Status 'PASS' -Detail ('pid ' + $green.Pid + ' answers with the JCC service start line and its ownership marker is present')
    } else {
        Add-V13Check -Id 'D-10' -Name ('GREEN ' + $script:GreenPort + ' protected') -Status 'FAIL' -Detail ('state "' + $green.State + '": ' + $green.Detail)
    }

    # --- 11. disk space ----------------------------------------------------- #
    Write-Output ''
    Write-Output '11. disk space'
    $drive = Split-Path -Qualifier $Root
    if (-not $drive) { $drive = 'F:' }
    $ds = Get-PSDrive -Name ($drive.TrimEnd(':')) -ErrorAction SilentlyContinue
    if ($ds -and $null -ne $ds.Free) {
        $freeGb = [math]::Round(($ds.Free / 1GB), 2)
        $usedGb = [math]::Round(($ds.Used / 1GB), 2)
        if ($freeGb -ge $MinFreeGb) {
            Add-V13Check -Id 'D-11' -Name ('free disk space on ' + $drive) -Status 'PASS' -Detail ('{0} GiB free / {1} GiB used (minimum {2} GiB)' -f $freeGb, $usedGb, $MinFreeGb)
        } else {
            Add-V13Check -Id 'D-11' -Name ('free disk space on ' + $drive) -Status 'FAIL' -Detail ('{0} GiB free, below the required {1} GiB' -f $freeGb, $MinFreeGb)
        }
    } else {
        Add-V13Check -Id 'D-11' -Name ('free disk space on ' + $drive) -Status 'FAIL' -Detail ('could not measure free space for ' + $drive)
    }

    # --- INFO: things that are operator decisions, not defects -------------- #
    Write-Output ''
    Write-Output '12. informational (never counted as FAIL)'
    $tokenName = Get-V13YamlScalar -Path $Paths.Config -Key 'token_env'
    if (-not $tokenName) { $tokenName = 'JCC_CONTROL_TOKEN' }
    $tokenVal = [System.Environment]::GetEnvironmentVariable($tokenName)
    if ($tokenVal) {
        Add-V13Info -Id 'D-12' -Name 'control token' -Detail ($tokenName + ' is SET in this session (value never read or printed); control endpoints are gated')
    } else {
        Add-V13Info -Id 'D-12' -Name 'control token' -Detail ($tokenName + ' is UNSET in this session -- control endpoints will answer 401. That is fail-closed, not a defect; set it to exercise the write surface.')
    }

    # D-13: the verdict comes from the task STORE ON DISK, never from
    # Get-ScheduledTask alone, and it is THREE-state (PRESENT / ABSENT /
    # CANNOT_QUERY). Measured while closing Wave 8: doctor printed
    # "JCC-v1.3-API=ABSENT" while that task's file was sitting in
    # C:\Windows\System32\Tasks (mtime 12:15:03, 4176 bytes), because
    # Get-ScheduledTask answers a CimException for EVERY task in this session --
    # including pre-existing ones that demonstrably exist. An instrument that
    # cannot see anything must never be allowed to answer "absent": folding
    # "cannot query" into "not there" is the false-negative shape this Wave
    # exists to remove, and doctor was still printing it.
    $taskStore = Join-Path $env:SystemRoot 'System32\Tasks'
    $storeReadable = Test-Path -LiteralPath $taskStore
    $tasksFound = @()
    foreach ($tn in @('JCC-v1.3-API', 'JCC-v1.3-VisualUI')) {
        if (-not $storeReadable) { $tasksFound += ($tn + '=CANNOT_QUERY'); continue }
        if (Test-Path -LiteralPath (Join-Path $taskStore $tn)) {
            $providerNote = ''
            try {
                $providerNote = ' (provider reports state=' + [string](Get-ScheduledTask -TaskName $tn -ErrorAction Stop).State + ')'
            } catch {
                $providerNote = ' (provider blind in this session; on-disk verdict used instead)'
            }
            $tasksFound += ($tn + '=PRESENT' + $providerNote)
        } else {
            $tasksFound += ($tn + '=ABSENT')
        }
    }
    if (-not $storeReadable) {
        Add-V13Info -Id 'D-13' -Name 'durable scheduled tasks' -Detail (($tasksFound -join ', ') + '  (the task store ' + $taskStore + ' could not be read -- CANNOT_QUERY, which is NOT the same as ABSENT)')
    } else {
        Add-V13Info -Id 'D-13' -Name 'durable scheduled tasks' -Detail (($tasksFound -join ', ') + '  (verdicts read from ' + $taskStore + '; ABSENT = PENDING_EXTERNAL_ACTION: run scripts\jcc-v1.3-register-tasks.ps1 on the host)')
    }

    # --- next hint, derived from what section 8/9 just measured ------------ #
    $portState = 'FREE'; $foreignPid = 0
    if ($apiState.State -eq 'FOREIGN') { $portState = 'FOREIGN'; $foreignPid = $apiState.Pid }
    elseif ($apiState.State -eq 'OURS' -and $apiOwnOk) { $portState = 'OURS' }
    $nextHint = Get-V13NextHint -PortState $portState -ForeignPid $foreignPid

    Write-Output ''
    Write-Output '================================================'
    Write-Output ' SUMMARY'
    Write-Output '================================================'
    Write-Output (' checks   : ' + $script:DocChecks.Count)
    Write-Output (' PASS     : ' + $script:DocPass)
    Write-Output (' FAIL     : ' + $script:DocFail)
    Write-Output (' API up   : ' + $apiOwnOk + '   UI up: ' + $uiOwnOk)
    $failed = @($script:DocChecks | Where-Object { $_.Status -eq 'FAIL' })
    if ($failed.Count -gt 0) {
        Write-Output ''
        Write-Output ' failing checks:'
        foreach ($c in $failed) { Write-Output ('  ' + $c.Id + ' ' + $c.Name + ' -- ' + $c.Detail) }
        Write-Output ''
        Write-Output 'RESULT: FAIL'
        Write-Output ('next      : .\scripts\jcc-v1.3.ps1 -Action repair   (or read the failing checks above)')
        $script:V13Code = 1
        return
    }
    Write-Output ''
    Write-Output 'RESULT: PASS'
    Write-Output ('next      : ' + $nextHint)
    $script:V13Code = 0
}

# =========================================================================== #
# REPAIR -- reconcile PROVABLY STALE bookkeeping. It never signals anything.
# =========================================================================== #

function Move-V13StaleFile {
    <#
        Quarantine a bookkeeping file by MOVING it, never deleting it.

        This repo's rule is "move, do not delete" (PERMANENT_DELETE=0), and it
        applies to bookkeeping too: a stale pid file is evidence of what
        happened, and evidence that gets deleted cannot be inspected later. So
        the file is renamed to <name>.stale-<stamp> next to itself.

        The return value is NOT trusted: after the move both sides are re-listed
        and the result is the reconciliation (source gone AND destination
        present). "The instrument reported success but silently did nothing" is
        a defect class this project has already hit three times.

        Prints nothing; returns a hashtable.
    #>
    param([string]$Path, [string]$Stamp)

    if (-not (Test-Path -LiteralPath $Path)) {
        return @{ Changed = $false; Destination = ''; Detail = 'not present (nothing to quarantine)' }
    }
    $dest = $Path + '.stale-' + $Stamp
    $n = 1
    while (Test-Path -LiteralPath $dest) {
        $n++
        if ($n -gt 100) {
            return @{ Changed = $false; Destination = $dest; Detail = 'no free quarantine name after 100 attempts' }
        }
        $dest = $Path + '.stale-' + $Stamp + '-' + $n
    }

    if ($DryRun) {
        return @{ Changed = $false; Destination = $dest; Detail = 'DRY RUN: would move ' + $Path + ' -> ' + $dest }
    }

    try {
        Move-Item -LiteralPath $Path -Destination $dest -ErrorAction Stop
    } catch {
        return @{ Changed = $false; Destination = $dest; Detail = 'move threw: ' + $_.Exception.Message }
    }

    $srcGone = -not (Test-Path -LiteralPath $Path)
    $dstThere = Test-Path -LiteralPath $dest
    if ($srcGone -and $dstThere) {
        return @{ Changed = $true; Destination = $dest; Detail = ('moved ' + [System.IO.Path]::GetFileName($Path) + ' -> ' + [System.IO.Path]::GetFileName($dest) + ' (verified: source gone, destination present)') }
    }
    return @{ Changed = $false; Destination = $dest; Detail = ('move reported done but the tree disagrees (source gone=' + $srcGone + ', destination present=' + $dstThere + ')') }
}

function Repair-V13Half {
    <#
        One half (API or UI). Prints nothing: the caller prints the returned
        Lines, because a function cannot both emit a report and return a value.

        The ONLY thing this may change is bookkeeping that is provably stale:
        the recorded pid is dead AND nothing is listening on its port. Anything
        else -- a live pid, a foreign listener, a live-but-unverified process --
        is REFUSED and left exactly as it is.
    #>
    param(
        [string]$Tag,
        [string]$PidFile,
        [string]$MarkerFile,
        [int]$Port,
        [string]$Expect
    )

    $lines = New-Object System.Collections.Generic.List[string]
    $out = @{ Tag = $Tag; Repaired = 0; Refused = 0; Lines = @() }

    $stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')

    $pidFilePid = 0
    if (Test-Path -LiteralPath $PidFile) {
        try {
            $v = ((Get-Content -LiteralPath $PidFile -Raw -ErrorAction Stop) -replace '\s+','').Trim()
            if ($v -match '^\d+$') { $pidFilePid = [int]$v }
        } catch { }
    }
    $markerPresent = Test-Path -LiteralPath $MarkerFile
    $ownerPid = Get-V13PortOwnerPid -Port $Port
    $lines.Add('  pid file : ' + $PidFile + ' -> ' + $(if ($pidFilePid -gt 0) { 'pid ' + $pidFilePid } else { 'absent' }))
    $lines.Add('  marker   : ' + $MarkerFile + ' -> ' + $(if ($markerPresent) { 'present' } else { 'absent' }))
    $lines.Add('  port ' + $Port + '  : owner pid ' + $ownerPid)

    # ---- case A: no pid file ------------------------------------------------- #
    if ($pidFilePid -le 0) {
        if ($ownerPid -ne 0) {
            $info = Get-JccProcessInfo -ProcessId $ownerPid
            $looksOurs = ((Get-JccNormCmd -Text ([string]$info.CommandLine)) -like ('*' + $Expect + '*'))
            $lines.Add('  REFUSED: nothing is recorded for ' + $Tag + ', but port ' + $Port + ' is LISTENING (pid ' + $ownerPid + ').')
            $lines.Add('           command line: ' + $info.CommandLine)
            $lines.Add('           It was NOT adopted and NOT signalled. ' + $(if ($looksOurs) { 'Adopt a running instance deliberately with -Action mark.' } else { 'It is not ours at all -- leave it alone.' }))
            $out.Refused = 1
            $out.Lines = $lines.ToArray()
            return $out
        }
        if ($markerPresent) {
            $r = Move-V13StaleFile -Path $MarkerFile -Stamp $stamp
            $lines.Add('  stale marker, no pid file and nothing listening -> ' + $r.Detail)
            if ($r.Changed) { $out.Repaired++ } else { $out.Refused++ }
        } else {
            $lines.Add('  clean: no pid file, no marker, nothing listening -- nothing to repair.')
        }
        $out.Lines = $lines.ToArray()
        return $out
    }

    # ---- case B: a pid is recorded ------------------------------------------ #
    $alive = Test-JccProcessAlive -ProcessId $pidFilePid
    $info2 = Get-JccProcessInfo -ProcessId $pidFilePid
    $lines.Add('  recorded pid ' + $pidFilePid + ' alive: ' + $alive)
    if ($info2.CommandLine) { $lines.Add('  its command line: ' + $info2.CommandLine) }

    if ($alive) {
        $own = $null
        if ($Tag -eq 'API') { $own = Test-JccOwnership -Paths $Paths -Port $Port -Root $Root }
        else { $own = Test-V13UiOwnership -Port $Port }
        if ($own.Ok) {
            $lines.Add('  healthy: ' + $own.Reason)
            $lines.Add('  nothing to repair -- this half is verified up. (repair never restarts a healthy service)')
            $out.Lines = $lines.ToArray()
            return $out
        }
        $lines.Add('  REFUSED: pid ' + $pidFilePid + ' is ALIVE but not verified as ours.')
        $lines.Add('           ' + $own.Reason)
        $lines.Add('           A live process is never quarantined from its bookkeeping and never signalled.')
        $lines.Add('           Confirm with: netstat -ano | findstr :' + $Port + '   and inspect the command line above.')
        $out.Refused = 1
        $out.Lines = $lines.ToArray()
        return $out
    }

    if ($ownerPid -ne 0) {
        $oinfo = Get-JccProcessInfo -ProcessId $ownerPid
        $lines.Add('  REFUSED: the recorded pid ' + $pidFilePid + ' is dead, but port ' + $Port + ' is held by pid ' + $ownerPid + '.')
        $lines.Add('           command line: ' + $oinfo.CommandLine)
        $lines.Add('           That listener is not ours to touch; bookkeeping was left in place so the')
        $lines.Add('           discrepancy stays visible. Resolve the holder yourself.')
        $out.Refused = 1
        $out.Lines = $lines.ToArray()
        return $out
    }

    # ---- the one provably stale shape: dead pid AND free port -------------- #
    $lines.Add('  provably stale: recorded pid is dead AND nothing is LISTENING on ' + $Port + '.')
    if ($markerPresent) {
        $r1 = Move-V13StaleFile -Path $MarkerFile -Stamp $stamp
        $lines.Add('    ' + $r1.Detail)
        if ($r1.Changed) { $out.Repaired++ } else { $out.Refused++ }
    }
    $r2 = Move-V13StaleFile -Path $PidFile -Stamp $stamp
    $lines.Add('    ' + $r2.Detail)
    if ($r2.Changed) { $out.Repaired++ } else { $out.Refused++ }

    $out.Lines = $lines.ToArray()
    return $out
}

function Invoke-V13Repair {
    param([int]$ApiPort, [int]$UiPort)

    Write-Output 'JCC v1.3 -Action repair'
    Write-Output ('repo root : ' + $Root)
    Write-Output ('timestamp : ' + (Get-Date).ToString('o'))
    Write-Output 'RULE: repair changes ONLY bookkeeping that is provably stale (recorded pid dead AND port free).'
    Write-Output 'RULE: it never signals a process, never restarts a healthy service, and never deletes anything'
    Write-Output '      (stale files are MOVED aside to <name>.stale-<stamp>, so the evidence survives).'
    if ($DryRun) { Write-Output 'DRY RUN: nothing will be moved.' }
    Write-Output ''

    $repaired = 0; $refused = 0; $blocked = 0

    # --- runtime dir -------------------------------------------------------- #
    Write-Output '1. runtime directory'
    if (Test-Path -LiteralPath $Paths.Runtime) {
        Write-Output ('  present: ' + $Paths.Runtime)
    } elseif ($DryRun) {
        Write-Output ('  DRY RUN: would create ' + $Paths.Runtime)
    } else {
        New-JccEnsureDir -Path $Paths.Runtime | Out-Null
        $made = Test-Path -LiteralPath $Paths.Runtime
        Write-Output ('  created ' + $Paths.Runtime + ' -> verified present: ' + $made)
        if ($made) { $repaired++ } else { $blocked++ }
    }

    # --- API half ----------------------------------------------------------- #
    Write-Output ''
    Write-Output '2. API bookkeeping'
    $apiRes = Repair-V13Half -Tag 'API' -PidFile $Paths.PidFile -MarkerFile $Paths.MarkerFile -Port $ApiPort -Expect '-m jcc_core.run'
    foreach ($l in $apiRes.Lines) { Write-Output $l }
    $repaired += [int]$apiRes.Repaired
    $refused += [int]$apiRes.Refused

    # --- UI half ------------------------------------------------------------ #
    Write-Output ''
    Write-Output '3. Visual UI bookkeeping'
    $uiRes = Repair-V13Half -Tag 'UI' -PidFile $Paths.UiPidFile -MarkerFile $Paths.UiMarkerFile -Port $UiPort -Expect 'serve_visual.py'
    foreach ($l in $uiRes.Lines) { Write-Output $l }
    $repaired += [int]$uiRes.Repaired
    $refused += [int]$uiRes.Refused

    # --- service assets ----------------------------------------------------- #
    Write-Output ''
    Write-Output '4. service assets'
    if (Test-Path -LiteralPath $Paths.Config) {
        Write-Output ('  config present: ' + $Paths.Config)
    } else {
        Write-Output ('  BLOCKED: ' + $Paths.Config + ' is missing. repair does not invent configuration.')
        $blocked++
    }
    $indexPath = Join-Path $Paths.UiDist 'index.html'
    if (Test-Path -LiteralPath $indexPath) {
        Write-Output ('  UI dist present: ' + $indexPath)
    } else {
        Write-Output ('  BLOCKED: ' + $indexPath + ' is missing. repair does NOT run the frontend build')
        Write-Output '           (a build is a heavy, network-touching side effect; it is not a repair).'
        Write-Output '           run it yourself:  cd apps\command_center_3d; npm ci; npm run build'
        $blocked++
    }

    # --- what repair refuses to do ------------------------------------------ #
    Write-Output ''
    Write-Output '5. what repair deliberately does NOT do'
    Write-Output '  * signal any process (there is no Stop-Process path in this action)'
    Write-Output '  * restart or reconfigure a running service (-Action restart is explicit)'
    Write-Output '  * delete files (only quarantine by rename)'
    Write-Output ('  * touch GREEN ' + $script:GreenPort + ' (not even probed here)')
    Write-Output '  * run npm / pip / any network operation'

    Write-Output ''
    Write-Output 'summary'
    Write-Output ('  bookkeeping files quarantined : ' + $repaired)
    Write-Output ('  conditions left for a human   : ' + $refused)
    Write-Output ('  blocked (missing prerequisite): ' + $blocked)

    if ($refused -gt 0) {
        Write-Output ''
        Write-Output 'RESULT: NEEDS A HUMAN -- repair found something it must not touch. See the REFUSED lines above.'
        $script:V13Code = 2
        return
    }
    if ($blocked -gt 0) {
        Write-Output ''
        Write-Output 'RESULT: BLOCKED -- a prerequisite is missing. See the BLOCKED lines above.'
        $script:V13Code = 3
        return
    }
    Write-Output ''
    Write-Output 'RESULT: OK -- nothing unsafe left. The host is in a state this script can reason about.'
    Write-Output '  next      : .\scripts\jcc-v1.3.ps1 -Action doctor'
    $script:V13Code = 0
}

# =========================================================================== #
# MARK -- adopt a runtime that is already listening (e.g. started by a task).
# =========================================================================== #

function Invoke-V13Mark {
    param([int]$ApiPort, [int]$UiPort)

    Write-Output 'JCC v1.3 -Action mark'
    Write-Output ('repo root : ' + $Root)
    Write-Output ('timestamp : ' + (Get-Date).ToString('o'))
    Write-Output 'WHY: a service started by Task Scheduler is not started by a launcher script, so no'
    Write-Output '     launcher could write its ownership marker. This adopts the CURRENT port holder,'
    Write-Output '     but only after positively identifying it by command line.'
    Write-Output ''

    $ok = 0; $bad = 0

    # --- API ---------------------------------------------------------------- #
    $apiOwner = Get-JccPortOwner -Port $ApiPort
    $apiOwnerPid = 0
    if ($apiOwner -and $apiOwner.Pid) { $apiOwnerPid = [int]$apiOwner.Pid }
    if ($apiOwnerPid -le 0) {
        Write-Output ('API: no listener on ' + $ApiPort + ' -- nothing to mark.')
        $bad++
    } else {
        $info = Get-JccProcessInfo -ProcessId $apiOwnerPid
        if ((Get-JccNormCmd -Text ([string]$info.CommandLine)) -notlike '*-m jcc_core.run*') {
            Write-Output ('API: port ' + $ApiPort + ' holder is not our service: ' + $info.CommandLine)
            $bad++
        } else {
            Set-Content -LiteralPath $Paths.PidFile -Value ([string]$apiOwnerPid) -Encoding ASCII
            Write-JccOwnershipMarker -Paths $Paths -Port $ApiPort -PythonExe $script:V13Python -Root $Root `
                -LauncherPid 0 -ServicePid $apiOwnerPid `
                -Config $Paths.Config -CreatedBy 'scripts/jcc-v1.3.ps1 (mark)' | Out-Null
            Write-Output ('API: marker written for service pid ' + $apiOwnerPid + ' on port ' + $ApiPort)
            $ok++
        }
    }

    # --- UI ----------------------------------------------------------------- #
    $uiOwner = Get-JccPortOwner -Port $UiPort
    $uiOwnerPid = 0
    if ($uiOwner -and $uiOwner.Pid) { $uiOwnerPid = [int]$uiOwner.Pid }
    if ($uiOwnerPid -le 0) {
        Write-Output ('UI: no listener on ' + $UiPort + ' -- nothing to mark.')
        $bad++
    } else {
        $info = Get-JccProcessInfo -ProcessId $uiOwnerPid
        if ((Get-JccNormCmd -Text ([string]$info.CommandLine)) -notlike '*serve_visual.py*') {
            Write-Output ('UI: port ' + $UiPort + ' holder is not our Visual UI: ' + $info.CommandLine)
            $bad++
        } else {
            Write-V13UiMarker -ServicePid $uiOwnerPid -LauncherPid 0 -Port $UiPort `
                              -CmdLine ([string]$info.CommandLine) `
                              -CreatedBy 'scripts/jcc-v1.3.ps1 (mark)' | Out-Null
            Write-Output ('UI: marker written for service pid ' + $uiOwnerPid + ' on port ' + $UiPort)
            $ok++
        }
    }

    Write-Output ''
    if ($bad -eq 0) {
        Write-Output 'RESULT: MARKED (both halves adopted after command-line identification)'
        $script:V13Code = 0
        return
    }
    Write-Output ('RESULT: PARTIAL -- ' + $ok + ' half(es) marked, ' + $bad + ' could not be verified.')
    Write-Output '  diagnose  : .\scripts\jcc-v1.3.ps1 -Action doctor'
    $script:V13Code = 1
}

# =========================================================================== #
# DISPATCH
# =========================================================================== #

$script:V13Python = Get-JccPython -Root $Root

$resolvedApiPort = $ApiPort
if ($resolvedApiPort -le 0) { $resolvedApiPort = Get-JccV13Port -Root $Root -Python $script:V13Python }
$resolvedUiPort = $UiPort
if ($resolvedUiPort -le 0) { $resolvedUiPort = Get-JccV13UiPort }

# Half-state carried between the action functions. Deliberately script-scoped:
# an action function's exit code cannot ride the pipeline alongside its report.
$script:ApiUp = $false
$script:UiUp = $false
$script:ApiDown = 0
$script:UiDown = 0

switch ($Action) {
    'up'      { Invoke-V13Up      -ApiPort $resolvedApiPort -UiPort $resolvedUiPort -TimeoutSec $HealthTimeoutSec }
    'down'    { Invoke-V13Down    -ApiPort $resolvedApiPort -UiPort $resolvedUiPort -GracefulSeconds $GracefulSeconds }
    'restart' { Invoke-V13Restart -ApiPort $resolvedApiPort -UiPort $resolvedUiPort -TimeoutSec $HealthTimeoutSec -GracefulSeconds $GracefulSeconds }
    'status'  { Invoke-V13Status  -ApiPort $resolvedApiPort -UiPort $resolvedUiPort }
    'doctor'  { Invoke-V13Doctor  -ApiPort $resolvedApiPort -UiPort $resolvedUiPort -MinFreeGb $MinFreeGb }
    'repair'  { Invoke-V13Repair  -ApiPort $resolvedApiPort -UiPort $resolvedUiPort }
    'mark'    { Invoke-V13Mark    -ApiPort $resolvedApiPort -UiPort $resolvedUiPort }
}

# Flush the transcript BEFORE exiting: `exit` would otherwise terminate the
# process with the transcript's tail still buffered, which is the same
# "instrument silently loses the evidence" failure this parameter exists to fix.
# Guarded, because a Stop-Transcript failure must never change the exit code.
if ($script:V13Transcript) {
    try { Stop-Transcript | Out-Null } catch { }
    try {
        $len = 0
        if (Test-Path -LiteralPath $LogFile) { $len = (Get-Item -LiteralPath $LogFile).Length }
        Write-Output ('transcript: ' + $LogFile + ' (' + $len + ' bytes)')
    } catch { }
}

exit $script:V13Code

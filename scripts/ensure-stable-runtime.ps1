<#
    JCC GREEN runtime -- the ONE authoritative restore action for 127.0.0.1:4778.
    Windows PowerShell 5.1 ONLY.

    WHAT GREEN IS. 4778 must serve the FROZEN, ACCEPTED v1.0.1 product code, no
    matter what the feature branch has done to the worktree. `scripts/start.ps1`
    builds PYTHONPATH from <root>\packages and <root> is the WORKTREE, so serving
    "the repo" would put in-progress v1.1 code on the production port. GREEN
    therefore serves an immutable snapshot extracted from the frozen commit into
    runtime\stable_v101\ (see tools/stable_runtime_snapshot.py, which proves that
    tree byte-identical to the commit by two independent routes).

    WHY THIS SCRIPT EXISTS AT ALL. There was already a service watchdog
    (scripts\service-watchdog.ps1) and it was installed, enabled, and ticking
    every two minutes. It also never once recovered anything: its Rule 1 requires
    .workbuddy\release_state.json to say READY, that file does not exist, and so
    every tick since installation logged
        NOOP -- release state is <unset>; monitoring is reserved for READY
    (measured: 951 lines in .workbuddy\scheduled\logs\service-watchdog.jsonl).
    A watchdog with a gate nobody knew about is not a watchdog. This script has
    NO such gate: its whole job is "make GREEN correct, or say precisely why you
    could not".

    THE DECISION, IN ORDER. Recovery is only ever applied to a process this
    project can prove it owns -- "restart whatever is on 4778" is how a watchdog
    becomes an outage on a shared machine.

        1. healthy AND ownership proven          -> NOOP
        2. Answering but ownership unprovable    -> FAIL-CLOSED (never touched)
        3. Listener exists, not provably ours    -> FAIL-CLOSED (never touched)
        4. Listener is ours but unhealthy        -> stop.ps1, start.ps1, verify
        5. No listener, recorded pids provably gone -> clean stale marker, start
        6. No listener, a recorded pid still live/unproven -> FAIL-CLOSED

    Note 5's "provably gone": a recorded pid that is ALIVE but whose real start
    time differs from the marker's is a RECYCLED pid, i.e. the recorded process
    is gone. That is the only way a live pid still authorises a marker cleanup,
    and it is recorded as such.

    EXIT: 0 NOOP / dry-run, 2 recovered, 3 FAIL-CLOSED, 4 recovery failed,
          5 NEEDS_OPERATOR, 1 tick failed.
#>
[CmdletBinding()]
param(
    # Install the backoff + NEEDS_OPERATOR bookkeeping the 2-minute task uses.
    # The boot/logon task runs WITHOUT this switch: a boot should always try.
    [switch]$Watchdog,
    [switch]$DryRun,
    # The runtime root: whose scripts\, config\ and runtime\ are used. Defaults to
    # the repo root. Tests point this at an isolated bundle so a real restore can
    # be exercised without going anywhere near the live 4778.
    [string]$Root = '',
    # Where the frozen snapshot and git live. Only differs from -Root in a test.
    [string]$RepoRoot = '',
    # 0 => read server.port out of the config.
    [int]$Port = 0,
    [string]$SnapshotSha = 'f8ac95d30f67f892e28fba03e3d99f900a65a61d',
    [string]$SnapshotDir = '',
    [string]$PythonExe = '',
    [int]$HealthTimeoutSec = 90,
    [int]$PidIdentitySkewSec = 2
)

# --------------------------------------------------------------------------- #
# EVIDENCE BEFORE ANYTHING THAT CAN THROW.
#
# Two measured facts about this host decide the shape of everything below.
#
# (1) A watchdog that fails silently is indistinguishable from a healthy one.
#     scripts\service-watchdog.ps1 was installed, enabled and ticking every two
#     minutes for 951 consecutive ticks, and every one logged
#     "NOOP -- release state is <unset>" because of a gate nobody knew about.
#     The log said NOOP, so the watchdog looked fine, so nobody looked.
#
# (2) THE EVIDENCE CHANNEL MUST NOT BE HANDED TO THE PROCESS BEING WATCHED.
#     This script LAUNCHES a detached service that outlives it, and Windows
#     handle inheritance gives that service a copy of every inheritable handle
#     the tick is holding. A previous revision let cmd.exe redirect the tick's
#     stdout into runtime\logs\stable-runtime-watchdog.log with `>>`. cmd opens
#     a redirect target with FILE_SHARE_READ, so the tick that launched a
#     service LOCKED ITS OWN LOG AGAINST EVERY LATER TICK:
#
#         tick 22:36:41  launched the service; handle inherited; exit 2
#         tick 22:37:41  `>>` open failed -> cmd never started PowerShell at all
#         tick 22:39:39  exit 1, no output, log byte-identical, and the log's
#         tick 22:41:41  silence read as health
#
#     Measured 2026-09-27 with the service up: opening that file for append
#     failed under FileShare None AND Read AND ReadWrite ("being used by another
#     process") while pid 14680, the service launcher, was alive.
#
#     The mirror image is equally bad: redirect a task into a PIPE and the
#     inherited write end keeps the reader waiting forever, the task never
#     leaves "Running", and MultipleInstances=IgnoreNew then suppresses every
#     later tick. Both traps are one defect -- the watchdog's evidence channel
#     gets captured by the thing it is watching.
#
#     So there is NO task-level redirection here, to a file or to a pipe. This
#     script writes its own durable log, and every write is an explicit
#     open/write/flush/close so no handle outlives the call and gets inherited.
#     scripts\ensure-stable-runtime-install.ps1 registers the action with no
#     `>>` and no `2>&1` for exactly this reason.
# --------------------------------------------------------------------------- #
function Write-JccRawLog {
    <#  Append one line with an explicit open/write/flush/close.

        Deliberately NOT Add-Content: the whole point is that no handle survives
        the call. Never throws -- every caller sits on a path that logging must
        not be able to break -- and reports success instead. #>
    param([string]$Path, [string]$Text)
    if (-not $Path) { return $false }
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$Text + "`r`n")
        $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Append,
                                     [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
        try { $fs.Write($bytes, 0, $bytes.Length); $fs.Flush() } finally { $fs.Dispose() }
        return $true
    } catch { return $false }
}

$script:JccBootLog    = $null
$script:JccDurableLog = $null
$script:JccDurableOk  = $true
$resolvedPort         = 0

function Write-JccBoot {
    <#  One line per milestone, so a tick that dies cannot do it invisibly.
        `stage` alone is enough for a reader: enter / preflight-ok / caught.

        THE LINE IS BUILT BY CONCATENATION, NOT WITH -f, ON PURPOSE. The first
        draft used '{"at":"{0}",...}' -f ..., and PowerShell's format operator
        read the literal braces of the JSON as format items, threw
        FormatException, and the catch swallowed it -- so the beacon that exists
        to make failure visible was itself invisible. Measured 2026-09-27: the
        script ran, printed NOOP, and stable-runtime-boot.jsonl was never
        created. An instrument that reports nothing must not be built out of
        anything that can fail quietly. #>
    param([string]$Stage, [string]$Detail = '')
    try {
        $who = 'unknown'
        try { $who = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name } catch { }
        $safe = ''
        # A Windows path in JSON must not carry a raw backslash; fold it to a
        # forward slash so the line stays readable AND valid, and strip only what
        # would actually break the JSON.
        if ($Detail) { $safe = (($Detail -replace '["\r\n]', ' ') -replace '\\', '/') -replace '\s+', ' ' }
        $rec = '{"at":"' + (Get-Date).ToString('o') + '","pid":' + $PID +
               ',"user":"' + $who + '","watchdog":' + ([bool]$Watchdog).ToString().ToLower() +
               ',"stage":"' + $Stage + '","detail":"' + $safe + '"}'
        if (Write-JccRawLog -Path $script:JccBootLog -Text $rec) { return $true }
        # The beacon file itself did not accept the line. Fall back to a sibling
        # before giving up: losing the beacon means losing the only evidence that
        # this tick ever started, which is the exact failure the beacon exists to
        # prevent. A sibling name is free by construction, so this cannot fail
        # for the same reason.
        if ($script:JccBootLog) { return (Write-JccRawLog -Path ($script:JccBootLog + '.unwritable') -Text $rec) }
        return $false
    } catch { return $false }
}

# The log directory is created here, before any dependency, because the beacon
# cannot be written into a directory that does not exist yet.
try {
    $logsDir = Join-Path (Split-Path -Parent (Split-Path -Parent $PSCommandPath)) 'runtime\logs'
    if (-not [System.IO.Directory]::Exists($logsDir)) {
        [System.IO.Directory]::CreateDirectory($logsDir) | Out-Null
    }
    $script:JccBootLog    = Join-Path $logsDir 'stable-runtime-boot.jsonl'
    $script:JccDurableLog = Join-Path $logsDir 'stable-runtime-watchdog.log'
} catch { }

Write-JccBoot -Stage 'enter' -Detail ('watchdog=' + [bool]$Watchdog + ';root=[' + [string]$Root + '];port=' + [string]$Port) | Out-Null

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $PSCommandPath
. (Join-Path $ScriptDir 'lib\JccCommon.ps1')
. (Join-Path $ScriptDir 'lib\JccWatchdogCommon.ps1')

if (-not $Root)     { $Root = Get-JccRepoRoot }
if (-not $RepoRoot) { $RepoRoot = $Root }
$Root     = (Resolve-Path -LiteralPath $Root).Path
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$script:JccRoot = $Root

$Paths = Get-JccPaths -Root $Root
if (-not $SnapshotDir) { $SnapshotDir = Join-Path $Root 'runtime\stable_v101' }

if (-not $PythonExe) { $PythonExe = Get-JccPython }
$resolvedPort = $Port
if ($resolvedPort -le 0) { $resolvedPort = Get-JccPort -Root $Root -Python $PythonExe }
$healthUrl = 'http://127.0.0.1:{0}/api/health' -f $resolvedPort

$LogDir   = Join-Path $Root 'runtime\logs'
$AuditLog = Join-Path $LogDir 'stable-runtime.jsonl'
$StateLog = Join-Path $LogDir 'stable-health-state.json'
$RecoveryDir = Join-Path $Root 'runtime\recovery'

# The backoff ladder. Each entry is the delay BEFORE the next attempt, indexed by
# how many attempts in a row have failed.
$BackoffLadder = @(10, 30, 60, 120, 300)

# Everything above this line runs OUTSIDE the try/catch below -- parameter
# binding, dot-sourcing the libraries, resolving the root, finding the
# interpreter, reading the port out of the config. A failure in any of it kills
# the tick before the first Write-Tick, which is precisely how the silence this
# file now guards against was produced. The beacon turns that into evidence.
Write-JccBoot -Stage 'preflight-ok' -Detail (
    'port=' + [string]$resolvedPort + ';python=[' + [string]$PythonExe + '];snapshot=[' + [string]$SnapshotDir + '];audit=[' + [string]$AuditLog + ']') | Out-Null

function Get-LastExitCode {
    <#  `$LASTEXITCODE` only exists once a native command (or a script that ran
        `exit N`) has executed, and under `Set-StrictMode -Version 2.0` -- which
        JccWatchdogCommon.ps1 turns on for everything that dot-sources it --
        reading it before that is a TERMINATING error, not `$null`. Measured on
        this host: "The variable '$LASTEXITCODE' cannot be retrieved because it
        has not been set."

        A watchdog that throws on its own bookkeeping is a watchdog that reports
        1 instead of doing its job, so every exit-code read goes through here. #>
    if (Test-Path variable:global:LASTEXITCODE) { return [int]$global:LASTEXITCODE }
    return 0
}

function Write-Tick {
    param([string]$Action, [string]$Detail, [string]$Level = 'INFO', [int]$ProcessId = 0)
    $rec = [pscustomobject]@{
        at      = (Get-Date).ToString('o')
        tick    = 'stable-runtime'
        action  = $Action
        level   = $Level
        detail  = $Detail
        pid     = $ProcessId
        port    = $resolvedPort
        watchdog = [bool]$Watchdog
    }
    try { Add-JccJsonLine -Path $AuditLog -Object $rec } catch { }
    $line = '[{0}] {1} {2}: {3}' -f $rec.at, $Level, $Action, $Detail
    # The durable human-readable log is written HERE, by this script, one
    # open/write/close per line -- never by a task-level redirect. A redirect
    # handle is inherited by the detached service and locks the file against
    # every later tick; see the header block for the measurement.
    if ($script:JccDurableLog) {
        if (-not (Write-JccRawLog -Path $script:JccDurableLog -Text $line)) {
            # The canonical log is unopenable. Never drop the line on the floor:
            # write it beside the log and say so. (Only reached if some other
            # process still holds a legacy lock -- the point of this design is
            # that nothing we launch ever can.)
            $script:JccDurableOk = $false
            Write-JccRawLog -Path ($script:JccDurableLog + '.unwritable') -Text ('CANONICAL-LOG-UNWRITABLE ' + $line) | Out-Null
        }
    }
    Write-Output $line
}

function Write-RecoveryBackup {
    <#  Copy a runtime state file aside BEFORE it is replaced, and record both
        hashes. The host already destroyed a whole source file to an all-NUL
        write-back once; a marker that is overwritten without a copy is a marker
        whose previous value nobody can ever check again. #>
    param([string]$Path, [string]$Stage)
    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{ path = $Path; before_sha256 = $null; after_sha256 = $null;
                                  backup = $null; existed = $false }
    }
    $before = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower()
    $stamp  = (Get-Date).ToString('yyyyMMdd-HHmmss')
    $dir    = Join-Path $RecoveryDir $stamp
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $backup = Join-Path $dir ('{0}-{1}' -f $Stage, (Split-Path -Leaf $Path))
    Copy-Item -LiteralPath $Path -Destination $backup -Force
    return [pscustomobject]@{ path = $Path; before_sha256 = $before; after_sha256 = $null;
                              backup = $backup; existed = $true }
}

function Complete-RecoveryBackup {
    param($Record)
    if (-not $Record -or -not $Record.existed) { return }
    if (Test-Path -LiteralPath $Record.path) {
        $Record.after_sha256 = (Get-FileHash -LiteralPath $Record.path -Algorithm SHA256).Hash.ToLower()
    }
    $ledgerPath = Join-Path $RecoveryDir 'ledger.jsonl'
    try {
        Add-JccJsonLine -Path $ledgerPath -Object ([pscustomobject]@{
            at            = (Get-Date).ToString('o')
            path          = $Record.path
            before_sha256 = $Record.before_sha256
            after_sha256  = $Record.after_sha256
            backup        = $Record.backup
        })
    } catch { }
}

function Get-PortListenerPid {
    return (Get-JccPortOwner -Port $resolvedPort).Pid
}

function Test-RecordedPidGone {
    <#  Is the process a recorded pid names PROVABLY no longer the one recorded?

        Returns a record: gone / alive / why. "alive and identical" is the only
        answer that withholds permission to clean the marker. #>
    param($Marker, [string]$Field)
    if (-not $Marker) { return [pscustomobject]@{ gone = $true; why = 'no marker at all' } }
    $p = 0
    if ($Marker.PSObject.Properties[$Field]) { $p = Get-JccPropInt -Object $Marker -Name $Field }
    if ($p -le 0) { return [pscustomobject]@{ gone = $true; why = "$Field is absent/zero" } }
    if (-not (Test-JccPidAlive -ProcessId $p)) {
        return [pscustomobject]@{ gone = $true; why = "$Field $p is not running" }
    }
    $recordedStart = Get-JccProp -Object $Marker -Name 'start_time'
    if (-not $recordedStart) {
        return [pscustomobject]@{ gone = $false; why = "$Field $p is alive and the marker records no start_time to compare" }
    }
    $actual = Get-JccProcessStartTime -ProcessId $p
    if (-not $actual) {
        return [pscustomobject]@{ gone = $false; why = "$Field $p is alive but its start time could not be read -- treated as still ours" }
    }
    $a = [datetime]::MinValue; $b = [datetime]::MinValue
    if (-not [datetime]::TryParse([string]$actual, [ref]$a) -or
        -not [datetime]::TryParse([string]$recordedStart, [ref]$b)) {
        return [pscustomobject]@{ gone = $false; why = "$Field $p start time unparseable -- treated as still ours" }
    }
    if ([math]::Abs(($a - $b).TotalSeconds) -le $PidIdentitySkewSec) {
        return [pscustomobject]@{ gone = $false; why = "$Field $p is alive and its start time matches the marker" }
    }
    return [pscustomobject]@{ gone = $true;
        why = "$Field $p is alive but is a DIFFERENT process (start $actual vs recorded $recordedStart): the pid was recycled" }
}

function Invoke-StableLaunch {
    <#  Serve the frozen snapshot on GREEN using the audited launcher.

        `scripts/start.ps1` prepends <root>\packages to PYTHONPATH UNLESS that path
        is already present. So PYTHONPATH is set here with the SNAPSHOT first and
        <root>\packages after it: start.ps1's own guard then leaves the value
        alone, and the snapshot -- being first -- wins every import. The worktree
        is never checked out, reset, or modified.

        Subtle mechanism, so it is never trusted on its own: every caller runs
        `tools/stable_runtime_snapshot.py verify-live` afterwards, which proves
        from the live payload that the frozen code is what actually answered.

        THE FROZEN TREE IS SERVED READ-ONLY, AND "READ-ONLY" IS ENFORCED BY
        MEASUREMENT. The first real restore proved the point: after the service
        had been up for a few minutes, `verify` FAILED with 35 problems --
        CPython had written `__pycache__\\*.cpython-313.pyc` INTO the snapshot,
        because that directory is on PYTHONPATH and is therefore writable by the
        interpreter it feeds. A directory the runtime can mutate is not an
        immutable snapshot, whatever it is called. So the interpreter is told
        not to write bytecode at all. The cost is recompiling ~13 small modules
        per start; the benefit is that `verify` stays a real check instead of one
        that has to be taught to ignore the thing it exists to catch. #>
    $snapshotPackages = Join-Path $SnapshotDir 'packages'
    $rootPackages = Join-Path $Root 'packages'
    $env:PYTHONPATH = $snapshotPackages + ';' + $rootPackages
    $env:JCC_CONFIG = $Paths.Config
    $env:PYTHONDONTWRITEBYTECODE = '1'

    $startScript = Join-Path $Root 'scripts\start.ps1'
    if (-not (Test-Path -LiteralPath $startScript -PathType Leaf)) {
        return [pscustomobject]@{ ok = $false; exit = -2; out = ''; err = "start.ps1 missing at $startScript" }
    }

    $argText = ''
    if ($resolvedPort -gt 0) { $argText = ' -Port ' + $resolvedPort }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = ''
    try {
        $out = Invoke-Expression -Command ('& "' + $startScript + '"' + $argText + ' -HealthTimeoutSec ' + $HealthTimeoutSec + ' 2>&1') | Out-String
    } catch {
        $out = 'start.ps1 threw: ' + $_.Exception.Message
    }
    $code = Get-LastExitCode
    $ErrorActionPreference = $prev
    return [pscustomobject]@{ ok = ($code -eq 0); exit = $code; out = $out.Trim() }
}

function Confirm-FrozenRuntime {
    <#  Prove the live service really is the frozen code. A false or unevaluable
        result is a failure of THIS restore, never a warning. #>
    $tool = Join-Path $RepoRoot 'tools\stable_runtime_snapshot.py'
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
        return [pscustomobject]@{ proven = $false; exit = -2; out = "snapshot verifier missing at $tool" }
    }
    $py = $PythonExe
    if (-not $py) { return [pscustomobject]@{ proven = $false; exit = -2; out = 'no python interpreter to run the verifier' } }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = ''
    try {
        $out = & $py $tool verify-live --dest $SnapshotDir --health-url $healthUrl --root $RepoRoot 2>&1 | Out-String
    } catch {
        $out = 'verifier threw: ' + $_.Exception.Message
    }
    $code = Get-LastExitCode
    $ErrorActionPreference = $prev
    return [pscustomobject]@{ proven = ($code -eq 0); exit = $code; out = $out.Trim() }
}

function Ensure-Snapshot {
    <#  The snapshot must EXIST and must PROVE itself. verify is run every time:
        a snapshot that has silently drifted is worse than none, because the
        script would otherwise happily serve it as "frozen". #>
    $tool = Join-Path $RepoRoot 'tools\stable_runtime_snapshot.py'
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
        return [pscustomobject]@{ ok = $false; detail = "snapshot tool missing at $tool" }
    }
    $manifest = Join-Path $SnapshotDir 'SNAPSHOT.json'
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if (-not (Test-Path -LiteralPath $manifest)) {
        $out = & $PythonExe $tool create --sha $SnapshotSha --dest $SnapshotDir --root $RepoRoot 2>&1 | Out-String
        $rc = Get-LastExitCode
        if ($rc -ne 0) {
            $ErrorActionPreference = $prev
            return [pscustomobject]@{ ok = $false; detail = "snapshot create failed (rc=$rc): $($out.Trim())" }
        }
    }
    $vout = & $PythonExe $tool verify --dest $SnapshotDir --root $RepoRoot 2>&1 | Out-String
    $vrc = Get-LastExitCode
    $ErrorActionPreference = $prev
    if ($vrc -ne 0) {
        return [pscustomobject]@{ ok = $false; detail = "snapshot verify failed (rc=$vrc): $($vout.Trim())" }
    }
    return [pscustomobject]@{ ok = $true; detail = $vout.Trim() }
}

function Get-BackoffState {
    $s = Read-JccJson -Path $StateLog
    if ($null -eq $s) {
        return [pscustomobject]@{ consecutive_failures = 0; next_attempt_at = $null; needs_operator = $false; history = @() }
    }
    return [pscustomobject]@{
        consecutive_failures = (Get-JccPropInt -Object $s -Name 'consecutive_failures')
        next_attempt_at      = (Get-JccProp -Object $s -Name 'next_attempt_at')
        needs_operator       = ([bool](Get-JccProp -Object $s -Name 'needs_operator'))
        history              = (Get-JccProp -Object $s -Name 'history')
    }
}

function Save-BackoffState {
    param([int]$Failures, [string]$NextAttemptAt, [bool]$NeedsOperator, $History)
    $h = @()
    if ($History) { $h = @($History) }
    if ($h.Count -gt 20) { $h = @($h | Select-Object -Last 20) }
    Write-JccJson -Path $StateLog -Object ([pscustomobject]@{
        consecutive_failures = $Failures
        next_attempt_at      = $NextAttemptAt
        needs_operator       = $NeedsOperator
        updated_at           = (Get-Date).ToString('o')
        history              = $h
    })
}

function New-HistoryEntry {
    param([string]$Result, [string]$Detail)
    return ('{0} {1} {2}' -f (Get-Date).ToString('o'), $Result, $Detail)
}

# =========================================================================== #
# The tick
# =========================================================================== #
try {
    if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    if (-not (Test-Path -LiteralPath $RecoveryDir)) { New-Item -ItemType Directory -Path $RecoveryDir -Force | Out-Null }

    $state = Get-BackoffState
    if ($Watchdog -and $state.next_attempt_at) {
        $due = [datetime]::MinValue
        if ([datetime]::TryParse([string]$state.next_attempt_at, [ref]$due)) {
            if ((Get-Date) -lt $due) {
                Write-Tick -Action 'NOOP' -Detail ("backing off until {0} after {1} consecutive failure(s)" -f $state.next_attempt_at, $state.consecutive_failures)
                exit 0
            }
        }
    }

    $health    = Invoke-JccHealth -Url $healthUrl -TimeoutMs 3000
    $listener  = Get-PortListenerPid
    $marker    = Read-JccJson -Path $Paths.MarkerFile
    $own       = Test-JccOwnership -Paths $Paths -Port $resolvedPort -Root $Root

    $healthDetail = 'unreachable'
    if ($health.Ok) { $healthDetail = 'HTTP 200' }
    elseif ($health.StatusCode -gt 0) { $healthDetail = 'HTTP ' + $health.StatusCode }

    # ---- 1. healthy AND ours -> NOOP -------------------------------------- #
    if ($health.Ok -and $own.Ok) {
        Write-Tick -Action 'NOOP' -Detail ("GREEN is healthy and provably ours; $healthDetail on :$resolvedPort; $($own.Reason)") -ProcessId $listener
        if ($Watchdog) { Save-BackoffState -Failures 0 -NextAttemptAt $null -NeedsOperator $false -History $state.history }
        exit 0
    }

    # ---- 2. answering but ownership unprovable -> FAIL-CLOSED ------------- #
    if ($health.Ok -and -not $own.Ok) {
        Write-Tick -Action 'FAIL_CLOSED' -Level 'ERROR' -ProcessId $listener -Detail (
            ":$resolvedPort answers $healthDetail but ownership cannot be proven ($($own.Reason)); " +
            "refusing to touch it. If a human confirms this listener is the JCC service, " +
            "the ownership marker under runtime\jcc must be repaired by hand.")
        exit 3
    }

    # ---- 3. a listener exists that is not provably ours -> FAIL-CLOSED ---- #
    if ($listener -gt 0 -and -not $own.Ok) {
        $info = Get-JccProcessInfo -ProcessId $listener
        Write-Tick -Action 'FAIL_CLOSED' -Level 'ERROR' -ProcessId $listener -Detail (
            "port $resolvedPort is LISTENING (pid $listener, $healthDetail) and ownership is not proven " +
            "($($own.Reason)); FOREIGN or UNPROVABLE OCCUPANCY -- nothing is signalled, nothing is started. " +
            "cmdline: $($info.CommandLine)")
        exit 3
    }

    # ---- 4. ours but unhealthy -> stop + start + verify ------------------- #
    $needsRestart = ($listener -gt 0 -and $own.Ok)

    # ---- 5/6. nothing listening -> stale-marker discipline ---------------- #
    if (-not $needsRestart) {
        $g1 = Test-RecordedPidGone -Marker $marker -Field 'service_pid'
        $g2 = Test-RecordedPidGone -Marker $marker -Field 'launcher_pid'
        if (-not $g1.gone -or -not $g2.gone) {
            Write-Tick -Action 'FAIL_CLOSED' -Level 'ERROR' -Detail (
                "nothing is listening on $resolvedPort and the runtime marker still names a live process: " +
                "$(if (-not $g1.gone) { $g1.why } else { $g2.why }). " +
                "A marker is only cleaned once the recorded pids are provably gone; refusing.")
            exit 3
        }
        Write-Tick -Action 'STALE_MARKER' -Detail (
            "nothing listening on $resolvedPort; recorded pids are provably gone ($($g1.why); $($g2.why)) -- cleaning the stale marker and restoring")
    }

    if ($DryRun) {
        $what = 'stop.ps1 + start.ps1'
        if (-not $needsRestart) { $what = 'clean stale marker, then start.ps1' }
        Write-Tick -Action 'DRYRUN' -Detail ("would run $what against the frozen snapshot $SnapshotSha; nothing was touched")
        exit 0
    }

    # ---- backoff gate (recovery attempts only) --------------------------- #
    if ($Watchdog -and $state.consecutive_failures -gt 0) {
        Write-Tick -Action 'NOTE' -Level 'WARN' -Detail ("retrying the restore after $($state.consecutive_failures) consecutive failure(s)")
    }

    # ---- a restore needs the project interpreter, or it is not a restore -- #
    if (-not $PythonExe) {
        Write-Tick -Action 'FAIL_CLOSED' -Level 'ERROR' -Detail (
            "no project interpreter under $Root\.venv; falling back to a PATH python would start a runtime " +
            "whose dependencies nobody verified, so nothing is started")
        exit 3
    }

    # ---- always make the snapshot prove itself before serving it ---------- #
    $snap = Ensure-Snapshot
    if (-not $snap.ok) {
        Write-Tick -Action 'SNAPSHOT_REFUSED' -Level 'ERROR' -Detail ("$($snap.detail) -- refusing to start anything from an unproven snapshot")
        if ($Watchdog) {
            $f = $state.consecutive_failures + 1
            $delay = $BackoffLadder[[math]::Min($f - 1, $BackoffLadder.Count - 1)]
            Save-BackoffState -Failures $f -NextAttemptAt ((Get-Date).AddSeconds($delay)).ToString('o') `
                -NeedsOperator ($f -gt $BackoffLadder.Count) `
                -History (@($state.history) + (New-HistoryEntry 'SNAPSHOT_REFUSED' $snap.detail))
        }
        exit 4
    }

    $backups = New-Object System.Collections.Generic.List[object]
    $backups.Add((Write-RecoveryBackup -Path $Paths.PidFile -Stage 'rootfs'))
    $backups.Add((Write-RecoveryBackup -Path $Paths.MarkerFile -Stage 'marker'))

    if ($needsRestart) {
        Write-Tick -Action 'RESTART' -Detail ("GREEN is ours but not healthy ($healthDetail); stopping then starting the frozen runtime")
        $stopScript = Join-Path $Root 'scripts\stop.ps1'
        if (Test-Path -LiteralPath $stopScript -PathType Leaf) {
            $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
            $stopOut = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $stopScript 2>&1 | Out-String
            $stopRc = $LASTEXITCODE
            $ErrorActionPreference = $prev
            Write-Tick -Action 'STOP' -Detail ("stop.ps1 exit=$stopRc :: " + ($stopOut.Trim() -replace '\s+', ' '))
        } else {
            Write-Tick -Action 'STOP' -Level 'WARN' -Detail ("no stop.ps1 at $stopScript; relying on the port being free after the service exits")
        }
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline) {
            if ((Get-PortListenerPid) -le 0) { break }
            Start-Sleep -Milliseconds 500
        }
    }

    # Clean a stale marker BEFORE launching: start.ps1 refuses when the pid file
    # names a live process, and it also refuses on a phantom marker.
    if (-not $needsRestart -and (Test-Path -LiteralPath $Paths.PidFile)) {
        try { Remove-Item -LiteralPath $Paths.PidFile -Force -ErrorAction Stop } catch { }
    }

    $launch = Invoke-StableLaunch
    Write-Tick -Action 'START' -Detail ("start.ps1 exit=$($launch.exit) :: " + ($launch.out -replace '\s+', ' '))

    $deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
    $up = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
        if ((Invoke-JccHealth -Url $healthUrl -TimeoutMs 2000).Ok) { $up = $true; break }
    }
    foreach ($b in $backups) { Complete-RecoveryBackup -Record $b }

    if (-not $up) {
        Write-Tick -Action 'RECOVERY_FAILED' -Level 'ERROR' -Detail ("the service did not answer $healthUrl within $HealthTimeoutSec s after start.ps1")
        if ($Watchdog) {
            $f = $state.consecutive_failures + 1
            $delay = $BackoffLadder[[math]::Min($f - 1, $BackoffLadder.Count - 1)]
            $needsOp = ($f -gt $BackoffLadder.Count)
            Save-BackoffState -Failures $f -NextAttemptAt ((Get-Date).AddSeconds($delay)).ToString('o') -NeedsOperator $needsOp `
                -History (@($state.history) + (New-HistoryEntry 'RECOVERY_FAILED' "health timeout after $HealthTimeoutSec s"))
            if ($needsOp) { Write-Tick -Action 'NEEDS_OPERATOR' -Level 'ERROR' -Detail ("$f consecutive failures; backing off to the $($BackoffLadder[-1]) s ceiling, but a Windows restart will still retry") }
        }
        exit 4
    }

    # ---- the restore is only DONE when the frozen code is proven live ----- #
    $proof = Confirm-FrozenRuntime
    if (-not $proof.proven) {
        Write-Tick -Action 'FROZEN_PROOF_FAILED' -Level 'ERROR' -Detail (
            "the service came up but is NOT provably the frozen runtime (verifier exit $($proof.exit)) :: " + ($proof.out -replace '\s+', ' '))
        if ($Watchdog) {
            $f = $state.consecutive_failures + 1
            Save-BackoffState -Failures $f -NextAttemptAt ((Get-Date).AddSeconds($BackoffLadder[[math]::Min($f - 1, $BackoffLadder.Count - 1)])).ToString('o') `
                -NeedsOperator ($f -gt $BackoffLadder.Count) `
                -History (@($state.history) + (New-HistoryEntry 'FROZEN_PROOF_FAILED' "verifier exit $($proof.exit)"))
        }
        exit 4
    }

    $newMarker = Read-JccJson -Path $Paths.MarkerFile
    Write-Tick -Action 'RECOVERED' -ProcessId (Get-PortListenerPid) -Detail (
        "GREEN restored from the frozen snapshot on :$resolvedPort; " +
        "service_pid=$(Get-JccProp -Object $newMarker -Name 'service_pid') " +
        "launcher_pid=$(Get-JccProp -Object $newMarker -Name 'launcher_pid'); " +
        "frozen-runtime proof: PROVEN")
    if ($Watchdog) { Save-BackoffState -Failures 0 -NextAttemptAt $null -NeedsOperator $false -History (@($state.history) + (New-HistoryEntry 'RECOVERED' 'frozen runtime proven live')) }
    exit 2
}
catch {
    $failure = $_.Exception.GetType().Name + ': ' + $_.Exception.Message
    # Write-Tick itself can throw here -- it reads $resolvedPort and friends, and
    # the whole reason we are in this block may be that one of them was never
    # set. So the beacon (which depends on nothing) is written unconditionally.
    try { Write-Tick -Action 'TICK_FAILED' -Level 'ERROR' -Detail $failure } catch { }
    Write-JccBoot -Stage 'caught' -Detail $failure | Out-Null
    exit 1
}

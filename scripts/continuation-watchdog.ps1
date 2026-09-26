# JCC continuation watchdog -- one tick.
#
# WHAT IT GUARANTEES: "not project complete" implies "a live controller exists".
# Everything else follows from that. A terminated conversation turn, a crashed
# controller, a reboot that cleared the process table -- none of them stop the
# pipeline, because the next tick notices the lock is stale and starts a
# replacement from the last persisted `current_step`.
#
# WHAT IT DELIBERATELY DOES NOT DO:
#   * It does not capture the controller's stdout. See Start-JccDetachedPython:
#     a pipe whose reader exits breaks the child, and a pump that waits for the
#     pipe means the tick never returns and the next tick is skipped.
#   * It does not restart a pipeline that has already finished. Once the state
#     says COMPLETE with project_run=PASS the watchdog is a permanent no-op --
#     a watchdog that resurrects a finished release is worse than no watchdog.
#   * It does not "clean up" a stale lock. The controller reclaims it on a dead
#     pid; deleting it here would let two controllers be started in the gap.
#
# EXIT: 0 tick complete (whether or not it acted), 1 the tick itself failed.

[CmdletBinding()]
param(
    [switch]$Force,
    [int]$WaitSeconds = 15,
    # Overrides the project root that the common library defaults to. The
    # scheduled task passes nothing and the default is used; the acceptance
    # tests pass a scratch root so they can drive the DECISION RULES (rule 1
    # no-op, rule 2 live-controller no-op, stale-lock restart) against synthetic
    # state without ever touching, starting or stopping the real pipeline. A
    # test that can only be run by racing the real controller is a test nobody
    # runs twice.
    [string]$Root = ''
)

$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'lib\JccWatchdogCommon.ps1')

if ($Root) { $script:JccRoot = $Root }

# The duplicate-case proxy environment trap is handled INSIDE the launcher now
# (Remove-JccDuplicateCaseProxyEnv in lib\JccWatchdogCommon.ps1). It used to be
# copied here, which guarded this one caller and left every other caller of
# Start-JccDetachedPython -- including the harness that measures it -- on the
# failing path. The pair list and the reasoning live next to the Start-Process
# call that cannot bind the duplicate.

$Root        = Get-JccRoot
$StatePath   = Join-Path $Root '.workbuddy\jcc_execution_state.json'
$LockPath    = Join-Path $Root '.workbuddy\locks\jcc-continuation.lock.json'
$LogDir      = Join-Path $Root '.workbuddy\scheduled\logs'
$CtrlLog     = Join-Path $LogDir 'continuation-controller.out.log'
$CtrlErr     = Join-Path $LogDir 'continuation-controller.err.log'
$AuditLog    = Join-Path $LogDir 'continuation-watchdog.jsonl'
$CtrlScript  = Join-Path $Root 'tools\jcc_continuation.py'
# NOT the service watchdog's `.workbuddy\maintenance.json`. That file means
# "hands off the SERVICE" and is raised by the soak, which the controller is
# itself running -- so honouring it here would mean a controller that died
# during the soak is never restarted, i.e. the watchdog would stop working
# exactly when it is most needed. This is a separate, operator-scoped flag.
$HoldPath    = Join-Path $Root '.workbuddy\operator_hold.json'

function Write-Tick {
    # NOT `$Pid`. PowerShell's `$PID` is a read-only automatic variable, and a
    # parameter named `$Pid` is not a local shadow -- binding it throws
    # "VariableNotWritable". So the FIRST tick of this watchdog failed, which
    # made the whole file inert: a watchdog that throws before it can decide
    # anything is indistinguishable from one with nothing to do, and it is
    # exactly the failure the acceptance suite exists to catch. Found by
    # tools/tests/test_continuation_infra.py, not by a parse check -- the file
    # has always been parseable.
    param([string]$Action, [string]$Detail, [int]$ProcessId = 0)
    $rec = [pscustomobject]@{
        at     = (Get-Date).ToString('o')
        tick   = 'continuation-watchdog'
        action = $Action
        detail = $Detail
        pid    = $ProcessId
    }
    Add-JccJsonLine -Path $AuditLog -Object $rec
    Write-Output ("[{0}] {1}: {2}" -f $rec.at, $Action, $Detail)
}

$state = Read-JccJson -Path $StatePath
$lock  = Read-JccJson -Path $LockPath

# --- Rule 0: an operator at the keyboard outranks the watchdog -----------------
# WHY A HOLD AND NOT "pretend the release is COMPLETE": the only way to silence
# this watchdog with the state file alone is to write runtime_state=COMPLETE and
# project_run=PASS, i.e. to make a false statement about the release in order to
# stop a script. That is the failure this project is built to refuse, so the
# silencing mechanism is a SEPARATE, EXPLICIT, EXPIRING flag that says exactly
# what is happening: a human is working on the platform right now.
#
# IT EXPIRES BY CONSTRUCTION. A hold with no readable expiry is IGNORED (with a
# WARN) rather than honoured, because the two failure modes are not symmetric: a
# hold that outlives its operator freezes the platform silently and forever --
# the watchdog would look exactly like a watchdog with nothing to do -- whereas
# advancing a little too early costs one wasted tick.
$hold = Read-JccJson -Path $HoldPath
if ((Get-JccProp -Object $hold -Name 'active') -eq $true) {
    $holdUntil = Get-JccProp -Object $hold -Name 'expires_at'
    $held = $false
    if ($holdUntil) {
        try { $held = ([datetime]::Parse([string]$holdUntil) -gt (Get-Date)) }
        catch { $held = $false }
    }
    if ($held) {
        Write-Tick -Action 'NOOP' -Detail ("operator hold active until {0} ({1}); not starting a controller" -f $holdUntil, (Get-JccProp -Object $hold -Name 'reason'))
        exit 0
    }
    $shown = '<unset>'
    if ($holdUntil) { $shown = [string]$holdUntil }
    Write-Tick -Action 'NOTE' -Detail ("operator hold is NOT in force (expires_at={0}); resuming" -f $shown)
}

# --- Rule 1: a finished release is never resurrected --------------------------
# Read through Get-JccProp: under StrictMode 2.0 a bare `$state.runtime_state`
# on a state file that lacks the field is a terminating error, and a watchdog
# that dies on a malformed state file is a watchdog that cannot report why.
$stateRuntime = Get-JccProp -Object $state -Name 'runtime_state'
$stateRun     = Get-JccProp -Object $state -Name 'project_run'
if (($stateRuntime -eq 'COMPLETE') -and ($stateRun -eq 'PASS')) {
    Write-Tick -Action 'NOOP' -Detail 'release is COMPLETE with project_run=PASS; watchdog stays idle'
    exit 0
}

# --- Rule 2: a live controller means there is nothing to do -------------------
if ($lock) {
    $lockPid = Get-JccPropInt -Object $lock -Name 'pid'
    $alive = Test-JccPidAlive -ProcessId $lockPid
    $fresh = $false
    $expires = Get-JccProp -Object $lock -Name 'expires_at'
    if ($expires) {
        try { $fresh = ([datetime]::Parse([string]$expires) -gt (Get-Date)) }
        catch { $fresh = $true }
    }
    if ($alive -and $fresh) {
        if ($Force) {
            Write-Tick -Action 'NOOP' -Detail ("controller pid {0} already live; -Force ignored for a live controller" -f $lockPid) -ProcessId $lockPid
        } else {
            Write-Tick -Action 'NOOP' -Detail ("controller pid {0} already live (lock fresh)" -f $lockPid) -ProcessId $lockPid
        }
        exit 0
    }
    Write-Tick -Action 'STALE' -Detail ("lock pid {0} alive={1} fresh={2}; starting a replacement" -f $lockPid, $alive, $fresh) -ProcessId $lockPid
}

# --- Rule 3: start a replacement ---------------------------------------------
if (-not (Test-Path -LiteralPath $CtrlScript -PathType Leaf)) {
    Write-Tick -Action 'ERROR' -Detail "controller script missing at $CtrlScript"
    exit 1
}
$python = Get-JccPython
if (-not $python) {
    Write-Tick -Action 'ERROR' -Detail 'no project venv interpreter found'
    exit 1
}

try {
    $started = Start-JccDetachedPython -PythonExe $python `
        -Arguments @($CtrlScript, 'run', '--resume') `
        -OutLog $CtrlLog -ErrLog $CtrlErr -WorkingDirectory $Root
} catch {
    Write-Tick -Action 'ERROR' -Detail ("Start-Process failed: {0}" -f $_.Exception.Message)
    exit 1
}
if (-not $started.ok) {
    Write-Tick -Action 'ERROR' -Detail ("controller did not start: {0}" -f $started.error)
    exit 1
}
Write-Tick -Action 'STARTED' -Detail 'controller launched detached (streams to files, no pipe)' -ProcessId $started.pid

# --- Rule 4: confirm a LIVE controller holds the lock, then get out of the way --
# The confirmation is "a live controller holds a fresh lock" -- NOT
# "Start-Process returned", because a python that dies on an import error also
# returns a pid.
#
# AND NOT "the lock names the pid Start-Process returned", which is what this rule
# originally required and which is UNREACHABLE ON THIS HOST. Measured: the project
# venv's python.exe is a redirector stub, so Start-Process reports the stub's pid
# while the process that actually runs jcc_contination gets a DIFFERENT pid of
# which the stub is the parent:
#
#     Start-JccDetachedPython pid = 1316   (the stub)
#     the script's own os.getpid()   = 24220, ppid = 1316   (the controller)
#
# The tick log shows the same thing live: "controller launched (pid 20924)" while
# the lock was taken by pid 11344. Requiring pid equality meant a controller could
# be started and never confirmed, and the tick still reported success -- the shape
# of a check that cannot fail. The lock is the thing the rest of the system agrees
# on, so the lock is what is confirmed, and a pid mismatch is recorded rather than
# hidden: it is a fact about this venv that any later liveness check must know.
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$confirmed = $false
$holderPid = 0
$lockObserved = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $l = Read-JccJson -Path $LockPath
    $lPid = Get-JccPropInt -Object $l -Name 'pid'
    $lExpires = Get-JccProp -Object $l -Name 'expires_at'
    $lFresh = $false
    if ($lExpires) {
        try { $lFresh = ([datetime]::Parse([string]$lExpires) -gt (Get-Date)) }
        catch { $lFresh = $true }
    }
    if (($lPid -gt 0) -and $lFresh -and (Test-JccPidAlive -ProcessId $lPid)) {
        $confirmed = $true
        $holderPid = $lPid
        if ($lPid -ne [int]$started.pid) { $lockObserved = $true }
        break
    }
    if (-not (Test-JccPidAlive -ProcessId $started.pid)) { break }
}

if ($confirmed) {
    $note = ("controller pid {0} holds the lock" -f $holderPid)
    if ($lockObserved) {
        $note += ("; Start-Process reported pid {0} -- the venv python.exe is a redirector, so the pid that runs the controller is its child" -f $started.pid)
    }
    Write-Tick -Action 'CONFIRMED' -Detail ("{0}; watchdog returning" -f $note) -ProcessId $holderPid
    exit 0
}
if (Test-JccPidAlive -ProcessId $started.pid) {
    Write-Tick -Action 'STARTED' -Detail ("the process Start-Process reported (pid {0}) is alive but no live controller holds the lock yet; the next tick will check again" -f $started.pid) -ProcessId $started.pid
    exit 0
}
Write-Tick -Action 'ERROR' -Detail ("controller pid {0} died before acquiring the lock; see {1}" -f $started.pid, $CtrlErr) -ProcessId $started.pid
exit 1

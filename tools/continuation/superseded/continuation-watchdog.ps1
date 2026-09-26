# JCC continuation watchdog.
#
# Effect: if the project is not yet complete and no live continuation controller
# is running, start one. If a live controller is already running, do nothing.
# If the project is already complete, stop scheduling permanently (the watchdog
# becomes a no-op so it never resurrects a finished pipeline).
#
# This is the §十二 / §十七 piece that makes "continuous execution" independent
# of any single conversation turn: when the controller is killed, the next
# watchdog tick (scheduled task, every 2 minutes, AtStartup) notices the dead
# lock and starts a replacement from the last persisted current_step.
#
# SAFETY / PowerShell pipe trap:
#   The controller is a long-lived child. If we let it inherit this script's
#   stdout pipe, PowerShell's pump would wait for EOF and the watchdog would
#   never return. We therefore start it detached and redirect its streams to a
#   log file.

$ErrorActionPreference = 'Stop'

# PowerShell 5.1 Start-Process builds a case-INSENSITIVE environment dictionary for
# the child process; if both "HTTP_PROXY" and "http_proxy" (same name, different
# case) are present it throws "An item with the same key has already been added."
# Drop the lower-case twin whenever the upper-case one also exists, so the
# controller can actually be launched. (This collision only appears when the
# watchdog is invoked from a shell that carries both proxy variables.)
foreach ($pair in @(@('http_proxy', 'HTTP_PROXY'), @('https_proxy', 'HTTPS_PROXY'),
                     @('ftp_proxy', 'FTP_PROXY'), @('all_proxy', 'ALL_PROXY'),
                     @('no_proxy', 'NO_PROXY'))) {
    $lo, $up = $pair
    if ((Test-Path -LiteralPath "Env:$lo") -and (Test-Path -LiteralPath "Env:$up")) {
        Remove-Item -LiteralPath "Env:$lo" -ErrorAction SilentlyContinue
    }
}

$ProjectRoot  = 'F:\jev'
$StatePath    = Join-Path $ProjectRoot '.workbuddy\jcc_execution_state.json'
$LockDir      = Join-Path $ProjectRoot '.workbuddy\locks'
$CtrlLockPath = Join-Path $LockDir 'jcc-continuation.lock.json'
$LogDir       = Join-Path $ProjectRoot '.workbuddy\scheduled\logs'
$CtrlLog      = Join-Path $LogDir 'continuation-controller.out.log'
$CtrlErr      = Join-Path $LogDir 'continuation-controller.err.log'

# Python interpreter resolution (prefer the project venv).
$PyCands = @(
    (Join-Path $ProjectRoot '.venv\Scripts\python.exe'),
    (Join-Path $ProjectRoot '.venv\bin\python.exe'),
    'python.exe'
)
$PythonExe = $null
foreach ($c in $PyCands) {
    if (Test-Path -LiteralPath $c -PathType Leaf) { $PythonExe = $c; break }
}
if (-not $PythonExe) {
    Write-Output "watchdog: ERROR no python interpreter found"
    exit 1
}

$CtrlScript = Join-Path $ProjectRoot 'tools\jcc_continuation.py'

function Read-Json([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json) }
    catch { return $null }
}

function Test-PidAlive([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return ($null -ne $p)
    }
    catch { return $false }
}

# --- Rule 1: project already complete -> watchdog is a permanent no-op. --------
$state = Read-Json $StatePath
if ($state -and $state.project_complete -eq $true) {
    Write-Output "watchdog: project already complete; nothing to start."
    exit 0
}

# --- Rule 2: a live controller is already running -> do nothing. ---------------
$lock = Read-Json $CtrlLockPath
if ($lock) {
    $pidAlive = Test-PidAlive ([int]$lock.pid)
    $notExpired = $false
    if ($lock.expires_at) {
        try { $notExpired = ([datetime]::Parse($lock.expires_at) -gt [datetime]::Now) } catch { $notExpired = $true }
    }
    if ($pidAlive -and $notExpired) {
        Write-Output ("watchdog: controller already running (pid {0}); nothing to do." -f $lock.pid)
        exit 0
    }
    # Stale lock for a dead pid/process: leave it; the controller refuses to
    # re-acquire while alive and reclaims on a dead pid, so starting is safe.
    Write-Output ("watchdog: found stale lock for pid {0} (alive={1}); will start a replacement." -f $lock.pid, $pidAlive)
}

# --- Rule 3: start a replacement controller. -----------------------------------
if (-not (Test-Path -LiteralPath $CtrlScript -PathType Leaf)) {
    Write-Output "watchdog: ERROR controller script missing at $CtrlScript"
    exit 1
}
if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# Start the controller detached (Start-Process does not tie it to this script's
# lifetime) and redirect its streams to FILES. We deliberately avoid piping the
# child's stdout through this script: a pipe whose reader exits (when the watchdog
# tick ends) would break and could kill the controller with a broken-pipe error.
# Writing to files keeps the controller robust across watchdog ticks.
$proc = Start-Process -FilePath $PythonExe `
    -ArgumentList ('"{0}" run' -f $CtrlScript) `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $CtrlLog `
    -RedirectStandardError $CtrlErr `
    -PassThru

if (-not $proc) {
    Write-Output "watchdog: ERROR controller process did not start"
    exit 1
}

# Give the controller a moment to acquire its lock, then confirm liveness.
Start-Sleep -Seconds 2
$alive = Test-PidAlive $proc.Id

# Confirm the controller actually wrote its lock (proof it is the real one, not a
# crash on startup).
$lock2 = Read-Json $CtrlLockPath
$lockOk = $lock2 -and (Test-PidAlive ([int]$lock2.pid)) -and (([int]$lock2.pid) -eq $proc.Id)

if ($alive -and $lockOk) {
    Write-Output ("started controller pid {0}" -f $proc.Id)
    exit 0
}

if ($alive) {
    # Started but lock not yet observed; still treat as success (lock may lag).
    Write-Output ("started controller pid {0} (lock not yet observed)" -f $proc.Id)
    exit 0
}

# Did not survive: surface the captured error tail for diagnosis.
Write-Output ("watchdog: ERROR controller pid {0} died on startup" -f $proc.Id)
try { $tail = (Get-Content -LiteralPath $CtrlErr -Tail 20 -ErrorAction SilentlyContinue) -join "`n" } catch { $tail = '' }
if ($tail) { Write-Output $tail }
exit 1

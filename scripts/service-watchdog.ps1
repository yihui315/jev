# JCC service watchdog -- one tick.
#
# WHAT IT GUARANTEES: while the release is in service and nothing says "hands
# off", the control plane on 127.0.0.1:4778 is answering /api/health.
#
# THE ONE RULE THAT MATTERS: recovery is only ever applied to a process this
# project can prove it owns. "Restart whatever is on 4778" is how a watchdog
# becomes an outage -- on a machine that also runs unrelated user servers, a
# blind restart kills a stranger's process and the watchdog reports success.
# So the order is always:
#
#     1. is it healthy?          -> NO-OP, stop here
#     2. who owns the listener?  -> not provably ours -> FAIL-CLOSED, record, exit
#     3. ours and dead?          -> stop.ps1, start.ps1, verify
#
# `taskkill /IM python.exe` appears nowhere in this file and must never appear:
# the host runs unrelated Python, and a watchdog is not entitled to guess.
#
# IT ALSO RESPECTS HANDS-OFF WINDOWS. A soak run or a gate run needs the service
# to be left alone; if the controller has raised a maintenance flag, or the
# release state is not READY, this tick does nothing at all. A watchdog that
# restarts the service during the 40-minute soak destroys the very measurements
# it is supposed to protect.
#
# EXIT: 0 healthy / deliberately idle, 2 restarted successfully,
#       3 FAIL-CLOSED (foreign owner), 4 restart budget exhausted, 1 tick failed.

[CmdletBinding()]
param(
    [switch]$DryRun,
    [int]$MaxRestartsPerHour = 6,
    # See continuation-watchdog.ps1 -Root: the acceptance tests drive the
    # decision rules against a scratch root instead of racing the real service.
    # -Port exists for the same reason: rule 2 (foreign occupancy -> FAIL-CLOSED)
    # can only be exercised against a port whose listener the test owns, and
    # binding the real 4778 to test it would be a test that breaks the product.
    [string]$Root = '',
    [int]$Port = 4778
)

$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'lib\JccWatchdogCommon.ps1')

if ($Root) { $script:JccRoot = $Root }

$Root        = Get-JccRoot
$RuntimeDir  = Join-Path $Root 'runtime\jcc'
$Ownership   = Join-Path $RuntimeDir 'jcc.ownership.json'
$PidFile     = Join-Path $RuntimeDir 'jcc.pid'
$ReleaseFile = Join-Path $Root '.workbuddy\release_state.json'
$MaintFile   = Join-Path $Root '.workbuddy\maintenance.json'
$LogDir      = Join-Path $Root '.workbuddy\scheduled\logs'
$AuditLog    = Join-Path $LogDir 'service-watchdog.jsonl'
$BudgetFile  = Join-Path $Root '.workbuddy\scheduled\service-watchdog-state.json'
$StopScript  = Join-Path $Root 'scripts\stop.ps1'
$StartScript = Join-Path $Root 'scripts\start.ps1'

function Write-Tick {
    param([string]$Action, [string]$Detail, [int]$ProcessId = 0, [string]$Level = 'INFO')
    $rec = [pscustomobject]@{
        at     = (Get-Date).ToString('o')
        tick   = 'service-watchdog'
        action = $Action
        level  = $Level
        detail = $Detail
        pid    = $ProcessId
    }
    if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    Add-JccJsonLine -Path $AuditLog -Object $rec
    Write-Output ("[{0}] {1} {2}: {3}" -f $rec.at, $Level, $Action, $Detail)
}

function Get-Health {
    try {
        $r = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/api/health" -f $Port) `
            -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        if ($r.StatusCode -eq 200) {
            return [pscustomobject]@{ ok = $true; status = 200; body = $r.Content }
        }
        return [pscustomobject]@{ ok = $false; status = $r.StatusCode; body = $r.Content }
    } catch {
        return [pscustomobject]@{ ok = $false; status = 0; body = $_.Exception.Message }
    }
}

function Get-ListenerPid {
    try {
        $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
             Select-Object -First 1
        if ($c) { return [int]$c.OwningProcess }
    } catch { }
    return 0
}

# --- Rule 0: hands-off windows -------------------------------------------------
$maint = Read-JccJson -Path $MaintFile
if ((Get-JccProp -Object $maint -Name 'active') -eq $true) {
    $expired = $false
    $maintExpires = Get-JccProp -Object $maint -Name 'expires_at'
    if ($maintExpires) {
        try { $expired = ([datetime]::Parse([string]$maintExpires) -le (Get-Date)) }
        catch { $expired = $false }
    }
    if (-not $expired) {
        Write-Tick -Action 'NOOP' -Detail ("maintenance window active ({0}); not touching the service" -f (Get-JccProp -Object $maint -Name 'reason'))
        exit 0
    }
    Write-Tick -Action 'NOTE' -Detail ("maintenance window expired at {0}; resuming monitoring" -f $maintExpires) -Level 'WARN'
}

$release = Read-JccJson -Path $ReleaseFile
$releaseState = Get-JccProp -Object $release -Name 'state'
if ($releaseState -ne 'READY') {
    $shown = '<unset>'
    if ($releaseState) { $shown = [string]$releaseState }
    Write-Tick -Action 'NOOP' -Detail ("release state is {0}; monitoring is reserved for READY" -f $shown)
    exit 0
}

# --- Rule 1: healthy is a NO-OP, and that is the common case -------------------
$health = Get-Health
$listenerPid = Get-ListenerPid
$own = Read-JccJson -Path $Ownership
$ownPid = Get-JccPropInt -Object $own -Name 'service_pid'

if ($health.ok) {
    $note = "health 200 on :$Port"
    if ($listenerPid -gt 0) { $note += " listener pid $listenerPid" }
    if ($ownPid -gt 0 -and $listenerPid -gt 0 -and $listenerPid -ne $ownPid) {
        Write-Tick -Action 'NOOP' -Detail ("$note -- WARNING listener pid != recorded owner $ownPid (ownership marker stale, service answering)") -Level 'WARN'
    } else {
        Write-Tick -Action 'NOOP' -Detail $note -ProcessId $listenerPid
    }
    exit 0
}

# --- Rule 2: ownership, before anything is stopped -----------------------------
if ($listenerPid -gt 0) {
    if ($ownPid -le 0) {
        # The message must state what was actually observed. "No ownership
        # marker exists" is FALSE when the marker is right there and simply
        # carries a pid that cannot be used (absent, non-numeric, or larger than
        # a 32-bit int -- Get-JccPropInt folds all three to 0). Both exit 3 and
        # both refuse to touch the process, so the decision is identical; but
        # this message is what a human is told to escalate on, and a FAIL-CLOSED
        # record that misdescribes its own evidence is a record that gets a
        # stranger's process killed on the next attempt.
        $why = 'no ownership marker exists'
        if ($own) {
            $why = ("the ownership marker exists but names no usable pid (service_pid={0})" -f (Get-JccProp -Object $own -Name 'service_pid'))
        }
        Write-Tick -Action 'FAIL_CLOSED' -Level 'ERROR' -ProcessId $listenerPid `
            -Detail ("port $Port is held by pid $listenerPid but $why; refusing to touch a process this project cannot prove it owns")
        exit 3
    }
    if ($listenerPid -ne $ownPid) {
        Write-Tick -Action 'FAIL_CLOSED' -Level 'ERROR' -ProcessId $listenerPid `
            -Detail ("port $Port is held by pid $listenerPid, recorded owner is $ownPid; FOREIGN OCCUPANCY -- refusing to kill or restart. Escalate to a human.")
        exit 3
    }
    # ours, holding the port, not answering: safe to recover
} else {
    if ($ownPid -gt 0 -and (Test-JccPidAlive -ProcessId $ownPid)) {
        Write-Tick -Action 'NOTE' -Level 'WARN' -ProcessId $ownPid `
            -Detail ("no listener on $Port while recorded owner pid $ownPid is alive; treating as a wedged instance and recovering")
    }
}

# --- Rule 3: budget ------------------------------------------------------------
$budget = Read-JccJson -Path $BudgetFile
$started = $null
$count = 0
if ($budget) {
    $started = Get-JccProp -Object $budget -Name 'window_start'
    $count = Get-JccPropInt -Object $budget -Name 'restart_count'
}
$windowOpen = $false
if ($started) {
    try { $windowOpen = ([datetime]::Parse([string]$started) -gt (Get-Date).AddHours(-1)) } catch { $windowOpen = $false }
}
if (-not $windowOpen) { $count = 0; $started = (Get-Date).ToString('o') }
if ($count -ge $MaxRestartsPerHour) {
    Write-Tick -Action 'BUDGET_EXCEEDED' -Level 'ERROR' `
        -Detail ("$count restart(s) in the current hour (limit $MaxRestartsPerHour); STOPPING recovery. A watchdog that restarts forever turns a crash loop into a denial of service.")
    exit 4
}

# --- Rule 4: recover, then verify ---------------------------------------------
Write-Tick -Action 'RECOVER' -Detail ("service unhealthy (health status {0}); owner pid {1}; restart {2}/{3} this hour" -f $health.status, $ownPid, ($count + 1), $MaxRestartsPerHour) -ProcessId $ownPid

if ($DryRun) {
    Write-Tick -Action 'DRYRUN' -Detail 'would run stop.ps1 then start.ps1; nothing was touched'
    exit 0
}

$psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $psExe)) { $psExe = 'powershell.exe' }

$stopOut = & $psExe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $StopScript 2>&1
$stopRc = $LASTEXITCODE
Write-Tick -Action 'STOP' -Detail ("stop.ps1 exit={0} :: {1}" -f $stopRc, (($stopOut | Out-String).Trim() -replace '\s+', ' ')) -ProcessId $ownPid
$startOut = & $psExe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $StartScript 2>&1
$startRc = $LASTEXITCODE
Write-Tick -Action 'START' -Detail ("start.ps1 exit={0} :: {1}" -f $startRc, (($startOut | Out-String).Trim() -replace '\s+', ' '))

$deadline = (Get-Date).AddSeconds(45)
$recovered = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
    if ((Get-Health).ok) { $recovered = $true; break }
}
$newPid = Get-ListenerPid
$newOwn = Read-JccJson -Path $Ownership
$newOwnPid = Get-JccPropInt -Object $newOwn -Name 'service_pid'

Write-JccJson -Path $BudgetFile -Object ([pscustomobject]@{
    window_start  = $started
    restart_count = ($count + 1)
    last_restart  = (Get-Date).ToString('o')
    last_old_pid  = $ownPid
    last_new_pid  = $newPid
    updated_at    = (Get-Date).ToString('o')
})

if ($recovered) {
    $changed = ($ownPid -gt 0 -and $newPid -gt 0 -and $newPid -ne $ownPid)
    Write-Tick -Action 'RECOVERED' -Detail ("health 200 after restart; old pid {0} -> new pid {1} (pid changed: {2}); ownership marker pid {3}" -f $ownPid, $newPid, $changed, $newOwnPid) -ProcessId $newPid
    exit 2
}
Write-Tick -Action 'RECOVERY_FAILED' -Level 'ERROR' -Detail ("service still unhealthy after stop.ps1 + start.ps1; old pid {0}, new pid {1}" -f $ownPid, $newPid) -ProcessId $newPid
exit 1

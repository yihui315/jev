# JCC runtime exclusive-lease (port-owner lease).
#
# WHY THIS FILE EXISTS. There is one port 4778 and one service behind it. During
# FINAL-08 / FINAL-09 / FINAL-10 / E-07 and any runtime acceptance there may be
# exactly ONE runtime owner: a second worker must be unable to start/stop/restart
# the service, or to probe-and-alter it, while a measurement is in flight. The
# old `runtime-4778.lock.json` was written by nothing and read by nothing (a
# grep found one reference in a superseded script), so there was no such rule.
#
# It is a HEARTBEAT LEASE, not an expiry-only file: the owner records its pid AND
# the real start time of that pid, and refreshes `heartbeat_at`. A recycled pid
# therefore cannot inherit the lease, and a lease whose heartbeat has stopped is
# reclaimable while a live, beating one is not.
#
# FAIL-SAFE DIRECTION (deliberate, and it is the whole point):
#   * "do not start a second owner" is the safety property, so a LIVE fresh owner
#     is refused;
#   * but a DEAD or STALE owner is reclaimed, because refusing to recover a lease
#     whose owner has crashed would wedge the release -- the opposite failure.
#   When the two conflict the rule prefers NOT BLOCKING THE LEGITIMATE OWNER.
#
# READ-ONLY VERIFICATION IS AVAILABLE AND WRITES NOTHING: `Test-JccRuntimeLeaseOwner`
# never touches the file, so an acceptance step can ask "is 4778 owned, by whom,
# and is the lease fresh?" OUTSIDE the measurement window without perturbing it.
#
# POWERSHELL 5.1 ONLY.

Set-StrictMode -Version 2.0

. (Join-Path (Split-Path -Parent $PSCommandPath) 'JccWatchdogCommon.ps1')

function Get-JccRuntimeLeasePath {
    param([Parameter(Mandatory=$true)][string]$Root,
          [Parameter(Mandatory=$true)][int]$Port)
    return (Join-Path $Root ('.workbuddy\locks\runtime-{0}.lock.json' -f $Port))
}

function Get-JccRuntimeLease {
    param([Parameter(Mandatory=$true)][string]$Root,
          [Parameter(Mandatory=$true)][int]$Port)
    return (Read-JccJson -Path (Get-JccRuntimeLeasePath -Root $Root -Port $Port))
}

function Test-JccRuntimeLeaseOwner {
    <# READ-ONLY. Decides whether a live, fresh owner holds the runtime lease.
       Returns a record; it never writes the lease file. #>
    param([Parameter(Mandatory=$true)][string]$Root,
          [Parameter(Mandatory=$true)][int]$Port,
          [int]$TtlSeconds = 180)
    $lease = Get-JccRuntimeLease -Root $Root -Port $Port
    if ($null -eq $lease) {
        return [pscustomobject]@{ held=$false; pid=0; alive=$false; fresh=$false;
                                  identity='unknown'; reason='no runtime lease exists' }
    }
    $owner = Get-JccPropInt -Object $lease -Name 'pid'
    $alive = Test-JccPidAlive -ProcessId $owner
    $fresh = Test-JccLeaseFresh -Lease $lease -HeartbeatTtlSeconds $TtlSeconds
    $identity = Test-JccLeaseIdentity -ProcessId $owner `
        -ExpectedStartTime (Get-JccProp -Object $lease -Name 'process_start_time')
    $held = $alive -and ($identity -ne 'mismatch') -and $fresh
    return [pscustomobject]@{ held=$held; pid=$owner; alive=$alive; fresh=$fresh;
                              identity=$identity;
                              reason=("owner pid {0} alive={1} identity={2} fresh={3}" -f $owner, $alive, $identity, $fresh) }
}

function Enter-JccRuntimeLease {
    <# Atomically become the single runtime owner. Refuses a live fresh owner. #>
    param([Parameter(Mandatory=$true)][string]$Root,
          [Parameter(Mandatory=$true)][int]$Port,
          [int]$TtlSeconds = 180,
          [int]$StaleAfterSeconds = 120,
          [switch]$Force)
    $path = Get-JccRuntimeLeasePath -Root $Root -Port $Port
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $cur = Test-JccRuntimeLeaseOwner -Root $Root -Port $Port -TtlSeconds $TtlSeconds
        if ($cur.held -and -not $Force) {
            return [pscustomobject]@{ ok=$false; owner=$cur.pid; path=$path
                                      reason=("a live owner holds the runtime lease (pid {0})" -f $cur.pid) }
        }
        try {
            $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::CreateNew,
                                         [System.IO.FileAccess]::Write,
                                         [System.IO.FileShare]::None)
            $rec = [pscustomobject]@{
                schema              = 'jcc.runtime-lease/1'
                port                = $Port
                pid                 = $PID
                process_start_time  = (Get-JccProcessStartTime -ProcessId $PID)
                acquired_at         = (Get-Date).ToString('o')
                heartbeat_at        = (Get-Date).ToString('o')
                expires_at          = ((Get-Date).AddSeconds($TtlSeconds)).ToString('o')
            }
            $bytes = $script:JccUtf8.GetBytes(($rec | ConvertTo-Json -Compress))
            $fs.Write($bytes, 0, $bytes.Length)
            $fs.Flush()
            $fs.Dispose()
            return [pscustomobject]@{ ok=$true; owner=$PID; path=$path; reason='acquired' }
        } catch [System.IO.IOException] {
            if ($cur.held) {
                return [pscustomobject]@{ ok=$false; owner=$cur.pid; path=$path
                                          reason=("a live owner holds the runtime lease (pid {0})" -f $cur.pid) }
            }
            # No LIVE FRESH owner according to the lease itself. Two sub-cases:
            #   * the file is EMPTY/unparsable and very recent -> another process is
            #     mid-create; refuse (do not start a second owner);
            #   * the file PARSES but its owner is dead or its heartbeat has stopped
            #     -> an abandoned lease; reclaim it (do not wedge the release).
            $existingLease = Read-JccJson -Path $path
            if ($null -eq $existingLease) {
                $recent = $false
                try {
                    $info = Get-Item -LiteralPath $path -ErrorAction Stop
                    if (((Get-Date) - $info.LastWriteTime).TotalSeconds -lt $StaleAfterSeconds) { $recent = $true }
                } catch { $recent = $false }
                if ($recent) {
                    return [pscustomobject]@{ ok=$false; owner=0; path=$path
                                              reason='another process is creating the runtime lease' }
                }
            }
            try { Remove-Item -LiteralPath $path -Force -ErrorAction Stop }
            catch { return [pscustomobject]@{ ok=$false; owner=0; path=$path
                                              reason='could not reclaim an abandoned runtime lease' } }
        }
    }
    return [pscustomobject]@{ ok=$false; owner=0; path=$path; reason='election attempts exhausted' }
}

function Update-JccRuntimeLease {
    <# Refresh the heartbeat. Only the recorded owner may; anyone else is refused. #>
    param([Parameter(Mandatory=$true)][string]$Root,
          [Parameter(Mandatory=$true)][int]$Port,
          [int]$TtlSeconds = 180)
    $path = Get-JccRuntimeLeasePath -Root $Root -Port $Port
    $lease = Read-JccJson -Path $path
    if ($null -eq $lease) { return $false }
    if ((Get-JccPropInt -Object $lease -Name 'pid') -ne $PID) { return $false }
    $lease.heartbeat_at = (Get-Date).ToString('o')
    $lease.expires_at = ((Get-Date).AddSeconds($TtlSeconds)).ToString('o')
    Write-JccJson -Path $path -Object $lease
    return $true
}

function Exit-JccRuntimeLease {
    <# Release only if THIS process owns it. Never removes another owner's lease. #>
    param([Parameter(Mandatory=$true)][string]$Root,
          [Parameter(Mandatory=$true)][int]$Port)
    $path = Get-JccRuntimeLeasePath -Root $Root -Port $Port
    $lease = Read-JccJson -Path $path
    if ($null -eq $lease) { return $true }
    if ((Get-JccPropInt -Object $lease -Name 'pid') -ne $PID) { return $false }
    try { Remove-Item -LiteralPath $path -Force -ErrorAction Stop; return $true } catch { return $false }
}

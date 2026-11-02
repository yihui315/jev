<#
    Register the GREEN runtime's two scheduled tasks. Windows PowerShell 5.1 ONLY.

      JEV-JCC-Stable-Runtime   AtStartup + AtLogOn
          The boot path. After a bugcheck or a hard hang this is what brings
          127.0.0.1:4778 back with nobody logged in. Runs WITHOUT -Watchdog, so a
          boot always attempts a restore.

      JEV-JCC-Stable-Health    every 2 minutes
          The steady-state path. Runs WITH -Watchdog, which adds the 10/30/60/
          120/300 s backoff and the NEEDS_OPERATOR record.

    BOTH run scripts\ensure-stable-runtime.ps1 -- the same audited action, so
    there is exactly one definition of "make GREEN correct".

    THERE IS NO REDIRECTION IN THE ACTION, ON PURPOSE -- NEITHER A FILE NOR A
    PIPE. Both are the same defect wearing different clothes, and both were
    measured on this host.

    A FILE redirect (`>> log 2>&1`) leaks: cmd.exe opens the target with
    FILE_SHARE_READ, the action LAUNCHES a detached service that outlives the
    tick, Windows handle inheritance hands the service a copy of that writable
    handle, and from that moment the file is locked against every later tick.
    cmd then cannot open its own redirect, never starts PowerShell at all, and
    the tick ends with LastTaskResult=1 having written NOTHING -- so the log's
    silence looks like health. Measured 2026-09-27: ticks at 22:37:41/22:39:39/
    22:41:41 produced no log growth (6211 -> 6211 bytes) and no audit line,
    while an append-open of that file failed under FileShare None, Read AND
    ReadWrite.

    A PIPE redirect is the mirror image: the inherited write end keeps the
    reader waiting forever, the task never leaves "Running", and
    MultipleInstances=IgnoreNew then suppresses every subsequent tick.

    So the action is a bare powershell.exe -File with no redirection. Durable
    output is the script's own job: ensure-stable-runtime.ps1 writes
    runtime\logs\stable-runtime-watchdog.log and runtime\logs\stable-runtime-
    boot.jsonl itself, with an explicit open/write/close per line, so that no
    handle can outlive a call and be inherited by anything. Removing cmd.exe
    also removes the legacy-quote hazard below.

    PRINCIPAL: SYSTEM when the host allows it, otherwise the current user at
    Highest. A per-user task does not exist until that user logs on, so AtStartup
    recovery silently never happens for one -- which is why SYSTEM is preferred
    and why the identity actually used is recorded rather than assumed. A real
    permission failure for SYSTEM is not a reason to stop: it is a reason to fall
    back and say so.
#>
[CmdletBinding()]
param(
    [int]$RepetitionMinutes = 2,
    [string]$Root = ''
)

$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'lib\JccCommon.ps1')
. (Join-Path (Split-Path -Parent $PSCommandPath) 'lib\JccWatchdogCommon.ps1')

if (-not $Root) { $Root = Get-JccRepoRoot }
$Root = (Resolve-Path -LiteralPath $Root).Path
$script:JccRoot = $Root

$scriptPath = Join-Path $Root 'scripts\ensure-stable-runtime.ps1'
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    Write-Output "install: ERROR the restore action is missing at $scriptPath"
    exit 1
}

$ps51 = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $ps51)) { $ps51 = 'powershell.exe' }

# Parse the action before registering it. Without a cmd.exe redirect there is no
# longer anything downstream that records a syntax error, so a script that cannot
# even be parsed must be refused HERE, at install time, where a human is looking.
# This closes the one hole that removing the redirect opens.
try {
    $tokens = $null; $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$errors)
    if ($errors -and $errors.Count -gt 0) {
        Write-Output ("install: ERROR {0} does not parse ({1} error(s)); refusing to register a tick nobody could debug" -f $scriptPath, $errors.Count)
        foreach ($e in $errors) { Write-Output ('  ' + $e.Message + ' @ line ' + $e.Extent.StartLineNumber) }
        exit 1
    }
} catch {
    Write-Output ("install: ERROR could not parse-check {0}: {1}" -f $scriptPath, $_.Exception.Message)
    exit 1
}

$logDir = Join-Path $Root 'runtime\logs'
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$durableLog = Join-Path $logDir 'stable-runtime-watchdog.log'

Import-Module ScheduledTasks -ErrorAction Stop

function Register-StableTask {
    param(
        [Parameter(Mandatory=$true)][string]$TaskName,
        [Parameter(Mandatory=$true)][string]$ActionArgs,
        [Parameter(Mandatory=$true)][string[]]$TriggerKinds,
        [switch]$Every
    )

    # NO cmd.exe, NO `>>`, NO `2>&1` -- see the header for the two measurements
    # (locked log / never-ending pipe) that make any redirection here a bug.
    # The script owns its durable output; the task just runs it.
    $action = New-ScheduledTaskAction -Execute $ps51 -Argument $ActionArgs -WorkingDirectory $Root

    $triggers = @()
    if ($TriggerKinds -contains 'AtLogOn')   { $triggers += New-ScheduledTaskTrigger -AtLogOn }
    if ($TriggerKinds -contains 'AtStartup') { $triggers += New-ScheduledTaskTrigger -AtStartup }
    if ($Every) {
        # New-ScheduledTaskTrigger has no -EveryMinutes; a repeating trigger is a
        # one-shot plus a repetition interval.
        $triggers += New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Minutes $RepetitionMinutes)
    }
    if ($triggers.Count -eq 0) { $triggers += New-ScheduledTaskTrigger -AtLogOn }

    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 12)

    $attempts = @(
        @{ user = 'SYSTEM'; logon = 'ServiceAccount'; level = 'Highest' },
        @{ user = ($env:USERDOMAIN + '\' + $env:USERNAME); logon = 'Interactive'; level = 'Highest' }
    )

    $used = $null
    $refusals = @()
    foreach ($a in $attempts) {
        try {
            $principal = New-ScheduledTaskPrincipal -UserId $a.user -LogonType $a.logon -RunLevel $a.level
            Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
                -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
            $used = $a
            break
        } catch {
            $refusals += ('{0} refused: {1}' -f $a.user, $_.Exception.Message)
        }
    }
    if ($null -eq $used) {
        return [pscustomobject]@{ ok = $false; identity = $null; refusals = $refusals }
    }
    return [pscustomobject]@{ ok = $true; identity = $used.user; logon_type = $used.logon
                              run_level = $used.level; refusals = $refusals }
}

$definitions = @(
    @{ name = 'JEV-JCC-Stable-Runtime'; args = ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $scriptPath);
       triggers = @('AtStartup', 'AtLogOn'); every = $false; record = 'stable-runtime-install.json' },
    @{ name = 'JEV-JCC-Stable-Health';  args = ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Watchdog' -f $scriptPath);
       triggers = @('AtStartup', 'AtLogOn'); every = $true;  record = 'stable-health-install.json' }
)

$records = Join-Path $Root '.workbuddy\scheduled'
if (-not (Test-Path -LiteralPath $records)) { New-Item -ItemType Directory -Path $records -Force | Out-Null }

$exit = 0
foreach ($d in $definitions) {
    $res = Register-StableTask -TaskName $d.name -ActionArgs $d.args -TriggerKinds $d.triggers -Every:$d.every
    if (-not $res.ok) {
        Write-Output ("install: ERROR could not register {0}" -f $d.name)
        foreach ($r in $res.refusals) { Write-Output ('  ' + $r) }
        $exit = 1
        continue
    }

    # Read the registration back through the three-state probe. `state` here is
    # never assumed, and if the probe could not answer that is recorded as
    # UNKNOWN rather than as a success.
    $report = Get-JccTaskReport -TaskName $d.name

    # Fail-closed: the registered action must contain NO redirection. If a future
    # edit reintroduces `>`/`|` here the task silently self-locks again, and the
    # symptom is not a crash -- it is a log that stops growing. Assert it while a
    # human is watching instead of discovering it from a silent port 4778.
    $registeredAction = ([string]$report.command + ' ' + [string]$report.arguments)
    if ($registeredAction -match '>>|2>&1|^\s*\|' -or $registeredAction -match '\|\s*$') {
        Write-Output ("install: ERROR {0} was registered WITH a redirection; that is the self-locking defect" -f $d.name)
        Write-Output ('  action: ' + $registeredAction)
        $exit = 1
        continue
    }

    $triggerText = @($d.triggers)
    if ($d.every) { $triggerText += ('Every {0} min' -f $RepetitionMinutes) }

    Write-JccJson -Path (Join-Path $records $d.record) -Object ([pscustomobject]@{
        task_name        = $d.name
        script           = $scriptPath
        action_arguments = $d.args
        triggers         = $triggerText
        principal        = $res.identity
        logon_type       = $res.logon_type
        run_level        = $res.run_level
        requested_system = $true
        principal_refusals = @($res.refusals)
        durable_log      = $durableLog
        durable_log_writer = 'scripts/ensure-stable-runtime.ps1 (open/write/close per line)'
        task_redirection = 'none -- a redirect handle is inherited by the detached service and locks the log against later ticks'
        state            = $report.state
        enabled          = $report.enabled
        verified         = $report.certain
        oracle           = $report.oracle
        verify_detail    = $report.detail
        verified_command = $report.command
        verified_args    = $report.arguments
        verified_repetition = $report.repetition
        last_run         = $report.last_run
        installed_at     = (Get-Date).ToString('o')
        installed_by     = ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
    })

    Write-Output ("install: {0} registered" -f $d.name)
    Write-Output ("  principal : {0} ({1}, {2})" -f $res.identity, $res.logon_type, $res.run_level)
    if ($res.refusals.Count) {
        Write-Output ("  fallback  : SYSTEM was refused -> " + ($res.refusals -join ' | '))
    }
    if (-not $report.certain) {
        Write-Output ("  verify    : UNKNOWN -- {0}" -f $report.detail)
        $exit = 1
    } else {
        Write-Output ("  verified  : state={0} enabled={1} repetition={2} oracle={3}" -f `
            $report.state, $report.enabled, $report.repetition, $report.oracle)
        Write-Output ("  action    : {0} {1}" -f $report.command, $report.arguments)
    }
}
exit $exit

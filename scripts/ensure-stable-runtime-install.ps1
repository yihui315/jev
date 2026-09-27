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

    WHY THE ACTION IS WRAPPED IN cmd.exe. `powershell.exe -File x.ps1` gives the
    script no durable output: if it fails to PARSE, nothing it wrote ever runs and
    the failure is invisible except as a LastTaskResult nobody reads. Wrapping in
    cmd.exe with `>> log 2>&1` means even a parse error lands in
    runtime\logs\stable-runtime-watchdog.log. Note this is a file redirect, never
    a pipe: an inherited pipe is what keeps a scheduled task "Running" forever.

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
$cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
if (-not (Test-Path -LiteralPath $cmdExe)) { $cmdExe = 'cmd.exe' }

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

    # cmd.exe /c "<ps51> <ActionArgs> >> "<log>" 2>&1"
    #
    # THE EXTRA OUTER QUOTE PAIR IS REQUIRED, NOT COSMETIC. cmd.exe's /c parsing
    # only PRESERVES quote characters when the command line holds EXACTLY two of
    # them; otherwise it falls back to the legacy rule "if the first character is
    # a quote, strip the leading quote and remove the LAST quote character on the
    # line". Written as '/c "<ps51>" args >> "<log>"' the line has four quotes, so
    # the legacy rule fired, the LAST quote (closing the log path) was removed,
    # the program path was left unterminated, and cmd failed with
    #
    #     LastTaskResult = 1
    #
    # while writing NOTHING: no log file, no audit line. Measured on this host
    # 2026-09-27 -- the task looked like it had run, and the only trace was a
    # number in the scheduler. Wrapping the whole command in one more pair makes
    # the first character a quote and the last character a quote, so the legacy
    # rule strips exactly those two and the inner quotes survive intact.
    $inner = '"{0}" {1} >> "{2}" 2>&1' -f $ps51, $ActionArgs, $durableLog
    $action = New-ScheduledTaskAction -Execute $cmdExe -Argument ('/c "' + $inner + '"') -WorkingDirectory $Root

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

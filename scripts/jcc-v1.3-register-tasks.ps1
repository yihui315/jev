<#
    JCC v1.3 PRODUCT EDITION -- Windows Task Scheduler durable runtime.

    REGISTER THE TWO v1.3 PRODUCT SURFACES AS DURABLE SCHEDULED TASKS.

        .\scripts\jcc-v1.3-register-tasks.ps1                     # PLAN ONLY (creates nothing)
        .\scripts\jcc-v1.3-register-tasks.ps1 -Apply              # apply to this host (elevated)
        .\scripts\jcc-v1.3-register-tasks.ps1 -Apply -WhatIf      # dry run of the apply path
        .\scripts\jcc-v1.3-register-tasks.ps1 -Apply -Unregister  # remove them again

    -WhatIf is the platform's own common parameter (this script declares
    SupportsShouldProcess), not a hand-rolled switch. A hand-rolled
    `[switch]$WhatIf` is UNREACHABLE on a [CmdletBinding()] script that does not
    declare SupportsShouldProcess: PowerShell owns the name and rejects it with
    NamedParameterNotFound before the parameter block is ever consulted. That was
    measured on this host (2026-09-29): the documented dry run simply errored out.
    Every host-modifying call below goes through $PSCmdlet.ShouldProcess, so
    -WhatIf is guaranteed to reach a real guard rather than merely parse.

    WHY THIS EXISTS
    ---------------
    A launcher script is not a runtime. scripts\jcc-v1.3.ps1 -Action up starts
    the surfaces and correctly reports on them, but the process it starts lives
    only as long as nothing reaps it, and it does not come back after a reboot or
    a logoff. The durable primitive on Windows is Task Scheduler: an AtStartup
    task, run as SYSTEM, with restart-on-failure and no execution time limit.

    So the DURABLE path is: Task Scheduler owns the two long-lived server
    processes; scripts\jcc-v1.3.ps1 remains the operator's control surface
    (up / down / restart / status / doctor / repair / mark).

    WHY THE TASKS RUN THE SERVERS DIRECTLY (and not "-Action up")
    ------------------------------------------------------------
    If a task's action were "jcc-v1.3.ps1 -Action up", that script would launch
    the server as a detached child and then EXIT 0. Task Scheduler would see a
    successfully completed action and its restart-on-failure setting would never
    fire -- the service could die and the task would still look "fine". Running
    the server itself as the task action is what makes the task's own liveness,
    restart count and reboot trigger mean anything.

    Consequence, stated plainly: a task-launched instance was not started by a
    launcher, so no launcher wrote its ownership marker. Adopt it explicitly:

        .\scripts\jcc-v1.3.ps1 -Action mark

    which identifies the current port holder by command line before writing the
    pid file and marker. Until then -Action status reports the listener as
    ANSWERING BUT UNVERIFIED (DEGRADED) rather than pretending it is ours.

    WHAT IT CREATES (local-only, 127.0.0.1)
    ---------------------------------------
      * JCC-v1.3-API       -> python -m jcc_core.run                       (port 4779)
      * JCC-v1.3-VisualUI  -> serve_visual.py --host 127.0.0.1 --port 4780 (port 4780)
                              uvicorn / ASGI -- NOT python http.server

    Both actions set JCC_CONFIG / PYTHONPATH EXPLICITLY, so neither depends on
    the ambient environment. Neither task touches the frozen GREEN service on
    4778, its pid/marker, its database or its process.

    WHY -Apply IS REQUIRED (this is not decoration)
    ----------------------------------------------
    MEASURED INCIDENT (2026-09-29, while building Wave 8). The first version of
    this script attempted registration whenever Task Scheduler *looked* usable,
    and then verified the result with `Get-ScheduledTask`. In the agent's sandbox
    that verification is BLIND: `Get-ScheduledTask` answers CimException
    ("the system cannot find the file specified") for EVERY task, including the
    pre-existing JEV-JCC-* tasks that demonstrably exist. The script therefore
    reported "reported success but re-reading the task found nothing" and
    PENDING_EXTERNAL_ACTION -- while, checked against the filesystem, the tasks
    had in fact been created. A verification step that cannot see anything does
    not turn a real write into a non-write; it only makes the report wrong.

    Two conclusions were baked into this revision:

      1. WRITING IS OPT-IN. Modifying a host's boot behaviour is a deliberate
         act, so it now requires an explicit -Apply. A bare run prints the plan
         and changes nothing. (The same incident also left two stray tasks on
         this host that this session could not remove, because schtasks.exe is
         blocked here and unregistering goes through the same blind provider.)

      2. THE VERDICT COMES FROM THE FILESYSTEM, NOT FROM Get-ScheduledTask. A
         task's store-of-record is the XML file at
         C:\Windows\System32\Tasks\<name>. That path is readable here, and it is
         what this script checks before and after every change. `Get-ScheduledTask`
         is consulted only as optional enrichment, and its blindness is reported
         as "provider blind in this session", never as "the task is absent".
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Apply,                     # REQUIRED to change this host; a bare run only prints the plan
    [switch]$Unregister,
    [string]$PrincipalUser = 'SYSTEM'   # SYSTEM = runs whether logged on or not, no password
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = $PSScriptRoot }
$Root = Split-Path -Parent $ScriptDir

. (Join-Path $ScriptDir 'lib\JccCommon.ps1')

$Paths = Get-JccV13Paths -Root $Root
$venvPy = $Paths.VenvPython
$config = $Paths.Config
$serveScript = $Paths.UiServeScript
$packages = Join-Path $Root 'packages'

$apiPort = Get-JccV13Port -Root $Root -Python ''
$uiPort = Get-JccV13UiPort
$xmlDir = Join-Path $Root 'artifacts\release\v1.3_product'

#: The task store-of-record. The CIM provider is a view of this; it is not the
#: truth, and in the agent sandbox it is not even a view.
$TASK_STORE = Join-Path $env:SystemRoot 'System32\Tasks'

function Get-V13TaskFilePath {
    param([string]$Name)
    return (Join-Path $TASK_STORE $Name)
}

function Test-V13TaskOnDisk {
    <#
        The ONLY registration verdict this script trusts.

        Returns 'PRESENT' | 'ABSENT'. Deliberately does not consult
        Get-ScheduledTask: a blind instrument must not be able to answer "absent"
        about a task that is on disk (that is exactly the false negative that
        produced the incident above).
    #>
    param([string]$Name)
    if (Test-Path -LiteralPath (Get-V13TaskFilePath -Name $Name)) { return 'PRESENT' }
    return 'ABSENT'
}

function Get-V13TaskProviderNote {
    param([string]$Name)
    try {
        $t = Get-ScheduledTask -TaskName $Name -ErrorAction Stop
        return ('provider says state=' + [string]$t.State)
    } catch {
        return ('provider BLIND in this session (' + $_.Exception.Message.Trim() + '); on-disk verdict used instead')
    }
}

function Write-Pending {
    param([string]$Why, [string[]]$Extra)
    Write-Output ''
    Write-Output 'PENDING_EXTERNAL_ACTION'
    Write-Output ('  reason : ' + $Why)
    foreach ($e in @($Extra)) { Write-Output ('  ' + $e) }
    Write-Output '  next   : run this script on the HOST, in an elevated PowerShell:'
    Write-Output ('             powershell -ExecutionPolicy Bypass -File ' + (Join-Path $ScriptDir 'jcc-v1.3-register-tasks.ps1') + ' -Apply')
    Write-Output '           then adopt the running instance once, so ownership is proven rather than assumed:'
    Write-Output '             .\scripts\jcc-v1.3.ps1 -Action mark'
    Write-Output '             .\scripts\jcc-v1.3.ps1 -Action status'
    Write-Output '  note   : this script does NOT fall back to schtasks.exe, to another shell, or to any'
    Write-Output '           other workaround. A blocked environment is a boundary, not a bug to route'
    Write-Output '           around, and a success claim this script did not observe would be a false PASS.'
}

function Fail {
    param([string]$Msg)
    Write-Output ('FAIL: ' + $Msg)
    exit 1
}

if (-not (Test-Path -LiteralPath $venvPy)) {
    Fail ('venv python not found: ' + $venvPy + '  (run .\scripts\bootstrap.ps1 first)')
}
if (-not (Test-Path -LiteralPath $config)) {
    Fail ('v1.3 config not found: ' + $config)
}
if (-not (Test-Path -LiteralPath $serveScript)) {
    Fail ('serve_visual.py not found: ' + $serveScript)
}
if (-not (Test-Path -LiteralPath (Join-Path $Paths.UiDist 'index.html'))) {
    Fail ('built UI not found: ' + (Join-Path $Paths.UiDist 'index.html') + '  (cd apps\command_center_3d; npm ci; npm run build)')
}

# --- task definitions ------------------------------------------------------- #
# NOTE the `set VAR=value && ...` form: a Task Scheduler action has no shell of
# its own, so the environment is established inside the same cmd.exe /c.
$apiArgs = ('/c "set JCC_CONFIG={0} && set PYTHONPATH={1} && ""{2}"" -m jcc_core.run"' `
    -f $config, $packages, $venvPy)
$uiArgs = ('/c "set PYTHONPATH={0} && ""{1}"" ""{2}"" --host 127.0.0.1 --port {3}"' `
    -f $packages, $venvPy, $serveScript, $uiPort)

$plan = @(
    @{ Name = 'JCC-v1.3-API';      Exe = 'cmd.exe'; Args = $apiArgs; Port = $apiPort; Desc = 'JCC v1.3 -- CORE API control plane (loopback only; isolated from GREEN 4778 and the v1.2 instance)' }
    @{ Name = 'JCC-v1.3-VisualUI'; Exe = 'cmd.exe'; Args = $uiArgs;  Port = $uiPort;  Desc = 'JCC v1.3 -- Visual Command Center SPA served by uvicorn (ASGI), NOT python http.server' }
)

Write-Output 'JCC v1.3 durable runtime -- scheduled task registration'
Write-Output ('repo root : ' + $Root)
Write-Output ('timestamp : ' + (Get-Date).ToString('o'))
Write-Output ('principal : ' + $PrincipalUser + '  (AtStartup, restart-on-failure x3, no execution time limit)')
Write-Output ('task store: ' + $TASK_STORE + '   (on-disk XML is the verdict; Get-ScheduledTask is not)')
Write-Output ''
foreach ($t in $plan) {
    Write-Output ('PLAN  ' + $t.Name + '  (port ' + $t.Port + ', local-only 127.0.0.1)  on-disk: ' + (Test-V13TaskOnDisk -Name $t.Name))
    Write-Output ('      exe : ' + $t.Exe)
    Write-Output ('      args: ' + $t.Args)
    Write-Output ('      desc: ' + $t.Desc)
}

# --- OPT-IN GATE ------------------------------------------------------------ #
if (-not $Apply) {
    Write-Output ''
    Write-Output 'PLAN ONLY -- this host was NOT modified.'
    Write-Output '  Registering these tasks adds two SYSTEM / AtStartup services to this machine, so'
    Write-Output '  the write is opt-in by design (see the MEASURED INCIDENT note at the top of this file).'
    Write-Output ''
    Write-Output '  to apply    : .\scripts\jcc-v1.3-register-tasks.ps1 -Apply        (elevated PowerShell)'
    Write-Output '  to remove   : .\scripts\jcc-v1.3-register-tasks.ps1 -Apply -Unregister'
    exit 0
}

# --- is the ScheduledTasks module even present? ----------------------------- #
$tsAvailable = $true
$tsWhy = ''
try {
    $null = Get-Command Register-ScheduledTask -ErrorAction Stop
} catch {
    $tsAvailable = $false
    $tsWhy = 'Register-ScheduledTask is not available in this session: ' + $_.Exception.Message
}
if ((-not $tsAvailable) -and (-not $WhatIfPreference)) { Write-Pending -Why $tsWhy; exit 3 }
if ((-not $tsAvailable) -and $WhatIfPreference) {
    Write-Output ''
    Write-Output 'note: Register-ScheduledTask is unavailable in this session, but -WhatIf changes nothing,'
    Write-Output '      so the plan above stands and the change set below is reported without being applied.'
}

# --- unregister ------------------------------------------------------------- #
if ($Unregister) {
    $stuck = @()
    foreach ($t in $plan) {
        if ((Test-V13TaskOnDisk -Name $t.Name) -eq 'ABSENT') {
            Write-Output ('ABSENT       ' + $t.Name + ' (nothing to remove)')
            continue
        }
        if (-not $PSCmdlet.ShouldProcess($t.Name, 'Unregister scheduled task')) {
            Write-Output ('WHATIF       ' + $t.Name + ' (still registered; -WhatIf)')
            continue
        }
        try {
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction Stop
        } catch {
            Write-Output ('UNREGISTER threw for ' + $t.Name + ': ' + $_.Exception.Message.Trim())
        }
        $after = Test-V13TaskOnDisk -Name $t.Name
        if ($after -eq 'ABSENT') {
            Write-Output ('UNREGISTERED ' + $t.Name + '  (verified: the task file is gone)')
        } else {
            Write-Output ('STILL PRESENT ' + $t.Name + '  (verified: the task file is still there)')
            $stuck += $t.Name
        }
    }
    Write-Output ''
    if ($WhatIfPreference) {
        Write-Output 'WHATIF: no task was removed; this host was NOT modified.'
        exit 0
    }
    if ($stuck.Count -gt 0) {
        Write-Output ('INCOMPLETE: could not remove ' + ($stuck -join ', ') + '.')
        Write-Output '  The ScheduledTasks provider is not reachable from this session, and this script will not'
        Write-Output '  delete task files by hand (that leaves the TaskCache index inconsistent).'
        Write-Output '  Remove them from an elevated HOST PowerShell:'
        foreach ($n in $stuck) { Write-Output ('    Unregister-ScheduledTask -TaskName ' + $n + ' -Confirm:$false') }
        Write-Output '    # No command-line fallback is printed here on purpose. schtasks is blacklisted in the'
        Write-Output '    # agent session, and the ScheduledTasks cmdlet above is the supported removal path.'
        exit 3
    }
    Write-Output 'DONE: the product runtime is no longer registered to start at boot.'
    exit 0
}

# --- build + register ------------------------------------------------------- #
$registered = 0
$skipped = 0
foreach ($t in $plan) {
    $before = Test-V13TaskOnDisk -Name $t.Name

    # The single host-modifying gate. -WhatIf makes ShouldProcess return false,
    # so the registration below is provably unreachable under a dry run.
    if (-not $PSCmdlet.ShouldProcess($t.Name, 'Register scheduled task (AtStartup, ' + $PrincipalUser + ')')) {
        Write-Output ('WHATIF      ' + $t.Name + '  (would be registered; nothing was written)')
        $skipped++
        continue
    }

    try {
        $action = New-ScheduledTaskAction -Execute $t.Exe -Argument $t.Args -WorkingDirectory $Root
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId $PrincipalUser -LogonType ServiceAccount -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Hours 0)   # 0 = no limit (long-lived service)

        $task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $t.Desc

        if ($before -eq 'PRESENT') {
            try { Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction Stop } catch {
                Write-Output ('note: could not pre-remove ' + $t.Name + ' (' + $_.Exception.Message.Trim() + '); registering with -Force instead')
            }
            Write-Output ('RE-REGISTER ' + $t.Name)
        } else {
            Write-Output ('REGISTER    ' + $t.Name)
        }
        Register-ScheduledTask -TaskName $t.Name -InputObject $task -Force -ErrorAction Stop | Out-Null
    } catch {
        Write-Pending -Why ('Register-ScheduledTask on ' + $t.Name + ' threw: ' + $_.Exception.Message.Trim()) `
                      -Extra @('effect : no further tasks were attempted.')
        exit 3
    }

    # VERDICT FROM THE STORE OF RECORD, not from the return value and not from
    # the CIM provider. `Register-ScheduledTask` returning without error is NOT
    # evidence that anything happened (measured on this host: it returns happily
    # in a session where the provider cannot see the result either way).
    $after = Test-V13TaskOnDisk -Name $t.Name
    if ($after -eq 'PRESENT') {
        Write-Output ('  OK -> ' + $t.Name + ' VERIFIED ON DISK at ' + (Get-V13TaskFilePath -Name $t.Name))
        Write-Output ('        ' + (Get-V13TaskProviderNote -Name $t.Name))
        $registered++

        try {
            if (-not (Test-Path -LiteralPath $xmlDir)) { New-Item -ItemType Directory -Path $xmlDir -Force | Out-Null }
            Export-ScheduledTask -TaskName $t.Name -ErrorAction Stop |
                Out-File -FilePath (Join-Path $xmlDir ($t.Name + '.task.xml')) -Encoding utf8
            Write-Output ('  exported -> ' + (Join-Path $xmlDir ($t.Name + '.task.xml')))
        } catch {
            Write-Output ('  (could not export XML for ' + $t.Name + ' via the provider: ' + $_.Exception.Message.Trim() + ')')
        }
    } else {
        Write-Pending -Why ('Register-ScheduledTask returned without error for ' + $t.Name + ', but the task file does NOT exist at ' + (Get-V13TaskFilePath -Name $t.Name) + ' -- it silently did nothing.') `
                      -Extra @('effect : the reported state is ABSENT. No success is claimed.')
        exit 3
    }
}

Write-Output ''
if ($WhatIfPreference) {
    Write-Output ('WHATIF: ' + $skipped + ' task(s) would be created; this host was NOT modified.')
    Write-Output '  re-run without -WhatIf (and with -Apply) to actually register them.'
    exit 0
}
Write-Output ('DURABLE RUNTIME REGISTERED: ' + $registered + '/' + $plan.Count + ' task(s), each verified present in ' + $TASK_STORE + '.')
Write-Output ('start now      : Start-ScheduledTask -TaskName "JCC-v1.3-API" ; Start-ScheduledTask -TaskName "JCC-v1.3-VisualUI"')
Write-Output ('adopt once     : .\scripts\jcc-v1.3.ps1 -Action mark')
Write-Output ('status         : .\scripts\jcc-v1.3.ps1 -Action status')
Write-Output ('rollback       : .\scripts\jcc-v1.3-register-tasks.ps1 -Apply -Unregister')
exit 0

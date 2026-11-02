<#
    JCC v1.2 VISUAL COMMAND CENTER -- Windows Task Scheduler durable runtime.

    REGISTER THE TWO v1.2 SERVICES AS DURABLE SCHEDULED TASKS.

    WHY THIS EXISTS
    ---------------
    The v1.2 surface is a SECOND, fully isolated JCC Core instance that runs
    ALONGSIDE the frozen GREEN service on 127.0.0.1:4778. It MUST NOT disturb
    GREEN: a different port (4779), a different runtime dir + SQLite DB
    (runtime\jcc-v1.2), the same loopback-only bind (127.0.0.1).

    The sandbox that runs this agent blocks schtasks.exe (Program Blacklist:
    "Permission denied" / "blocked by security policy"), so the task creation
    CANNOT be performed from inside the agent session. This script is the
    OPERATOR-RUN deliverable: run it on the HOST (where Task Scheduler works)
    to make the v1.2 services durable across reboots, with automatic restart
    on failure. The actual registration is recorded as PENDING_EXTERNAL_ACTION
    in the release evidence until an operator runs this on the host.

    WHAT IT CREATES (local-only, 127.0.0.1)
    ---------------------------------------
      * JCC-v1.2-API       -> python -m jcc_core.run          (port 4779)
      * JCC-v1.2-VisualUI  -> serve_visual.py --port 4780      (port 4780)

    Both actions set JCC_CONFIG / PYTHONPATH EXPLICITLY (no reliance on the
    ambient environment) and bind loopback only. Neither task touches GREEN
    4778, its pid/marker, its database, or its process.

    USAGE (on the host, as Administrator)
    -------------------------------------
      powershell -ExecutionPolicy Bypass -File scripts\jcc-v1.2-register-tasks.ps1
      # dry run (creates nothing, shows the plan):
      powershell -ExecutionPolicy Bypass -File scripts\jcc-v1.2-register-tasks.ps1 -WhatIf

    After registration the services are controlled exactly like before, via
    the same fail-closed ownership scripts:
      .\scripts\start-jcc-v1.2.ps1 / stop-jcc-v1.2.ps1 / status-jcc-v1.2.ps1
      .\scripts\start-jcc-v1.2-visual-ui.ps1
#>

[CmdletBinding()]
param(
    [switch]$WhatIf,
    [string]$PrincipalUser = 'SYSTEM'   # SYSTEM = runs whether logged on or not, no password
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = $PSScriptRoot }
$Root = Split-Path -Parent $ScriptDir

$venvPy     = Join-Path $Root '.venv\Scripts\python.exe'
$config     = Join-Path $Root 'config\jcc-v1.2.yaml'
$serveScript= Join-Path $Root 'apps\command_center_3d\serve_visual.py'
$packages   = Join-Path $Root 'packages'

function Fail {
    param([string]$Msg)
    Write-Error $Msg
    exit 1
}

if (-not (Test-Path -LiteralPath $venvPy)) {
    Fail ("venv python not found: " + $venvPy + "  (run .\scripts\bootstrap.ps1 first)")
}
if (-not (Test-Path -LiteralPath $config)) {
    Fail ("v1.2 config not found: " + $config)
}
if (-not (Test-Path -LiteralPath $serveScript)) {
    Fail ("serve_visual.py not found: " + $serveScript + "  (build it: cd apps\command_center_3d; npm ci; npm run build)")
}

# --- pre-flight: is Task Scheduler even reachable? (it is NOT inside the agent sandbox) ---
$tsAvailable = $true
try {
    $null = Get-Command Register-ScheduledTask -ErrorAction Stop
} catch {
    $tsAvailable = $false
}
if (-not $tsAvailable) {
    Write-Output ''
    Write-Output 'PENDING_EXTERNAL_ACTION: Register-ScheduledTask / schtasks.exe is not available in this'
    Write-Output 'session (sandbox Program Blacklist). Run this script on the HOST (as Administrator) to'
    Write-Output 'create the durable tasks. The registration definition below is the deliverable; nothing'
    Write-Output 'was created here. The LIVE runtime is already proven via the agent background runner.'
    Write-Output ''
}

# --- API task action (explicit env, loopback) --------------------------------- #
$apiCmd = 'cmd.exe'
$apiArgs = ('/c "set JCC_CONFIG={0} && set PYTHONPATH={1} && ""{2}"" -m jcc_core.run"' `
    -f $config, $packages, $venvPy)

# --- Visual UI task action (explicit env, loopback 4780) ---------------------- #
$uiCmd = 'cmd.exe'
$uiArgs = ('/c "set PYTHONPATH={0} && ""{1}"" ""{2}"" --host 127.0.0.1 --port 4780"' `
    -f $packages, $venvPy, $serveScript)

$plan = @(
    @{ Name = 'JCC-v1.2-API';       Exe = $apiCmd; Args = $apiArgs; Port = 4779; Desc = 'JCC v1.2 Visual Command Center -- API control plane (isolated from GREEN 4778)' }
    @{ Name = 'JCC-v1.2-VisualUI';  Exe = $uiCmd; Args = $uiArgs; Port = 4780; Desc = 'JCC v1.2 Visual Command Center -- static SPA server (isolated from GREEN 4778)' }
)

foreach ($t in $plan) {
    Write-Output ('PLAN  ' + $t.Name + '  (port ' + $t.Port + ', local-only 127.0.0.1)')
    Write-Output ('      exe : ' + $t.Exe)
    Write-Output ('      args: ' + $t.Args)
    Write-Output ('      desc: ' + $t.Desc)
}

if ($WhatIf) {
    Write-Output ''
    Write-Output 'WHATIF: no tasks were created. Re-run without -WhatIf on the HOST to register.'
    exit 0
}

if (-not $tsAvailable) {
    Write-Output ''
    Write-Output 'ABORTED: Task Scheduler not reachable in this session. PENDING_EXTERNAL_ACTION on host.'
    exit 3
}

# --- build + register each task ----------------------------------------------- #
foreach ($t in $plan) {
    $action = New-ScheduledTaskAction -Execute $t.Exe -Argument $t.Args -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId $PrincipalUser -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 0)   # 0 = no time limit (long-lived service)
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $t.Desc

    # Idempotent: re-register updates an existing task.
    $existing = $null
    try { $existing = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue } catch { }
    if ($existing) {
        Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction Stop
        Write-Output ('RE-REGISTER ' + $t.Name)
    } else {
        Write-Output ('REGISTER    ' + $t.Name)
    }
    Register-ScheduledTask -TaskName $t.Name -InputObject $task -Force -ErrorAction Stop | Out-Null

    # Also export the task XML for inspection / offline review.
    try {
        $xmlDir = Join-Path $Root 'artifacts\release\v1.2_visual'
        if (-not (Test-Path -LiteralPath $xmlDir)) { New-Item -ItemType Directory -Path $xmlDir -Force | Out-Null }
        Export-ScheduledTask -TaskName $t.Name -ErrorAction Stop | Out-File -FilePath (Join-Path $xmlDir ($t.Name + '.task.xml')) -Encoding utf8
    } catch {
        Write-Output ('  (could not export XML for ' + $t.Name + ': ' + $_.Exception.Message + ')')
    }
    Write-Output ('  OK -> ' + $t.Name + ' registered (AtStartup, restart-on-failure x3, no time limit)')
}

Write-Output ''
Write-Output 'DURABLE RUNTIME REGISTERED.'
Write-Output 'Verify on host:  Get-ScheduledTask -TaskName "JCC-v1.2-*"'
Write-Output 'Start now:      Start-ScheduledTask -TaskName "JCC-v1.2-API" ; Start-ScheduledTask -TaskName "JCC-v1.2-VisualUI"'
Write-Output 'Status:         .\scripts\status-jcc-v1.2.ps1'
exit 0

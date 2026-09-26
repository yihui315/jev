# JCC watchdog common helpers.
#
# WHY THIS FILE EXISTS. Two watchdogs are needed -- one that keeps the
# continuation controller alive, one that keeps the service alive -- and both
# need the same four primitives: read JSON defensively, ask whether a pid is
# alive, start a long-lived child WITHOUT capturing its stdout, and register a
# scheduled task. Duplicating those four is how the two watchdogs drift apart and
# one of them ends up with a subtly different liveness rule than the other.
#
# POWERSHELL 5.1 ONLY. The product's scripts are parsed by Windows PowerShell,
# and 5.1 has no `??`, no ternary operator and no `-ErrorAction` on some
# cmdlets. Nothing here may use a 7.x-only construct.

Set-StrictMode -Version 2.0

$script:JccRoot = 'F:\jev'

# ONE encoder, used by every writer in this file and by both watchdogs' audit
# logs. Windows PowerShell 5.1's `-Encoding UTF8` on Set-Content/Add-Content
# prepends a BOM, which makes the FIRST line of a .jsonl audit log unparseable
# by any standard reader (`json.loads` raises) and made a perfectly good
# ownership marker read as "no marker exists" -- a FAIL-CLOSED refusal to
# recover a service this project does own. Readers stay BOM-tolerant (see
# Read-JccJson) because a BOM from an older artifact must not blind a watchdog;
# writers simply never create one again.
$script:JccUtf8 = New-Object System.Text.UTF8Encoding($false)

function Get-JccRoot { return $script:JccRoot }

function Write-JccText {
    <# Overwrite a file as UTF-8 with NO byte order mark. #>
    param([Parameter(Mandatory=$true)][string]$Path, [Parameter(Mandatory=$true)][string]$Text)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Text, $script:JccUtf8)
}

function Add-JccJsonLine {
    <#
      Append one compact JSON object as a line of a .jsonl audit log.

      WHY A SHARED HELPER. The audit log is the only evidence that a watchdog
      ever ran, and it is read back line-by-line by the acceptance suite. Two
      copies of "append JSON" is how one watchdog ends up writing a BOM (or a
      pretty-printed multi-line record, which breaks the one-object-per-line
      contract just as thoroughly) and the other not. The first line of the
      continuation log carried a BOM once, and every reader of it raised.
    #>
    param([Parameter(Mandatory=$true)][string]$Path, [Parameter(Mandatory=$true)]$Object)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $line = ($Object | ConvertTo-Json -Compress -Depth 8) + "`r`n"
    [System.IO.File]::AppendAllText($Path, $line, $script:JccUtf8)
}

function Read-JccJson {
    <#
      Read a JSON file, returning $null instead of throwing.
      A watchdog that dies because a state file is half-written (the writer is
      mid `os.replace`) is a watchdog that cannot report the very incident it
      exists for. Every failure mode here is a $null, and the caller decides.

      UTF8 BOM is stripped explicitly. Windows PowerShell 5.1's `Set-Content
      -Encoding UTF8` writes a BOM and `ConvertFrom-Json` refuses it; the Python
      side already reads with `utf-8-sig`, and this is the matching half of that
      pair. A BOM turned a perfectly good ownership marker into "no marker
      exists" once, which is a FAIL-CLOSED exit in the service watchdog -- safe,
      but wrong, and wrong in the direction of refusing to recover a service the
      project does own.
    #>
    param([Parameter(Mandatory=$true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
        if ($raw.Length -gt 0 -and [int][char]$raw[0] -eq 0xFEFF) { $raw = $raw.Substring(1) }
        return ($raw | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return $null
    }
}

function Get-JccProp {
    <#
      Read a property off a JSON object WITHOUT throwing when it is absent.

      WHY THIS IS NOT PEDANTRY. `Set-StrictMode -Version 2.0` is in force for
      every script that dot-sources this file, and under strict mode 2.0 a
      reference to a non-existent property is a terminating error -- not $null.
      So `$state.runtime_state` on a state file written by an older schema, or
      on a hand-edited one, throws; the watchdog exits 1; and the pipeline that
      the watchdog exists to keep alive simply stops.

      A watchdog's whole job is to still be standing when something else is
      malformed, so every field it reads goes through here.

      `[AllowNull()]` IS THE POINT OF THE FUNCTION, NOT DECORATION. A parameter
      that is Mandatory without AllowNull() rejects $null at BINDING time --
      before the body runs -- so `if ($null -eq $Object) { return $Default }`
      was unreachable code and the helper could not be called with the absent
      object it exists for. Passing a state file that needs reading (i.e. one
      that is not there or is malformed, which is `$null` from Read-JccJson)
      threw "Cannot bind argument to parameter 'Object' because it is null"
      and the watchdog exited 1. Measured: that single missing attribute made
      every "no maintenance window / no release state yet" tick fail instead of
      doing nothing, which is precisely the idle case.
    #>
    param(
        [Parameter(Mandatory=$true)][AllowNull()]$Object,
        [Parameter(Mandatory=$true)][string]$Name,
        $Default = $null
    )
    if ($null -eq $Object) { return $Default }
    $prop = $Object.PSObject.Properties[$Name]
    if ($null -eq $prop) { return $Default }
    if ($null -eq $prop.Value) { return $Default }
    return $prop.Value
}

function Get-JccPropInt {
    param(
        [Parameter(Mandatory=$true)][AllowNull()]$Object,
        [Parameter(Mandatory=$true)][string]$Name,
        [int]$Default = 0
    )
    $v = Get-JccProp -Object $Object -Name $Name
    $parsed = 0
    if ($null -eq $v) { return $Default }
    if (-not [int]::TryParse([string]$v, [ref]$parsed)) { return $Default }
    return $parsed
}

function Write-JccJson {
    param([Parameter(Mandatory=$true)][string]$Path, [Parameter(Mandatory=$true)]$Object)
    # No BOM: see $script:JccUtf8. The Python side reads with utf-8-sig, so it
    # accepts either, but nothing new gets written with an invisible first byte.
    Write-JccText -Path $Path -Text ($Object | ConvertTo-Json -Depth 8)
}

function Test-JccPidAlive {
    <#
      Liveness by consulting the process table, not by a name lookup.

      `Get-Process -Name python` is what this must never become: it answers a
      different question (which pids are called python), and on a host that runs
      unrelated Python it answers it about someone else's process.

      WHY NOT OpenProcess, WHICH THIS USED TO USE. It had the dangerous failure
      direction. ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, ...)`` returns 0
      both for "no such pid" and for "the pid exists but this caller may not query
      it", and the old code turned every 0 into ``$false`` -- "dead". A watchdog
      that reads a live-but-unqueryable controller as dead starts a SECOND
      controller next to it. Measured on this host: pid 4 (SYSTEM) and a live
      child both open fine while elevated, but that is a property of the caller's
      token, not of the rule.

      THE CONTRACT HERE:
        * the pid is not in the process table at all -> ArgumentException -> DEAD
          (this is the only way this function says dead, and it is reliable)
        * the pid exists but cannot be opened      -> Win32Exception -> ALIVE
          (fail-safe: never start a duplicate over a process we merely cannot see)
        * the pid opens                             -> HasExited decides

      IT ALSO REMOVES A RUNTIME COMPILER. The previous implementation compiled a
      C# helper on first use in every PowerShell process, and a scheduled task
      starts a new process every two minutes. Measured: 716 ms for the compiled
      path against 49 ms here, for identical answers on {impossible pid, pid 4,
      this process}. On a host whose memory subsystem has been measured to corrupt
      data under memory-bandwidth load, spending a compiler per tick is both slow
      and the wrong kind of load.
    #>
    param([Parameter(Mandatory=$true)][AllowNull()]$ProcessId)
    $pidValue = 0
    if (-not [int]::TryParse([string]$ProcessId, [ref]$pidValue)) { return $false }
    if ($pidValue -le 0) { return $false }
    try {
        return -not ([System.Diagnostics.Process]::GetProcessById($pidValue)).HasExited
    } catch [System.ArgumentException] {
        return $false
    } catch [System.ComponentModel.Win32Exception] {
        return $true
    } catch {
        return $false
    }
}

function Get-JccPython {
    <# The project venv's interpreter, or $null. Never a bare `python`. #>
    $candidates = @(
        (Join-Path $script:JccRoot '.venv\Scripts\python.exe'),
        (Join-Path $script:JccRoot '.venv\bin\python'),
        (Join-Path $script:JccRoot '.venv\bin\python.exe')
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c -PathType Leaf) { return $c }
    }
    return $null
}

function Remove-JccDuplicateCaseProxyEnv {
    <#
      Remove the lower-case twin of any proxy variable that also exists in
      upper case, before the environment reaches Start-Process.

      PowerShell 5.1's Start-Process rejects an environment block that holds two
      names differing only in case:

          Start-Process : An item with the same key has already been added.
          Key in dictionary: 'HTTP_PROXY'  Key being added: 'http_proxy'
          FullyQualifiedErrorId : System.ArgumentException

      MEASURED 2026-09-26, thrown at line 239 of this file. The launching
      environment was an MSYS/Git-Bash shell, which exports both spellings.
      Windows looks a variable up case-insensitively, so the two names are ONE
      variable and dropping the twin loses no value -- it only removes the
      duplicate spelling that Start-Process cannot bind.

      IT LIVES HERE, NOT IN THE CALLER. It used to sit in
      scripts/continuation-watchdog.ps1, which protected exactly one caller. The
      FINAL-05 harness (tools/continuation/detached_survival.py) calls this
      function directly to measure the shipped launcher, hit the unguarded path,
      and got a ParameterBindingException instead of a measurement -- i.e. the
      guard's placement turned a real launch into a false "the launcher does not
      work" reading. The failure belongs to Start-Process inside this function,
      so the guard belongs inside this function; every caller now inherits it.
    #>
    foreach ($pair in @(@('http_proxy', 'HTTP_PROXY'), @('https_proxy', 'HTTPS_PROXY'),
                         @('ftp_proxy', 'FTP_PROXY'), @('all_proxy', 'ALL_PROXY'),
                         @('no_proxy', 'NO_PROXY'))) {
        $lo = $pair[0]; $up = $pair[1]
        if ((Test-Path -LiteralPath "Env:$lo") -and (Test-Path -LiteralPath "Env:$up")) {
            Remove-Item -LiteralPath "Env:$lo" -ErrorAction SilentlyContinue
        }
    }
}

function Start-JccDetachedPython {
    <#
      Start a long-lived python child with NO pipe to this process.

      This is the whole point of the function. If the child inherits this
      script's stdout, then:
        * PowerShell's output pump waits for the pipe to close, so the watchdog
          (or the scheduled task that hosts it) never returns -- the task stays
          "Running" forever and the next tick is skipped; and
        * when the tick does end, the reader goes away and the child takes a
          broken-pipe error mid-pipeline.

      So: redirect both streams to FILES and never use a pipeline to start it.
      Returns a small object rather than a Process object, because the only
      facts the caller needs are the pid and whether the file handles opened.
    #>
    param(
        [Parameter(Mandatory=$true)][string]$PythonExe,
        # [AllowEmptyCollection()] is required, not decorative: a Mandatory
        # [string[]] REJECTS @(), so a launcher that takes no arguments fails at
        # binding time, before the function body runs. `-ScriptArguments @()` did
        # exactly that in both install scripts -- the registration could never
        # have succeeded, and the only thing true about it was that the file
        # existed. Static checks cannot see this; only a call can.
        [Parameter(Mandatory=$true)][AllowEmptyCollection()][string[]]$Arguments,
        [Parameter(Mandatory=$true)][string]$OutLog,
        [Parameter(Mandatory=$true)][string]$ErrLog,
        [string]$WorkingDirectory = $script:JccRoot
    )
    # First statement, before any Start-Process: see the docstring on
    # Remove-JccDuplicateCaseProxyEnv for the measured failure this prevents.
    Remove-JccDuplicateCaseProxyEnv
    foreach ($p in @($OutLog, $ErrLog)) {
        $dir = Split-Path -Parent $p
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }
    $proc = Start-Process -FilePath $PythonExe -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -PassThru
    if ($null -eq $proc) {
        return [pscustomobject]@{ ok = $false; pid = 0; error = 'Start-Process returned nothing' }
    }
    return [pscustomobject]@{ ok = $true; pid = $proc.Id; error = $null }
}

function New-JccScheduledTask {
    <#
      Register (idempotently) a scheduled task that runs a PowerShell script.

      `schtasks.exe` is on this host's program blacklist, so the ScheduledTasks
      module is the only route. The principal is SYSTEM when asked for and
      permitted, otherwise the current user at Highest run level; the caller
      records which one was actually used, because "runs as SYSTEM" and "runs as
      Administrator at logon" have different availability (the latter does not
      exist until someone logs on).
    #>
    param(
        [Parameter(Mandatory=$true)][string]$TaskName,
        [Parameter(Mandatory=$true)][string]$ScriptPath,
        # See Start-JccDetachedPython: without [AllowEmptyCollection()] a
        # Mandatory [string[]] rejects @() at BINDING time, so the watchdog --
        # which takes no arguments -- could never be registered at all.
        [Parameter(Mandatory=$true)][AllowEmptyCollection()][string[]]$ScriptArguments,
        [Parameter(Mandatory=$true)][string[]]$TriggerRepetition,
        [int]$RepetitionMinutes = 2,
        [switch]$PreferSystem,
        # Build everything for real and stop before Register-ScheduledTask.
        #
        # WHY THIS SEAM EXISTS. The acceptance test has to prove that
        # `-ScriptArguments @()` BINDS (a Mandatory [string[]] refuses an empty
        # array before the body runs, which is how both install scripts could
        # never have registered anything while every static check passed) and
        # that the action, triggers and principal come out right. A test that
        # calls the real path registers a real task on the host -- measured: an
        # attempt to shadow Register-ScheduledTask with a global function did
        # NOT take effect (the ScheduledTasks module's own export won), the call
        # went through, and a task named JEV-JCC-Unit-Test appeared in the
        # scheduler and had to be unregistered by hand. So the seam is explicit:
        # the installer never passes -DryRun, and a test asserts that.
        [switch]$DryRun
    )
    Import-Module ScheduledTasks -ErrorAction Stop

    $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $psExe)) { $psExe = 'powershell.exe' }

    $argList = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', ('"{0}"' -f $ScriptPath)) + $ScriptArguments
    $action = New-ScheduledTaskAction -Execute $psExe -Argument ($argList -join ' ') `
        -WorkingDirectory $script:JccRoot

    $triggers = @()
    if ($TriggerRepetition -contains 'AtLogOn') {
        $triggers += New-ScheduledTaskTrigger -AtLogOn
    }
    if ($TriggerRepetition -contains 'AtStartup') {
        $triggers += New-ScheduledTaskTrigger -AtStartup
    }
    if ($TriggerRepetition -contains 'Every') {
        # A repeating trigger is built from a one-shot trigger plus a repetition
        # interval: New-ScheduledTaskTrigger has no -EveryMinutes switch.
        $t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Minutes $RepetitionMinutes)
        $triggers += $t
    }
    if ($triggers.Count -eq 0) { $triggers += New-ScheduledTaskTrigger -AtLogOn }

    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 12)

    $usedIdentity = $null
    $attempts = @()
    if ($PreferSystem) { $attempts += @{ user = 'SYSTEM'; logon = 'ServiceAccount'; level = 'Highest' } }
    $attempts += @{ user = ($env:USERDOMAIN + '\' + $env:USERNAME); logon = 'Interactive'; level = 'Highest' }

    if ($DryRun) {
        return [pscustomobject]@{
            ok         = $true
            identity   = $null
            logon_type = $null
            run_level  = $null
            dry_run    = $true
            definition = [pscustomobject]@{
                task_name          = $TaskName
                script_path        = $ScriptPath
                script_arguments   = @($ScriptArguments)
                execute            = $psExe
                arguments          = ($argList -join ' ')
                working_directory  = $script:JccRoot
                trigger_count      = @($triggers).Count
                repetition         = @(@($triggers) |
                                       Where-Object { $_.Repetition.Interval } |
                                       ForEach-Object { [string]$_.Repetition.Interval })
                multiple_instances = [string]$settings.MultipleInstances
                prefer_system      = [bool]$PreferSystem
                principals         = @($attempts | ForEach-Object {
                                        ('{0}/{1}/{2}' -f $_.user, $_.logon, $_.level) })
                registers          = $false
            }
        }
    }

    foreach ($a in $attempts) {
        try {
            $principal = New-ScheduledTaskPrincipal -UserId $a.user -LogonType $a.logon -RunLevel $a.level
            Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
                -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
            $usedIdentity = $a
            break
        } catch {
            $last = $_.Exception.Message
            Write-Output ("  principal {0} refused: {1}" -f $a.user, $last)
        }
    }
    if ($null -eq $usedIdentity) {
        return [pscustomobject]@{ ok = $false; identity = $null }
    }
    return [pscustomobject]@{
        ok = $true
        identity = $usedIdentity.user
        logon_type = $usedIdentity.logon
        run_level = $usedIdentity.level
    }
}

function Get-JccTaskReport {
    param([Parameter(Mandatory=$true)][string]$TaskName)
    Import-Module ScheduledTasks -ErrorAction Stop
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        return [pscustomobject]@{ exists = $false; enabled = $false; state = $null;
                                  last_result = $null; last_run = $null; next_run = $null;
                                  identity = $null }
    }
    $info = $null
    try { $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop } catch { $info = $null }
    return [pscustomobject]@{
        exists      = $true
        enabled     = ($task.State -ne 'Disabled')
        state       = [string]$task.State
        identity    = [string]$task.Principal.UserId
        last_result = $(if ($info) { $info.LastTaskResult } else { $null })
        last_run    = $(if ($info) { [string]$info.LastRunTime } else { $null })
        next_run    = $(if ($info) { [string]$info.NextRunTime } else { $null })
    }
}

<#
    JCC shared helpers for scripts/*.ps1.

    Owner: scripts/ (forge-3). Windows PowerShell 5.1 ONLY.
    Dot-source this file from each top-level script; it is never run directly.

    Contract notes (why this file exists):
      * The JCC service is ONE process: "<venv>\Scripts\python.exe -m jcc_core.run"
        bound to 127.0.0.1:<port> (config/jcc.yaml -> server.port, default 4778).
        packages/supervisor is a LIBRARY, not a second process -- it is wired into
        the core engine by packages/jcc_core/run.py (_wire_shadow) and only when
        p3.shadow.enabled is true (it is false in the committed config).
      * Every stop/restart decision goes through Test-JccOwnership. Nothing in
        this file, and no script that dot-sources it, ever signals a process it
        did not start and could not positively identify.
#>

function Get-JccRepoRoot {
    # This file is DOT-SOURCED from scripts\*.ps1, so inside this file
    # $PSScriptRoot is "<repo>\scripts\lib" (the directory of THIS file), NOT
    # "<repo>\scripts". The repo root is therefore TWO levels up from $PSScriptRoot:
    #   <repo>\scripts\lib  ->  <repo>\scripts  ->  <repo>
    # (If the file were ever imported as a module, $PSScriptRoot would still be
    # the lib dir, so this computation is correct either way.)
    return (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
}

function Get-JccPaths {
    param([string]$Root)

    if (-not $Root) { $Root = Get-JccRepoRoot }
    $runtime = Join-Path $Root 'runtime\jcc'
    $req = Join-Path (Join-Path $Root 'packages\jcc_core') 'requirements.txt'
    if (-not (Test-Path -LiteralPath $req)) {
        $alt = Join-Path $Root 'scripts\requirements.txt'
        if (Test-Path -LiteralPath $alt) { $req = $alt }
    }

    return @{
        Root            = $Root
        Runtime         = $runtime
        VenvPython      = Join-Path $Root '.venv\Scripts\python.exe'
        VenvPip         = Join-Path $Root '.venv\Scripts\pip.exe'
        Config          = Join-Path $Root 'config\jcc.yaml'
        EnvExample      = Join-Path $Root 'config\.env.example'
        EnvFile         = Join-Path $Root '.env'
        PidFile         = Join-Path $runtime 'jcc.pid'
        MarkerFile      = Join-Path $runtime 'jcc.ownership.json'
        ServerLog       = Join-Path $runtime 'server.log'
        ServerErrLog    = Join-Path $runtime 'server.err.log'
        Requirements    = $req
        GateRunner      = Join-Path (Join-Path $Root 'artifacts\acceptance') 'final_gate_run.py'
        CorePackage     = Join-Path $Root 'packages\jcc_core'
    }
}

function Get-JccV12Paths {
    <#
        The JCC v1.2 VISUAL COMMAND CENTER runs as a SECOND, fully isolated
        instance so the frozen GREEN service on 127.0.0.1:4778 is NEVER touched:

          * its own config (config\jcc-v1.2.yaml -> server.port 4779),
          * its own runtime dir (runtime\jcc-v1.2) with its own pid/marker/logs,
          * its own SQLite database (runtime\jcc-v1.2\jcc-v1.2.db).

        run.py writes its Python-side marker (JCC-marker) into the SAME directory
        as state_store.db_path, so an isolated db_path already gives the v1.2
        instance a separate JCC-marker too -- there is no cross-talk with the
        GREEN runtime\jcc\JCC-marker.

        Every ownership / health helper in this file takes a $Paths table, so the
        v1.2 launcher reuses Test-JccOwnership / Write-JccOwnershipMarker /
        Get-JccPortOwner / Invoke-JccHealth unchanged, pointed at these paths.
    #>
    param([string]$Root)

    if (-not $Root) { $Root = Get-JccRepoRoot }
    $runtime = Join-Path $Root 'runtime\jcc-v1.2'
    $req = Join-Path (Join-Path $Root 'packages\jcc_core') 'requirements.txt'
    if (-not (Test-Path -LiteralPath $req)) {
        $alt = Join-Path $Root 'scripts\requirements.txt'
        if (Test-Path -LiteralPath $alt) { $req = $alt }
    }

    return @{
        Root            = $Root
        Runtime         = $runtime
        VenvPython      = Join-Path $Root '.venv\Scripts\python.exe'
        VenvPip         = Join-Path $Root '.venv\Scripts\pip.exe'
        Config          = Join-Path $Root 'config\jcc-v1.2.yaml'
        EnvExample      = Join-Path $Root 'config\.env.example'
        EnvFile         = Join-Path $Root '.env'
        PidFile         = Join-Path $runtime 'jcc-v1.2.pid'
        MarkerFile      = Join-Path $runtime 'jcc-v1.2.ownership.json'
        ServerLog       = Join-Path $runtime 'server.log'
        ServerErrLog    = Join-Path $runtime 'server.err.log'
        Requirements    = $req
        GateRunner      = Join-Path (Join-Path $Root 'artifacts\acceptance') 'final_gate_run.py'
        CorePackage     = Join-Path $Root 'packages\jcc_core'
    }
}

function Get-JccV12Port {
    <#
        The v1.2 instance's port, read from config\jcc-v1.2.yaml. Mirrors
        Get-JccPort but targets the v1.2 config (server.port, normally 4779) so
        the launcher and the service can never disagree about which port is "v1.2".
        Falls back to a YAML line-parse, then to 4779.
    #>
    param([string]$Root, [string]$Python)

    $cfg = Join-Path $Root 'config\jcc-v1.2.yaml'
    if ($Python -and (Test-Path -LiteralPath $cfg)) {
        $code = @'
import sys, os
sys.path.insert(0, os.path.join(os.getcwd(), 'packages'))
os.environ['JCC_CONFIG'] = r'{0}'
from jcc_core.config import load_config
print(load_config().server.port)
'@ -f $cfg
        $cur = $null
        try { $cur = (Get-Location).Path } catch { $cur = $null }
        try {
            Set-Location -LiteralPath $Root -ErrorAction Stop
            $out = & $Python '-c' $code 2>$null
            $v = ([string]$out).Trim()
            if ($LASTEXITCODE -eq 0 -and $v -match '^\d+$') { return [int]$v }
        } catch {
            # fall through to the YAML fallback below
        } finally {
            if ($cur) {
                try { Set-Location -LiteralPath $cur -ErrorAction Stop } catch { }
            }
        }
    }

    if (Test-Path -LiteralPath $cfg) {
        $inServer = $false
        foreach ($line in Get-Content -LiteralPath $cfg) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:') {
                $key = $Matches[1]
                $inServer = ($key -eq 'server')
                continue
            }
            if ($inServer -and $line -match '^\s*port\s*:\s*(\d+)\s*$') {
                return [int]$Matches[1]
            }
        }
    }

    return 4779
}

function Get-JccPython {
    param([string]$Root)

    # 1. an already-provisioned virtualenv wins: that is the interpreter the
    #    rest of the platform was built and tested against.
    $venvPy = (Get-JccPaths -Root $Root).VenvPython
    if (Test-Path -LiteralPath $venvPy) { return $venvPy }

    # 2. python on PATH
    $c = Get-Command 'python' -ErrorAction SilentlyContinue
    if ($c) { return [string]$c.Source }

    # 3. the Python launcher
    $c = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($c) {
        try {
            $exe = & $c '-3' '-c' 'import sys; print(sys.executable)' 2>$null
            if ($exe) { return ([string]$exe).Trim() }
        } catch { }
    }

    # 4. python3
    $c = Get-Command 'python3' -ErrorAction SilentlyContinue
    if ($c) { return [string]$c.Source }

    return ''
}

function Get-JccPowershellExe {
    # The script that calls this usually runs under the same engine, but the
    # Windows productisation scripts are specified as 5.1; prefer 5.1 when the
    # running engine is newer so the child parses exactly like the gate expects.
    $cand = Join-Path $PSHOME 'powershell.exe'
    if (Test-Path -LiteralPath $cand) { return $cand }
    $c = Get-Command 'powershell' -ErrorAction SilentlyContinue
    if ($c) { return [string]$c.Source }
    return ''
}

function Invoke-JccScript {
    # Runs scripts\<Name>.ps1 and returns @{ Exit; Lines }.
    # Used by restart.ps1 so the stop/start pair is the SAME code a human runs.
    #
    # The script runs in THIS engine, not in a nested powershell.exe. Measured on
    # this host: a child ENGINE given start.ps1 never returns. The parent sat
    # inside '& $exe -File start.ps1' for more than 240 s with no output at all
    # while the service was up and answering /api/health 200. The same script
    # launched from a live engine with '&' returns in 8.3 s with the right exit
    # code; only the nested-engine form blocks.
    #
    # Arguments are handed over unquoted, through Invoke-Expression: with a
    # variable script path, '& $file '-Port' 4778' binds the literal "-Port" as a
    # value and the [int] parameter refuses it
    # (ParameterArgumentTransformationException, measured).
    #
    # Nothing here captures, reads or redirects: the script writes to this
    # process's own stdout/stderr, and the service's own logs are files owned by
    # the service itself (see start.ps1).
    param(
        [string]$Root,
        [string]$Name,
        [string[]]$ScriptArgs
    )

    $file = Join-Path (Join-Path $Root 'scripts') $Name
    $lines = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path -LiteralPath $file)) {
        return @{ Exit = -2; Lines = $lines.ToArray() }
    }

    $argText = ''
    if ($ScriptArgs) {
        foreach ($a in $ScriptArgs) { $argText = $argText + ' ' + [string]$a }
    }

    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Invoke-Expression -Command ('& "' + $file + '"' + $argText)
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev

    return @{ Exit = [int]$code; Lines = $lines.ToArray() }
}


function Test-JccPython {
    param([string]$Python)
    if (-not $Python) { return $false }
    try {
        $out = & $Python '-c' 'import sys; sys.stdout.write(''ok'')' 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        return ([string]$out).Trim() -eq 'ok'
    } catch { return $false }
}

function Get-JccPythonVersion {
    param([string]$Python)
    if (-not (Test-JccPython -Python $Python)) { return '( unusable )' }
    try {
        $out = & $Python '-c' 'import sys; sys.stdout.write(''%d.%d.%d'' % sys.version_info[:3])' 2>$null
        return ([string]$out).Trim()
    } catch { return '( unknown )' }
}

function Get-JccPort {
    param([string]$Root, [string]$Python)

    # Preferred: ask the configuration itself (packages/jcc_core/config.py).
    if ($Python) {
        $code = @'
import sys, os
sys.path.insert(0, os.path.join(os.getcwd(), 'packages'))
from jcc_core.config import load_config
print(load_config().server.port)
'@
        $cur = $null
        try { $cur = (Get-Location).Path } catch { $cur = $null }
        try {
            Set-Location -LiteralPath $Root -ErrorAction Stop
            $out = & $Python '-c' $code 2>$null
            $v = ([string]$out).Trim()
            if ($LASTEXITCODE -eq 0 -and $v -match '^\d+$') { return [int]$v }
        } catch {
            # fall through to the YAML fallback below
        } finally {
            if ($cur) {
                try { Set-Location -LiteralPath $cur -ErrorAction Stop } catch { }
            }
        }
    }

    # Fallback: read server.port out of config/jcc.yaml, server section only.
    $cfg = Join-Path $Root 'config\jcc.yaml'
    if (Test-Path -LiteralPath $cfg) {
        $inServer = $false
        foreach ($line in Get-Content -LiteralPath $cfg) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:') {
                $key = $Matches[1]
                $inServer = ($key -eq 'server')
                continue
            }
            if ($inServer -and $line -match '^\s*port\s*:\s*(\d+)\s*$') {
                return [int]$Matches[1]
            }
        }
    }

    return 4778
}

function Test-JccProcessAlive {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    return ($null -ne $p)
}

function Get-JccProcessInfo {
    param([int]$ProcessId)

    $blank = @{
        Alive       = $false
        Name        = ''
        CommandLine = ''
        CreationDate= ''
        Method      = 'none'
    }
    if ($ProcessId -le 0) { return $blank }

    try {
        $c = Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId = " + $ProcessId) -ErrorAction Stop
        if ($c) {
            return @{
                Alive       = $true
                Name        = [string]$c.Name
                CommandLine = [string]$c.CommandLine
                CreationDate= [string]$c.CreationDate
                Method      = 'Win32_Process'
            }
        }
    } catch { }

    $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($p) {
        return @{
            Alive       = $true
            Name        = [string]$p.ProcessName
            CommandLine = ''
            CreationDate= ''
            Method      = 'Get-Process'
        }
    }
    return $blank
}

function Get-JccPortOwner {
    param([int]$Port)

    try {
        $conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
        if ($conns.Count -gt 0) {
            $o = $conns[0]
            $opid = 0
            if ($o.OwningProcess) { $opid = [int]$o.OwningProcess }
            return @{ Pid = $opid; Method = 'Get-NetTCPConnection' }
        }
        return @{ Pid = 0; Method = 'none' }
    } catch { }

    # Fallback for a host without the TCP cmdlets: read-only netstat parse.
    try {
        $out = netstat -ano -p TCP 2>$null
        foreach ($line in $out) {
            if ($line -notmatch 'LISTENING') { continue }
            if ($line -notmatch '^\s*TCP\s+(\S+):(\d+)\s+(\S+):(\d+)\s+LISTENING\s+(\d+)') { continue }
            $lp = [int]$Matches[2]
            if ($lp -ne $Port) { continue }
            return @{ Pid = [int]$Matches[5]; Method = 'netstat' }
        }
    } catch { }

    return @{ Pid = 0; Method = 'none' }
}

function Get-JccMarker {
    param($Paths)
    if (-not $Paths -or -not $Paths.MarkerFile) { return $null }
    if (-not (Test-Path -LiteralPath $Paths.MarkerFile)) { return $null }
    try {
        $txt = Get-Content -LiteralPath $Paths.MarkerFile -Raw -ErrorAction Stop
        if (-not $txt) { return $null }
        return ($txt | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Get-JccPidFromFile {
    param($Paths)
    if (-not $Paths -or -not $Paths.PidFile) { return 0 }
    if (-not (Test-Path -LiteralPath $Paths.PidFile)) { return 0 }
    try {
        $v = (Get-Content -LiteralPath $Paths.PidFile -Raw -ErrorAction Stop).Trim()
        if ($v -match '^\d+$') { return [int]$v }
    } catch { }
    return 0
}

function Get-JccNormCmd {
    # Collapse whitespace so "python.exe  -m x" matches "python.exe -m x": cmd.exe
    # hands CreateProcess its own spacing and Win32 echoes it back verbatim.
    param([string]$Text)
    if (-not $Text) { return '' }
    return ([string]$Text).Replace('"', '') -replace '\s+', ' '
}

function Get-JccProcessChain {
    # Pids of $ProcessId and its ancestors, innermost first, at most $MaxDepth
    # deep. Used to prove that a pid is a DESCENDANT of the process we started.
    param([int]$ProcessId, [int]$MaxDepth = 12)
    $chain = New-Object System.Collections.Generic.List[int]
    if ($ProcessId -le 0) { return $chain.ToArray() }
    $seen = @{}
    $cur = $ProcessId
    for ($i = 0; $i -lt $MaxDepth; $i++) {
        if ($cur -le 0) { break }
        if ($seen.ContainsKey($cur)) { break }
        $seen[$cur] = $true
        $chain.Add($cur)
        $par = 0
        try {
            $c = Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId = " + $cur) -ErrorAction Stop
            if ($c) { $par = [int]$c.ParentProcessId } else { break }
        } catch { break }
        $cur = $par
    }
    return $chain.ToArray()
}

function Test-JccDescent {
    # Proves that $ServicePid is (a descendant of) a process whose command line
    # is the JCC service start line of THIS repo, and that $AncestorPid -- the
    # process the launching script actually started -- is on that chain.
    # This is the evidence that replaces "pid file and port owner happen to be
    # the same number".
    param(
        [int]$ServicePid,
        [string]$WantCommandLine,
        [string]$WantModule,
        [int]$AncestorPid = 0
    )

    $want = (Get-JccNormCmd -Text $WantCommandLine)
    $wantMod = (Get-JccNormCmd -Text $WantModule)

    $chain = Get-JccProcessChain -ProcessId $ServicePid
    if ($chain.Count -eq 0) {
        return @{ Ok = $false; Reason = 'no process chain could be built for pid ' + $ServicePid; Chain = @() }
    }

    $matched = $false
    foreach ($p in $chain) {
        $info = Get-JccProcessInfo -ProcessId $p
        if (-not $info.Alive) { continue }
        $cl = (Get-JccNormCmd -Text ([string]$info.CommandLine))
        if (-not $cl) { continue }
        if ($want -and $cl -like ('*' + $want + '*')) { $matched = $true; break }
        if ($wantMod -and $cl -like ('*' + $wantMod + '*')) { $matched = $true; break }
    }

    if (-not $matched) {
        return @{
            Ok    = $false
            Reason = 'no process in the ancestry of pid ' + $ServicePid + ' runs the JCC service start line'
            Chain = $chain
        }
    }
    if ($AncestorPid -gt 0 -and $chain -notcontains $AncestorPid) {
        return @{
            Ok     = $false
            Reason = 'pid ' + $ServicePid + ' is not a descendant of the launcher pid ' + $AncestorPid +
                     ' (chain: ' + ($chain -join '<') + ')'
            Chain  = $chain
        }
    }
    return @{ Ok = $true; Reason = 'pid ' + $ServicePid + ' descends from the JCC service start line'; Chain = $chain }
}

function Write-JccOwnershipMarker {
    param(
        $Paths,
        [int]$Port,
        [string]$PythonExe,
        [string]$Root,
        [int]$LauncherPid = 0,
        [int]$ServicePid = 0,
        [string]$Config = '',
        [string]$CreatedBy = ''
    )

    # `pid` / `service_pid` are the SERVICE process: the one that actually holds
    # the listening socket. `launcher_pid` is the process the launching script
    # started and that owns the redirected log handles.
    # -Config lets a second instance (e.g. v1.2 on port 4779) record its OWN
    #   config file in the marker instead of the default config\jcc.yaml; the
    #   default is unchanged so the GREEN launcher's behaviour is identical.
    # -CreatedBy records which launcher wrote the marker (default start.ps1).
    $configField = if ($Config) { $Config } else { (Join-Path $Root 'config\jcc.yaml') }
    $createdByField = if ($CreatedBy) { $CreatedBy } else { 'scripts/start.ps1' }
    $marker = [ordered]@{
        service        = 'jcc-core'
        pid            = (Get-JccPidFromFile -Paths $Paths)
        service_pid    = $ServicePid
        launcher_pid   = $LauncherPid
        start_time     = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        start_time_local = (Get-Date).ToString('o')
        script_version = '1.0.0'
        repo_path      = $Root
        cwd            = $Root
        venv_python    = $PythonExe
        command_line   = ('{0} -m jcc_core.run' -f $PythonExe)
        module         = 'jcc_core.run'
        port           = $Port
        host           = '127.0.0.1'
        config         = $configField
        platform       = 'windows'
        created_by     = $createdByField
    }

    if (-not (Test-Path -LiteralPath $Paths.Runtime)) {
        New-Item -ItemType Directory -Path $Paths.Runtime -Force | Out-Null
    }
    # UTF-8 without a byte order mark. `Set-Content -Encoding UTF8` on Windows
    # PowerShell 5.1 writes a BOM, and the ownership marker is read back by
    # Python (tools/continuation/soak_driver.py) with plain `utf-8`, where a BOM
    # makes json.load raise. The reader swallows that into `None`, so a marker
    # that exists and is perfectly correct was reported as "ownership marker
    # written: False" -- a false negative in the FINAL-08 soak evidence, in the
    # direction of "the release did not do what it did". Fixed at the writer;
    # the Python reader also accepts utf-8-sig so an older marker still reads.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Paths.MarkerFile,
        ($marker | ConvertTo-Json -Depth 6), $utf8NoBom)
    return $marker
}

function Test-JccOwnership {
    param($Paths, [int]$Port, [string]$Root)

    $marker = Get-JccMarker -Paths $Paths
    if (-not $marker) {
        return @{ Ok = $false; Reason = 'ownership marker missing (' + $Paths.MarkerFile + ')'; Marker = $null; Pid = 0; PortOwnerPid = 0; PortOwnerMethod = ''; ProcessAlive = $false }
    }

    $mpid = 0
    if ($marker.pid) { $mpid = [int]$marker.pid }
    $pidFile = Get-JccPidFromFile -Paths $Paths
    $portOwner = Get-JccPortOwner -Port $Port
    $info = Get-JccProcessInfo -ProcessId $mpid
    $alive = $info.Alive

    $base = @{
        Marker = $marker
        Pid = $mpid
        PortOwnerPid = $portOwner.Pid
        PortOwnerMethod = $portOwner.Method
        ProcessAlive = $alive
    }

    $spid = 0
    if ($marker.service_pid) { $spid = [int]$marker.service_pid }
    if ($spid -eq 0) { $spid = $mpid }

    if ($pidFile -ne $spid) {
        $base.Ok = $false
        $base.Reason = 'PID file (' + $pidFile + ') and ownership marker service pid (' + $spid + ') disagree'
        return $base
    }
    if (-not $alive) {
        $base.Ok = $false
        $base.Reason = 'recorded pid ' + $mpid + ' is not running'
        return $base
    }
    $mkRepo = ''
    if ($marker.repo_path) { $mkRepo = [string]$marker.repo_path }
    if ($mkRepo -and ($mkRepo -ne $Root)) {
        $base.Ok = $false
        $base.Reason = 'ownership marker was written for repo "' + $mkRepo + '", this repo is "' + $Root + '"'
        return $base
    }
    $cl = [string]$info.CommandLine
    if ($cl) {
        $norm = (Get-JccNormCmd -Text $cl)
        $want = (Get-JccNormCmd -Text ([string]$marker.command_line))
        if ($norm -notlike ('*' + $want + '*')) {
            # Accept any interpreter as long as the module is the one we started.
            if ($norm -notlike '*-m jcc_core.run*') {
                $base.Ok = $false
                $base.Reason = 'process command line is not the JCC service start line: ' + $cl
                return $base
            }
        }
    }

    # On Windows the service is the CHILD of the process that was launched: the
    # listening socket belongs to the child, the parent only owns the redirected
    # log handles. So "recorded pid == port owner" is not the only correct shape.
    # What must always hold: the port owner is the recorded service pid, or the
    # recorded launcher pid, and the service pid descends from a process that
    # really ran "python -m jcc_core.run" for this repo.
    $lp = $spid
    if ($lp -eq 0) { $lp = $mpid }
    $launcher = 0
    if ($marker.launcher_pid) { $launcher = [int]$marker.launcher_pid }

    if ($portOwner.Pid -ne 0 -and $portOwner.Pid -ne $lp -and $portOwner.Pid -ne $launcher) {
        $base.Ok = $false
        $base.Reason = 'port ' + $Port + ' is owned by foreign pid ' + $portOwner.Pid +
                       ' (recorded service pid ' + $lp + ', launcher pid ' + $launcher + ')'
        return $base
    }
    if ($portOwner.Pid -eq 0 -and $portOwner.Method -eq 'none') {
        $base.Ok = $false
        $base.Reason = 'no listener found on port ' + $Port + ' while the marker claims pid ' + $lp + ' is the service'
        return $base
    }

    $descent = Test-JccDescent -ServicePid $lp -WantCommandLine ([string]$marker.command_line) `
                               -WantModule 'jcc_core.run' -AncestorPid $launcher
    if (-not $descent.Ok) {
        $base.Ok = $false
        $base.Reason = 'ownership chain rejected: ' + $descent.Reason
        return $base
    }

    $base.Ok = $true
    $base.Reason = 'service pid ' + $lp + ' verified (marker + command line + port owner + ancestry)'
    return $base
}

function Invoke-JccHealth {
    param([string]$Url, [int]$TimeoutMs)

    $res = @{ Ok = $false; StatusCode = 0; Body = ''; Error = '' }

    $req = $null
    try {
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.Method = 'GET'
        $req.Timeout = [int][Math]::Max(1000, $TimeoutMs)
        $req.ReadWriteTimeout = [int][Math]::Max(1000, $TimeoutMs)
        $req.AllowAutoRedirect = $false
        $req.KeepAlive = $false
        # Loopback health checks must bypass any system HTTP proxy (this host
        # runs a v2rayN proxy that otherwise swallows 127.0.0.1:<port> and
        # returns a misleading HTTP 502 instead of "no listener"). An empty proxy
        # forces a direct connection to localhost.
        try { $req.Proxy = [System.Net.GlobalProxySelection]::GetEmptyWebProxy() } catch { }
    } catch {
        $res.Error = 'cannot build request: ' + $_.Exception.Message
        return $res
    }

    try {
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        $sr = $null
        try {
            $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
            $body = $sr.ReadToEnd()
        } finally {
            if ($sr) { $sr.Close() }
            $resp.Close()
        }
        $res.StatusCode = $code
        $res.Body = [string]$body
        if ($code -eq 200) { $res.Ok = $true }
    } catch [System.Net.WebException] {
        $we = $_.Exception
        $r = $null
        try { $r = $we.Response } catch { $r = $null }
        if ($r) {
            $sr = $null
            try {
                $sr = New-Object System.IO.StreamReader($r.GetResponseStream())
                $res.Body = [string]$sr.ReadToEnd()
            } catch {
                $res.Body = ''
            } finally {
                if ($sr) { $sr.Close() }
            }
            $res.StatusCode = [int]$r.StatusCode
            $res.Error = 'HTTP ' + $res.StatusCode
        } else {
            $res.Error = 'no response: ' + $we.Message
        }
    } catch {
        $res.Error = 'request failed: ' + $_.Exception.Message
    }

    return $res
}

function Read-JccHealthJson {
    param([string]$Body)
    if (-not $Body) { return $null }
    try {
        return ($Body | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Write-JccLine {
    param([string]$Text)
    Write-Output $Text
}

function Get-JccTail {
    param([string[]]$Lines, [int]$Count)

    if (-not $Lines) { return @() }
    $arr = @($Lines)
    if ($arr.Count -le $Count) { return $arr }
    $start = $arr.Count - $Count
    $out = New-Object System.Collections.Generic.List[string]
    for ($i = $start; $i -lt $arr.Count; $i++) { $out.Add([string]$arr[$i]) }
    return $out.ToArray()
}

function Get-JccElapsed {
    param([datetime]$Since, [datetime]$Now)
    if (-not $Since) { return '( unknown )' }
    if (-not $Now) { $Now = Get-Date }
    try {
        $d = $Now - $Since
        if ($d.TotalMilliseconds -lt 0) { $d = New-TimeSpan -Seconds 0 }

        # TRUNCATE, never round.
        #
        # MEASURED DEFECT (2026-09-27): this used to be
        #     $totalSec = [int]$d.TotalSeconds
        #     $hh = [int]($totalSec / 3600)
        #     $mm = [int](($totalSec % 3600) / 60)
        # In PowerShell a [int] cast applied to a DOUBLE does NOT truncate -- it
        # rounds (banker's rounding, via Convert.ToInt32). Measured on this host
        # with the live service: an elapsed time of 31033.3186547s -- plainly
        # 8h37m13s -- was printed as "09h37m13s", because 31033/3600 = 8.6203
        # rounds UP to 9. The same is true of the minutes: a remainder of 34m45s
        # (34.75) was printed as "35m".
        #
        # So the reading was silently ONE HOUR LONG whenever the minute
        # remainder passed 30 -- an uptime that jumps forward by an hour on its
        # own, which is precisely the "impossible uptime" this was reported for.
        # It also made the bug invisible in exactly the common case: a service
        # restarted within the last half hour shows the right hour, so a quick
        # check after a restart passes and the drift only appears later.
        #
        # [math]::Floor keeps it a truncation all the way down.
        $totalSec = [int][math]::Floor($d.TotalSeconds)
        $hh = [int][math]::Floor($totalSec / 3600)
        $mm = [int][math]::Floor(($totalSec % 3600) / 60)
        $ss = $totalSec % 60
        return ('{0:d2}h{1:d2}m{2:d2}s' -f $hh, $mm, $ss)
    } catch {
        return '( unknown )'
    }
}

function New-JccEnsureDir {
    param([string]$Path)
    if (-not $Path) { return }
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Get-JccUptimeReading {
    <#
        The one definition of "how long has the service been up".

        SOURCE PRECEDENCE (and the order is the point):
          1. Get-Process(<service pid>).StartTime  -- authoritative
          2. Win32_Process.CreationDate            -- cross-check ONLY
          3. the ownership marker                  -- provenance ONLY, never a source

        WHY THE MARKER MAY NOT BE A SOURCE. It is written by start.ps1 a moment
        AFTER the process already exists (measured here: process 22:18:17.865,
        marker 22:18:20.005 -- 2.14 s later), and it is a file: it can be copied,
        edited, or restored from a backup. Uptime is a property of the LIVE
        PROCESS, so a file must never be allowed to answer for it. The marker
        still proves identity ("this is the process we started"), which is what
        it is good at.

        WHY THE RESULT CANNOT CHANGE UP/DOWN. Warnings here are observations
        about the CLOCKS, not about the service. A drifted marker or a skewed CIM
        reading says "I do not trust my own instrumentation", never "the service
        is down"; misreading it as the latter would mark a healthy service DOWN.

        Pure: takes timestamps, returns a reading. Testable without a service.
    #>
    param(
        [System.Nullable[datetime]]$ProcessStart,
        [System.Nullable[datetime]]$CimCreation,
        [System.Nullable[datetime]]$MarkerStart,
        [System.Nullable[datetime]]$Now,
        [int]$SourceSkewWarnSec = 5,
        [int]$MarkerDriftWarnSec = 30
    )

    if (-not $Now) { $Now = Get-Date }

    $warnings = New-Object System.Collections.Generic.List[string]
    $uptime = '-'
    $source = 'none'

    if ($ProcessStart) {
        $uptime = (Get-JccElapsed -Since $ProcessStart -Now $Now)
        $source = 'process'
    } elseif ($CimCreation) {
        $uptime = (Get-JccElapsed -Since $CimCreation -Now $Now)
        $source = 'cim'
        $warnings.Add('UPTIME_SOURCE_WARNING: Get-Process(StartTime) was unavailable, so uptime was read from Win32_Process.CreationDate instead. That is the cross-check, not the authoritative source.')
    } else {
        $warnings.Add('UPTIME_SOURCE_WARNING: neither Get-Process(StartTime) nor Win32_Process.CreationDate could be read, so uptime is unknown. It is deliberately NOT taken from the ownership marker.')
    }

    $skew = $null
    if ($ProcessStart -and $CimCreation) {
        $skew = [math]::Abs((($CimCreation - $ProcessStart).TotalSeconds))
        if ($skew -gt $SourceSkewWarnSec) {
            $warnings.Add(('UPTIME_SOURCE_WARNING: Get-Process(StartTime) and Win32_Process.CreationDate disagree by {0:N1}s (tolerance {1}s). The process reading is authoritative and was used; the CIM reading is reported for diagnosis only.' -f $skew, $SourceSkewWarnSec))
        }
    }

    $drift = $null
    if ($ProcessStart -and $MarkerStart) {
        $drift = [math]::Abs((($MarkerStart - $ProcessStart).TotalSeconds))
        if ($drift -gt $MarkerDriftWarnSec) {
            $warnings.Add(('UPTIME_SOURCE_WARNING: the ownership marker says the service started {0:N1}s away from when the process was actually created (tolerance {1}s). The marker proves identity, not uptime, so uptime was NOT taken from it -- but the marker may be stale or from a different instance.' -f $drift, $MarkerDriftWarnSec))
        }
    }

    return @{
        Uptime       = $uptime
        Source       = $source
        Warnings     = @($warnings)
        SkewSec      = $skew
        MarkerDriftSec = $drift
        NowIso       = $Now.ToString('o')
        ProcessStartIso = $(if ($ProcessStart) { $ProcessStart.ToString('o') } else { '' })
        CimCreationIso  = $(if ($CimCreation)  { $CimCreation.ToString('o') }  else { '' })
        MarkerStartIso  = $(if ($MarkerStart)  { $MarkerStart.ToString('o') }  else { '' })
    }
}

function Get-JccNextHint {
    <#
        What to run next, given what we just measured.

        The old doctor printed "next: .\scripts\start.ps1" unconditionally, so a
        host where the service was already UP was told to start it again -- a
        hint that, taken literally, is a maintenance restart of a healthy
        instance. Worse, it was printed even when the port was held by a FOREIGN
        process, where start.ps1 would refuse (and where telling an operator to
        cover someone else's listener is actively unsafe).

        Pure: takes a port state, returns the hint. Testable without a service.
    #>
    param(
        [ValidateSet('OURS','FREE','FOREIGN')]
        [string]$PortState,
        [int]$ForeignPid = 0
    )

    switch ($PortState) {
        'OURS' {
            return 'none -- JCC is already running (verify with .\scripts\status.ps1)'
        }
        'FREE' {
            return '.\scripts\status.ps1  (or start it with .\scripts\start.ps1)'
        }
        'FOREIGN' {
            return ('do NOT run .\scripts\start.ps1 -- port is held by foreign pid ' + $ForeignPid + '; identify it first with .\scripts\status.ps1')
        }
    }
    return '.\scripts\status.ps1'
}

# NOTE: this file is DOT-SOURCED by every scripts\*.ps1, not imported as a module,
# so a top-level Export-ModuleMember would throw (InvalidOperationException: "can only
# be called from inside a module") under $ErrorActionPreference='Stop' and halt the
# calling script at the dot-source line. The functions defined above are already in
# the dot-sourcing script's scope, so no export is needed or permitted here.

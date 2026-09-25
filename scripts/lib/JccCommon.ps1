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
    # Runs scripts\<Name>.ps1 in a child engine and returns @{ Exit; Lines }.
    # Used by restart.ps1 so the stop/start pair is the SAME code a human runs.
    param(
        [string]$Root,
        [string]$Name,
        [string[]]$ScriptArgs
    )

    $exe = Get-JccPowershellExe
    $file = Join-Path (Join-Path $Root 'scripts') $Name
    $lines = New-Object System.Collections.Generic.List[string]
    $code = 0
    if (-not $exe) { return @{ Exit = -1; Lines = $lines.ToArray() } }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $raw = & $exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $file @ScriptArgs 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    foreach ($l in $raw) { $lines.Add([string]$l) }
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

function Write-JccOwnershipMarker {
    param($Paths, [int]$Port, [string]$PythonExe, [string]$Root)

    $marker = [ordered]@{
        service        = 'jcc-core'
        pid            = (Get-JccPidFromFile -Paths $Paths)
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
        config         = (Join-Path $Root 'config\jcc.yaml')
        platform       = 'windows'
        created_by     = 'scripts/start.ps1'
    }

    if (-not (Test-Path -LiteralPath $Paths.Runtime)) {
        New-Item -ItemType Directory -Path $Paths.Runtime -Force | Out-Null
    }
    ($marker | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $Paths.MarkerFile -Encoding UTF8
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

    if ($pidFile -ne $mpid) {
        $base.Ok = $false
        $base.Reason = 'PID file (' + $pidFile + ') and ownership marker pid (' + $mpid + ') disagree'
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
        $norm = $cl.Replace('"', '')
        $want = [string]$marker.command_line
        $wantNorm = $want.Replace('"', '')
        if ($norm -notlike ('*' + $wantNorm + '*')) {
            # Accept any interpreter as long as the module is the one we started.
            if ($norm -notlike '*-m jcc_core.run*') {
                $base.Ok = $false
                $base.Reason = 'process command line is not the JCC service start line: ' + $cl
                return $base
            }
        }
    }
    if ($portOwner.Pid -ne 0 -and $portOwner.Pid -ne $mpid) {
        $base.Ok = $false
        $base.Reason = 'port ' + $Port + ' is owned by foreign pid ' + $portOwner.Pid + ' (not ' + $mpid + ')'
        return $base
    }
    if ($portOwner.Pid -eq 0 -and $portOwner.Method -eq 'none') {
        $base.Ok = $false
        $base.Reason = 'no listener found on port ' + $Port + ' while the marker claims pid ' + $mpid + ' is the service'
        return $base
    }

    $base.Ok = $true
    $base.Reason = 'pid ' + $mpid + ' verified (marker + command line + port owner)'
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
    param([datetime]$Since)
    if (-not $Since) { return '( unknown )' }
    try {
        $d = (Get-Date) - $Since
        if ($d.TotalMilliseconds -lt 0) { $d = New-TimeSpan -Seconds 0 }
        $totalSec = [int]$d.TotalSeconds
        $hh = [int]($totalSec / 3600)
        $mm = [int](($totalSec % 3600) / 60)
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

# NOTE: this file is DOT-SOURCED by every scripts\*.ps1, not imported as a module,
# so a top-level Export-ModuleMember would throw (InvalidOperationException: "can only
# be called from inside a module") under $ErrorActionPreference='Stop' and halt the
# calling script at the dot-source line. The functions defined above are already in
# the dot-sourcing script's scope, so no export is needed or permitted here.

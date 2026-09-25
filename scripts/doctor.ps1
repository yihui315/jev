<#
    JCC local-first doctor (W-06). Windows PowerShell 5.1 ONLY.

    Genuine measurements, every line printed with a measured value:
      1. python on PATH
      2. venv present and its interpreter runs
      3. required packages importable by the venv interpreter
      4. config file present and load_config() parses it
      5. SQLite DB directory writable (created and removed a probe file)
      6. server port not stuck in a foreign hold
      7. free disk space on the repo volume
      8. acceptance gate runner present

    Exit code: 0 only when every check is PASS, non-zero otherwise.
#>

[CmdletBinding()]
param(
    [int]$MinFreeGb = 1
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccPaths -Root $Root

$checks = New-Object System.Collections.Generic.List[object]
$pass = 0
$fail = 0

function Add-Check {
    param([string]$Id, [string]$Name, [string]$Status, [string]$Measured)
    $script:checks.Add(@{ Id = $Id; Name = $Name; Status = $Status; Measured = $Measured })
    if ($Status -eq 'PASS') { $script:pass++ } else { $script:fail++ }
    Write-Output ('  [' + $Status + '] ' + $Id + ' ' + $Name)
    Write-Output ('          measured: ' + $Measured)
}

function Out-Title {
    param([string]$Text)
    Write-Output ('')
    Write-Output $Text
    Write-Output ('--------------------------------------------' )
}

Write-Output ('JCC doctor (W-06)')
Write-Output ('repo root : ' + $Root)
Write-Output ('timestamp : ' + (Get-Date).ToString('o'))
Write-Output ('host      : ' + $env:COMPUTERNAME + ' / user ' + $env:USERNAME)

# 1 ----------------------------------------------------------------------- #
Out-Title '1. python on PATH'
$pathPython = ''
foreach ($n in @('python', 'py', 'python3')) {
    $c = Get-Command $n -ErrorAction SilentlyContinue
    if ($c) { $pathPython = ([string]$c.Source); break }
}
if ($pathPython) {
    Add-Check -Id 'D-1' -Name 'python on PATH' -Status 'PASS' -Detail ($pathPython)
} else {
    Add-Check -Id 'D-1' -Name 'python on PATH' -Status 'FAIL' -Detail 'no python/py/python3 found on PATH (bootstrap can still run if .venv exists)'
}

# 2 ----------------------------------------------------------------------- #
Out-Title '2. virtualenv'
if (Test-Path -LiteralPath $Paths.VenvPython) {
    $pyver = Get-JccPythonVersion -Python $Paths.VenvPython
    if (Test-JccPython -Python $Paths.VenvPython) {
        Add-Check -Id 'D-2' -Name 'venv interpreter runs' -Status 'PASS' -Detail ($Paths.VenvPython + ' -> python ' + $pyver)
    } else {
        Add-Check -Id 'D-2' -Name 'venv interpreter runs' -Status 'FAIL' -Detail ($Paths.VenvPython + ' present but "python -c ..." did not complete (python ' + $pyver + ')')
    }
} else {
    Add-Check -Id 'D-2' -Name 'venv interpreter runs' -Status 'FAIL' -Detail ('missing ' + $Paths.VenvPython + ' -- run .\scripts\bootstrap.ps1')
}

$python = $Paths.VenvPython
if (-not (Test-Path -LiteralPath $Paths.VenvPython)) { $python = $pathPython }

# 3 ----------------------------------------------------------------------- #
Out-Title '3. required packages importable'
$probe = @'
import fastapi, uvicorn, pydantic, yaml, httpx, pytest
print('all-imports-ok')
'@
if ($python) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = & $python '-c' $probe 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    $ok = ($code -eq 0) -and (([string]$out) -match 'all-imports-ok')
    if ($ok) {
        Add-Check -Id 'D-3' -Name 'runtime packages importable' -Status 'PASS' -Detail ('fastapi+uvicorn+pydantic+pyyaml+httpx+pytest imported by ' + $python + ' (exit 0)')
    } else {
        Add-Check -Id 'D-3' -Name 'runtime packages importable' -Status 'FAIL' -Detail ('import probe exit ' + $code + ' in ' + $python + ': ' + ([string]$out).Trim())
    }
} else {
    Add-Check -Id 'D-3' -Name 'runtime packages importable' -Status 'FAIL' -Detail 'no interpreter available to test'
}

# 4 ----------------------------------------------------------------------- #
Out-Title '4. configuration'
if (Test-Path -LiteralPath $Paths.Config) {
    Add-Check -Id 'D-4a' -Name 'config file present' -Status 'PASS' -Detail ('size ' + (Get-Item -LiteralPath $Paths.Config).Length + ' bytes at ' + $Paths.Config)
} else {
    Add-Check -Id 'D-4a' -Name 'config file present' -Status 'FAIL' -Detail ('missing ' + $Paths.Config)
}

if ($python -and (Test-Path -LiteralPath $Paths.Config)) {
    $cfgCode = @'
import sys, os
sys.path.insert(0, os.path.join(os.getcwd(), 'packages'))
from jcc_core.config import load_config
c = load_config()
print('%s|%s|%s' % (c.server.host, c.server.port, c.state_store_db_path))
'@
    $cur = (Get-Location).Path
    try {
        Set-Location -LiteralPath $Root -ErrorAction Stop
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $out = & $python '-c' $cfgCode 2>&1
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prev
    } finally {
        Set-Location -LiteralPath $cur -ErrorAction SilentlyContinue
    }
    if ($code -eq 0) {
        $parts = ([string]$out).Trim().Split('|')
        Add-Check -Id 'D-4b' -Name 'config parses (load_config)' -Status 'PASS' -Detail ('host=' + $parts[0] + ' port=' + $parts[1] + ' db=' + $parts[2])
    } else {
        Add-Check -Id 'D-4b' -Name 'config parses (load_config)' -Status 'FAIL' -Detail ('load_config() exit ' + $code + ': ' + ([string]$out).Trim())
    }
} else {
    Add-Check -Id 'D-4b' -Name 'config parses (load_config)' -Status 'FAIL' -Detail 'skipped: no interpreter'
}

# 5 ----------------------------------------------------------------------- #
Out-Title '5. SQLite database writable'
$dbDir = Split-Path -Parent (Join-Path $Root 'runtime\jcc\jcc.db')
$probeFile = Join-Path $dbDir 'doctor-write-probe.tmp'
try {
    New-JccEnsureDir -Path $dbDir | Out-Null
    $existsBefore = Test-Path -LiteralPath $probeFile
    Set-Content -LiteralPath $probeFile -Value ('jcc-doctor ' + (Get-Date).ToString('o')) -Encoding ASCII
    $bytes = (Get-Item -LiteralPath $probeFile).Length
    Remove-Item -LiteralPath $probeFile -Force -ErrorAction Stop
    Add-Check -Id 'D-5' -Name 'DB directory writable' -Status 'PASS' -Detail ('created ' + $probeFile + ' (' + $bytes + ' bytes), wrote and removed it; dir ' + $dbDir)
} catch {
    $why = $_.Exception.Message
    Add-Check -Id 'D-5' -Name 'DB directory writable' -Status 'FAIL' -Detail ('probe failed: ' + $why + ' (dir ' + $dbDir + ', probe pre-existed: ' + $existsBefore + ')')
}

# 6 ----------------------------------------------------------------------- #
Out-Title '6. server port ownership'
$port = Get-JccPort -Root $Root -Python $python
$owner = Get-JccPortOwner -Port $port
if ($owner.Pid -eq 0) {
    Add-Check -Id 'D-6' -Name 'port ' + $port + ' not in foreign hold' -Status 'PASS' -Detail ('no LISTENING socket (detected via ' + $owner.Method + ')')
} else {
    $pidFilePid = Get-JccPidFromFile -Paths $Paths
    $own = Test-JccOwnership -Paths $Paths -Port $port -Root $Root
    if ($own.Ok) {
        Add-Check -Id 'D-6' -Name 'port ' + $port + ' hold' -Status 'PASS' -Detail ('listener owned by OUR pid ' + $owner.Pid + ' via ' + $owner.Method + '; verification: ' + $own.Reason)
    } else {
        $info = Get-JccProcessInfo -ProcessId $owner.Pid
        Add-Check -Id 'D-6' -Name 'port ' + $port + ' not in foreign hold' -Status 'FAIL' -Detail ('LISTENING held by foreign pid ' + $owner.Pid + ' via ' + $owner.Method + '; command line: ' + $info.CommandLine + '; verification said: ' + $own.Reason)
    }
}

# 7 ----------------------------------------------------------------------- #
Out-Title '7. disk space'
$drive = Split-Path -Qualifier $Root
if (-not $drive) { $drive = 'F:' }
$ds = Get-PSDrive -Name ($drive.TrimEnd(':')) -ErrorAction SilentlyContinue
if ($ds -and $null -ne $ds.Free) {
    $freeGb = [math]::Round(($ds.Free / 1GB), 2)
    $usedGb = [math]::Round(($ds.Used / 1GB), 2)
    if ($freeGb -ge $MinFreeGb) {
        Add-Check -Id 'D-7' -Name 'free disk space on ' + $drive -Status 'PASS' -Detail ($freeGb + ' GiB free / ' + $usedGb + ' GiB used (minimum ' + $MinFreeGb + ' GiB)')
    } else {
        Add-Check -Id 'D-7' -Name 'free disk space on ' + $drive -Status 'FAIL' -Detail ($freeGb + ' GiB free, below the required ' + $MinFreeGb + ' GiB')
    }
} else {
    Add-Check -Id 'D-7' -Name 'free disk space on ' + $drive -Status 'FAIL' -Detail ('could not measure free space for ' + $drive)
}

# 8 ----------------------------------------------------------------------- #
Out-Title '8. acceptance gate runner'
if (Test-Path -LiteralPath $Paths.GateRunner) {
    Add-Check -Id 'D-8' -Name 'final gate runner present' -Status 'PASS' -Detail ($Paths.GateRunner + ' (' + (Get-Item -LiteralPath $Paths.GateRunner).Length + ' bytes)')
} else {
    Add-Check -Id 'D-8' -Name 'final gate runner present' -Status 'FAIL' -Detail ('missing ' + $Paths.GateRunner)
}

# --- summary -------------------------------------------------------------- #
Write-Output ('')
Write-Output ('================================================')
Write-Output (' SUMMARY')
Write-Output ('================================================')
Write-Output (' checks run : ' + $checks.Count)
Write-Output (' PASS       : ' + $pass)
Write-Output (' FAIL       : ' + $fail)
Write-Output ('')
$failedChecks = @($checks | Where-Object { $_.Status -ne 'PASS' })
if ($failedChecks.Count -gt 0) {
    Write-Output (' failing checks:')
    foreach ($c in $failedChecks) {
        Write-Output ('  ' + $c.Id + ' ' + $c.Name + ' -- ' + $c.Measured)
    }
    Write-Output ('')
    Write-Output ('RESULT: FAIL')
    exit 1
}
Write-Output ('RESULT: PASS')
Write-Output ('next      : .\scripts\start.ps1')
exit 0

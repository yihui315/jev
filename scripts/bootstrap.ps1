<#
    JCC bootstrap (W-01).

    Idempotent, Windows PowerShell 5.1 ONLY.

    What it does, in order:
      1. locate a Python interpreter (existing .venv first, else PATH python,
         else the py launcher, else python3) and make sure it can run;
      2. create F:\jev\.venv if it does not exist;
      3. install the pinned requirements file if the runtime packages are
         NOT already importable (never silently "successful": the install is
         re-verified afterwards);
      4. write F:\jev\.env from config\.env.example ONLY if .env is absent;
      5. verify the tree and print a summary.

    Exit code: 0 when every check passed, non-zero (1) otherwise. It never
    exits 0 while something a human needs is missing.
#>

[CmdletBinding()]
param(
    [string]$Requirements = '',
    [switch]$ForceInstall,
    [int]$TimeoutSec = 900
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccPaths -Root $Root

$results = New-Object System.Collections.Generic.List[object]
$actions = New-Object System.Collections.Generic.List[string]

function Add-R {
    param([string]$Name, [string]$Status, [string]$Detail)
    $script:results.Add(@{ Name = $Name; Status = $Status; Detail = $Detail })
    Write-Output ('[' + $Status + '] ' + $Name + ' -- ' + $Detail)
}

function Out-Section {
    param([string]$Text)
    Write-Output ('')
    Write-Output $Text
    Write-Output ('-' * $Text.Length)
}

Write-Output ('JCC bootstrap (W-01)')
Write-Output ('repo root : ' + $Root)
Write-Output ('timestamp : ' + (Get-Date).ToString('o'))

Out-Section 'step 1/6  python interpreter'

$fallbackPython = Get-JccPython -Root $Root
$venvExists = Test-Path -LiteralPath $Paths.VenvPython

if ($venvExists) {
    Add-R -Name 'venv present' -Status 'PASS' -Detail $Paths.VenvPython
    $python = $Paths.VenvPython
} elseif ($fallbackPython) {
    Add-R -Name 'venv absent -> will create from PATH interpreter' -Status 'INFO' -Detail ([string]$fallbackPython)
    $python = ''
} else {
    Add-R -Name 'python interpreter' -Status 'FAIL' -Detail 'no python on PATH, no py launcher, no python3. Install Python 3.11+ and re-run this script.'
    Write-Output ('')
    Write-Output ('bootstrap FAILED: no Python interpreter available.')
    exit 1
}

if (-not $venvExists) {
    New-JccEnsureDir -Path $Paths.Runtime | Out-Null
    $venvArgs = @('-m', 'venv', $Paths.VenvPython)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $job = Start-Process -FilePath $fallbackPython -ArgumentList $venvArgs -WorkingDirectory $Root -Wait -PassThru -RedirectStandardOutput (Join-Path $Paths.Runtime 'venv-create.log') -RedirectStandardError (Join-Path $Paths.Runtime 'venv-create.err.log')
    $ErrorActionPreference = $prev
    $code = $job.ExitCode
    if ($code -ne 0 -or -not (Test-Path -LiteralPath $Paths.VenvPython)) {
        Add-R -Name 'venv create' -Status 'FAIL' -Detail ('"' + $fallbackPython + ' -m venv" exited ' + $code + '; log: runtime\jcc\venv-create.err.log')
        Write-Output ('')
        Write-Output ('bootstrap FAILED: could not create the virtual environment.')
        exit 1
    }
    Add-R -Name 'venv create' -Status 'PASS' -Detail ('created ' + $Paths.VenvPython + ' from ' + $fallbackPython)
    $actions.Add('created virtualenv')
    $python = $Paths.VenvPython
}

if (-not (Test-JccPython -Python $python)) {
    Add-R -Name 'interpreter runs' -Status 'FAIL' -Detail ('"python -c ..." on ' + $python + ' did not complete successfully')
    Write-Output ('')
    Write-Output ('bootstrap FAILED: the interpreter does not run.')
    exit 1
}
Add-R -Name 'interpreter runs' -Status 'PASS' -Detail ($python + ' (python ' + (Get-JccPythonVersion -Python $python) + ')')

$actions.Add('interpreter ' + $python)

Out-Section 'step 2/6  requirements file'

$isPinned = $Paths.Requirements.EndsWith('packages\jcc_core\requirements.txt')
if ($Requirements) {
    if (Test-Path -LiteralPath $Requirements) { $Paths.Requirements = $Requirements } else {
        Add-R -Name 'requirements file' -Status 'FAIL' -Detail ('explicit -Requirements "' + $Requirements + '" does not exist')
        Write-Output ('')
        Write-Output ('bootstrap FAILED: requirements file missing.')
        exit 1
    }
}
if (-not (Test-Path -LiteralPath $Paths.Requirements)) {
    Add-R -Name 'requirements file' -Status 'FAIL' -Detail ('no pinned requirements file at ' + $Paths.Requirements)
    Write-Output ('')
    Write-Output ('bootstrap FAILED: pinned requirements file missing.')
    exit 1
}
Add-R -Name 'requirements file' -Status 'PASS' -Detail ($Paths.Requirements + ' (pinned project file: ' + $isPinned + ')')

Out-Section 'step 3/6  dependencies'

$probe = @'
import fastapi, uvicorn, pydantic, yaml, httpx, pytest
print('all-imports-ok')
'@
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$out = & $python '-c' $probe 2>&1
$probeExit = $LASTEXITCODE
$ErrorActionPreference = $prev
$probeOk = ($probeExit -eq 0) -and (([string]$out) -match 'all-imports-ok')

if ($probeOk) {
    Add-R -Name 'dependencies importable' -Status 'PASS' -Detail 'fastapi/uvicorn/pydantic/pyyaml/httpx/pytest already importable -- install skipped (use -ForceInstall to reinstall)'
} else {
    $needInstall = $true
    if (-not (Test-Path -LiteralPath $Paths.VenvPip)) {
        Add-R -Name 'ensurepip bootstrap' -Status 'INFO' -Detail ('pip not found next to ' + $python + '; running "python -m ensurepip"')
        $ErrorActionPreference = 'Continue'
        $job = Start-Process -FilePath $python -ArgumentList @('-m', 'ensurepip', '--upgrade') -WorkingDirectory $Root -Wait -PassThru -RedirectStandardOutput (Join-Path $Paths.Runtime 'ensurepip.log') -RedirectStandardError (Join-Path $Paths.Runtime 'ensurepip.err.log')
        $ErrorActionPreference = $prev
        if ($job.ExitCode -ne 0) {
            Add-R -Name 'ensurepip bootstrap' -Status 'FAIL' -Detail ('"python -m ensurepip --upgrade" exited ' + $job.ExitCode)
            Write-Output ('')
            Write-Output ('bootstrap FAILED: could not bootstrap pip inside the virtualenv.')
            exit 1
        }
    }
    $installArgs = @('-m', 'pip', 'install', '-r', $Paths.Requirements)
    if (-not $ForceInstall) { $installArgs += @('--quiet') }
    if ($TimeoutSec -gt 0) { $installArgs += @('--timeout', '60') }

    $ErrorActionPreference = 'Continue'
    $job = Start-Process -FilePath $python -ArgumentList $installArgs -WorkingDirectory $Root -Wait -PassThru -RedirectStandardOutput (Join-Path $Paths.Runtime 'pip-install.log') -RedirectStandardError (Join-Path $Paths.Runtime 'pip-install.err.log')
    $ErrorActionPreference = $prev
    if ($job.ExitCode -ne 0) {
        Add-R -Name 'dependency install' -Status 'FAIL' -Detail ('"python -m pip install -r requirements" exited ' + $job.ExitCode + '; log: runtime\jcc\pip-install.err.log')
        Write-Output ('')
        Write-Output ('bootstrap FAILED: dependency install reported an error.')
        exit 1
    }
    Add-R -Name 'dependency install' -Status 'PASS' -Detail ('installed from ' + $Paths.Requirements + ' (exit 0)')
    $actions.Add('dependency install performed')
}

# Re-verify AFTER any install: never report success without measuring it.
$ErrorActionPreference = 'Continue'
$out2 = & $python '-c' $probe 2>&1
$probeExit2 = $LASTEXITCODE
$ErrorActionPreference = $prev
if (($probeExit2 -ne 0) -or (-not (([string]$out2) -match 'all-imports-ok'))) {
    Add-R -Name 'dependencies re-verified' -Status 'FAIL' -Detail ('post-install import probe exited ' + $probeExit2)
    Write-Output ('')
    Write-Output ('bootstrap FAILED: required packages are still not importable.')
    exit 1
}
Add-R -Name 'dependencies re-verified' -Status 'PASS' -Detail 'import probe exit 0 after install'

Out-Section 'step 4/6  .env template'

if (Test-Path -LiteralPath $Paths.EnvFile) {
    Add-R -Name '.env' -Status 'PASS' -Detail ('already present, left untouched: ' + $Paths.EnvFile)
} else {
    $src = $Paths.EnvExample
    if (-not (Test-Path -LiteralPath $src)) {
        Add-R -Name '.env' -Status 'FAIL' -Detail ('missing config\.env.example to copy from, and no .env present')
        Write-Output ('')
        Write-Output ('bootstrap FAILED: no .env and no template.')
        exit 1
    }
    Copy-Item -LiteralPath $src -Destination $Paths.EnvFile -Force
    Add-R -Name '.env' -Status 'PASS' -Detail ('created from ' + $src + ' -- fill JCC_CONTROL_TOKEN before using control endpoints')
    $actions.Add('created .env from template')
}

Out-Section 'step 5/6  runtime directories'

New-JccEnsureDir -Path $Paths.Runtime | Out-Null
$dbDir = Split-Path -Parent (Join-Path $Paths.Root 'runtime\jcc\jcc.db')
New-JccEnsureDir -Path $dbDir | Out-Null
Add-R -Name 'runtime dir' -Status 'PASS' -Detail ($Paths.Runtime + ' ready')

Out-Section 'step 6/6  verification'

if (-not (Test-Path -LiteralPath $Paths.Config)) {
    Add-R -Name 'config file' -Status 'FAIL' -Detail ('missing ' + $Paths.Config)
    Write-Output ('')
    Write-Output ('bootstrap FAILED: config file missing.')
    exit 1
}
Add-R -Name 'config file' -Status 'PASS' -Detail $Paths.Config

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
    $ErrorActionPreference = 'Continue'
    $cfgOut = & $python '-c' $cfgCode 2>&1
    $cfgExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
} finally {
    Set-Location -LiteralPath $cur -ErrorAction SilentlyContinue
}
if ($cfgExit -ne 0) {
    Add-R -Name 'config parses' -Status 'FAIL' -Detail ('load_config() exited ' + $cfgExit + ': ' + [string]$cfgOut)
    Write-Output ('')
    Write-Output ('bootstrap FAILED: config/jcc.yaml does not parse.')
    exit 1
}
$cfgFields = [string]$cfgOut
Add-R -Name 'config parses' -Status 'PASS' -Detail ('host=' + ($cfgFields.Split('|')[0]) + ' port=' + ($cfgFields.Split('|')[1]) + ' db=' + ($cfgFields.Split('|')[2]))

if (Test-Path -LiteralPath $Paths.GateRunner) {
    Add-R -Name 'acceptance gate runner' -Status 'PASS' -Detail $Paths.GateRunner
} else {
    Add-R -Name 'acceptance gate runner' -Status 'FAIL' -Detail ('missing ' + $Paths.GateRunner + ' (acceptance.ps1 cannot run the machine stop gate)')
}

Out-Section 'summary'

Write-Output ('repository  : ' + $Root)
Write-Output ('interpreter : ' + $python)
Write-Output ('requirements: ' + $Paths.Requirements)
Write-Output ('python      : ' + (Get-JccPythonVersion -Python $python))
Write-Output ('')
Write-Output ('changes made by this run:')
if ($actions.Count -eq 0) {
    Write-Output ('  (none -- every step was already in the desired state; idempotent no-op)')
} else {
    foreach ($a in $actions) { Write-Output ('  - ' + $a) }
}
Write-Output ('')
$failed = @($results | Where-Object { $_.Status -eq 'FAIL' })
if ($failed.Count -gt 0) {
    Write-Output ('RESULT: FAIL')
    Write-Output ('failed checks: ' + $failed.Count)
    exit 1
}
Write-Output ('RESULT: PASS (all checks green)')
Write-Output ('')
Write-Output ('next: run  .\scripts\start.ps1')
exit 0

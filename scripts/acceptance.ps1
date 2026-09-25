<#
    JCC acceptance runner (W-07). Windows PowerShell 5.1 ONLY.

    Default (the machine stop gate):
        F:\jev\.venv\Scripts\python.exe F:\jev\artifacts\acceptance\final_gate_run.py --full
    A different runner may be passed as the first positional argument (or via
    -Runner), e.g. artifacts/acceptance/p3_gate_run.py; extra arguments follow
    via -RunnerArgs.

    The exit code of the runner is PROPAGATED verbatim. Nothing here swallows a
    non-zero exit, prints SUCCESS on failure, or returns an empty value.
#>

[CmdletBinding()]
param(
    [string]$Runner = '',
    [string[]]$RunnerArgs,
    [int]$TimeoutSec = 3600
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\JccCommon.ps1')

$Root  = Get-JccRepoRoot
$Paths = Get-JccPaths -Root $Root

Write-Output ('JCC acceptance (W-07)')
Write-Output ('repo root : ' + $Root)
Write-Output ('timestamp : ' + (Get-Date).ToString('o'))

# --- which runner -------------------------------------------------------- #
if ($Runner) {
    if (Test-Path -LiteralPath $Runner) {
        $runnerPath = (Resolve-Path -LiteralPath $Runner).Path
    } elseif (Test-Path -LiteralPath (Join-Path $Root $Runner)) {
        $runnerPath = (Resolve-Path -LiteralPath (Join-Path $Root $Runner)).Path
    } else {
        Write-Output ('')
        Write-Output ('FAILED: runner not found: ' + $Runner)
        Write-Output ('        pass a path relative to ' + $Root + ' or an absolute path.')
        exit 2
    }
} else {
    $runnerPath = $Paths.GateRunner
}

if (-not (Test-Path -LiteralPath $runnerPath)) {
    Write-Output ('')
    Write-Output ('FAILED: default gate runner is missing: ' + $runnerPath)
    Write-Output ('        The final gate (docs/FINAL_STOP_CONDITIONS.md F-06) must be runnable from a')
    Write-Output ('        clone. Restore artifacts\acceptance\final_gate_run.py, or pass another')
    Write-Output ('        runner with: .\scripts\acceptance.ps1 <path-to-runner>')
    exit 2
}

# --- which interpreter ---------------------------------------------------- #
$python = Get-JccPython -Root $Root
if (-not $python) {
    Write-Output ('')
    Write-Output ('FAILED: no usable Python interpreter (.venv missing).')
    Write-Output ('        Run .\scripts\bootstrap.ps1 first.')
    exit 2
}
if (-not (Test-JccPython -Python $python)) {
    Write-Output ('')
    Write-Output ('FAILED: interpreter ' + $python + ' does not run.')
    exit 2
}

$argList = @()
if ($RunnerArgs -and $RunnerArgs.Count -gt 0) {
    $argList = @($RunnerArgs)
} else {
    $argList = @('--full')
}
# NOTE: no extra flags are injected. final_gate_run.py and p3_gate_run.py both
# expose only --full/--json-out/--quiet; inventing a flag here would turn a
# green gate into an argparse error. -TimeoutSec only bounds this wrapper.
$fullArgList = @($runnerPath) + $argList

Write-Output ('python    : ' + $python)
Write-Output ('runner    : ' + $runnerPath)
Write-Output ('args      : ' + ($argList -join ' '))
Write-Output ('')
Write-Output ('running the acceptance path ...')
Write-Output ('================================================================')
Write-Output ('')

$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$out = & $python @fullArgList 2>&1
$code = $LASTEXITCODE
$ErrorActionPreference = $prev

foreach ($line in $out) { Write-Output ([string]$line) }

Write-Output ('')
Write-Output ('================================================================')
Write-Output ('runner exit code: ' + $code)
if ($code -eq 0) {
    Write-Output ('RESULT: PASS')
} else {
    Write-Output ('RESULT: FAIL (non-zero exit propagated, not absorbed)')
}
exit $code

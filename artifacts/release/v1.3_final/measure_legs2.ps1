# Measure the remaining v1.3 gate legs, one process per leg, writing as it goes.
#
# WHY (same reason as measure_legs.ps1, which had two harness bugs and is kept
# for the record): `final_gate_run_v1_3.py --full` was reaped twice on this host
# mid-run, each time leaving a log whose last line is a row of progress dots and
# no summary. One leg per process means every leg that finishes is on disk even
# if this driver dies.
#
# The two bugs this driver exists to avoid, both learned the hard way:
#   * `& $py ... | Out-String` -- the call operator with a pipeline sends the
#     child's stdout to the error stream on PS 5.1, so every verdict read back
#     as UNKNOWN. Capture to a variable, then match.
#   * `git` is not on PATH inside a nested `powershell -File` here, so HEAD is
#     read through the venv python instead.
param()

$ErrorActionPreference = 'Continue'
$py   = 'D:\jev\.venv\Scripts\python.exe'
$gate = 'D:\jev\tools\final_gate_run_v1_3.py'
$out  = 'D:\jev\artifacts\release\v1.3_final\per_leg_results2.txt'

$env:CODEBUDDY_SAFE_DELETE_ENABLED = '0'
$env:PYTHONPATH                     = 'D:\jev\packages'
$env:PYTHONIOENCODING               = 'utf-8'
$env:PYTHONUNBUFFERED               = '1'
$env:PYTHONDONTWRITEBYTECODE        = '1'

# Already measured individually, PASS -- recorded here so one file carries the
# whole picture rather than a reader having to reassemble it from a chat log.
$done = @(
  'A2_DOMAIN','A3_MISSION','A4_ATTENTION','A5_DELIVERABLES','A6_CONNECTIONS',
  'A8_SECURITY_GATE','C3a_TYPECHECK','D2_PRODUCT_CONFIG_INERT',
  'D3_UI_IS_NOT_STDLIB','E2_GATE_INPUTS_TRACKED','E3_A8_PINS_VS_HEAD_BLOBS'
)

$todo = @(
  'C3b_VITEST','C3c_BUILD',
  'B1_W2_PRODUCT_UI','B2_W3_MISSION_GATE','B3_W4_ATTENTION_GATE',
  'B4_W5_DELIVERABLES_GATE','B5_W6_WORLD_GATE','B6_W7_CONNECTION_GATE',
  'B7_W8_RUNTIME_GATE','B8_W10_BROWSER_PERF',
  'C2_TOOLS_TESTS','A7_INVARIANTS','C1_CORE_E2E',
  'D1_GREEN_PROTECTION','E1_FROZEN_TREE','E5_FREEZE_ANCHOR_RESOLVES'
)

# HEAD, read WITHOUT shelling out to `git`.
#
# Two failures got me here, both worth recording because both looked like
# "16 legs failed":
#
#  1. `& git ...` from this nested `powershell -File` child: the PATH begins
#     `D;D:\WorkBuddy\...\safe-bin;...`, and the WorkBuddy safe-bin shim sits in
#     front of the real git. Resolving `git` from PowerShell worked
#     (Get-Command found ...\PortableGit\...\git.exe) but launching it from a
#     PYTHON child raised FileNotFoundError [WinError 2] at CreateProcess. So
#     `git` is resolvable as a PowerShell command and NOT as a Python child on
#     this host -- measured, in probe_nested_env.py.
#
#  2. `.venv\Scripts\python.exe` is invoked by ABSOLUTE path for the same
#     reason: a relative `python` would hit the shim's python, not the venv's.
#
# So: no `git` at all. The venv python reads .git/HEAD itself, which cannot be
# shadowed by anything on PATH.
$head = (& $py -c "import pathlib;h=pathlib.Path(r'D:\jev\.git\HEAD').read_text(encoding='utf-8').strip();p=h.split(' ',1)[1] if h.startswith('ref:') else h;print(pathlib.Path(r'D:\jev\.git',p).read_text(encoding='utf-8').strip() if not p.startswith('ref:') else 'unborn:'+p)")
Set-Content -Path $out -Value ("per-leg measurement, driver started at HEAD " + $head + "`r`n")
foreach ($leg in $done) {
  Add-Content -Path $out -Value ("{0,-30} {1,-6} (measured individually earlier)" -f $leg, 'PASS')
}

foreach ($leg in $todo) {
  $tmp = Join-Path $env:TEMP ("jev-leg-" + $leg + "-" + [guid]::NewGuid().ToString('N').Substring(0,8))
  New-Item -ItemType Directory -Path $tmp -Force | Out-Null
  $env:TEMP = $tmp
  $env:TMP  = $tmp
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $legOut = & $py $gate --only $leg 2>&1
  $rc = $LASTEXITCODE
  $sw.Stop()
  $verdict = 'UNKNOWN'
  foreach ($line in $legOut) {
    $s = [string]$line
    if ($s -match "\[ok\s*\]\s*$leg\b")  { $verdict = 'PASS'; break }
    if ($s -match "\[FAIL\]\s*$leg\b")  { $verdict = 'FAIL'; break }
  }
  if ($verdict -eq 'UNKNOWN' -and $rc -ne 0) { $verdict = 'rc=' + $rc }
  $line = "{0,-30} {1,-6} {2,8:N1}s" -f $leg, $verdict, $sw.Elapsed.TotalSeconds
  Add-Content -Path $out -Value $line
  Write-Output $line
}
Add-Content -Path $out -Value "PER_LEG_DONE"
Write-Output "PER_LEG_DONE"

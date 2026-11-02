# Measure the v1.3 final gate legs one at a time, surviving a reaped parent.
#
# WHY THIS EXISTS. `final_gate_run_v1_3.py --full` takes ~60 minutes and was
# killed twice mid-run on this host (once during A7_INVARIANTS, once during
# C1_CORE_E2E) with no summary line and no exit code -- a SANDBOX reaping the
# background process, not a leg failing. A reaped run produces a log whose last
# line is a row of progress dots, which is indistinguishable from a hang unless
# you know to look for the missing summary.
#
# So each leg is measured in its OWN process, and this driver runs the legs
# sequentially, writing one line per leg as it completes. If the driver is
# reaped, every leg that already finished is still on disk -- which is the
# whole point: partial coverage that survives, instead of one all-or-nothing
# 60-minute run that does not.
#
# OUTPUT IS UNBUFFERED (PYTHONUNBUFFERED=1) because a redirected stdout is
# block-buffered: a leg that finishes writes nothing until 8 KB accumulates, so
# a reaped driver can lose the results of legs that already succeeded.
#
# Each leg is invoked with its OWN fresh TEMP, matching what the gate runner
# does, and the driver records the exit code of every leg separately. It exits
# non-zero if any leg failed, so the file is a result and not a log.
param()

$ErrorActionPreference = 'Continue'
$py   = 'D:\jev\.venv\Scripts\python.exe'
$gate = 'D:\jev\tools\final_gate_run_v1_3.py'
$out  = 'D:\jev\artifacts\release\v1.3_final\per_leg_results.txt'
$git  = 'C:\Program Files\Git\cmd\git.exe'
if (-not (Test-Path $git)) { $git = 'C:\Program Files\Git\bin\git.exe' }

$env:CODEBUDDY_SAFE_DELETE_ENABLED = '0'
$env:PYTHONPATH                     = 'D:\jev\packages'
$env:PYTHONIOENCODING               = 'utf-8'
$env:PYTHONUNBUFFERED               = '1'
$env:PYTHONDONTWRITEBYTECODE        = '1'

$legs = @(
  'C2_TOOLS_TESTS','C3a_TYPECHECK','C3b_VITEST','C3c_BUILD',
  'B1_W2_PRODUCT_UI','B2_W3_MISSION_GATE','B3_W4_ATTENTION_GATE',
  'B4_W5_DELIVERABLES_GATE','B5_W6_WORLD_GATE','B6_W7_CONNECTION_GATE',
  'B7_W8_RUNTIME_GATE','B8_W10_BROWSER_PERF',
  'A7_INVARIANTS','C1_CORE_E2E'
)

# `git` is NOT on PATH inside a nested powershell -File run on this host, so the
# HEAD is read by the venv python rather than by shelling out to git. Reading it
# from $git would have aborted the whole driver on the first leg if the path
# were also wrong; a wrong HEAD line is recoverable, a dead driver is not.
$head = (& $py -c "import subprocess;print(subprocess.run(['git','rev-parse','HEAD'],cwd=r'D:\jev',capture_output=True,text=True).stdout.strip())")

Set-Content -Path $out -Value ("per-leg measurement at HEAD " + $head + "`r`n")
foreach ($leg in $legs) {
  $tmp = Join-Path $env:TEMP ("jev-leg-" + $leg + "-" + [guid]::NewGuid().ToString('N').Substring(0,8))
  New-Item -ItemType Directory -Path $tmp -Force | Out-Null
  $env:TEMP = $tmp
  $env:TMP  = $tmp
  $sw = [Diagnostics.Stopwatch]::StartNew()
  # NO `& ... | Out-String`: the call operator with a pipeline swallows the
  # child's stdout into the error stream on PS 5.1, which is why the first
  # attempt recorded rc=1 and verdict UNKNOWN for all 14 legs in ~0.5s each.
  # Capture via a variable, then split, so the verdict line is actually read.
  $legOut = & $py $gate --only $leg 2>&1
  $rc = $LASTEXITCODE
  $sw.Stop()
  $verdict = 'UNKNOWN'
  foreach ($line in $legOut) {
    $s = [string]$line
    if ($s -match "\[ok\s*\]\s*$leg\b")  { $verdict = 'PASS'; break }
    if ($s -match "\[FAIL\]\s*$leg\b")  { $verdict = 'FAIL'; break }
  }
  if ($verdict -eq 'UNKNOWN' -and $rc -ne 0) { $verdict = 'HARNESS/rc' }
  $line = "{0,-24} {1,-11} rc={2} {3,7:N1}s" -f $leg, $verdict, $rc, $sw.Elapsed.TotalSeconds
  Add-Content -Path $out -Value $line
  Write-Output $line
  # The per-leg temp is this driver's own; leaving it is safer than deleting a
  # tree a child may still hold open, and TEMP is not the repo.
}
Write-Output "PER_LEG_DONE"

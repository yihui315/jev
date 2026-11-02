# Probes the three pure cases of Get-JccNextHint and wires them to doctor.ps1.
# Windows PowerShell 5.1 ONLY. Driven by tools/tests/test_doctor_next_hint.py.
#
# Kept under tools/tests/fixtures/probes so $PSScriptRoot is the file's
# directory, making `..\..\..\..\scripts\lib\JccCommon.ps1` the clean path to
# the helper. Avoids the historical relative-resolution trap of a scratch dir.
[CmdletBinding()]
param(
    [string]$OutPath = ''
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '..\..\..\..\scripts\lib\JccCommon.ps1')

$ours = Get-JccNextHint -PortState 'OURS'
$free = Get-JccNextHint -PortState 'FREE'
$foreign = Get-JccNextHint -PortState 'FOREIGN' -ForeignPid 9999

# Drive doctor.ps1 end-to-end on this live host. The result is what is wired
# to the printed `next:` line; a passing OURS pure helper with no behaviour in
# doctor.ps1 would still be a defect, so this is the only way to test the
# whole pipeline in one go.
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$doctorOut = & (Join-Path $repoRoot 'scripts\doctor.ps1') 2>&1 | Out-String
$doctorExit = $LASTEXITCODE
$doctorNext = ''
foreach ($line in ($doctorOut -split "`r?`n")) {
    if ($line -match '^\s*next\s*:\s*(.+?)\s*$') {
        $doctorNext = $Matches[1]
        break
    }
}

# Read the port state back out of DOCTOR'S OWN section-6 detail line.
#
# WHY THIS EXISTS. The python test used to assert that the printed `next:` hint
# was always the OURS hint, because whoever wrote it snapshotted a host where the
# service happened to be UP ("pid 9952, port 4778") and froze that transient
# state into the test's premise. On a host where nothing is listening, doctor
# correctly prints the FREE hint, so the assertion -- not the doctor -- was
# wrong.
#
# So do NOT re-measure the port here. Re-measuring would be a SECOND opinion:
# it could disagree with the measurement doctor already made, and the test would
# then be comparing two different worlds. Instead we take doctor's own report of
# what it measured and require the printed hint to be the hint for THAT state.
# The premise is now a live measurement by construction, in every state, with
# nothing spawned and nothing skipped.
$doctorD6 = ''
$doctorD6Pid = 0
foreach ($line in ($doctorOut -split "`r?`n")) {
    $t = $line.Trim()
    if ($t -match 'measured:\s*no LISTENING socket') {
        $doctorD6 = $t
        break
    }
    if ($t -match 'measured:\s*listener owned by OUR pid\s+(\d+)') {
        $doctorD6 = $t
        break
    }
    if ($t -match 'measured:\s*LISTENING held by foreign pid\s+(\d+)') {
        $doctorD6 = $t
        $doctorD6Pid = [int]$Matches[1]
        break
    }
}

# The FOREIGN hint embeds the owning pid, so the probe's fixed-pid 9999 sample
# cannot stand in for the real one. Compute the hint for the pid doctor named.
$foreignForDoctorPid = ''
if ($doctorD6Pid -gt 0) {
    $foreignForDoctorPid = Get-JccNextHint -PortState 'FOREIGN' -ForeignPid $doctorD6Pid
}

$report = [ordered]@{
    schema                = 'jcc-next-hint-probe/2'
    ours                  = $ours
    free                  = $free
    foreign               = $foreign
    foreign_pid_used      = 9999
    doctor_exit           = $doctorExit
    doctor_next_printed   = $doctorNext
    doctor_d6_measured    = $doctorD6
    doctor_d6_foreign_pid = $doctorD6Pid
    foreign_for_doctor_pid = $foreignForDoctorPid
}
$json = $report | ConvertTo-Json -Depth 4
if ($OutPath) {
    [System.IO.File]::WriteAllText($OutPath, $json, (New-Object System.Text.UTF8Encoding($false)))
} else {
    Write-Output $json
}
exit 0
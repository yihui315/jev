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

$report = [ordered]@{
    schema              = 'jcc-next-hint-probe/1'
    ours                = $ours
    free                = $free
    foreign             = $foreign
    foreign_pid_used    = 9999
    doctor_exit         = $doctorExit
    doctor_next_printed = $doctorNext
}
$json = $report | ConvertTo-Json -Depth 4
if ($OutPath) {
    [System.IO.File]::WriteAllText($OutPath, $json, (New-Object System.Text.UTF8Encoding($false)))
} else {
    Write-Output $json
}
exit 0
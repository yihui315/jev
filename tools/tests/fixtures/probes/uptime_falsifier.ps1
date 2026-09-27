[CmdletBinding()]
param(
    [int]$SampleGapSec = 45,
    [int]$Samples = 3,
    [string]$OutPath = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\..\..\scripts\lib\JccCommon.ps1')

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$Paths = Get-JccPaths -Root $Root
$pidFilePid = Get-JccPidFromFile -Paths $Paths

# Bail early if no service; the test treats that as "no measurement".
if ($pidFilePid -le 0) {
    [System.IO.File]::WriteAllText($OutPath,
        ('{"error":"no_pid_file","pid_file":"' + $Paths.PidFile + '"}'),
        (New-Object System.Text.UTF8Encoding($false)))
    exit 2
}
if (-not (Test-JccProcessAlive -ProcessId $pidFilePid)) {
    [System.IO.File]::WriteAllText($OutPath,
        ('{"error":"service_dead","pid":' + $pidFilePid + '}'),
        (New-Object System.Text.UTF8Encoding($false)))
    exit 2
}

# Inlined sample loop. Functions on a param-bound script run in PS 5.1 work,
# but a closure over $pidFilePid / $Root inside Sample-Once raised an
# ArgumentException that was opaque to trace. Inlining avoids whatever shape
# the function was tripping over (measured: the same six lines in main scope
# produce a valid JSON report).
$rows = New-Object System.Collections.Generic.List[object]
for ($i = 0; $i -lt $Samples; $i++) {
    $now = Get-Date
    $p = Get-Process -Id $pidFilePid -ErrorAction Stop
    $uptime = Get-JccElapsed -Since $p.StartTime -Now $now
    $deltaSec = [int][math]::Floor($now.Subtract($p.StartTime).TotalSeconds)

    $statusOut = & (Join-Path $Root 'scripts\status.ps1') 2>&1 | Out-String
    $scriptUp = ''
    foreach ($line in ($statusOut -split "`r?`n")) {
        if ($line -match '^\s*uptime\s*:\s*(\d+h\d+m\d+s)') {
            $scriptUp = $Matches[1]
            break
        }
    }

    $rows.Add([pscustomobject]@{
        sample=($i + 1)
        timestamp_iso=$now.ToString('o')
        pid=$pidFilePid
        proc_start_iso=$p.StartTime.ToString('o')
        elapsed_seconds=$deltaSec
        elapsed_str=$uptime
        script_str=$scriptUp
    })

    if ($i -lt ($Samples - 1)) { Start-Sleep -Seconds $SampleGapSec }
}

$report = [ordered]@{
    schema='jcc-uptime-falsifier/1'
    sample_count=$rows.Count
    pid=$pidFilePid
    sample_gap_sec=$SampleGapSec
}
$report.samples = $rows.ToArray()
$json = $report | ConvertTo-Json -Depth 5
if ($OutPath) {
    [System.IO.File]::WriteAllText($OutPath, $json, (New-Object System.Text.UTF8Encoding($false)))
} else {
    Write-Output $json
}
exit 0
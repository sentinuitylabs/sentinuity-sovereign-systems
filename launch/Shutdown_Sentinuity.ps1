[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Root
)

$ErrorActionPreference = 'Stop'
$rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
$rootRegex = [regex]::Escape($rootPath)
$selfPid = $PID

function Get-ProcessSnapshot {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
}

function Get-ProtectedPids {
    param([object[]]$Processes)

    $byPid = @{}
    foreach ($process in $Processes) {
        $byPid[[int]$process.ProcessId] = $process
    }

    $protected = [System.Collections.Generic.HashSet[int]]::new()
    $current = $selfPid
    while ($current -gt 0 -and $protected.Add([int]$current)) {
        if (-not $byPid.ContainsKey([int]$current)) { break }
        $current = [int]$byPid[[int]$current].ParentProcessId
    }
    return $protected
}

$authorityRegex = '(?i)(Launch_Sentinuity|Restart_Sentinuity|Shutdown_Sentinuity|Sentinuity_Watch|Watchdog_Sentinuity|services[\\/.](sentinuity_watch|system_guardian|sovereign_governor|polaris|polaris_auxiliary|reconnaissance_engine))'
$runtimeRegex = '(?i)(services[\\/.]|streamlit\s+run\s+services[\\/]sovereign_hub\.py|master_console|sentinuity_matrix\.db|substrate_)'

function Find-SentinuityProcesses {
    param(
        [object[]]$Processes,
        [System.Collections.Generic.HashSet[int]]$Protected,
        [switch]$AuthoritiesOnly
    )

    foreach ($process in $Processes) {
        $pidValue = [int]$process.ProcessId
        if ($Protected.Contains($pidValue)) { continue }

        $commandLine = [string]$process.CommandLine
        if ([string]::IsNullOrWhiteSpace($commandLine)) { continue }

        $inRoot = $commandLine -match $rootRegex
        $isAuthority = $commandLine -match $authorityRegex
        $isRuntime = $commandLine -match $runtimeRegex

        if ($AuthoritiesOnly) {
            if ($inRoot -and $isAuthority) { $process }
        }
        elseif ($inRoot -and ($isAuthority -or $isRuntime)) {
            $process
        }
    }
}

function Stop-MatchedProcesses {
    param([object[]]$Targets)

    $targetsByPid = @{}
    foreach ($target in $Targets) {
        $targetsByPid[[int]$target.ProcessId] = $target
    }

    # Stop children before parents so watchdog wrappers cannot respawn workers.
    $ordered = $Targets | Sort-Object -Property @{ Expression = {
        $depth = 0
        $parent = [int]$_.ParentProcessId
        while ($targetsByPid.ContainsKey($parent) -and $depth -lt 64) {
            $depth++
            $parent = [int]$targetsByPid[$parent].ParentProcessId
        }
        $depth
    }; Descending = $true }

    foreach ($target in $ordered) {
        try {
            Stop-Process -Id ([int]$target.ProcessId) -Force -ErrorAction Stop
            Write-Host ("  stopped PID {0,-6} {1}" -f $target.ProcessId, $target.Name)
        }
        catch {
            if (Get-Process -Id ([int]$target.ProcessId) -ErrorAction SilentlyContinue) {
                Write-Warning ("Could not stop PID {0}: {1}" -f $target.ProcessId, $_.Exception.Message)
            }
        }
    }
}

try {
    Write-Host '[1/5] Stopping restart authorities and watchdogs...'
    $snapshot = Get-ProcessSnapshot
    $protected = Get-ProtectedPids -Processes $snapshot
    $authorities = @(Find-SentinuityProcesses -Processes $snapshot -Protected $protected -AuthoritiesOnly)
    Stop-MatchedProcesses -Targets $authorities
    Start-Sleep -Seconds 2

    Write-Host '[2/5] Stopping remaining Sentinuity services...'
    $snapshot = Get-ProcessSnapshot
    $protected = Get-ProtectedPids -Processes $snapshot
    $services = @(Find-SentinuityProcesses -Processes $snapshot -Protected $protected)
    Stop-MatchedProcesses -Targets $services
    Start-Sleep -Seconds 3

    Write-Host '[3/5] Running final shutdown sweep...'
    $snapshot = Get-ProcessSnapshot
    $protected = Get-ProtectedPids -Processes $snapshot
    $remaining = @(Find-SentinuityProcesses -Processes $snapshot -Protected $protected)
    Stop-MatchedProcesses -Targets $remaining
    Start-Sleep -Seconds 2

    Write-Host '[4/5] Best-effort SQLite WAL checkpoint...'
    $dbPath = Join-Path $rootPath 'sentinuity_matrix.db'
    $python = Get-Command py -ErrorAction SilentlyContinue
    $pythonArgs = @('-3')
    if (-not $python) {
        $python = Get-Command python -ErrorAction SilentlyContinue
        $pythonArgs = @()
    }

    if ($python -and (Test-Path -LiteralPath $dbPath)) {
        $checkpointCode = @'
import sqlite3, sys
path = sys.argv[1]
connection = sqlite3.connect(path, timeout=10)
connection.execute("PRAGMA busy_timeout=10000")
print(connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall())
connection.close()
'@
        try {
            & $python.Source @pythonArgs -c $checkpointCode $dbPath | ForEach-Object { Write-Host "  $_" }
            if ($LASTEXITCODE -eq 0) { Write-Host '  [OK] WAL checkpoint complete.' }
            else { Write-Warning 'WAL checkpoint returned a non-zero exit code.' }
        }
        catch {
            Write-Warning ("WAL checkpoint skipped: {0}" -f $_.Exception.Message)
        }
    }
    else {
        Write-Host '  [SKIP] Python or sentinuity_matrix.db unavailable.'
    }

    Write-Host '[5/5] Verifying shutdown...'
    $snapshot = Get-ProcessSnapshot
    $protected = Get-ProtectedPids -Processes $snapshot
    $left = @(Find-SentinuityProcesses -Processes $snapshot -Protected $protected)

    if ($left.Count -gt 0) {
        Write-Host ''
        Write-Host ("[FAIL] {0} Sentinuity process(es) remain:" -f $left.Count) -ForegroundColor Red
        $left | Select-Object ProcessId, Name, CommandLine | Format-Table -AutoSize -Wrap
        exit 2
    }

    Write-Host '[PASS] No Sentinuity services remain.' -ForegroundColor Green
    exit 0
}
catch {
    Write-Host ("[FAIL] Shutdown error: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}

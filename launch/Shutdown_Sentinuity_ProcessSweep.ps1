param(
    [Parameter(Mandatory = $true)]
    [string]$RootPath,

    [ValidateSet('stop','verify')]
    [string]$Mode = 'stop'
)

$ErrorActionPreference = 'SilentlyContinue'
$root = [System.IO.Path]::GetFullPath($RootPath).TrimEnd('\')
$selfPid = $PID
$self = Get-CimInstance Win32_Process -Filter "ProcessId=$selfPid"
$parentPid = if ($self) { [int]$self.ParentProcessId } else { -1 }

$serviceNames = @(
    'execution_engine','ingest_pipeline','market_intelligence','ws_price_oracle',
    'neural_supervisor','system_guardian','sovereign_governor','freshness_enforcer',
    'active_pipeline_cleaner','price_enricher','periodic_refresh','winner_snapshot_archiver',
    'shadow_runner_tracker','wallet_scout','telegram_scout','x_scout','symbiotic_router',
    'reconciliation_engine','live_settlement_recovery','council_build_orchestrator',
    'council_autobuilder','intelligence_orchestrator','forge_code_writer','github_scout',
    'openclaw_security_sentinel','copytrade_shadow_scanner','smart_wallet_trade_ingester',
    'substrate_opportunity_scanner','substrate_portfolio_supervisor',
    'substrate_copytrade_bridge_loop','substrate_paper_trader','macro_channel',
    'macro_price_feed','paper_wallet_refresher','council_chamber_bridge','market_tide',
    'signal_gate','signal_gate_sensor','sentinuity_watch','polaris','polaris_auxiliary',
    'reconnaissance_engine','replay_engine','debate_engine','code_vault','master_console'
)

$escapedServices = ($serviceNames | ForEach-Object { [regex]::Escape($_) }) -join '|'
$rx = [regex]::new(
    [regex]::Escape($root) +
    '|sentinuity_matrix\.db|sentinuity_intelligence\.db' +
    '|Launch_Sentinuity|Restart_Sentinuity|Watchdog_Sentinuity|Sentinuity_Watch' +
    '|sovereign_hub|streamlit|cloudflared|openclaw' +
    '|services[\\/.](' + $escapedServices + ')',
    [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
)

function Get-SentinuityProcesses {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $selfPid -and
        $_.ProcessId -ne $parentPid -and
        ([string]$_.CommandLine -match $rx)
    })
}

$matches = Get-SentinuityProcesses

if ($Mode -eq 'verify') {
    if ($matches.Count -eq 0) {
        Write-Host '[PASS] No Sentinuity processes remain.'
        exit 0
    }

    Write-Host ("[FAIL] Sentinuity processes still running: {0}" -f $matches.Count)
    $matches | Select-Object ProcessId, Name, CommandLine | Format-List
    exit 4
}

# Stop restart authorities first so they cannot repopulate the runtime.
$authorityPattern = 'Watchdog_Sentinuity|Sentinuity_Watch|services[\\/.](sentinuity_watch|system_guardian|sovereign_governor|polaris|polaris_auxiliary|reconnaissance_engine)'
$authorities = @($matches | Where-Object { [string]$_.CommandLine -match $authorityPattern })
foreach ($proc in $authorities) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($authorities.Count -gt 0) { Start-Sleep -Milliseconds 800 }

# Then stop all remaining matched children and wrappers.
$matches = Get-SentinuityProcesses
foreach ($proc in $matches) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 800

# One final targeted sweep catches children that were orphaned during teardown.
$remaining = Get-SentinuityProcesses
foreach ($proc in $remaining) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host ("Stopped restart authorities: {0}" -f $authorities.Count)
Write-Host ("Stopped remaining matched processes: {0}" -f $matches.Count)
exit 0

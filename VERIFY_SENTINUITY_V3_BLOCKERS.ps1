# =============================================================================
# VERIFY_SENTINUITY_V3_BLOCKERS.ps1   (V3_BLOCKERS_20260721)
# =============================================================================
# Decisive verification of the V3 blocker fixes on a workspace. Read-only for
# trading state: every fixture runs against throwaway databases; the canary
# submits no transaction.
#
#   powershell -ExecutionPolicy Bypass -File .\VERIFY_SENTINUITY_V3_BLOCKERS.ps1 `
#       -Workspace "C:\path\to\trading-bot"
#
# Exit 0 = all package fixtures pass AND the live canary reports READY.
# Exit 1 = a package fixture failed (fixes not verified).
# Exit 2 = package fixtures pass but the canary reports LIVE REMAINS BLOCKED
#          (correct fail-closed state until the launcher interview stamps
#           live sizing on this machine).
# =============================================================================
param(
    [Parameter(Mandatory = $true)][string]$Workspace
)
$ErrorActionPreference = 'Continue'
$Workspace = (Resolve-Path $Workspace).Path

# Windows Python console contract: prevent cp1252 UnicodeEncodeError.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$Python = $null
foreach ($cand in @(@('py','-3'), @('python'))) {
    try { & $cand[0] $cand[1..($cand.Count-1)] -c "import sys" 2>$null | Out-Null
          if ($LASTEXITCODE -eq 0) { $Python = $cand; break } } catch {}
}
if (-not $Python) { Write-Host '[ABORT] Python 3 not found on PATH.'; exit 1 }
function Invoke-Py {
    param([string[]]$ArgList)

    if ($Python.Count -gt 1) {
        & $Python[0] @($Python[1..($Python.Count - 1)]) @ArgList | Out-Host
    } else {
        & $Python[0] @ArgList | Out-Host
    }

    $rc = $LASTEXITCODE
    return [int]$rc
}

Push-Location $Workspace
$results = @{}
foreach ($fixture in @('launch\replay_nto_case.py',
                       'launch\verify_sizing_gate.py',
                       'launch\verify_ivaris_routing.py',
                       'launch\verify_substrate_lifecycle.py')) {
    Write-Host "== $fixture =="
    $results[$fixture] = Invoke-Py @($fixture)
}
Write-Host '== launch\live_canary_fixtures.py (RUN_LIVE_CANARY_VERIFY) =='
$canary = Invoke-Py @('launch\live_canary_fixtures.py')
Pop-Location

Write-Host ''
Write-Host '=================== VERIFICATION SUMMARY ==================='
$pkgFail = 0
foreach ($k in $results.Keys | Sort-Object) {
    $state = if ($results[$k] -eq 0) { 'PASS' } else { $pkgFail++; 'FAIL' }
    Write-Host ("  {0,-42} {1}" -f $k, $state)
}
$canaryState = if ($canary -eq 0) { 'READY' } else { 'LIVE REMAINS BLOCKED' }
Write-Host ("  {0,-42} {1}" -f 'live canary', $canaryState)

if ($pkgFail -gt 0) { Write-Host 'VERIFY: FAIL - package fixtures failed.'; exit 1 }
if ($canary -ne 0) {
    Write-Host 'VERIFY: package fixtures PASS; live remains blocked (fail-closed until the launcher interview stamps live sizing).'
    exit 2
}
Write-Host 'VERIFY: PASS - all fixtures pass and the limited live canary is READY.'
exit 0

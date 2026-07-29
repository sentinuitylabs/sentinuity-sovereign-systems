param([string]$RepoRoot = "C:\Users\Polar\.openclaw\workspace\trading-bot")
$ErrorActionPreference='Stop'
$PackRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot
if (-not (Test-Path .git)) { throw "Not a Git repository: $RepoRoot" }
$dirty = git status --porcelain
Write-Host "Existing working-tree changes:"; $dirty
$runtime = git status --porcelain | Select-String -Pattern '(\.db($|-)|\.env($|\.)|logs[\\/]|audits[\\/]|backups[\\/]|__pycache__|\.pyc$|\.bak)'
if ($runtime) { throw "Runtime/private artifacts are already tracked or staged. Remove them before continuing.`n$runtime" }
Write-Host "Copying public overlay..."
Get-ChildItem $PackRoot -Recurse -File | Where-Object {
  $_.Name -notin @('GITHUB_PUSH_PUBLIC_UPDATE.ps1','PUBLIC_RELEASE_MANIFEST.json')
} | ForEach-Object {
  $rel=$_.FullName.Substring($PackRoot.Length).TrimStart('\\','/')
  $dst=Join-Path $RepoRoot $rel
  New-Item -ItemType Directory -Force (Split-Path -Parent $dst) | Out-Null
  Copy-Item $_.FullName $dst -Force
}
python .\VERIFY_PUBLIC_GITHUB_SIGNOFF.py
if ($LASTEXITCODE -ne 0) { throw 'Public sign-off verifier failed.' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'git diff --check failed.' }
git add -- .gitignore PUBLIC_RELEASE_NOTES_20260729.md VERIFY_PUBLIC_GITHUB_SIGNOFF.py core launch services ui wallets
$bad = git diff --cached --name-only | Select-String -Pattern '(\.db($|-)|\.env($|\.)|logs[\\/]|audits[\\/]|backups[\\/]|__pycache__|\.pyc$|\.bak)'
if ($bad) { git reset; throw "Unsafe staged paths detected; staging reset.`n$bad" }
Write-Host "\nSTAGED FILES:" -ForegroundColor Cyan
git diff --cached --name-only
Write-Host "\nSTAGED STAT:" -ForegroundColor Cyan
git diff --cached --stat
Write-Host "\nReview above. Then run:" -ForegroundColor Yellow
Write-Host 'git commit -m "Public paper release: living world, resilient marking and Council safety"'
Write-Host 'git push origin HEAD:main'

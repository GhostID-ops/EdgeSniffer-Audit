param(
  [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"

function Run-Step {
  param(
    [string]$Label,
    [scriptblock]$Command
  )

  Write-Host ""
  Write-Host "==> $Label" -ForegroundColor Cyan
  & $Command
}

if (-not (Test-Path ".git")) {
  throw "Run this from inside the bot folder. I could not find a .git directory here."
}

if (-not (Test-Path ".env")) {
  throw "Missing .env. Create/fill his .env with his private key, license, RPC/API keys first, then rerun this."
}

Run-Step "Pull latest code from origin/$Branch" {
  git pull origin $Branch
}

Run-Step "Install/update npm dependencies" {
  npm install
}

Run-Step "Apply safe friend env preset" {
  npm run env:friend-latest
}

Run-Step "Build TypeScript" {
  npm run build
}

Write-Host ""
Write-Host "Done. His private key/API keys were preserved; .env was backed up under .restore-backups." -ForegroundColor Green
Write-Host "Start the bot with: npm run bot" -ForegroundColor Green

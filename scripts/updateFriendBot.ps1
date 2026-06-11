param(
  [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
$BotRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $BotRoot

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
  throw @"
This folder is not a git clone: $BotRoot

It looks like the bot was downloaded as a ZIP or copied without its .git folder.
To enable updates, clone it once, then copy his existing .env into the clone:

  cd C:\Users\maxim\Downloads
  git clone https://github.com/frankykas/TradingBot.git TradingBot-git
  copy "$BotRoot\.env" "C:\Users\maxim\Downloads\TradingBot-git\.env"
  cd C:\Users\maxim\Downloads\TradingBot-git
  npm run friend:update

After that, always use C:\Users\maxim\Downloads\TradingBot-git for the bot.
"@
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

Run-Step "Apply safe friend infra env preset" {
  npm run env:friend-latest
}

Run-Step "Build TypeScript" {
  npm run build
}

Write-Host ""
Write-Host "Done. His private key/API keys and strategy/accounting values were preserved; .env was backed up under .restore-backups." -ForegroundColor Green
Write-Host "Start the bot with: npm run bot" -ForegroundColor Green

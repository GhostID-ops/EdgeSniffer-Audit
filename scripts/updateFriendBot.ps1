param(
  [string]$Branch = "main",
  [switch]$ApplyExposure
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

function Get-EnvValue {
  param([string]$Key)

  $line = Get-Content ".env" |
    Where-Object { $_ -match "^\s*$([regex]::Escape($Key))\s*=" } |
    Select-Object -Last 1
  if (-not $line) {
    return $null
  }
  return ($line -replace "^\s*$([regex]::Escape($Key))\s*=", "")
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

if ($ApplyExposure) {
  Run-Step "Apply optional friend exposure preset" {
    npm run env:friend-exposure
  }
}

Run-Step "Build TypeScript" {
  npm run build
}

Write-Host ""
Write-Host "Done. His private key/API keys and strategy/accounting values were preserved; .env was backed up under .restore-backups." -ForegroundColor Green
if ($ApplyExposure) {
  Write-Host "Exposure values now active in .env:" -ForegroundColor Green
  Write-Host "  COPY_BUY_PERCENTAGE=$(Get-EnvValue "COPY_BUY_PERCENTAGE")"
  Write-Host "  MIN_BUY_BEHAVIOR=$(Get-EnvValue "MIN_BUY_BEHAVIOR")"
  Write-Host "  MAX_SOL_PER_TOKEN=$(Get-EnvValue "MAX_SOL_PER_TOKEN")"
  Write-Host "  MAX_SESSION_DEPLOYED_SOL=$(Get-EnvValue "MAX_SESSION_DEPLOYED_SOL")"
} else {
  Write-Host "Optional exposure caps can be applied with: npm run env:friend-exposure" -ForegroundColor Yellow
  Write-Host "Or update infra + exposure together with: npm run friend:update:exposure" -ForegroundColor Yellow
}
Write-Host "Start the bot with: npm run bot" -ForegroundColor Green

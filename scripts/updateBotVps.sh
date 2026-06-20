#!/usr/bin/env bash
#
# Update the copytrading bot on the VPS to the latest code and rebuild.
#
# Usage:
#   bash scripts/updateBotVps.sh            # update to origin/main
#   BOT_BRANCH=some-branch bash scripts/updateBotVps.sh
#
# The bot process itself runs via tsx from src/ (npm run bot), so a git pull is
# enough for the TypeScript changes. The Rust sidecar is a compiled binary and
# MUST be rebuilt, which is why this script always rebuilds it (with the Jito
# ShredStream feature the hot sniper depends on).
#
set -euo pipefail

# Resolve the repo root from this script's location (scripts/ -> repo root).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BRANCH="${BOT_BRANCH:-main}"

cd "$REPO_DIR"
echo "==> Repo:   $REPO_DIR"
echo "==> Branch: $BRANCH"

# Refuse to run while the bot/sidecar is up: a running process holds the Rust
# binary open and the rebuild would fail with "text file busy".
if pgrep -f "tsx src/copytrade.ts" >/dev/null 2>&1 \
   || pgrep -f "pumpfun-sidecar" >/dev/null 2>&1; then
  echo
  echo "ERROR: the bot or Rust sidecar is still running."
  echo "       Stop it first (Ctrl+C in its terminal), then re-run this script."
  exit 1
fi

# 1. Pull the latest code (fast-forward only; aborts on local divergence).
echo "==> Fetching latest..."
git fetch --prune origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"
echo "==> Now at: $(git log --oneline -1)"

# 2. Node dependencies (idempotent; installs anything missing).
echo "==> Installing Node dependencies..."
npm install

# 3. Compile TypeScript to dist/. The bot runs from src/ via tsx, so this is
#    mainly for the dist-based helper scripts -- quick and harmless.
echo "==> Building TypeScript (dist/)..."
npm run build

# 4. Rebuild the Rust sidecar in release mode WITH the Jito ShredStream feature.
echo "==> Building Rust sidecar (release + jito-shredstream)..."
npm run rust-sidecar:jito-build

echo
echo "==> Update complete: $(git log --oneline -1)"
echo "    Start the bot again to pick up the changes:"
echo "      npm run bot"

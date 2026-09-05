#!/usr/bin/env bash
# Install the launchd job that pulls VPS backups to this Mac every morning.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.romantika.backup-pull.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s#__REPO__#$REPO#g" -e "s#__HOME__#$HOME#g" "$REPO/scripts/launchd/com.romantika.backup-pull.plist" > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "installed $PLIST (daily 09:15); run now: launchctl start com.romantika.backup-pull"

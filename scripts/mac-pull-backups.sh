#!/usr/bin/env bash
# Pull the VPS backups to this Mac (second copy). Runs daily via launchd (scripts/launchd/*.plist).
# Requires the ssh alias `romantika-vps` (docs/RUNBOOK.md «Access») and rsync on both ends.
set -euo pipefail
HOST="${ROMANTIKA_VPS:-romantika-vps}"
REMOTE="${ROMANTIKA_REMOTE_BACKUPS:-/opt/stacks/romantika/data/backups/}"
LOCAL="${ROMANTIKA_LOCAL_BACKUPS:-$HOME/Backups/romantika/}"
mkdir -p "$LOCAL"
rsync -az --delete --partial -e ssh "$HOST:$REMOTE" "$LOCAL"
echo "$(date '+%F %T') pulled backups from $HOST to $LOCAL: $(du -sh "$LOCAL" | cut -f1)"

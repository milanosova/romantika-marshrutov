#!/usr/bin/env bash
# Run a `docker compose` command for the production stack on the VPS, as the romantika user.
#   scripts/rc.sh ps
#   scripts/rc.sh logs --since 30m bot web worker
#   scripts/rc.sh exec -T backup scripts/backup.sh
# Never `down -v`, never `volume rm` (CLAUDE.md rule 1); this wrapper refuses both.
set -euo pipefail
HOST="${HOST:-romantika-vps}"
DEST="${DEST:-/opt/stacks/romantika}"
case " $* " in
  *" down "*" -v"*|*" down -v"*|*" volume rm "*|*" rm -v"*) echo "refused: that would destroy production data (CLAUDE.md rule 1)"; exit 2 ;;
esac
[ $# -gt 0 ] || { echo "usage: scripts/rc.sh <docker compose args>"; exit 2; }
ARGS=$(printf ' %q' "$@")
exec ssh "$HOST" "cd '$DEST' && docker compose -f docker/compose.yml -f docker/compose.vps.yml --project-directory . $ARGS"

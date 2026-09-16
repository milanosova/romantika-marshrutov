#!/usr/bin/env bash
# Run a `docker compose` command for the production stack on the VPS, as the romantika user.
#   scripts/rc.sh ps
#   scripts/rc.sh logs --since 30m bot web worker
#   scripts/rc.sh exec -T backup scripts/backup.sh
#   scripts/rc.sh exec -T db psql -U romantika -d romantika -Atc "select count(*) from users"
# Guard rails (CLAUDE.md rules 1 and 10): only the subcommands listed below; `down`, `rm` and
# `volume` are refused outright; `psql` runs only a single read-only `select` given with -c/-Atc.
# RC_DRY=1 prints the remote command instead of running it (used by the acceptance tests).
set -euo pipefail
HOST="${HOST:-romantika-vps}"
DEST="${DEST:-/opt/stacks/romantika}"
ALLOWED=" ps logs exec restart up pull config stop start build run images top events "

refuse() { echo "refused: $1 (CLAUDE.md rule ${2:-1})"; exit 2; }
[ $# -gt 0 ] || { echo "usage: scripts/rc.sh <docker compose args>"; exit 2; }

# The subcommand is the first argument that is not an option of `docker compose` itself.
sub=""
for arg in "$@"; do
  case "$arg" in
    -*) continue ;;
    *) sub=$arg; break ;;
  esac
done
[ -n "$sub" ] || refuse "no subcommand given (allowed:$ALLOWED)"
case "$ALLOWED" in
  *" $sub "*) ;;
  *) refuse "subcommand '$sub' is not allowed from a laptop (allowed:$ALLOWED)" ;;
esac
for arg in "$@"; do
  case "$arg" in
    -v|--volumes|--rmi|--rmi=*) refuse "'$arg' would destroy production data" ;;
  esac
done

# psql: exactly one read-only statement, passed with -c / -Atc / --command, no `;`, no writes.
if printf '%s\n' "$@" | grep -qx 'psql'; then
  stmt=""; prev=""
  for arg in "$@"; do
    case "$prev" in -c|-Atc|-tAc|-tc|-Ac|--command) stmt=$arg ;; esac
    prev=$arg
  done
  [ -n "$stmt" ] || refuse "psql only with a single -c/-Atc statement (no interactive shell)" 10
  case "$stmt" in *";"*) refuse "one statement at a time, no ';'" 10 ;; esac
  printf '%s' "$stmt" | grep -qiE '^[[:space:]]*select[[:space:]]' || refuse "psql from a laptop is read-only: the statement must start with select" 10
  printf '%s' "$stmt" | grep -qiE '\b(insert|update|delete|drop|alter|truncate|create|grant|copy|into)\b' && refuse "the statement contains a write keyword" 10
fi

ARGS=$(printf ' %q' "$@")
REMOTE="cd '$DEST' && docker compose -f docker/compose.yml -f docker/compose.vps.yml --project-directory . $ARGS"
if [ "${RC_DRY:-0}" = 1 ]; then echo "$REMOTE"; exit 0; fi
exec ssh "$HOST" "$REMOTE"

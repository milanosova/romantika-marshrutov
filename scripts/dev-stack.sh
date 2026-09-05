#!/usr/bin/env bash
# Local stand without Telegram: Postgres in Docker, fake Bot API, web, bot (polling the fake),
# worker. Everything under .dev/ (ignored by git). Usage:
#   scripts/dev-stack.sh up      # start (idempotent), migrate, seed + activate the season
#   scripts/dev-stack.sh down    # stop processes and the database container
#   scripts/dev-stack.sh logs    # tail all logs
#   scripts/dev-stack.sh link 1001 Алиса [/app/journal]   # signed Mini App link for a user
set -euo pipefail
cd "$(dirname "$0")/.."
DEV=.dev; mkdir -p "$DEV/logs" "$DEV/media" "$DEV/backups"
PG_NAME=romantika-dev-pg; PG_PORT=${PG_PORT:-55442}
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
# No secrets: the token only signs initData on this machine.
export BOT_TOKEN="1000:DEV-STAND-TOKEN" ADMIN_IDS="${ADMIN_IDS:-900001}" ADMIN_CHAT_ID="${ADMIN_CHAT_ID:-900001}"
export DATABASE_URL="postgresql+asyncpg://romantika:romantika@127.0.0.1:${PG_PORT}/romantika"
export MEDIA_DIR="$PWD/$DEV/media" BACKUPS_DIR="$PWD/$DEV/backups" PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://127.0.0.1:8010}"
export TELEGRAM_API_BASE="http://127.0.0.1:8081" ENV=dev LOG_LEVEL=INFO BOT_USERNAME=romantika_dev_bot
export CHANNEL_URL="https://t.me/romantika_marshrutov"

start() { # name, command...
  local name=$1; shift
  if [ -f "$DEV/$name.pid" ] && kill -0 "$(cat "$DEV/$name.pid")" 2>/dev/null; then echo "$name: already running"; return; fi
  # no nohup: macOS strips DYLD_* from the environment of system binaries, and WeasyPrint needs it
  "$@" >"$DEV/logs/$name.log" 2>&1 < /dev/null &
  echo $! >"$DEV/$name.pid"; echo "$name: pid $!"
}
stop() { local name=$1; if [ -f "$DEV/$name.pid" ]; then kill "$(cat "$DEV/$name.pid")" 2>/dev/null || true; rm -f "$DEV/$name.pid"; echo "$name: stopped"; fi; }

case "${1:-up}" in
  up)
    if ! docker ps --format '{{.Names}}' | grep -qx "$PG_NAME"; then
      docker rm -f "$PG_NAME" >/dev/null 2>&1 || true
      docker run -d --name "$PG_NAME" -e POSTGRES_USER=romantika -e POSTGRES_PASSWORD=romantika -e POSTGRES_DB=romantika -p "127.0.0.1:${PG_PORT}:5432" postgres:16-alpine >/dev/null
      echo "db: started on $PG_PORT"
    fi
    for _ in $(seq 1 30); do docker exec "$PG_NAME" pg_isready -U romantika -d romantika >/dev/null 2>&1 && break; sleep 1; done
    uv run alembic upgrade head
    uv run python -m romantika.ops.seed --activate
    start telegram uv run python -m romantika.ops.fake_telegram --port 8081
    sleep 1
    start web uv run python -m romantika.web
    start bot uv run python -m romantika.bot
    start worker uv run python -m romantika.worker
    sleep 2
    curl -fsS http://127.0.0.1:8010/healthz >/dev/null && echo "web: http://127.0.0.1:8010 (healthz ok)"
    echo "fake Bot API: http://127.0.0.1:8081 (control: /_control/text, /_control/media, /_control/callback, /_control/sent)"
    echo "admin user id: $ADMIN_IDS"
    ;;
  down) for n in worker bot web telegram; do stop $n; done; docker rm -f "$PG_NAME" >/dev/null 2>&1 && echo "db: removed" || true ;;
  restart) for n in worker bot web telegram; do stop $n; done; "$0" up ;;
  logs) tail -n 50 -F "$DEV"/logs/*.log ;;
  link) uv run python -m romantika.ops.dev_link --user "$2" --name "${3:-Гость}" --path "${4:-/app}" ;;
  *) echo "usage: $0 up|down|restart|logs|link <id> <name> [/path]"; exit 2 ;;
esac

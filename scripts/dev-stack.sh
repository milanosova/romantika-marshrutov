#!/usr/bin/env bash
# Local stand: Postgres in Docker, web, bot, worker and thirty invented participants.
# Two modes — «work» talks to a fake Bot API (agents play the participants), «live» runs the
# test bot in real Telegram with the same invented data. Everything under .dev/ (git-ignored).
#   scripts/dev-stack.sh up            # work mode (idempotent): db, migrate, season, demo data, services
#   scripts/dev-stack.sh up --live     # live mode: token from .dev/dev-bot.env, no fake Telegram
#   scripts/dev-stack.sh down          # stop processes and the database container (data kept)
#   scripts/dev-stack.sh reset         # down + drop the stand data (.dev/media too), then up again
#   scripts/dev-stack.sh logs          # tail all logs
#   scripts/dev-stack.sh link 1001 Алиса [/app/journal]   # signed Mini App link for a user
#   scripts/dev-stack.sh link admin [/app/admin]           # the same for the admin of the current mode
#   scripts/dev-stack.sh status        # what is running
# Nothing here touches production; the stand never sees real participant data (CLAUDE.md rule 1).
set -euo pipefail
cd "$(dirname "$0")/.."
DEV=.dev; mkdir -p "$DEV/logs" "$DEV/media" "$DEV/backups" "$DEV/shots"
PG_NAME=romantika-dev-pg; PG_PORT=${PG_PORT:-55442}
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
export DATABASE_URL="postgresql+asyncpg://romantika:romantika@127.0.0.1:${PG_PORT}/romantika"
export MEDIA_DIR="$PWD/$DEV/media" BACKUPS_DIR="$PWD/$DEV/backups" ENV=dev LOG_LEVEL=INFO
export CHANNEL_URL="https://t.me/romantika_marshrutov"

MODE=work
[ "${2:-}" = "--live" ] && MODE=live
# `restart` without a flag keeps the mode the stand was started in (live stays live).
[ "${1:-}" = restart ] && [ -z "${2:-}" ] && MODE=$(cat "$DEV/mode" 2>/dev/null || echo work)

configure_work() {
  # No secrets: the token only signs initData on this machine.
  export BOT_TOKEN="1000:DEV-STAND-TOKEN" ADMIN_IDS="${ADMIN_IDS:-900001}" ADMIN_CHAT_ID="${ADMIN_CHAT_ID:-900001}"
  export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://127.0.0.1:8010}"
  export TELEGRAM_API_BASE="http://127.0.0.1:8081" BOT_USERNAME=romantika_dev_bot
}

configure_live() {
  # .dev/dev-bot.env: BOT_TOKEN, BOT_USERNAME, ADMIN_IDS (real Telegram ids of Mila and Dima),
  # optional TELEGRAM_PROXY. Fetched from the VPS per docs/SETUP-RU.md, never committed.
  if [ ! -f "$DEV/dev-bot.env" ]; then
    echo "live mode needs $DEV/dev-bot.env (see docs/SETUP-RU.md, «Тестовый бот»)"; exit 1
  fi
  set -a; . "$DEV/dev-bot.env"; set +a
  [ -n "${BOT_TOKEN:-}" ] || { echo "$DEV/dev-bot.env has no BOT_TOKEN"; exit 1; }
  export ADMIN_IDS="${ADMIN_IDS:-900001}" ADMIN_CHAT_ID="${ADMIN_CHAT_ID:-${ADMIN_IDS%%,*}}"
  export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://127.0.0.1:8010}"
  export TELEGRAM_API_BASE=""
  # The token never appears in a command line (not in `ps`, not in what Claude sees): curl reads
  # its config from stdin, and only the verdict of the answer is printed.
  local me
  me=$( { printf 'url = "https://api.telegram.org/bot%s/getMe"\nsilent\nshow-error\nmax-time = 8\n' "$BOT_TOKEN"
         [ -n "${TELEGRAM_PROXY:-}" ] && printf 'proxy = "%s"\n' "$TELEGRAM_PROXY"; } | curl --config - 2>&1 || true)
  if ! printf '%s' "$me" | grep -q '"ok":true'; then
    echo "Telegram не отвечает — включи VPN (или задай TELEGRAM_PROXY в $DEV/dev-bot.env) и повтори"
    echo "  ответ: $(printf '%s' "$me" | grep -oE '"error_code":[0-9]+|"description":"[^"]{0,60}"|curl: \([0-9]+\)[^:]{0,60}' | head -1)"; exit 1
  fi
  echo "telegram: ok (@${BOT_USERNAME:-?})"
}

start() { # name, command...
  local name=$1; shift
  if [ -f "$DEV/$name.pid" ] && kill -0 "$(cat "$DEV/$name.pid")" 2>/dev/null; then echo "$name: already running"; return; fi
  # no nohup: macOS strips DYLD_* from the environment of system binaries, and WeasyPrint needs it
  "$@" >"$DEV/logs/$name.log" 2>&1 < /dev/null &
  echo $! >"$DEV/$name.pid"; echo "$name: pid $!"
}
stop() { # name — terminate and wait, so a restart never races the old process for its port
  local name=$1 pid
  [ -f "$DEV/$name.pid" ] || return 0
  pid=$(cat "$DEV/$name.pid"); rm -f "$DEV/$name.pid"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 40); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
  fi
  echo "$name: stopped"
}

db_up() {
  docker info >/dev/null 2>&1 || { echo "Docker не запущен — открой Docker Desktop и повтори"; exit 1; }
  if docker ps --format '{{.Names}}' | grep -qx "$PG_NAME"; then
    :
  elif docker ps -a --format '{{.Names}}' | grep -qx "$PG_NAME"; then
    docker start "$PG_NAME" >/dev/null && echo "db: resumed (data kept from the previous run)"
  else
    docker run -d --name "$PG_NAME" -e POSTGRES_USER=romantika -e POSTGRES_PASSWORD=romantika -e POSTGRES_DB=romantika \
      -p "127.0.0.1:${PG_PORT}:5432" postgres:16-alpine >/dev/null
    echo "db: started on $PG_PORT"
  fi
  for _ in $(seq 1 30); do docker exec "$PG_NAME" pg_isready -U romantika -d romantika >/dev/null 2>&1 && break; sleep 1; done
  uv run alembic upgrade head
  uv run python -m romantika.ops.seed --activate
  uv run python -m romantika.ops.demo_data
}

up() {
  if [ "$MODE" = live ]; then configure_live; else configure_work; fi
  echo "$MODE" > "$DEV/mode"
  db_up
  if [ "$MODE" = work ]; then
    start telegram uv run python -m romantika.ops.fake_telegram --port 8081
    sleep 1
  else
    stop telegram
  fi
  start web uv run python -m romantika.web
  start bot uv run python -m romantika.bot
  start worker uv run python -m romantika.worker
  for _ in $(seq 1 20); do curl -fsS http://127.0.0.1:8010/healthz >/dev/null 2>&1 && break; sleep 0.5; done
  curl -fsS http://127.0.0.1:8010/healthz >/dev/null && echo "web: $PUBLIC_BASE_URL (healthz ok)" || { echo "web did not come up — see $DEV/logs/web.log"; exit 1; }
  if [ "$MODE" = work ]; then
    echo "fake Bot API: http://127.0.0.1:8081 (control: /_control/text, /_control/media, /_control/callback, /_control/sent)"
  else
    echo "live: напиши боту @${BOT_USERNAME:-?} в Telegram /start; логи — $DEV/logs/bot.log"
  fi
  echo "admin user id: $ADMIN_IDS · demo participants: 1001…1030 · mode: $MODE"
}

case "${1:-up}" in
  up) up ;;
  down) for n in worker bot web telegram; do stop $n; done; docker stop "$PG_NAME" >/dev/null 2>&1 && echo "db: stopped (data kept; reset drops it)" || true ;;
  reset)
    for n in worker bot web telegram; do stop $n; done
    docker rm -f "$PG_NAME" >/dev/null 2>&1 && echo "db: removed" || true
    find "$DEV/media" -mindepth 1 -delete 2>/dev/null || true
    echo "stand data dropped (.dev/media, stand database); production untouched"
    up ;;
  restart) for n in worker bot web telegram; do stop $n; done; if [ "$MODE" = live ]; then "$0" up --live; else "$0" up; fi ;;
  logs) tail -n 50 -F "$DEV"/logs/*.log ;;
  status)
    echo "mode: $(cat "$DEV/mode" 2>/dev/null || echo '-')"
    for n in telegram web bot worker; do
      if [ -f "$DEV/$n.pid" ] && kill -0 "$(cat "$DEV/$n.pid")" 2>/dev/null; then echo "$n: running (pid $(cat "$DEV/$n.pid"))"; else echo "$n: stopped"; fi
    done
    docker ps --format '{{.Names}}: {{.Status}}' | grep "$PG_NAME" || echo "db: stopped" ;;
  link)
    # `link admin [/path]` signs as the first admin of the current mode (900001 in work mode, the
    # real Telegram id from .dev/dev-bot.env in live mode). First printed line is the URL for a
    # browser; the second is the header value for API calls.
    if [ "$(cat "$DEV/mode" 2>/dev/null)" = live ]; then set -a; . "$DEV/dev-bot.env"; set +a; else configure_work; fi
    export ADMIN_IDS="${ADMIN_IDS:-900001}"
    if [ "${2:-}" = admin ]; then
      uv run python -m romantika.ops.dev_link --user "${ADMIN_IDS%%,*}" --name "Мила" --path "${3:-/app/admin}"
    else
      uv run python -m romantika.ops.dev_link --user "$2" --name "${3:-Гость}" --path "${4:-/app}"
    fi ;;
  *) echo "usage: $0 up [--live] | down | reset | restart | logs | status | link <id> <name> [/path] | link admin [/path]"; exit 2 ;;
esac

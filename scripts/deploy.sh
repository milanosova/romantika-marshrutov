#!/usr/bin/env bash
# Deploy to the VPS: rsync the repository (no data, no .env), build the image ON the VPS
# (amd64, through the host proxy for apt/PyPI), run migrations, restart the stack, smoke-check.
#
#   scripts/deploy.sh            # full deploy
#   DRY=1 scripts/deploy.sh      # only show what rsync would send
#   HOST=vps247 DEST=/opt/stacks/romantika scripts/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${HOST:-vps247}"
DEST="${DEST:-/opt/stacks/romantika}"
PROXY="${BUILD_PROXY:-http://172.17.0.1:10809}"
COMPOSE="docker compose -f docker/compose.yml -f docker/compose.vps.yml --project-directory ."

RSYNC_OPTS=(-az --delete
  --exclude .git --exclude .venv --exclude .dev --exclude data/media --exclude data/backups --exclude data/postgres --exclude .env --exclude '.env.*' --exclude legacy
  --exclude __pycache__ --exclude .pytest_cache --exclude .mypy_cache --exclude .ruff_cache
  --exclude '*.sqlite' --exclude journals --exclude .playwright-mcp)
if [ "${DRY:-0}" = "1" ]; then
  rsync "${RSYNC_OPTS[@]}" --dry-run -v ./ "$HOST:$DEST/" | head -50
  exit 0
fi

ssh "$HOST" "mkdir -p '$DEST/data/backups'"
rsync "${RSYNC_OPTS[@]}" ./ "$HOST:$DEST/"

ssh "$HOST" bash -s <<EOF
set -euo pipefail
cd "$DEST"
test -f .env || { echo "no $DEST/.env on the VPS — copy it first (see docs/RUNBOOK.md)"; exit 1; }
chmod 600 .env
echo "== build (one image, sequential, via proxy $PROXY)"
nice -n 10 $COMPOSE build \
  --build-arg http_proxy=$PROXY --build-arg https_proxy=$PROXY \
  --build-arg HTTP_PROXY=$PROXY --build-arg HTTPS_PROXY=$PROXY \
  --build-arg no_proxy=localhost,127.0.0.1 --build-arg NO_PROXY=localhost,127.0.0.1 \
  migrate
echo "== migrate + up"
$COMPOSE up -d --remove-orphans
sleep 25
$COMPOSE ps
echo "== smoke"
curl -fsS http://127.0.0.1:8010/healthz && echo
$COMPOSE logs --tail 20 bot worker | tail -40
EOF
echo "deployed to $HOST:$DEST"

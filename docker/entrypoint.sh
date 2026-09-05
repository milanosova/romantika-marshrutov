#!/usr/bin/env bash
# Dispatch the container command: bot | web | worker | migrate | backup | shell.
set -euo pipefail
cd /app
case "${1:-web}" in
  bot)     exec python -m romantika.bot ;;
  web)     exec python -m romantika.web ;;
  worker)  exec python -m romantika.worker ;;
  migrate) exec alembic upgrade head ;;
  backup)  exec bash scripts/backup-loop.sh ;;
  shell)   exec bash ;;
  *)       exec "$@" ;;
esac

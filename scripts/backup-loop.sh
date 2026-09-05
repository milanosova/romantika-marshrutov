#!/usr/bin/env bash
# The `backup` container: backup every night at 03:30 Moscow, restore-verify every Sunday 04:30.
# A tiny loop instead of cron so the image stays one process, one log.
set -uo pipefail
cd "$(dirname "$0")/.."
export TZ="${TZ:-Europe/Moscow}"
BACKUP_AT="${BACKUP_AT:-03:30}"
VERIFY_AT="${VERIFY_AT:-04:30}"
VERIFY_DAY="${VERIFY_DAY:-7}"   # 1 = Monday … 7 = Sunday
last_backup=""
last_verify=""
echo "backup loop: backup at $BACKUP_AT daily, verify at $VERIFY_AT on weekday $VERIFY_DAY"
if [ "${RUN_ON_START:-0}" = "1" ]; then
  scripts/backup.sh || echo "backup failed on start"
fi
while true; do
  now="$(date +%H:%M)"; today="$(date +%F)"; weekday="$(date +%u)"
  if [ "$now" = "$BACKUP_AT" ] && [ "$last_backup" != "$today" ]; then
    last_backup="$today"
    scripts/backup.sh || echo "backup failed $today"
  fi
  if [ "$now" = "$VERIFY_AT" ] && [ "$weekday" = "$VERIFY_DAY" ] && [ "$last_verify" != "$today" ]; then
    last_verify="$today"
    scripts/restore-verify.sh || echo "restore-verify failed $today"
  fi
  sleep 30
done

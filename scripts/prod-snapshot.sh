#!/usr/bin/env bash
# Read-only snapshot of production for `/prod` and the release window question in `/relize`.
# Prints Markdown. Every database query here is listed in docs/RUNBOOK.md «Read-only queries».
#   scripts/prod-snapshot.sh            # full snapshot
#   scripts/prod-snapshot.sh --brief    # activity only (used by /relize)
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${HOST:-romantika-vps}"
DEST="${DEST:-/opt/stacks/romantika}"
PUBLIC_URL="${PUBLIC_URL:-https://romantika.vibe-coding.trade}"
BRIEF=0; [ "${1:-}" = "--brief" ] && BRIEF=1

q() { # one read-only SQL statement against the production database, single value or rows
  scripts/rc.sh exec -T db psql -U romantika -d romantika -Atc "$1" 2>/dev/null | tr '\n' ' ' | sed 's/ *$//'
}

now=$(date '+%Y-%m-%d %H:%M %Z')
echo "# Снимок прода · $now"
echo
echo "## Активность"
echo "- участниц всего (не заблокировали бота): $(q "select count(*) from users where blocked_at is null and is_admin = false")"
echo "- писали боту за последний час: $(q "select count(distinct user_id) from reports where created_at > now() - interval '1 hour'")"
echo "- писали боту за сутки: $(q "select count(distinct user_id) from reports where created_at > now() - interval '24 hours'")"
echo "- писали боту за неделю: $(q "select count(distinct user_id) from reports where created_at > now() - interval '7 days'")"
echo "- отчётов за неделю: $(q "select count(*) from reports where created_at > now() - interval '7 days' and deleted_at is null")"
echo "- писем без ответа: $(q "select count(*) from letters where replied_at is null")"
[ "$BRIEF" = 1 ] && exit 0

echo
echo "## Живость"
health=$(curl -sS --max-time 10 "$PUBLIC_URL/healthz" || echo "нет ответа")
echo "- healthz: \`$health\`"
echo '- контейнеры:'
echo '```'
scripts/rc.sh ps --format 'table {{.Name}}\t{{.Status}}' 2>/dev/null || echo "rc ps failed"
echo '```'
echo "- диск: $(ssh "$HOST" "df -h $DEST | tail -1 | awk '{print \$4\" свободно из \"\$2\" (\"\$5\" занято)\"}'")"

echo
echo "## Бэкапы"
echo '```'
ssh "$HOST" "cat $DEST/data/backups/last-verify.json 2>/dev/null || echo 'last-verify.json отсутствует'"
echo
ssh "$HOST" "ls -1t $DEST/data/backups/db/ 2>/dev/null | head -3"
echo '```'

echo
echo "## Ошибки в логах за сутки"
echo '```'
scripts/rc.sh logs --since 24h bot web worker 2>/dev/null | grep -iE '"level": *"(ERROR|CRITICAL)"|Traceback' | tail -20 || true
echo '```'
echo "- строк с ошибками за сутки: $(scripts/rc.sh logs --since 24h bot web worker 2>/dev/null | grep -icE '"level": *"(ERROR|CRITICAL)"' || echo 0)"
echo "- сбойных задач воркера за сутки: $(q "select count(*) from jobs where status = 'failed' and finished_at > now() - interval '24 hours'")"

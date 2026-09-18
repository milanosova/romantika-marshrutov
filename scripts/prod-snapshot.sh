#!/usr/bin/env bash
# Read-only snapshot of production for `/prod` and the release-window question in `/relize`.
# Prints Markdown that is safe to keep in git (brain/ops/): counts and error classes only, never
# raw log lines (they carry participant ids and texts). Raw error lines go to .dev/prod-logs/
# (git-ignored) and the path is printed. Every SQL statement here is listed in docs/RUNBOOK.md
# «Read-only queries» and runs through scripts/rc.sh, which allows nothing but a select.
#   scripts/prod-snapshot.sh            # full snapshot
#   scripts/prod-snapshot.sh --brief    # activity only (used by /relize)
set -euo pipefail
cd "$(dirname "$0")/.."
PUBLIC_URL="${PUBLIC_URL:-https://romantika.vibe-coding.trade}"
BRIEF=0; [ "${1:-}" = "--brief" ] && BRIEF=1
FAILED=0
redact() { sed -E 's#bot[0-9]+:[A-Za-z0-9_-]+#bot<redacted>#g'; }

q() { # one read-only statement → a number, or «ошибка» (and the snapshot is marked failed)
  local out
  out=$(scripts/rc.sh exec -T db psql -U romantika -d romantika -Atc "$1" 2>/dev/null | tr -d '[:space:]' || true)
  if printf '%s' "$out" | grep -qE '^[0-9]+$'; then printf '%s' "$out"; else FAILED=1; printf 'ошибка'; fi
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
echo "- поздних отчётов (в журнал, без штампа): $(q "select count(*) from reports where late and deleted_at is null")"
if [ "$BRIEF" = 1 ]; then [ "$FAILED" = 0 ] || { echo; echo "**снимок неполный: часть запросов не выполнилась (ssh? rc.sh?)**"; exit 1; }; exit 0; fi

echo
echo "## Живость"
health=$(curl -sS --max-time 10 "$PUBLIC_URL/healthz" 2>/dev/null | redact || echo "нет ответа")
echo "- healthz: \`${health:-нет ответа}\`"
echo '- контейнеры:'
echo '```'
scripts/rc.sh ps --format 'table {{.Name}}\t{{.Status}}' 2>/dev/null || { echo "rc ps failed"; FAILED=1; }
echo '```'
disk=$(scripts/rc.sh run --rm -T --no-deps --entrypoint df web -hP /media 2>/dev/null | tail -1 | awk '{print $4" свободно из "$2" ("$5" занято)"}' || true)
echo "- диск с фотографиями: ${disk:-ошибка}"

echo
echo "## Бэкапы"
echo '```'
scripts/rc.sh exec -T backup sh -c 'cat /backups/last-verify.json 2>/dev/null || echo last-verify.json отсутствует; echo; ls -1t /backups/db 2>/dev/null | head -3' 2>/dev/null | redact || { echo "rc exec backup failed"; FAILED=1; }
echo '```'

echo
echo "## Ошибки в логах за сутки"
mkdir -p .dev/prod-logs
raw=".dev/prod-logs/$(date '+%Y-%m-%d-%H%M').log"
scripts/rc.sh logs --since 24h --no-log-prefix bot web worker 2>/dev/null | grep -iE '"level": *"(ERROR|CRITICAL)"|Traceback' | redact > "$raw" || true
echo "- строк с ошибками за сутки: $(wc -l < "$raw" | tr -d ' ')"
echo "- сбойных задач воркера за сутки: $(q "select count(*) from jobs where status = 'failed' and finished_at > now() - interval '24 hours'")"
if [ -s "$raw" ]; then
  echo "- классы ошибок (логгер · сообщение · сколько раз):"
  echo '```'
  grep -oE '"logger": *"[^"]+"|"message": *"[^"]{0,80}' "$raw" | paste - - 2>/dev/null | sort | uniq -c | sort -rn | head -10 | sed 's/"logger": *//; s/"message": *//'
  echo '```'
  echo "- сырые строки (не для git): \`$raw\`"
fi
[ "$FAILED" = 0 ] || { echo; echo "**снимок неполный: часть запросов не выполнилась — не делай вывод «норма»**"; exit 1; }

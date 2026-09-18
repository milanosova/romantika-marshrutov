#!/usr/bin/env bash
# Screenshots of the Mini App screens on the local stand, through headless Google Chrome.
#   scripts/shots.sh                                  # every participant screen as user 1001 into .dev/shots/
#   scripts/shots.sh --as 900001 --name Мила --page /app/admin --out brain/tasks/03-x/before
#   scripts/shots.sh --page all --dark                # both participant and admin screens, dark theme
# Pages: /app (week) /app/bag /app/season /app/admin / /calendar — old names (/app/passport …) still open the right tab
# /app/admin is signed as ADMIN_UID (default 900001, the work-mode admin); --as applies to /app* pages.
# Files: <out>/<page-slug>[-<id>][-dark].png (the id suffix appears when --as is given, so two
# participants never overwrite each other). Needs the stand up (scripts/dev-stack.sh up) and Chrome.
set -euo pipefail
cd "$(dirname "$0")/.."
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
USER_ID=1001; NAME="Алиса"; PAGE=""; OUT=.dev/shots; DARK=0; WIDTH=500; HEIGHT=1100; AS_GIVEN=0
PARTICIPANT_PAGES=(/app /app/bag /app/season)
ADMIN_PAGES=(/app/admin)
PUBLIC_PAGES=(/ /calendar)
while [ $# -gt 0 ]; do
  case "$1" in
    --as) USER_ID=$2; AS_GIVEN=1; shift 2 ;;
    --name) NAME=$2; shift 2 ;;
    --page) PAGE=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    --dark) DARK=1; shift ;;
    --width) WIDTH=$2; shift 2 ;;
    --height) HEIGHT=$2; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done
[ -x "$CHROME" ] || { echo "Google Chrome not found at $CHROME — install it or take the screenshots by hand"; exit 1; }
curl -fsS http://127.0.0.1:8010/healthz >/dev/null || { echo "the stand is not up: scripts/dev-stack.sh up"; exit 1; }
[ "$WIDTH" -ge 500 ] || echo "note: headless Chrome renders at 500px minimum; using $WIDTH may crop" >&2
mkdir -p "$OUT"

# The stand's environment (token, base URL) is what dev_link signs with.
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
export BOT_TOKEN="${BOT_TOKEN:-1000:DEV-STAND-TOKEN}" ENV=dev MEDIA_DIR="${MEDIA_DIR:-$PWD/.dev/media}" \
  PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://127.0.0.1:8010}" ADMIN_IDS="${ADMIN_IDS:-900001}"

pages=()
case "$PAGE" in
  "") pages=("${PARTICIPANT_PAGES[@]}") ;;
  all) pages=("${PARTICIPANT_PAGES[@]}" "${ADMIN_PAGES[@]}" "${PUBLIC_PAGES[@]}") ;;
  *) pages=("$PAGE") ;;
esac

shot() { # url, file
  local url=$1 file=$2
  if [ "$DARK" = 1 ]; then
    "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-dark-mode --enable-features=WebContentsForceDark \
      --window-size="$WIDTH,$HEIGHT" --virtual-time-budget=6000 --timeout=20000 --screenshot="$file" "$url" >/dev/null 2>&1
  else
    "$CHROME" --headless=new --disable-gpu --hide-scrollbars \
      --window-size="$WIDTH,$HEIGHT" --virtual-time-budget=6000 --timeout=20000 --screenshot="$file" "$url" >/dev/null 2>&1
  fi
}

for page in "${pages[@]}"; do
  slug=$(echo "$page" | sed 's#^/$#season#; s#^/##; s#/#-#g')
  suffix=""; [ "$AS_GIVEN" = 1 ] && [ "$page" != /app/admin ] && suffix="-$USER_ID"; [ "$DARK" = 1 ] && suffix="$suffix-dark"
  file="$OUT/$slug$suffix.png"
  case "$page" in
    /app/admin) uid=${ADMIN_UID:-900001}; uname="Мила" ;;
    /app*) uid=$USER_ID; uname=$NAME ;;
    *) uid=""; uname="" ;;
  esac
  if [ -n "$uid" ]; then
    signed=$(uv run python -m romantika.ops.dev_link --user "$uid" --name "$uname" --path "$page")
    url=${signed%%$'\n'*}
  else
    url="http://127.0.0.1:8010$page"
  fi
  shot "$url" "$file" || true
  [ -s "$file" ] && echo "$file" || echo "failed: $page" >&2
done

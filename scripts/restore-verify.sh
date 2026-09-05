#!/usr/bin/env bash
# Restore the latest dump into a scratch database and verify counts and media hashes.
# Env: BACKUP_DIR, SCRATCH_DATABASE_URL (dropped and recreated!), MEDIA_SAMPLE (20).
# Writes $BACKUP_DIR/last-verify.json; exit 1 on any error.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
exec "$PY" -m romantika.ops.restore_verify "$@"

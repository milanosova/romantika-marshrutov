#!/usr/bin/env bash
# Nightly backup: pg_dump + hard-linked media snapshot + manifest (ARCHITECTURE §11.1).
# Env: DATABASE_URL, MEDIA_DIR, BACKUP_DIR, RETENTION_DAYS (30), TODAY (tests).
# Never touches MEDIA_DIR; only reads it.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
exec "$PY" -m romantika.ops.backup "$@"

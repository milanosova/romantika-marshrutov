"""Prove the latest backup restores: scratch database + row counts + media hashes.

Environment (ARCHITECTURE §11.1): BACKUP_DIR, SCRATCH_DATABASE_URL (a database this script may
drop and recreate), optional MEDIA_SAMPLE (20). Writes `<BACKUP_DIR>/last-verify.json` and
exits 1 on any error — a backup that was never restored is not a backup.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from romantika.ops.backup import TABLES, dated, pg_url, sha256_of

#: A newer pg_dump than the server emits SET lines the server does not know (e.g. pg_dump 18
#: → Postgres 16 on a developer Mac). They carry no data; the row counts below are the proof.
IGNORABLE_RESTORE_ERRORS = ('unrecognized configuration parameter "transaction_timeout"',)


def _ignorable(line: str) -> bool:
    return any(marker in line for marker in IGNORABLE_RESTORE_ERRORS) or "Command was: SET" in line


def latest_dump(backup_dir: Path) -> Path | None:
    dumps = [p for p in (backup_dir / "db").glob("romantika-*.dump") if dated(p.name, "romantika-", ".dump")]
    return max(dumps, key=lambda p: p.name) if dumps else None


async def recreate_scratch(scratch_url: str) -> None:
    url = pg_url(scratch_url)
    base, _, name = url.rpartition("/")
    name = name.split("?", 1)[0]
    admin = await asyncpg.connect(f"{base}/postgres")
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()


async def run() -> int:
    backup_dir = Path(os.environ["BACKUP_DIR"])
    scratch_url = os.environ["SCRATCH_DATABASE_URL"]
    sample_size = int(os.environ.get("MEDIA_SAMPLE", "20"))
    now = datetime.now(UTC)
    errors: list[str] = []
    status: dict[str, object] = {
        "ok": False,
        "checked_at": now.isoformat(),
        "dump": None,
        "tables": {},
        "media_checked": 0,
        "errors": errors,
    }

    dump = latest_dump(backup_dir)
    if dump is None:
        errors.append("no dump found")
        return finish(backup_dir, status)
    status["dump"] = dump.name
    when = dated(dump.name, "romantika-", ".dump")
    manifest_path = backup_dir / f"manifest-{when}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    if manifest is None:
        errors.append(f"manifest for {dump.name} is missing")
    elif manifest.get("dump_sha256") != sha256_of(dump):
        errors.append("dump sha256 does not match the manifest")

    await recreate_scratch(scratch_url)
    restore = subprocess.run(
        ["pg_restore", "--no-owner", "--no-privileges", "-d", pg_url(scratch_url), str(dump)],
        capture_output=True,
        text=True,
    )
    if restore.returncode != 0:
        fatal = [line for line in restore.stderr.splitlines() if "error:" in line and not _ignorable(line)]
        if fatal:
            errors.append(f"pg_restore failed: {' | '.join(fatal)[-500:]}")
            return finish(backup_dir, status)
        status["warnings"] = [line.strip() for line in restore.stderr.splitlines() if "error:" in line][:10]

    connection = await asyncpg.connect(pg_url(scratch_url))
    try:
        counts: dict[str, int] = {}
        for table in TABLES:
            counts[table] = int(await connection.fetchval(f'SELECT count(*) FROM "{table}"'))
        status["tables"] = counts
        if manifest is not None:
            for table, expected in manifest.get("tables", {}).items():
                if counts.get(table) != expected:
                    errors.append(f"row count mismatch: {table} {counts.get(table)} != {expected}")
        snapshot = (
            backup_dir / str(manifest.get("media_snapshot", f"media/{when}"))
            if manifest
            else backup_dir / "media" / str(when)
        )
        rows = await connection.fetch(
            "SELECT path, sha256 FROM media WHERE sha256 IS NOT NULL AND downloaded_at IS NOT NULL"
        )
        sample = random.sample(list(rows), min(sample_size, len(rows)))
        checked = 0
        for row in sample:
            path = snapshot / str(row["path"])
            if not path.exists():
                errors.append(f"media missing in snapshot: {row['path']}")
                continue
            if sha256_of(path) != row["sha256"]:
                errors.append(f"media sha256 mismatch: {row['path']}")
                continue
            checked += 1
        status["media_checked"] = checked
        if rows and checked == 0:
            errors.append("no media file could be verified")
    finally:
        await connection.close()
    return finish(backup_dir, status)


def finish(backup_dir: Path, status: dict[str, object]) -> int:
    errors = status["errors"]
    status["ok"] = not errors
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "last-verify.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))
    return 0 if status["ok"] else 1


def main() -> None:
    import asyncio

    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()

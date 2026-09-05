"""Nightly backup: `pg_dump` of the database, hard-linked snapshot of the media, manifest.

Driven by environment variables (ARCHITECTURE §11.1): DATABASE_URL, MEDIA_DIR, BACKUP_DIR,
RETENTION_DAYS (30), TODAY (YYYY-MM-DD, for tests). Never touches MEDIA_DIR itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import asyncpg

TABLES = (
    "users", "seasons", "weeks", "achievement_types", "season_members", "intents", "reports", "media",
    "stamps", "freezes", "achievements", "words", "facts", "wishes", "admin_links", "dialog_states",
    "settings", "reminder_log", "audit_log", "jobs",
)  # fmt: skip


def pg_url(database_url: str) -> str:
    """SQLAlchemy URL → libpq URL (`postgresql+asyncpg://` → `postgresql://`)."""
    scheme, _, rest = database_url.partition("://")
    return f"postgresql://{rest}" if scheme.startswith("postgresql") else database_url


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def table_counts(database_url: str) -> dict[str, int]:
    connection = await asyncpg.connect(pg_url(database_url))
    try:
        counts: dict[str, int] = {}
        for table in TABLES:
            counts[table] = int(await connection.fetchval(f'SELECT count(*) FROM "{table}"'))
        return counts
    finally:
        await connection.close()


def snapshot_media(media_dir: Path, snapshot_dir: Path, previous: Path | None) -> tuple[int, int]:
    """Copy the media tree, hard-linking files unchanged since the previous snapshot."""
    snapshot_dir.parent.mkdir(parents=True, exist_ok=True)
    command = ["rsync", "-a", "--delete"]
    if previous is not None and previous.is_dir():
        command.append(f"--link-dest={previous.resolve()}")
    command += [f"{media_dir.resolve()}/", f"{snapshot_dir.resolve()}/"]
    if media_dir.exists():
        subprocess.run(command, check=True)
    else:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
    files = [p for p in snapshot_dir.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def dated(name: str, prefix: str, suffix: str) -> date | None:
    if not (name.startswith(prefix) and name.endswith(suffix)):
        return None
    try:
        return date.fromisoformat(name[len(prefix) : len(name) - len(suffix)])
    except ValueError:
        return None


def apply_retention(backup_dir: Path, today: date, keep_days: int) -> list[str]:
    """Delete dumps, media snapshots and manifests older than `keep_days` by their date name."""
    cutoff = today - timedelta(days=keep_days)
    removed: list[str] = []
    for path in sorted((backup_dir / "db").glob("romantika-*.dump")):
        when = dated(path.name, "romantika-", ".dump")
        if when is not None and when < cutoff:
            path.unlink()
            removed.append(path.name)
    for path in sorted((backup_dir / "media").glob("*")):
        when = dated(path.name, "", "")
        if path.is_dir() and when is not None and when < cutoff:
            shutil.rmtree(path)
            removed.append(f"media/{path.name}")
    for path in sorted(backup_dir.glob("manifest-*.json")):
        when = dated(path.name, "manifest-", ".json")
        if when is not None and when < cutoff:
            path.unlink()
            removed.append(path.name)
    return removed


def latest_snapshot(backup_dir: Path, before: date) -> Path | None:
    candidates = [
        p
        for p in (backup_dir / "media").glob("*")
        if p.is_dir() and (d := dated(p.name, "", "")) is not None and d < before
    ]
    return max(candidates, key=lambda p: p.name) if candidates else None


async def run() -> int:
    database_url = os.environ["DATABASE_URL"]
    media_dir = Path(os.environ["MEDIA_DIR"])
    backup_dir = Path(os.environ["BACKUP_DIR"])
    keep_days = int(os.environ.get("RETENTION_DAYS", "30"))
    today = date.fromisoformat(os.environ["TODAY"]) if os.environ.get("TODAY") else datetime.now(UTC).date()
    stamp = today.isoformat()

    (backup_dir / "db").mkdir(parents=True, exist_ok=True)
    dump = backup_dir / "db" / f"romantika-{stamp}.dump"
    tmp = dump.with_suffix(".dump.part")
    subprocess.run(
        ["pg_dump", "-Fc", "--no-owner", "--no-privileges", "-f", str(tmp), pg_url(database_url)], check=True
    )
    tmp.replace(dump)

    previous = latest_snapshot(backup_dir, today)
    files, size = snapshot_media(media_dir, backup_dir / "media" / stamp, previous)
    counts = await table_counts(database_url)
    manifest = {
        "date": stamp,
        "created_at": datetime.now(UTC).isoformat(),
        "dump": dump.name,
        "dump_sha256": sha256_of(dump),
        "dump_bytes": dump.stat().st_size,
        "tables": counts,
        "media_files": files,
        "media_bytes": size,
        "media_snapshot": f"media/{stamp}",
    }
    (backup_dir / f"manifest-{stamp}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    removed = apply_retention(backup_dir, today, keep_days)
    print(json.dumps({"ok": True, "dump": dump.name, "media_files": files, "removed": removed}, ensure_ascii=False))
    return 0


def main() -> None:
    import asyncio

    try:
        sys.exit(asyncio.run(run()))
    except subprocess.CalledProcessError as exc:
        print(json.dumps({"ok": False, "error": f"{exc.cmd[0]} exited with {exc.returncode}"}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

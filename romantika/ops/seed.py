"""`python -m romantika.ops.seed [--file data/seasons/mexico-2026.json] [--activate]`.

Imports (or re-imports) a season description and optionally makes it the active season.
Safe to run again: the import is an upsert and never deletes rows.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from romantika.config import DATA_DIR, get_settings
from romantika.db.session import make_session_factory
from romantika.services import content, seed


async def run(path: Path, *, activate: bool) -> None:
    settings = get_settings()
    factory = make_session_factory(settings.database_url)
    async with factory() as session, session.begin():
        result = await seed.import_season(session, path)
        status = "unchanged"
        if activate:
            status = (await content.activate_season(session, result.season_id, actor_id=None)).status
        print(
            f"season {result.slug}: created={result.created} weeks={result.weeks} "
            f"achievement_types={result.achievement_types} status={status}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a season file into the database")
    parser.add_argument("--file", default=str(DATA_DIR / "seasons" / "mexico-2026.json"))
    parser.add_argument("--activate", action="store_true", help="make this the active season")
    args = parser.parse_args()
    asyncio.run(run(Path(args.file), activate=args.activate))


if __name__ == "__main__":
    main()

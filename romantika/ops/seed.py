"""`python -m romantika.ops.seed [--file data/seasons/mexico-2026.json] [--activate]`.

Imports a season description and optionally makes it the active season.
Safe to run again while the season is untouched: the import is an upsert and never
deletes rows. Once the admin app has created, moved, deleted or announced a week the
file is no longer the source of truth — the import is then skipped with a line saying so
(exit 0: a stand or a deploy keeps starting), and `--activate` still runs.
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
        try:
            result = await seed.import_season(session, path)
        except seed.CalendarOwnedByAdmin as exc:
            # Raised before any write, so the session holds nothing to undo; the season row
            # already exists, and activating it is still a valid ask.
            print(f"seed skipped: {exc}")
            if activate:
                season = await seed.season_by_file(session, path)
                activated = await content.activate_season(session, season.id, actor_id=None)
                print(f"season {season.slug}: status={activated.status.value}")
            return
        status = "unchanged"
        if activate:
            status = (await content.activate_season(session, result.season_id, actor_id=None)).status.value
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

# RUNBOOK — Romantika Marshrutov v2

Operations guide for the person who runs the stack (English; the owner's guide is `GUIDE-RU.md`).

## Topology

- VPS (shared with other stacks), Docker Compose project `romantika` in `/opt/stacks/romantika`.
- Services: `db` (postgres:16-alpine, volume `pgdata`), `migrate` (one-shot), `bot`, `web`
  (`127.0.0.1:8010`), `worker`, `backup`. Media in the named volume `media`, backups in the
  bind mount `./data/backups` (so the Mac can rsync them).
- Public HTTPS: `https://romantika.vibe-coding.trade` → cloudflared tunnel on the host →
  `localhost:8010`. The bot and the worker reach Telegram through the host proxy
  `host.docker.internal:10809` (see `docker/compose.vps.yml`).
- Secrets only in `/opt/stacks/romantika/.env` (chmod 600): `BOT_TOKEN`, `ADMIN_IDS`,
  `ADMIN_CHAT_ID`, `POSTGRES_PASSWORD`, `PUBLIC_BASE_URL`, `BOT_USERNAME`, `CHANNEL_URL`.
- Telegram from the containers goes through `HTTPS_PROXY` (set by `compose.vps.yml`); aiogram
  reads it via `TELEGRAM_PROXY`/`HTTPS_PROXY` (`romantika/bot/factory.py`, needs `aiohttp-socks`).
- Season content: `rc exec -T bot python -m romantika.ops.seed --activate` (idempotent).

## Deploy

```bash
scripts/deploy.sh                 # from the repo root on the Mac
DRY=1 scripts/deploy.sh           # see what rsync would send
```

What it does: rsync the repository (no `.git`, `.venv`, `data`, `.env`, `legacy`) →
`docker compose build migrate` on the VPS through the host proxy (apt/PyPI are blocked from
the RU datacenter otherwise) → `docker compose up -d` (migrations run first, the rest waits for
them) → `curl /healthz` → tail of bot/worker logs. The image is built once and shared by all
services. First deploy: put `.env` in place before running it.

Compose on the VPS: `cd /opt/stacks/romantika && docker compose -f docker/compose.yml -f docker/compose.vps.yml --project-directory . <cmd>`.
Alias it as `rc` in your shell.

## Logs

```bash
rc logs --tail 100 -f bot          # or web / worker / backup / db
rc ps                              # health of every service
docker stats --no-stream           # CPU/RAM against the limits in compose.vps.yml
```

Logs are JSON lines in prod (`ENV=prod`). The worker logs every job (`job_finished`,
`job_failed`) and every reminder run. Telegram delivery failures are logged with `chat_id`.

## Backup

- Nightly at 03:30 Moscow the `backup` container runs `scripts/backup.sh`: `pg_dump -Fc` →
  `data/backups/db/romantika-YYYY-MM-DD.dump`, hard-linked media snapshot →
  `data/backups/media/YYYY-MM-DD/`, `manifest-YYYY-MM-DD.json` (row counts, media count and
  bytes, sha256 of the dump). Retention 30 days (by date in the file name).
- Every Sunday 04:30 `scripts/restore-verify.sh` restores the latest dump into the scratch
  database `romantika_verify`, compares row counts with the manifest and sha256 of up to 20
  media files, and writes `data/backups/last-verify.json`. The worker reads that file every
  6 hours and alerts the admin chat if it is missing, failed, or older than 8 days.
- Second copy on the Mac: `scripts/install-mac-pull.sh` installs a launchd job that runs
  `scripts/mac-pull-backups.sh` daily at 09:15 (rsync over ssh to `~/Backups/romantika/`).
  Run it by hand any time: `scripts/mac-pull-backups.sh`.
- Manual backup now: `rc exec backup scripts/backup.sh`. Manual verify: `rc exec backup scripts/restore-verify.sh`.

Nothing in the stack ever deletes media or participant rows; "removal" is a timestamp.

## Restore

1. Stop the writers: `rc stop bot worker web`.
2. Database: `rc exec -T db pg_restore --clean --if-exists --no-owner -U romantika -d romantika < data/backups/db/romantika-YYYY-MM-DD.dump`
   (for a full rebuild: `rc down`, `docker volume rm romantika_pgdata`, `rc up -d db migrate`, then restore).
3. Media: `rc run --rm --user root -v "$PWD/data/backups/media/YYYY-MM-DD:/snapshot:ro" bot rsync -a /snapshot/ /media/`.
4. `rc up -d` and check `/healthz`, then open the journal Mini App for one participant.
5. If the VPS is gone: the Mac copy (`~/Backups/romantika/`) has the same layout; recreate
   the stack from the repo, copy `.env`, put the dump and the media snapshot in place, follow
   steps 2–4.

## Production bot

Decision 2026-09-04 (owner): the legacy data is not migrated. Since 2026-09-05 production runs
on **Mila's own bot `@romantika_marshrutov_bot`** (token in `~/.romantika/prod.env` on the Mac
and in `/opt/stacks/romantika/.env` on the VPS — nowhere else). The interim
`@romantika_marshrutov_club_bot` on Dmitry's account is unused and may be deleted in BotFather.
The stack on the VPS runs with a clean database and the seeded Mexico season. Bot name,
descriptions, commands and the menu button are set through the Bot API:
`rc exec -T bot python -m romantika.ops.telegram_setup` (idempotent, run after every bot change).

Switching the token: edit `BOT_TOKEN` / `BOT_USERNAME` in the VPS `.env`, `scripts/deploy.sh`
(or `rc up -d bot web worker`), then `telegram_setup`. Sessions of the Mini App are signed with
the token, so open links get re-issued by Telegram on the next tap — nothing to migrate.

## Local stand (no Telegram)

`scripts/dev-stack.sh up` — Postgres in Docker (port 55442), a fake Bot API on :8081, web on
:8010, bot polling the fake, worker delivering through it; season seeded and activated.
`scripts/dev-stack.sh link 1001 Алиса /app/journal` prints a signed link; `/_control/*` on :8081
acts as a user (`text`, `media`, `callback`) and reads what the bot sent (`sent?chat_id=`).
Logs under `.dev/logs/`. `down` removes everything. On macOS WeasyPrint needs
`DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` (the script sets it; do not wrap the processes in
`nohup`/`env`, macOS strips `DYLD_*` for system binaries).

## Cut-over from the legacy bot (kept for reference, not used)

The legacy bot runs on Mila's Mac with `данные.sqlite` and the production token. Reusing that
data requires the **same** bot (same token), otherwise all Telegram `file_id`s stop working.

1. Deploy the stack with the **staging** token first (`@romantika_staging_bot`), check everything.
2. Ask Mila to stop the legacy bot (`pkill -f бот.py`) and send `данные.sqlite` + the token.
3. On the VPS: put the production token into `.env`, `rc up -d` (bot restarts with the new token).
4. Import: copy the SQLite next to the repo and run
   `rc exec -T bot python -m romantika.migration.legacy_import --sqlite /tmp/данные.sqlite --season-slug mexico-2026`
   (mount or `docker cp` the file into the container first). The import downloads every photo
   by `file_id` — it needs the production token to be live. Re-running is safe (idempotent).
   Check the reconciliation table it prints against the legacy counts.
5. In BotFather (Mila's account): `/newapp` twice for `@romantika_marshrutov_bot` with the URLs
   `https://romantika.vibe-coding.trade/app/journal` (short name `journal`) and `/calendar`
   (`calendar`); `/setmenubutton` → journal. `/setcommands` with the list from `GUIDE-RU.md`.
6. Send `/start` to the bot from Mila's account and from a participant's; check `/results`.
7. Run `scripts/backup.sh` once by hand (`BACKUP_ON_START=1` in `.env` for the first start).

## Release checklist

1. `make check` green locally; CI green on the branch.
2. `/release-check` in Claude Code (verifier + code/security/data reviewers over the diff).
3. `DRY=1 scripts/deploy.sh` — nothing unexpected in the file list.
4. Migrations reviewed: additive, reversible, no data loss.
5. `scripts/deploy.sh`; watch `rc logs -f bot worker` for two minutes; open the Mini App.
6. Note the release in `docs/CHANGELOG.md` (what changed for participants, for Mila).

## Rollback

- Code: `git checkout <previous tag or commit>` locally and `scripts/deploy.sh` again (the
  image is rebuilt from the checked-out tree).
- Database: migrations are reversible (`rc run --rm migrate alembic downgrade -1`), but prefer
  a forward fix; restore from the nightly dump only if data is corrupted.
- The bot is stateless apart from the DB: restarting it never loses reports (Telegram keeps
  unacknowledged updates for 24 h).

## Common problems

- **Bot silent, logs say `Не дозвонился` / connection errors** — the host proxy `:10809` is
  down or the container lacks `extra_hosts`. Check `curl -x http://127.0.0.1:10809 https://api.telegram.org` on the host.
- **`409 Conflict: terminated by other getUpdates request`** — two bots on one token (legacy
  still running on the Mac). Stop one.
- **Mini App says «Открой из бота»** — the page was opened outside Telegram or `initData`
  is older than 24 h; reopen from the bot button.
- **PDF without Cyrillic** — fonts missing in the image; rebuild (`fonts-dejavu-core`).
- **Backup alert in Telegram** — read `data/backups/last-verify.json`, run the verify by hand,
  look at `rc logs backup`.

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
- Season content: `rc exec -T bot python -m romantika.ops.seed --activate` — seeds a **fresh** season from `data/seasons/*.json` and is idempotent until then. Once weeks have been created, edited, moved, deleted or announced in the admin app the file is no longer the source of truth — texts included — and the seed **refuses** (SeedError names the actions): edit weeks in the app, or seed a new season.

## Access

- Host `77.91.92.61` (Hostkey, Moscow). The VPS is shared with other stacks: touch only
  `/opt/stacks/romantika` and the `romantika-*` containers. Work as the user `romantika`
  (member of `docker`, no sudo, key-only login); `docker` membership is root-equivalent on the
  host, so the key is as sensitive as a root key.
- `~/.ssh/config` on the machine that deploys (the private key stays outside any repository,
  `chmod 600`):

  ```
  Host romantika-vps
    HostName 77.91.92.61
    User romantika
    IdentityFile ~/.ssh/romantika_vps_ed25519
    ControlMaster auto
    ControlPath ~/.ssh/cm-romantika-%p
    ControlPersist 600
  ```

  `ssh romantika-vps 'docker ps --format "{{.Names}}"'` lists the `romantika-*` containers.
  fail2ban is on: reuse the connection (ControlMaster above) instead of reconnecting in a loop.
- Each person generates their own key pair (`docs/GUIDE-RU.md` «Доступ Claude к серверу») and
  sends the public half to the person who runs the VPS (Dima), who appends it to
  `/home/romantika/.ssh/authorized_keys`. A lost key is removed from that file; nothing else
  changes. Private keys never travel: not in chats, not in the repository.
- Secrets live only in `/opt/stacks/romantika/.env` on the VPS (owner `romantika`, chmod 600).
  Nobody needs them locally: the local stand runs on the fake Bot API, tests on testcontainers.
- Host level (cloudflared tunnel, xray proxy, the backup copy to the Mac) is run by Dima and is
  not touched from the project.

## Deploy

```bash
scripts/deploy.sh                 # from the repo root, as the romantika user (see Access)
DRY=1 scripts/deploy.sh           # see what rsync would send
```

What it does: rsync the repository (no `.git`, `.venv`, `data`, `.env`, `legacy`) →
`docker compose build migrate` on the VPS through the host proxy (apt/PyPI are blocked from
the RU datacenter otherwise) → `docker compose up -d` (migrations run first, the rest waits for
them) → `curl /healthz` (must say `"status":"ok"`: `media:false` means the media volume is not
writable for web) → tail of bot/worker logs. The image is built once and shared by all
services. First deploy: put `.env` in place before running it.

Compose on the VPS: `cd /opt/stacks/romantika && docker compose -f docker/compose.yml -f docker/compose.vps.yml --project-directory . <cmd>`.
Alias it as `rc` in your shell.

## Logs

```bash
rc logs --tail 100 -f bot          # or web / worker / backup / db
rc ps                              # health of every service
docker stats --no-stream           # CPU/RAM against the limits in compose.vps.yml
scripts/rc.sh logs --since 30m bot web worker   # the same from a laptop, over ssh
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
The stack on the VPS runs with a clean database and the seeded Mexico season. Bot name and
descriptions are set through the Bot API: `rc exec -T bot python -m romantika.ops.telegram_setup`
(idempotent, run after changing them). The command list and the menu button are applied by
the bot itself at every start (`apply_menu`; log line `menu_applied`), so a deploy is enough.

Switching the token: edit `BOT_TOKEN` / `BOT_USERNAME` in the VPS `.env`, `scripts/deploy.sh`
(or `rc up -d bot web worker`), then `telegram_setup`. Sessions of the Mini App are signed with
the token, so open links get re-issued by Telegram on the next tap — nothing to migrate.

## Local stand

`scripts/dev-stack.sh up` — Postgres 16 in Docker (`romantika-dev-pg`, port 55442), migrations,
the season, thirty invented participants (`romantika/ops/demo_data.py`), the fake Bot API on
`:8081`, web on `:8010`, bot and worker. Everything under `.dev/` (git-ignored). Modes:

- **work** (default): the bot polls the fake Bot API; agents and scripts play participants
  through `/_control/text`, `/_control/media`, `/_control/callback` and read `/_control/sent`.
- **live** (`up --live`): the test bot in real Telegram; token, username and admin ids come from
  `.dev/dev-bot.env` (fetched from the VPS, `docs/SETUP-RU.md`); the script checks `getMe`
  first and says «включи VPN» when Telegram is unreachable. The Mini App is opened through a
  signed link, not from the bot's menu button (that button points at production).

`scripts/dev-stack.sh reset` drops the stand database and `.dev/media` and seeds again;
`status`, `logs`, `link <id> <name> [/path]`, `down`. Production data never reaches the stand.

Screenshots: `scripts/shots.sh [--as 1001] [--page /app/journal|all] [--out DIR] [--dark]` (headless
Google Chrome, 500px wide). Conversation mock-ups from the bot's real answers:
`uv run python -m romantika.ops.chat_mockup --scenario start --out DIR`.

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
   (`calendar`). Commands and the menu button are set by the bot at start, not in BotFather
   (`/setcommands` there would be overwritten at the next bot restart).
6. Send `/start` to the bot from Mila's account and from a participant's; check `/results`.
7. Run `scripts/backup.sh` once by hand (`BACKUP_ON_START=1` in `.env` for the first start).

## Release checklist

1. `make check` green locally; CI green on the branch.
2. `/release-check` in Claude Code (verifier + code/security/data reviewers over the diff).
3. `DRY=1 scripts/deploy.sh` — nothing unexpected in the file list.
4. Migrations reviewed: additive, reversible, no data loss.
5. `scripts/deploy.sh`; `healthz` says `"status":"ok"`; watch `rc logs -f bot worker web` for two
   minutes; open the bot and the Mini App once.
6. Note the release in `docs/CHANGELOG.md` (what changed for participants, for Mila); merge
   into `master` and push.

## Rollback

- Code: `git checkout <previous tag or commit>` locally and `scripts/deploy.sh` again (the
  image is rebuilt from the checked-out tree).
- Database: migrations are reversible (`rc run --rm migrate alembic downgrade -1`), but prefer
  a forward fix; restore from the nightly dump only if data is corrupted.
  `c4e8f1a2b9d3` (weeks.announced_at) **refuses to downgrade while draft weeks exist**: the
  previous release would show them to participants. Announce or delete the drafts in the
  admin app («Задания»), then downgrade.
  `d5e6f7a8b9c0` (reports.late, v2.4.0) **refuses to downgrade while late reports exist**,
  and rolling back the *code* to v2.3.0 is unsafe for the same reason: v2.3.0's stamp
  recomputation does not know `late`, so the next cancel or edit would award stamps for
  late reports (on the stand: 8 of 61 person-weeks). While `SELECT count(*) FROM reports
  WHERE late` (read-only, see below) is not zero, **fix forward** — do not roll back
  v2.4.0. With zero late reports the code may be rolled back without touching the
  migration: the column keeps `server_default false` and v2.3.0 runs on the new schema.
  **v2.5.0 has no migration, and that is the trap:** it made participants' own words and
  facts personal (seen by the author and Mila only) and the form promises it. Rolling the
  code back to v2.4.0 shows every word and fact of every participant to everyone again —
  the old ones included — and a look cannot be undone. Ask Mila before rolling back below
  v2.5.0; prefer a forward fix. The data itself is untouched either way.
- The bot is stateless apart from the DB: restarting it never loses reports (Telegram keeps
  unacknowledged updates for 24 h). The one thing it writes on Telegram's side is the command
  list and the menu button (`apply_menu` at start, v2.3.0+). Rolling back to a release before
  v2.3.0 does not undo that: run `rc exec -T bot python -m romantika.ops.telegram_setup` from
  the rolled-back tree afterwards. Reply keyboards are cached on people's phones until their
  next `/start`; a client too old for `web_app` buttons sends «🎒 Открыть клуб» as text, which
  a pre-v2.3.0 bot would take for a report inside a week.

## Read-only queries

The only SQL run against the production database outside a restore (CLAUDE.md rule 10). All of
them are read-only and are what `scripts/prod-snapshot.sh` executes for `/prod` and for the
release-window question in `/relize`:

```sql
select count(*) from users where blocked_at is null and is_admin = false;                       -- participants in the bot
select count(distinct user_id) from reports where created_at > now() - interval '1 hour';       -- active in the last hour
select count(distinct user_id) from reports where created_at > now() - interval '24 hours';     -- active today
select count(distinct user_id) from reports where created_at > now() - interval '7 days';       -- active this week
select count(*) from reports where created_at > now() - interval '7 days' and deleted_at is null; -- reports this week
select count(*) from letters where replied_at is null;                                           -- unanswered letters
select count(*) from jobs where status = 'failed' and finished_at > now() - interval '24 hours'; -- failed worker jobs
select count(*) from reports where late and deleted_at is null;                                  -- late reports (blocks a rollback below v2.4.0)
```

Run by hand: `scripts/rc.sh exec -T db psql -U romantika -d romantika -Atc "<one of the above>"`.
Anything else against production is a RUNBOOK change first.

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

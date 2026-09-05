# Romantika Marshrutov v2 — Architecture Contract

Status: binding contract for the rewrite (2026-09-03). Implementers follow this document;
deviations are proposed in the stage report, not silently applied.

## 1. Goal

One Telegram bot (`@romantika_marshrutov_bot`, same token as before) plus several Telegram
Mini Apps served by our own backend, with all participant data and photos stored on our VPS,
verified backups, an admin UI for season content, and a PDF season journal.

Product semantics (what a season, week, report, stamp, freeze, level, achievement mean) are
defined in `docs/DOMAIN.md` and are the same as in `legacy/` unless DOMAIN.md says otherwise.
Legacy code is reference only; never import it.

## 2. Non‑negotiables

- Python 3.12, `uv`, `ruff`, `mypy --strict`-ish (see pyproject), `pytest`. No other build
  systems, no Node build step (npm is unreachable from the RU datacenter; front-end is plain
  HTML/CSS/JS served by FastAPI).
- All identifiers, comments, commit messages and developer docs in English. User-facing
  texts (bot messages, Mini App UI, PDF) are Russian and live in `romantika/texts/*.py` or
  templates — never inline inside handlers.
- Postgres 16 is the only database. SQLAlchemy 2.0 (async, `asyncpg`), Alembic migrations.
  No raw positional `INSERT ... VALUES` without column lists anywhere.
- Media files are immutable. Nothing in the codebase deletes a media file or a report row;
  "removal" is `hidden_at`/`deleted_at` timestamps. Backups verify that.
- Time: store UTC `timestamptz` in DB; business calendar is `Europe/Moscow` (`zoneinfo`,
  `tzdata` is installed in the image). Never `datetime.now()` without tz.
- Every external call (Telegram API, filesystem, DB) is behind a service function; handlers
  and routes contain no business logic.
- Telegram message length: any outgoing text goes through `romantika.bot.send.split_text`
  (4096-char safe splitting). Errors from Telegram are logged with context, never swallowed.
- Secrets only from environment (`.env` in deployment, never committed). `ADMIN_IDS` is a
  comma-separated list.

## 3. Repository layout

```
romantika/                 package (installed, `romantika` on sys.path)
  config.py                Settings (pydantic-settings): BOT_TOKEN, ADMIN_IDS, DATABASE_URL,
                           MEDIA_DIR, PUBLIC_BASE_URL, ADMIN_CHAT_ID (optional), LOG_LEVEL
  logging.py               structured logging setup (stdlib logging, JSON in prod)
  db/
    base.py                Declarative Base, naming conventions
    models.py              all ORM models (section 4)
    session.py             engine + async_sessionmaker factory `make_session_factory(url)`
    migrations/            alembic (env.py async, versions/)
  domain/                  PURE functions, no IO, fully unit-tested
    calendar.py            moscow_now(), moscow_today(), week_for(date, weeks), julian_day()
    rules.py               report_level(), levels, season_breakdown(), streaks, core
    tzolkin.py             tzolkin_day(date) -> TzolkinDay (data from data/tzolkin.json)
    types.py               dataclasses/enums used by domain (WeekState, Breakdown, ...)
  services/                use-cases; take an AsyncSession (+ optional gateways), return DTOs
    content.py             seasons/weeks/achievement types read + admin edits (audit log)
    people.py              upsert user, membership in season, dialog state
    reports.py             accept report (+media metadata), cancel, fix level
    media.py               MediaStore: download from Telegram to MEDIA_DIR, sha256, dedupe
    stamps.py              award/upgrade stamp, admin override
    freezes.py             grant (auto/manual), totals
    achievements.py        award/list
    words.py, facts.py, wishes.py
    passport.py            passport view model (breakdown + texts)
    journal.py             journal view model (for bot text, Mini App and PDF)
    summary.py             weekly summary, core, draft post, reminder recipients
    jobs.py                enqueue/claim/finish jobs
    seed.py                import season JSON (data/seasons/*.json) into DB
  texts/                   Russian strings/templates for the bot (greeting, help, buttons ...)
  bot/                     aiogram 3: `create_dispatcher(settings, session_factory, media_store)`
    routers/               user.py, reports.py, admin.py, callbacks.py
    keyboards.py, send.py (split_text, safe_send), middlewares.py (db session, user upsert)
    main.py                polling entrypoint `python -m romantika.bot`
  web/                     FastAPI: `create_app(settings, session_factory, media_store)`
    auth.py                Telegram initData validation (HMAC-SHA256), `CurrentUser` dependency
    routes/                public.py (/, /calendar), api.py (/api/...), admin_api.py, media.py
    templates/             Jinja2: public season page, calendar, miniapp shells
    static/                css/js for Mini Apps (vanilla, no build)
    main.py                `python -m romantika.web` (uvicorn)
  worker/                  `python -m romantika.worker`: job loop + schedulers (reminders,
                           backups verification alerts)
  pdf/                     journal HTML template + WeasyPrint renderer `render_journal_pdf()`
  migration/               legacy_import.py: legacy SQLite + file_id download -> Postgres/media
data/
  tzolkin.json             single source of truth: {"correlation": 584283,
                           "signs": [20 × {name (simple spelling «Ик»), name_academic («Ик'»),
                           latin, emoji, symbol, meaning, destiny, short, day_advice}],
                           "tones": [13 × {number, name, text}]} — merged from legacy bot
                           (ЗНАКИ_ЦОЛЬКИНА: symbol/day_advice) and Mini App (SIGNS/TONES)
  seasons/mexico-2026.json season content seed (legacy сезон.json, same keys)
docker/                    Dockerfile (one image, 3 commands), compose.yml, compose.vps.yml
scripts/                   backup.sh, restore-verify.sh, mac-pull-backups.sh, deploy.sh, ...
tests/
  acceptance/              READ-ONLY for implementers (written by the orchestrator per stage)
  unit/  integration/      written by implementers
  conftest.py              Postgres fixture: TEST_DATABASE_URL env or testcontainers-postgres
docs/                      ARCHITECTURE.md (this), DOMAIN.md, RUNBOOK.md, GUIDE-RU.md
legacy/                    old code, reference only
```

## 4. Data model (Postgres, SQLAlchemy models in `romantika/db/models.py`)

Naming: snake_case tables, `id` PKs, `created_at timestamptz default now()` everywhere,
`bigint` for Telegram ids. Enums are Python `enum.StrEnum` stored as `text` with a CHECK.

| Table | Columns (besides id/created_at) | Notes |
|---|---|---|
| `users` | `id bigint PK` (telegram id), `username`, `first_name`, `last_name`, `is_admin bool`, `joined_at timestamptz`, `blocked_at timestamptz null` | joined_at = first contact, never updated |
| `seasons` | `slug unique`, `title`, `title_accusative` (e.g. «Мексику»), `hashtag`, `starts_on date`, `ends_on date`, `status` (`draft/active/archived`), `daily_kind null` (`tzolkin`), `daily_title`, `daily_note`, `base_freezes int=2`, `max_freezes int=5`, `level_tourist int=1`, `level_traveler int=4`, `level_resident int=9`, `journal_promise_on date null` | exactly one `active` at a time (partial unique index) |
| `weeks` | `season_id FK`, `number int`, `title`, `starts_on`, `ends_on`, `intro`, `task_min`, `task_max`, `word`, `word_ru`, `word_meaning` | unique(season_id, number); weeks may not overlap inside a season |
| `achievement_types` | `season_id FK`, `code`, `emoji`, `name`, `description`, `sort int` | unique(season_id, code) |
| `season_members` | `season_id`, `user_id`, `joined_at` | PK(season_id,user_id); created on first contact during season |
| `intents` | `season_id`, `user_id`, `week_id`, `choice` (`take/try/skip`), `updated_at` | unique(user_id, week_id) |
| `reports` | `season_id`, `user_id`, `week_id`, `kind` (`text/photo/video/video_note/document/voice/audio/other`), `text`, `level` (`min/max`), `tg_chat_id`, `tg_message_id`, `deleted_at null` | full history, never physically deleted |
| `media` | `id uuid`, `report_id FK`, `tg_file_id`, `tg_file_unique_id`, `mime`, `size int`, `width null`, `height null`, `sha256`, `path` (relative to MEDIA_DIR), `downloaded_at null`, `hidden_at null` | `path` = `<season_slug>/<user_id>/<uuid>.<ext>` |
| `stamps` | `season_id`, `user_id`, `week_id`, `level` (`min/max`), `week_title_snapshot`, `awarded_at`, `source` (`report/admin`) | unique(user_id, week_id) |
| `freezes` | `season_id`, `user_id`, `reason` (`word/max/comment/meetup/friend/manual`), `granted_by null`, `note null` | bonus freezes only; base freezes are a season constant |
| `achievements` | `season_id`, `user_id`, `code`, `label`, `awarded_by`, `awarded_at` | unique(season_id, user_id, code) |
| `words` | `season_id`, `user_id`, `week_id null`, `word`, `meaning` | meaning parsed from "word — meaning" (first ` — `, ` - `, `:`) |
| `facts` | `season_id`, `week_id null`, `text`, `author_id null`, `deleted_at null` | author null = admin |
| `wishes` | `season_id`, `user_id`, `text`, `updated_at` | unique(season_id,user_id) |
| `admin_links` | `admin_chat_id`, `admin_message_id`, `user_id`, `report_id null`, `week_id null` | reply-routing; PK(admin_chat_id, admin_message_id) |
| `dialog_states` | `user_id PK`, `state`, `payload jsonb`, `updated_at` | TTL 6h enforced in `people.get_dialog_state` |
| `settings` | `key PK`, `value text` | e.g. `reminders_enabled` |
| `reminder_log` | `key PK` (`YYYY-MM-DD:<slug>`), `sent_at`, `recipients int` | dedupe |
| `audit_log` | `actor_id`, `action`, `entity`, `entity_id`, `before jsonb`, `after jsonb` | every admin content edit |
| `jobs` | `kind`, `payload jsonb`, `status` (`queued/running/done/failed`), `attempts`, `run_after`, `started_at`, `finished_at`, `error` | claimed with `FOR UPDATE SKIP LOCKED` |

## 5. Domain rules (pure; `romantika/domain/rules.py`)

```python
def report_level(kind: ReportKind) -> StampLevel        # photo/video/video_note/document -> MAX, else MIN
def merge_level(existing: StampLevel | None, new: StampLevel) -> StampLevel   # MAX never downgrades
def season_breakdown(*, weeks: Sequence[WeekInfo], stamps: Mapping[int, StampLevel],
                     bonus_freezes: int, base_freezes: int, max_freezes: int,
                     joined_on: date, today: date) -> Breakdown
def level_for(stamps_count: int, freezes_left: int, cfg: LevelConfig) -> Level | None
def core_members(breakdowns: Mapping[int, Breakdown], min_streak: int = 2) -> list[int]
```

Types live in `romantika/domain/types.py`: `ReportKind`, `StampLevel` (`min`/`max`),
`WeekState`, `Level` (`tourist`/`traveler`/`resident`), `LevelConfig(tourist, traveler,
resident)`, `WeekInfo(number, title, starts_on, ends_on)`, `Breakdown(states: dict[int,
WeekState] keyed by week number, stamps, freezes_used, freezes_left, freezes_total,
best_streak, current_streak)`. All are `StrEnum`/frozen dataclasses.

`Breakdown` has per-week `WeekState` in {`locked`, `stamped`, `current`, `before_join`,
`frozen`, `missed`}. A `frozen`/`current`/`before_join` week keeps the streak unchanged,
`stamped` adds one, `missed` resets `current_streak` to 0. `core_members` returns user ids
with `stamps ≥ 1` and `best_streak ≥ min_streak`, sorted by `best_streak` desc then id asc.
Rules are exactly DOMAIN.md §3–§5 (ported from legacy `разбор_сезона`, `всего_заморозок`,
`УРОВНИ`, `ядро`). Russian names of levels live in `romantika/texts`.

`romantika/domain/tzolkin.py`: `tzolkin_day(d: date) -> TzolkinDay(number 1..13, sign: Sign,
kin 1..260)` with GMT correlation 584283 and the exact legacy formulas
(`number = ((x+3) % 13) + 1`, `sign_index = (x+19) % 20`, `x = jdn - 584283`).

## 6. Services (async; signature convention)

Every service function takes `session: AsyncSession` first, plain values next, and returns
DTOs (dataclasses in the same module) — never ORM instances across the boundary. Services
do not commit; the caller (middleware/route/job) commits. Side effects to Telegram happen in
gateways passed in (`TelegramGateway` protocol in `romantika/services/gateways.py`) so services
are testable without network.

Key flows:

- `reports.accept(session, *, user, message: IncomingMessage, now) -> AcceptResult` —
  finds active week (else stores an `other`-kind report with `week_id=None`, and result says
  `out_of_week=True`), creates report, media rows (not yet downloaded), awards/merges stamp
  via `stamps`, grants auto-freeze `max` on first MAX; returns texts to send + admin copy.
- `media.MediaStore.download(session, media_id, telegram)` — getFile + stream to
  `MEDIA_DIR/<path>.part` then atomic rename; sets sha256/size/downloaded_at; idempotent.
  Called inline by the bot right after `accept`; failure enqueues job `media_download`.
- `reports.fix_level(session, user, week_number, level)` — via `stamps.merge`, refuses to
  downgrade MAX (returns explanation), refuses when no report exists for the week.
- `stamps.admin_set(session, actor, user_id, week_number, level | None)` — override with audit.
- `passport.build(session, user, season, today)`, `journal.build(...)`, `summary.week(...)`,
  `summary.draft_post(...)`, `summary.reminder_recipients(...)`.
- `content.*` — admin CRUD for seasons/weeks/achievement types/facts with audit log;
  `content.active_season(session, today)`; `content.week_for(session, season, today)`.
- `seed.import_season(session, path)` — idempotent upsert by slug/number.

### 6.1 Service API (binding; mirrors `tests/acceptance/test_stage2_services.py`)

Time is always explicit: `now: datetime` (aware, UTC) or `today: date` (Moscow calendar day).
`romantika/services/gateways.py` defines the `TelegramGateway` protocol
(`get_file(file_id) -> TelegramFile(file_path, file_size)`, `download_file(file_path,
destination: Path)`, later stages add `send_message(chat_id, text)` and
`send_document(chat_id, path, caption)`); the bot provides an adapter over aiogram, tests use fakes.

| Module | Functions (all `async`, first arg `session`) |
|---|---|
| `people` | `upsert_user(session, tg: TelegramUser, *, now) -> UserDTO` (keeps first `joined_at`); `ensure_member(session, season_id, user_id, *, now) -> datetime` (returns existing `joined_at`); `set_dialog_state(session, user_id, state, payload=None, *, now)`, `get_dialog_state(session, user_id, *, now) -> DialogStateDTO | None` (TTL 6 h), `clear_dialog_state(session, user_id)`; `set_intent(session, *, season_id, user_id, week_id, choice: IntentChoice, now)` |
| `content` | `active_season(session, *, today) -> SeasonDTO | None`; `activate_season(session, season_id, *, actor_id)`; `weeks(session, season_id) -> list[WeekDTO]`; `current_week(session, season_id, *, today) -> WeekDTO | None`; `update_week(session, *, actor_id, week_id, changes: dict[str, str]) -> WeekDTO` (only title/intro/task_min/task_max/word/word_ru/word_meaning; audit row); `get_setting/set_setting(session, key, value)` |
| `reports` | `IncomingFile`, `IncomingMessage` dataclasses; `accept(session, *, season_id, user_id, message, now) -> AcceptResult(report_id, week_number, out_of_week, level, stamp_level, freeze_granted, media_ids)`; `fix_level(session, *, season_id, user_id, week_number, level, now) -> FixResult(ok, stamp_level, reason)`; `cancel(session, *, user_id, report_id, now) -> CancelResult(ok, stamp_level)` |
| `stamps` | `admin_set(session, *, actor_id, season_id, user_id, week_number, level: StampLevel | None, now) -> StampLevel | None` (audit row) |
| `freezes` | `grant(session, *, season_id, user_id, reason: FreezeReason, granted_by, now, note=None) -> bool`; `bonus_count(session, season_id, user_id) -> int` |
| `media` | `MediaStore(root: Path)`: `.root`, `download(session, media_id, telegram, *, now) -> MediaDTO(path, sha256, size)`; path `<season_slug>/<user_id>/<uuid>.<ext>`, `.part` + atomic rename, idempotent |
| `achievements` | `award(session, *, season_id, user_id, code_or_text, awarded_by, now) -> AwardResult(created, code, label)`; `labels(session, *, season_id, user_id) -> list[str]` |
| `words` | `add(session, *, season_id, user_id, week_id, raw, now) -> WordResult(word, meaning, freeze_granted)`; `season_dictionary(session, season_id, *, today) -> DictionaryView(week_words, user_words)` |
| `facts` | `add(session, *, season_id, week_id, text, author_id, now) -> int`; `list_active(session, season_id) -> list[FactDTO]`; `remove(session, *, fact_id, actor_id, now) -> bool` |
| `wishes` | `set_wish(session, *, season_id, user_id, text, now)`; `get_wish(session, season_id, user_id) -> str | None` |
| `passport` | `build(session, *, season_id, user_id, today) -> PassportView(breakdown, stamps_max, level, achievements, ...)` |
| `journal` | `build(session, *, season_id, user_id, today) -> JournalView(user, season, weeks: [JournalWeek(number, title, level, quote)], media: [JournalMedia(media_id, path)], achievements, words, facts, wish)` |
| `summary` | `week(session, *, season_id, week_number, today) -> WeekSummary(members_total, reports_total, took, submitted: dict[int, StampLevel], took_not_submitted, core_best, core_current)`; `reminder_recipients(session, *, season_id, week_number) -> list[int]`; `draft_post(...)` |
| `jobs` | `enqueue(session, kind, payload, *, now, run_after=None) -> int`; `claim(session, *, now) -> JobDTO | None` (`FOR UPDATE SKIP LOCKED`, respects `run_after`); `finish(session, job_id, *, error, now)` (error → requeue with exponential backoff, `failed` after 5 attempts) |

## 7. Bot (aiogram 3)

- Long polling (`allowed_updates=["message","callback_query"]`), `drop_pending_updates=False`.
  `deleteWebhook` at start.
- Middlewares: DB session per update (commit on success), user upsert + season membership.
- Reply keyboard and inline flows replicate legacy (DOMAIN.md §7): Задание / Сегодня /
  Паспорт / Словарь / Что узнали / Ещё / Помощь / Написать Миле, admin panel «⚙️».
  Button detection by normalized word (emoji-insensitive) as in legacy.
- Inline `web_app` buttons open the Mini Apps: journal (`{PUBLIC_BASE_URL}/app/journal`),
  calendar (`/calendar`), admin (`/app/admin`).
- Report intake accepts text, photo (largest size), video, video_note, document, voice,
  audio. Voice/audio = MIN level report with kind `voice/audio`. Stickers/other: reply
  «не поняла» text, nothing stored.
- Media download happens inline after accept; on failure user still gets the stamp, and a
  `media_download` job is queued.
- Out-of-week messages are stored (report with `week_id=None`, kind as received) AND copied
  to admin; reply says it was passed to Mila (legacy lied — fixed).
- Admin: reply-routing via `admin_links`, `/results`, `/core`, `/remind`, `/badges`,
  `/badge`, `/reminders`, `/who`, `/wish`, `/fact`, `/fact-` (Russian aliases kept:
  `/ачивка`, `/пожелание`, `/факт`, `/факт-`, `/факты`, `/журнал`).
- Reminders are NOT in the bot process; see worker.

### 7.1 Bot API (binding; mirrors `tests/acceptance/test_stage3_bot.py`)

- `romantika.bot.app.create_dispatcher(settings, session_factory, media_store, *, telegram=None,
  clock=None) -> aiogram.Dispatcher`. `telegram` is a `TelegramGateway` for media downloads
  (default `romantika.bot.gateway.AiogramTelegramGateway(bot)` built lazily from the Bot of the
  update); `clock: Callable[[], datetime]` returns an aware datetime (default `moscow_now()`).
  The dispatcher must be usable through `dp.feed_update(bot, update)` with any `aiogram.Bot`.
- Middlewares (outer, on `update`): DB session from `session_factory` (commit on success,
  rollback on error), user upsert + season membership, settings/clock/media injection via
  handler kwargs.
- `romantika.bot.send.split_text(text: str, limit: int = 4096) -> list[str]` splits on
  paragraph, then line, then space boundaries; never returns an empty piece; every outgoing
  text passes through it (`safe_send`).
- `romantika.bot.keyboards.normalize_button(text) -> str` (drops emoji/variation selectors,
  collapses spaces, lower-cases) and `button_action(text) -> str | None` with actions
  `task, today, passport, words, facts, more, help, write, admin`.
- Callback data: `intent:<week_number>:<take|try|skip>`, `level:<week_number>:<min|max>`,
  `notreport:<report_id>`, `more:<journal|write|help>`, `addword`, `addfact`, `endofseason`,
  admin `adm:<action>...` (free format, documented in keyboards.py).
- Commands: participant `/start /help /whoami /task /today /passport /words /facts /journal`
  (+ Russian aliases `/помощь /журнал /факты`); admin `/results [N] /core /remind /badges
  /badge /reminders /who /wish /fact <text> /fact- <id>` (+ `/ачивка /ачивки /пожелание
  /факт /факт-`). Admin = `user.is_admin or id in settings.admin_ids`; non-admins get a polite
  refusal, never the admin output.
- Report intake: text → MIN; photo (largest size)/video/video_note/document → MAX; voice/audio
  → MIN with kind voice/audio; sticker/location/contact/other → reply «не поняла…», nothing
  stored. After `reports.accept`, media are downloaded inline through the gateway; on failure
  a `media_download` job is enqueued and the participant still gets the stamp reply.
- Admin copy: header `sendMessage` to `settings.admin_chat_id` («📨 Отчёт за неделю N от
  Имя: …») and `copyMessage` of the original; both message ids are stored in `admin_links`.
  A reply from the admin chat to one of those messages is delivered to the author as
  «💬 Мила ответила на твой отчёт: …».
- Out-of-week messages: stored (`week_id=None`), copied to the admin, honest reply.
- Texts: `romantika/texts/ru.py` — greeting, help (11 legacy FAQ items), admin memo, button
  labels, report replies; ported from legacy wording.

## 8. Web (FastAPI)

- `GET /healthz` → `{"status":"ok","db":true}`.
- Public: `GET /` season page (SSR from DB; future weeks not in HTML), `GET /calendar`
  (tzolkin Mini App; signs embedded from data/tzolkin.json).
- Mini Apps: `GET /app` and `GET /app/{tab}` (participant: today · passport · journal · words ·
  more), `GET /app/admin` (Mila: week · people · letters · content · more; facts from «Ещё»).
  HTML shells; JS calls `/api`.
- Auth: header `X-Telegram-Init-Data` validated per Telegram docs (HMAC-SHA256 with
  `WebAppData` key, `auth_date` ≤ 24h); `POST /api/session` turns it into the `rm_session`
  cookie so `<img src="/media/…">` loads. Dev bypass only when `settings.dev_auth_user_id` is
  set and `settings.env == "dev"` (header `X-Dev-Auth: 1`); the local stand instead signs real
  initData with `python -m romantika.ops.dev_link` (`?init=` query parameter).
- The bridge `telegram-web-app.js` is vendored under `static/vendor/` (telegram.org is slow or
  blocked on some networks; without initData the app can only say «open it from the bot»).
- Participant writes: `POST /api/reports` (multipart `text`, `files[]`, `client_id` — a retry
  with the same `client_id` answers 200 with the report already made, never a second report),
  `PATCH /api/reports/{id}` (multipart `text`, `remove[]` media ids, `files[]`; allowed while
  the report's week is open — DOMAIN §2; recomputes the stamp, hides removed files, copies the
  new version to Mila, receipts the author; 403 foreign, 409 cancelled or week over, 413 over
  10 files, 422 empty; an `edit_key` makes a retried PATCH idempotent), `POST
  /api/reports/{id}/cancel`, `POST /api/weeks/{n}/level`, `POST /api/intent` (409 for a week
  that has not started), `POST /api/letters`, `POST /api/words` (422 for a word the person
  already has), `POST /api/facts`.
- Multipart limits (`routes/api.py`): the request is refused with 413 from `Content-Length`
  before parsing when it exceeds 200 MB; `request.form(max_files=11, max_fields=64)`; one file
  ≤ 50 MB, 10 files per report, text ≤ 4000 characters (422); only parts named `files` are
  attachments; a zero-size file is refused (422) rather than dropped; a NUL byte in any text
  is 422; `client_id` / `edit_key` longer than 64 characters are 422, never cut; files stored
  before a failure are removed again. One person's attempts are serialised with
  `pg_advisory_xact_lock` on `user:client_id` (POST) and `user:edit:report:edit_key` (PATCH),
  so a retry in flight finds the first attempt's row instead of doing the work twice. A
  service that refuses the input raises `services.errors.Refused` (a `ValueError` with a
  Russian message) and the app answers 422 `{"detail": …}` (`web/app.py`); any other error
  stays a 500.
- `GET /media/{id}` sends only images, video and audio inline (`INLINE_TYPES`); anything else
  goes out as `application/octet-stream` with `Content-Disposition: attachment`, always with
  `X-Content-Type-Options: nosniff`. Hidden media are 404 for the owner but still open for Mila.
- Letters: everything sent to Mila that is not a report — «Написать Миле» (bot or app),
  a message between weeks, a report taken back — is a `letters` row (`source` bot | app |
  out_of_week | not_report) with `reply_text/replied_at`; the admin copy's `admin_links` row
  carries `letter_id`, so a chat reply marks the same letter answered as a reply from the app.
- API (JSON, all under `/api`, Pydantic schemas in `romantika/web/schemas.py`):
  `GET /api/me`, `GET /api/journal` (passport + weeks + reports + media urls + achievements +
  words + wishes), `POST /api/journal/pdf` (enqueue) / `GET /api/journal/pdf/{job_id}`,
  `GET /media/{media_id}` (auth: owner or admin; `Cache-Control: private`),
  admin: `GET/PUT /api/admin/seasons/{id}`, `GET/PUT /api/admin/weeks/{id}`,
  `GET/POST/PATCH /api/admin/achievement-types`, `GET/POST/DELETE /api/admin/facts`,
  `GET /api/admin/participants`, `GET /api/admin/participants/{id}`,
  `PUT /api/admin/participants/{id}/stamps/{week}`, `POST .../freezes`, `POST .../achievements`,
  `PUT .../wish`, `POST .../message`, `GET /api/admin/summary?week=` (+ `week_ended`),
  `POST /api/admin/remind` body `{week_number?}` (404 unknown week, 409 past week),
  `GET /api/admin/letters` → `{unanswered, letters[]}` (each letter with the `media` of the
  report it came from), `POST /api/admin/letters/{id}/reply`, `GET/PUT /api/admin/reminders`,
  `GET /api/admin/audit` (rows carry `actor_name`). `GET /api/admin/participants` also
  carries `week_intent` / `week_level` for the current week (the people filters). Stamps,
  freezes and achievements given from the admin app notify the participant through
  `telegram_notify`; a stamp or a reminder for a week that has not started is 409.
- Admin = `user.is_admin or user.id in settings.admin_ids`.
- Front-end: vanilla JS modules in `romantika/web/static/`, Telegram `telegram-web-app.js`
  from `https://telegram.org/js/telegram-web-app.js`; `tg.expand()`; works in a plain browser
  in dev with the dev bypass.

### 8.1 Web API details (binding; mirrors `tests/acceptance/test_stage4_web.py`)

- `romantika.web.app.create_app(settings, session_factory, media_store, *, clock=None) -> FastAPI`.
- `romantika.web.auth`: `build_init_data(bot_token, user: dict, *, auth_date: int) -> str`
  (query-string form Telegram sends, with `hash`), `validate_init_data(init_data, bot_token,
  *, now) -> InitDataUser | None` (HMAC per Telegram docs; `auth_date` older than 24 h →
  None), dependency `current_user` (401 without/invalid header, upserts the user),
  `admin_user` (403 for non-admins).
- JSON shapes: `/api/me` → `{id, first_name, username, is_admin}`; `/api/journal` →
  `{season: {slug,title,starts_on,ends_on}, passport: {stamps, stamps_max, freezes_used,
  freezes_left, freezes_total, best_streak, current_streak, level}, weeks: [{number, title,
  state, level, starts_on, ends_on, task_min, task_max, word}], reports: [{id, week_number,
  kind, text, created_at, media: [{id, url, mime}]}], achievements: [label], words: [{word,
  meaning}], facts: [text], wish}`; future weeks are present only as `state: "locked"`
  without task texts. `/api/journal/pdf` POST → 202 `{job_id, status}`; GET
  `/api/journal/pdf/{job_id}` → `{status, url?}` (403 for other users' jobs).
  `/media/{media_id}` → file bytes with the stored mime, `Cache-Control: private, max-age=3600`;
  401 anonymous, 403 not owner/admin, 404 unknown or hidden.
- Admin API: `GET /api/admin/weeks` (all weeks of the active season), `PUT /api/admin/weeks/{id}`
  body = subset of editable fields (422 for anything else), `GET /api/admin/participants`
  (`[{id, first_name, username, joined_at, stamps, level}]`), `GET
  /api/admin/participants/{id}` (`{user, passport, achievements, wish, reports}`), `PUT
  /api/admin/participants/{id}/stamps/{week_number}` body `{level: "min"|"max"|null}` → 200
  `{level}`, `POST .../freezes` body `{reason, note?}` → 201, `POST .../achievements` body
  `{code_or_text}` → 201 `{code, label, created}`, `PUT .../wish` body `{text}` → 200,
  `GET /api/admin/summary?week=N` → WeekSummary JSON with `submitted: [{user_id, level}]`,
  `GET/POST /api/admin/facts`, `DELETE /api/admin/facts/{id}` → 204, `GET /api/admin/audit`.
- Pages: `/` public season page (SSR, no participant data, future weeks absent from HTML),
  `/calendar` (tzolkin Mini App, signs embedded from data/tzolkin.json), `/app/journal`,
  `/app/admin` (HTML shells + vanilla JS from `/static/...` that sends `X-Telegram-Init-Data`
  from `window.Telegram.WebApp.initData`).

## 9. Worker

`python -m romantika.worker` runs forever: (a) job loop — claims one job at a time
(`FOR UPDATE SKIP LOCKED`), kinds: `telegram_notify` (everything the web wants said in a chat:
text + media ids, with the `admin_links` row for Mila's replies — the web never talks to
Telegram), `reminders_now` (payload `week_number` optional), `media_download`, `journal_pdf`
(render + `sendDocument` to the user + store path under `MEDIA_DIR/journals/`),
`season_journals` (one `journal_pdf` per participant with a stamp, `requested_via: season_end`),
`broadcast`; retries with backoff, max 5 attempts; (b) schedulers ticking every 60 s in Moscow
time: reminders (Thu ≥19:00, Sun ≥12:00, deduped by `reminder_log`, catch-up within the same
day), `season_end_tick` (the day after the season's last day, ≥ 12:00, once per season —
`reminder_log` key `<slug>:journals` — queues `season_journals`), nightly
`backup_status_check` (reads `/backups/last-verify.json`, alerts admin if stale > 8 days or failed).

### 9.1 Worker API (binding; mirrors `tests/acceptance/test_stage5_worker.py`)

- `TelegramGateway` gains `send_message(chat_id, text)` and `send_document(chat_id, path:
  Path, caption: str | None = None)`; the aiogram adapter implements them.
- `romantika.worker.runner.run_once(session_factory, *, telegram, media_store, now) -> str | None`
  claims one job, runs its handler in its own session/transaction, returns the kind.
  Handlers: `media_download` (MediaStore.download), `journal_pdf` (render → save under
  `<media_root>/journals/<season_slug>/<user_id>-<YYYYMMDD-HHMMSS>.pdf` → `send_document`),
  `broadcast` (`{user_ids, text}`).
- `romantika.worker.schedulers.reminders_tick(session, *, telegram, now, admin_chat=None) -> int`: Thursday
  ≥ 19:00 and Sunday ≥ 12:00 Moscow, once per day (`reminder_log` key `YYYY-MM-DD:thu` /
  `:sun`), skipped when setting `reminders_enabled == "off"` or no current week; sends the
  legacy texts (with the week title / «18:00» deadline) to `summary.reminder_recipients`, then
  a one-line report to the admin chat. `backup_status_tick(session, *, telegram, backups_dir,
  now, admin_chat=None) -> str | None`: reads `<backups_dir>/last-verify.json`; alerts the admin when the file
  is missing, `ok` is false (include the errors), or `checked_at` is older than 8 days; at
  most one alert per day (reminder_log key `YYYY-MM-DD:backup`).
- `python -m romantika.worker` loops: job every 2 s, schedulers every 60 s, structured logs.
- PDF: `romantika.pdf.journal.render_journal_html(view: JournalView) -> str`,
  `render_journal_pdf(view) -> bytes` (WeasyPrint, DejaVu fonts, photos by absolute path).

## 10. PDF

`romantika/pdf/templates/journal.html` reads like a travel journal: a title page (season, name,
dates, level, three totals, the passport grid with ★/✓, the legend), then one section per stamped
week — dates, every report text of the week, up to 6 photos of that week (36 per journal), a
note for files that stayed in the app — then achievements, the dictionary (week words + the
participant's own), the facts and Mila's wish. Photos are embedded by `file://` path from
`MEDIA_DIR`; only downloaded images are used. Fonts: DejaVu Serif for headings, DejaVu Sans for
text (present in the image), colour emoji via Noto.

`romantika/pdf/journal.py`: `render_journal_html(view: JournalView) -> str` (Jinja) and
`render_journal_pdf(view) -> bytes` (WeasyPrint). Fonts: DejaVu (installed in image) with
Cyrillic. Photos referenced by absolute file paths under MEDIA_DIR (no network).

## 11. Ops

Local stand without Telegram (`scripts/dev-stack.sh up`): Postgres in Docker, the fake Bot API
(`python -m romantika.ops.fake_telegram`, `TELEGRAM_API_BASE=http://127.0.0.1:8081` — the
bot polls it, the worker delivers through it; `/_control/text|media|callback` act as a user,
`/_control/sent` reads what the bot sent), web, bot, worker; `scripts/dev-stack.sh link <id>
<name>` prints a signed Mini App link. Everything lives under `.dev/` (git-ignored).

- One image `docker/Dockerfile` (python:3.12-slim + tzdata + WeasyPrint deps + fonts-dejavu,
  `uv sync --frozen --no-dev`, non-root user `app` uid 1000). Commands: `bot`, `web`, `worker`,
  `migrate` (alembic upgrade head), `backup`.
- `docker/compose.yml`: `db` (postgres:16-alpine, named volume `pgdata`), `migrate`
  (one-shot), `bot`, `web` (`127.0.0.1:8010:8010`), `worker`, `backup` (same image, runs
  `scripts/backup.sh` on a daily schedule via a tiny loop, plus weekly `restore-verify.sh`).
  Named volumes: `pgdata`, `media`, `backups`. Own network. `restart: unless-stopped`,
  json-file log rotation.
- `docker/compose.vps.yml`: `HTTP(S)_PROXY=http://host.docker.internal:10809` for bot/worker/web
  egress to Telegram, `NO_PROXY` for internal names, `extra_hosts`, `cpus`/`mem_limit`
  (db 0.5/512m, bot 0.5/256m, web 0.5/384m, worker 0.5/512m, backup 0.25/256m).
- `scripts/backup.sh`: `pg_dump -Fc` → `/backups/db/romantika-YYYY-MM-DD.dump`; media
  snapshot `rsync -a --link-dest` → `/backups/media/YYYY-MM-DD/`; retention 30 days; writes
  `/backups/manifest-YYYY-MM-DD.json` (row counts per table, media count, total bytes,
  sha256 of dump).
- `scripts/restore-verify.sh`: restores latest dump into a scratch database, compares row
  counts with manifest, verifies sha256 of 20 random media files against DB, writes
  `/backups/last-verify.json` `{ok, checked_at, dump, tables, media_checked, errors}`.
- `scripts/mac-pull-backups.sh` + `scripts/launchd/com.romantika.backup-pull.plist`: rsync
  `/backups` from the VPS to `~/Backups/romantika/` daily via `ssh vps247 docker run ... tar`.
- `scripts/deploy.sh`: rsync repo (no data) to `/opt/stacks/romantika`, build sequentially
  on the VPS, `up -d`, `docker compose ps`, smoke `curl 127.0.0.1:8010/healthz`.
- CI: `.github/workflows/ci.yml` — ruff, mypy, pytest with a `postgres:16` service
  (`TEST_DATABASE_URL`).

### 11.1 Ops contract (binding; mirrors `tests/acceptance/test_stage6_ops.py`)

- `romantika.migration.legacy_import.import_legacy(session, *, sqlite_path: Path, season_id:
  int, media_store, telegram, now) -> ImportReport(legacy_counts: dict[str, int], imported:
  dict[str, int])` with mapping DOMAIN §9; naive legacy timestamps are Europe/Moscow;
  `правки_недель` are applied to `weeks` + audit rows; media downloaded via the gateway
  (file path extension from the Telegram file_path); idempotent by natural keys
  (reports: user+week+created_at+kind; words: user+word; etc.). CLI wrapper
  `python -m romantika.migration.legacy_import --sqlite … --season-slug mexico-2026`.
- `scripts/backup.sh` (bash, `set -euo pipefail`): env `DATABASE_URL` (strip `+asyncpg`),
  `MEDIA_DIR`, `BACKUP_DIR`, `RETENTION_DAYS` (default 30), optional `TODAY` (YYYY-MM-DD,
  for tests). Produces `db/romantika-<date>.dump` (`pg_dump -Fc`), `media/<date>/` (rsync
  `-a --link-dest` against the previous snapshot), `manifest-<date>.json` (`{date, tables:
  {name: rows}, media_files, media_bytes, dump_sha256}`), deletes dumps/snapshots/manifests
  older than the retention. Never touches `MEDIA_DIR` itself.
- `scripts/restore-verify.sh`: env as above + `SCRATCH_DATABASE_URL`; drops/recreates the
  scratch DB, `pg_restore`s the latest dump, compares row counts with the manifest, verifies
  sha256 of up to 20 media files of the latest snapshot against the `media` table, writes
  `last-verify.json` (`{ok, checked_at, dump, tables, media_checked, errors}`), exit 1 on any
  error.
- `scripts/mac-pull-backups.sh` + `scripts/launchd/com.romantika.backup-pull.plist`,
  `scripts/deploy.sh`, `docker/Dockerfile`, `docker/compose.yml`, `docker/compose.vps.yml`,
  `.github/workflows/ci.yml` as in §11.

## 12. Testing contract

- `make check` = `uv run ruff check . && uv run ruff format --check . && uv run mypy romantika
  && uv run pytest -q`. This is the deterministic acceptance for every stage, plus
  stage-specific `tests/acceptance/stageN_*` files (read-only for implementers).
- Postgres for tests: `tests/conftest.py` provides `session_factory`/`db_session` fixtures using
  `TEST_DATABASE_URL` if set, otherwise `testcontainers[postgres]`. Each test runs in a
  transaction rolled back at the end, or on a freshly migrated schema per session.
- Bot handlers are tested through services plus router smoke tests; Telegram calls are
  captured by a fake `TelegramGateway`.
- Web is tested with `httpx.AsyncClient(app=...)` and a test helper `sign_init_data(user)`.

## 13. Legacy migration (`romantika/migration/legacy_import.py`)

`python -m romantika.migration.legacy_import --sqlite path --season mexico-2026 [--download]`
maps 12 legacy tables to the model (see DOMAIN.md §9 for the mapping), downloads every
`file_id` via the bot token into MEDIA_DIR, is idempotent (re-running updates nothing that
already matches), and prints a reconciliation table (legacy counts vs imported counts).

## 14. Process (binding; mirrors `tests/acceptance/test_stage7_process.py`)

- `CLAUDE.md` (rules + commands + change workflow), `README.md`, `docs/RUNBOOK.md`
  (Deploy, Logs, Backup, Restore, Cut-over from the legacy bot, Release checklist,
  Rollback), `docs/GUIDE-RU.md` (owner's guide in Russian: admin Mini App, bot panel,
  what backups are and how to check them, how to ask Claude for a change safely, PDF).
- In-repo review roles `.claude/agents/forge-*.md` (copies of the global forge roles,
  project-specific rubrics appended), skill `.claude/skills/release-check/SKILL.md` and
  workflow `.claude/workflows/release-check.js` (verifier + code/security/data lenses over
  the diff of the release branch; ui lens optional).

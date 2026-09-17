# Changelog

## v2.2.1 — 2026-09-18 (Mila edits the season's calendar herself; deployed)

For Mila: in the admin's «Задания» a week can be added («＋ Добавить неделю»), a future
week's number and dates can be changed («Переставить») and an untouched future week can be
deleted. The rules (DOMAIN §1): only into the future and inside the season; a week that has
started keeps its calendar; a week anyone touched (intents, reports, stamps, words, facts,
reply links) is never deleted. A new week is a **draft** (`weeks.announced_at`, migration
`c4e8f1a2b9d3`): participants do not see it, it is never the current week, it takes no stamps
or intents and it costs nobody a freeze — Mila plans weeks ahead and fills them in as the
channel's plan settles, then presses «Объявить» (needs a title and a minimum). Announcing is
one way, and an announced week keeps its title and minimum, so stamps never lose their week.
Every calendar change is in «Изменения», named by the week's number.

For participants: nothing new to do. Week numbers may now have gaps; the passport, the PDF
grid, the public progress bar and «N-я из M» on the home screen follow the season's real
weeks in calendar order instead of assuming 1..N.

## v2.2.0 — 2026-09-16 (the owner's harness)

Nothing changes for participants: the bot, the app and the worker are the same code.

For Mila: every request is sorted into one of four routes (content in the admin app, a
micro-change, a feature with a plan page and a «было — стало» report, an emergency); eight
Claude Code skills carry the procedures and answer to Russian phrases; a local stand on her
Mac with thirty invented participants (never production data) in a work mode (fake Telegram)
and a live mode (the test bot in real Telegram); screenshots and conversation mock-ups for the
reports; a project memory `brain/` with task cards, a generated backlog, bugs, ideas and
production snapshots; `docs/SETUP-RU.md` for the one-time setup and a rewritten «how to ask
Claude» in `docs/GUIDE-RU.md`.

Under the hood: `CLAUDE.md` rewritten around the routes and the branch model
`master ← dev ← feature/NN-slug`; `.claude/settings.json` (Russian answers, deny on destructive
commands, ask on deploy); critic agents replace the `forge-*` roles and the Workflow-based
release check; `romantika/ops/demo_data.py`, `romantika/ops/chat_mockup.py`, `scripts/shots.sh`,
`scripts/rc.sh`, `scripts/prod-snapshot.sh`, `scripts/brain_index.py`; acceptance stage 7 mirrors
the new process and stage 8 forbids Cyrillic identifiers and ratchets Russian literals outside
`romantika/texts/`; `scripts/mac-pull-backups.sh` defaults to the `romantika-vps` alias.

## v2.1.1 — 2026-09-05 (hotfix: photo reports from the app)

On the VPS the media volume was mounted read-only into `web`, so every report with a file from
the Mini App answered «Internal Server Error» (text-only reports and the bot were fine). The
mount is read-write now; `/healthz` reports `media` and answers 503 when the directory is not
writable, so the compose healthcheck and the deploy smoke catch it before people do; the
upload cleanup no longer hides the original error in the log.

For the people who change the code: `master` of the owner's repository is now the trunk;
`CLAUDE.md` describes the change loop (branch → `make check` → critics on the local stand →
data review gate → deploy → report), and `docs/RUNBOOK.md` «Access» how the VPS is reached
(the `romantika` user; `scripts/deploy.sh` refuses to run as anyone else).

## v2.1.0 — 2026-09-05 (Mini App round two, new bot)

For participants: reports can be edited in the app while the week is open (text and files;
the stamp follows), «изменено» marks the edited ones; a lost network answer no longer makes a
second report; after a maximum the app no longer offers «это был минимум»; file limits (10 files,
50 MB) are explained before the upload; the deadline names the real last day of the week (the
closing week ends on a Wednesday); the PDF journal is a proper journal (title page with the
passport, weeks with texts and photos, dictionary, facts, Mila's word) named
«Романтика-Мексика-Имя.pdf»; the day after the season ends everyone with a stamp gets their
journal automatically. Texts were reread end to end: Mila speaks in the first person, nothing
is gendered, a text after a photo says «минимум — звёздочка остаётся», the FAQ in the app points
at the app's own screens, a stamp Mila removed is not brought back by old reports, the same
word twice is refused, freezes and achievements from the admin app are announced to the
person.

For Mila: «Письма» — an inbox of everything that is not a report, with replies from the app or
the chat marking the same letter; «Напомнить сейчас» is about the week on screen and refuses
past weeks; the week editor keeps its state after saving; explicit stamp choice with a confirm
before removing (a week that has not started refuses stamps, intents and reminders); people
filters «Без штампа на неделе» / «Взялись и молчат»; the audit log names who did what, in
Russian; letters show their files; the Sunday draft has no service lines in the text to copy
and none for a week that has not started; a failed screen says so instead of spinning.
After the second round of critics: a stamp Mila removed stays removed on every path (also
when a later report earned the week and was then cancelled); the PDF footer is back (the
template had HTML-escaped its CSS string); a retried edit in flight is applied once; a NUL
byte or an over-long attempt id is a 422, not a 500 or a collapsed report; the Sunday draft
quotes one line per person; the bot's «это не отчёт» makes one letter like the app's.
From the UI critic: no dead «Это был минимум» button in the bot once the star is there; the FAQ says honestly what an edit does to the stamp; «О клубе» in the app points at the «Сегодня» tab; the task card follows the stamp right after sending or upgrading; a word without spaces no longer widens the page; the people list says «отчёты есть, штамп снят» instead of «пока без отчёта»; two gendered lines that slipped into the first round are neutral again.

Under the hood: `reports.client_id`, `reports.edited_at`, `letters`, `admin_links.letter_id`
(migration b7d4e2a90c15); jobs `season_journals`; a local stand with a fake Bot API
(`scripts/dev-stack.sh`); vendored Telegram bridge; production on `@romantika_marshrutov_bot`.

## v2.0.0 — 2026-09-04 (rewrite)

For participants: same bot, same buttons and texts; voice and audio now count as a minimum
report; messages outside a week are saved and passed to Mila; the «Это был максимум/минимум»
button works; the journal is also a Mini App with photos and a PDF.

For Mila: admin Mini App (weeks, participants, stamps, freezes, achievements, wishes, facts,
audit log); backups every night with a weekly restore check and a Telegram alert.

Under the hood: Python 3.12, aiogram 3, FastAPI, Postgres 16, one Docker image, media stored
on the server, legacy SQLite import.

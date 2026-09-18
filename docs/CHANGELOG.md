# Changelog

## v2.6.0 — 2026-09-19 (Mila's edits of 19.09: the day card, the names of weeks, a freeze for the first fact)

For participants: the day of the Maya calendar is four lines now — the label, the day with
its sign, what it means, and «Узнай своё предназначение →» where the calendar link used to
sit — the same words now name the calendar everywhere (the public season page, the button on
the calendar itself, the bot's button). The status line says what the deadline is for («дедлайн задания до воскресенья, 18:00»).
The task card carries the week's name as Mila wrote it, with no number in front; every screen
that prints the number itself («Неделя 3 · …», «3. …») drops the word «Неделя» from the name
when a latin word follows it, so a week named as the channel names it («Неделя rola [музыка]»)
never says it twice, while a Russian name («Неделя памяти») stays whole. The
season's name on the «Сезон» tab is in the accent colour, like on «Неделя». **The first own
fact of a season earns a freeze**, like the first own word: the form promises it, the sheet
lists it, the answer carries it and the tile above redraws; the bot promises a freeze only
while `freezes.pending` says it can still be earned — never to Mila, whose facts are the
club's, and never above the ceiling. The end-of-season list no longer
guesses the reader's gender and no longer names the month.

**A season now holds six freezes instead of five** (two base, four earned): with three
automatic reasons the old ceiling left no room for the ones Mila gives by hand. Copies to
Mila decline the name — «📨 Отчёт … от Юли», not «от Юля» — for the endings that are safe
(-а, -я, -й, and a surname in -ова/-ина); anything else stays as it is.

For Mila: name the weeks as the channel does — the guide says what the screens then show.
Under the hood: `ru.week_name` / `RM.weekName` (one helper, used by the bot, the app, the
admin app and the PDF), `facts.add_own` with the `fact` freeze reason (migration
`e7f8a9b0c1d2`: the CHECK constraint and the «once per season» partial unique index),
`f8a9b0c1d2e3` (the ceiling, rewriting `seasons.max_freezes` — a fresh backup first),
`ru.name_genitive`,
`POST /api/facts` answers `FactAdded` with `freeze_granted`.

## v2.5.0 — 2026-09-18 (Mila's edits of 18.09: the week tab, freezes, personal words and facts)

For participants: the «Неделя» tab opens with the club name small, the season name large in
the accent colour, then the date and the week line; the day card is one line about the Maya
day (no memory word, no disclaimer); the report forms say «Загрузить фото или видео». The
«Заморозки» sheet is a plain sentence and a list, with the letter form as its own block; the
legend under the week grid is a column; the chronicle in «Сезон» shows each week's own mark
(⭐ ✅ ❄️ ◦) instead of ✓. «О клубе» no longer breaks its sentences at bold words. **Own words
and facts are personal**: «Мои слова» and «Мои факты» with their forms live in «Рюкзак» and
are seen by the author only (and by Mila in the person's card); «Сезон» keeps the week words
and Mila's facts; the bot's `/words` and `/facts` show the same; the journal and the PDF
carry the person's own. The freeze for the first word stays.

For Mila: the texts of any week — a finished one included — are editable in the admin app
and in «⚙️ Мила» (only the calendar of a started week stays frozen); the person's card shows
their facts. «Что будет в конце сезона» lists what the PDF holds in plain words. Under the
task there are two answers now — «Берусь» and «В этот раз мимо»; an old «Попробую» button on
a cached message still answers and counts as «берусь». A bare word typed into the chat
(«Паспорт», «Паспорт!», «Сегодня») is a one-word report, not a button: a button starts with
its emoji. Four refusals people can see are new: «Эта неделя уже прошла…», «Штамп за эту
неделю у тебя уже есть…» and «Эта неделя ещё не открылась…» on an intent button (in the bot
and in the app alike), «Такой факт у тебя уже записан» on a repeated fact (with a hint how to
try again); a fact is capped at 4000 characters in the bot as in the app. Mila's fact form in the bot says her facts are the club's; the help sheet no
longer breaks a sentence at «две заморозки»; the freezes sheet says «в канале» like the rest.

Under the hood: the intent rules live in `people.choose_intent`, shared by the bot button and
`POST /api/intent` (a repeated answer is stored but not copied to Mila); every «once per
person» write whose duplicate check is read-then-write — a word, a fact, an intent — takes a
transaction-scoped advisory lock (`services/locks.py`), and the first row of a user is an
idempotent insert, so a double tap or two devices no longer make copies or a 500; the texts of a week are bounded like their
columns (255 / 4000 characters, no NUL) and answer 422 in Russian; the stand's demo facts of
Mila carry no author, as production does.

## v2.4.0 — 2026-09-18 (late reports into the journal)

For participants: a week that has ended takes a report until the season ends — «Добавить в
журнал» (or «Дописать в журнал» when the week already has one) on the week's sheet in
«Сезон» and from the week cell in «Рюкзак». The report goes into the journal chapter of
that week and into the PDF marked «дослано позже»; it earns no stamp, gives no freeze back,
and never keeps a stamp alive when the on-time report is cancelled. It can be edited and
taken back until the season ends. The chat still takes reports for the running week only;
`/help` says where a past week is added.

For Mila: a copy in the chat headed «📨 Имя дослала за неделю N: …» with «Штамп не
ставится» (or «Штамп за неделю как был» when the week has one); reply as usual. Every copy
of a report or an edit now ends with the same line, «Ответь реплаем — передам». The week's summary and «Привал» ignore late reports. The
participant card in the admin app shows the mark, and report kinds there are in Russian now.

Under the hood: `reports.late` (migration `d5e6f7a8b9c0`, additive; the downgrade refuses
while late reports exist), `reports.accept_late`, `POST /api/reports` with `week_number`,
`WeekOut.late_open`, `ReportOut.late`; every stamp computation and the week summary filter
`late = false`; the journal carries `JournalEntry(text, late)` and late-only chapters
(`level = None`); `RM.kindName` shared by both apps.

## v2.3.0 — 2026-09-18 (one door: a button in the chat, three tabs in the app)

For participants: the keyboard under the chat is one button, «🎒 Открыть клуб», and the
command menu lists `/start` and `/help` only — the old labels and commands keep answering for
cached keyboards. The Mini App has three tabs instead of five: **Неделя** (day and word first,
then the task, intent, every report of the week, the report form), **Рюкзак** (passport with
freezes as a number and «как заработать ещё?», achievements, the wish, the journal with
«Собрать» PDF), **Сезон** (released weeks as a chronicle, the word form, week words, facts,
about, channel). Old tab paths (`/app/journal` …) open the right tab. A letter to Mila inside a
week goes through `/help` → «✉️ Написать Миле». Nothing in reports, stamps, freezes or
reminders changes.

For Mila: nothing to do; DOMAIN §7 and GUIDE-RU describe the new layout. The bot's
command list and menu button are applied by the bot itself at every start (log line
`menu_applied`), so BotFather needs no visit; the name and descriptions still come from
`python -m romantika.ops.telegram_setup`.

Also for participants: when a service refuses something in the chat (a word the person
already has), the bot now says so instead of staying silent — and the word dialog closes, so
the next message is a report again, not a word in the shared dictionary.

Under the hood: a service's `Refused` reaching the bot is answered by a dispatcher error
handler (`bot/app.py`); a multipart upload cut by the client answers 400 with one log line
instead of an ASGI traceback; `keyboards.app_page_url` builds every Mini App link and `PUBLIC_BASE_URL`
loses a trailing slash in `Settings`; a request body that fails validation answers with one
Russian sentence (`web/app.py`) instead of pydantic's JSON, since the app shows `detail` as
is; the fake Bot API keeps a reply keyboard beside the echoed message so the chat mock-up can
draw it. No migration.

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

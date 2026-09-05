# Changelog

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

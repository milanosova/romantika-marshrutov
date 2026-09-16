"""`python -m romantika.ops.demo_data [--today YYYY-MM-DD] [--participants 30] [--force]`.

Fills the local stand with invented participants so the admin app, the journal and the PDF look
like the real thing: reports with placeholder photos, stamps, freezes, intents, words, facts,
letters, achievements, wishes, one edited report. Deterministic (fixed seed), idempotent (does
nothing when the demo users already exist, unless `--force`).

Never run against production: the stand is the only place where invented people belong
(CLAUDE.md rule 1). Everything but the admin flag goes through the services (there is no
service that grants admin rights — they come from ADMIN_IDS), so the rows are exactly what the
bot and the app would have written.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import struct
import zlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.config import get_settings
from romantika.db import models
from romantika.db.session import make_session_factory
from romantika.domain.calendar import MOSCOW, moscow_today
from romantika.domain.types import ReportKind
from romantika.services import achievements, content, facts, freezes, letters, people, reports, wishes, words
from romantika.services.errors import Refused
from romantika.services.letters import Source
from romantika.services.media import MediaStore
from romantika.services.people import TelegramUser
from romantika.services.reports import IncomingFile, IncomingMessage

FIRST_DEMO_ID = 1001
ADMIN_ID = 900001
SEED = 20260916

# Personas decide how a participant behaves week after week; the mix is what makes the admin
# filters («без штампа», «взялись и молчат») and the journal look realistic.
PERSONAS = ("star", "steady", "steady", "quiet", "frozen", "late", "silent", "letters", "editor", "steady")

NAMES = [
    ("Алиса", "alisa_r"),
    ("Марина", "marina_v"),
    ("Ольга", "olga_t"),
    ("Настя", "nastya_k"),
    ("Катя", "katya_m"),
    ("Лена", "lena_p"),
    ("Юля", "yulia_s"),
    ("Ира", "ira_b"),
    ("Даша", "dasha_l"),
    ("Света", "sveta_n"),
    ("Таня", "tanya_g"),
    ("Аня", "anya_d"),
    ("Вера", "vera_z"),
    ("Маша", "masha_f"),
    ("Полина", "polina_e"),
    ("Соня", "sonya_h"),
    ("Женя", "zhenya_c"),
    ("Лиза", "liza_u"),
    ("Наташа", "natasha_i"),
    ("Оксана", "oksana_j"),
    ("Галя", "galya_o"),
    ("Рита", "rita_w"),
    ("Люба", "lyuba_q"),
    ("Инна", "inna_x"),
    ("Кира", "kira_y"),
    ("Нина", "nina_a"),
    ("Зоя", "zoya_bb"),
    ("Алёна", "alyona_cc"),
    ("Варя", "varya_dd"),
    ("Тома", "toma_ee"),
    ("Ева", "eva_ff"),
    ("Лида", "lida_kk"),
]

REPORT_TEXTS = [
    "Сделала минимум: посмотрела ролик про {title} и записала три слова.",
    "Готово! {title} — оказалось интереснее, чем думала. Фото прилагаю.",
    "Отчёт: {title}. Ходила с подругой, обсудили на кухне до ночи.",
    "Максимум на этой неделе: {title} и ещё нашла рецепт, буду повторять.",
    "Успела в последний день, но успела. {title} — зачёт.",
    "Минимум, но с удовольствием. Про {title} теперь знаю больше, чем хотела.",
]
LETTER_TEXTS = [
    "Мила, привет! Можно мне заморозку на следующей неделе — уезжаю без интернета.",
    "Спасибо за сезон, очень нравится формат. Вопрос: считается ли аудиокнига?",
    "Я перепутала неделю и отправила отчёт не туда, помоги, пожалуйста.",
]
WORDS = [
    ("sobremesa — разговоры за столом после еды", 1),
    ("alebrije — фантастический зверь из папье-маше", 2),
    ("grito — крик, клич", 3),
    ("cempasúchil — бархатцы, цветы Дня мёртвых", 4),
]
FACTS = [
    "В Мексике 68 официальных языков, испанский — только один из них.",
    "Шоколад изначально был горьким напитком, а не сладостью.",
    "Мехико стоит на дне высохшего озера и медленно проседает.",
]
WISHES = [
    "Ты прошла этот сезон так, как будто и правда там была. Спасибо!",
    "Пусть следующая страна будет ещё дальше, а ты — ещё смелее.",
]


@dataclass(frozen=True, slots=True)
class Persona:
    name: str
    username: str
    user_id: int
    kind: str


def _png(width: int, height: int, hue: int) -> bytes:
    """A small solid-ish PNG with a diagonal gradient; no image library needed."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter: none
        for x in range(width):
            shade = (x + y) * 255 // (width + height)
            r = (hue * 37 + shade) % 256
            g = (hue * 91 + 255 - shade) % 256
            b = (hue * 53 + shade // 2 + 60) % 256
            rows += bytes((r, g, b))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )


def _at(day: date, hour: int) -> datetime:
    """An aware instant at `hour` o'clock Moscow time on `day`, as UTC — never later than now.

    The clamp matters on the current day: a report stamped this evening would sit in the future
    and hide its author from the reminder scheduler, which counts by `moscow_now()`.
    """
    moment = datetime(day.year, day.month, day.day, hour, tzinfo=MOSCOW).astimezone(UTC)
    return min(moment, datetime.now(UTC) - timedelta(seconds=1))


async def _demo_users_present(session: AsyncSession, count: int) -> int:
    query = (
        select(func.count())
        .select_from(models.User)
        .where(models.User.id.between(FIRST_DEMO_ID, FIRST_DEMO_ID + count - 1))
    )
    return int((await session.execute(query)).scalar_one())


async def _ensure_admin(session: AsyncSession, now: datetime) -> None:
    await people.upsert_user(session, TelegramUser(id=ADMIN_ID, first_name="Мила", username="mila_admin"), now=now)
    row = await session.get(models.User, ADMIN_ID)
    if row is not None:
        row.is_admin = True
    await session.flush()


async def _report(
    session: AsyncSession,
    store: MediaStore,
    *,
    season_id: int,
    persona: Persona,
    week: content.WeekDTO,
    now: datetime,
    with_photo: bool,
    rng: random.Random,
) -> int:
    text = rng.choice(REPORT_TEXTS).format(title=week.title.lower())
    files = (
        [IncomingFile(kind=ReportKind.PHOTO, file_id=None, mime="image/png", width=640, height=480)]
        if with_photo
        else []
    )
    message = IncomingMessage(kind=ReportKind.PHOTO if with_photo else ReportKind.TEXT, text=text, files=files)
    result = await reports.accept(session, season_id=season_id, user_id=persona.user_id, message=message, now=now)
    for media_id in result.media_ids:
        row = await session.get(models.Media, media_id)
        assert row is not None
        part = store.upload_part_path(row.path)
        part.write_bytes(_png(640, 480, persona.user_id + week.number))
        await store.save_upload(session, media_id, part, now=now)
    return result.report_id


async def populate(session: AsyncSession, store: MediaStore, *, today: date, participants: int) -> dict[str, int]:
    """Create the demo world; returns counts for the summary line."""
    rng = random.Random(SEED)
    participants = min(participants, len(NAMES))
    season = await content.active_season(session, today=today)
    if season is None:
        raise SystemExit("no active season: run `python -m romantika.ops.seed --activate` first")
    weeks = await content.weeks(session, season.id)
    passed = [week for week in weeks if week.ends_on < today]
    current = next((week for week in weeks if week.starts_on <= today <= week.ends_on), None)
    season_start = _at(season.starts_on, 10)
    await _ensure_admin(session, season_start)

    personas = [
        Persona(name=NAMES[i][0], username=NAMES[i][1], user_id=FIRST_DEMO_ID + i, kind=PERSONAS[i % len(PERSONAS)])
        for i in range(participants)
    ]
    counts = {"participants": 0, "reports": 0, "photos": 0, "letters": 0, "words": 0, "facts": 0}

    for persona in personas:
        joined_week = weeks[1] if persona.kind == "late" and len(weeks) > 1 else weeks[0]
        joined_at = _at(joined_week.starts_on, 9 + persona.user_id % 8)
        await people.upsert_user(
            session, TelegramUser(id=persona.user_id, first_name=persona.name, username=persona.username), now=joined_at
        )
        await people.ensure_member(session, season.id, persona.user_id, now=joined_at)
        counts["participants"] += 1

        for week in passed + ([current] if current else []):
            if week.starts_on < joined_week.starts_on:
                continue
            is_current = current is not None and week.id == current.id
            report_day = week.starts_on + timedelta(days=rng.randint(1, 5))
            if is_current and report_day > today:
                # The current week is only half lived: intents are set, reports come later.
                choice = rng.choice((models.IntentChoice.TAKE, models.IntentChoice.TRY, models.IntentChoice.SKIP))
                await people.set_intent(
                    session,
                    season_id=season.id,
                    user_id=persona.user_id,
                    week_id=week.id,
                    choice=choice,
                    now=_at(week.starts_on, 12),
                )
                continue
            plan = {
                "star": ("max", 1.0),
                "steady": ("min", 0.85),
                "quiet": ("min", 0.4),
                "frozen": ("min", 0.6),
                "late": ("max", 0.8),
                "silent": ("min", 0.0),
                "letters": ("min", 0.7),
                "editor": ("max", 0.9),
            }[persona.kind]
            level, probability = plan
            if rng.random() > probability:
                # Missed the week; «взялись и молчат» needs an intent without a report now and then.
                if rng.random() < 0.5:
                    await people.set_intent(
                        session,
                        season_id=season.id,
                        user_id=persona.user_id,
                        week_id=week.id,
                        choice=models.IntentChoice.TAKE,
                        now=_at(week.starts_on, 12),
                    )
                continue
            with_photo = level == "max" or (level == "min" and rng.random() < 0.25)
            await people.set_intent(
                session,
                season_id=season.id,
                user_id=persona.user_id,
                week_id=week.id,
                choice=models.IntentChoice.TAKE,
                now=_at(week.starts_on, 11),
            )
            report_id = await _report(
                session,
                store,
                season_id=season.id,
                persona=persona,
                week=week,
                now=_at(report_day, 19 + persona.user_id % 4),
                with_photo=with_photo,
                rng=rng,
            )
            counts["reports"] += 1
            counts["photos"] += int(with_photo)
            if persona.kind == "editor" and passed and week.id == passed[-1].id:
                await reports.edit(
                    session,
                    user_id=persona.user_id,
                    report_id=report_id,
                    text="Дописала вечером: нашла ещё одно место, куда хочу вернуться.",
                    new_files=[],
                    remove_media_ids=[],
                    now=_at(report_day, 23),
                    edit_key=f"demo-edit-{report_id}",
                )

        if persona.kind == "frozen":
            await freezes.grant(
                session,
                season_id=season.id,
                user_id=persona.user_id,
                reason=models.FreezeReason.MANUAL,
                granted_by=ADMIN_ID,
                now=season_start + timedelta(days=3),
                note="уезжала",
            )
        if persona.kind == "letters" or persona.user_id % 9 == 0:
            await letters.create(
                session,
                season_id=season.id,
                user_id=persona.user_id,
                source=Source.BOT,
                text=rng.choice(LETTER_TEXTS),
                now=_at(today - timedelta(days=rng.randint(0, 6)), 21),
            )
            counts["letters"] += 1
        if persona.kind in ("star", "late", "editor") and passed:
            raw, week_number = rng.choice(WORDS)
            week_for_word = next((week for week in weeks if week.number == week_number), passed[0])
            if week_for_word.starts_on <= today:
                try:
                    await words.add(
                        session,
                        season_id=season.id,
                        user_id=persona.user_id,
                        week_id=week_for_word.id,
                        raw=raw,
                        now=_at(week_for_word.starts_on + timedelta(days=2), 20),
                    )
                    counts["words"] += 1
                except Refused:  # a repeated word is refused by the service; demo data does not care
                    pass
        if persona.kind == "star" and passed:
            await achievements.award(
                session,
                season_id=season.id,
                user_id=persona.user_id,
                code_or_text="повар",
                awarded_by=ADMIN_ID,
                now=_at(passed[-1].ends_on, 21),
            )
        if persona.user_id % 11 == 0:
            await wishes.set_wish(
                session, season_id=season.id, user_id=persona.user_id, text=rng.choice(WISHES), now=_at(today, 12)
            )

    for index, text in enumerate(FACTS):
        week_for_fact = passed[index % len(passed)] if passed else None
        await facts.add(
            session,
            season_id=season.id,
            week_id=week_for_fact.id if week_for_fact else None,
            text=text,
            author_id=ADMIN_ID,
            now=_at(today - timedelta(days=index + 1), 15),
        )
        counts["facts"] += 1
    return counts


async def run(*, today: date, participants: int, force: bool) -> None:
    settings = get_settings()
    if settings.env != "dev":
        raise SystemExit("demo data is for the local stand only (ENV=dev)")
    factory = make_session_factory(settings.database_url)
    store = MediaStore(settings.media_dir)
    async with factory() as session, session.begin():
        present = await _demo_users_present(session, participants)
        if present and not force:
            print(f"demo data already present ({present} demo users); use --force to add again")
            return
        counts = await populate(session, store, today=today, participants=participants)
    print("demo data: " + ", ".join(f"{key}={value}" for key, value in counts.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill the local stand with invented participants")
    parser.add_argument(
        "--today", type=date.fromisoformat, default=None, help="pretend today is this date (default: Moscow today)"
    )
    parser.add_argument("--participants", type=int, default=30)
    parser.add_argument("--force", action="store_true", help="populate even if demo users exist")
    args = parser.parse_args()
    asyncio.run(
        run(today=args.today or moscow_today(), participants=min(args.participants, len(NAMES)), force=args.force)
    )


if __name__ == "__main__":
    main()

"""The deadline names the real last day of the week: the closing week of a season ends on a
Wednesday, not a Sunday (DOMAIN §1, spec v2 D3)."""

from __future__ import annotations

from datetime import date

from romantika.domain.types import StampLevel
from romantika.services.content import WeekDTO
from romantika.texts import ru


def week(number: int, starts: date, ends: date) -> WeekDTO:
    return WeekDTO(
        id=number, season_id=1, number=number, title="Красками", starts_on=starts, ends_on=ends,
        intro="", task_min="мин", task_max="макс", word="", word_ru="", word_meaning="",
    )  # fmt: skip


def test_regular_week_ends_on_sunday() -> None:
    first = week(1, date(2026, 8, 31), date(2026, 9, 6))
    assert ru.deadline_text(first) == "воскресенье 06.09, 18:00"
    assert ru.week_end_accusative(first) == "в воскресенье"
    assert "воскресенье 06.09" in ru.task_text(first)


def test_closing_week_ends_on_wednesday() -> None:
    last = week(12, date(2026, 11, 16), date(2026, 11, 18))
    assert ru.deadline_text(last) == "среда 18.11, 18:00"
    assert ru.week_end_accusative(last) == "в среду"
    assert "В среду покажу общие итоги" in ru.report_reply(last, StampLevel.MIN, freeze_granted=False)


def test_the_journal_file_is_named_after_the_season_and_the_person() -> None:
    from romantika.pdf.journal import journal_filename

    assert journal_filename("Мексика", "Алиса") == "Романтика-Мексика-Алиса.pdf"
    assert journal_filename("Мексика", None) == "Романтика-Мексика.pdf"
    assert journal_filename("Южная Корея", "Al/ice ../x") == "Романтика-Южная-Корея-Al-ice-x.pdf"

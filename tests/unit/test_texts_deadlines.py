"""The deadline names the real last day of the week: the closing week of a season ends on a
Wednesday, not a Sunday (DOMAIN §1, spec v2 D3)."""

from __future__ import annotations

from dataclasses import replace
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


# --- the name Mila writes, next to the number a screen prints itself --------------------------


def test_week_name_drops_the_word_mila_writes_in_the_title() -> None:
    """Mila names weeks as the channel does («Неделя rola [музыка]»), and the bot, the passport
    and the PDF print «Неделя N ·» themselves: the word must not be said twice."""
    assert ru.week_name("Неделя rola [музыка]") == "rola [музыка]"
    assert ru.week_name("неделя antojo [еда]") == "antojo [еда]"
    assert ru.week_name("Недели города") == "Недели города", "only the standalone word is dropped"
    assert ru.week_name("За столом") == "За столом"
    assert ru.week_name("Неделя") == "Неделя", "a title of one word survives as it is"
    assert ru.week_name("") == ""


def test_the_task_of_a_week_says_its_number_once() -> None:
    named = replace(week(3, date(2026, 9, 14), date(2026, 9, 20)), title="Неделя rola [музыка]")
    assert "Неделя 3 · rola [музыка]" in ru.task_text(named)
    assert "Неделя rola" not in ru.task_text(named)


def test_a_week_nobody_has_seen_keeps_its_placeholder() -> None:
    """The app sends «Неделя 7» as the name of a week that has not opened: trimming it would
    leave a bare «7» in the sheet's heading (critic-code, 19.09)."""
    assert ru.week_name("Неделя 7") == "7", "the helper itself trims"
    # The app compares the raw title with the placeholder before trimming — see app.js openWeek.

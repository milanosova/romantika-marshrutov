"""Pure unit tests of the bot's keyboards and of the 4096-character splitter (DOMAIN §7, §10)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from romantika.bot import keyboards
from romantika.bot.keyboards import PEOPLE_PAGE, button_action, normalize_button
from romantika.bot.send import TELEGRAM_LIMIT, split_text
from romantika.domain.types import StampLevel
from romantika.services.achievements import AchievementTypeDTO
from romantika.services.content import WeekDTO
from romantika.services.facts import FactDTO
from romantika.services.people import UserDTO

HTTPS = "https://romantika.example.test"
HTTP = "http://localhost:8000"


def user(user_id: int, name: str) -> UserDTO:
    return UserDTO(id=user_id, username=f"u{user_id}", first_name=name, last_name=None, joined_at=datetime(2026, 8, 18))


def data_of(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def labels_of(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


# --- split_text ---------------------------------------------------------------------


def test_split_text_never_returns_an_empty_piece() -> None:
    assert split_text("") == []
    assert split_text("   \n\n  ") == ["   \n\n  "], "short whitespace is left alone"
    long_gap = "А" * 4000 + "\n\n" + " " * 5000 + "\n\n" + "Б" * 10
    pieces = split_text(long_gap)
    assert all(piece.strip() for piece in pieces), "a whitespace-only piece is never sent"
    assert all(len(piece) <= TELEGRAM_LIMIT for piece in pieces)


def test_split_text_keeps_a_piece_that_fits() -> None:
    exact = "я" * TELEGRAM_LIMIT
    assert split_text(exact) == [exact]
    assert split_text("я" * (TELEGRAM_LIMIT + 1))[0] == "я" * TELEGRAM_LIMIT, "no separator → a hard cut"
    assert len(split_text("я" * (TELEGRAM_LIMIT + 1))) == 2


def test_split_text_prefers_paragraph_then_line_then_space() -> None:
    paragraphs = "\n\n".join("абзац " * 400 for _ in range(4))
    pieces = split_text(paragraphs)
    assert len(pieces) >= 2
    assert all(len(piece) <= TELEGRAM_LIMIT for piece in pieces)
    assert "".join(pieces).replace("\n", "").replace(" ", "") == paragraphs.replace("\n", "").replace(" ", "")


# --- button detection ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "action"),
    [
        ("📋 Задание", "task"),
        ("📋️ Задание", "task"),  # the desktop client adds U+FE0F
        ("ЗАДАНИЕ", "task"),
        ("🌤 Сегодня", "today"),
        ("📘 Паспорт", "passport"),
        ("🛡 Мой паспорт", "passport"),
        ("📖 Словарь", "words"),
        ("💡 Что узнали", "facts"),
        ("⋯ Ещё", "more"),
        ("❔ Помощь", "help"),
        ("✉️ Написать Миле", "write"),
        ("⚙️ Мила", "admin"),
        ("📔 Мой журнал", "journal"),
        ("тако удались, фото ниже", None),
        ("", None),
        (None, None),
    ],
)
def test_button_action(label: str | None, action: str | None) -> None:
    assert button_action(label) == action


def test_normalize_button_drops_everything_but_letters() -> None:
    assert normalize_button("📋️  Задание  ") == "задание"
    assert normalize_button("В этот раз мимо") == "в этот раз мимо"
    assert normalize_button("2026") == ""


# --- people pagination (DOMAIN §10.7: «назад» must never be cut off) ------------------


@pytest.mark.parametrize("total", [0, 1, PEOPLE_PAGE, PEOPLE_PAGE + 1, PEOPLE_PAGE * 2 + 3])
def test_people_list_always_offers_back(total: int) -> None:
    people = [user(100 + index, f"Человек{index}") for index in range(total)]
    markup = keyboards.people_list(people, prefix="badge", page=0, exclude=None)
    assert "adm:panel" in data_of(markup)
    picked = [d for d in data_of(markup) if d.startswith("adm:badge:")]
    assert len(picked) == min(total, PEOPLE_PAGE)
    has_next = "adm:people:badge:1" in data_of(markup)
    assert has_next is (total > PEOPLE_PAGE)
    assert "adm:people:badge:-1" not in data_of(markup)


def test_people_list_paginates_and_excludes() -> None:
    people = [user(100 + index, f"Человек{index}") for index in range(45)]
    page1 = keyboards.people_list(people, prefix="freeze", page=1, exclude=100)
    assert len([d for d in data_of(page1) if d.startswith("adm:freeze:")]) == PEOPLE_PAGE
    assert "adm:people:freeze:0" in data_of(page1) and "adm:people:freeze:2" in data_of(page1)
    assert "adm:freeze:100" not in data_of(page1), "the excluded person is never offered"

    last = keyboards.people_list(people, prefix="freeze", page=2, exclude=100)
    assert "adm:people:freeze:3" not in data_of(last), "no «дальше» past the last page"
    assert "adm:people:freeze:1" in data_of(last) and "adm:panel" in data_of(last)


def test_people_list_past_the_end_is_still_navigable() -> None:
    markup = keyboards.people_list([user(1, "Один")], prefix="wish", page=5, exclude=None)
    assert not [d for d in data_of(markup) if d.startswith("adm:wish:")]
    assert "adm:panel" in data_of(markup)


# --- other keyboards -----------------------------------------------------------------


def test_web_app_buttons_need_https() -> None:
    assert len(keyboards.more_menu(HTTPS).inline_keyboard) == 4
    assert len(keyboards.more_menu(HTTP).inline_keyboard) == 3, "no web_app button over plain http"
    assert keyboards.journal_app_button(HTTP) is None
    assert keyboards.calendar_button(HTTP) is None
    assert keyboards.journal_app_button(HTTPS) is not None


def test_report_buttons_offer_the_upgrade_and_the_cancellation() -> None:
    """A maximum never goes down (DOMAIN §2): with the star there, only «это не отчёт» is left."""
    markup = keyboards.report_buttons(3, StampLevel.MAX, report_id=42)
    assert data_of(markup) == ["notreport:42"]

    markup = keyboards.report_buttons(3, StampLevel.MIN, report_id=42)
    assert data_of(markup) == ["level:3:max", "notreport:42"]
    assert "максимум" in labels_of(markup)[0]


def test_task_buttons_carry_the_week_number() -> None:
    assert data_of(keyboards.task_buttons(7)) == ["intent:7:take", "intent:7:try", "intent:7:skip"]


def test_fact_choices_are_capped_and_keep_back() -> None:
    facts = [
        FactDTO(id=index, text="ф" * 100, author_id=None, week_id=None, created_at=datetime(2026, 9, 2))
        for index in range(60)
    ]
    markup = keyboards.fact_choices(facts)
    assert len([d for d in data_of(markup) if d.startswith("adm:delfact:")]) == 50, "Telegram will not take more"
    assert "adm:panel" in data_of(markup)
    assert all(len(label) <= 60 for label in labels_of(markup))


def test_achievement_choices_keep_back() -> None:
    catalogue = [
        AchievementTypeDTO(code="повар", emoji="🌮", name="Повар", description="приготовил", label="🌮 Повар"),
        AchievementTypeDTO(code="идея", emoji="💡", name="Штурман", description="придумал", label="💡 Штурман"),
    ]
    markup = keyboards.achievement_choices(1001, catalogue)
    assert data_of(markup) == ["adm:give:1001:повар", "adm:give:1001:идея", "adm:people:badge:0"]


def test_week_choices_mark_the_running_week() -> None:
    weeks = [
        WeekDTO(
            id=index,
            season_id=1,
            number=index,
            title=f"Неделя {index}",
            starts_on=date(2026, 8, 31) + (index - 1) * (date(2026, 9, 7) - date(2026, 8, 31)),
            ends_on=date(2026, 9, 6) + (index - 1) * (date(2026, 9, 7) - date(2026, 8, 31)),
            intro="",
            task_min="",
            task_max="",
            word="",
            word_ru="",
            word_meaning="",
        )
        for index in (1, 2)
    ]
    markup = keyboards.week_choices(weeks, today=date(2026, 9, 2))
    assert labels_of(markup)[0].startswith("▶ 1"), "the running week is marked"
    assert labels_of(markup)[1].startswith("🔒 2"), "a future week is still closed"
    assert "adm:panel" in data_of(markup)


def test_main_keyboard_shows_the_panel_only_to_mila() -> None:
    plain = [b.text for row in keyboards.main_keyboard(is_admin=False).keyboard for b in row]
    admin = [b.text for row in keyboards.main_keyboard(is_admin=True).keyboard for b in row]
    assert "⚙️ Мила" not in plain and "⚙️ Мила" in admin
    assert all(button_action(label) is not None for label in plain), "every visible button is recognised"


def test_facts_buttons_show_the_bin_only_to_mila_and_only_with_facts() -> None:
    assert data_of(keyboards.facts_buttons(is_admin=False, has_facts=True)) == ["addfact"]
    assert data_of(keyboards.facts_buttons(is_admin=True, has_facts=False)) == ["addfact"]
    assert data_of(keyboards.facts_buttons(is_admin=True, has_facts=True)) == ["addfact", "adm:delfact"]

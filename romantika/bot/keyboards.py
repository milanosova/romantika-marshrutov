"""Reply and inline keyboards, and the emoji-insensitive button detection (DOMAIN §7)."""

from __future__ import annotations

from datetime import date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from romantika.domain.types import StampLevel
from romantika.services.achievements import AchievementTypeDTO
from romantika.services.content import WeekDTO
from romantika.services.facts import FactDTO
from romantika.services.people import UserDTO
from romantika.texts import ru

# Button labels people see. Old labels stay recognised: the keyboard is cached on the
# client and is redrawn only on /start.
BUTTON_ACTIONS: dict[str, str] = {
    "задание": "task",
    "сегодня": "today",
    "паспорт": "passport",
    "мой паспорт": "passport",
    "словарь": "words",
    "что узнали": "facts",
    "ещё": "more",
    "помощь": "help",
    "написать миле": "write",
    "мила": "admin",
    "мой журнал": "journal",
}

PEOPLE_PAGE = 20


def normalize_button(text: str | None) -> str:
    """Keep letters, spaces and dashes: emoji and U+FE0F-style selectors are dropped."""
    kept = "".join(ch for ch in (text or "") if ch.isalpha() or ch.isspace() or ch == "-")
    return " ".join(kept.split()).lower()


def button_action(text: str | None) -> str | None:
    return BUTTON_ACTIONS.get(normalize_button(text))


def main_keyboard(*, is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📋 Задание"), KeyboardButton(text="🌤 Сегодня")],
        [KeyboardButton(text="📘 Паспорт"), KeyboardButton(text="📖 Словарь")],
        [KeyboardButton(text="💡 Что узнали"), KeyboardButton(text="⋯ Ещё")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="⚙️ Мила")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _web_app_button(text: str, url: str) -> InlineKeyboardButton | None:
    """Telegram accepts web_app buttons only over https; locally there is simply no button."""
    if not url.startswith("https://"):
        return None
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))


def more_menu(public_base_url: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="📔 Мой журнал", callback_data="more:journal")],
        [InlineKeyboardButton(text="✉️ Написать Миле", callback_data="more:write")],
        [InlineKeyboardButton(text="❔ Помощь", callback_data="more:help")],
    ]
    if button := _web_app_button("📱 Открыть приложение", f"{public_base_url}/app"):
        rows.insert(0, [button])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def task_buttons(week_number: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Берусь", callback_data=f"intent:{week_number}:take"),
                InlineKeyboardButton(text="Попробую", callback_data=f"intent:{week_number}:try"),
                InlineKeyboardButton(text="В этот раз мимо", callback_data=f"intent:{week_number}:skip"),
            ]
        ]
    )


def report_buttons(week_number: int, level: StampLevel, report_id: int) -> InlineKeyboardMarkup:
    """`level` is the week's stamp. A maximum never goes down (DOMAIN §2), so once the star
    is there the only correction left is «это не отчёт» — a «минимум» button would be dead."""
    rows: list[list[InlineKeyboardButton]] = []
    if level is not StampLevel.MAX:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⭐ Это был максимум", callback_data=f"level:{week_number}:{StampLevel.MAX.value}"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="✉️ Это не отчёт, а сообщение Миле", callback_data=f"notreport:{report_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def passport_buttons(public_base_url: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="📔 Что будет в конце сезона", callback_data="endofseason")]]
    if button := _web_app_button("📱 Паспорт в приложении", f"{public_base_url}/app/passport"):
        rows.append([button])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def journal_app_button(public_base_url: str) -> InlineKeyboardMarkup | None:
    button = _web_app_button("📱 Журнал в приложении", f"{public_base_url}/app/journal")
    return InlineKeyboardMarkup(inline_keyboard=[[button]]) if button else None


def journal_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📔 Посмотреть, как он выглядит сейчас", callback_data="journal:me")]
        ]
    )


def word_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="➕ Добавить своё слово", callback_data="addword")]]
    )


def facts_buttons(*, is_admin: bool, has_facts: bool) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text="➕ Добавить свой факт", callback_data="addfact")]
    if is_admin and has_facts:
        row.append(InlineKeyboardButton(text="🗑 Убрать", callback_data="adm:delfact"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def calendar_button(public_base_url: str) -> InlineKeyboardMarkup | None:
    button = _web_app_button("☀️ Узнать свой день", f"{public_base_url}/calendar")
    return InlineKeyboardMarkup(inline_keyboard=[[button]]) if button else None


# --- admin -------------------------------------------------------------------------------


def panel(*, reminders_enabled: bool, public_base_url: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="📝 Черновик Привала", callback_data="adm:draft"),
            InlineKeyboardButton(text="✏️ Задание недели", callback_data="adm:edit"),
        ],
        [
            InlineKeyboardButton(text="📊 Сводка недели", callback_data="adm:summary"),
            InlineKeyboardButton(text="🔑 Ядро", callback_data="adm:core"),
        ],
        [
            InlineKeyboardButton(text="🏅 Выдать ачивку", callback_data="adm:people:badge:0"),
            InlineKeyboardButton(text="❄️ Дать заморозку", callback_data="adm:people:freeze:0"),
        ],
        [InlineKeyboardButton(text="💌 Пожелание", callback_data="adm:people:wish:0")],
        [
            InlineKeyboardButton(text="➕ Записать факт", callback_data="addfact"),
            InlineKeyboardButton(text="🗑 Убрать факт", callback_data="adm:delfact"),
        ],
        [
            InlineKeyboardButton(text="⏰ Напомнить сейчас", callback_data="adm:remind"),
            InlineKeyboardButton(
                text="🔔 Автонапоминания: вкл" if reminders_enabled else "🔕 Автонапоминания: выкл",
                callback_data="adm:toggle",
            ),
        ],
        [InlineKeyboardButton(text="👥 Кто в боте", callback_data="adm:who")],
    ]
    if button := _web_app_button("🛠 Открыть админку", f"{public_base_url}/app/admin"):
        rows.append([button])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def people_list(people: list[UserDTO], *, prefix: str, page: int, exclude: int | None) -> InlineKeyboardMarkup:
    """People as buttons, `PEOPLE_PAGE` per page, «назад» always reachable."""
    candidates = [user for user in people if user.id != exclude]
    start = page * PEOPLE_PAGE
    chunk = candidates[start : start + PEOPLE_PAGE]
    rows = [
        [InlineKeyboardButton(text=ru.short_name(user)[:30], callback_data=f"adm:{prefix}:{user.id}")] for user in chunk
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="‹ раньше", callback_data=f"adm:people:{prefix}:{page - 1}"))
    if start + PEOPLE_PAGE < len(candidates):
        nav.append(InlineKeyboardButton(text="дальше ›", callback_data=f"adm:people:{prefix}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="‹ назад", callback_data="adm:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def freeze_reasons(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 За комментарий", callback_data=f"adm:frz:{user_id}:comment")],
            [InlineKeyboardButton(text="🤝 За приход на встречу", callback_data=f"adm:frz:{user_id}:meetup")],
            [InlineKeyboardButton(text="🧭 За приведённого друга", callback_data=f"adm:frz:{user_id}:friend")],
            [InlineKeyboardButton(text="‹ назад", callback_data="adm:people:freeze:0")],
        ]
    )


def achievement_choices(user_id: int, catalogue: list[AchievementTypeDTO]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{item.emoji} {item.name}", callback_data=f"adm:give:{user_id}:{item.code}")]
        for item in catalogue
    ]
    rows.append([InlineKeyboardButton(text="‹ назад", callback_data="adm:people:badge:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def week_choices(weeks: list[WeekDTO], *, today: date) -> InlineKeyboardMarkup:
    rows = []
    for week in weeks:
        prefix = "▶ " if week.starts_on <= today else "🔒 "
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix}{week.number} · {week.title}"[:60], callback_data=f"adm:week:{week.number}"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="‹ назад", callback_data="adm:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def field_choices(week_number: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"adm:field:{week_number}:{field}")]
        for field, label in ru.WEEK_FIELDS.items()
    ]
    rows.append([InlineKeyboardButton(text="‹ назад", callback_data="adm:edit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fact_choices(facts: list[FactDTO]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=fact.text[:60], callback_data=f"adm:delfact:{fact.id}")] for fact in facts[:50]]
    rows.append([InlineKeyboardButton(text="‹ назад", callback_data="adm:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

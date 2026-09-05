"""Russian texts of the bot, ported from the legacy bot word for word where behaviour is
unchanged (DOMAIN §7–§8). Formatting helpers take service DTOs and return HTML strings.

Nothing here touches the database or Telegram.
"""

from __future__ import annotations

from datetime import date
from html import escape

from romantika.domain.types import Level, StampLevel, WeekState
from romantika.domain.tzolkin import TzolkinDay
from romantika.services.achievements import AchievementTypeDTO
from romantika.services.content import SeasonDTO, WeekDTO
from romantika.services.facts import FactDTO
from romantika.services.journal import JournalView
from romantika.services.passport import PassportView
from romantika.services.people import UserDTO
from romantika.services.summary import CoreView, WeekSummary
from romantika.services.words import DictionaryView

RULE = "─" * 18

LEVEL_NAMES: dict[Level | None, str] = {
    Level.RESIDENT: "Резидент",
    Level.TRAVELER: "Путешественник",
    Level.TOURIST: "Турист",
    None: "Ещё в пути",
}
JOURNAL_LEVEL_NAMES: dict[Level | None, str] = LEVEL_NAMES

FREEZE_REASONS: dict[str, str] = {
    "word": "за своё слово в словарике",
    "max": "за первый выполненный максимум",
    "comment": "за комментарий в канале",
    "meetup": "за приход на встречу",
    "friend": "за приведённого друга",
    "manual": "от Милы",
}

WEEK_FIELDS: dict[str, str] = {
    "title": "Название",
    "intro": "Вступление",
    "task_min": "Минимум",
    "task_max": "Максимум",
    "word": "Слово",
    "word_ru": "Произношение",
    "word_meaning": "Значение слова",
}

MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]  # fmt: skip


def plural(n: int, one: str, two: str, many: str) -> str:
    """1 неделя · 2 недели · 5 недель"""
    n = abs(n)
    if 11 <= n % 100 <= 14:
        return many
    return {1: one, 2: two, 3: two, 4: two}.get(n % 10, many)


def display_name(user: UserDTO | None, fallback: int | None = None) -> str:
    """«Имя (@ник)» as the admin sees people; «без имени» when Telegram gave nothing."""
    if user is None:
        return f"id {fallback}" if fallback is not None else "без имени"
    name = " ".join(part for part in (user.first_name, user.last_name) if part) or "без имени"
    return f"{name} (@{user.username})" if user.username else name


def short_name(user: UserDTO | None, fallback: int | None = None) -> str:
    return display_name(user, fallback).split(" (@")[0]


def date_genitive(day: date) -> str:
    return f"{day.day} {MONTHS_GENITIVE[day.month - 1]}"


# --- greeting, help, admin ------------------------------------------------------------


WEEKDAYS_NOMINATIVE = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WEEKDAYS_ACCUSATIVE = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]
WEEKDAYS_GENITIVE = ["понедельника", "вторника", "среды", "четверга", "пятницы", "субботы", "воскресенья"]


def deadline_text(week: WeekDTO) -> str:
    """«воскресенье 06.09, 18:00» — the last day of the week by name, whatever day it falls on:
    the closing week of a season may end on a Wednesday (DOMAIN §1)."""
    return f"{WEEKDAYS_NOMINATIVE[week.ends_on.weekday()]} {week.ends_on:%d.%m}, 18:00"


def deadline_short(week: WeekDTO) -> str:
    """«до воскресенья, 18:00» — for a status line where the date would be noise."""
    return f"до {WEEKDAYS_GENITIVE[week.ends_on.weekday()]}, 18:00"


def week_end_accusative(week: WeekDTO) -> str:
    """«в воскресенье» / «в среду» — when the week's results are shown."""
    return WEEKDAYS_ACCUSATIVE[week.ends_on.weekday()]


def greeting(season: SeasonDTO | None, *, app: bool = False) -> str:
    """«О клубе»: in the bot the report is sent right here, in the app — on the «Сегодня» tab."""
    current = escape(season.title) if season else "пока не выбрана"
    how = (
        "Чтобы сдать — отправь текст или фото во вкладке «Сегодня». Больше ничего нажимать не надо."
        if app
        else "Чтобы сдать — просто пришли сюда текст или фото. Больше нажимать ничего не надо."
    )
    return (
        "Это бот клуба <b>«Романтика маршрутов»</b>.\n\n"
        "Раз в три месяца рандомайзер выбирает страну, и мы разбираем её "
        f"до мелочей. Сейчас — <b>{current}</b>.\n\n"
        "Каждый понедельник тут появляется задание: минимум на пять минут "
        f"и максимум на вечер. Оба необязательные.\n\n{how}"
    )


GREETING_CTA = "\n\nНачни с «📋 Задание» 👇"
"""The bot appends this to the greeting; the Mini App shows the greeting without it."""


#: (heading, answer in the bot, answer in the Mini App or None when it is the same).
#: The two surfaces have different buttons, so the answers that point at a button differ.
_HELP_ITEMS: tuple[tuple[str, str, str | None], ...] = (
    (
        "Записалось не то, что нужно",
        "Текст засчитывается как минимум, фото — как максимум. Если это был максимум, "
        "под ответом бота есть кнопка «⭐ Это был максимум». Текст и фото отчёта можно "
        "поправить в приложении, пока неделя идёт.",
        "Текст засчитывается как минимум, фото — как максимум. Если это был максимум, "
        "нажми «⭐ Это был максимум» под ответом. Текст и фото отчёта можно поправить "
        "сразу после отправки или потом в «Журнале», пока неделя идёт.",
    ),
    (
        "Хочу дослать фото или переделать",
        "Просто пришли ещё раз: повторный отчёт штамп не понижает, а фото поднимет его до "
        "максимума. Другое дело — правка уже присланного отчёта в приложении: если убрать из "
        "него все фото, штамп пересчитается по тому, что осталось.",
        "Просто отправь ещё один отчёт: повторный штамп не понижает, а фото поднимет его до "
        "максимума. Другое дело — правка уже присланного: если убрать из него все фото, штамп "
        "пересчитается по тому, что осталось.",
    ),
    (
        "Пропуск недели",
        "Ничего страшного. На сезон есть <b>две заморозки</b>: пропущенная неделя "
        "тратит одну, но цепочка не рвётся и ты остаёшься в строю. Тратятся сами, "
        "просить не надо. Когда кончатся — участие всё равно продолжается: "
        "это клуб, а не школа.",
        None,
    ),
    (
        "Как заработать ещё заморозку",
        "Всего можно накопить пять. Сверх двух базовых:\n"
        "· +1 за своё слово в словарике — сразу, автоматически\n"
        "· +1 за первый выполненный максимум — тоже автоматически\n"
        "· +1 от меня за комментарий в канале, приход на встречу или приведённого друга",
        None,
    ),
    (
        "Старт в середине сезона",
        "Заходи с любой недели, догонять с начала не нужно. Ничей результат не считается поздним.",
        None,
    ),
    ("Не знаю, что написать", "Минимум — это правда одно слово. «Чимичанга» — уже полноценный отчёт.", None),
    (
        "Не хочу писать в комментариях в канале",
        "И не надо. Присылай сюда — я всё увижу. В воскресном посте назову тебя "
        "по имени, а если не хочешь и этого — скажи, не буду.",
        None,
    ),
    (
        "Много уведомлений",
        "Напоминания приходят только тем, кто нажал «Берусь» или «Попробую», "
        "и не чаще двух раз за неделю. Нажми «В этот раз мимо» — не придёт ничего.",
        None,
    ),
    (
        "Пропали кнопки или не видно задание",
        "Напиши /start — клавиатура перерисуется.",
        "Закрой приложение и открой снова. Если кнопки пропали в чате с ботом — напиши там /start.",
    ),
    (
        "Хочу добавить своё слово в словарик",
        "Открой «📖 Словарь» — там есть кнопка «➕ Добавить своё слово».",
        "Открой «Словарь» — форма «Записать» наверху.",
    ),
    (
        "Заморозку не дали",
        "Скорее всего я просто не заметила — комментарии и встречи бот "
        "не видит. Жми «✉️ Написать Миле» и скажи, я поставлю.",
        "Скорее всего я просто не заметила — комментарии и встречи бот "
        "не видит. Напиши мне ниже, в «Написать Миле», я поставлю.",
    ),
    (
        "Что-то другое",
        "Жми «✉️ Написать Миле» — это обычное сообщение, не отчёт по заданию. Я прочитаю и отвечу. Это я, а не робот.",
        "Напиши мне в «Написать Миле» ниже — это обычное сообщение, не отчёт по заданию. "
        "Я прочитаю и отвечу. Это я, а не робот.",
    ),
)


def help_text(*, app: bool = False) -> str:
    """The FAQ, phrased for the bot's buttons or for the Mini App's screens."""
    items = [f"<b>{heading}</b>\n{(in_app if app and in_app else in_bot)}" for heading, in_bot, in_app in _HELP_ITEMS]
    return "<b>Если что-то пошло не так</b>\n\n" + "\n\n".join(items)


HELP = help_text()

PANEL = (
    "<b>⚙️ Панель</b>\n\n"
    "Всё, что тебе нужно по ходу недели. Команды со слешем тоже работают, "
    "но помнить их не обязательно."
)

ADMIN_MEMO = (
    "<b>Что бот умеет — коротко</b>\n\n"
    "<b>Что видят люди</b>\n"
    "📋 Задание — задание недели, кнопки «Берусь · Попробую · Мимо»\n"
    "💡 Что узнали — факты сезона\n"
    "📘 Паспорт — штампы, закрытые недели, ачивки, статус\n"
    "📖 Словарь — слова недели + добавленные людьми\n"
    "Отчёт — просто прислать текст или фото. Текст = минимум, "
    "фото = максимум со звёздочкой\n"
    "/журнал — свой журнал сезона, каким он собран на сегодня\n\n"
    "<b>Смотреть</b>\n"
    "📝 Черновик Привала — готовый текст воскресного поста\n"
    "/results — сводка за неделю\n"
    "/results 2 — то же по любой неделе\n"
    "/core — ядро: кто сдаёт две недели подряд. Главная цифра\n"
    "/who — все, кто писал боту\n"
    "/журнал @ksu — чужой журнал\n\n"
    "<b>Делать</b>\n"
    "/факт текст — записать факт после поста\n"
    "/факт- номер — убрать факт · /факты — список\n"
    "/ачивка повар — ответом на отчёт · /ачивка @ksu повар — по нику\n"
    "/badges — список ачивок\n"
    "/пожелание @ksu текст — в журнал, к концу сезона\n"
    "/remind — напомнить тем, кто взялся и молчит\n"
    "/reminders — включить или выключить автонапоминания\n\n"
    "<b>Ответить человеку</b>\n"
    "Ответь реплаем на присланный отчёт — бот передаст автору.\n\n"
    "<b>Само по себе</b>\n"
    "Четверг 19:00 и воскресенье 12:00 — напоминания тем, кто взялся "
    "и не прислал. Понедельник — открывается очередная неделя в паспорте."
)

WHOAMI = "Твой id: <code>{user_id}</code>\nПоложи его в <code>ADMIN_IDS</code>, чтобы получить админские команды."
UNKNOWN_COMMAND = "Такой команды нет. Жми кнопки внизу 👇"
NOT_ADMIN = "Это команда Милы. Тебе — кнопки внизу 👇"
NO_WEEK_TASK = "Сейчас неделя сезона не идёт. Ближайшее задание — в понедельник."
NO_SEASON = "Сезон ещё не начался. Как только рандомайзер выберет страну — здесь появится задание."
MORE_MENU = "Что открыть:"
WRITE_PROMPT = (
    "Пиши. Я прочитаю и отвечу — это не отчёт по заданию, а обычное сообщение.\n\n"
    "<i>Если за тобой заморозка — комментарий в канале, приведённый друг, "
    "встреча — тоже напиши сюда. Я могла не заметить.</i>"
)
LETTER_SENT = "Передала ✅ Отвечу, как увижу."
WORD_PROMPT = (
    "Напиши слово и что оно значит, одним сообщением.\n"
    "Например: <i>sobremesa — время за столом уже после еды, когда все сидят и разговаривают</i>"
)
WORD_SAVED = (
    "Записала в общий словарик 📖\n\nК концу сезона соберём из них словарь — твоё слово будет там с твоим именем."
)
WORD_FREEZE_BONUS = (
    "\n\n❄️ И тебе +1 заморозка — это право пропустить неделю так, чтобы цепочка не порвалась. Смотри «📘 Паспорт»."
)
FACT_PROMPT = (
    "Что записать? Пиши одним сообщением.\n\n"
    "<i>Например: Ацтеки называли себя мешика — отсюда «Мексика». Это попадёт в общий список "
    "и в журнал сезона, с твоим именем.</i>"
)
FACT_SAVED = "Спасибо, записала ✅ Твой факт теперь в общем списке, с твоим именем — и попадёт в журнал сезона."
NOT_UNDERSTOOD = (
    "Не поняла 🙈 Отчёт — это текст, фото, видео, кружок, голосовое или файл. "
    "Пришли что-то из этого, и я поставлю штамп."
)
OUT_OF_WEEK = "Спасибо! Сейчас неделя сезона не идёт, так что штамп не ставлю — но сообщение сохранила и прочитаю."
JOURNAL_NOW = "Так он выглядит сейчас. К {end} здесь будет весь сезон."
NOT_REPORT_DONE = "Поняла, это не отчёт — штамп пересчитала. Сохранила как обычное сообщение, прочитаю."
NOT_REPORT_FOREIGN = "Этот отчёт не твой, ничего не трогаю."
NOT_REPORT_ALREADY = "Этот отчёт уже отменён — всё в порядке."
EDIT_WEEK_OVER = "Эта неделя уже закрыта — отчёт остаётся как есть. Дописать можно, пока неделя идёт."
WEEK_ALREADY_OVER = "Эта неделя уже закончилась — задним числом её не меняем. Правка не сохранена."
REPLY_DELIVERED = "Отправила ✅"
REPLY_FAILED = "Не дошло — человек, видимо, заблокировал бота"


# --- participant screens -----------------------------------------------------------


def word_lines(week: WeekDTO | None) -> list[str]:
    """Word, pronunciation and meaning — the parts that are filled in."""
    if week is None or not week.word:
        return []
    head = escape(week.word)
    if week.word_ru:
        head += " · " + escape(week.word_ru)
    lines = [head]
    if week.word_meaning:
        lines.append("<i>" + escape(week.word_meaning) + "</i>")
    return lines


def task_text(week: WeekDTO) -> str:
    parts = [
        f"<b>Неделя {week.number} · {escape(week.title)}</b>",
        "",
        escape(week.intro),
        "",
        RULE,
        "",
        "<b>Минимум</b>",
        escape(week.task_min),
    ]
    if week.task_max:
        parts += ["", "<b>Максимум</b>", escape(week.task_max)]
    parts += [
        "",
        RULE,
        "",
        f"Дедлайн — {deadline_text(week)}.",
        "Пришли сюда текст или фото — и это засчитается.",
    ]
    if week.word:
        parts += ["", "<b>Слово недели</b>", *word_lines(week)]
    return "\n".join(parts)


def today_text(
    today: date,
    *,
    tzolkin: TzolkinDay | None,
    word_week: WeekDTO | None,
    memory_week: WeekDTO | None,
    note: str,
) -> str:
    lines = [f"<b>🌤 {today:%d.%m}</b>", ""]
    if tzolkin is not None:
        lines += [
            f"<b>{tzolkin.number} {escape(tzolkin.sign.name)}</b> · {escape(tzolkin.sign.symbol)}",
            f"<i>{escape(tzolkin.sign.day_advice)}</i>",
            "",
        ]
    if word_week is not None and word_week.word:
        lines += [RULE, "", "<b>Слово недели</b>", *word_lines(word_week), ""]
    if memory_week is not None and memory_week.word:
        lines += ["<b>А помнишь?</b>", *word_lines(memory_week), ""]
    lines += [RULE, ""]
    if note:
        lines += [f"<i>{escape(note)}</i>", ""]
    lines.append("Задание недели — в «📋 Задание».")
    return "\n".join(lines)


def passport_text(view: PassportView, bonus_reasons: list[str]) -> str:
    season = view.season
    lines = [f"<b>📘 Паспорт сезона · {escape(season.title)}</b>", ""]
    marks = {
        WeekState.FROZEN: "❄️",
        WeekState.MISSED: "◦",
        WeekState.CURRENT: "▸",
        WeekState.BEFORE_JOIN: "◦",
    }
    locked = 0
    for week in view.weeks:
        state = view.breakdown.states.get(week.number, WeekState.LOCKED)
        if state is WeekState.LOCKED:
            locked += 1
            continue
        if state is WeekState.STAMPED:
            level = view.stamps.get(week.number)
            mark = "⭐" if level is StampLevel.MAX else "✅"
            title = view.stamp_titles.get(week.number) or week.title
            lines.append(f"{mark}  {week.number}. {escape(title)}")
            continue
        tail = {WeekState.FROZEN: " · заморозка", WeekState.BEFORE_JOIN: " · до тебя"}.get(state, "")
        lines.append(f"{marks[state]}  {week.number}. {escape(week.title)}{tail}")
    if locked:
        lines.append(
            f"🔒  Дальше ещё {locked} {plural(locked, 'неделя', 'недели', 'недель')}"
            " — открываются по понедельникам, по одной за раз"
        )

    if view.achievements:
        lines += ["", "<b>Ачивки</b>", *(escape(label) for label in view.achievements)]

    breakdown = view.breakdown
    stars = f" · со звёздочкой: {view.stamps_max}" if view.stamps_max else ""
    lines += [
        "",
        RULE,
        "",
        f"Штампов: <b>{breakdown.stamps}</b> из {view.weeks_total}{stars}",
        f"Статус: <b>{LEVEL_NAMES[view.level]}</b>",
        f"Заморозок осталось: {breakdown.freezes_left} из {breakdown.freezes_total}",
    ]
    if bonus_reasons:
        joined = ", ".join(FREEZE_REASONS.get(reason, reason) for reason in bonus_reasons)
        lines.append(f"<i>Из них заработано: {len(bonus_reasons)} — {escape(joined)}</i>")
    if breakdown.freezes_used:
        lines.append("<i>❄️ — пропущенная неделя, закрытая заморозкой. Цепочка от этого не рвётся.</i>")
    if breakdown.freezes_left == 0:
        lines.append(
            "<i>Заморозки кончились. Статус «Резидент» больше недоступен, но участие продолжается — это главное.</i>"
        )
    return "\n".join(lines)


def end_of_season_text(season: SeasonDTO) -> str:
    return (
        "<b>Что будет в конце</b>\n\n"
        f"{date_genitive(season.ends_on)} сезон заканчивается, и каждый, кто участвовал, получит "
        "<b>журнал сезона</b> — свой собственный, не общий.\n\n"
        "Внутри будет:\n"
        "· твои недели и что в них было — твоими же словами\n"
        "· фотографии из твоих отчётов\n"
        "· ачивки, которые у тебя набрались\n"
        "· словарик: слова сезона и слова участников\n"
        "· несколько слов лично от меня\n\n"
        "Это не сертификат об окончании. Это чтобы в ноябре было видно: "
        "три месяца прожиты, а не пролистаны."
    )


def dictionary_text(season: SeasonDTO, view: DictionaryView, names: dict[int, str]) -> str:
    lines = [f"<b>📖 Словарик сезона · {escape(season.title)}</b>", ""]
    if view.week_words:
        for item in view.week_words:
            head = f"<b>{escape(item.word)}" + (f" · {escape(item.word_ru)}" if item.word_ru else "") + "</b>"
            lines.append(head)
            if item.meaning:
                lines.append(f"<i>{escape(item.meaning)}</i>")
            lines.append("")
        lines.pop()
    else:
        lines.append("Слова недели появятся вместе с заданиями.")
    if view.user_words:
        lines += ["", RULE, "", "<b>Слова участников</b>", ""]
        for entry in view.user_words:
            text = escape(entry.word) + (f" — {escape(entry.meaning)}" if entry.meaning else "")
            lines.append(f"{text} <i>— {escape(names.get(entry.user_id, str(entry.user_id)))}</i>")
    lines += ["", RULE, "", "<i>К концу сезона соберём из них общий словарь.</i>"]
    return "\n".join(lines)


def facts_text(season: SeasonDTO, facts: list[FactDTO], names: dict[int, str], *, with_ids: bool = False) -> str:
    about = escape(season.title_accusative or season.title)
    if not facts:
        return (
            f"<b>💡 Что мы узнали про {about}</b>\n\n"
            "Пока пусто. Жми «➕ Добавить свой факт» — что зацепило из постов или нашлось само."
        )
    lines = [f"<b>💡 Что мы узнали про {about}</b>", f"<i>Собрано вместе: {len(facts)}</i>", "", RULE, ""]
    for index, fact in enumerate(facts, 1):
        number = f"<code>{fact.id}</code>" if with_ids else f"<b>{index}.</b>"
        line = f"{number} {escape(fact.text)}"
        if fact.author_id is not None:
            line += f" <i>— {escape(names.get(fact.author_id, str(fact.author_id)))}</i>"
        lines += [line, ""]
    text = "\n".join(lines).rstrip()
    return text + "\n\n<i>Пополняется после каждого поста. В конце сезона всё это будет в твоём журнале.</i>"


def journal_text(view: JournalView, level: Level | None) -> str:
    season = view.season
    name = short_name(view.user)
    lines = [
        f"📔 <b>Журнал сезона · {escape(season.title)}</b>",
        f"{escape(name)} · {season.starts_on:%d.%m} — {season.ends_on:%d.%m.%Y}",
        "",
        f"Пройдено <b>{len(view.weeks)}</b> {plural(len(view.weeks), 'неделя', 'недели', 'недель')} "
        f"из {view.weeks_total}. Статус: <b>{JOURNAL_LEVEL_NAMES[level]}</b>.",
    ]
    if view.weeks:
        lines += ["", "───", "<b>Твои недели</b>"]
        for week in view.weeks:
            mark = "⭐" if week.level is StampLevel.MAX else "✅"
            lines.append(f"{mark} <b>Неделя {week.number} · {escape(week.title)}</b>")
            if week.quote:
                lines.append(f"<i>«{escape(week.quote[:400])}»</i>")
    else:
        lines += ["", "<i>Пока пусто — здесь появятся твои недели и твои же слова о них.</i>"]
    if view.achievements:
        lines += ["", "───", "<b>Твои ачивки</b>", *(escape(a) for a in view.achievements)]
    if view.words:
        lines += ["", "───", "<b>Твои слова</b>"]
        lines += ["· " + escape(w.word) + (f" — {escape(w.meaning)}" if w.meaning else "") for w in view.words]
    if view.season_words:
        lines += ["", "───", "<b>Словарик сезона</b>"]
        lines += [f"<b>{escape(w.word)}</b> — {escape(w.meaning)}" for w in view.season_words]
    if view.facts:
        about = escape(season.title_accusative or season.title)
        lines += ["", "───", f"<b>Что мы узнали про {about}</b>"]
        lines += [f"<b>{i}.</b> {escape(f.text)}" for i, f in enumerate(view.facts, 1)]
    if view.wish:
        lines += ["", "───", "<b>От Милы</b>", f"<i>{escape(view.wish)}</i>"]
    return "\n".join(lines)


# --- report replies ------------------------------------------------------------------


def report_reply(
    week: WeekDTO, level: StampLevel, *, stamp_level: StampLevel | None = None, freeze_granted: bool
) -> str:
    """The receipt for one report: what it counted as and what the week's stamp is now.

    `level` is this report's own level, `stamp_level` the week's stamp after it. A stamp never
    goes down (DOMAIN §2), so a text sent after a photo is a minimum while the star stays —
    the receipt has to say both, or it reads as «the star is gone».
    """
    title = escape(week.title)
    if level is StampLevel.MAX:
        text = f"⭐ Записала как <b>максимум</b> — штамп со звёздочкой за неделю «{title}»."
    elif stamp_level is StampLevel.MAX:
        text = f"✅ Записала как <b>минимум</b>. Звёздочка за неделю «{title}» у тебя уже есть — она остаётся."
    else:
        text = f"✅ Записала как <b>минимум</b> — штамп за неделю «{title}»."
    if freeze_granted:
        text += (
            "\n\n❄️ И тебе +1 заморозка за первый максимум — это право пропустить неделю так, "
            "чтобы цепочка не порвалась."
        )
    return text + f"\n\n{week_end_accusative(week).capitalize()} покажу общие итоги."


def level_name(level: StampLevel) -> str:
    return "максимум" if level is StampLevel.MAX else "минимум"


INTENT_HINTS = {
    "take": "Записала: берёшься 💪\n\nКак сделаешь — пришли сюда текст или фото, и я поставлю штамп в паспорт.",
    "try": "Записала: попробуешь.\n\nДаже минимум на пять минут считается — это полноценный штамп.",
    "skip": "Хорошо, неделя может не задаться.\n\nНапоминаний не пришлю. Реакция под постом — тоже участие.",
}
INTENT_NAMES = {"take": "берусь", "try": "попробую", "skip": "мимо"}


#: How much of a report's text Mila's copy carries (Telegram's cap is 4096 for the whole message).
ADMIN_COPY_CHARS = 3500


def admin_report_header(week_number: int, author: str, text: str | None, kind: str) -> str:
    body = f": {escape(clip(text, ADMIN_COPY_CHARS))}" if text else f" ({kind})"
    return (
        f"📨 Отчёт за неделю {week_number} от {escape(author)}{body}"
        "\n\n<i>Ответь на это сообщение — я передам ответ автору.</i>"
    )


def admin_edit_header(week_number: int, author: str, text: str | None, *, added: int, removed: int) -> str:
    """Mila's copy of an edited report: what the text is now and what happened to the files."""
    changes = []
    if added:
        changes.append(f"+{added} {plural(added, 'файл', 'файла', 'файлов')}")
    if removed:
        changes.append(f"−{removed} {plural(removed, 'файл', 'файла', 'файлов')}")
    body = f": {escape(clip(text, ADMIN_COPY_CHARS))}" if text else ""
    tail = f" ({', '.join(changes)})" if changes else ""
    return (
        f"✏️ Правка отчёта за неделю {week_number} от {escape(author)}{tail}{body}"
        "\n\n<i>Ответь на это сообщение — я передам ответ автору.</i>"
    )


def edit_reply(week: WeekDTO, level: StampLevel | None, *, freeze_granted: bool) -> str:
    """The receipt after an edit in the Mini App; names the stamp the week actually has."""
    if level is StampLevel.MAX:
        text = f"✏️ Отчёт за неделю «{escape(week.title)}» обновила — штамп со звёздочкой ⭐ на месте."
    elif level is StampLevel.MIN:
        text = f"✏️ Отчёт за неделю «{escape(week.title)}» обновила — засчитан как <b>минимум</b> ✅."
    else:
        text = f"✏️ Отчёт за неделю «{escape(week.title)}» обновила."
    if freeze_granted:
        text += "\n\n❄️ И тебе +1 заморозка за первый максимум."
    return text


def admin_letter_header(author: str, text: str | None, *, corrected: bool = False) -> str:
    suffix = " (сначала пришло как отчёт)" if corrected else ""
    return (
        f"✉️ <b>Сообщение от {escape(author)}</b>{suffix}\n\n{escape(text or '(без текста)')}"
        "\n\n<i>Ответь реплаем — передам.</i>"
    )


def admin_word_added(author: str, text: str, week_number: int | None = None) -> str:
    where = f" · неделя {week_number}" if week_number else ""
    return f"📖 Новое слово от {escape(author)}{where}: {escape(text)}"


def admin_fact_added(author: str, text: str, week_number: int | None = None) -> str:
    where = f" · неделя {week_number}" if week_number else ""
    return f"💡 Новый факт от {escape(author)}{where}: {escape(text)}"


def admin_out_of_week_header(author: str, text: str | None, kind: str) -> str:
    body = escape(text) if text else f"({kind})"
    return f"✉️ <b>Сообщение от {escape(author)}</b> (неделя не идёт)\n\n{body}\n\n<i>Ответь реплаем — передам.</i>"


def reply_to_author(text: str, *, about: str = "report") -> str:
    """Mila's answer as the participant sees it: to a report, to a letter, or out of the blue."""
    head = {
        "report": "Мила ответила на твой отчёт:",
        "letter": "Мила ответила на твоё сообщение:",
        "message": "Сообщение от Милы:",
    }[about]
    return f"💬 <b>{head}</b>\n\n{escape(text)}"


def clip(text: str, limit: int) -> str:
    """Cut with an ellipsis at a word, so the reader sees there was more."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0] if " " in text[: limit - 1] else text[: limit - 1]
    return cut.rstrip(" ,;:—-") + "…"


# --- admin screens -------------------------------------------------------------------


def summary_text(summary: WeekSummary, names: dict[int, str], core: CoreView) -> str:
    lines = [
        f"<b>Неделя {summary.week_number} · {escape(summary.week_title)}</b>",
        f"В боте людей: {summary.members_total} · отчётов: {summary.reports_total}",
        "",
        f"<b>Взялись ({len(summary.took)})</b>",
    ]
    by_name = lambda u: names.get(u, str(u)).lower()  # noqa: E731
    lines += [f"· {escape(names.get(u, str(u)))}" for u in sorted(summary.took, key=by_name)] or ["· пока никто"]
    lines += ["", f"<b>Сдали ({len(summary.submitted)})</b>"]
    lines += [
        ("⭐ " if level is StampLevel.MAX else "✅ ") + escape(names.get(u, str(u)))
        for u, level in sorted(summary.submitted.items(), key=lambda item: by_name(item[0]))
    ] or ["· пока никто"]
    if summary.took_not_submitted:
        lines += ["", f"<b>Взялись, но не прислали ({len(summary.took_not_submitted)})</b>"]
        lines += [f"· {escape(names.get(u, str(u)))}" for u in sorted(summary.took_not_submitted, key=by_name)]
        lines += ["", "Разослать им напоминание: /remind"]
    lines += ["", f"<b>Сдали две недели подряд: {len(core.best)}</b>"]
    lines += [f"· {escape(names.get(u, str(u)))}" for u in core.best] or ["· пока никто · подробнее: /core"]
    if summary.submitted:
        joined = ", ".join(names.get(u, str(u)).split(" (@")[0] for u in summary.submitted)
        lines += [
            "",
            "───",
            "<b>Кусок для воскресного поста:</b>",
            f"На этой неделе задание сделали: {escape(joined)}.",
        ]
    return "\n".join(lines)


def core_text(core: CoreView, names: dict[int, str], streaks: dict[int, tuple[int, int]]) -> str:
    lines = [
        "<b>🔑 Ядро сезона</b>",
        "Те, кто сдал две недели подряд. Главная цифра сезона: по ней решается всё остальное.",
        "",
        f"<b>По лучшей цепочке: {len(core.best)}</b>",
    ]
    lines += [f"· {escape(names.get(u, str(u)))} — {streaks.get(u, (0, 0))[0]} нед." for u in core.best] or [
        "· пока никто"
    ]
    lines += ["", f"<b>В строю сейчас: {len(core.current)}</b>"]
    lines += [f"· {escape(names.get(u, str(u)))} — {streaks.get(u, (0, 0))[1]} нед." for u in core.current] or [
        "· пока никто"
    ]
    lines += ["", "<i>Ориентиры: пятеро к 18.09, пятнадцать — порог платного клуба.</i>"]
    return "\n".join(lines)


def badges_text(catalogue: list[AchievementTypeDTO]) -> str:
    lines = ["<b>Ачивки сезона</b>", "Выдаются руками, за поступок, а не за посещаемость.", ""]
    for item in catalogue:
        lines.append(
            f"{item.emoji} <b>{escape(item.name)}</b> — {escape(item.description)}\n<code>{escape(item.code)}</code>"
        )
    lines += [
        "",
        "<b>Как выдать</b>",
        "· кнопка «🏅 Выдать ачивку» в панели",
        "· ответом на присланный отчёт: <code>/ачивка повар</code>",
        "· по нику: <code>/ачивка @ksu повар</code>",
    ]
    return "\n".join(lines)


def who_text(people: list[UserDTO]) -> str:
    lines = [f"<b>В боте {len(people)} человек</b>", ""]
    lines += [f"· {escape(display_name(user))}" for user in people]
    return "\n".join(lines)


def reminders_toggled(enabled: bool) -> str:
    state = "вкл" if enabled else "выкл"
    return (
        f"Автонапоминания <b>{state}</b>.\n\n"
        "Когда включены: <b>четверг 19:00</b> («впереди выходные») и "
        "<b>воскресенье 12:00</b> («сегодня до 18:00»).\n"
        "Уходят только тем, кто нажал «Берусь» или «Попробую» и ещё не прислал. "
        "Нажавшим «В этот раз мимо» — ничего.\n\n"
        "Повторить эту команду — переключить обратно."
    )


def achievement_given(label: str) -> str:
    return (
        f"🏅 <b>Тебе ачивка: {escape(label)}</b>\n\n"
        "Не за посещаемость, а за поступок. Она останется в паспорте сезона и попадёт в итоговый журнал."
    )


def freeze_given(reason: str) -> str:
    return (
        f"❄️ <b>Тебе +1 заморозка</b> — {FREEZE_REASONS.get(reason, reason)}.\n\n"
        "Это право пропустить неделю так, чтобы цепочка не порвалась. Смотри «📘 Паспорт»."
    )


def reminder_thursday(week: WeekDTO) -> str:
    return (
        f"Впереди выходные — как раз время сделать задание недели «{escape(week.title)}».\n\n"
        f"Дедлайн — {deadline_text(week)}. Пришли сюда текст или фото, и всё."
    )


def reminder_sunday(week: WeekDTO) -> str:
    return (
        f"Сегодня до 18:00 — дедлайн по заданию «{escape(week.title)}».\n\n"
        "Даже минимум на пять минут считается. Вечером покажу общие итоги."
    )

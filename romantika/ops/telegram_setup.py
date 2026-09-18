"""`python -m romantika.ops.telegram_setup` — configure the bot through the Bot API.

Sets the display name, descriptions, the command list and the menu button that opens the
participant Mini App. Idempotent; run after creating a bot in BotFather or after changing
`PUBLIC_BASE_URL`. Everything BotFather asks for interactively lives here instead.
"""

from __future__ import annotations

import asyncio

from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from romantika.bot.factory import make_bot
from romantika.bot.keyboards import app_page_url
from romantika.config import get_settings
from romantika.texts import ru

NAME = "Романтика маршрутов"
SHORT_DESCRIPTION = "Бот клуба «Романтика маршрутов»: задания недели, паспорт со штампами, журнал сезона."
DESCRIPTION = (
    "Раз в три месяца рандомайзер выбирает страну, и мы разбираем её до мелочей.\n\n"
    "Каждый понедельник здесь появляется задание: минимум на пять минут и максимум на вечер. "
    "Пришли текст или фото — и в паспорте сезона будет штамп."
)
# Two commands in the menu (DOMAIN §7, 14.09.2026): everything else lives in the app behind
# the one keyboard button. The old commands keep answering, they are just not advertised.
COMMANDS = [
    ("start", "Начать"),
    ("help", "Если что-то пошло не так"),
]


async def run() -> None:
    settings = get_settings()
    bot = make_bot(settings)
    try:
        await bot.set_my_name(NAME)
        await bot.set_my_short_description(SHORT_DESCRIPTION)
        await bot.set_my_description(DESCRIPTION)
        await bot.set_my_commands([BotCommand(command=command, description=text) for command, text in COMMANDS])
        app_url = app_page_url(settings.public_base_url)
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text=ru.OPEN_CLUB, web_app=WebAppInfo(url=app_url)))
        me = await bot.get_me()
        print(f"configured @{me.username} ({me.first_name}); menu button → {app_url}")
    finally:
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()

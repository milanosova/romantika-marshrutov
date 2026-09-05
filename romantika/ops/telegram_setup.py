"""`python -m romantika.ops.telegram_setup` — configure the bot through the Bot API.

Sets the display name, descriptions, the command list and the menu button that opens the
participant Mini App. Idempotent; run after creating a bot in BotFather or after changing
`PUBLIC_BASE_URL`. Everything BotFather asks for interactively lives here instead.
"""

from __future__ import annotations

import asyncio

from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from romantika.bot.factory import make_bot
from romantika.config import get_settings

NAME = "Романтика маршрутов"
SHORT_DESCRIPTION = "Бот клуба «Романтика маршрутов»: задания недели, паспорт со штампами, журнал сезона."
DESCRIPTION = (
    "Раз в три месяца рандомайзер выбирает страну, и мы разбираем её до мелочей.\n\n"
    "Каждый понедельник здесь появляется задание: минимум на пять минут и максимум на вечер. "
    "Пришли текст или фото — и в паспорте сезона будет штамп."
)
COMMANDS = [
    ("start", "Начать"),
    ("task", "Задание недели"),
    ("today", "Сегодня: день и слово"),
    ("passport", "Мой паспорт"),
    ("words", "Словарь сезона"),
    ("facts", "Что мы узнали"),
    ("journal", "Мой журнал"),
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
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Открыть", web_app=WebAppInfo(url=f"{settings.public_base_url}/app"))
        )
        me = await bot.get_me()
        print(f"configured @{me.username} ({me.first_name}); menu button → {settings.public_base_url}/app")
    finally:
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()

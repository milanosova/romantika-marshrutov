"""Mila replies to a forwarded report or letter → the answer goes to its author."""

from __future__ import annotations

from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.bot.send import safe_send
from romantika.services import letters, links, people
from romantika.texts import ru


async def relay_reply(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    is_admin: bool,
    now: datetime,
) -> None:
    from aiogram.dispatcher.event.bases import SkipHandler

    replied = message.reply_to_message
    if not is_admin or replied is None or not message.text:
        raise SkipHandler
    link = await links.lookup(session, admin_chat_id=message.chat.id, admin_message_id=replied.message_id)
    if link is None:
        raise SkipHandler
    about = "letter" if link.letter_id is not None else "report"
    delivered = await safe_send(bot, link.user_id, ru.reply_to_author(message.text, about=about))
    if delivered is not None and link.letter_id is not None:
        # The admin chat may be a group; only a person known to the bot fits `replied_by`.
        author = message.from_user
        known = await people.get_user(session, author.id) if author is not None else None
        await letters.mark_replied(
            session, link.letter_id, reply_text=message.text, replied_by=known.id if known else None, now=now
        )
    await safe_send(bot, message.chat.id, ru.REPLY_DELIVERED if delivered else ru.REPLY_FAILED)


def build() -> Router:
    router = Router(name="admin_reply")
    router.message.register(relay_reply, F.reply_to_message, F.text, ~F.text.startswith("/"))
    return router

"""Sending helpers: Telegram's 4096-character limit and honest error logging."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, Message, ReplyKeyboardMarkup

logger = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096


def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on paragraph, then line, then space boundaries; never an empty piece.

    HTML tags are not balanced across pieces on purpose: Telegram rejects an unbalanced
    tag, so the fallback in `safe_send` re-sends such a piece as plain text.
    """
    if len(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = -1
        for separator in ("\n\n", "\n", " "):
            cut = rest.rfind(separator, 1, limit)
            if cut > 0:
                cut += len(separator)
                break
        if cut <= 0:
            cut = limit
        head, rest = rest[:cut], rest[cut:]
        if head.strip():
            pieces.append(head.rstrip("\n") or head)
    if rest.strip():
        pieces.append(rest)
    return pieces


async def safe_send(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> Message | None:
    """Send `text` in as many messages as needed; the keyboard goes with the last one.

    Returns the last sent message or None when Telegram refused (blocked bot, bad chat);
    the refusal is logged with context and never hides behind a generic exception.
    """
    pieces = split_text(text)
    if not pieces:
        return None
    last: Message | None = None
    for index, piece in enumerate(pieces):
        markup = reply_markup if index == len(pieces) - 1 else None
        try:
            last = await bot.send_message(
                chat_id, piece, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True
            )
        except TelegramAPIError as exc:
            if "can't parse entities" in str(exc).lower():
                logger.warning("html_rejected", extra={"chat_id": chat_id, "error": str(exc)})
                try:
                    last = await bot.send_message(
                        chat_id, piece, reply_markup=markup, parse_mode=None, disable_web_page_preview=True
                    )
                    continue
                except TelegramAPIError as retry_exc:
                    exc = retry_exc
            logger.error("send_failed", extra={"chat_id": chat_id, "error": str(exc), "piece": index})
            return None
    return last

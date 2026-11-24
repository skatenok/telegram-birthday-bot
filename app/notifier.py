"""Отправка сообщений с учётом топиков и лимитов Telegram."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, Message

from .utils import chunk_lines

log = logging.getLogger(__name__)

_THREAD_ERROR_MARKERS = (
    "thread not found",
    "message thread not found",
    "topic",
    "topic_id_invalid",
    "message thread",
)


def _is_thread_error(error: TelegramBadRequest) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _THREAD_ERROR_MARKERS)


async def send_to_chat(
    bot: Bot,
    chat_id: int,
    text: str,
    topic_id: int | None = None,
) -> Message | None:
    """Отправляет сообщение в чат (или в топик) и возвращает результат.

    Если топик удалён — отправляет в общий чат (General).
    """
    kwargs: dict[str, object] = {"chat_id": chat_id, "text": text}
    if topic_id:
        kwargs["message_thread_id"] = topic_id

    for attempt in (1, 2):
        try:
            return await bot.send_message(**kwargs)  # type: ignore[arg-type]
        except TelegramRetryAfter as error:
            if attempt == 2:
                raise
            wait = min(int(error.retry_after) + 1, 30)
            log.warning("Telegram просит подождать %s c, повторяю отправку", wait)
            await asyncio.sleep(wait)
        except TelegramBadRequest as error:
            if topic_id and _is_thread_error(error):
                log.warning(
                    "Не удалось отправить в топик %s чата %s (%s), отправляю в общий чат",
                    topic_id,
                    chat_id,
                    error,
                )
                kwargs.pop("message_thread_id", None)
                topic_id = None
                continue
            raise

    return None


async def send_long_text(
    message: Message,
    lines: list[str],
    limit: int = 3800,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Отправляет длинный текст несколькими сообщениями (ответом на команду)."""
    chunks = chunk_lines(lines, limit=limit)
    for index, chunk in enumerate(chunks):
        markup = reply_markup if index == len(chunks) - 1 else None
        await message.answer(chunk, reply_markup=markup)

"""Проверки прав в чате."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

ADMIN_STATUSES = {"creator", "administrator"}
ANONYMOUS_ADMIN_ID = 1087968824


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return False
    return member.status in ADMIN_STATUSES


async def can_manage_chat(bot: Bot, message: Message) -> bool:
    """Админ чата или анонимный администратор, пишущий от имени группы."""
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True
    user = message.from_user
    if user is None:
        return False
    if user.id == ANONYMOUS_ADMIN_ID:
        return True
    return await is_admin(bot, message.chat.id, user.id)

"""Общие команды и реакция на добавление/удаление бота."""

from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.filters import (
    JOIN_TRANSITION,
    LEAVE_TRANSITION,
    ChatMemberUpdatedFilter,
    Command,
    CommandStart,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import ChatMemberUpdated, Message

from ..db import Database

log = logging.getLogger(__name__)

router = Router(name="common")

GROUP_TYPES = {"group", "supergroup"}

HELP_TEXT = """🎂 <b>Бот дней рождения</b>

Я живу в группе и напоминаю о днях рождения — в общем чате или в выбранном топике.

<b>Как добавить запись</b>
• <code>/add Иван 12.05.1990</code> — имя и дата
• <code>/add 12.05</code> ответом на сообщение человека — имя возьму из профиля
• <code>/add</code> — выбрать: добавить себя или ввести имя
• <code>/addpet Барсик 12.05.2020</code> — день рождения питомца (можно и <code>/add pet ...</code>)

<b>Команды</b>
• /list — все дни рождения
• /today — у кого день рождения сегодня
• /del — удалить запись (<code>/del 3</code> или <code>/del Иван</code>)
• /settings — текущие настройки
• /topic — в какой топик присылать напоминания
• /time — время напоминания
• /tz — часовой пояс
• /daybefore — предупреждать за день
• /reminders — включить/выключить напоминания
• /test — проверить отправку
• /help — эта справка

<b>Топики</b>
Включите в группе темы (Topics) и отправьте <code>/topic</code> внутри нужного топика — напоминания будут приходить туда. <code>/topic off</code> — вернуть в общий чат.

<b>Права</b>
Добавлять дни рождения может любой участник, удалять — автор записи или админ. Настройки меняют только администраторы.

<b>Важно</b>
В пошаговом вводе бот использует Force Reply — отвечайте на его сообщения. При включённом privacy mode обычный текст без ответа до бота не доходит: отключите privacy mode в @BotFather (/setprivacy → Disable) или выдайте боту права администратора.
"""

PRIVATE_TEXT = """Привет! Я напоминаю о днях рождения в групповых чатах.

Добавьте меня в нужную группу — и я начну работу:
{link}

После добавления:
1. Напишите <code>/add Иван 12.05.1990</code>
2. Если в группе есть темы — отправьте <code>/topic</code> внутри нужного топика
3. Проверьте настройки: /settings
"""


async def _bot_username(bot: Bot) -> str | None:
    try:
        me = await bot.get_me()
    except Exception:
        log.exception("Не удалось получить данные бота")
        return None
    return me.username


@router.my_chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_bot_added(event: ChatMemberUpdated, bot: Bot, db: Database) -> None:
    if event.new_chat_member.user.id != bot.id:
        return
    if event.chat.type not in GROUP_TYPES:
        return

    reminder_time = db.default_reminder_time
    timezone_name = db.default_timezone

    try:
        chat = await db.ensure_chat(event.chat.id, event.chat.title)
        await db.update_chat(event.chat.id, reminders_enabled=1)
        reminder_time = chat["reminder_time"]
        timezone_name = chat["timezone"]
    except Exception:
        log.exception("Не удалось сохранить чат %s", event.chat.id)

    text = (
        "Спасибо, что добавили меня! 🎂\n\n"
        "Я напоминаю о днях рождения участников.\n\n"
        "1️⃣ Добавьте первую запись:\n"
        "<code>/add Иван 12.05.1990</code>\n"
        "или ответьте на сообщение человека: <code>/add 12.05</code>\n\n"
        "2️⃣ Если в группе включены темы (Topics), отправьте <code>/topic</code> "
        "внутри нужного топика — напоминания будут приходить туда.\n\n"
        f"3️⃣ Настройки по умолчанию: {reminder_time} ({timezone_name}). "
        "Изменить: /time и /tz.\n\n"
        "Вся справка: /help"
    )

    try:
        await bot.send_message(event.chat.id, text)
    except Exception:
        log.exception("Не удалось отправить приветствие в чат %s", event.chat.id)


@router.my_chat_member(ChatMemberUpdatedFilter(LEAVE_TRANSITION))
async def on_bot_removed(event: ChatMemberUpdated, bot: Bot) -> None:
    if event.new_chat_member.user.id != bot.id:
        return
    log.info("Бота удалили из чата %s", event.chat.id)


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, db: Database) -> None:
    if message.chat.type in GROUP_TYPES:
        await db.ensure_chat(message.chat.id, message.chat.title)
        await message.answer(HELP_TEXT)
        return

    username = await _bot_username(bot)
    if username:
        link = f'<a href="https://t.me/{username}?startgroup=true">Добавить в группу</a>'
    else:
        link = "Найдите меня в Telegram и нажмите «Добавить в группу»."
    await message.answer(PRIVATE_TEXT.format(link=link))


@router.message(Command("help", "помощь", "справка"))
async def cmd_help(message: Message, bot: Bot, db: Database) -> None:
    if message.chat.type in GROUP_TYPES:
        await db.ensure_chat(message.chat.id, message.chat.title)
        await message.answer(HELP_TEXT)
        return

    username = await _bot_username(bot)
    if username:
        link = f'<a href="https://t.me/{username}?startgroup=true">Добавить в группу</a>'
    else:
        link = "Найдите меня в Telegram и нажмите «Добавить в группу»."
    await message.answer(PRIVATE_TEXT.format(link=link))


@router.message(Command("cancel", "отмена"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    if current is None:
        await message.reply("Нечего отменять 🙂")
    else:
        await message.reply("Ок, отменил.")

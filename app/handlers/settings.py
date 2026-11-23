"""Настройки чата: топик, время, часовой пояс, тест."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .. import notifier
from ..db import Database
from ..permissions import can_manage_chat
from ..reminders import test_message
from ..utils import escape, normalize_time, validate_timezone

log = logging.getLogger(__name__)

router = Router(name="settings")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

ON_VALUES = {"on", "вкл", "включить", "да", "1", "true", "yes"}
OFF_VALUES = {"off", "выкл", "выключить", "нет", "0", "false", "no"}


async def _require_admin(message: Message, bot: Bot) -> bool:
    if await can_manage_chat(bot, message):
        return True
    await message.reply("⛔ Эта команда доступна только администраторам чата.")
    return False


def _topic_title(chat: dict) -> str:
    if chat["topic_id"]:
        return f"топик <code>{chat['topic_id']}</code>"
    return "общий чат (General)"


@router.message(Command("settings", "настройки"))
async def cmd_settings(message: Message, bot: Bot, db: Database) -> None:
    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    records = await db.list_birthdays(message.chat.id)

    text = (
        "⚙️ <b>Настройки напоминаний</b>\n\n"
        f"• Куда: {_topic_title(chat)}\n"
        f"• Время: <b>{chat['reminder_time']}</b>\n"
        f"• Часовой пояс: <code>{escape(chat['timezone'])}</code>\n"
        f"• Предупреждать за день: {'да' if chat['day_before'] else 'нет'}\n"
        f"• Напоминания: {'включены' if chat['reminders_enabled'] else 'выключены'}\n"
        f"• Записей: {len(records)}\n\n"
        "<b>Изменить</b>\n"
        "• /topic — куда присылать (отправьте внутри нужного топика)\n"
        "• /time 09:30 — время\n"
        "• /tz Europe/Moscow — часовой пояс\n"
        "• /daybefore on|off — предупреждать за день\n"
        "• /reminders on|off — включить/выключить\n"
        "• /test — тестовое сообщение"
    )
    await message.reply(text)


@router.message(Command("topic", "топик"))
async def cmd_topic(
    message: Message,
    command: CommandObject,
    bot: Bot,
    db: Database,
) -> None:
    if not await _require_admin(message, bot):
        return

    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    args = (command.args or "").strip().lower()

    if args in {"off", "выкл", "выключить", "нет", "general", "общий", "сброс"}:
        await db.update_chat(message.chat.id, topic_id=None)
        await message.reply(
            "🧹 Готово: напоминания будут приходить в общий чат (General)."
        )
        return

    if args in {"here", "тут", "здесь", ""}:
        topic_id = message.message_thread_id
        if topic_id is None:
            if chat["topic_id"]:
                await message.reply(
                    f"Сейчас напоминания идут в топик <code>{chat['topic_id']}</code>.\n"
                    "Чтобы изменить — отправьте <code>/topic</code> внутри нужного топика "
                    "или <code>/topic &lt;id&gt;</code>."
                )
            else:
                await message.reply(
                    "Сейчас напоминания идут в общий чат (General).\n"
                    "Чтобы изменить — отправьте <code>/topic</code> внутри нужного топика."
                )
            return
    elif args.isdigit():
        topic_id = int(args)
    else:
        await message.reply(
            "Не понял. Варианты:\n"
            "• <code>/topic</code> — внутри нужного топика\n"
            "• <code>/topic 42</code> — по id топика\n"
            "• <code>/topic off</code> — вернуть в общий чат"
        )
        return

    if not message.chat.is_forum:
        await message.reply(
            "В этой группе не включены темы (Topics), поэтому напоминания и так "
            "приходят в общий чат. Включите темы в настройках группы, если нужны топики."
        )
        return

    await db.update_chat(message.chat.id, topic_id=topic_id)

    try:
        await notifier.send_to_chat(
            bot,
            message.chat.id,
            "✅ Напоминания о днях рождения будут приходить в этот топик.",
            topic_id,
        )
    except Exception as error:
        log.exception("Не удалось отправить сообщение в топик %s", topic_id)
        await message.reply(
            "⚠️ Топик сохранён, но отправить в него сообщение не получилось. "
            f"Проверьте, что он существует и я могу туда писать.\n<code>{escape(str(error))}</code>"
        )
        return

    if message.message_thread_id != topic_id:
        await message.reply(
            f"✅ Готово: напоминания будут приходить в топик <code>{topic_id}</code>."
        )


@router.message(Command("time", "время"))
async def cmd_time(message: Message, command: CommandObject, bot: Bot, db: Database) -> None:
    if not await _require_admin(message, bot):
        return

    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    args = (command.args or "").strip()

    if not args:
        await message.reply(
            f"⏰ Напоминания приходят в <b>{chat['reminder_time']}</b> "
            f"({escape(chat['timezone'])}).\n"
            "Изменить: <code>/time 09:30</code>"
        )
        return

    normalized = normalize_time(args)
    if normalized is None:
        await message.reply(
            "Нужен формат ЧЧ:ММ, например <code>/time 09:30</code> или <code>/time 8:05</code>."
        )
        return

    await db.update_chat(message.chat.id, reminder_time=normalized)
    await message.reply(
        f"⏰ Готово: напоминания будут приходить в <b>{normalized}</b> "
        f"({escape(chat['timezone'])})."
    )


@router.message(Command("tz", "timezone", "часовой_пояс"))
async def cmd_timezone(message: Message, command: CommandObject, bot: Bot, db: Database) -> None:
    if not await _require_admin(message, bot):
        return

    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    args = (command.args or "").strip()

    if not args:
        await message.reply(
            f"🌍 Часовой пояс: <code>{escape(chat['timezone'])}</code>\n"
            "Изменить: <code>/tz Europe/Moscow</code>\n"
            "Другие примеры: <code>Asia/Almaty</code>, <code>Asia/Tbilisi</code>, "
            "<code>Europe/Berlin</code>, <code>Asia/Yekaterinburg</code>."
        )
        return

    if not validate_timezone(args):
        await message.reply(
            f"Не знаю такой часовой пояс: <code>{escape(args)}</code>.\n"
            "Нужен из базы IANA, например <code>Europe/Moscow</code> или <code>Asia/Almaty</code>."
        )
        return

    await db.update_chat(message.chat.id, timezone=args)
    await message.reply(
        f"🌍 Готово: часовой пояс <code>{escape(args)}</code>, "
        f"напоминания в {chat['reminder_time']}."
    )


@router.message(Command("daybefore", "загодя"))
async def cmd_day_before(message: Message, command: CommandObject, bot: Bot, db: Database) -> None:
    if not await _require_admin(message, bot):
        return

    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    args = (command.args or "").strip().lower()

    if args in ON_VALUES:
        await db.update_chat(message.chat.id, day_before=1)
        await message.reply("🔔 Ок: буду предупреждать за день до дня рождения.")
        return
    if args in OFF_VALUES:
        await db.update_chat(message.chat.id, day_before=0)
        await message.reply("🔕 Ок: предупреждать заранее больше не буду.")
        return

    state = "включено" if chat["day_before"] else "выключено"
    await message.reply(
        f"Сейчас предупреждение за день {state}.\n"
        "Изменить: <code>/daybefore on</code> или <code>/daybefore off</code>"
    )


@router.message(Command("reminders", "напоминания"))
async def cmd_reminders(message: Message, command: CommandObject, bot: Bot, db: Database) -> None:
    if not await _require_admin(message, bot):
        return

    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    args = (command.args or "").strip().lower()

    if args in ON_VALUES:
        await db.update_chat(message.chat.id, reminders_enabled=1)
        await message.reply("🔔 Напоминания включены.")
        return
    if args in OFF_VALUES:
        await db.update_chat(message.chat.id, reminders_enabled=0)
        await message.reply("🔕 Напоминания выключены. Включить: <code>/reminders on</code>")
        return

    state = "включены" if chat["reminders_enabled"] else "выключены"
    await message.reply(
        f"Сейчас напоминания {state}.\n"
        "Изменить: <code>/reminders on</code> или <code>/reminders off</code>"
    )


@router.message(Command("test", "тест"))
async def cmd_test(message: Message, bot: Bot, db: Database) -> None:
    if not await _require_admin(message, bot):
        return

    chat = await db.ensure_chat(message.chat.id, message.chat.title)

    try:
        sent = await notifier.send_to_chat(
            bot,
            message.chat.id,
            test_message(chat["title"]),
            chat["topic_id"],
        )
    except Exception as error:
        log.exception("Тестовая отправка не удалась")
        await message.reply(
            "⚠️ Не удалось отправить тестовое сообщение.\n"
            f"<code>{escape(str(error))}</code>"
        )
        return

    if sent is not None and sent.message_thread_id != message.message_thread_id:
        where = f"топик <code>{sent.message_thread_id}</code>" if sent.message_thread_id else "общий чат"
        await message.reply(f"✅ Тестовое сообщение отправлено в {where}.")

"""Настройки чата: интерактивное меню, топик, время, часовой пояс, тест."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .. import notifier
from ..db import Database
from ..permissions import can_manage_callback, can_manage_chat
from ..reminders import test_message
from ..utils import escape, normalize_time, validate_timezone

log = logging.getLogger(__name__)

router = Router(name="settings")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

ON_VALUES = {"on", "вкл", "включить", "да", "1", "true", "yes"}
OFF_VALUES = {"off", "выкл", "выключить", "нет", "0", "false", "no"}


class SettingsFlow(StatesGroup):
    waiting_time = State()
    waiting_tz = State()


# --------------------------------------------------------------------------- #
# Внешний вид меню
# --------------------------------------------------------------------------- #
def _topic_title(chat: dict) -> str:
    if chat["topic_id"]:
        return f"топик <code>{chat['topic_id']}</code>"
    return "общий чат (General)"


def _settings_text(chat: dict, count: int, admin: bool = True) -> str:
    text = (
        "⚙️ <b>Настройки напоминаний</b>\n\n"
        f"• Куда: {_topic_title(chat)}\n"
        f"• Время: <b>{chat['reminder_time']}</b>\n"
        f"• Часовой пояс: <code>{escape(chat['timezone'])}</code>\n"
        f"• Предупреждать за день: {'да' if chat['day_before'] else 'нет'}\n"
        f"• Напоминания: {'включены' if chat['reminders_enabled'] else 'выключены'}\n"
        f"• Записей: {count}"
    )
    if admin:
        text += "\n\nНажмите кнопку, чтобы изменить настройку."
    else:
        text += "\n\nНастройки меняют только администраторы чата."
    return text


def _settings_keyboard(chat: dict) -> InlineKeyboardMarkup:
    day_state = "да" if chat["day_before"] else "нет"
    reminders_state = "вкл" if chat["reminders_enabled"] else "выкл"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏰ Время", callback_data="set:time"),
                InlineKeyboardButton(text="🌍 Часовой пояс", callback_data="set:tz"),
            ],
            [
                InlineKeyboardButton(
                    text=f"🔔 За день: {day_state}", callback_data="set:day"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🔕 Напоминания: {reminders_state}", callback_data="set:rem"
                )
            ],
            [
                InlineKeyboardButton(text="🎯 Топик", callback_data="set:topic"),
                InlineKeyboardButton(text="🧪 Тест", callback_data="set:test"),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить запись", callback_data="del:list:0"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить", callback_data="set:refresh"
                )
            ],
        ]
    )


def _topic_text(chat: dict) -> str:
    return (
        "🎯 <b>Куда присылать напоминания?</b>\n\n"
        f"Сейчас: {_topic_title(chat)}\n\n"
        "Выберите вариант ниже. «Этот топик» доступен, если меню открыто "
        "внутри нужного топика."
    )


def _topic_keyboard(thread_id: int | None) -> InlineKeyboardMarkup:
    rows = []
    if thread_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🎯 Этот топик", callback_data="set:topic:here"
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="💬 Общий чат", callback_data="set:topic:general")]
    )
    rows.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="set:refresh")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _settings_payload(
    chat_id: int, db: Database, admin: bool = True
) -> tuple[str, InlineKeyboardMarkup | None]:
    chat = await db.ensure_chat(chat_id)
    records = await db.list_birthdays(chat_id)
    keyboard = _settings_keyboard(chat) if admin else None
    return _settings_text(chat, len(records), admin), keyboard


async def _send_settings(message: Message, db: Database, admin: bool = True) -> None:
    text, keyboard = await _settings_payload(message.chat.id, db, admin)
    await message.answer(text, reply_markup=keyboard)


async def _edit_settings(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    text, keyboard = await _settings_payload(callback.message.chat.id, db)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=keyboard)


async def _require_admin(message: Message, bot: Bot) -> bool:
    if await can_manage_chat(bot, message):
        return True
    await message.reply("⛔ Эта команда доступна только администраторам чата.")
    return False


async def _require_admin_callback(callback: CallbackQuery, bot: Bot) -> bool:
    if await can_manage_callback(bot, callback):
        return True
    await callback.answer(
        "⛔ Настройки меняют только администраторы чата.", show_alert=True
    )
    return False


def _force_reply(placeholder: str) -> ForceReply:
    return ForceReply(force_reply=True, input_field_placeholder=placeholder)


# --------------------------------------------------------------------------- #
# Команды
# --------------------------------------------------------------------------- #
@router.message(Command("settings", "настройки"))
async def cmd_settings(message: Message, bot: Bot, db: Database) -> None:
    await _send_settings(message, db, admin=await can_manage_chat(bot, message))


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

    if not args:
        await message.answer(
            _topic_text(chat),
            reply_markup=_topic_keyboard(message.message_thread_id),
        )
        return

    if args in {"off", "выкл", "выключить", "нет", "general", "общий", "сброс"}:
        await db.update_chat(message.chat.id, topic_id=None)
        await message.reply(
            "🧹 Готово: напоминания будут приходить в общий чат (General)."
        )
        return

    if args in {"here", "тут", "здесь"}:
        topic_id = message.message_thread_id
        if topic_id is None:
            await message.reply(
                "Отправьте <code>/topic</code> внутри нужного топика или "
                "выберите топик в меню /settings."
            )
            return
    elif args.isdigit():
        topic_id = int(args)
    else:
        await message.reply(
            "Не понял. Варианты:\n"
            "• <code>/topic</code> — меню выбора\n"
            "• <code>/topic 42</code> — по id топика\n"
            "• <code>/topic off</code> — вернуть в общий чат"
        )
        return

    if not message.chat.is_forum:
        await message.reply(
            "В этой группе не включены темы (Topics), поэтому напоминания и так "
            "приходят в общий чат."
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
            f"Проверьте, что он существует и я могу туда писать.\n"
            f"<code>{escape(str(error))}</code>"
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

    args = (command.args or "").strip()
    if not args:
        await _send_settings(message, db)
        return

    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    normalized = normalize_time(args)
    if normalized is None:
        await message.reply(
            "Нужен формат ЧЧ:ММ, например <code>/time 09:30</code> или "
            "<code>/time 8:05</code>."
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

    args = (command.args or "").strip()
    if not args:
        await _send_settings(message, db)
        return

    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    if not validate_timezone(args):
        await message.reply(
            f"Не знаю такой часовой пояс: <code>{escape(args)}</code>.\n"
            "Нужен из базы IANA, например <code>Europe/Moscow</code> или "
            "<code>Asia/Almaty</code>."
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

    args = (command.args or "").strip().lower()
    if not args:
        await _send_settings(message, db)
        return

    if args in ON_VALUES:
        await db.update_chat(message.chat.id, day_before=1)
        await message.reply("🔔 Ок: буду предупреждать за день до дня рождения.")
        return
    if args in OFF_VALUES:
        await db.update_chat(message.chat.id, day_before=0)
        await message.reply("🔕 Ок: предупреждать заранее больше не буду.")
        return

    await message.reply("Не понял. Используйте кнопку в /settings.")


@router.message(Command("reminders", "напоминания"))
async def cmd_reminders(message: Message, command: CommandObject, bot: Bot, db: Database) -> None:
    if not await _require_admin(message, bot):
        return

    args = (command.args or "").strip().lower()
    if not args:
        await _send_settings(message, db)
        return

    if args in ON_VALUES:
        await db.update_chat(message.chat.id, reminders_enabled=1)
        await message.reply("🔔 Напоминания включены.")
        return
    if args in OFF_VALUES:
        await db.update_chat(message.chat.id, reminders_enabled=0)
        await message.reply("🔕 Напоминания выключены.")
        return

    await message.reply("Не понял. Используйте кнопку в /settings.")


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
        where = (
            f"топик <code>{sent.message_thread_id}</code>"
            if sent.message_thread_id
            else "общий чат"
        )
        await message.reply(f"✅ Тестовое сообщение отправлено в {where}.")


# --------------------------------------------------------------------------- #
# Интерактивные настройки
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.regexp(r"^set:"))
async def settings_callback(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    state: FSMContext,
) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    if not await _require_admin_callback(callback, bot):
        return

    action = (callback.data or "").removeprefix("set:")
    chat_id = callback.message.chat.id

    if action == "refresh":
        await callback.answer()
        await _edit_settings(callback, db)
        return

    if action == "time":
        await state.set_state(SettingsFlow.waiting_time)
        await callback.answer()
        await callback.message.answer(
            "Отправьте время в формате ЧЧ:ММ ответом на это сообщение "
            "(например <code>09:30</code>). Отмена — /cancel",
            reply_markup=_force_reply("09:30"),
        )
        return

    if action == "tz":
        await state.set_state(SettingsFlow.waiting_tz)
        await callback.answer()
        await callback.message.answer(
            "Отправьте часовой пояс из базы IANA ответом на это сообщение.\n"
            "Примеры: <code>Europe/Moscow</code>, <code>Asia/Almaty</code>, "
            "<code>Asia/Tbilisi</code>. Отмена — /cancel",
            reply_markup=_force_reply("Europe/Moscow"),
        )
        return

    if action == "day":
        chat = await db.ensure_chat(chat_id)
        value = 0 if chat["day_before"] else 1
        await db.update_chat(chat_id, day_before=value)
        await callback.answer("Включено" if value else "Выключено")
        await _edit_settings(callback, db)
        return

    if action == "rem":
        chat = await db.ensure_chat(chat_id)
        value = 0 if chat["reminders_enabled"] else 1
        await db.update_chat(chat_id, reminders_enabled=value)
        await callback.answer("Включены" if value else "Выключены")
        await _edit_settings(callback, db)
        return

    if action == "topic":
        chat = await db.ensure_chat(chat_id)
        await callback.answer()
        try:
            await callback.message.edit_text(
                _topic_text(chat),
                reply_markup=_topic_keyboard(callback.message.message_thread_id),
            )
        except TelegramBadRequest:
            pass
        return

    if action == "topic:here":
        thread_id = callback.message.message_thread_id
        if thread_id is None:
            await callback.answer(
                "Откройте настройки внутри нужного топика", show_alert=True
            )
            return
        await db.update_chat(chat_id, topic_id=thread_id)
        await callback.answer("Топик сохранён")
        await _edit_settings(callback, db)
        return

    if action == "topic:general":
        await db.update_chat(chat_id, topic_id=None)
        await callback.answer("Буду писать в общий чат")
        await _edit_settings(callback, db)
        return

    if action == "test":
        chat = await db.ensure_chat(chat_id)
        try:
            await notifier.send_to_chat(
                bot,
                chat_id,
                test_message(chat["title"]),
                chat["topic_id"],
            )
        except Exception:
            log.exception("Тестовая отправка не удалась")
            await callback.answer(
                "Не удалось отправить тестовое сообщение", show_alert=True
            )
            return
        await callback.answer("Тестовое сообщение отправлено")
        return

    await callback.answer()


# --------------------------------------------------------------------------- #
# Ввод времени и часового пояса
# --------------------------------------------------------------------------- #
@router.message(SettingsFlow.waiting_time, F.text, ~F.text.startswith("/"))
async def save_time(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await can_manage_chat(bot, message):
        await message.reply("⛔ Настройки меняют только администраторы чата.")
        return

    normalized = normalize_time(message.text or "")
    if normalized is None:
        await message.answer(
            "Нужен формат ЧЧ:ММ, например <code>09:30</code>. Отмена — /cancel",
            reply_markup=_force_reply("09:30"),
        )
        return

    await db.update_chat(message.chat.id, reminder_time=normalized)
    await state.clear()
    await message.answer(f"⏰ Время обновлено: <b>{normalized}</b>.")
    await _send_settings(message, db)


@router.message(SettingsFlow.waiting_tz, F.text, ~F.text.startswith("/"))
async def save_timezone(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    if not await can_manage_chat(bot, message):
        await message.reply("⛔ Настройки меняют только администраторы чата.")
        return

    timezone_name = (message.text or "").strip()
    if not validate_timezone(timezone_name):
        await message.answer(
            f"Не знаю такой часовой пояс: <code>{escape(timezone_name)}</code>.\n"
            "Примеры: <code>Europe/Moscow</code>, <code>Asia/Almaty</code>. "
            "Отмена — /cancel",
            reply_markup=_force_reply("Europe/Moscow"),
        )
        return

    await db.update_chat(message.chat.id, timezone=timezone_name)
    await state.clear()
    await message.answer(
        f"🌍 Часовой пояс обновлён: <code>{escape(timezone_name)}</code>."
    )
    await _send_settings(message, db)

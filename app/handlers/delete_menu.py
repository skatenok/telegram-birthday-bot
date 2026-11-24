"""Интерактивное удаление записей: список с кнопками и подтверждением."""

from __future__ import annotations

import math

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..db import KIND_PET, Database
from ..permissions import can_manage_callback
from ..utils import escape, format_date

router = Router(name="delete-menu")

PAGE_SIZE = 8


def _mark(record: dict) -> str:
    return "🐾 " if record.get("kind") == KIND_PET else ""


def _label(record: dict) -> str:
    return (
        f"{_mark(record)}{record['name']} — "
        f"{format_date(record['day'], record['month'], record['year'])}"
    )


def _page_info(records: list[dict], page: int) -> tuple[list[dict], int, int]:
    pages = max(1, math.ceil(len(records) / PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    return records[start : start + PAGE_SIZE], page, pages


def _menu_text(page: int, pages: int) -> str:
    text = "🗑 <b>Удаление записи</b>\n\nВыберите запись:"
    if pages > 1:
        text += f"\n<i>Страница {page + 1} из {pages}</i>"
    return text


def _menu_keyboard(
    records: list[dict], page: int, pages: int
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=_label(record), callback_data=f"del:ask:{record['id']}")]
        for record in records
    ]

    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"del:list:{page - 1}")
        )
    if page < pages - 1:
        navigation.append(
            InlineKeyboardButton(text="➡️", callback_data=f"del:list:{page + 1}")
        )
    if navigation:
        rows.append(navigation)

    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="del:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_delete_menu(message: Message, db: Database, page: int = 0) -> None:
    records = await db.list_birthdays(message.chat.id)
    if not records:
        await message.reply("Записей нет — удалять нечего.")
        return

    page_records, page, pages = _page_info(records, page)
    await message.reply(
        _menu_text(page, pages),
        reply_markup=_menu_keyboard(page_records, page, pages),
    )


async def _render_list(callback: CallbackQuery, db: Database, page: int) -> None:
    records = await db.list_birthdays(callback.message.chat.id)
    if not records:
        await callback.message.edit_text("Записей нет — удалять нечего.")
        return

    page_records, page, pages = _page_info(records, page)
    await callback.message.edit_text(
        _menu_text(page, pages),
        reply_markup=_menu_keyboard(page_records, page, pages),
    )


async def _can_delete(
    bot: Bot, callback: CallbackQuery, record: dict
) -> bool:
    if record.get("created_by") == callback.from_user.id:
        return True
    return await can_manage_callback(bot, callback)


@router.callback_query(F.data.regexp(r"^del:(list|ask|yes|close)(:|$)"))
async def handle_delete_callback(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    action = parts[1]

    if action == "close":
        await callback.answer()
        try:
            await callback.message.edit_text("Удаление закрыто.")
        except TelegramBadRequest:
            pass
        return

    if action == "list":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await callback.answer()
        try:
            await _render_list(callback, db, page)
        except TelegramBadRequest:
            pass
        return

    if len(parts) < 3 or not parts[2].isdigit():
        await callback.answer()
        return

    record = await db.get_birthday(int(parts[2]))
    if record is None or int(record["chat_id"]) != callback.message.chat.id:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    if action == "ask":
        if not await _can_delete(bot, callback, record):
            await callback.answer(
                "Удалить может автор записи или администратор чата",
                show_alert=True,
            )
            return

        text = (
            "Удалить эту запись?\n\n"
            f"{_mark(record)}<b>{escape(record['name'])}</b> — "
            f"{format_date(record['day'], record['month'], record['year'])} "
            f"(<code>#{record['id']}</code>)"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🗑 Да, удалить", callback_data=f"del:yes:{record['id']}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад", callback_data="del:list:0"
                    )
                ],
            ]
        )
        await callback.answer()
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest:
            pass
        return

    if action == "yes":
        if not await _can_delete(bot, callback, record):
            await callback.answer(
                "Удалить может автор записи или администратор чата",
                show_alert=True,
            )
            return

        await db.delete_birthday(int(record["id"]))
        await callback.answer("Удалено")
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К списку", callback_data="del:list:0")]
            ]
        )
        try:
            await callback.message.edit_text(
                f"🗑 Удалено: {_mark(record)}<b>{escape(record['name'])}</b> — "
                f"{format_date(record['day'], record['month'], record['year'])}",
                reply_markup=keyboard,
            )
        except TelegramBadRequest:
            pass
        return

    await callback.answer()

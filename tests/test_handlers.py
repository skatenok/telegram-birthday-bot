"""Сквозные тесты обработчиков через фейковую Telegram-сессию."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    GetChatMember,
    GetMe,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import (
    Chat,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberOwner,
    ChatMemberUpdated,
    Message,
    MessageEntity,
    Update,
    User,
)

from app.db import Database
from app.handlers import birthdays, common, settings
from app.middlewares import ClearStateOnCommand


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self.sent: list[SendMessage] = []

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, SendMessage):
            self.sent.append(method)
            return Message(
                message_id=1000 + len(self.sent),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="supergroup"),
                text=str(method.text),
                message_thread_id=method.message_thread_id,
            )
        if isinstance(method, GetMe):
            return User(id=42, is_bot=True, first_name="Bot", username="test_bot")
        if isinstance(method, GetChatMember):
            return ChatMemberOwner(
                status="creator",
                user=User(id=int(method.user_id), is_bot=False, first_name="Admin"),
                is_anonymous=False,
            )
        return True

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b""

    async def close(self) -> None:
        return None


def _command(text: str, *, reply_to: Message | None = None, thread_id: int | None = None,
             is_forum: bool = False) -> Update:
    message = Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=-1001234567890, type="supergroup", title="Друзья", is_forum=is_forum),
        from_user=User(id=5, is_bot=False, first_name="Петя"),
        text=text,
        entities=[MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))],
        message_thread_id=thread_id,
        reply_to_message=reply_to,
    )
    return Update(update_id=1, message=message)


_dispatcher: Dispatcher | None = None


def _get_dispatcher() -> Dispatcher:
    """Роутеры — синглтоны, поэтому диспетчер создаём один раз на процесс."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher()
        _dispatcher.message.outer_middleware(ClearStateOnCommand())
        _dispatcher.include_routers(common.router, birthdays.router, settings.router)
    return _dispatcher


async def _run(dispatcher: Dispatcher, bot: Bot, update: Update, db: Database) -> None:
    await dispatcher.feed_update(bot, update, db=db)


def test_add_list_delete(tmp_path) -> None:
    asyncio.run(_add_list_delete(tmp_path))


async def _add_list_delete(tmp_path) -> None:
    db = Database(str(tmp_path / "handlers.db"))
    await db.connect()
    session = FakeSession()
    bot = Bot(token="42:TEST", session=session)
    dp = _get_dispatcher()

    try:
        await _run(dp, bot, _command("/add Иван 12.05.1990"), db)
        assert session.sent, "бот ничего не ответил на /add"
        assert "Записал" in session.sent[-1].text
        assert "Иван" in session.sent[-1].text
        assert "12.05.1990" in session.sent[-1].text

        records = await db.list_birthdays(-1001234567890)
        assert len(records) == 1
        assert records[0]["day"] == 12 and records[0]["month"] == 5
        assert records[0]["year"] == 1990
        assert records[0]["created_by"] == 5

        # Дубликат не создаётся
        await _run(dp, bot, _command("/add Иван 12.05.1990"), db)
        assert "уже есть" in session.sent[-1].text
        assert len(await db.list_birthdays(-1001234567890)) == 1

        # Добавление ответом на сообщение человека
        person = Message(
            message_id=9,
            date=datetime.now(UTC),
            chat=Chat(id=-1001234567890, type="supergroup"),
            from_user=User(id=77, is_bot=False, first_name="Мария", last_name="Иванова"),
            text="привет",
        )
        await _run(dp, bot, _command("/add 03.11", reply_to=person), db)
        records = await db.list_birthdays(-1001234567890)
        maria = [row for row in records if row["user_id"] == 77]
        assert len(maria) == 1
        assert maria[0]["name"] == "Мария Иванова"

        await _run(dp, bot, _command("/list"), db)
        assert "Иван" in session.sent[-1].text
        assert "Мария Иванова" in session.sent[-1].text

        await _run(dp, bot, _command(f"/del {records[0]['id']}"), db)
        assert "Удалено" in session.sent[-1].text
        assert len(await db.list_birthdays(-1001234567890)) == 1

        await _run(dp, bot, _command("/settings"), db)
        assert "Настройки напоминаний" in session.sent[-1].text
    finally:
        await bot.session.close()
        await db.close()


def test_invalid_date_and_topic(tmp_path) -> None:
    asyncio.run(_invalid_date_and_topic(tmp_path))


async def _invalid_date_and_topic(tmp_path) -> None:
    db = Database(str(tmp_path / "topics.db"))
    await db.connect()
    session = FakeSession()
    bot = Bot(token="42:TEST", session=session)
    dp = _get_dispatcher()

    try:
        await _run(dp, bot, _command("/add Иван 32.13"), db)
        assert "Не вижу дату" in session.sent[-1].text
        assert await db.list_birthdays(-1001234567890) == []

        await _run(dp, bot, _command("/topic 42", is_forum=True), db)
        chat = await db.get_chat(-1001234567890)
        assert chat is not None
        assert chat["topic_id"] == 42
        # Подтверждение отправлено в топик 42
        assert any(message.message_thread_id == 42 for message in session.sent)

        await _run(dp, bot, _command("/topic off", is_forum=True), db)
        chat = await db.get_chat(-1001234567890)
        assert chat["topic_id"] is None

        await _run(dp, bot, _command("/time 8:05"), db)
        chat = await db.get_chat(-1001234567890)
        assert chat["reminder_time"] == "08:05"

        await _run(dp, bot, _command("/time 25:00"), db)
        assert "формат ЧЧ:ММ" in session.sent[-1].text
    finally:
        await bot.session.close()
        await db.close()


def test_dispatch_wiring() -> None:
    dp = _get_dispatcher()
    used = set(dp.resolve_used_update_types())
    assert "message" in used
    assert "my_chat_member" in used
    assert len(birthdays.router.message.handlers) >= 6
    assert len(settings.router.message.handlers) >= 7


def _text(text: str, message_id: int = 11) -> Update:
    message = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=-1001234567890, type="supergroup", title="Друзья"),
        from_user=User(id=5, is_bot=False, first_name="Петя"),
        text=text,
    )
    return Update(update_id=2, message=message)


def test_add_flow_and_cancel(tmp_path) -> None:
    asyncio.run(_add_flow_and_cancel(tmp_path))


async def _add_flow_and_cancel(tmp_path) -> None:
    db = Database(str(tmp_path / "flow.db"))
    await db.connect()
    session = FakeSession()
    bot = Bot(token="42:TEST", session=session)
    dp = _get_dispatcher()

    try:
        await _run(dp, bot, _command("/add"), db)
        assert "Кого записываем" in session.sent[-1].text

        await _run(dp, bot, _text("Пётр"), db)
        assert "дата рождения" in session.sent[-1].text

        await _run(dp, bot, _text("07.04.1985"), db)
        assert "Записал" in session.sent[-1].text

        records = await db.list_birthdays(-1001234567890)
        assert len(records) == 1
        assert records[0]["name"] == "Пётр"
        assert (records[0]["day"], records[0]["month"], records[0]["year"]) == (7, 4, 1985)

        # /cancel с активным состоянием
        await _run(dp, bot, _command("/add"), db)
        assert "Кого записываем" in session.sent[-1].text
        await _run(dp, bot, _command("/cancel"), db)
        assert "отменил" in session.sent[-1].text

        # После отмены обычный текст не создаёт записей
        before = len(await db.list_birthdays(-1001234567890))
        await _run(dp, bot, _text("случайное сообщение"), db)
        assert len(await db.list_birthdays(-1001234567890)) == before

        # Любая команда сбрасывает незавершённый диалог
        await _run(dp, bot, _command("/add"), db)
        await _run(dp, bot, _text("Василий"), db)
        assert "дата рождения" in session.sent[-1].text
        await _run(dp, bot, _command("/settings"), db)
        await _run(dp, bot, _text("08.08.1991"), db)
        assert len(await db.list_birthdays(-1001234567890)) == before
    finally:
        await bot.session.close()
        await db.close()


def test_bot_added_to_chat(tmp_path) -> None:
    asyncio.run(_bot_added_to_chat(tmp_path))
async def _bot_added_to_chat(tmp_path) -> None:
    db = Database(str(tmp_path / "join.db"))
    await db.connect()
    session = FakeSession()
    bot = Bot(token="42:TEST", session=session)
    dp = _get_dispatcher()
    bot_user = User(id=42, is_bot=True, first_name="Bot", username="test_bot")

    update = Update(
        update_id=3,
        my_chat_member=ChatMemberUpdated(
            chat=Chat(id=-100999, type="supergroup", title="Новая группа"),
            from_user=User(id=5, is_bot=False, first_name="Петя"),
            date=datetime.now(UTC),
            old_chat_member=ChatMemberLeft(user=bot_user),
            new_chat_member=ChatMemberMember(user=bot_user),
        ),
    )

    try:
        await _run(dp, bot, update, db)
        assert session.sent, "бот не поздоровался после добавления"
        assert "Спасибо" in session.sent[-1].text
        chat = await db.get_chat(-100999)
        assert chat is not None
        assert chat["reminders_enabled"] == 1
    finally:
        await bot.session.close()
        await db.close()


def test_pet_commands(tmp_path) -> None:
    asyncio.run(_pet_commands(tmp_path))


async def _pet_commands(tmp_path) -> None:
    db = Database(str(tmp_path / "pet-handlers.db"))
    await db.connect()
    session = FakeSession()
    bot = Bot(token="42:TEST", session=session)
    dp = _get_dispatcher()

    try:
        await _run(dp, bot, _command("/addpet Барсик 12.05.2020"), db)
        assert "Записал питомца" in session.sent[-1].text
        assert "Барсик" in session.sent[-1].text

        records = await db.list_birthdays(-1001234567890)
        pet = next(row for row in records if row["kind"] == "pet")
        assert pet["name"] == "Барсик"
        assert pet["year"] == 2020
        assert pet["user_id"] is None
        assert pet["created_by"] == 5

        # Альтернативный синтаксис: /add pet ...
        await _run(dp, bot, _command("/add pet Мурка 07.07"), db)
        records = await db.list_birthdays(-1001234567890)
        assert len([row for row in records if row["kind"] == "pet"]) == 2

        # Человек с тем же именем и датой не считается дубликатом питомца
        await _run(dp, bot, _command("/add Барсик 12.05.2020"), db)
        records = await db.list_birthdays(-1001234567890)
        assert len([row for row in records if row["kind"] == "person"]) == 1

        # Пошаговый ввод: /addpet → кличка → дата
        await _run(dp, bot, _command("/addpet"), db)
        assert "Как зовут питомца" in session.sent[-1].text

        await _run(dp, bot, _text("Рекс"), db)
        assert "дата рождения" in session.sent[-1].text

        await _run(dp, bot, _text("01.01.2021"), db)
        assert "Записал питомца" in session.sent[-1].text

        records = await db.list_birthdays(-1001234567890)
        rex = next(row for row in records if row["name"] == "Рекс")
        assert rex["kind"] == "pet"
        assert rex["year"] == 2021

        # В списке питомцы помечены лапкой
        await _run(dp, bot, _command("/list"), db)
        listing = session.sent[-1].text
        assert "🐾 Барсик" in listing
        assert "🐾 Рекс" in listing
    finally:
        await bot.session.close()
        await db.close()


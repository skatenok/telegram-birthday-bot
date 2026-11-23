"""Тесты ядра: разбор дат, БД и планировщик (запуск: pytest)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import reminders, scheduler
from app.db import Database
from app.utils import (
    days_until,
    format_age,
    human_days,
    next_occurrence,
    normalize_time,
    occurs_on,
    parse_date,
    plural,
    user_mention,
)


# --------------------------------------------------------------------------- #
# Даты
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12.05", (12, 5, None)),
        ("12.05.1990", (12, 5, 1990)),
        ("1.2.1990", (1, 2, 1990)),
        ("12/05/1990", (12, 5, 1990)),
        ("12-05-1990", (12, 5, 1990)),
        ("29.02", (29, 2, None)),
        ("29 февраля 2000", (29, 2, 2000)),
        ("12 мая 1990г.", (12, 5, 1990)),
        ("12 МАЯ", (12, 5, None)),
        (" 7  марта ", (7, 3, None)),
    ],
)
def test_parse_date_valid(value: str, expected: tuple[int, int, int | None]) -> None:
    assert parse_date(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Иван",
        "32.01",
        "12.13",
        "31.02",
        "29.02.1990",
        "12.05.1800",
        "12.05.2999",
        "0.05",
        "12 маяк 1990",
    ],
)
def test_parse_date_invalid(value: str) -> None:
    assert parse_date(value) is None


def test_next_occurrence_and_days() -> None:
    today = date(2026, 5, 12)
    assert next_occurrence(12, 5, today) == today
    assert days_until(12, 5, today) == 0
    assert days_until(13, 5, today) == 1
    assert next_occurrence(11, 5, today) == date(2027, 5, 11)
    assert days_until(11, 5, today) == 364


def test_feb_29() -> None:
    # В невисокосный год 29 февраля превращается в 28-е
    assert occurs_on(2, 29, date(2025, 2, 28))
    assert not occurs_on(2, 29, date(2025, 2, 27))
    assert occurs_on(2, 29, date(2028, 2, 29))
    assert next_occurrence(29, 2, date(2025, 3, 1)) == date(2026, 2, 28)


def test_russian_formatting() -> None:
    assert plural(1, "день", "дня", "дней") == "день"
    assert plural(2, "день", "дня", "дней") == "дня"
    assert plural(5, "день", "дня", "дней") == "дней"
    assert plural(11, "день", "дня", "дней") == "дней"
    assert plural(21, "день", "дня", "дней") == "день"
    assert human_days(0) == "сегодня"
    assert human_days(1) == "завтра"
    assert human_days(3) == "через 3 дня"
    assert human_days(12) == "через 12 дней"
    assert format_age(30) == "30 лет"
    assert user_mention("Иван", 42) == '<a href="tg://user?id=42">Иван</a>'
    assert user_mention("<Иван>", None) == "<b>&lt;Иван&gt;</b>"


def test_normalize_time() -> None:
    assert normalize_time("9:05") == "09:05"
    assert normalize_time(" 23:59 ") == "23:59"
    assert normalize_time("24:00") is None
    assert normalize_time("12:60") is None
    assert normalize_time("abc") is None


def test_reminder_texts() -> None:
    record = {"name": "Иван", "user_id": 42, "day": 12, "month": 5, "year": 1990}
    text = reminders.today_message([record], date(2026, 5, 12))
    assert "Сегодня день рождения" in text
    assert 'tg://user?id=42' in text
    assert "36 лет" in text

    without_year = {"name": "Мария", "user_id": None, "day": 13, "month": 5, "year": None}
    text = reminders.tomorrow_message([without_year], date(2026, 5, 12))
    assert "Завтра день рождения" in text
    assert "Мария" in text
    assert "13.05" in text


# --------------------------------------------------------------------------- #
# БД
# --------------------------------------------------------------------------- #
def test_database_flow(tmp_path) -> None:
    asyncio.run(_database_flow(tmp_path))


async def _database_flow(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"), "Europe/Moscow", "09:00")
    await db.connect()
    try:
        chat = await db.ensure_chat(-100500, "Тестовый чат")
        assert chat["timezone"] == "Europe/Moscow"
        assert chat["reminder_time"] == "09:00"
        assert chat["topic_id"] is None
        assert chat["reminders_enabled"] == 1

        await db.update_chat(-100500, topic_id=77, reminder_time="08:30")
        chat = await db.get_chat(-100500)
        assert chat is not None
        assert chat["topic_id"] == 77
        assert chat["reminder_time"] == "08:30"

        birthday_id = await db.add_birthday(-100500, "Иван", 12, 5, 1990, user_id=42, created_by=1)
        assert await db.find_duplicate(-100500, "иван", 12, 5, None) is not None
        assert await db.find_duplicate(-100500, "Пётр", 12, 5, None) is None

        rows = await db.list_birthdays(-100500)
        assert len(rows) == 1
        assert rows[0]["name"] == "Иван"

        found = await db.search_birthdays(-100500, "Ива")
        assert [row["id"] for row in found] == [birthday_id]

        assert await db.claim_reminder(-100500, birthday_id, "today", "2026-05-12")
        assert not await db.claim_reminder(-100500, birthday_id, "today", "2026-05-12")
        assert await db.reminded_ids(-100500, "today", "2026-05-12") == {birthday_id}

        await db.delete_birthday(birthday_id)
        assert await db.list_birthdays(-100500) == []
        assert await db.reminded_ids(-100500, "today", "2026-05-12") == set()
    finally:
        await db.close()


# --------------------------------------------------------------------------- #
# Планировщик
# --------------------------------------------------------------------------- #
def test_is_due_window() -> None:
    tz = ZoneInfo("Europe/Moscow")
    at_nine = datetime(2026, 5, 12, 9, 0, tzinfo=tz)
    assert scheduler._is_due(at_nine, "09:00")
    assert scheduler._is_due(at_nine.replace(hour=10, minute=30), "09:00")
    assert not scheduler._is_due(at_nine.replace(hour=12, minute=5), "09:00")
    assert not scheduler._is_due(at_nine.replace(hour=8, minute=59), "09:00")


def test_scheduler_sends_once(tmp_path) -> None:
    asyncio.run(_scheduler_case(tmp_path))


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_thread_id=kwargs.get("message_thread_id"))


async def _scheduler_case(tmp_path) -> None:
    db = Database(str(tmp_path / "sched.db"), "Europe/Moscow", "09:00")
    await db.connect()
    try:
        await db.ensure_chat(-100700, "Тест")
        await db.update_chat(-100700, topic_id=77)
        await db.add_birthday(-100700, "Иван", 12, 5, 1990)
        await db.add_birthday(-100700, "Мария", 13, 5, None)

        bot = _FakeBot()
        now = datetime(2026, 5, 12, 6, 0, tzinfo=UTC)  # 09:00 МСК

        await scheduler._run_tick(bot, db, now)
        assert len(bot.sent) == 2  # «сегодня» + «завтра»
        assert all(message["message_thread_id"] == 77 for message in bot.sent)
        assert "Иван" in bot.sent[0]["text"]
        assert "Мария" in bot.sent[1]["text"]

        # Повторный тик в тот же день ничего не отправляет
        await scheduler._run_tick(bot, db, now)
        assert len(bot.sent) == 2

        # Другой день — снова одно напоминание
        next_day = now.replace(day=13)
        await scheduler._run_tick(bot, db, next_day)
        assert len(bot.sent) == 3
        assert "Мария" in bot.sent[2]["text"]
    finally:
        await db.close()

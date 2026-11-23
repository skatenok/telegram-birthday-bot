"""Хранилище на SQLite (aiosqlite)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id           INTEGER PRIMARY KEY,
    title             TEXT,
    timezone          TEXT    NOT NULL DEFAULT 'Europe/Moscow',
    reminder_time     TEXT    NOT NULL DEFAULT '09:00',
    day_before        INTEGER NOT NULL DEFAULT 1,
    reminders_enabled INTEGER NOT NULL DEFAULT 1,
    topic_id          INTEGER,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS birthdays (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    day        INTEGER NOT NULL CHECK (day BETWEEN 1 AND 31),
    month      INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    year       INTEGER,
    user_id    INTEGER,
    created_by INTEGER,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_birthdays_chat ON birthdays(chat_id);
CREATE INDEX IF NOT EXISTS idx_birthdays_month_day ON birthdays(month, day);

CREATE TABLE IF NOT EXISTS sent_reminders (
    chat_id     INTEGER NOT NULL,
    birthday_id INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    sent_on     TEXT    NOT NULL,
    PRIMARY KEY (chat_id, birthday_id, kind, sent_on)
);

CREATE INDEX IF NOT EXISTS idx_sent_day ON sent_reminders(sent_on);
"""

_EDITABLE_CHAT_FIELDS = {
    "title",
    "timezone",
    "reminder_time",
    "day_before",
    "reminders_enabled",
    "topic_id",
}


class Database:
    def __init__(
        self,
        path: str,
        default_timezone: str = "Europe/Moscow",
        default_reminder_time: str = "09:00",
    ) -> None:
        self.path = path
        self.default_timezone = default_timezone
        self.default_reminder_time = default_reminder_time
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------ #
    # Подключение
    # ------------------------------------------------------------------ #
    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База данных ещё не подключена")
        return self._conn

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        log.info("База данных готова: %s", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cursor = await self.conn.execute(sql, params)
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row is not None else None

    async def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ #
    # Чаты
    # ------------------------------------------------------------------ #
    async def ensure_chat(self, chat_id: int, title: str | None = None) -> dict[str, Any]:
        await self.conn.execute(
            """
            INSERT INTO chats (chat_id, title, timezone, reminder_time)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = COALESCE(excluded.title, chats.title)
            """,
            (chat_id, title, self.default_timezone, self.default_reminder_time),
        )
        await self.conn.commit()
        chat = await self.get_chat(chat_id)
        assert chat is not None
        return chat

    async def get_chat(self, chat_id: int) -> dict[str, Any] | None:
        return await self._fetchone("SELECT * FROM chats WHERE chat_id = ?", (chat_id,))

    async def update_chat(self, chat_id: int, **fields: Any) -> None:
        unknown = set(fields) - _EDITABLE_CHAT_FIELDS
        if unknown:
            raise ValueError(f"Недопустимые поля чата: {', '.join(sorted(unknown))}")
        if not fields:
            return

        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = tuple(fields.values()) + (chat_id,)
        await self.conn.execute(
            f"UPDATE chats SET {assignments}, updated_at = datetime('now') WHERE chat_id = ?",
            values,
        )
        await self.conn.commit()

    async def list_chats_for_scheduler(self) -> list[dict[str, Any]]:
        return await self._fetchall("SELECT * FROM chats WHERE reminders_enabled = 1")

    # ------------------------------------------------------------------ #
    # Дни рождения
    # ------------------------------------------------------------------ #
    async def add_birthday(
        self,
        chat_id: int,
        name: str,
        day: int,
        month: int,
        year: int | None = None,
        user_id: int | None = None,
        created_by: int | None = None,
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO birthdays (chat_id, name, day, month, year, user_id, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (chat_id, name, day, month, year, user_id, created_by),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def get_birthday(self, birthday_id: int) -> dict[str, Any] | None:
        return await self._fetchone("SELECT * FROM birthdays WHERE id = ?", (birthday_id,))

    async def list_birthdays(self, chat_id: int) -> list[dict[str, Any]]:
        return await self._fetchall(
            "SELECT * FROM birthdays WHERE chat_id = ? ORDER BY month, day, name",
            (chat_id,),
        )

    async def find_duplicate(
        self, chat_id: int, name: str, day: int, month: int, user_id: int | None
    ) -> dict[str, Any] | None:
        rows = await self._fetchall(
            "SELECT * FROM birthdays WHERE chat_id = ? AND day = ? AND month = ?",
            (chat_id, day, month),
        )
        lowered = name.casefold()
        for row in rows:
            if user_id is not None and row["user_id"] == user_id:
                return row
            if row["name"].casefold() == lowered:
                return row
        return None

    async def search_birthdays(self, chat_id: int, query: str) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT * FROM birthdays
            WHERE chat_id = ? AND name LIKE ? COLLATE NOCASE
            ORDER BY month, day, name
            """,
            (chat_id, f"%{query}%"),
        )

    async def delete_birthday(self, birthday_id: int) -> None:
        await self.conn.execute("DELETE FROM sent_reminders WHERE birthday_id = ?", (birthday_id,))
        await self.conn.execute("DELETE FROM birthdays WHERE id = ?", (birthday_id,))
        await self.conn.commit()

    # ------------------------------------------------------------------ #
    # Отметки об отправленных напоминаниях
    # ------------------------------------------------------------------ #
    async def claim_reminder(
        self, chat_id: int, birthday_id: int, kind: str, sent_on: str
    ) -> bool:
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO sent_reminders (chat_id, birthday_id, kind, sent_on)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, birthday_id, kind, sent_on),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def reminded_ids(self, chat_id: int, kind: str, sent_on: str) -> set[int]:
        rows = await self._fetchall(
            "SELECT birthday_id FROM sent_reminders WHERE chat_id = ? AND kind = ? AND sent_on = ?",
            (chat_id, kind, sent_on),
        )
        return {int(row["birthday_id"]) for row in rows}

    async def cleanup_sent(self, keep_days: int = 90) -> None:
        await self.conn.execute(
            "DELETE FROM sent_reminders WHERE sent_on < date('now', ?)",
            (f"-{int(keep_days)} days",),
        )
        await self.conn.commit()

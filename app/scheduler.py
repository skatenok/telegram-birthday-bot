"""Планировщик напоминаний: раз в 20 секунд проверяем, кому пора напомнить."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from . import notifier
from .db import Database
from .reminders import today_message, tomorrow_message
from .utils import occurs_on

log = logging.getLogger(__name__)

TICK_SECONDS = 20
# Если бот был перезапущен после времени напоминания, он всё ещё отправит
# сообщение, пока не прошло это окно.
CATCH_UP_WINDOW = timedelta(hours=3)


def _is_due(local_now: datetime, reminder_time: str) -> bool:
    hours, minutes = (int(part) for part in reminder_time.split(":"))
    due = local_now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    return due <= local_now <= due + CATCH_UP_WINDOW


async def scheduler_loop(bot: Bot, db: Database) -> None:
    log.info("Планировщик напоминаний запущен (проверка каждые %s c)", TICK_SECONDS)
    last_cleanup: date | None = None

    while True:
        try:
            now_utc = datetime.now(UTC)
            await _run_tick(bot, db, now_utc)

            if last_cleanup != now_utc.date():
                await db.cleanup_sent(keep_days=90)
                last_cleanup = now_utc.date()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка в планировщике напоминаний")

        await asyncio.sleep(TICK_SECONDS)


async def _run_tick(bot: Bot, db: Database, now_utc: datetime) -> None:
    chats = await db.list_chats_for_scheduler()

    for chat in chats:
        try:
            tzinfo = ZoneInfo(chat["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            log.warning(
                "Неизвестный часовой пояс %r у чата %s — пропускаю",
                chat["timezone"],
                chat["chat_id"],
            )
            continue

        local_now = now_utc.astimezone(tzinfo)
        if not _is_due(local_now, chat["reminder_time"]):
            continue

        try:
            await _process_chat(bot, db, chat, local_now)
        except TelegramForbiddenError:
            log.warning(
                "Бот больше не может писать в чат %s — напоминания отключены", chat["chat_id"]
            )
            await db.update_chat(chat["chat_id"], reminders_enabled=0)
        except Exception:
            log.exception("Не удалось обработать напоминания для чата %s", chat["chat_id"])


async def _process_chat(
    bot: Bot,
    db: Database,
    chat: dict,
    local_now: datetime,
) -> None:
    chat_id = int(chat["chat_id"])
    today = local_now.date()
    tomorrow = today + timedelta(days=1)

    birthdays = await db.list_birthdays(chat_id)
    if not birthdays:
        return

    today_items = [item for item in birthdays if occurs_on(item["month"], item["day"], today)]
    tomorrow_items = (
        [item for item in birthdays if occurs_on(item["month"], item["day"], tomorrow)]
        if chat["day_before"]
        else []
    )

    sent_today = await db.reminded_ids(chat_id, "today", today.isoformat())
    sent_tomorrow = await db.reminded_ids(chat_id, "tomorrow", today.isoformat())
    today_items = [item for item in today_items if int(item["id"]) not in sent_today]
    tomorrow_items = [item for item in tomorrow_items if int(item["id"]) not in sent_tomorrow]

    if today_items:
        text = today_message(today_items, today)
        await _deliver(bot, chat_id, text, chat.get("topic_id"))
        for item in today_items:
            await db.claim_reminder(chat_id, int(item["id"]), "today", today.isoformat())

    if tomorrow_items:
        text = tomorrow_message(tomorrow_items, today)
        await _deliver(bot, chat_id, text, chat.get("topic_id"))
        for item in tomorrow_items:
            await db.claim_reminder(chat_id, int(item["id"]), "tomorrow", today.isoformat())


async def _deliver(
    bot: Bot,
    chat_id: int,
    text: str,
    topic_id: int | None,
) -> None:
    try:
        await notifier.send_to_chat(bot, chat_id, text, topic_id)
    except TelegramForbiddenError:
        raise
    except Exception:
        # Не спамим ошибкой каждые 20 секунд: пропускаем это напоминание.
        log.exception("Не удалось отправить напоминание в чат %s", chat_id)

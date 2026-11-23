"""Точка входа: запуск бота и планировщика напоминаний."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)

from .config import load_config
from .db import Database
from .handlers import birthdays, common, settings
from .middlewares import ClearStateOnCommand
from .scheduler import scheduler_loop

log = logging.getLogger(__name__)

GROUP_COMMANDS = [
    BotCommand(command="add", description="Добавить день рождения"),
    BotCommand(command="list", description="Список дней рождения"),
    BotCommand(command="today", description="У кого день рождения сегодня"),
    BotCommand(command="del", description="Удалить запись"),
    BotCommand(command="settings", description="Настройки напоминаний"),
    BotCommand(command="topic", description="Топик для напоминаний"),
    BotCommand(command="time", description="Время напоминания"),
    BotCommand(command="tz", description="Часовой пояс"),
    BotCommand(command="daybefore", description="Предупреждать за день"),
    BotCommand(command="reminders", description="Включить/выключить напоминания"),
    BotCommand(command="test", description="Проверить отправку"),
    BotCommand(command="help", description="Справка"),
]

PRIVATE_COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="help", description="Как добавить бота в группу"),
]


async def _setup_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    except Exception:
        log.exception("Не удалось обновить список команд")


async def main() -> None:
    config = load_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    db = Database(
        path=config.db_path,
        default_timezone=config.default_timezone,
        default_reminder_time=config.default_reminder_time,
    )
    await db.connect()

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(db=db, config=config)
    dispatcher.message.outer_middleware(ClearStateOnCommand())
    dispatcher.include_routers(common.router, birthdays.router, settings.router)

    await _setup_commands(bot)
    me = await bot.get_me()
    log.info("Бот запущен: @%s (id %s)", me.username, me.id)
    log.info(
        "Часовой пояс по умолчанию: %s, время напоминаний: %s",
        config.default_timezone,
        config.default_reminder_time,
    )

    scheduler_task = asyncio.create_task(scheduler_loop(bot, db), name="reminders-scheduler")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        scheduler_task.cancel()
        await asyncio.gather(scheduler_task, return_exceptions=True)
        await db.close()
        await bot.session.close()
        log.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

"""Загрузка конфигурации из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .utils import normalize_time, validate_timezone


@dataclass(frozen=True)
class Config:
    bot_token: str
    db_path: str
    default_timezone: str
    default_reminder_time: str
    log_level: str


def _read_token() -> str:
    token = os.getenv("BOT_TOKEN", "").strip()
    token_file = os.getenv("BOT_TOKEN_FILE", "").strip()
    if not token and token_file:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    return token


def load_config() -> Config:
    token = _read_token()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN не задан. Добавьте его в .env (см. .env.example) "
            "или передайте через переменную окружения."
        )

    timezone_name = os.getenv("DEFAULT_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
    if not validate_timezone(timezone_name):
        raise RuntimeError(f"Неизвестный часовой пояс в DEFAULT_TIMEZONE: {timezone_name}")

    reminder_time = normalize_time(os.getenv("DEFAULT_REMINDER_TIME", "09:00"))
    if reminder_time is None:
        raise RuntimeError(
            "DEFAULT_REMINDER_TIME должен быть в формате ЧЧ:ММ (например 09:00)."
        )

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"

    return Config(
        bot_token=token,
        db_path=os.getenv("DB_PATH", "data/birthdays.db").strip() or "data/birthdays.db",
        default_timezone=timezone_name,
        default_reminder_time=reminder_time,
        log_level=log_level,
    )

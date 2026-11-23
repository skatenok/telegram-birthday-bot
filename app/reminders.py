"""Формирование текстов напоминаний."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .utils import escape, format_age, format_ddmm, user_mention


def _person(record: dict[str, Any], on_date: date, with_age: bool = True) -> str:
    name = user_mention(record["name"], record.get("user_id"))
    year = record.get("year")
    if not with_age or year is None:
        return name

    age = on_date.year - int(year)
    if age < 1 or age > 150:
        return name
    return f"{name} — исполняется {format_age(age)}"


def today_message(items: list[dict[str, Any]], today: date) -> str:
    lines = ["🎉 <b>Сегодня день рождения!</b>", ""]
    for record in items:
        lines.append(f"🎂 {_person(record, today)}")
    lines.append("")
    lines.append("Не забудьте поздравить 🥳")
    return "\n".join(lines)


def tomorrow_message(items: list[dict[str, Any]], today: date) -> str:
    tomorrow = today + timedelta(days=1)
    lines = ["🔔 <b>Завтра день рождения</b>", ""]
    for record in items:
        date_part = f" <i>({format_ddmm(record['day'], record['month'])})</i>"
        lines.append(f"🎂 {_person(record, tomorrow)}{date_part}")
    return "\n".join(lines)


def test_message(chat_title: str | None = None) -> str:
    title = f" в чате «{escape(chat_title)}»" if chat_title else ""
    return (
        "🔔 <b>Проверка напоминаний</b>\n\n"
        f"Всё работает: напоминания{title} будут приходить сюда.\n\n"
        "🎂 <b>Иван</b> — исполняется 30 лет\n"
        "<i>Это тестовое сообщение</i>"
    )

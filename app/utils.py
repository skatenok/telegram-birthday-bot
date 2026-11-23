"""Даты, склонения, форматирование и мелочи."""

from __future__ import annotations

import calendar
import html
import re
from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS_NOMINATIVE = [
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
]

MONTHS_GENITIVE = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]

_MONTH_LOOKUP: dict[str, int] = {}
for _index, (_nominative, _genitive) in enumerate(
    zip(MONTHS_NOMINATIVE, MONTHS_GENITIVE), start=1
):
    _MONTH_LOOKUP[_nominative] = _index
    _MONTH_LOOKUP[_genitive] = _index

_DATE_NUMERIC_RE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?$")
_DATE_WORDS_RE = re.compile(r"^(\d{1,2})\s+([а-яё]+)(?:\s+(\d{2,4}))?$")
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# --------------------------------------------------------------------------- #
# Даты
# --------------------------------------------------------------------------- #
def _normalize_year(value: str | None) -> int | None:
    if value is None:
        return None
    year = int(value)
    if len(value) <= 2:
        current_short = date.today().year % 100
        year = 2000 + year if year <= current_short else 1900 + year
    return year


def parse_date(value: str) -> tuple[int, int, int | None] | None:
    """Разбирает дату рождения.

    Поддерживаются форматы: 12.05, 12.05.1990, 12/05/1990, 12-05-1990,
    12 мая, 12 мая 1990, 12 мая 1990г.
    Возвращает (день, месяц, год | None) или None.
    """
    if not value:
        return None

    text = value.strip().lower()
    text = re.sub(r"\s*г(?:ода|од)?\.?\s*$", "", text)
    text = text.replace(",", " ").strip()
    text = re.sub(r"\s+", " ", text)

    match = _DATE_NUMERIC_RE.fullmatch(text)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = _normalize_year(match.group(3))
    else:
        match = _DATE_WORDS_RE.fullmatch(text)
        if not match:
            return None
        day = int(match.group(1))
        month = _MONTH_LOOKUP.get(match.group(2).rstrip("."))
        if month is None:
            return None
        year = _normalize_year(match.group(3))

    if not 1 <= month <= 12:
        return None
    # Без года принимаем 29.02; с годом — проверяем реальную дату
    reference_year = year if year is not None else 2000
    if not 1 <= day <= calendar.monthrange(reference_year, month)[1]:
        return None
    if year is not None and not 1900 <= year <= date.today().year:
        return None

    return day, month, year


def safe_date(year: int, month: int, day: int) -> date:
    """Дата с «мягким» 29 февраля: в невисокосный год — 28 февраля."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def next_occurrence(day: int, month: int, today: date) -> date:
    occurrence = safe_date(today.year, month, day)
    if occurrence < today:
        occurrence = safe_date(today.year + 1, month, day)
    return occurrence


def days_until(day: int, month: int, today: date) -> int:
    return (next_occurrence(day, month, today) - today).days


def occurs_on(month: int, day: int, on_date: date) -> bool:
    return safe_date(on_date.year, month, day) == on_date


def local_today(timezone_name: str) -> date:
    return datetime.now(ZoneInfo(timezone_name)).date()


# --------------------------------------------------------------------------- #
# Форматирование
# --------------------------------------------------------------------------- #
def plural(number: int, one: str, few: str, many: str) -> str:
    number = abs(int(number))
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def human_days(number: int) -> str:
    if number == 0:
        return "сегодня"
    if number == 1:
        return "завтра"
    return f"через {number} {plural(number, 'день', 'дня', 'дней')}"


def format_ddmm(day: int, month: int) -> str:
    return f"{day:02d}.{month:02d}"


def format_date(day: int, month: int, year: int | None) -> str:
    if year is None:
        return format_ddmm(day, month)
    return f"{day:02d}.{month:02d}.{year}"


def format_age(age: int) -> str:
    return f"{age} {plural(age, 'год', 'года', 'лет')}"


def escape(text: str) -> str:
    return html.escape(str(text), quote=False)


def user_mention(name: str, user_id: int | None) -> str:
    safe_name = escape(name)
    if user_id:
        return f'<a href="tg://user?id={int(user_id)}">{safe_name}</a>'
    return f"<b>{safe_name}</b>"


def validate_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False
    return True


def normalize_time(value: str | None) -> str | None:
    if not value:
        return None
    match = _TIME_RE.fullmatch(value.strip())
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def chunk_lines(lines: Sequence[str], limit: int = 3800) -> list[str]:
    """Склеивает строки в сообщения, не превышающие лимит Telegram."""
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0

    for line in lines:
        line_size = len(line) + 1
        if current and current_size + line_size > limit:
            chunks.append("\n".join(current))
            current = []
            current_size = 0
        current.append(line)
        current_size += line_size

    if current:
        chunks.append("\n".join(current))

    return chunks


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalize_name(value: str) -> str:
    name = re.sub(r"\s+", " ", value).strip()
    return truncate(name, 64)

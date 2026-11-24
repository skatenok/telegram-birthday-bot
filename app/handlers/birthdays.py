"""Команды работы с днями рождения."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from ..db import KIND_PERSON, KIND_PET, Database
from ..notifier import send_long_text
from ..permissions import can_manage_chat
from ..reminders import pet_today_message, today_message
from ..utils import (
    days_until,
    escape,
    format_age,
    format_date,
    format_ddmm,
    human_days,
    local_today,
    next_occurrence,
    normalize_name,
    occurs_on,
    parse_date,
    plural,
)

router = Router(name="birthdays")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

MAX_ENTRIES_IN_LIST = 100


class AddBirthday(StatesGroup):
    waiting_name = State()
    waiting_date = State()


class AddPet(StatesGroup):
    waiting_name = State()
    waiting_date = State()


def _split_name_and_date(args: str) -> tuple[str, tuple[int, int, int | None] | None]:
    """Из строки «Иван 12.05.1990» достаёт имя и дату."""
    tokens = args.split()
    for take in (3, 2, 1):
        if len(tokens) < take:
            continue
        parsed = parse_date(" ".join(tokens[-take:]))
        if parsed is not None:
            return " ".join(tokens[:-take]).strip(), parsed
    return args.strip(), None


def _reply_target(message: Message):
    reply = message.reply_to_message
    if reply is None or reply.from_user is None or reply.from_user.is_bot:
        return None
    return reply.from_user


async def _save_birthday(
    db: Database,
    chat: dict,
    name: str,
    day: int,
    month: int,
    year: int | None,
    user_id: int | None,
    created_by: int | None,
    kind: str = KIND_PERSON,
) -> str:
    duplicate = await db.find_duplicate(
        int(chat["chat_id"]), name, day, month, kind=kind
    )
    if duplicate:
        return (
            "⚠️ Такая запись уже есть: "
            f"<b>{escape(duplicate['name'])}</b> — "
            f"{format_date(duplicate['day'], duplicate['month'], duplicate['year'])} "
            f"(<code>#{duplicate['id']}</code>)"
        )

    birthday_id = await db.add_birthday(
        chat_id=int(chat["chat_id"]),
        name=name,
        day=day,
        month=month,
        year=year,
        user_id=user_id,
        created_by=created_by,
        kind=kind,
    )

    today = local_today(chat["timezone"])
    days = days_until(day, month, today)
    occurrence = next_occurrence(day, month, today)

    if chat["topic_id"]:
        where = f"в топик <code>{chat['topic_id']}</code>"
    else:
        where = "в общий чат"

    if kind == KIND_PET:
        intro = f"✅ Записал питомца: <b>{escape(name)}</b>"
    else:
        intro = f"✅ Записал: <b>{escape(name)}</b>"

    text = (
        f"{intro} — {format_date(day, month, year)} (<code>#{birthday_id}</code>)\n"
        f"Ближайший день рождения: {occurrence.strftime('%d.%m.%Y')} — {human_days(days)}.\n"
        f"Напоминание придёт {where} в {chat['reminder_time']} ({chat['timezone']})."
    )
    if not chat["reminders_enabled"]:
        text += "\n\n⚠️ Напоминания в этом чате отключены — включите командой <code>/reminders on</code>."

    return text


async def _handle_pet_add(
    message: Message,
    args: str,
    state: FSMContext,
    db: Database,
) -> None:
    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    args = args.strip()

    if not args:
        await state.set_state(AddPet.waiting_name)
        await message.reply(
            "Как зовут питомца? Напишите кличку.\n"
            "Дату спрошу следующим сообщением. Отмена — /cancel"
        )
        return

    name, parsed = _split_name_and_date(args)

    if parsed is None:
        # Только кличка — спросим дату отдельным шагом
        if not any(character.isdigit() for character in args):
            normalized = normalize_name(args)
            if normalized:
                await state.update_data(pet_name=normalized)
                await state.set_state(AddPet.waiting_date)
                await message.reply(
                    f"Записываю питомца «{escape(normalized)}».\n"
                    "Теперь дата рождения: <code>12.05</code> или <code>12.05.2020</code>.\n"
                    "Можно словами: <code>12 мая 2020</code>. Отмена — /cancel"
                )
                return

        await message.reply(
            "Не вижу дату 🤔\n\n"
            "Так: <code>/addpet Барсик 12.05.2020</code>\n"
            "Можно без года: <code>/addpet Барсик 12.05</code>"
        )
        return

    name = normalize_name(name)
    if not name:
        await message.reply(
            "Укажите кличку: <code>/addpet Барсик 12.05.2020</code>"
        )
        return

    day, month, year = parsed
    response = await _save_birthday(
        db,
        chat,
        name,
        day,
        month,
        year,
        None,
        message.from_user.id if message.from_user else None,
        kind=KIND_PET,
    )
    await message.reply(response)


@router.message(Command("add", "добавить"))
async def cmd_add(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    db: Database,
) -> None:
    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    target = _reply_target(message)
    args = (command.args or "").strip()

    # Поддержка варианта «/add pet Барсик 12.05.2020»
    if args:
        first, _, rest = args.partition(" ")
        if first.casefold() in {"pet", "питомец"}:
            await _handle_pet_add(message, rest, state, db)
            return

    if not args and target is None:
        await state.set_state(AddBirthday.waiting_name)
        await message.reply(
            "Кого записываем? Напишите имя.\n"
            "Дату спрошу следующим сообщением. Отмена — /cancel"
        )
        return

    name, parsed = _split_name_and_date(args)

    if parsed is None:
        # Похоже, человек написал только имя — спросим дату отдельным шагом
        if args and not any(character.isdigit() for character in args):
            normalized = normalize_name(args)
            if normalized:
                await state.update_data(name=normalized)
                await state.set_state(AddBirthday.waiting_date)
                await message.reply(
                    f"Записываю «{escape(normalized)}».\n"
                    "Теперь дата рождения: <code>12.05</code> или <code>12.05.1990</code>.\n"
                    "Можно словами: <code>12 мая 1990</code>. Отмена — /cancel"
                )
                return

        await message.reply(
            "Не вижу дату 🤔\n\n"
            "Так: <code>/add Иван 12.05.1990</code>\n"
            "Или так: <code>/add 12.05</code> — ответом на сообщение человека.\n"
            "Можно без года: <code>/add Иван 12.05</code>"
        )
        return

    if not name:
        if target is None:
            await message.reply(
                "Укажите имя: <code>/add Иван 12.05.1990</code>"
            )
            return
        name = target.full_name

    name = normalize_name(name)
    if not name:
        await message.reply("Имя не должно быть пустым.")
        return

    day, month, year = parsed
    response = await _save_birthday(
        db,
        chat,
        name,
        day,
        month,
        year,
        target.id if target else None,
        message.from_user.id if message.from_user else None,
    )
    await message.reply(response)


@router.message(AddBirthday.waiting_name, F.text, ~F.text.startswith("/"))
async def add_flow_name(message: Message, state: FSMContext, db: Database) -> None:
    text = (message.text or "").strip()
    name, parsed = _split_name_and_date(text)

    if parsed is not None and name:
        chat = await db.ensure_chat(message.chat.id, message.chat.title)
        day, month, year = parsed
        response = await _save_birthday(
            db,
            chat,
            normalize_name(name),
            day,
            month,
            year,
            None,
            message.from_user.id if message.from_user else None,
        )
        await state.clear()
        await message.reply(response)
        return

    name = normalize_name(text)
    if not name:
        await message.reply("Имя не должно быть пустым. Напишите ещё раз или /cancel")
        return

    await state.update_data(name=name)
    await state.set_state(AddBirthday.waiting_date)
    await message.reply(
        f"Записываю «{escape(name)}».\n"
        "Теперь дата рождения: <code>12.05</code> или <code>12.05.1990</code>.\n"
        "Можно словами: <code>12 мая 1990</code>. Отмена — /cancel"
    )


@router.message(AddBirthday.waiting_date, F.text, ~F.text.startswith("/"))
async def add_flow_date(message: Message, state: FSMContext, db: Database) -> None:
    parsed = parse_date((message.text or "").strip())
    if parsed is None:
        await message.reply(
            "Не получилось разобрать дату 🤔\n"
            "Примеры: <code>12.05</code>, <code>12.05.1990</code>, <code>12 мая 1990</code>.\n"
            "Отмена — /cancel"
        )
        return

    data = await state.get_data()
    name = data.get("name")
    if not name:
        await state.clear()
        await message.reply("Что-то потерялось, начните заново: /add")
        return

    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    day, month, year = parsed
    response = await _save_birthday(
        db,
        chat,
        name,
        day,
        month,
        year,
        None,
        message.from_user.id if message.from_user else None,
    )
    await state.clear()
    await message.reply(response)


@router.message(Command("addpet", "add_pet", "питомец"))
async def cmd_add_pet(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    db: Database,
) -> None:
    await _handle_pet_add(message, command.args or "", state, db)


@router.message(AddPet.waiting_name, F.text, ~F.text.startswith("/"))
async def pet_flow_name(message: Message, state: FSMContext, db: Database) -> None:
    text = (message.text or "").strip()
    name, parsed = _split_name_and_date(text)

    if parsed is not None and name:
        chat = await db.ensure_chat(message.chat.id, message.chat.title)
        day, month, year = parsed
        response = await _save_birthday(
            db,
            chat,
            normalize_name(name),
            day,
            month,
            year,
            None,
            message.from_user.id if message.from_user else None,
            kind=KIND_PET,
        )
        await state.clear()
        await message.reply(response)
        return

    name = normalize_name(text)
    if not name:
        await message.reply("Кличка не должна быть пустой. Напишите ещё раз или /cancel")
        return

    await state.update_data(pet_name=name)
    await state.set_state(AddPet.waiting_date)
    await message.reply(
        f"Записываю питомца «{escape(name)}».\n"
        "Теперь дата рождения: <code>12.05</code> или <code>12.05.2020</code>.\n"
        "Можно словами: <code>12 мая 2020</code>. Отмена — /cancel"
    )


@router.message(AddPet.waiting_date, F.text, ~F.text.startswith("/"))
async def pet_flow_date(message: Message, state: FSMContext, db: Database) -> None:
    parsed = parse_date((message.text or "").strip())
    if parsed is None:
        await message.reply(
            "Не получилось разобрать дату 🤔\n"
            "Примеры: <code>12.05</code>, <code>12.05.2020</code>, <code>12 мая 2020</code>.\n"
            "Отмена — /cancel"
        )
        return

    data = await state.get_data()
    name = data.get("pet_name")
    if not name:
        await state.clear()
        await message.reply("Что-то потерялось, начните заново: /addpet")
        return

    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    day, month, year = parsed
    response = await _save_birthday(
        db,
        chat,
        name,
        day,
        month,
        year,
        None,
        message.from_user.id if message.from_user else None,
        kind=KIND_PET,
    )
    await state.clear()
    await message.reply(response)


@router.message(Command("list", "birthdays", "список"))
async def cmd_list(message: Message, db: Database) -> None:
    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    records = await db.list_birthdays(message.chat.id)

    if not records:
        await message.reply(
            "Пока пусто 📭\nДобавьте первую запись: <code>/add Иван 12.05.1990</code>"
        )
        return

    today = local_today(chat["timezone"])
    records.sort(key=lambda row: (days_until(row["day"], row["month"], today), row["name"].casefold()))

    shown = records[:MAX_ENTRIES_IN_LIST]
    lines = [
        (
            f"🎂 <b>Дни рождения</b> — {len(records)} "
            f"{plural(len(records), 'запись', 'записи', 'записей')}"
        ),
        "",
    ]

    for index, record in enumerate(shown, start=1):
        days = days_until(record["day"], record["month"], today)
        if days == 0:
            when = "🎉 сегодня"
        elif days == 1:
            when = "завтра"
        else:
            when = f"через {days} {plural(days, 'день', 'дня', 'дней')}"

        age_part = ""
        if record["year"]:
            age = next_occurrence(record["day"], record["month"], today).year - int(record["year"])
            if 1 <= age <= 150:
                age_part = f" <i>({format_age(age)})</i>"

        pet_mark = "🐾 " if record.get("kind") == KIND_PET else ""
        lines.append(
            f"{index}. <b>{format_date(record['day'], record['month'], record['year'])}</b> — "
            f"{pet_mark}{escape(record['name'])}{age_part} · {when} · <code>#{record['id']}</code>"
        )

    if len(records) > MAX_ENTRIES_IN_LIST:
        lines.append("")
        lines.append(f"…и ещё {len(records) - MAX_ENTRIES_IN_LIST} записей.")

    lines.append("")
    lines.append("Удалить запись: <code>/del #id</code>")

    await send_long_text(message, lines)


@router.message(Command("today", "сегодня"))
async def cmd_today(message: Message, db: Database) -> None:
    chat = await db.ensure_chat(message.chat.id, message.chat.title)
    today = local_today(chat["timezone"])
    records = await db.list_birthdays(message.chat.id)
    todays = [row for row in records if occurs_on(row["month"], row["day"], today)]

    if not todays:
        await message.reply(f"Сегодня ({format_ddmm(today.day, today.month)}) дней рождения нет.")
        return

    people = [row for row in todays if row.get("kind") != KIND_PET]
    pets = [row for row in todays if row.get("kind") == KIND_PET]

    if people:
        await message.answer(today_message(people, today))
    if pets:
        await message.answer(pet_today_message(pets, today))


@router.message(Command("del", "delete", "удалить", "убрать"))
async def cmd_del(message: Message, command: CommandObject, bot: Bot, db: Database) -> None:
    await db.ensure_chat(message.chat.id, message.chat.title)
    query = (command.args or "").strip().lstrip("#")

    if not query:
        await message.reply(
            "Кого удаляем?\n"
            "• <code>/del 5</code> — по номеру записи\n"
            "• <code>/del Иван</code> — по имени\n"
            "Номера записей видны в /list"
        )
        return

    record = None
    if query.isdigit():
        candidate = await db.get_birthday(int(query))
        if candidate is not None and int(candidate["chat_id"]) == message.chat.id:
            record = candidate
        else:
            await message.reply(f"Запись <code>#{query}</code> не найдена в этом чате.")
            return
    else:
        matches = await db.search_birthdays(message.chat.id, query)
        if not matches:
            await message.reply(f"По запросу «{escape(query)}» ничего не нашлось. Список: /list")
            return
        if len(matches) > 1:
            lines = [f"Нашлось несколько записей по «{escape(query)}»:", ""]
            for row in matches:
                pet_mark = "🐾 " if row.get("kind") == KIND_PET else ""
                lines.append(
                    f"• <code>#{row['id']}</code> — {pet_mark}{escape(row['name'])} — "
                    f"{format_date(row['day'], row['month'], row['year'])}"
                )
            lines.append("")
            lines.append("Уточните: <code>/del #id</code>")
            await message.reply("\n".join(lines))
            return
        record = matches[0]

    await db.ensure_chat(message.chat.id, message.chat.title)
    author_id = message.from_user.id if message.from_user else None
    if record["created_by"] != author_id and not await can_manage_chat(bot, message):
        await message.reply(
            "Удалить запись может тот, кто её добавил, или администратор чата."
        )
        return

    await db.delete_birthday(int(record["id"]))
    await message.reply(
        f"🗑 Удалено: <b>{escape(record['name'])}</b> — "
        f"{format_date(record['day'], record['month'], record['year'])}"
    )

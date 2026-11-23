"""Middleware: команда сбрасывает незавершённый диалог."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, TelegramObject


class ClearStateOnCommand(BaseMiddleware):
    # Эти команды сами управляют состоянием и должны его увидеть
    _CANCEL_COMMANDS: ClassVar[set[str]] = {"cancel", "отмена"}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        state: FSMContext | None = data.get("state")
        if (
            state is not None
            and isinstance(event, Message)
            and event.text
            and event.text.startswith("/")
        ):
            command = event.text.split()[0].lstrip("/").split("@")[0].lower()
            if command not in self._CANCEL_COMMANDS:
                await state.clear()
        return await handler(event, data)

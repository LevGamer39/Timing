"""Небольшое persist-хранилище состояния бота.

JWT здесь не хранятся: бот всегда может получить новый по хешу MAX user_id.
В файле лежат только идентификаторы, роли и служебные отметки.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("timing_bot.state")


class StateStore:
    """JSON-файл с атомарной записью."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = {
            "users": {},
            "homework_chats": {},
            "sent_reminders": {},
        }

    async def load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Не удалось прочитать %s: %s", self._path, exc)
            return
        if not isinstance(raw, dict):
            return
        for key in self._data:
            if key in raw and isinstance(raw[key], type(self._data[key])):
                self._data[key] = raw[key]

    async def save(self) -> None:
        async with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)

    def user(self, max_user_id: int | str) -> dict[str, Any] | None:
        return self._data["users"].get(str(max_user_id))

    def upsert_user(self, max_user_id: int | str, **fields: Any) -> None:
        key = str(max_user_id)
        current = self._data["users"].setdefault(key, {})
        current.update(
            {name: value for name, value in fields.items() if value is not None}
        )
        current["max_user_id"] = int(max_user_id)

    def users(self) -> list[dict[str, Any]]:
        return list(self._data["users"].values())

    def is_homework_chat(self, chat_id: int | str) -> bool:
        chat = self._data["homework_chats"].get(str(chat_id))
        return bool(chat and chat.get("enabled"))

    def set_homework_chat(
        self, chat_id: int | str, enabled: bool, title: str | None = None
    ) -> None:
        entry = self._data["homework_chats"].setdefault(
            str(chat_id), {"enabled": enabled, "title": title or ""}
        )
        entry["enabled"] = enabled
        if title:
            entry["title"] = title

    def homework_chats(self) -> list[int]:
        return [
            int(chat_id)
            for chat_id, entry in self._data["homework_chats"].items()
            if entry.get("enabled")
        ]

    def was_sent(self, max_user_id: int | str, key: str) -> bool:
        return key in self._data["sent_reminders"].get(str(max_user_id), {})

    def mark_sent(self, max_user_id: int | str, key: str, value: str) -> None:
        bucket = self._data["sent_reminders"].setdefault(str(max_user_id), {})
        bucket[key] = value
        if len(bucket) > 500:
            for old_key in list(bucket)[: len(bucket) - 500]:
                bucket.pop(old_key, None)

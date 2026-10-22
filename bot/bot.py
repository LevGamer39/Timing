"""Точка входа MAX-бота VectorVK.

Бот использует библиотеку ``maxapi`` (pip install maxapi) — это аналог
aiogram для мессенджера MAX. Своего HTTP-клиента к MAX API здесь нет.

Режимы:
- ``long_polling`` — по умолчанию, удобно для разработки;
- ``webhook`` — боевой режим, требует HTTPS-URL и ``WEBHOOK_SECRET``.
"""

from __future__ import annotations

import asyncio
import logging

from maxapi import Bot, Dispatcher
from maxapi.enums.parse_mode import Format
from maxapi.types.command import BotCommand
from maxapi.webhook.aiohttp import AiohttpMaxWebhook

from api import Backend
from config import settings, validate
from handlers import register_handlers
from parser import HomeworkParser
from scheduler import Scheduler
from sessions import SessionManager
from state import StateStore

logger = logging.getLogger("vector_bot")

COMMANDS = [
    BotCommand(name="start", description="Главное меню"),
    BotCommand(name="today", description="Задания на сегодня"),
    BotCommand(name="add", description="Добавить задание"),
    BotCommand(name="grades", description="Оценки"),
    BotCommand(name="summary", description="Сводка успеваемости"),
    BotCommand(name="schedule", description="Расписание"),
    BotCommand(name="ai", description="ИИ-помощник"),
    BotCommand(name="done", description="Отметить выполненное"),
    BotCommand(name="hw", description="Парсер домашних заданий"),
    BotCommand(name="help", description="Помощь"),
]


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def main() -> None:
    validate()
    _configure_logging()

    bot = Bot(
        token=settings.max_bot_token,
        format=Format.HTML,
        parse_mode=Format.HTML,
    )
    dp = Dispatcher()
    backend = Backend()
    store = StateStore(settings.state_path)
    await store.load()

    sessions = SessionManager(backend, store)
    parser = HomeworkParser(bot, store)
    scheduler = Scheduler(bot, backend, sessions, store)

    register_handlers(dp, bot, backend, store, sessions, parser)
    await parser.start_existing()
    await scheduler.start()

    try:
        await bot.set_commands(*COMMANDS)
    except Exception:  # noqa: BLE001 — не критично для запуска
        logger.warning("Не удалось обновить список команд", exc_info=True)

    logger.info(
        "Бот запущен: mode=%s backend=%s model=%s",
        settings.update_mode,
        settings.backend_url,
        settings.ollama_model,
    )

    try:
        if settings.update_mode == "webhook":
            webhook = AiohttpMaxWebhook(
                dp=dp,
                bot=bot,
                secret=settings.webhook_secret or None,
            )
            await bot.subscribe_webhook(
                url=settings.webhook_url,
                secret=settings.webhook_secret or None,
            )
            logger.info("Webhook: %s", settings.webhook_url)
            await webhook.run(
                host=settings.webhook_host,
                port=settings.webhook_port,
                path=settings.webhook_path,
            )
        else:
            await dp.start_polling(bot)
    finally:
        await scheduler.stop()
        await parser.stop()
        await backend.aclose()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

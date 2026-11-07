"""Точка входа MAX-бота Timing."""

from __future__ import annotations

import asyncio
import logging

from maxapi import Bot, Dispatcher
from maxapi.enums.parse_mode import Format
from maxapi.types.command import BotCommand
from maxapi.webhook.aiohttp import AiohttpMaxWebhook

from config import settings, validate
from core.context import AppContext
from core.sessions import SessionManager
from core.state import StateStore
from routers import include_routers
from services.backend import Backend
from services.homework import HomeworkParser
from services.notifications import NotificationRelay
from services.reminders import ReminderScheduler
from services.voice import Transcriber

logger = logging.getLogger("timing_bot")

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
    BotCommand(name="notify", description="Уведомления в MAX"),
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
    backend.set_unauthorized_handler(sessions.refresh)
    transcriber = None
    if settings.voice_enabled:
        transcriber = Transcriber(
            model=settings.whisper_model,
            model_dir=settings.whisper_model_dir,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            language=settings.whisper_language,
            temp_dir=settings.audio_temp_dir,
            timeout=settings.voice_download_timeout,
        )
        try:
            await transcriber.warmup()
        except Exception:  # noqa: BLE001 — голосовые не критичны
            logger.warning("faster-whisper не загрузился", exc_info=True)
    else:
        logger.info("Голосовые отключены: VOICE_ENABLED=false")
    parser = HomeworkParser(bot, store, transcriber)
    scheduler = ReminderScheduler(bot, backend, sessions, store)
    relay = NotificationRelay(bot, backend, sessions, store)
    ctx = AppContext(
        bot=bot,
        backend=backend,
        store=store,
        sessions=sessions,
        parser=parser,
        transcriber=transcriber,
    )

    include_routers(dp, ctx)
    await parser.start_existing()
    await scheduler.start()
    await relay.start()

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
        await relay.stop()
        await scheduler.stop()
        await parser.stop()
        await backend.aclose()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

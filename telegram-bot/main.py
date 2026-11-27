import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from logging import getLogger
import os

import uvicorn
from fastapi import FastAPI
from telegram.ext import filters, CommandHandler, MessageHandler, CallbackQueryHandler

from bot import application
from bot.command_handlers import process_button, process_message
from database import initialize_db
from bot.commands.start import start
from bot.commands.logout import log_out_request
from bot.commands.menu import menu
from features.search import search_request
from features.summarize import summarize_request
from server.server_runner import create_api_handlers
from settings import settings
from utils.infinite_task import send_shutdown_notification
from utils.initialize_users import initialize_users
from utils.state import user_info
from utils.session import watch_idle_sessions

logger = getLogger(__name__)


@asynccontextmanager
async def run_bot(app: FastAPI) -> AsyncIterator[None]:
    await application.initialize()
    try:
        await initialize_users()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        session_watcher = asyncio.create_task(watch_idle_sessions())
        try:
            yield
        finally:
            session_watcher.cancel()
            with suppress(asyncio.CancelledError):
                await session_watcher
            try:
                await asyncio.wait_for(send_shutdown_notification(immediate=True), timeout=20)
            except Exception:
                logger.exception("Could not complete shutdown notifications")
    finally:
        try:
            if application.updater.running:
                await application.updater.stop()
            if application.running:
                await application.stop()
        finally:
            for user in list(user_info.values()):
                if user.client is not None:
                    try:
                        await user.client.disconnect()
                    except Exception:
                        logger.exception("Could not disconnect Telegram client for user %s", user.user_id)
            await application.shutdown()


app = FastAPI(lifespan=run_bot)

async def run_server() -> None:
    create_api_handlers(app)
    config = uvicorn.Config(
        app,
        host=os.getenv("COORDINATOR_BIND_HOST", "localhost"),
        port=8000,
        log_level="info",
        timeout_graceful_shutdown=10,
        ws_max_size=None,
    )
    server = uvicorn.Server(config=config)
    await server.serve()

async def main() -> None:
    await run_server()


if __name__ == "__main__":
    initialize_db()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("summarize", summarize_request))
    application.add_handler(CommandHandler("search", search_request))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("log_out", log_out_request))
    # application.add_handler(CommandHandler("shut_down", shut_down))
    application.add_handler(CallbackQueryHandler(process_button))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), process_message))

    asyncio.run(main())

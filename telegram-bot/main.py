import asyncio

import uvicorn
from fastapi import FastAPI
from telegram.ext import filters, CommandHandler, MessageHandler, CallbackQueryHandler

from bot import application
from bot.command_handlers import process_button, process_message
from bot.commands.shut_down import shut_down
from database import initialize_db
from bot.commands.start import start
from bot.commands.logout import log_out_request
from bot.commands.menu import menu
from features.search import search_request
from features.summarize import summarize_request
from server.server_runner import create_api_handlers
from settings import settings
from utils import infinite_task
from utils.initialize_users import initialize_users

app = FastAPI()

async def run_server():
    create_api_handlers(app)
    config = uvicorn.Config(
        app,
        host="localhost",
        port=8000,
        log_level="info"
    )
    server = uvicorn.Server(config=config)
    await server.serve()

    try:
        await infinite_task.infinite_task
    except asyncio.CancelledError:
        pass

async def run_bot():
    await initialize_users()
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    try:
        await infinite_task.infinite_task
    except asyncio.CancelledError:
        pass

    await application.updater.stop()
    await application.stop()
    await application.shutdown()

async def main():
    infinite_task.infinite_task = asyncio.create_task(infinite_task.get_infinite_task())
    try:
        await asyncio.gather(
            run_bot(),
            run_server()
        )
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    initialize_db()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("summarize", summarize_request))
    application.add_handler(CommandHandler("search", search_request))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("log_out", log_out_request))
    application.add_handler(CommandHandler("shut_down", shut_down))
    application.add_handler(CallbackQueryHandler(process_button))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), process_message))

    asyncio.run(main())
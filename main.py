import asyncio
from telegram.ext import filters, CommandHandler, MessageHandler, CallbackQueryHandler
from bot import application, process_button, process_message
from bot.commands.shut_down import shut_down
from database import initialize_db
from bot.commands.start import start
from bot.commands.logout import log_out_request
from bot.commands.menu import menu
from features.search import search_request
from features.summarize import summarize_request
from settings import settings
from utils import infinite_task
from utils.initialize_users import initialize_users

# handle errors telegram.error.BadRequest
# add pagers for summarize and search
# add openai interface for all llm requests

async def main():
    await initialize_users()
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    infinite_task.infinite_task = asyncio.create_task(infinite_task.get_infinite_task())
    try:
        await infinite_task.infinite_task
    except asyncio.CancelledError:
        pass

    await application.updater.stop()
    await application.stop()
    await application.shutdown()

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
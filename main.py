import asyncio
from telegram.ext import filters, CommandHandler, MessageHandler, CallbackQueryHandler
from bot import application, process_button, process_message
from database import initialize_db
from bot.commands.start import start
from bot.commands.logout import log_out_request
from bot.commands.menu import menu
from features.search import search_request
from features.summarize import summarize_request
from settings import settings
from utils import initialize_users

# add event handlings
# add more options when choosing allowed dialogs
# add admin?
# add back buttons
# change settings
# maybe change the way I list dialogs

async def main():
    await initialize_users()
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    await asyncio.Event().wait()

if __name__ == "__main__":
    initialize_db()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("summarize", summarize_request))
    application.add_handler(CommandHandler("search", search_request))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("log_out", log_out_request))
    application.add_handler(CallbackQueryHandler(process_button))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), process_message))

    asyncio.run(main())
from telegram import Update
from telegram.ext import ContextTypes

from bot.commands.menu import menu
from utils.classes import UserState
from utils.state import user_info
from utils.check_authentication import check_authentication
from utils.helpers import send_message


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    start_text = ("Welcome to Busy Lazy Bot!\n"
           "This bot will help you find info you lost in your chats "
           "and summarize your unread chats without the need to read them.\n\n"
           "Please read about this bot <a href='https://github.com/tonda228/extendedgram'>here</a> before signing in.")
    await send_message(chat_id=update.effective_chat.id, text=start_text, parse_mode="html")

    if await check_authentication(update.effective_user.id):
        user_info[update.effective_user.id].status = UserState.AUTHENTICATED
        await menu(update, context)

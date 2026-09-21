from telegram import Update
from telegram.ext import ContextTypes

from bot.commands.menu import menu
from utils.classes import UserState
from utils.state import user_info
from utils.check_authentication import check_authentication


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_text = "Welcome to Busy Lazy Bot!\n"\
           "This bot will help you to find info you lost in your chats "\
           "and summarize your unread groups without the need to read them."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=start_text)

    if await check_authentication(update, context):
        user_info[update.effective_user.id].status = UserState.AUTHENTICATED
        await menu(update, context)
from telegram import Update
from telegram.ext import ContextTypes

from bot.commands.menu import menu
from classes import UserState
from state import user_info
from utils import reset_idle_timer, check_authentication


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_text = "Welcome to Busy Lazy Bot!\n"\
           "This bot will help you to find info you lost in your chats "\
           "and summarize your unread groups without the need to read them."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=start_text)

    reset_idle_timer(update.effective_user.id)

    if await check_authentication(update, context):
        user_info[update.effective_user.id].status = UserState.AUTHENTICATED

    await menu(update, context)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from classes import UserState
from state import user_info
from utils import reset_idle_timer, check_authentication


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_authentication(update, context):
        return

    user_id = update.effective_user.id
    reset_idle_timer(user_id)

    keyboard = [
        [
            InlineKeyboardButton(text="Summarize", callback_data="summarize"),
            InlineKeyboardButton(text="Search", callback_data="search"),
            InlineKeyboardButton(text="Settings", callback_data="settings")
        ]
    ]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Where do you wish to continue?", reply_markup=InlineKeyboardMarkup(keyboard))
    # user_info[user_id].status = UserState.WAIT_FOR_MENU_CHOICE
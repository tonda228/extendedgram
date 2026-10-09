import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from features.preloading import reset_idle_timer
from utils.check_authentication import check_authentication
from utils.helpers import get_edit_message_text_func, send_message


async def menu(update: Update|None=None, context: ContextTypes.DEFAULT_TYPE|None=None, user_id=None, query=None, edit=False):
    if user_id is None:
        user_id = update.effective_user.id
    if not await check_authentication(user_id):
        return

    reset_idle_timer(user_id)

    keyboard = [
        [
            InlineKeyboardButton(text="Summarize", callback_data="summarize"),
            InlineKeyboardButton(text="Search", callback_data="search"),
            InlineKeyboardButton(text="Settings", callback_data="settings")
        ]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await send_message(user_id, "Where do you wish to continue?", reply_markup=markup)
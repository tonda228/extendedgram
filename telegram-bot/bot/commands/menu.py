import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from features.preloading import reset_idle_timer
from utils.check_authentication import check_authentication
from utils.classes import UserState
from utils.helpers import get_edit_message_text_func
from utils.state import user_info


async def menu(update: Update|None=None, context: ContextTypes.DEFAULT_TYPE|None=None, user_id=None, bot=None, query=None, edit=False):
    if not await check_authentication(update, context, user_id, bot):
        return

    if user_id:
        chat_id = user_id
    else:
        bot = context.bot
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

    reset_idle_timer(user_id)

    keyboard = [
        [
            InlineKeyboardButton(text="Summarize", callback_data="summarize"),
            InlineKeyboardButton(text="Search", callback_data="search"),
            InlineKeyboardButton(text="Settings", callback_data="settings")
        ]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    if edit:
        sender_func = get_edit_message_text_func(query.message.message_id, bot=bot)
    else:
        sender_func = bot.send_message
    try:
        await sender_func(chat_id=chat_id, text="Where do you wish to continue?", reply_markup=markup)
    except telegram.error.BadRequest as e:
        print(e)
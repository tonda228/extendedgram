from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from features.preloading import reset_idle_timer
from utils.check_authentication import check_authentication


async def menu(update: Update|None=None, context: ContextTypes.DEFAULT_TYPE|None=None, user_id=None, bot=None):
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
    await bot.send_message(chat_id=chat_id, text="Where do you wish to continue?", reply_markup=InlineKeyboardMarkup(keyboard))
    # user_info[user_id].status = UserState.WAIT_FOR_MENU_CHOICE
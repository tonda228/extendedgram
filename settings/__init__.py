from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.classes import UserState
from features.preloading import reset_idle_timer
from utils.state import user_info
from utils.check_authentication import check_authentication


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_authentication(update, context):
        return

    user_id = update.effective_user.id
    reset_idle_timer(user_id)

    app_user = user_info[user_id]
    allow_all = "✅" if app_user.allow_all else "❌"
    set_read_after_summary = "✅" if app_user.set_read_after_summary else "❌"
    preloading = "✅" if app_user.preloading else "❌"
    history_size = app_user.history_size
    keyboard = [
        [
            InlineKeyboardButton(text="Allowed dialogs", callback_data="allowed_dialogs"),
            InlineKeyboardButton(text=f"Allow all: {allow_all}", callback_data="allow_all")
        ],
        [
            InlineKeyboardButton(text=f"Read after summary: {set_read_after_summary}", callback_data="set_read_after_summary"),
            InlineKeyboardButton(text=f"Preloading: {preloading}", callback_data="preloading")
        ],
        [
            InlineKeyboardButton(text=f"History size: {history_size} days", callback_data="history_size")
        ],
        [
            InlineKeyboardButton(text="Log out", callback_data="log_out"),
            InlineKeyboardButton(text="Back", callback_data="back")
        ]
    ]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Settings", reply_markup=InlineKeyboardMarkup(keyboard))
    app_user.status = UserState.WAIT_FOR_SETTINGS_CHOICE
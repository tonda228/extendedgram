from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.classes import UserState
from features.preloading import reset_idle_timer
from utils.helpers import send_message
from utils.state import user_info
from utils.check_authentication import check_authentication


async def settings(update: Update|None=None, context: ContextTypes.DEFAULT_TYPE|None=None, user_id: int | None=None, edit: bool=False) -> None:
    if user_id is None:
        user_id = update.effective_user.id
    if not await check_authentication(user_id):
        return

    reset_idle_timer(user_id)

    app_user = user_info[user_id]
    allow_all = "✅" if app_user.allow_all else "❌"
    set_read_after_summary = "✅" if app_user.set_read_after_summary else "❌"
    preloading = "✅" if app_user.preloading else "❌"
    order_by = app_user.order_by
    keyboard = [
        [
            InlineKeyboardButton(text="Allowed dialogs", callback_data="allowed_dialogs"),
            InlineKeyboardButton(text=f"Allow all: {allow_all}", callback_data="allow_all")
        ],
        [
            InlineKeyboardButton(text=f"Read after summary: {set_read_after_summary}", callback_data="set_read_after_summary")
        ]
    ]
    if app_user.is_host:
        keyboard[1].append(InlineKeyboardButton(text=f"Preloading: {preloading}", callback_data="preloading"))
    keyboard.append([InlineKeyboardButton(text="Groups", callback_data="groups")])
    keyboard += [
        [InlineKeyboardButton(text=f"History size: {app_user.history_size} days", callback_data="history_size")],
        [InlineKeyboardButton(text=f"Order dialogs by: {order_by}", callback_data="order_by")],
        [
            InlineKeyboardButton(text="Log out", callback_data="log_out"),
            InlineKeyboardButton(text="Back", callback_data="back")
        ]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await send_message(user_id, "Settings", reply_markup=markup, edit=edit)
    app_user.status = UserState.WAIT_FOR_SETTINGS_CHOICE

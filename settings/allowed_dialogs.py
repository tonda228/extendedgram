from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from classes import UserState
from database import cur, connection
from database.dialogs import get_allowed_dialogs, get_all_dialogs, store_dialog
from settings import settings
from state import user_info
from utils import reset_idle_timer


async def display_allowed_dialogs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reset_idle_timer(user_id)

    if user_info[user_id].allow_all:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"All dialogs are allowed.")
    else:
        allowed_dialogs = await get_allowed_dialogs(user_id)
        if len(allowed_dialogs) == 0:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"No dialogs are allowed.")

        for dialog in allowed_dialogs:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"{dialog[0].title}")

    keyboard = [
        [
            InlineKeyboardButton(text="Yes", callback_data="Yes"),
            InlineKeyboardButton(text="No", callback_data="No")
        ]
    ]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Do you wish to change your choice?", reply_markup=InlineKeyboardMarkup(keyboard))
    user_info[user_id].status = UserState.WAIT_FOR_CHANGE_ALLOWED_DIALOGS_CONFIRMATION

async def query_new_allowed_dialogs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    all_dialogs = await get_all_dialogs(user_id)

    reset_idle_timer(user_id)

    for index, dialog in enumerate(all_dialogs, start = 1):
        name = dialog[0].title
        if dialog[1]:
            name += f"|{dialog[1].title}"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"{index}) {name}")

    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="Enter chosen chats separated by comma. Whitespaces will be ignored.")
    app_user.status = UserState.WAIT_FOR_ALLOWED_DIALOGS_CHOICE
    app_user.dialogs = all_dialogs

async def change_allowed_dialogs(update: Update, context: ContextTypes.DEFAULT_TYPE, text):
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    all_dialogs = app_user.dialogs

    reset_idle_timer(user_id)

    try:
        indexes = {int(index) for index in text.split(",")}
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid input. Try again:")
        return
    for index in indexes:
        if not (1 <= index <= len(all_dialogs)):
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid index. Try again:")
            return

    await context.bot.send_message(chat_id=update.effective_chat.id, text="New allowed dialogs are:")

    allow_all = True
    for index, dialog in enumerate(all_dialogs, start=1):
        name = dialog[0].title
        if dialog[1]:
            name += f"|{dialog[1].title}"
        is_allowed = index in indexes
        if is_allowed:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"{name}")
        else:
            allow_all = False

        store_dialog(dialog, user_id)

        cur.execute("""
        UPDATE dialog
        SET is_allowed = %s
        WHERE dialog_id = %s
            and user_id = %s
        """, (is_allowed, dialog[0].id, user_id))


    if not allow_all:
        cur.execute("""
        UPDATE app_user
        SET allow_all = FALSE
        WHERE user_id = %s
        """, (user_id,))
        app_user.allow_all = False

    # delete dialogs that became restricted?
    connection.commit()
    await settings(update, context)
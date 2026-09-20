from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.classes import UserState
from database import cur, connection
from settings import settings
from utils.state import user_info


async def display_history_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    history_size = user_info[user_id].history_size
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text=f"Current history size is {history_size} days")

    keyboard = [
        [
            InlineKeyboardButton(text="Yes, I do.", callback_data="Yes"),
            InlineKeyboardButton(text="No, I do not.", callback_data="No")
        ]
    ]
    msg = await context.bot.send_message(chat_id=update.effective_chat.id,
                                         text="Do you wish to change your history size?",
                                         reply_markup=InlineKeyboardMarkup(keyboard))
    user_info[user_id].message_id = msg.id
    user_info[user_id].status = UserState.WAIT_FOR_HISTORY_SIZE_CHANGE_CONFIRMATION

async def query_new_history_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text=f"Enter your new history size in days:")

    user_info[user_id].status = UserState.WAIT_FOR_NEW_HISTORY_SIZE

async def change_history_size(update: Update, context: ContextTypes.DEFAULT_TYPE, text):
    try:
        new_history_size = int(text)
        if new_history_size <= 0:
            raise ValueError
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid input. Try again.")
        return

    user_id = update.effective_user.id
    cur.execute("""
    UPDATE app_user
    SET history_size = %s
    WHERE user_id = %s
    """, (new_history_size, user_id))
    connection.commit()

    user_info[user_id].history_size = new_history_size
    await context.bot.send_message(chat_id=update.effective_chat.id, text="New history size was set.")
    await settings(update, context)



























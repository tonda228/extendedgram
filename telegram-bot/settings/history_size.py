from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from utils.classes import UserState
from database import cur, connection
from settings import settings
from utils.helpers import send_message
from utils.state import user_info


async def display_history_size(user_id):

    history_size = user_info[user_id].history_size
    await send_message(chat_id=user_id, text=f"Current history size is {history_size} days")

    keyboard = [
        [
            InlineKeyboardButton(text="Yes, I do.", callback_data="Yes"),
            InlineKeyboardButton(text="No, I do not.", callback_data="No")
        ]
    ]
    await send_message(user_id, "Do you wish to change your history size?", InlineKeyboardMarkup(keyboard))
    user_info[user_id].status = UserState.WAIT_FOR_HISTORY_SIZE_CHANGE_CONFIRMATION

async def query_new_history_size(user_id):
    await send_message(chat_id=user_id, text=f"Enter your new history size in days:")
    user_info[user_id].status = UserState.WAIT_FOR_NEW_HISTORY_SIZE

async def change_history_size(user_id, text):
    try:
        new_history_size = int(text)
        if new_history_size <= 0:
            raise ValueError
    except ValueError:
        await send_message(chat_id=user_id, text="Invalid input. Try again.")
        return

    cur.execute("""
    UPDATE app_user
    SET history_size = %s
    WHERE user_id = %s
    """, (new_history_size, user_id))
    connection.commit()

    user_info[user_id].history_size = new_history_size
    await send_message(chat_id=user_id, text="New history size was set.")
    await settings(user_id=user_id)



























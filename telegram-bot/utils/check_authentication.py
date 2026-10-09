import os

from dotenv import load_dotenv
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telethon import TelegramClient
from telethon.sessions import StringSession

from utils.classes import AppUser, UserState
from utils.state import user_info
from utils.helpers import send_message

load_dotenv()

# Telethon
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

async def query_phone_number(user_id, is_user_in_memory: bool=False):
    phone_number_text = "To use this bot you need to be logged into your account.\nPlease enter your phone number below  (with + at the start):"
    if is_user_in_memory:
        user_info[user_id].status = UserState.WAIT_FOR_PHONE_NUMBER
    else :
        user_info[user_id] = AppUser(
            UserState.WAIT_FOR_PHONE_NUMBER,
            TelegramClient(StringSession(), API_ID, API_HASH)
        )
    await send_message(chat_id=user_id, text=phone_number_text)



async def check_authentication(user_id) -> bool:
    is_user_in_memory = user_id in user_info
    if is_user_in_memory and user_info[user_id].status >= UserState.AUTHENTICATED:
        return True
    elif is_user_in_memory and user_info[user_id].status < UserState.AUTHENTICATED:
        await query_phone_number(user_id, is_user_in_memory)
    return False
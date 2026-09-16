import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ContextTypes
from telethon import TelegramClient
from telethon.sessions import StringSession

from utils.classes import AppUser, UserState
from utils.state import user_info

load_dotenv()

# Telethon
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

async def check_authentication(update: Update|None=None, context: ContextTypes.DEFAULT_TYPE|None=None, user_id=None, bot=None) -> bool:
    phone_number_text = "To use this bot you need to be logged into your account.\nPlease enter your phone number below  (with + at the start):"
    if user_id:
        chat_id = user_id
    else:
        bot = context.bot
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

    if user_id in user_info and user_info[user_id].status < UserState.AUTHENTICATED:
        user_info[user_id].status = UserState.WAIT_FOR_PHONE_NUMBER
        await bot.send_message(chat_id=chat_id, text=phone_number_text)
    elif user_id not in user_info:
        user_info[user_id] = AppUser(
            UserState.WAIT_FOR_PHONE_NUMBER,
            TelegramClient(StringSession(), API_ID, API_HASH)
        )
        await bot.send_message(chat_id=chat_id, text=phone_number_text)
    return user_info[user_id].status >= UserState.AUTHENTICATED
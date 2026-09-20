import os

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telethon import TelegramClient
from telethon.sessions import StringSession

from database import cur
from utils.classes import AppUser, UserState
from utils.state import user_info

load_dotenv()

# Telethon
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

async def query_phone_number(user_id, bot, is_user_in_memory: bool=False):
    phone_number_text = "To use this bot you need to be logged into your account.\nPlease enter your phone number below  (with + at the start):"
    if is_user_in_memory:
        user_info[user_id].status = UserState.WAIT_FOR_PHONE_NUMBER
    else :
        user_info[user_id] = AppUser(
            UserState.WAIT_FOR_PHONE_NUMBER,
            TelegramClient(StringSession(), API_ID, API_HASH)
        )
    await bot.send_message(chat_id=user_id, text=phone_number_text)

async def offer_to_send_request(user_id, bot):
    text = "To use this bot you need to be accepted by admin.\nDo you wish to send a request?"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(text="Yes", callback_data="yes"),
                                      InlineKeyboardButton(text="No", callback_data="no")]])
    await bot.send_message(chat_id=user_id, text=text, reply_markup=keyboard)

async def check_authentication(update: Update|None=None, context: ContextTypes.DEFAULT_TYPE|None=None, user_id=None, bot=None) -> bool:
    if user_id is None:
        bot = context.bot
        user_id = update.effective_user.id

    is_user_in_memory = user_id in user_info
    if is_user_in_memory and user_info[user_id].status >= UserState.AUTHENTICATED:
        return True
    if is_user_in_memory and user_info[user_id].status < UserState.AUTHENTICATED:
        await query_phone_number(user_id, bot, is_user_in_memory)
        return False

    cur.execute("""
    SELECT *
    FROM user_request
    WHERE user_id = %s
    """, (user_id,))
    request = cur.fetchone()
    if request is None:
        await offer_to_send_request(user_id, bot)
    elif not request.is_resolved:
        await bot.send_message(chat_id=user_id, text="You request has not been resolved yet. It may take a day or two.")
    elif request.is_accepted:
        await query_phone_number(user_id, bot)
    return False
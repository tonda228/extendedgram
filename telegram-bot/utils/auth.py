import hashlib
import os
import secrets

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telethon import TelegramClient
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError, PasswordHashInvalidError, \
    PhoneCodeExpiredError, PhoneNumberInvalidError
from telethon.sessions import StringSession

from database.users import store_app_user, store_telegram_user
from utils.check_authentication import offer_group_options
from utils.classes import UserState, LoginStage
from utils.helpers import get_user_name, send_message
from utils.security import decrypt_string
from utils.state import user_info, uninitialized_users

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

async def process_phone_number(user_id, number):
    uninitialized_user = uninitialized_users[user_id]
    client = user_info[user_id].client

    await client.connect()
    try:
        result = await client.send_code_request(number)
    except (TypeError, PhoneNumberInvalidError):
        await send_message(chat_id=user_id, text="Invalid phone number try again.")
        return
    uninitialized_user.phone_code_hash = result.phone_code_hash

    code_text = "Please enter your code (Separate your code with whitespaces or telegram may block this sign in):"
    await send_message(chat_id=user_id, text=code_text)
    uninitialized_user.status = LoginStage.AWAIT_CODE
    uninitialized_user.phone_num = number

async def process_code(user_id: int, code: str):
    app_user = user_info[user_id]
    uninitialized_user = uninitialized_users[user_id]
    client = app_user.client
    number = uninitialized_user.phone_num
    phone_code_hash = uninitialized_user.phone_code_hash

    if uninitialized_user.wait:
        await send_message(chat_id=user_id, text="You have no tries left. Wait for 5 minutes to retry.")
        return

    try:
        await client.sign_in(phone=number, code=code, phone_code_hash=phone_code_hash)
    except PhoneCodeInvalidError:
        uninitialized_user.tries_left -= 1
        text = f"Could not login.\nYou have {uninitialized_user.tries_left} left"
        await send_message(chat_id=user_id, text=text)
        if uninitialized_user.tries_left == 0:
            await client.disconnect()
    except SessionPasswordNeededError:
        uninitialized_user.status = LoginStage.AWAIT_TELEGRAM_PASSWORD
        text = "Two-steps verification is enabled and a password is required:"
        await send_message(chat_id=user_id, text=text)
    except PhoneCodeExpiredError:
        text = "Try to separate your code with whitespaces. Try again with new code."
        result = await client.send_code_request(number)
        uninitialized_user.phone_code_hash = result.phone_code_hash
        await send_message(chat_id=user_id, text=text)
    else:
        await successful_login(user_id)

async def process_password(user_id, text: str) -> None:
    client = user_info[user_id].client

    try:
        await client.sign_in(password=text)
    except PasswordHashInvalidError:
        text = "Incorrect password. Try again:"
        await send_message(chat_id=user_id, text=text)
    else:
        await query_bot_password(user_id)

async def query_bot_password(user_id):
    
    text = "Please create password for this bot."
    uninitialized_users[user_id].status = LoginStage.AWAIT_BOT_PASSWORD
    await send_message(chat_id=user_id, text=text)

async def process_bot_password(user_id, password: str):
    uninitialized_users[user_id].hashed_password = hashlib.sha256(password.encode()).hexdigest()
    text = "Please repeat this password."
    uninitialized_users[user_id].status = LoginStage.AWAIT_BOT_PASSWORD_CONFIRMATION
    await send_message(chat_id=user_id, text=text)

async def process_bot_password_confirmation(user_id, password: str):
    
    prev_hashed_password = uninitialized_users[user_id].hashed_password
    if prev_hashed_password != hashlib.sha256(password.encode()).hexdigest():
        text = "Passwords are not the same. Try again."
        await send_message(chat_id=user_id, text=text)
        await query_bot_password(user_id)
    else:
        
        client = user_info[user_id].client
        uninitialized_user = uninitialized_users[user_id]

        cur_user = await client.get_me()
        user_name = get_user_name(cur_user)
        store_telegram_user(cur_user.id, user_name)
        store_app_user(user_id, client, uninitialized_user.is_host, uninitialized_user.hashed_password, password)
        if uninitialized_user.is_host:
            uninitialized_user.bot_password = password
            await offer_one_time_code(user_id)
        else:
            await offer_group_options(user_id)


async def offer_one_time_code(user_id):
    text = ("Please make sure that you have downloaded code from github. Once you are ready you will receive "
            "one-time code that you will use to connect your device to the server application. "
            "Once you are ready press the button below.")
    uninitialized_users[user_id].status = LoginStage.AWAIT_IS_READY
    await send_message(user_id, text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text="Ready", callback_data="ready")]]))

async def send_one_time_code(user_id: int):
    pairing_code = secrets.token_urlsafe(8)
    user_info[user_id].pairing_code_hash = hashlib.sha256(pairing_code.encode()).hexdigest()
    await send_message(user_id, f"Your user_id is {user_id}.\nYour code is: {pairing_code}")

async def check_bot_password(user_id, password: str):
    app_user = user_info[user_id]
    if app_user.password_hash != hashlib.sha256(password.encode()).hexdigest():
        text = "Passwords are not the same. Try again."
        await send_message(chat_id=user_id, text=text)
        return
    string_session = decrypt_string(app_user.string_session, password)
    app_user.client = TelegramClient(StringSession(string_session), API_ID, API_HASH)
    app_user.status = UserState.AUTHENTICATED
    await send_message(chat_id=user_id, text="Successfully logged in.")

async def successful_login(user_id):
    
    app_user = user_info[user_id]
    app_user.status = UserState.AUTHENTICATED

    await send_message(chat_id=user_id, text="Successfully signed in.")
    await query_bot_password(user_id)

# async def process_qr_code(user_id):
#     
#     client = user_info[user_id].client
#     qr_login = await client.qr_login()
#
#     print(qr_login.url)
#
#     await qr_login.wait()
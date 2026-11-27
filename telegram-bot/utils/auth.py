import hashlib
import os
import secrets
from time import monotonic

from telethon import TelegramClient
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError, PasswordHashInvalidError, \
    PhoneCodeExpiredError, PhoneNumberInvalidError
from telethon.sessions import StringSession

from database.users import store_app_user, store_telegram_user
from features import event_handlers
from features.preloading import reset_idle_timer
from utils.check_authentication import offer_group_options
from utils.classes import UserState, LoginStage, AppUserPreloading, AppUser, AppUserDBObject
from utils.helpers import get_user_name, send_message
from utils.security import decrypt_string
from utils.state import user_info, uninitialized_users, user_preloading
from utils.session import record_activity

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

async def process_phone_number(user_id: int, number: str) -> None:
    uninitialized_user = uninitialized_users[user_id]
    client = user_info[user_id].client
    if client is None:
        return
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

async def process_code(user_id: int, code: str) -> None:
    app_user = user_info[user_id]
    uninitialized_user = uninitialized_users[user_id]
    client = app_user.client
    number = uninitialized_user.phone_num
    phone_code_hash = uninitialized_user.phone_code_hash
    if client is None:
        return

    if uninitialized_user.wait:
        await send_message(chat_id=user_id, text="You have no tries left. Wait for 5 minutes to retry.")
        return

    try:
        await client.sign_in(phone=number, code=code, phone_code_hash=phone_code_hash)
    except PhoneCodeInvalidError:
        uninitialized_user.tries_left -= 1
        text = f"Could not login.\nYou have {uninitialized_user.tries_left} left"
        await send_message(chat_id=user_id, text=text)
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

async def process_password(user_id: int, text: str) -> None:
    client = user_info[user_id].client
    if client is None:
        return

    try:
        await client.sign_in(password=text)
    except PasswordHashInvalidError:
        text = "Incorrect password. Try again:"
        await send_message(chat_id=user_id, text=text)
    else:
        await successful_login(user_id)

async def query_new_bot_password(user_id: int) -> None:
    text = "Please create password for this bot."
    uninitialized_users[user_id].status = LoginStage.AWAIT_BOT_PASSWORD
    await send_message(chat_id=user_id, text=text)

async def process_bot_password(user_id: int, password: str) -> None:
    uninitialized_users[user_id].hashed_password = hashlib.sha256(password.encode()).hexdigest()
    text = "Please repeat this password."
    uninitialized_users[user_id].status = LoginStage.AWAIT_BOT_PASSWORD_CONFIRMATION
    await send_message(chat_id=user_id, text=text)

async def process_bot_password_confirmation(user_id: int, password: str) -> None:
    prev_hashed_password = uninitialized_users[user_id].hashed_password
    if prev_hashed_password != hashlib.sha256(password.encode()).hexdigest():
        text = "Passwords are not the same. Try again."
        await send_message(chat_id=user_id, text=text)
        await query_new_bot_password(user_id)
    else:
        app_user = user_info[user_id]
        client = app_user.client
        if client is None:
            return
        uninitialized_user = uninitialized_users[user_id]

        cur_user = await client.get_me()
        user_name = get_user_name(cur_user)
        store_telegram_user(cur_user.id, user_name)
        store_app_user(user_id, client, uninitialized_user.is_host, uninitialized_user.hashed_password, password)
        app_user.is_host = uninitialized_user.is_host
        app_user.password_hash = prev_hashed_password
        app_user.status = UserState.AUTHENTICATED
        record_activity(user_id)
        del uninitialized_users[user_id]
        if uninitialized_user.is_host:
            await send_one_time_code(user_id)
        if uninitialized_user.in_group or not uninitialized_user.is_host:
            await offer_group_options(user_id)
        else:
            from bot.commands.menu import menu
            await menu(user_id=user_id)

async def send_one_time_code(user_id: int) -> None:
    app_user = user_info.get(user_id)
    if (app_user is None or not app_user.is_host or not app_user.password_hash
            or app_user.status < UserState.AUTHENTICATED or user_id in uninitialized_users):
        return
    pairing_code = secrets.token_urlsafe(8)
    app_user.pairing_code_hash = hashlib.sha256(pairing_code.encode()).hexdigest()
    app_user.pairing_code_expires_at = monotonic() + 600
    await send_message(user_id, f"Your user ID is {user_id}.\nYour pairing code is: {pairing_code}\n"
                       "Enter it and your bot password in the host terminal within 10 minutes. "
                       "You can get a new code from Settings → Pair host.")

async def query_bot_password(app_user: AppUser | AppUserDBObject) -> None:
    await send_message(chat_id=app_user.user_id, text="Please enter your bot password.")

async def check_bot_password(user_id: int, password: str) -> None:
    app_user = user_info[user_id]
    if app_user.password_hash != hashlib.sha256(password.encode()).hexdigest():
        text = "Passwords are not the same. Try again."
        await send_message(chat_id=user_id, text=text)
        return
    if app_user.string_session is None:
        return
    string_session = decrypt_string(app_user.string_session, password)
    client = TelegramClient(StringSession(string_session), API_ID, API_HASH)
    await client.connect()
    app_user.client = client
    user_preloading[app_user.user_id] = AppUserPreloading()
    await event_handlers.create_new_message_handler(app_user.user_id)
    await event_handlers.create_delete_message_handler(app_user.user_id)
    reset_idle_timer(app_user.user_id)

    app_user.status = UserState.AUTHENTICATED
    record_activity(user_id)
    await send_message(chat_id=user_id, text="Successfully logged in.")
    from bot.commands.menu import menu
    await menu(user_id=user_id)

async def successful_login(user_id: int) -> None:
    user_preloading[user_id] = AppUserPreloading()
    await event_handlers.create_new_message_handler(user_id)
    await event_handlers.create_delete_message_handler(user_id)
    reset_idle_timer(user_id)

    await send_message(chat_id=user_id, text="Successfully signed in.")
    await query_new_bot_password(user_id)

# async def process_qr_code(user_id):
#     
#     client = user_info[user_id].client
#     qr_login = await client.qr_login()
#
#     print(qr_login.url)
#
#     await qr_login.wait()

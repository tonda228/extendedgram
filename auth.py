from telegram import Update
from telegram.ext import ContextTypes
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError, PasswordHashInvalidError
from classes import UserState, AppUserPreloading
from database import connection, cur
from state import user_info, user_preloading
from utils import reset_idle_timer


def validate_phone_number(number: str) -> bool:
    number = "".join(number.split())
    if len(number) != 13 or number[0] != '+' or not number[1:].isdigit():
        return False
    return True

async def process_phone_number(update: Update, context, number):
    if not validate_phone_number(number):
        raise ValueError

    user_id = update.effective_user.id
    user_info[user_id].phone_num = number

    client = user_info[user_id].client
    await client.connect()
    result = await client.send_code_request(number)
    user_info[user_id].phone_code_hash = result.phone_code_hash

    code_text = "Please enter your code:"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=code_text)
    user_info[user_id].status = UserState.WAIT_FOR_CODE

async def process_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    code = "".join(code.split())
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    client = user_info[user_id].client
    number = user_info[user_id].phone_num
    phone_code_hash = user_info[user_id].phone_code_hash

    if app_user.tries_left <= 0:
        # change this later
        await context.bot.send_message(chat_id=update.effective_chat.id, text="You have no tries left. Wait for 5 minutes to retry.")
        return

    try:
        await client.sign_in(phone=number, code=code, phone_code_hash=phone_code_hash)
    except PhoneCodeInvalidError as e:
        app_user.tries_left -= 1
        print(e)
        text = f"Could not login.\nYou have {app_user.tries_left} left"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        if app_user.tries_left == 0:
            client.disconnect()
    except SessionPasswordNeededError as e:
        print(e)
        app_user.tries_left = UserState.WAIT_FOR_PASSWORD
        text = "Two-steps verification is enabled and a password is required:"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    else:
        await successful_login(update, context)

async def process_password(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    user_id = update.effective_user.id
    client = user_info[user_id].client

    try:
        await client.sign_in(password=text)
    except PasswordHashInvalidError as e:
        text = "Incorrect password. Try again:"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    else:
        await successful_login(update, context)

async def successful_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    client = user_info[user_id].client

    await context.bot.send_message(chat_id=update.effective_chat.id, text="Successfully signed in.")

    # make separate function for this
    cur.execute("""
    INSERT INTO app_user (user_id,
                          string_session,
                          set_read_after_summary,
                          set_read_after_search,
                          allow_all)
    VALUES (%s, %s, %s, %s, %s)
    """, (user_id, client.session.save(), False, False, True))
    connection.commit()
    user_info[user_id].status = UserState.AUTHENTICATED
    user_preloading[user_id] = AppUserPreloading()
    reset_idle_timer(user_id)

# async def process_qr_code(update: Update, context):
#     user_id = update.effective_user.id
#     client = user_info[user_id].client
#     qr_login = await client.qr_login()
#
#     print(qr_login.url)
#
#     await qr_login.wait()
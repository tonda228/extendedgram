from telegram import Update
from telegram.ext import ContextTypes
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError, PasswordHashInvalidError, \
    PhoneCodeExpiredError, PhoneNumberInvalidError

from bot.commands.menu import menu
from database.users import store_app_user, delete_user_request
from utils.classes import UserState, AppUserPreloading
from features.preloading import reset_idle_timer
from utils.state import user_info, user_preloading


def validate_phone_number(number: str) -> bool:
    number = "".join(number.split())
    return (
            number.startswith("+")
            and number[1:].isdigit()
            and 8 <= len(number) <= 16
    )

async def process_phone_number(update: Update, context, number):
    user_id = update.effective_user.id
    client = user_info[user_id].client

    await client.connect()
    try:
        result = await client.send_code_request(number)
    except (TypeError, PhoneNumberInvalidError):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid phone number try again.")
        return
    user_info[user_id].phone_code_hash = result.phone_code_hash

    code_text = ("Please enter your code:\n\n!!!NOTICE!!!\nIf you are trying to log in using the same account "
                 "as you are currently logged in, separate code using whitespaces like this:\n * * * * *. "
                 "Telegram will not let you log in otherwise.")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=code_text)
    user_info[user_id].status = UserState.WAIT_FOR_CODE
    user_info[user_id].phone_num = number

async def process_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    code = "".join(code.split())
    user_id = update.effective_user.id
    app_user = user_info[user_id]
    client = user_info[user_id].client
    number = user_info[user_id].phone_num
    phone_code_hash = user_info[user_id].phone_code_hash

    if app_user.wait:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="You have no tries left. Wait for 5 minutes to retry.")
        return

    try:
        await client.sign_in(phone=number, code=code, phone_code_hash=phone_code_hash)
    except PhoneCodeInvalidError:
        app_user.tries_left -= 1
        text = f"Could not login.\nYou have {app_user.tries_left} left"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        if app_user.tries_left == 0:
            await client.disconnect()
    except SessionPasswordNeededError:
        app_user.status = UserState.WAIT_FOR_PASSWORD
        text = "Two-steps verification is enabled and a password is required:"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    except PhoneCodeExpiredError:
        text = "You might have forgot to separate your code with whitespaces. Try again with new code."
        result = await client.send_code_request(number)
        user_info[user_id].phone_code_hash = result.phone_code_hash
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    else:
        await successful_login(update, context)

async def process_password(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    user_id = update.effective_user.id
    client = user_info[user_id].client

    try:
        await client.sign_in(password=text)
    except PasswordHashInvalidError:
        text = "Incorrect password. Try again:"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    else:
        await successful_login(update, context)

async def successful_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    client = user_info[user_id].client

    await context.bot.send_message(chat_id=update.effective_chat.id, text="Successfully signed in.")

    # make separate function for this
    store_app_user(user_id, client)
    delete_user_request(user_id)
    user_info[user_id].status = UserState.AUTHENTICATED
    user_preloading[user_id] = AppUserPreloading()
    reset_idle_timer(user_id)

    await menu(update, context)

# async def process_qr_code(update: Update, context):
#     user_id = update.effective_user.id
#     client = user_info[user_id].client
#     qr_login = await client.qr_login()
#
#     print(qr_login.url)
#
#     await qr_login.wait()
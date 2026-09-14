import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telethon import TelegramClient
from telethon.tl.types import User, Channel
from telethon.sessions import StringSession

from classes import AppUser, UserState, AppUserPreloading
from database import cur
from database.users import store_telegram_user
from features import event_handlers
from features.preloading import reset_idle_timer
from state import user_preloading, user_info

# Telethon
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]


def update_inline_button(button, data, new_state=None, user_id=None, save_to=None):
    if button.callback_data != data:
        return InlineKeyboardButton(text=button.text, callback_data=button.callback_data)

    split_text = button.text.split(": ")
    if not new_state:
        new_state = split_text[-1] == "❌"
    if save_to:
        app_user = user_info[user_id]
        dest = getattr(app_user, save_to)
        if new_state:
            dest.add(data)
        else:
            dest.remove(data)

    new_text = "".join(button.text.split(":")[:-1]) + ": " + ("✅" if new_state else "❌")
    return InlineKeyboardButton(text=new_text, callback_data=data)


def update_inline_keyboard(keyboard, data, new_state=None, user_id=None, save_to=None):
    copy = []
    for i, row in enumerate(keyboard):
        copy.append(list())
        for button in row:
            copy[i].append(update_inline_button(button, data, new_state, user_id, save_to))
    return InlineKeyboardMarkup(copy)

def get_full_chat_name(dialog):
    chat_name = f"{dialog[0].title}"
    if dialog[1]:
        chat_name += f"|{dialog[1].title}"
    return chat_name

async def initialize_users() -> None:
    cur.execute("""
    SELECT * FROM app_user
    """)
    app_users = cur.fetchall()
    if not app_users:
        return
    for app_user in app_users:
        client = TelegramClient(StringSession(app_user.string_session), API_ID, API_HASH)
        await client.connect()

        #update user info
        cur_user = await client.get_me()
        if cur_user.username:
            user_name = cur_user.username
        else:
            user_name = cur_user.first_name + (cur_user.last_name if cur_user.last_name else "")
        store_telegram_user(cur_user.id, user_name)

        user_info[app_user.user_id] = AppUser(UserState.AUTHENTICATED, client, app_user)
        user_preloading[app_user.user_id] = AppUserPreloading()
        await event_handlers.create_new_message_handler(app_user.user_id)
        await event_handlers.create_delete_message_handler(app_user.user_id)
        reset_idle_timer(app_user.user_id)

async def get_message_info(data, message):
    if message.user_name:
         name = "Username: " + message.user_name
    else:
        name = message.title
    data.append({
        "type": "text",
        "text": name
    })

    message_id = str(message.message_id)
    date =  message.date_time.strftime("%H:%M:%S %d.%m.%Y")
    message_text = "Message " + message_id + " " + date + ": " + (message.text if message.text else "")
    media_text = "Media: " + ("None" if message.media_description is None else message.media_description)
    full_text = message_text + "\n" + media_text
    data.append({
        "type": "text",
        "text": full_text
    })

async def check_authentication(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    phone_number_text = "To use this bot you need to be logged into your account.\nPlease enter your phone number below:"
    user_id = update.effective_user.id

    if user_id in user_info and user_info[user_id].status < UserState.AUTHENTICATED:
        user_info[user_id].status = UserState.WAIT_FOR_PHONE_NUMBER
        await context.bot.send_message(chat_id=update.effective_chat.id, text=phone_number_text)
    elif user_id not in user_info:
        user_info[user_id] = AppUser(
            UserState.WAIT_FOR_PHONE_NUMBER,
            TelegramClient(StringSession(), API_ID, API_HASH)
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text=phone_number_text)
    return user_info[user_id].status >= UserState.AUTHENTICATED

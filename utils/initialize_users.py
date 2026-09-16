import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

from bot import application
from bot.commands.menu import menu
from utils.classes import AppUserPreloading, UserState, AppUser
from database import cur
from database.users import store_telegram_user
from features import event_handlers
from features.preloading import reset_idle_timer
from utils.state import user_info, user_preloading

load_dotenv()

# Telethon
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

async def send_restart_notification(user_id):
    await application.bot.send_message(chat_id=user_id,
                                       text="The bot is running again.")

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

        # await send_restart_notification(app_user.user_id)
        await menu(user_id=app_user.user_id, bot=application.bot)

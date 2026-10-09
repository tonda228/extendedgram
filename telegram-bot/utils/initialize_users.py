import os
from telethon import TelegramClient
from telethon.sessions import StringSession

from bot import application
from bot.commands.menu import menu
from utils.classes import AppUserPreloading, UserState, AppUser
from database import cur
from database.users import store_telegram_user
from features import event_handlers
from features.preloading import reset_idle_timer
from utils.helpers import get_user_name, send_message
from utils.state import user_info, user_preloading

# load_dotenv()

# Telethon
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

async def notify_user(user_id):
    await send_message(chat_id=user_id, text="The bot is running again.")

async def initialize_users() -> None:
    cur.execute("""
    SELECT * FROM app_user
    """)
    app_users = cur.fetchall()
    if not app_users:
        return
    for app_user in app_users:
        user_info[app_user.user_id] = AppUser(app_user.user_id, UserState.LOGGED_OUT, app_user=app_user)
        await notify_user(app_user.user_id)
        bot_password_txt = "Please enter bot password"
        if not app_user.is_host:
            await send_message(chat_id=app_user.user_id, text=bot_password_txt)
        else:
            text = "Please connect to the server"
            if app_user.group_id:
                text = bot_password_txt + " or enter your password."
            await send_message(chat_id=app_user.user_id, text=text)
        continue
        client = TelegramClient(StringSession(app_user.string_session), API_ID, API_HASH)
        await client.connect()

        #update user info
        cur_user = await client.get_me()
        user_name = get_user_name(cur_user)
        store_telegram_user(cur_user.id, user_name)

        user_info[app_user.user_id] = AppUser(UserState.AUTHENTICATED, client, app_user)
        user_preloading[app_user.user_id] = AppUserPreloading()
        await event_handlers.create_new_message_handler(app_user.user_id)
        await event_handlers.create_delete_message_handler(app_user.user_id)
        reset_idle_timer(app_user.user_id)

        # if config_file["notify_on_start"]:

        await menu(user_id=app_user.user_id, bot=application.bot)

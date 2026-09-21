import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

from bot import application
from bot.commands.menu import menu
from utils.check_authentication import query_phone_number, offer_to_send_request
from utils.classes import AppUserPreloading, UserState, AppUser
from database import cur, connection
from database.users import store_telegram_user
from features import event_handlers
from features.preloading import reset_idle_timer
from utils.helpers import get_user_name, accept_user, reject_user
from utils.state import user_info, user_preloading

load_dotenv()

# Telethon
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

async def notify_user(user_id):
    await application.bot.send_message(chat_id=user_id,
                                       text="The bot is running again.")


async def notify_unnotified_user(user_request):
    if not user_request.is_resolved or user_request.is_user_notified:
        return
    # for several clients should resolve conflicts
    if user_request.is_accepted:
        await accept_user(user_request.user_id, application.bot)
    else:
        await reject_user(user_request.user_id, application.bot)

    cur.execute("""
    UPDATE user_requests
    SET is_user_notified = TRUE
    WHERE user_id = %s
    """, (user_request.user_id,))
    connection.commit()


async def initialize_user_requests():
    cur.execute("""
    SELECT * FROM user_request
    """)
    for user_request in cur.fetchall():
        await notify_user(user_request.user_id)
        await notify_unnotified_user(user_request)
        if user_request.is_accepted:
            await query_phone_number(user_request.user_id, application.bot)
        else:
            await offer_to_send_request(user_request.user_id, application.bot)

async def initialize_users() -> None:
    cur.execute("""
    SELECT * FROM app_user
    """)
    app_users = cur.fetchall()
    print(app_users)
    if not app_users:
        return
    for app_user in app_users:
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

        await notify_user(app_user.user_id)
        await menu(user_id=app_user.user_id, bot=application.bot)

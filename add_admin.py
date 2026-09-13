import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

from database import cur, connection
from database.users import store_telegram_user

load_dotenv()
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]


async def save_admin():
    user = await client.get_me()

    if user.username:
        sender_name = user.username
    else:
        sender_name = user.first_name + (user.last_name if user.last_name else "")
    store_telegram_user(user.id, sender_name)

    # place this in database
    cur.execute("""
    INSERT INTO app_user (user_id,
                          string_session,
                          set_read_after_summary,
                          set_read_after_search,
                          allow_all,
                          is_admin
    ) VALUES (%s, %s, %s, %s, %s, TRUE) ON CONFLICT (user_id) DO UPDATE
    SET is_admin = TRUE
    """, (user.id, client.session.save(), False, False, True))
    connection.commit()

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    client.loop.run_until_complete(save_admin())

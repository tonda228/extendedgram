import os, asyncio

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from database import cur, connection
from database.users import store_telegram_user
from utils.helpers import get_user_name

load_dotenv()
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

async def login():
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    phone_num = input("Enter your phone number: ")

    result = await client.send_code_request(phone_num)

    code = input("Enter the code you received: ")

    try:
        await client.sign_in(phone_num, code, phone_code_hash=result.phone_code_hash)
    except SessionPasswordNeededError:
        password = input("Enter your password: ")
        await client.sign_in(password=password)

    await save_admin(client)

async def save_admin(client):
    user = await client.get_me()

    sender_name = get_user_name(user)
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
    SET is_admin = TRUE,
        string_session = EXCLUDED.string_session
    """, (user.id, client.session.save(), False, False, True))
    connection.commit()

# with TelegramClient(StringSession(), API_ID, API_HASH) as client:
#     client.loop.run_until_complete(save_admin())

asyncio.run(login())

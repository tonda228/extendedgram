import asyncio
import os, shutil

from database import cur
from utils.helpers import send_message
from utils.state import user_info, uninitialized_users

infinite_task = None

async def send_shutdown_notification():
    cur.execute("""
    SELECT user_id FROM app_user
    """)
    for user in cur.fetchall():
        if user.user_id in user_info and user_info[user.user_id].client:
            user_info[user.user_id].client.disconnect()
        await send_message(chat_id=user.user_id, text="The bot will be turned off in 5 minutes, you will be notified ones it's up again.")

    for user_id, uninitialized_user in uninitialized_users.items():
        await send_message(user_id, "The bot will be turned off in 5 minutes, please try to finish the set up in time.")

async def get_infinite_task():
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        print("Task was successfully finished.")
        if os.path.exists("media"):
            shutil.rmtree("media")
        await send_shutdown_notification()

        await asyncio.sleep(300)
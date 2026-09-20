import asyncio
import os, shutil

from bot import application
from database import cur
from utils.state import user_info

infinite_task = None

async def send_shutdown_notification():
    cur.execute("""
    (SELECT user_id FROM app_user)
    UNION
    (SELECT user_id FROM user_request)
    """)
    for user in cur.fetchall():
        if user.user_id in user_info:
            await user_info[user.user_id].client.disconnect()
        await application.bot.send_message(chat_id=user.user_id,
                                           text="The bot was just turned off, you will be notified ones it's up again.")

async def get_infinite_task():
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        print("Task was successfully finished.")
        if os.path.exists("media"):
            shutil.rmtree("media")
        await send_shutdown_notification()
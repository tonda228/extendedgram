import asyncio
import os, shutil

from bot import application
from database import cur

infinite_task = None

async def send_shutdown_notification():
    cur.execute("""
    SELECT user_id FROM app_user
    """)
    for user in cur.fetchall():
        await application.bot.send_message(chat_id=user.user_id,
                                           text="The bot was just turned off, you will be notified ones it's up again.")

async def get_infinite_task():
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        print("Task was successfully finished.")
        if os.path.exists("media"):
            shutil.rmtree("media")
        # await send_shutdown_notification()
import asyncio
import os, shutil

from psycopg.rows import class_row

from database import connection
from utils.classes import AppUserDBObject
from utils.helpers import send_message
from utils.state import user_info, uninitialized_users

infinite_task = None
shutdown_requested = False

async def send_shutdown_notification() -> None:
    with connection.cursor(row_factory=class_row(AppUserDBObject)) as db_cur:
        db_cur.execute("""
        SELECT * FROM app_user
        """)
        users = db_cur.fetchall()
    for user in users:
        app_user = user_info.get(user.user_id)
        if app_user:
            client = app_user.client
            if client:
                client.disconnect()
        await send_message(chat_id=user.user_id, text="The bot will be turned off in 5 minutes, you will be notified ones it's up again.")

    for user_id, uninitialized_user in uninitialized_users.items():
        await send_message(user_id, "The bot will be turned off in 5 minutes, please try to finish the set up in time.")

async def get_infinite_task() -> None:
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        print("Task was successfully finished.")
        if os.path.exists("media"):
            shutil.rmtree("media")
        if shutdown_requested:
            await send_shutdown_notification()
            await asyncio.sleep(300)
        raise
import asyncio
from logging import getLogger
import os, shutil

from telegram.error import TelegramError

from utils.helpers import send_message
from utils.state import user_info, uninitialized_users

infinite_task = None
shutdown_requested = False
logger = getLogger(__name__)

async def send_shutdown_notification(*, immediate: bool = False) -> None:
    recipients = set(user_info) | set(uninitialized_users)
    for user_id in recipients:
        if immediate:
            text = "The bot is shutting down. You will be notified when it is running again."
        elif user_id in uninitialized_users:
            text = "The bot will be turned off in 5 minutes. Please try to finish the setup in time."
        else:
            text = "The bot will be turned off in 5 minutes. You will be notified when it is running again."
        try:
            await asyncio.wait_for(send_message(chat_id=user_id, text=text), timeout=3)
        except (TimeoutError, TelegramError):
            logger.warning("Could not send shutdown notification to user %s", user_id, exc_info=True)

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

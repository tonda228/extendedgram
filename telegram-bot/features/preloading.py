import asyncio

from database.dialogs import get_recent_dialogs
from database.messages import store_unsaved_messages
from utils.state import user_preloading, user_info

SLEEP_TIME = 60

async def wait_until_idle(user_id: int) -> None:
    user = user_preloading[user_id]
    if not user_info[user_id].preloading:
        return
    try:
        await asyncio.sleep(SLEEP_TIME)
        user.preloading = asyncio.create_task(start_preloading(user_id))
        await user.preloading
    except asyncio.CancelledError:
        pass

async def start_preloading(user_id: int) -> None:
    app_user = user_info[user_id]
    try:
        dialogs = await get_recent_dialogs(user_id)
        for dialog in dialogs:
            if not await store_unsaved_messages(user_id, dialog, app_user.client, add_embeddings=True):
                # reset when user reconnects
                break
    except asyncio.CancelledError:
        pass

def reset_idle_timer(user_id: int, reset: bool=True) -> None:
    user = user_preloading.get(user_id)
    app_user = user_info.get(user_id)
    if user is None or app_user is None:
        return
    if user.idle_task:
        user.idle_task.cancel()
    if user.preloading:
        user.preloading.cancel()
        user.preloading = None
    if reset and app_user.preloading:
        user.idle_task = asyncio.create_task(wait_until_idle(user_id))

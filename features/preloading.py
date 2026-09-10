import asyncio

from database.dialogs import get_recent_dialogs
from database.messages import store_unsaved_messages
from state import user_preloading, user_info

SLEEP_TIME = 60

async def wait_until_idle(user_id: int):
    user = user_preloading[user_id]
    try:
        await asyncio.sleep(SLEEP_TIME)
        user.preloading = asyncio.create_task(start_preloading(user_id))
        await user.preloading
    except asyncio.CancelledError:
        pass

async def start_preloading(user_id: int):
    app_user = user_info[user_id]

    try:
        dialogs = await get_recent_dialogs(user_id)
        print("Starting preloading.")

        for dialog in dialogs:
            await store_unsaved_messages(user_id, dialog, app_user.client, add_embeddings=True)
    except asyncio.CancelledError:
        print("Finished preloading.")
        pass

def reset_idle_timer(user_id: int, reset=True):
    if not user_info[user_id].preloading:
        return
    user = user_preloading[user_id]
    if user.idle_task:
        user.idle_task.cancel()
    if user.preloading:
        user.preloading.cancel()
        user.preloading = None
    if reset:
        user.idle_task = asyncio.create_task(wait_until_idle(user_id))
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from database import connection, cur
from database.dialogs import delete_unused_channels
from database.groups import update_user_groups, get_affected_users_and_groups
from database.users import delete_unused_users, delete_app_user
from server import disconnect_host, available_hosts, available_group_hosts, close_websocket, finish_requests
from utils.classes import UserState, UserGroupDBObject
from utils.helpers import send_message
from utils.check_authentication import check_authentication
from utils.state import user_info, user_preloading


async def log_out_request(update: Update|None=None, context: ContextTypes.DEFAULT_TYPE|None=None, user_id: int | None=None) -> None:
    if user_id is None:
        user_id = update.effective_user.id
    if not await check_authentication(user_id):
        return
    app_user = user_info.get(user_id)
    if app_user is None:
        return
    keyboard = [
        [
            InlineKeyboardButton(text="Yes, I do.", callback_data="Yes"),
            InlineKeyboardButton(text="No, I do not.", callback_data="No")
        ]
    ]
    await send_message(user_id, "Do you wish to log out and delete all your data?", InlineKeyboardMarkup(keyboard))
    app_user.status = UserState.WAIT_FOR_LOG_OUT_CONFIRMATION

async def log_out_confirmation(user_id: int) -> None:
    affected_users = get_affected_users_and_groups(user_id)
    deleted_group_ids = {group_id for groups in affected_users.values() for group_id, _ in groups}
    preloading = user_preloading.pop(user_id, None)
    if preloading:
        tasks = []
        if preloading.preloading:
            tasks.append(preloading.preloading)
            preloading.preloading.cancel()
        if preloading.idle_task:
            tasks.append(preloading.idle_task)
            preloading.idle_task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app_user = user_info.get(user_id)
    host = None
    if app_user:
        if app_user.is_host:
            host = available_hosts.get(app_user.user_id)
        client = app_user.client
        if client:
            await client.log_out()

    try:
        delete_app_user(user_id, commit=False)
        delete_unused_channels(commit=False)
        delete_unused_users(commit=False)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    if host:
        await disconnect_host(host)
        await finish_requests(host)
        await close_websocket(host, notify=False, code=4001, reason="Account logged out")
    user_info.pop(user_id, None)
    for group_id in deleted_group_ids:
        available_group_hosts.pop(group_id, None)
    await update_user_groups(affected_users, user_id)
    await send_message(chat_id=user_id, text="You've successfully logged out.")

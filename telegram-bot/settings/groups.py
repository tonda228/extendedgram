import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from database.groups import delete_group, get_app_user_groups
from utils.classes import UserState
from utils.helpers import send_message
from utils.state import user_info


async def group_settings(user_id: int, edit: bool = False, notice: str | None = None) -> None:
    user = user_info[user_id]
    groups = get_app_user_groups(user_id)
    keyboard = [[InlineKeyboardButton(group.name[:60], callback_data=f"group:{group.group_id}")]
                for group in groups]
    keyboard += [
        [InlineKeyboardButton("Join group", callback_data="join"),
         InlineKeyboardButton("Create group", callback_data="create")],
        [InlineKeyboardButton("Back", callback_data="groups_back")],
    ]
    user.pending_group = None
    user.pending_group_name = None
    user.pending_group_password_hash = None
    user.status = UserState.WAIT_FOR_GROUP_SETTINGS
    text = "Groups" if groups else "You haven't joined any groups yet."
    if notice:
        text = f"{notice}\n\n{text}"
    await send_message(user_id, text,
                       reply_markup=InlineKeyboardMarkup(keyboard), edit=edit)


async def show_group(user_id: int, group_id: int) -> None:
    group = next((group for group in get_app_user_groups(user_id) if group.group_id == group_id), None)
    if group is None:
        await group_settings(user_id, edit=True)
        return

    user = user_info[user_id]
    user.pending_group = None
    user.status = UserState.WAIT_FOR_GROUP_SETTINGS
    model = group.embedding_model or os.getenv("EMBEDDING_MODEL") or "Not configured"
    buttons = [[InlineKeyboardButton("Back", callback_data="groups")]]
    if group.created_by == user_id:
        buttons.insert(0, [InlineKeyboardButton("Change embedding model", callback_data=f"group_model:{group_id}")])
        buttons.insert(1, [InlineKeyboardButton("Delete group", callback_data=f"group_delete:{group_id}")])
    await send_message(user_id, f"Group: {group.name[:200]}\nID: {group.group_id}\nRequired embedding model: {model}",
                       reply_markup=InlineKeyboardMarkup(buttons), edit=True)


async def request_group_model_change(user_id: int, group_id: int) -> None:
    group = next((group for group in get_app_user_groups(user_id) if group.group_id == group_id), None)
    if group is None or group.created_by != user_id:
        return
    user = user_info[user_id]
    user.pending_group = group
    user.status = UserState.WAIT_FOR_GROUP_EMBEDDING_MODEL
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data=f"group:{group_id}")]])
    await send_message(user_id, "Enter the exact name of a 1,024-dimensional embedding model, or type default to use the server default.",
                       reply_markup=keyboard, edit=True)


async def request_group_delete(user_id: int, group_id: int) -> None:
    group = next((group for group in get_app_user_groups(user_id) if group.group_id == group_id), None)
    if group is None or group.created_by != user_id:
        return

    user = user_info[user_id]
    user.pending_group = group
    user.status = UserState.WAIT_FOR_GROUP_DELETE_CONFIRMATION
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Yes, delete group", callback_data=f"group_delete_confirm:{group_id}")],
        [InlineKeyboardButton("Back", callback_data=f"group:{group_id}")],
    ])
    await send_message(user_id, f'Delete "{group.name[:200]}" for everyone? This removes all group memberships.',
                       reply_markup=keyboard, edit=True)


async def confirm_group_delete(user_id: int, group_id: int) -> None:
    user = user_info[user_id]
    group = user.pending_group
    if (user.status != UserState.WAIT_FOR_GROUP_DELETE_CONFIRMATION or group is None
            or group.group_id != group_id or group.created_by != user_id):
        return

    deleted = delete_group(user_id, group_id)
    if not deleted:
        user.groups = None
    notice = "Group deleted." if deleted else "This group no longer exists or you do not own it."
    await group_settings(user_id, edit=True, notice=notice)

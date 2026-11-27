from telegram.error import TelegramError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import hashlib
import os
from heapq import heappush

from psycopg.rows import class_row

from database import cur, connection
from utils.classes import UserState, UserGroupDBObject
from utils.helpers import send_message
from utils.state import user_info

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def check_password(password: str, hashed_password: str | None) -> bool:
    return hashlib.sha256(password.encode()).hexdigest() == hashed_password

def get_affected_users_and_groups(user_id: int) -> dict[int, list[tuple[int, str]]]:
    result = cur.execute("""
    SELECT * FROM user_group_app_user 
    JOIN user_group using(group_id)
    WHERE created_by = %s
    """, (user_id,)).fetchall()

    affected_users = {}
    for row in result:
        affected_users.setdefault(row.user_id, []).append((row.group_id, row.name))
    return affected_users

async def update_user_groups(affected_users: dict[int, list[tuple[int, str]]], deleted_user_id: int) -> None:
    for user_id, groups in affected_users.items():
        app_user = user_info.get(user_id)
        if not app_user or user_id == deleted_user_id:
            continue
        description = "Following groups were deleted:\n"
        for group in groups:
            description += f"Id: {group[0]}, name: {group[1]}\n"
        app_user.groups = None
        app_user.groups = get_app_user_groups(app_user.user_id)
        try:
            await send_message(app_user.user_id, description)
        except TelegramError as error:
            print(f"Could not notify user {user_id}: {error}")

def get_app_user_groups(user_id: int) -> list[UserGroupDBObject]:
    app_user = user_info.get(user_id)
    if app_user and app_user.groups:
        return app_user.groups

    with connection.cursor(row_factory=class_row(UserGroupDBObject)) as group_cur:
        result = group_cur.execute("""
        SELECT * FROM user_group g
        WHERE EXISTS (
            SELECT 1
            FROM user_group_app_user gu
            WHERE user_id = %s AND g.group_id = gu.group_id
        )""", (user_id,)).fetchall()
        if app_user:
            app_user.groups = result
        return result

def delete_group(user_id: int, group_id: int) -> bool:
    try:
        with connection.cursor() as db_cur:
            db_cur.execute("""
                DELETE FROM user_group
                WHERE group_id = %s AND created_by = %s
                RETURNING group_id
                """, (group_id, user_id))
            deleted = db_cur.fetchone() is not None
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    if deleted:
        from server import available_group_hosts
        available_group_hosts.pop(group_id, None)
        for user in user_info.values():
            if user.groups is not None:
                user.groups[:] = [group for group in user.groups if group.group_id != group_id]
            if user.pending_group is not None and user.pending_group.group_id == group_id:
                user.pending_group = None
    return deleted


def group_setup_back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Back to groups", callback_data="groups")]])


async def create_group_request(user_id: int) -> None:
    text = "Enter your group name. This name should be unique, you will be notified otherwise."
    user_info[user_id].status = UserState.WAIT_FOR_NEW_GROUP_NAME
    await send_message(chat_id=user_id, text=text, reply_markup=group_setup_back_button(), keep_in_history=True)

async def query_new_group_password(user_id: int, group_name: str) -> None:
    if group_name.isdigit():
        await send_message(chat_id=user_id, text="Name should contain at least one non-digit character. Try again.")
        return
    with connection.cursor(row_factory=class_row(UserGroupDBObject)) as db_cur:
        group = db_cur.execute("SELECT * FROM user_group WHERE name = %s", (group_name,)).fetchone()
    if group is not None:
        await send_message(chat_id=user_id, text="Group with this name already exists. Try again.")
        return
    user_info[user_id].pending_group_name = group_name

    text = "Enter password for your group."
    user_info[user_id].status = UserState.WAIT_FOR_NEW_GROUP_PASSWORD
    await send_message(chat_id=user_id, text=text, reply_markup=group_setup_back_button(), keep_in_history=True)

async def query_password_confirmation(user_id: int, password: str) -> None:
    user_info[user_id].pending_group_password_hash = hash_password(password)
    text = "Confirm password."
    user_info[user_id].status = UserState.WAIT_FOR_NEW_GROUP_PASSWORD_CONFIRMATION
    await send_message(chat_id=user_id, text=text, reply_markup=group_setup_back_button(), keep_in_history=True)

async def process_password_confirmation(user_id: int, password: str) -> None:
    app_user = user_info[user_id]
    if not check_password(password, app_user.pending_group_password_hash):
        await send_message(chat_id=user_id, text="Passwords are not the same. Try again.")
        await query_new_group_password(user_id, app_user.pending_group_name)
        return

    app_user.status = UserState.WAIT_FOR_NEW_GROUP_EMBEDDING_MODEL
    default_model = os.getenv("EMBEDDING_MODEL") or "Not configured"
    await send_message(user_id, "Enter the exact name of a 1,024-dimensional embedding model, or type default.\n"
                       f"Server default: {default_model}\n"
                       "A shared host must provide this exact model. Typing a name does not install it.",
                       reply_markup=group_setup_back_button(), keep_in_history=True)


async def save_group_embedding_model(user_id: int, text: str) -> None:
    app_user = user_info[user_id]
    model = text.strip()
    if not model or len(model) > 200 or any(char.isspace() or not char.isprintable() for char in model):
        await send_message(user_id, "Enter a model name of at most 200 characters without spaces, or type default.")
        return
    model = None if model.lower() == "default" else model
    if app_user.status == UserState.WAIT_FOR_NEW_GROUP_EMBEDDING_MODEL:
        if app_user.pending_group_name is not None and app_user.pending_group_password_hash is not None:
            await create_group(user_id, model)
        return

    group = app_user.pending_group
    if app_user.status != UserState.WAIT_FOR_GROUP_EMBEDDING_MODEL or group is None or group.created_by != user_id:
        return

    try:
        with connection.cursor() as db_cur:
            db_cur.execute("""
                UPDATE user_group SET embedding_model = %s
                WHERE group_id = %s AND created_by = %s
                RETURNING group_id
                """, (model, group.group_id, user_id))
            updated = db_cur.fetchone() is not None
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    from settings.groups import group_settings
    if updated:
        for user in user_info.values():
            for cached_group in user.groups or []:
                if cached_group.group_id == group.group_id:
                    cached_group.embedding_model = model
            if user.pending_group is not None and user.pending_group.group_id == group.group_id:
                user.pending_group.embedding_model = model
        await send_message(user_id, "Group embedding model updated.")
    else:
        app_user.groups = None
        await send_message(user_id, "This group no longer exists or you do not own it.")
    await group_settings(user_id)


async def create_group(user_id: int, embedding_model: str | None) -> None:
    app_user = user_info[user_id]

    if app_user.groups is None:
        get_app_user_groups(user_id)

    try:
        with connection.cursor(row_factory=class_row(UserGroupDBObject)) as db_cur:
            db_cur.execute("""
            INSERT INTO user_group (name, password_hash, created_by, embedding_model)
            VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
            RETURNING *
            """, (app_user.pending_group_name, app_user.pending_group_password_hash, user_id, embedding_model))
            result = db_cur.fetchone()
            if result is not None:
                db_cur.execute("""
                INSERT INTO user_group_app_user (group_id, user_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
                """, (result.group_id, user_id))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    if result is None:
        await send_message(chat_id=user_id, text="Sorry. While you were setting the password someone created group "
                                                 "with such name. Please enter another name")
        app_user.status = UserState.WAIT_FOR_NEW_GROUP_NAME
        return

    if app_user.groups is None:
        app_user.groups = list()
    app_user.groups.append(result)

    from server import available_hosts, available_group_hosts
    host = available_hosts.get(user_id)
    if host is not None:
        hosts = available_group_hosts.setdefault(result.group_id, [])
        if not any(stored_host is host for stored_host in hosts):
            heappush(hosts, host)

    app_user.pending_group_name = None
    app_user.pending_group_password_hash = None
    app_user.pending_group = None
    app_user.status = UserState.AUTHENTICATED
    try:
        await send_message(chat_id=user_id, text=f"Your group's id is {result.group_id}.")
    except TelegramError as error:
        print(f"Group {result.group_id} was created, but notification failed: {error}")
        return
    from bot.commands.menu import menu
    await menu(user_id=user_id)

async def join_group_request(user_id: int) -> None:
    text = "Enter group name or group id."
    user_info[user_id].status = UserState.WAIT_FOR_GROUP_NAME
    await send_message(chat_id=user_id, text=text, reply_markup=group_setup_back_button(), keep_in_history=True)

async def join_group(user_id: int, group: str) -> None:
    try:
        group_id = int(group)
    except (ValueError, TypeError):
        group_id = None

    with connection.cursor(row_factory=class_row(UserGroupDBObject)) as db_cur:
        db_cur.execute("""
        SELECT *
        FROM user_group
        WHERE name = %s OR group_id = %s
        """, (group, group_id))
        result = db_cur.fetchone()
    if result is None:
        await send_message(chat_id=user_id, text="No such group exists. Please try again.")
    else:
        user_info[user_id].pending_group = result
        await query_group_password(user_id)

async def query_group_password(user_id: int) -> None:
    text = "Enter password for selected group."
    user_info[user_id].status = UserState.WAIT_FOR_GROUP_PASSWORD
    await send_message(chat_id=user_id, text=text, reply_markup=group_setup_back_button(), keep_in_history=True)

async def check_group_password(user_id: int, password: str) -> None:
    group = user_info[user_id].pending_group
    if group is None:
        await join_group_request(user_id)
        return
    if not check_password(password, group.password_hash):
        await send_message(chat_id=user_id, text="Invalid password. Try again.")
        return

    cur.execute("""
    INSERT INTO user_group_app_user (group_id, user_id)
    VALUES (%s, %s) ON CONFLICT DO NOTHING
    """, (group.group_id, user_id))
    connection.commit()
    groups = user_info[user_id].groups
    if groups is None:
        groups = get_app_user_groups(user_id)
    if not any(existing.group_id == group.group_id for existing in groups):
        groups.append(group)

    app_user = user_info[user_id]
    from server import available_hosts, available_group_hosts
    host = available_hosts.get(user_id)
    if host is not None:
        hosts = available_group_hosts.setdefault(group.group_id, [])
        if not any(stored_host is host for stored_host in hosts):
            heappush(hosts, host)

    app_user.pending_group_name = None
    app_user.pending_group_password_hash = None
    app_user.pending_group = None
    app_user.status = UserState.AUTHENTICATED
    from bot.commands.menu import menu
    await menu(user_id=user_id)




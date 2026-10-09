import hashlib

from bot.commands.menu import menu
from database import cur, connection
from utils.auth import offer_one_time_code
from utils.classes import LoginStage
from utils.helpers import send_message
from utils.state import uninitialized_users, user_info

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_password(password, hashed_password):
    return hashlib.sha256(password.encode()).hexdigest() == hashed_password

async def display_group_info(user_id):
    app_user = user_info[user_id]
    cur.execute("SELECT * FROM group WHERE group_id = %s", (app_user.group_id,))
    group = cur.fetchone()
    description = f"Id: {group.group_id}\nName: {group.name}"
    await send_message(chat_id=user_id, text=description)
    await menu(user_id)

async def create_group_request(user_id):
    text = "Enter your group name. This name should be unique, you will be notified otherwise."
    uninitialized_users[user_id].status = LoginStage.AWAIT_NEW_GROUP_NAME
    await send_message(chat_id=user_id, text=text)

async def query_new_group_password(user_id, group_name: str):
    if group_name.isdigit():
        await send_message(chat_id=user_id, text="Name should contain at least one non-digit character. Try again.")
        return
    group = cur.execute("SELECT * FROM group WHERE name = %s", (group_name,)).fetchone()
    if group is not None:
        await send_message(chat_id=user_id, text="Group with this name already exists. Try again.")
        return
    uninitialized_users[user_id].group_name = group_name

    text = "Enter password for your group."
    uninitialized_users[user_id].status = LoginStage.AWAIT_NEW_GROUP_PASSWORD
    await send_message(chat_id=user_id, text=text)

async def query_password_confirmation(user_id, password):
    uninitialized_users[user_id].hashed_password = hash_password(password)
    text = "Confirm password."
    uninitialized_users[user_id].status = LoginStage.AWAIT_NEW_GROUP_PASSWORD_CONFIRMATION
    await send_message(chat_id=user_id, text=text)

async def process_password_confirmation(user_id, password):
    uninitialized_user = uninitialized_users[user_id]
    if not check_password(password, uninitialized_user.hashed_password):
        uninitialized_user.status = LoginStage.AWAIT_GROUP_PASSWORD
        await send_message(chat_id=user_id, text="Passwords are not the same. Try again.")
        await query_new_group_password(user_id, uninitialized_user.group_name)
        return

    cur.execute("""
    INSERT INTO user_group (name, password_hash, created_by)
    VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
    RETURNING group_id
    """, (uninitialized_user.group_name, uninitialized_user.hashed_password, user_id))
    connection.commit()
    result = cur.fetchone()
    if result is None:
        await send_message(chat_id=user_id, text="Sorry. While you were setting the password someone created group "
                                                     "with such name. Please enter another name")
        uninitialized_user.status = LoginStage.AWAIT_GROUP_NAME
        return

    await send_message(chat_id=user_id, text=f"Your groups's id is {result.group_id}.")

    cur.execute("""
    UPDATE app_user
    SET group_id = %s
    WHERE user_id = %s
    """, (result.group_id, user_id))
    user_info[user_id].group_id = result.group_id
    connection.commit()

    if uninitialized_user.is_host:
        await offer_one_time_code(user_id)
    else:
        del uninitialized_users[user_id]
        await menu(user_id=user_id)

async def join_group_request(user_id):
    text = "Enter group name or group id."
    uninitialized_users[user_id].status = LoginStage.AWAIT_GROUP_NAME
    await send_message(chat_id=user_id, text=text)

async def join_group(user_id, group):
    try:
        group_id = int(group)
    except TypeError:
        group_id = None

    cur.execute("""
    SELECT *
    FROM user_group
    WHERE name = %s OR group_id = %s
    """, (group, group_id))
    result = cur.fetchone()
    if result is None:
        await send_message(chat_id=user_id, text="No such group exists. Please try again.")
    else:
        uninitialized_users[user_id].group = result
        await query_group_password(user_id)

async def query_group_password(user_id):
    text = "Enter password for selected group."
    uninitialized_users[user_id].status = LoginStage.AWAIT_GROUP_PASSWORD
    await send_message(chat_id=user_id, text=text)

async def check_group_password(user_id, password):
    group = uninitialized_users[user_id].group
    if not check_password(password, group.hashed_password):
        await send_message(chat_id=user_id, text="Invalid password. Try again.")
        return

    cur.execute("""
    UPDATE app_user
    SET group_id = %s
    WHERE user_id = %s
    """, (group.group_id, user_id))
    connection.commit()
    user_info[user_id].group_id = group.group_id

    uninitialized_user = uninitialized_users[user_id]
    if uninitialized_user.is_host:
        await offer_one_time_code(user_id)
    else:
        del uninitialized_users[user_id]
        await menu(user_id=user_id)




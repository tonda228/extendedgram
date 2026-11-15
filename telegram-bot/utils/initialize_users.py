from psycopg.rows import class_row

from database.groups import get_app_user_groups
from utils.auth import query_bot_password
from utils.classes import UserState, AppUser, AppUserDBObject, UserGroupDBObject
from database import connection
from utils.helpers import send_message
from utils.state import user_info

async def notify_user(user_id: int) -> None:
    await send_message(chat_id=user_id, text="The bot is running again. "
                                             "If you want to join or create a group you can do so in settings.")

async def initialize_users() -> None:
    with connection.cursor(row_factory=class_row(AppUserDBObject)) as user_cur:
        app_users: list[AppUserDBObject] = user_cur.execute("SELECT * FROM app_user").fetchall()

    if not app_users:
        return
    for app_user in app_users:
        user_info[app_user.user_id] = AppUser(
            status=UserState.LOGGED_OUT,
            **app_user.model_dump())
        groups = get_app_user_groups(app_user.user_id)
        user_info[app_user.user_id].groups = groups
        await notify_user(app_user.user_id)
        await query_bot_password(app_user)

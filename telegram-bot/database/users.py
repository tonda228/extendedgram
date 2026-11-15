from telegram import Bot
from telethon.tl.types import User
from telethon import TelegramClient

from utils.helpers import get_user_name, send_message
from utils.security import encrypt_string
from utils.state import user_info, uninitialized_users
from . import cur, connection


def delete_unused_users(commit: bool = True) -> None:
    cur.execute("""
    DELETE 
    FROM telegram_user as tu
    WHERE NOT EXISTS (
        SELECT 1
        FROM app_user au
        WHERE tu.user_id = au.user_id
    ) AND NOT EXISTS (
        SELECT 1
        FROM public_message pb
        WHERE tu.user_id = pb.sender_id
    ) AND NOT EXISTS (
        SELECT 1
        FROM private_message pb
        WHERE tu.user_id = pb.sender_id
    )
    """)
    if commit:
        connection.commit()

def store_telegram_user(user_id: int, user_name: str) -> None:
    cur.execute("""
    INSERT INTO telegram_user (user_id, user_name)
    VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE
        SET user_name = EXCLUDED.user_name
    """, (user_id, user_name))

    connection.commit()

def update_ordering(user_id: int, order_by: str) -> None:
    cur.execute("""
    UPDATE app_user
    SET order_by = %s
    WHERE user_id = %s
    """, (order_by, user_id))
    connection.commit()

def delete_app_user(user_id: int, commit: bool = True) -> None:
    cur.execute("DELETE FROM app_user WHERE user_id = %s", (user_id,))
    if commit:
        connection.commit()

def store_app_user(user_id: int, client: TelegramClient, is_host: bool=False, password_hash: str | None=None, password: str | None=None) -> None:
    session = client.session.save()
    if password:
        session = encrypt_string(session, password)
    cur.execute("""
    INSERT INTO app_user (user_id,
                          string_session,
                          set_read_after_summary,
                          allow_all,
                          preloading,
                          history_size, 
                          is_host,
                          password_hash)
    VALUES (%s, %s, FALSE, TRUE, FALSE, 100, %s, %s) ON CONFLICT (user_id) DO UPDATE
    SET string_session = EXCLUDED.string_session,
        set_read_after_summary = EXCLUDED.set_read_after_summary,
        allow_all = EXCLUDED.allow_all,
        preloading = EXCLUDED.preloading,
        history_size = EXCLUDED.history_size, 
        is_host = EXCLUDED.is_host,
        password_hash= EXCLUDED.password_hash
    """, (user_id, session, is_host, password_hash))
    connection.commit()

def flip_user_state(user_id: int, column: str) -> None:
    cur.execute(f"""
    UPDATE app_user
    SET {column} = NOT {column}
    WHERE user_id = %s
    """, (user_id,))
    connection.commit()

    current_value = getattr(user_info[user_id], column)
    setattr(user_info[user_id], column, not current_value)

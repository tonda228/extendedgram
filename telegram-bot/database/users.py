from utils.helpers import get_user_name
from utils.security import encrypt_string
from utils.state import user_info, uninitialized_users
from . import cur, connection


def store_telegram_user(user_id: int, user_name: str):
    cur.execute("""
    INSERT INTO telegram_user (user_id, user_name)
    VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE
        SET user_name = EXCLUDED.user_name
    """, (user_id, user_name))

    connection.commit()

def update_ordering(user_id: int, order_by: str):
    cur.execute("""
    UPDATE app_user
    SET order_by = %s
    WHERE user_id = %s
    """, (order_by, user_id))
    connection.commit()

def store_app_user(user_id: int, client, is_host=False, password_hash=None, password=None):
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
        is_host = EXCLUDED.is_host
    """, (user_id, session, is_host, password_hash))
    connection.commit()

def flip_user_state(user_id: int, column: str):
    cur.execute(f"""
    UPDATE app_user
    SET {column} = NOT {column}
    WHERE user_id = %s
    """, (user_id,))
    connection.commit()

    current_value = getattr(user_info[user_id], column)
    setattr(user_info[user_id], column, not current_value)

async def send_user_request(user, bot):
    store_telegram_user(user.id, get_user_name(user))

    cur.execute("""
    INSERT INTO user_request (
        user_id,
        is_resolved,
        is_user_notified,
        resolved_by
    ) VALUES (%s, FALSE, FALSE, NULL) ON CONFLICT DO NOTHING
    """, (user.id,))
    connection.commit()

    await send_message(chat_id=user.id, text="Your request has been successfully sent.")

def delete_user_request(user_id):
    cur.execute("""
    DELETE FROM user_request
    WHERE user_id = %s
    """, (user_id,))
    connection.commit()
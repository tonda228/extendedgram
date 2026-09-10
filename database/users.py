from state import user_info
from . import cur, connection


def store_telegram_user(user_id: int, user_name: str):
    cur.execute("""
    INSERT INTO telegram_user (user_id, user_name)
    VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE
        SET user_name = EXCLUDED.user_name
    """, (user_id, user_name))

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